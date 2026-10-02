import asyncio
import uuid
from typing import List
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Request
from pydantic import BaseModel

from broker.identity_store import InstallationStore
from broker.limits import BrokerLimits
from broker.registry import Registry
from remote_protocol import (parse_frame, decode_frame, parse_reply, format_reply, format_error_reply,
                             ErrorCode, Reply, RoutedCommand, RoutingContext, DeviceInvalidated,
                             AUTHENTICATED_OPS, MAX_PENDING_REQUESTS)

class BrokerSettings(BaseModel):
    storage_path: str
    allowed_origins: List[str]
    turn_shared_secret: str

class DeleteInstallationRequest(BaseModel):
    credential: str

def create_broker_app(settings: BrokerSettings) -> FastAPI:
    store = InstallationStore(settings.storage_path)
    limits = BrokerLimits()
    registry = Registry()
    
    app = FastAPI()
    
    @app.post("/installations")
    def register_installation(request: Request):
        ip = request.client.host if request.client else "127.0.0.1"
        if not limits.check_registration(ip):
            raise HTTPException(status_code=429, detail="quota_exceeded")
            
        identity = store.register()
        if not limits.check_credential_issuance(identity.installation_id):
            pass # Just consume the token for the new installation
            
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
            
        registry.invalidate_installation(installation_id)
        return {"ok": True}

    app.state.registry = registry

    async def send(connection, data):
        async with connection.send_lock:
            await connection.websocket.send_text(data)

    async def forward(viewer, command, received_at, *, authentication=False):
        host = registry.get_host(viewer.installation_id)
        if host is None or host.epoch != viewer.epoch:
            return Reply(v=1, id=command.id, ok=False, error={"code": "offline", "message": "Host offline; authenticate again"})
        pending = registry.add_pending_request(viewer, command.id)
        if pending is None:
            return Reply(v=1, id=command.id, ok=False, error={"code": "invalid_request", "message": "Too many pending requests or duplicate ID"})
        timeout = 10.0 if authentication else 30.0
        deadline = received_at + (command.payload["timeout_ms"] / 1000 if command.op == "negotiate" else timeout)
        try:
            async with asyncio.timeout_at(deadline):
                async with host.send_lock:
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
                    await host.websocket.send_text(routed.model_dump_json())
                reply = await pending.future
            return reply.model_copy(update={"id": command.id})
        except asyncio.TimeoutError:
            return Reply(v=1, id=command.id, ok=False, error={"code": "timeout", "message": "Remote command timed out"})
        except (WebSocketDisconnect, RuntimeError, OSError):
            return Reply(v=1, id=command.id, ok=False, error={"code": "offline", "message": "Host offline"})
        finally:
            registry.drop_pending(pending)

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
                    reply = await forward(viewer, command, received_at)
                    await send(viewer, format_reply(reply))
                except (WebSocketDisconnect, RuntimeError, OSError):
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
                                raise ValueError("unknown request")
                            if not pending.future.done():
                                pending.future.set_result(reply)
                        elif parsed.get("op") == "device_invalidated":
                            notification = DeviceInvalidated.model_validate(parsed)
                            for affected in registry.invalidate_device(host, notification.payload["device_id"]):
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
                        if not permitted or (command.op == "renew" and not limits.check_credential_issuance(viewer.installation_id)):
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
        except (WebSocketDisconnect, asyncio.TimeoutError, RuntimeError, OSError):
            pass
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if viewer is not None:
                registry.remove_viewer(viewer.viewer_id)
            if host is not None:
                registry.remove_host(host)
            try:
                await websocket.close()
            except (RuntimeError, WebSocketDisconnect, OSError):
                pass

    return app
