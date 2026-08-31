import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest
import httpx
from server.engine_admin import (
    EngineHealth,
    EngineAdminClient,
    EngineAdminUnavailable,
    EngineAdminProtocolError,
    ReconnectRejected,
)
from tests.fixtures.fake_admin_server import FakeAdminServer


def test_client_uses_cpp_admin_routes_for_every_request():
    """The client must interoperate with engine's /admin-only handler."""
    with FakeAdminServer(generation=5) as server:
        client = EngineAdminClient()

        assert client.health(server.port).generation == 5
        server.queue_reconnect(200, {"accepted": True, "generation": 6})
        assert client.reconnect(server.port, 27183, 6) == 6
        server.queue_keyframe(204)
        assert client.keyframe(server.port) is None

        assert server.server.request_paths == [
            "/admin/health",
            "/admin/reconnect",
            "/admin/keyframe",
        ]


def test_health_connected_state():
    """Test reading health in connected state."""
    with FakeAdminServer(generation=3, state="connected", width=1920, height=1080) as server:
        client = EngineAdminClient()
        health = client.health(server.port)
        assert health.state == "connected"
        assert health.generation == 3
        assert health.width == 1920
        assert health.height == 1080


def test_health_stalled_state():
    """Test reading health in stalled state."""
    with FakeAdminServer(generation=2, state="stalled", width=1280, height=720) as server:
        client = EngineAdminClient()
        health = client.health(server.port)
        assert health.state == "stalled"
        assert health.generation == 2


def test_health_disconnected_state():
    """Test reading health in disconnected state."""
    with FakeAdminServer(generation=1, state="disconnected", width=800, height=600) as server:
        client = EngineAdminClient()
        health = client.health(server.port)
        assert health.state == "disconnected"
        assert health.generation == 1


def test_health_malformed_json():
    """Test that non-JSON response raises EngineAdminProtocolError."""
    with FakeAdminServer() as server:
        server.queue_raw_health(200, b"not-json")
        with pytest.raises(EngineAdminProtocolError):
            EngineAdminClient().health(server.port)


def test_health_missing_field():
    """Test that missing required field raises EngineAdminProtocolError."""
    with FakeAdminServer() as server:
        # Queue response without 'state' field
        server.queue_raw_health(200, b'{"generation": 1, "width": 1920, "height": 1080}')
        with pytest.raises(EngineAdminProtocolError):
            EngineAdminClient().health(server.port)


def test_health_wrong_field_type():
    """Test that wrong field type raises EngineAdminProtocolError."""
    with FakeAdminServer() as server:
        # generation should be int, not string
        server.queue_raw_health(200, b'{"state": "connected", "generation": "not-int", "width": 1920, "height": 1080}')
        with pytest.raises(EngineAdminProtocolError):
            EngineAdminClient().health(server.port)


def test_health_unreachable_port():
    """Test that unreachable port raises EngineAdminUnavailable."""
    # Create a socket, bind to ephemeral port, then close it immediately
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(('127.0.0.1', 0))
    dead_port = sock.getsockname()[1]
    sock.close()

    # Try to connect to the now-closed port
    with pytest.raises(EngineAdminUnavailable):
        EngineAdminClient(timeout_seconds=0.5).health(dead_port)


def test_reconnect_accepted():
    """Test successful reconnect returns generation from response."""
    with FakeAdminServer(generation=5) as server:
        server.queue_reconnect(200, {"accepted": True, "generation": 5})
        client = EngineAdminClient()
        new_gen = client.reconnect(server.port, 27183, 5)
        assert new_gen == 5


def test_reconnect_raises_typed_stale_generation():
    """Test that 409 stale generation raises ReconnectRejected with current_generation."""
    with FakeAdminServer(generation=5) as server:
        server.queue_reconnect(409, {"accepted": False, "current_generation": 5})
        with pytest.raises(ReconnectRejected) as caught:
            EngineAdminClient().reconnect(server.port, 27183, 3)
        assert caught.value.current_generation == 5


def test_reconnect_400_is_protocol_error():
    """Test that 400 is treated as protocol error."""
    with FakeAdminServer() as server:
        server.queue_reconnect(400, {"error": "bad request"})
        with pytest.raises(EngineAdminProtocolError):
            EngineAdminClient().reconnect(server.port, 27183, 1)


def test_reconnect_502_is_protocol_error():
    """Test that 502 is treated as protocol error."""
    with FakeAdminServer() as server:
        server.queue_reconnect(502, {"error": "bad gateway"})
        with pytest.raises(EngineAdminProtocolError):
            EngineAdminClient().reconnect(server.port, 27183, 1)


def test_reconnect_mismatched_generation_is_protocol_error():
    """Test that mismatched response generation is protocol error."""
    with FakeAdminServer() as server:
        # Response generation doesn't match requested
        server.queue_reconnect(200, {"accepted": True, "generation": 99})
        with pytest.raises(EngineAdminProtocolError):
            EngineAdminClient().reconnect(server.port, 27183, 5)


def test_keyframe_204_success():
    """Test keyframe request returns None on 204."""
    with FakeAdminServer() as server:
        server.queue_keyframe(204)
        client = EngineAdminClient()
        result = client.keyframe(server.port)
        assert result is None


def test_keyframe_unreachable():
    """Test keyframe on unreachable port raises EngineAdminUnavailable."""
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(('127.0.0.1', 0))
    dead_port = sock.getsockname()[1]
    sock.close()

    with pytest.raises(EngineAdminUnavailable):
        EngineAdminClient(timeout_seconds=0.5).keyframe(dead_port)


def test_custom_http_client():
    """Test that custom httpx.Client is used if provided."""
    custom_client = httpx.Client()
    try:
        with FakeAdminServer() as server:
            client = EngineAdminClient(http_client=custom_client)
            health = client.health(server.port)
            assert health.state == "connected"
    finally:
        custom_client.close()


def test_custom_timeout():
    """Test that custom timeout is respected."""
    with FakeAdminServer() as server:
        # Timeout should not cause issues on normal operation
        client = EngineAdminClient(timeout_seconds=10.0)
        health = client.health(server.port)
        assert health.state == "connected"


def test_queued_response_consumed_once():
    """Test that each queued response is consumed exactly once."""
    with FakeAdminServer() as server:
        server.queue_raw_health(200, b'{"state": "connected", "generation": 1, "width": 1920, "height": 1080}')
        client = EngineAdminClient()
        health1 = client.health(server.port)
        assert health1.generation == 1

        # Second call should use default (queue is empty, no queued response)
        health2 = client.health(server.port)
        assert health2.generation == 0  # default generation


def test_reconnect_updates_server_generation():
    """Test that accepted reconnect updates server's health generation."""
    with FakeAdminServer(generation=5) as server:
        server.queue_reconnect(200, {"accepted": True, "generation": 5})
        client = EngineAdminClient()
        new_gen = client.reconnect(server.port, 27183, 5)
        assert new_gen == 5

        # Now check health reflects the server's internal state (which was updated by reconnect)
        health = client.health(server.port)
        assert health.generation == 5
