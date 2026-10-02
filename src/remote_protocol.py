import json
from enum import Enum
from typing import Optional, Any, Dict
from pydantic import BaseModel, Field, field_validator, ConfigDict

MAX_FRAME_BYTES = 1024 * 1024  # 1 MiB
MAX_SDP_BYTES = 128 * 1024     # 128 KiB
MAX_PREVIEW_BYTES = 384 * 1024 # 384 KiB
MAX_PENDING_REQUESTS = 32

class ErrorCode(str, Enum):
    NOT_PAIRED = "not_paired"
    OFFLINE = "offline"
    EXPIRED_PAIRING = "expired_pairing"
    INVALID_REQUEST = "invalid_request"
    BUSY = "busy"
    STALE_GENERATION = "stale_generation"
    UNAVAILABLE = "unavailable"
    RELAY_UNAVAILABLE = "relay_unavailable"
    QUOTA_EXCEEDED = "quota_exceeded"
    TIMEOUT = "timeout"
    CANCELED = "canceled"

class Command(BaseModel):
    v: int
    id: str
    op: str
    payload: Dict[str, Any]
    model_config = ConfigDict(extra="forbid")
    
    @field_validator('v')
    def validate_v(cls, v):
        if v != 1:
            raise ValueError(f"invalid version: {v}")
        return v
        
    @field_validator('id')
    def validate_uuid(cls, v):
        import uuid
        try:
            uuid.UUID(v)
        except ValueError:
            raise ValueError("invalid UUID")
        return v

class ErrorPayload(BaseModel):
    code: str
    message: str

class Reply(BaseModel):
    v: int
    id: str
    ok: bool
    result: Optional[Dict[str, Any]] = None
    error: Optional[ErrorPayload] = None
    model_config = ConfigDict(extra="forbid")

def parse_frame(data: str) -> Command:
    if len(data.encode('utf-8')) > MAX_FRAME_BYTES:
        raise ValueError("frame size exceeds maximum")
    
    parsed = json.loads(data)
    if "op" in parsed:
        op = parsed["op"]
        if op not in ("instances", "host_auth", "viewer_auth", "pair", "preview", "pairing", "webrtc_offer", "webrtc_answer", "select", "keyframe", "quality", "negotiate", "close", "renew"):
            raise ValueError(f"unknown operation: {op}")
            
        payload = parsed.get("payload", {})
        
        # Enforce specific payload bounds
        if op in ("webrtc_offer", "webrtc_answer", "negotiate"):
            sdp = payload.get("sdp", "")
            if len(sdp.encode('utf-8')) > MAX_SDP_BYTES:
                raise ValueError("SDP exceeds MAX_SDP_BYTES")
                
        # Preview might have bounds before base64, but maybe that's checked on reply? 
        # "MAX_PREVIEW_BYTES (384 KiB) before base64"
        # Since preview data is sent by the host in a reply, wait, if the host sends it in a command or reply?
        # A preview frame: command `preview`, host replies with image.
        
        return Command.model_validate(parsed)
    else:
        return Command.model_validate(parsed)

def format_command(cmd: Command) -> str:
    return cmd.model_dump_json()

def format_reply(reply: Reply) -> str:
    if reply.result and "image" in reply.result:
        # Check preview image bounds before base64 
        # wait, the image is ALREADY base64 in JSON. 384KiB before base64 = 384 * 1024 bytes.
        # Length in base64 is ~ (384 * 1024) * 4 / 3 = 512 KiB.
        b64_len = len(reply.result["image"])
        if b64_len > (MAX_PREVIEW_BYTES * 4 / 3 + 4):
            raise ValueError("preview exceeds MAX_PREVIEW_BYTES")
    return reply.model_dump_json(exclude_none=True)

def format_error_reply(request_id: str, code: ErrorCode, message: str) -> str:
    reply = Reply(
        v=1,
        id=request_id,
        ok=False,
        error=ErrorPayload(code=code.value, message=message)
    )
    return format_reply(reply)
