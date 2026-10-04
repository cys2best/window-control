import json
from enum import Enum
from typing import Optional, Any, Dict, Literal
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

AUTHENTICATED_OPS = frozenset({"instances", "select", "preview", "keyframe", "quality", "negotiate", "close", "renew"})
HOST_OPS = frozenset({"pairing_open", "pairing_close"})
PAYLOAD_FIELDS = {
    "host_auth": ({"installation_id", "credential"}, set()),
    "viewer_auth": ({"installation_id", "token"}, set()),
    "pair": ({"handle", "code"}, {"device_name"}),
    "pairing_open": ({"handle", "expires_at"}, set()),
    "pairing_close": ({"handle"}, set()),
    "instances": (set(), set()),
    "select": ({"serial"}, set()),
    "preview": ({"serial"}, set()),
    "keyframe": ({"serial"}, set()),
    "quality": ({"serial", "tier"}, set()),
    "negotiate": ({"session_id", "generation", "offer", "timeout_ms"}, set()),
    "close": ({"session_id", "generation"}, set()),
    "renew": ({"session_id", "generation"}, set()),
}


def validate_payload(op, payload):
    import math
    import re
    import uuid
    if op not in PAYLOAD_FIELDS:
        raise ValueError("unknown operation")
    required, optional = PAYLOAD_FIELDS[op]
    if not required <= payload.keys() or payload.keys() - required - optional:
        raise ValueError("invalid payload fields")
    for key, value in payload.items():
        if key == "offer":
            if not isinstance(value, str) or not value or len(value.encode("utf-8")) > MAX_SDP_BYTES:
                raise ValueError("SDP exceeds MAX_SDP_BYTES or is invalid")
        elif key in {"generation", "timeout_ms"}:
            if type(value) is not int or value < 1 or (key == "timeout_ms" and value > 30000):
                raise ValueError("invalid setup budget or generation")
        elif key == "expires_at":
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError("invalid invitation expiry")
        else:
            bound = {"token": 256, "credential": 4096, "offer": MAX_SDP_BYTES, "device_name": 128}.get(key, 128)
            if not isinstance(value, str) or len(value) > bound or (not value and key != "device_name"):
                raise ValueError("invalid string field")
            if key in {"installation_id", "serial", "handle", "tier"} and not re.fullmatch(r"[A-Za-z0-9_.:-]+", value):
                raise ValueError("invalid field format")
            if key == "handle" and len(value) < 22:
                raise ValueError("invalid invitation handle")
            if key == "code" and not re.fullmatch(r"[0-9]{6}", value):
                raise ValueError("invalid pairing code")
            if key == "session_id":
                uuid.UUID(value)
    return payload


class Command(BaseModel):
    v: int
    id: str
    op: str
    payload: Dict[str, Any] = Field(repr=False)
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    @field_validator("v")
    def validate_v(cls, value):
        if value != 1:
            raise ValueError("invalid version")
        return value

    @field_validator("id")
    def validate_uuid(cls, value):
        import uuid
        uuid.UUID(value)
        return value

    @field_validator("payload")
    def validate_fields(cls, value, info):
        return validate_payload(info.data.get("op"), value)


class RoutingContext(BaseModel):
    """Only the authenticated broker constructs this PC-facing context."""
    installation_id: str = Field(min_length=1, max_length=128)
    host_epoch: int = Field(ge=1)
    viewer_id: str = Field(min_length=1, max_length=128)
    token: str = Field(max_length=256, repr=False)
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class RoutedCommand(Command):
    context: RoutingContext

    def command(self) -> Command:
        return Command.model_validate(self.model_dump(exclude={"context"}))


class DeviceInvalidated(BaseModel):
    """Internal host-only notification; never accepted as a viewer command."""
    v: int
    id: str
    op: Literal["device_invalidated"]
    payload: Dict[str, str]
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    @field_validator("v")
    def version(cls, value):
        if value != 1:
            raise ValueError("invalid version")
        return value

    @field_validator("id")
    def request_id(cls, value):
        import uuid
        uuid.UUID(value)
        return value

    @field_validator("payload")
    def device(cls, value):
        import re
        if set(value) != {"device_id"} or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value["device_id"]):
            raise ValueError("invalid device ID")
        return value


class ErrorPayload(BaseModel):
    code: str
    message: str
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    @field_validator("code")
    def error_code(cls, value):
        ErrorCode(value)
        return value

class Reply(BaseModel):
    v: int
    id: str
    ok: bool
    result: Optional[Dict[str, Any]] = Field(default=None, repr=False)
    error: Optional[ErrorPayload] = None
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

def decode_frame(data: str) -> dict:
    if not isinstance(data, str) or len(data.encode("utf-8")) > MAX_FRAME_BYTES:
        raise ValueError("frame size exceeds maximum")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError("invalid JSON constant")

    try:
        parsed = json.loads(data, object_pairs_hook=unique, parse_constant=reject_constant)
    except RecursionError:
        raise ValueError("JSON nesting is too deep") from None
    if not isinstance(parsed, dict):
        raise ValueError("expected JSON object")
    return parsed


def parse_frame(data: str) -> Command:
    return Command.model_validate(decode_frame(data))


def parse_routed_frame(data: str) -> RoutedCommand:
    return RoutedCommand.model_validate(decode_frame(data))


def format_command(cmd: Command) -> str:
    return cmd.model_dump_json()


