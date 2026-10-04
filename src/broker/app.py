import asyncio
import anyio
import uuid
import time
from contextlib import asynccontextmanager, suppress
from typing import List
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from broker.identity_store import InstallationStore, InstallationStoreError
from broker.limits import BrokerLimits
from broker.registry import Registry
from broker.media import MediaAdmission, MediaError
from remote_protocol import (parse_frame, decode_frame, parse_reply, format_reply, format_error_reply,
                             ErrorCode, Reply, RoutedCommand, RoutingContext, DeviceInvalidated,
                             AUTHENTICATED_OPS, MAX_PENDING_REQUESTS, validate_ice_url,
                             parse_media_authorization, parse_media_release)

COMMAND_TIMEOUT_SECONDS = 30.0


class BrokerSettings(BaseModel):
    storage_path: str
    allowed_origins: List[str]
    turn_shared_secret: str = Field(repr=False)
    max_active_streams: int = Field(gt=0)
    stun_urls: list[str] = Field(default_factory=list)
    turn_urls: list[str] = Field(default_factory=list)
    relay_available: bool = True
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)

    @field_validator("stun_urls", "turn_urls")
    def ice_urls(cls, values, info):
        schemes = ("stun:", "stuns:") if info.field_name == "stun_urls" else ("turn:", "turns:")
        if len(values) > 16:
            raise ValueError("too many ICE URLs")
        for value in values:
            validate_ice_url(value)
            if not value.startswith(schemes):
                raise ValueError("invalid ICE scheme")
        return values

    @field_validator("turn_shared_secret")
    def signing_secret(cls, value):
        if not value:
            raise ValueError("missing TURN signing secret")
        return value

class DeleteInstallationRequest(BaseModel):
    credential: str

