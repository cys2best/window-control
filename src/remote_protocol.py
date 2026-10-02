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
        if parsed["op"] not in ("instances", "host_auth", "preview", "pairing", "webrtc_offer", "webrtc_answer"):
            raise ValueError(f"unknown operation: {parsed['op']}")
        return Command.model_validate(parsed)
    else:
        # Actually parse_frame returns a Command. If it was a reply? 
        # Wait, the test parse_frame("...") expects it to return a Command?
        # In test test_invalid_frames_are_bounded it parses Command.
        return Command.model_validate(parsed)

def format_command(cmd: Command) -> str:
    return cmd.model_dump_json()

def format_reply(reply: Reply) -> str:
    return reply.model_dump_json(exclude_none=True)

def format_error_reply(request_id: str, code: ErrorCode, message: str) -> str:
    reply = Reply(
        v=1,
        id=request_id,
        ok=False,
        error=ErrorPayload(code=code.value, message=message)
    )
    return format_reply(reply)
