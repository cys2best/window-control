"""Shared instance actions and PC-authoritative typed remote dispatch."""

import asyncio
import base64
from fastapi import HTTPException
from config import TIER_ORDER
from server.instance_manager import MutationGuard
from server.remote_pairing import RemotePairingError
from remote_protocol import AUTHENTICATED_OPS, Command, Reply, RoutingContext, ErrorPayload, ErrorCode, MAX_PREVIEW_BYTES


class InstanceActions:
    def __init__(self, manager):
        self.manager = manager

    async def instances(self):
        return self.manager.list_instances()

    async def select(self, serial, advertised_host, *, mutation_guard=None):
        from server.app import _selection_ice_servers
        inst = self.manager.get(serial)
        if inst is None:
            raise HTTPException(404, "Instance not found")
        kwargs = {"mutation_guard": mutation_guard} if mutation_guard is not None else {}
        selection = await asyncio.to_thread(self.manager.select, serial, advertised_host, **kwargs)
        if selection is None:
            raise HTTPException(503, "Engine runtime not ready")
        return {"ok": True, "id": inst.id, "serial": inst.serial, "name": inst.name,
                "w": selection.width, "h": selection.height, "whep_url": selection.whep_url,
                "whep_token": selection.whep_token, "ice_servers": _selection_ice_servers(advertised_host),
                "generation": selection.generation, "tier": selection.tier}

    async def preview(self, serial):
        from server.app import _capture_preview
        return await _capture_preview(serial)

    async def keyframe(self, serial):
        await asyncio.to_thread(self.manager.request_keyframe, serial)
        return {"ok": True}

    async def quality(self, serial, tier, *, mutation_guard=None):
        if tier not in TIER_ORDER:
            raise HTTPException(400, "Invalid tier")
        if self.manager.get(serial) is None:
            raise HTTPException(404, "Instance not found")
        kwargs = {"mutation_guard": mutation_guard} if mutation_guard is not None else {}
        if not await asyncio.to_thread(self.manager.set_tier, serial, tier, **kwargs):
            raise HTTPException(404, "Instance not found")
        return {"ok": True, "tier": tier}


class RemoteDispatcher:
    def __init__(self, actions, pairing, *, sessions=None):
        """The coordinator owns media; InstanceActions retains local mutations."""
        self.actions = actions
        self.pairing = pairing
        self.sessions = sessions

    async def dispatch(self, command: Command, token: str, *, trusted_context: RoutingContext | None = None) -> Reply:
        def error(code, message):
            return Reply(v=1, id=command.id, ok=False, error=ErrorPayload(code=code.value, message=message))
        try:
            # Validate even if an internal caller used model_construct.
            command = Command.model_validate(command.model_dump())
            if command.op not in AUTHENTICATED_OPS:
                return error(ErrorCode.INVALID_REQUEST, "Operation is not a viewer action")
        except ValueError:
            return error(ErrorCode.INVALID_REQUEST, "Invalid command")
        device = self.pairing.device_for_token(token)
        if device is None:
            return error(ErrorCode.NOT_PAIRED, "Device is not paired")
        payload = command.payload
        guard = MutationGuard() if command.op in {"select", "quality", "renew"} else None
        try:
            if command.op in {"select", "negotiate", "close", "renew"}:
                if self.sessions is None:
                    return error(ErrorCode.UNAVAILABLE, "Remote media is not available")
                if trusted_context is None or trusted_context.token != token:
                    return error(ErrorCode.INVALID_REQUEST, "Trusted routing context is required")
                result = await self.sessions.dispatch(command, device, context=trusted_context, mutation_guard=guard)
            elif command.op == "instances":
                result = {"instances": await self.actions.instances()}
            elif command.op == "preview":
                preview = await self.actions.preview(payload["serial"])
                if len(preview.body) > MAX_PREVIEW_BYTES:
                    return error(ErrorCode.UNAVAILABLE, "Remote preview is too large")
                result = {"mime": "image/jpeg", "data_base64": base64.b64encode(preview.body).decode("ascii")}
            elif command.op == "keyframe":
                result = await self.actions.keyframe(payload["serial"])
            elif command.op == "quality":
                result = await self.actions.quality(payload["serial"], payload["tier"], mutation_guard=guard)
            return Reply(v=1, id=command.id, ok=True, result=result)
        except RemotePairingError as exc:
            return error(exc.code, str(exc))
        except HTTPException as exc:
            code = ErrorCode.INVALID_REQUEST if exc.status_code in (400, 404) else ErrorCode.UNAVAILABLE
            return error(code, str(exc.detail))
        except Exception:
            return error(ErrorCode.UNAVAILABLE, "Remote action failed")
        finally:
            if guard is not None:
                guard.cancel()