def create_broker_app(settings: BrokerSettings) -> FastAPI:
    store = InstallationStore(settings.storage_path)
    limits = BrokerLimits()
    registry = Registry()
    
    media = MediaAdmission(settings, limits, registry, time.time)

    @asynccontextmanager
    async def lifespan(app):
        async def cleanup():
            while True:
                await asyncio.sleep(1)
                media.sweep(time.time())
                await flush_cancellations()
        task = asyncio.create_task(cleanup())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    app = FastAPI(lifespan=lifespan)

    @app.exception_handler(InstallationStoreError)
    async def installation_storage_unavailable(request: Request, exc: InstallationStoreError):
        return JSONResponse(status_code=503, content={"detail": "Installation storage unavailable"})
    
    @app.post("/installations")
    def register_installation(request: Request):
        ip = request.client.host if request.client else "127.0.0.1"
        if not limits.check_registration(ip):
            raise HTTPException(status_code=429, detail="quota_exceeded")
            
        identity = store.register()
            
        return {
            "installation_id": identity.installation_id,
            "credential": identity.credential
        }

    @app.delete("/installations/{installation_id}")
    async def delete_installation(installation_id: str, req: DeleteInstallationRequest):
        if not store.exists(installation_id):
            raise HTTPException(status_code=404, detail="Not Found")
            
        if not store.authenticate(installation_id, req.credential):
            raise HTTPException(status_code=401, detail="Unauthorized")
        
        success = store.revoke(installation_id, req.credential)
        if not success:
            raise HTTPException(status_code=404, detail="Not Found")
            
        for viewer in list(registry.viewers.values()):
            if viewer.installation_id == installation_id:
                media.cancel(viewer)
        await flush_cancellations()
        registry.invalidate_installation(installation_id)
        return {"ok": True}

    app.state.registry = registry
    app.state.limits = limits
    app.state.media = media

    @asynccontextmanager
    async def outbound(connection):
        if connection.queued_sends >= 32:
            raise MediaError("busy")
        connection.queued_sends += 1
        try:
            async with connection.send_lock:
                yield
        finally:
            connection.queued_sends -= 1

    async def send(connection, data, *, validate=None):
        async with outbound(connection):
            if validate is not None and not validate():
                raise MediaError("canceled")
            await connection.websocket.send_text(data)

    async def flush_cancellations():
        # Decisions have already retired metadata. Bound each network send.
        async def notify(message):
            host = registry.get_host(message.payload["installation_id"])
            if host is not None and host.epoch == message.payload["host_epoch"]:
                try:
                    async with asyncio.timeout(1):
                        await send(host, message.model_dump_json())
                        media.cancellation_delivered(message)
                except (MediaError, TimeoutError, WebSocketDisconnect, RuntimeError, OSError):
                    # A stuck signaling channel cannot retain trusted authority.
                    media.host_disconnected(host)
                    registry.remove_host(host)
                    with suppress(TimeoutError, WebSocketDisconnect, RuntimeError, OSError):
                        async with asyncio.timeout(1):
                            await host.websocket.close(code=1011)
        messages = media.take_cancellations()
        if messages:
            await asyncio.gather(*(notify(message) for message in messages))

    def public_media_reply(pending, reply):
        if not reply.ok or pending.op not in {"select", "renew", "negotiate", "close"}:
            return reply
        current = media.matching(pending)
        result = reply.result or {}
        if current is None and not (pending.op == "close" and pending.media_owned):
            raise MediaError("stale_generation")
        if pending.op in {"select", "renew"}:
            keys = {"ok", "id", "serial", "name", "w", "h", "tier", "session_id", "generation"}
            if not keys <= result.keys() or (result["session_id"], result["generation"]) != (current.session_id, current.generation):
                raise MediaError("invalid_request")
            result = {key: result[key] for key in keys}
            result.update(current.bundles.viewer.model_dump())
        elif pending.op == "negotiate":
            if (result.get("session_id"), result.get("generation")) != (current.session_id, current.generation) or not isinstance(result.get("answer"), str):
                raise MediaError("invalid_request")
            result = {key: result[key] for key in ("session_id", "generation", "answer")}
        else:
            result = {"closed": True}
        return reply.model_copy(update={"result": result})

    async def forward(viewer, command, received_at, *, authentication=False):
        host = registry.get_host(viewer.installation_id)
        if host is None or host.epoch != viewer.epoch:
            reply = Reply(v=1, id=command.id, ok=False, error={"code": "offline", "message": "Host offline; authenticate again"})
            if not authentication:
                await send(viewer, format_reply(reply))
            return reply
        received_wall = time.time() - (asyncio.get_running_loop().time() - received_at)
        pending = registry.add_pending_request(viewer, command.id, op=command.op,
                                               payload=command.payload, received_at=received_wall)
        if pending is None:
            reply = Reply(v=1, id=command.id, ok=False, error={"code": "invalid_request", "message": "Too many pending requests or duplicate ID"})
            if not authentication:
                await send(viewer, format_reply(reply))
            return reply
        timeout = 10.0 if authentication else COMMAND_TIMEOUT_SECONDS
        deadline = received_at + (command.payload["timeout_ms"] / 1000 if command.op == "negotiate" else timeout)
        delivered = False
        try:
            setup_deadline = media.track_request(pending)
            if setup_deadline is not None:
                deadline = min(deadline, asyncio.get_running_loop().time() + setup_deadline - time.time())
            async with asyncio.timeout_at(deadline):
                async with outbound(host):
                    payload = dict(command.payload)
                    if command.op == "negotiate":
                        remaining = deadline - asyncio.get_running_loop().time()
                        if remaining <= 0:
                            raise asyncio.TimeoutError
                        payload["timeout_ms"] = max(1, int(remaining * 1000))
                    routed = RoutedCommand(v=1, id=pending.routing_id, op=command.op, payload=payload,
                                           context=RoutingContext(installation_id=viewer.installation_id,
                                                                  host_epoch=host.epoch, viewer_id=viewer.viewer_id,
                                                                  token=viewer.token))
                    if command.op in {"select", "renew", "negotiate", "close"}:
                        viewer.media_requested = True
                    await host.websocket.send_text(routed.model_dump_json())
                reply = await pending.future
                reply = public_media_reply(pending, reply).model_copy(update={"id": command.id})
                if not authentication:
                    await send(viewer, format_reply(reply), validate=lambda: (
                        not reply.ok or (
                            registry.is_valid_host(viewer.installation_id, viewer.epoch)
                            and viewer.authenticated
                            and (pending.op not in {"select", "renew", "negotiate"}
                                 or media.matching(pending) is not None)
                        )
                    ))
                    media.delivered(pending, reply)
                delivered = reply.ok
            return reply
        except asyncio.TimeoutError:
            reply = Reply(v=1, id=command.id, ok=False, error={"code": "timeout", "message": "Remote command timed out"})
        except MediaError as exc:
            reply = Reply(v=1, id=command.id, ok=False, error={"code": exc.code, "message": "Media request rejected"})
        except (WebSocketDisconnect, RuntimeError, OSError):
            reply = Reply(v=1, id=command.id, ok=False, error={"code": "offline", "message": "Host offline"})
        finally:
            registry.drop_pending(pending)
            if not delivered:
                media.cancel_request(pending)
                await flush_cancellations()
        if not authentication:
            async with asyncio.timeout(1):
                await send(viewer, format_reply(reply))
        return reply

    @app.websocket("/connect")
    async def websocket_endpoint(websocket: WebSocket):
        origin = websocket.headers.get("origin")
        if websocket.query_params or (origin and origin not in settings.allowed_origins):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        host = None
        viewer = None
        tasks = set()
        ip = websocket.client.host if websocket.client else "127.0.0.1"
        try:
            raw = await asyncio.wait_for(websocket.receive_text(), 10)
            try:
                command = parse_frame(raw)
            except ValueError:
                await websocket.send_text(format_error_reply("", ErrorCode.INVALID_REQUEST, "Invalid authentication frame"))
                return
            if command.op == "host_auth":
                installation_id = command.payload["installation_id"]
                if not store.authenticate(installation_id, command.payload["credential"]):
                    await websocket.send_text(format_error_reply(command.id, ErrorCode.NOT_PAIRED, "Authentication failed"))
                    return
                previous = registry.get_host(installation_id)
                if previous is not None:
                    media.host_disconnected(previous)
                host = registry.register_host(installation_id, websocket)
                await send(host, format_reply(Reply(v=1, id=command.id, ok=True, result={"authenticated": True, "host_epoch": host.epoch})))
                if previous is not None:
                    await previous.websocket.close(code=1008)
            elif command.op in {"viewer_auth", "pair"}:
                if command.op == "pair":
                    if not limits.check_pairing(ip):
                        await websocket.send_text(format_error_reply(command.id, ErrorCode.QUOTA_EXCEEDED, "Pairing quota exceeded"))
                        return
                    installation_id = registry.installation_for_handle(command.payload["handle"])
                    if installation_id is None:
                        await websocket.send_text(format_error_reply(command.id, ErrorCode.EXPIRED_PAIRING, "Invitation is closed or host offline"))
                        return
                else:
                    installation_id = command.payload["installation_id"]
                current = registry.get_host(installation_id)
                if current is None:
                    await websocket.send_text(format_error_reply(command.id, ErrorCode.OFFLINE, "Host offline"))
                    return
                viewer = registry.register_viewer(uuid.uuid4().hex, installation_id, websocket, current.epoch)
                viewer.token = command.payload.get("token", "")
                reply = await forward(viewer, command, asyncio.get_running_loop().time(), authentication=True)
                if not reply.ok:
                    await send(viewer, format_reply(reply))
                    return
                result = reply.result or {}
                device_id = result.get("device_id")
                token = result.get("token") if command.op == "pair" else viewer.token
                if (not isinstance(device_id, str) or not device_id or len(device_id) > 128
                        or not isinstance(token, str) or not token or len(token) > 256
                        or not registry.is_valid_host(installation_id, viewer.epoch)):
                    await send(viewer, format_error_reply(command.id, ErrorCode.NOT_PAIRED, "PC did not authorize this device"))
                    return
                viewer.device_id = device_id
                viewer.token = token
                viewer.authenticated = True
                public_result = {"installation_id": installation_id, "token": token} if command.op == "pair" else {"authenticated": True, "viewer_id": viewer.viewer_id}
                await send(viewer, format_reply(Reply(v=1, id=command.id, ok=True, result=public_result)))
            else:
                await websocket.send_text(format_error_reply(command.id, ErrorCode.INVALID_REQUEST, "Expected authentication command"))
                return

            async def viewer_command(command, received_at):
                try:
                    await forward(viewer, command, received_at)
                except (TimeoutError, WebSocketDisconnect, RuntimeError, OSError):
                    pass

            while True:
                raw = await websocket.receive_text()
                received_at = asyncio.get_running_loop().time()
                try:
                    if host is not None:
                        if not registry.is_valid_host(host.installation_id, host.epoch):
                            await send(host, format_error_reply("", ErrorCode.STALE_GENERATION, "Host connection was replaced"))
                            return
                        parsed = decode_frame(raw)
                        if "ok" in parsed:
                            reply = parse_reply(raw)
                            pending = registry.resolve_pending_request(reply.id, host.installation_id, host.epoch)
                            if pending is None:
                                if registry.is_settled_request(reply.id, host.installation_id, host.epoch):
                                    continue
                                raise ValueError("unknown request")
                            if not pending.future.done():
                                pending.future.set_result(reply)
                        elif parsed.get("op") == "media_authorize":
                            request = parse_media_authorization(raw)
                            try:
                                bundles = media.authorize(host, request)
                                response = format_reply(Reply(v=1, id=request.id, ok=True, result=bundles.model_dump()))
                            except MediaError as exc:
                                response = format_error_reply(request.id, ErrorCode(exc.code), "Media admission rejected")
                            await send(host, response)
                            await flush_cancellations()
                        elif parsed.get("op") == "media_release":
                            request = parse_media_release(raw)
                            if not media.release(host, request.payload["session_id"], request.payload["generation"]):
                                raise ValueError("stale media release")
                            await send(host, format_reply(Reply(v=1, id=request.id, ok=True, result={"released": True})))
                        elif parsed.get("op") == "device_invalidated":
                            notification = DeviceInvalidated.model_validate(parsed)
                            for affected in registry.invalidate_device(host, notification.payload["device_id"]):
                                media.cancel(affected)
                                await flush_cancellations()
                                await affected.websocket.close(code=1008)
                        else:
                            command = parse_frame(raw)
                            if command.op == "pairing_open":
                                if not registry.open_invitation(host, command.payload["handle"], command.payload["expires_at"]):
                                    raise ValueError("invalid invitation")
                            elif command.op == "pairing_close":
                                registry.close_invitation(host, command.payload["handle"])
                            else:
                                raise ValueError("host role violation")
                            await send(host, format_reply(Reply(v=1, id=command.id, ok=True, result={"accepted": True})))
                    else:
                        command = parse_frame(raw)
                        if command.op not in AUTHENTICATED_OPS:
                            raise ValueError("viewer role violation")
                        if not viewer.authenticated or not registry.is_valid_host(viewer.installation_id, viewer.epoch):
                            await send(viewer, format_error_reply(command.id, ErrorCode.NOT_PAIRED, "Authenticate again"))
                            continue
                        permitted = limits.check_preview(viewer.viewer_id) if command.op == "preview" else limits.check_authenticated_command(viewer.viewer_id)
                        if not permitted:
                            await send(viewer, format_error_reply(command.id, ErrorCode.QUOTA_EXCEEDED, "Command quota exceeded"))
                            continue
                        # Reserve IDs before yielding/spawning so duplicates cannot race.
                        if command.id in viewer.pending_requests or len(tasks) >= MAX_PENDING_REQUESTS:
                            raise ValueError("duplicate or too many pending requests")
                        task = asyncio.create_task(viewer_command(command, received_at))
                        tasks.add(task)
                        task.add_done_callback(tasks.discard)
                        await asyncio.sleep(0)
                except ValueError:
                    request_id = ""
                    try:
                        candidate = decode_frame(raw).get("id", "")
                        uuid.UUID(candidate)
                        request_id = candidate
                    except (ValueError, TypeError, AttributeError):
                        pass
                    target = host if host is not None else viewer
                    await send(target, format_error_reply(request_id, ErrorCode.INVALID_REQUEST, "Invalid remote frame"))
        except InstallationStoreError:
            await websocket.close(code=1011, reason="Installation storage unavailable")
        except (WebSocketDisconnect, asyncio.TimeoutError, RuntimeError, OSError):
            pass
        finally:
            with anyio.CancelScope(shield=True):
                for task in tasks:
                    task.cancel()
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
                if viewer is not None:
                    media.cancel(viewer)
                    await flush_cancellations()
                    registry.remove_viewer(viewer.viewer_id)
                if host is not None:
                    media.host_disconnected(host)
                    registry.remove_host(host)
                try:
                    await websocket.close()
                except (RuntimeError, WebSocketDisconnect, OSError):
                    pass

    return app
