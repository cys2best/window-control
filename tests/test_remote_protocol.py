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
            "v": 1, "id": "550e8400-e29b-41d4-a716-446655440000", "op": "negotiate", "payload": {"generation": 1, "session_id": "550e8400-e29b-41d4-a716-446655440000", "offer": oversized_sdp, "timeout_ms": 30000}
        }))
        
    # max Preview (in reply formatting)
    oversized_preview = 'a' * int(MAX_PREVIEW_BYTES * 1.5)  # More than 384KiB after base64
    reply = Reply(v=1, id="1", ok=True, result={"mime": "image/jpeg", "data_base64": oversized_preview})
    with pytest.raises(ValueError, match="preview exceeds MAX_PREVIEW_BYTES"):
        format_reply(reply)



@pytest.mark.parametrize("op", ["pairing", "webrtc_offer", "webrtc_answer"])
def test_obsolete_operations_rejected(op):
    with pytest.raises(ValueError):
        parse_frame(json.dumps({"v": 1, "id": "550e8400-e29b-41d4-a716-446655440000", "op": op, "payload": {}}))


@pytest.mark.parametrize("payload", [[], {"url": "http://internal"}, {"installation_id": "other"}, {"role": "host"}, {"headers": {}}, {"token": "x"}])
def test_reserved_or_malformed_payload_rejected(payload):
    with pytest.raises(ValueError):
        parse_frame(json.dumps({"v": 1, "id": "550e8400-e29b-41d4-a716-446655440000", "op": "instances", "payload": payload}))


def test_answer_sdp_bound_and_malformed_json():
    with pytest.raises(ValueError, match="SDP"):
        format_reply(Reply(v=1, id="x", ok=True, result={"answer": "x" * (MAX_SDP_BYTES + 1)}))
    for raw in ("[]", "null", '{"v":1,"v":2}'):
        with pytest.raises(ValueError):
            parse_frame(raw)


@pytest.mark.parametrize("changes", [{"v": True}, {"ok": 1}, {"v": 2}, {"error": {"code": "unknown", "message": "x"}}, {"error": {"code": "offline", "message": "x", "owner": "host"}}])
def test_reply_rejects_invalid_formats(changes):
    from remote_protocol import parse_reply
    data = {"v": 1, "id": "request", "ok": False, "error": {"code": "offline", "message": "x"}}
    data.update(changes)
    with pytest.raises(ValueError):
        parse_reply(json.dumps(data))


def test_json_nesting_and_non_json_constants_rejected():
    from remote_protocol import decode_frame
    for raw in ('{"nested":' + '[' * 20000 + '0' + ']' * 20000 + '}', '{"number": NaN}'):
        with pytest.raises(ValueError):
            decode_frame(raw)


@pytest.mark.parametrize("op,parser,payload", [
    ("media_authorize", "parse_media_authorization", {"routing_id": "11111111-1111-1111-1111-111111111111", "session_id": "22222222-2222-2222-2222-222222222222", "generation": 1}),
    ("media_release", "parse_media_release", {"session_id": "22222222-2222-2222-2222-222222222222", "generation": 1}),
    ("media_cancel", "parse_media_cancellation", {"installation_id": "pc", "host_epoch": 1, "viewer_id": "phone"}),
])
def test_private_media_roles_are_strict(op, parser, payload):
    import remote_protocol
    decode = getattr(remote_protocol, parser)
    envelope = {"v": 1, "id": "33333333-3333-3333-3333-333333333333", "op": op, "payload": payload}
    assert decode(json.dumps(envelope)).payload == payload
    with pytest.raises(ValueError):
        parse_frame(json.dumps(envelope))
    for bad in ({**payload, "token": "secret"}, {**payload, "generation": True}, {}):
        with pytest.raises(ValueError):
            decode(json.dumps({**envelope, "payload": bad}))
    with pytest.raises(ValueError):
        decode(json.dumps({**envelope, "op": "instances"}))
    with pytest.raises(ValueError):
        decode(json.dumps(envelope).replace('"v": 1', '"v": 1, "v": 1'))


def test_session_ice_bundle_strict_shape_and_redacted_repr():
    from remote_protocol import SessionIceBundle, MediaBundles
    values = dict(ice_servers=[{"urls": ["stun:stun.example:3478"]}, {"urls": ["turn:turn.example:3478?transport=udp"], "username": "temporary", "credential": "private-password"}], expires_at=4600, renew_after=3300, relay_available=True)
    bundle = SessionIceBundle(**values)
    assert "private-password" not in repr(bundle)
    assert "private-password" not in repr(MediaBundles(host=bundle, viewer=bundle))
    for changes in ({"expires_at": True}, {"renew_after": 3600}, {"relay_available": 1}, {"ice_servers": [{"urls": "stun:example"}]}, {"ice_servers": [{"urls": ["turn:example"], "credential": "missing-user"}]}, {"ice_servers": [{"urls": ["https://internal"]}]}, {"ice_servers": [{"urls": ["stun:example"], "secret": "bad"}]}):
        with pytest.raises(ValueError):
            SessionIceBundle(**{**values, **changes})


def test_protocol_repr_and_validation_errors_exclude_credentials():
    from remote_protocol import RoutingContext, RoutedCommand
    command = Command(v=1, id="11111111-1111-1111-1111-111111111111", op="viewer_auth", payload={"installation_id": "pc", "token": "private-device-token"})
    assert "private-device-token" not in repr(command)
    reply = Reply(v=1, id=command.id, ok=True, result={"credential": "private-turn-password"})
    assert "private-turn-password" not in repr(reply)
    with pytest.raises(ValueError) as error:
        Command(v=1, id=command.id, op="viewer_auth", payload={"installation_id": "pc", "token": "private-device-token", "unexpected": True})
    assert "private-device-token" not in str(error.value)
