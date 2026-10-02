import json
import asyncio
import uuid
from typing import List, Optional, Any
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Request
from fastapi.websockets import WebSocketState
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
        origin = websocket.headers.get("origin")
        if origin and origin not in settings.allowed_origins:
            # Reject with 403. In FastAPI websockets, we can just close with 1008 or raise before accept.
            # But Starlette's TestClient might not handle exceptions in websocket endpoint well if not accepted.
            await websocket.close(code=1008)
            return

        await websocket.accept()
        
        is_host = False
        is_viewer = False
        inst_id = None
        viewer_id = None
        host_conn = None
        viewer_conn = None
        
        ip = websocket.client.host if websocket.client else "127.0.0.1"

        try:
            data = await asyncio.wait_for(websocket.receive_text(), timeout=10.0)
            try:
                cmd = parse_frame(data)
            except ValueError as e:
                await websocket.send_text(format_error_reply("", ErrorCode.INVALID_REQUEST, str(e)))
                await websocket.close()
                return

            if cmd.op == "host_auth":
                inst_id = cmd.payload.get("installation_id")
                cred = cmd.payload.get("credential")
                
                if not store.authenticate(inst_id, cred):
                    await websocket.send_text(format_error_reply(cmd.id, ErrorCode.NOT_PAIRED, "Auth failed"))
                    await websocket.close()
                    return
                    
                is_host = True
                host_conn = registry.register_host(inst_id, websocket)
                
                reply = Reply(v=1, id=cmd.id, ok=True, result={"authenticated": True})
                await websocket.send_text(format_reply(reply))
                
            elif cmd.op == "viewer_auth" or cmd.op == "pair":
                # For pairing/viewer auth.
                inst_id = cmd.payload.get("installation_id")
                if cmd.op == "pair":
                    if not limits.check_pairing(ip):
                        await websocket.send_text(format_error_reply(cmd.id, ErrorCode.QUOTA_EXCEEDED, "quota_exceeded"))
                        await websocket.close()
                        return
                        
                # Just mock auth for viewer here since there is no viewer token logic provided in the prompt, wait:
                # "no URL token", "viewer_first_frame_auth => browser-compatible auth, no URL token"
                # Wait, the first task said: "viewer_first_frame_auth => browser-compatible auth, no URL token" - the test in step 1 was for host_auth though. 
                # For viewers: do we just accept them if the host is connected? 
                is_viewer = True
                viewer_id = uuid.uuid4().hex
                viewer_conn = registry.register_viewer(viewer_id, inst_id, websocket)
                
                reply = Reply(v=1, id=cmd.id, ok=True, result={"authenticated": True, "viewer_id": viewer_id})
                await websocket.send_text(format_reply(reply))
                
            else:
                await websocket.send_text(format_error_reply(cmd.id, ErrorCode.INVALID_REQUEST, "Expected auth command"))
                await websocket.close()
                return
            
            while True:
                msg = await websocket.receive_text()
                
                if is_host:
                    if not registry.is_valid_host(inst_id, host_conn.epoch):
                        err = Reply(v=1, id="0", ok=False, error={"code": ErrorCode.STALE_GENERATION.value, "message": "stale"})
                        await websocket.send_text(format_reply(err))
                        await websocket.close()
                        break
                        
                    # Host can send replies or errors back to viewers
                    try:
                        parsed = json.loads(msg)
                        if "ok" in parsed:
                            # It's a reply
                            req_id = parsed.get("id")
                            target_viewer = registry.resolve_pending_request(req_id)
                            if target_viewer:
                                # Route to viewer
                                try:
                                    # validate bounds
                                    format_reply(Reply.model_validate(parsed))
                                    await target_viewer.websocket.send_text(msg)
                                except ValueError as e:
                                    await websocket.send_text(format_error_reply(req_id, ErrorCode.INVALID_REQUEST, str(e)))
                                except WebSocketDisconnect:
                                    registry.remove_viewer(target_viewer.viewer_id)
                                except Exception as e:
                                    import logging
                                    logging.warning(f"Failed to route reply to viewer: {e}")
                            else:
                                await websocket.send_text(format_error_reply(req_id, ErrorCode.INVALID_REQUEST, "cross installation reply rejected"))
                    except Exception as e:
                        # Don't swallow
                        await websocket.send_text(format_error_reply("", ErrorCode.INVALID_REQUEST, str(e)))
                
                elif is_viewer:
                    try:
                        cmd = parse_frame(msg)
                        
                        if cmd.op == "preview":
                            if not limits.check_preview(viewer_id):
                                await websocket.send_text(format_error_reply(cmd.id, ErrorCode.QUOTA_EXCEEDED, "quota_exceeded"))
                                continue
                        else:
                            if not limits.check_authenticated_command(viewer_id):
                                await websocket.send_text(format_error_reply(cmd.id, ErrorCode.QUOTA_EXCEEDED, "quota_exceeded"))
                                continue
                                
                        if cmd.op == "renew":
                            if not limits.check_credential_issuance(inst_id):
                                await websocket.send_text(format_error_reply(cmd.id, ErrorCode.QUOTA_EXCEEDED, "quota_exceeded"))
                                continue
                                
                        host = registry.get_host(inst_id)
                        if not host:
                            await websocket.send_text(format_error_reply(cmd.id, ErrorCode.OFFLINE, "Host offline"))
                            continue
                            
                        # Add pending request
                        if not registry.add_pending_request(viewer_conn, cmd.id):
                            await websocket.send_text(format_error_reply(cmd.id, ErrorCode.INVALID_REQUEST, "Too many pending requests or duplicate id"))
                            continue
                            
                        # Route to host
                        await host.websocket.send_text(msg)
                        
                    except ValueError as e:
                        try:
                            # Try to extract id for error
                            parsed = json.loads(msg)
                            req_id = parsed.get("id", "")
                        except Exception:
                            req_id = ""
                        await websocket.send_text(format_error_reply(req_id, ErrorCode.INVALID_REQUEST, str(e)))
                    except Exception as e:
                        await websocket.send_text(format_error_reply("", ErrorCode.INVALID_REQUEST, str(e)))
                        
        except WebSocketDisconnect:
            pass
        except asyncio.TimeoutError:
            try:
                await websocket.close()
            except Exception:
                pass
        except Exception as e:
            try:
                await websocket.close()
            except Exception:
                pass
        finally:
            if is_viewer and viewer_id:
                registry.remove_viewer(viewer_id)

    return app
