import json
import pytest
from pydantic import ValidationError
from remote_protocol import (
    Command,
    Reply,
    ErrorPayload,
    ErrorCode,
    MAX_FRAME_BYTES,
    MAX_SDP_BYTES,
    MAX_PREVIEW_BYTES,
    MAX_PENDING_REQUESTS,
    parse_frame,
    format_command,
    format_reply,
    format_error_reply,
)


def test_command_valid_envelope():
    cmd = Command(
        v=1,
        id="550e8400-e29b-41d4-a716-446655440000",
        op="instances",
        payload={},
    )
    data = format_command(cmd)
    parsed = parse_frame(data)
    assert parsed.v == 1
    assert parsed.id == "550e8400-e29b-41d4-a716-446655440000"
    assert parsed.op == "instances"


def test_invalid_frames_are_bounded():
    # 1. Reject invalid version
    with pytest.raises(ValueError, match="version"):
        parse_frame('{"v": 2, "id": "550e8400-e29b-41d4-a716-446655440000", "op": "instances", "payload": {}}')

    # 2. Reject missing ID
    with pytest.raises(ValueError):
        parse_frame('{"v": 1, "op": "instances", "payload": {}}')

    # 3. Reject invalid UUID ID
    with pytest.raises(ValueError):
        parse_frame('{"v": 1, "id": "not-a-uuid", "op": "instances", "payload": {}}')

    # 4. Reject unknown operation
    with pytest.raises(ValueError, match="unknown operation"):
        parse_frame('{"v": 1, "id": "550e8400-e29b-41d4-a716-446655440000", "op": "arbitrary_eval", "payload": {}}')

    # 5. Reject oversized frame exceeding 1 MiB
    oversized = '{"v": 1, "id": "550e8400-e29b-41d4-a716-446655440000", "op": "instances", "payload": {"data": "' + ("a" * (MAX_FRAME_BYTES + 10)) + '"}}'
    with pytest.raises(ValueError, match="frame size"):
        parse_frame(oversized)


def test_reply_success_and_error_envelopes():
    # Success reply
    reply = Reply(
        v=1,
        id="550e8400-e29b-41d4-a716-446655440000",
        ok=True,
        result={"instances": []},
    )
    raw = format_reply(reply)
    assert '"ok":true' in raw or '"ok": true' in raw

    # Error reply
    err_reply = format_error_reply(
        request_id="550e8400-e29b-41d4-a716-446655440000",
        code=ErrorCode.NOT_PAIRED,
        message="Device is not paired with this installation",
    )
    assert '"ok":false' in err_reply or '"ok": false' in err_reply
    assert ErrorCode.NOT_PAIRED.value in err_reply


def test_protocol_bounds_constants():
    assert MAX_FRAME_BYTES == 1024 * 1024  # 1 MiB
    assert MAX_SDP_BYTES == 128 * 1024     # 128 KiB
    assert MAX_PREVIEW_BYTES == 384 * 1024 # 384 KiB
    assert MAX_PENDING_REQUESTS == 32

def test_specific_bounds_enforcement():
    # max SDP
    oversized_sdp = 'a' * (MAX_SDP_BYTES + 10)
    with pytest.raises(ValueError, match="SDP exceeds MAX_SDP_BYTES"):
        parse_frame(json.dumps({
            "v": 1, "id": "1", "op": "webrtc_offer", "payload": {"sdp": oversized_sdp}
        }))
        
    # max Preview (in reply formatting)
    oversized_preview = 'a' * int(MAX_PREVIEW_BYTES * 1.5)  # More than 384KiB after base64
    reply = Reply(v=1, id="1", ok=True, result={"image": oversized_preview})
    with pytest.raises(ValueError, match="preview exceeds MAX_PREVIEW_BYTES"):
        format_reply(reply)

