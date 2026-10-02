import json
import asyncio
from typing import List, Optional, Any
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Request
from pydantic import BaseModel
from contextlib import asynccontextmanager

from broker.identity_store import InstallationStore
from broker.limits import BrokerLimits
from broker.registry import Registry
from remote_protocol import parse_frame, format_reply, format_error_reply, ErrorCode, Reply

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
        ip = "127.0.0.1"
        if request.client and request.client.host:
            ip = request.client.host
            
        identity = store.register()
        return {
            "installation_id": identity.installation_id,
            "credential": identity.credential
        }

    @app.delete("/installations/{installation_id}")
    def delete_installation(installation_id: str, req: DeleteInstallationRequest):
        if not store.exists(installation_id):
            raise HTTPException(status_code=404, detail="Not Found")
            
        if not store.authenticate(installation_id, req.credential):
            raise HTTPException(status_code=401, detail="Unauthorized")
        
        success = store.revoke(installation_id, req.credential)
        if not success:
            raise HTTPException(status_code=404, detail="Not Found")
            
        registry.invalidate_installation(installation_id)
        return {"ok": True}

    @app.websocket("/connect")
    async def websocket_endpoint(websocket: WebSocket):
        await websocket.accept()
        try:
            data = await asyncio.wait_for(websocket.receive_text(), timeout=10.0)
            try:
                cmd = parse_frame(data)
            except ValueError as e:
                await websocket.send_text(json.dumps({"v":1,"id":"","ok": False, "error": {"code": ErrorCode.INVALID_REQUEST.value, "message": str(e)}}))
                await websocket.close()
                return

            if cmd.op != "host_auth":
                await websocket.send_text(json.dumps({"v":1,"id":cmd.id,"ok": False, "error": {"code": ErrorCode.INVALID_REQUEST.value, "message": "Expected host_auth"}}))
                await websocket.close()
                return
                
            inst_id = cmd.payload.get("installation_id")
            cred = cmd.payload.get("credential")
            
            if not store.authenticate(inst_id, cred):
                await websocket.send_text(json.dumps({"v":1,"id":cmd.id,"ok": False, "error": {"code": ErrorCode.NOT_PAIRED.value, "message": "Auth failed"}}))
                await websocket.close()
                return
                
            host_conn = registry.register_host(inst_id, websocket)
            
            reply = Reply(v=1, id=cmd.id, ok=True, result={"authenticated": True})
            await websocket.send_text(format_reply(reply))
            
            while True:
                msg = await websocket.receive_text()
                if not registry.is_valid_host(inst_id, host_conn.epoch):
                    err = Reply(v=1, id="0", ok=False, error={"code": ErrorCode.STALE_GENERATION.value, "message": "stale"})
                    await websocket.send_text(format_reply(err))
                    await websocket.close()
                    break
                    
                try:
                    parsed = json.loads(msg)
                    if "ok" in parsed:
                        err = Reply(v=1, id=parsed.get("id", ""), ok=False, error={"code": ErrorCode.INVALID_REQUEST.value, "message": "cross installation reply rejected"})
                        await websocket.send_text(format_reply(err))
                except Exception:
                    pass
                
        except WebSocketDisconnect:
            pass
        except asyncio.TimeoutError:
            await websocket.close()
        except Exception:
            await websocket.close()

    return app