def format_reply(reply: Reply) -> str:
    import base64
    result = reply.result or {}
    for key in ("offer", "answer"):
        if key in result and (not isinstance(result[key], str) or len(result[key].encode("utf-8")) > MAX_SDP_BYTES):
            raise ValueError("SDP exceeds MAX_SDP_BYTES")
    if "data_base64" in result:
        data = result["data_base64"]
        if not isinstance(data, str) or len(data) > (MAX_PREVIEW_BYTES + 2) // 3 * 4:
            raise ValueError("preview exceeds MAX_PREVIEW_BYTES")
        try:
            decoded = base64.b64decode(data, validate=True)
        except Exception:
            raise ValueError("invalid preview encoding") from None
        if len(decoded) > MAX_PREVIEW_BYTES:
            raise ValueError("preview exceeds MAX_PREVIEW_BYTES")
    raw = reply.model_dump_json(exclude_none=True)
    if len(raw.encode("utf-8")) > MAX_FRAME_BYTES:
        raise ValueError("frame size exceeds maximum")
    return raw


def parse_reply(data: str) -> Reply:
    reply = Reply.model_validate(decode_frame(data))
    if reply.v != 1 or (reply.ok and (reply.result is None or reply.error is not None)) or (not reply.ok and (reply.error is None or reply.result is not None)):
        raise ValueError("invalid reply")
    format_reply(reply)
    return reply


def format_error_reply(request_id: str, code: ErrorCode, message: str) -> str:
    return format_reply(Reply(v=1, id=request_id, ok=False, error=ErrorPayload(code=code.value, message=message)))


class SessionIceBundle(BaseModel):
    """ICE adapter contract: URL lists, optional paired TURN user/password only."""
    ice_servers: list[dict] = Field(repr=False)
    expires_at: int = Field(gt=0)
    renew_after: Literal[3300]
    relay_available: bool
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    @field_validator("renew_after", mode="before")
    def renewal(cls, value):
        if type(value) is not int:
            raise ValueError("invalid renewal interval")
        return value

    @field_validator("ice_servers")
    def servers(cls, value):
        for server in value:
            urls = server.get("urls")
            if (set(server) - {"urls", "username", "credential"}
                    or type(urls) is not list or not urls or len(urls) > 16):
                raise ValueError("invalid ICE server")
            for url in urls:
                validate_ice_url(url)
            turn = any(url.startswith(("turn:", "turns:")) for url in urls)
            if turn:
                if set(server) != {"urls", "username", "credential"} or any(
                    not isinstance(server[key], str) or not 0 < len(server[key]) <= 4096
                    for key in ("username", "credential")
                ):
                    raise ValueError("invalid TURN authentication")
            elif set(server) != {"urls"}:
                raise ValueError("STUN authentication is unsupported")
        if len(value) > 16:
            raise ValueError("too many ICE servers")
        return value


def validate_ice_url(value):
    import re
    if not isinstance(value, str) or len(value) > 2048 or not re.fullmatch(
        r"(?:stun|stuns|turn|turns):(?:[A-Za-z0-9.-]+|\[[0-9A-Fa-f:.]+\])(?::[0-9]{1,5})?(?:\?transport=(?:udp|tcp))?", value
    ):
        raise ValueError("invalid ICE URL")
    return value


class MediaBundles(BaseModel):
    host: SessionIceBundle
    viewer: SessionIceBundle
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class _MediaMessage(BaseModel):
    v: int
    id: str
    op: str
    payload: Dict[str, Any] = Field(repr=False)
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    @field_validator("v")
    def version(cls, value):
        if value != 1:
            raise ValueError("invalid version")
        return value

    @field_validator("id")
    def request_id(cls, value):
        import uuid
        uuid.UUID(value)
        return value

    @field_validator("payload")
    def fields(cls, value, info):
        import re
        import uuid
        op = info.data.get("op")
        required, optional = {
            "media_authorize": ({"routing_id", "session_id", "generation"}, set()),
            "media_release": ({"session_id", "generation"}, set()),
            "media_cancel": ({"installation_id", "host_epoch", "viewer_id"}, {"routing_id"}),
        }.get(op, (set(), set()))
        if not required or not required <= value.keys() or value.keys() - required - optional:
            raise ValueError("invalid media fields")
        for key, item in value.items():
            if key in {"generation", "host_epoch"}:
                if type(item) is not int or item < 1:
                    raise ValueError("invalid media revision")
            elif not isinstance(item, str) or not 0 < len(item) <= 128:
                raise ValueError("invalid media identifier")
            elif key in {"routing_id", "session_id"}:
                uuid.UUID(item)
            elif not re.fullmatch(r"[A-Za-z0-9_.:-]+", item):
                raise ValueError("invalid media owner")
        return value


class MediaAuthorization(_MediaMessage):
    op: Literal["media_authorize"]


class MediaRelease(_MediaMessage):
    op: Literal["media_release"]


class MediaCancellation(_MediaMessage):
    op: Literal["media_cancel"]


def parse_media_authorization(raw: str) -> MediaAuthorization:
    return MediaAuthorization.model_validate(decode_frame(raw))


def parse_media_release(raw: str) -> MediaRelease:
    return MediaRelease.model_validate(decode_frame(raw))


def parse_media_cancellation(raw: str) -> MediaCancellation:
    return MediaCancellation.model_validate(decode_frame(raw))
