"""Shared instance actions and PC-authoritative typed remote dispatch."""

import asyncio
import base64
from fastapi import HTTPException
from config import TIER_ORDER
from remote_protocol import AUTHENTICATED_OPS, Command, Reply, ErrorPayload, ErrorCode, MAX_PREVIEW_BYTES


class InstanceActions:
    def __init__(self, manager):
        self.manager = manager

    async def instances(self):
        return self.manager.list_instances()

    async def select(self, serial, advertised_host):
        from server.app import _selection_ice_servers
        inst = self.manager.get(serial)
        if inst is None:
            raise HTTPException(404, "Instance not found")
        selection = await asyncio.to_thread(self.manager.select, serial, advertised_host)
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

    async def quality(self, serial, tier):
        if tier not in TIER_ORDER:
            raise HTTPException(400, "Invalid tier")
        if self.manager.get(serial) is None:
            raise HTTPException(404, "Instance not found")
        if not await asyncio.to_thread(self.manager.set_tier, serial, tier):
            raise HTTPException(404, "Instance not found")
        return {"ok": True, "tier": tier}


class RemoteDispatcher:
    def __init__(self, actions, pairing, *, sessions=None):
        """sessions.dispatch(command, PairedDevice) is Task 6's async media boundary."""
        self.actions = actions
        self.pairing = pairing
        self.sessions = sessions

    async def dispatch(self, command: Command, token: str) -> Reply:
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
        try:
            if command.op == "instances":
                result = {"instances": await self.actions.instances()}
            elif command.op == "select":
                selection = await self.actions.select(payload["serial"], "127.0.0.1")
                result = {key: value for key, value in selection.items()
                          if key in {"ok", "id", "serial", "name", "w", "h", "generation", "tier"}}
            elif command.op == "preview":
                preview = await self.actions.preview(payload["serial"])
                if len(preview.body) > MAX_PREVIEW_BYTES:
                    return error(ErrorCode.UNAVAILABLE, "Remote preview is too large")
                result = {"mime": "image/jpeg", "data_base64": base64.b64encode(preview.body).decode("ascii")}
            elif command.op == "keyframe":
                result = await self.actions.keyframe(payload["serial"])
            elif command.op == "quality":
                result = await self.actions.quality(payload["serial"], payload["tier"])
            elif self.sessions is None:
                return error(ErrorCode.UNAVAILABLE, "Remote media is not available")
            else:
                return await self.sessions.dispatch(command, device)
            return Reply(v=1, id=command.id, ok=True, result=result)
        except HTTPException as exc:
            code = ErrorCode.INVALID_REQUEST if exc.status_code in (400, 404) else ErrorCode.UNAVAILABLE
            return error(code, str(exc.detail))
        except Exception:
            return error(ErrorCode.UNAVAILABLE, "Remote action failed")
