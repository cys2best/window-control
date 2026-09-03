import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import httpx
import pytest
from unittest.mock import patch, MagicMock

from server.supabase_client import SupabaseClient, SupabaseUnavailable

BASE = "https://project.supabase.co"


@pytest.fixture
def client():
    return SupabaseClient(BASE, "service-role-key")


def test_list_linked_instance_ids_returns_instance_ids(client):
    with patch("server.supabase_client.httpx.get") as mock_get:
        mock_get.return_value = httpx.Response(
            200,
            json=[
                {"instance_id": "adb:emulator-5554"},
                {"instance_id": "adb:emulator-5556"},
            ],
            request=httpx.Request("GET", f"{BASE}/rest/v1/device_links"),
        )
        result = client.list_linked_instance_ids("user-1")

    assert result == ["adb:emulator-5554", "adb:emulator-5556"]
    mock_get.assert_called_once()
    args, kwargs = mock_get.call_args
    assert args[0] == f"{BASE}/rest/v1/device_links"
    assert kwargs["params"] == {"user_id": "eq.user-1", "select": "instance_id"}


def test_list_linked_instance_ids_raises_on_network_failure(client):
    with patch("server.supabase_client.httpx.get", side_effect=httpx.ConnectError("boom")):
        with pytest.raises(SupabaseUnavailable):
            client.list_linked_instance_ids("user-1")


def test_link_instance_succeeds_when_unclaimed(client):
    with patch("server.supabase_client.httpx.get") as mock_get, \
         patch("server.supabase_client.httpx.post") as mock_post:
        mock_get.return_value = httpx.Response(
            200, json=[],
            request=httpx.Request("GET", f"{BASE}/rest/v1/device_links"),
        )
        mock_post.return_value = httpx.Response(
            201, json=[{"user_id": "user-1", "instance_id": "adb:x"}],
            request=httpx.Request("POST", f"{BASE}/rest/v1/device_links"),
        )

        result = client.link_instance("user-1", "adb:x")

    assert result is True
    mock_get.assert_called_once()
    get_args, get_kwargs = mock_get.call_args
    assert get_args[0] == f"{BASE}/rest/v1/device_links"
    assert get_kwargs["params"] == {"instance_id": "eq.adb:x", "select": "user_id"}

    mock_post.assert_called_once()
    post_args, post_kwargs = mock_post.call_args
    assert post_args[0] == f"{BASE}/rest/v1/device_links"
    assert post_kwargs["json"] == {"user_id": "user-1", "instance_id": "adb:x"}


def test_link_instance_returns_false_when_already_claimed(client):
    with patch("server.supabase_client.httpx.get") as mock_get, \
         patch("server.supabase_client.httpx.post") as mock_post:
        mock_get.return_value = httpx.Response(
            200, json=[{"user_id": "other-user"}],
            request=httpx.Request("GET", f"{BASE}/rest/v1/device_links"),
        )

        result = client.link_instance("user-1", "adb:x")

    assert result is False
    mock_post.assert_not_called()


def test_link_instance_raises_on_network_failure(client):
    with patch("server.supabase_client.httpx.get", side_effect=httpx.ConnectError("boom")):
        with pytest.raises(SupabaseUnavailable):
            client.link_instance("user-1", "adb:x")


def test_unlink_instance_deletes_the_row(client):
    with patch("server.supabase_client.httpx.delete") as mock_delete:
        mock_delete.return_value = httpx.Response(
            204,
            request=httpx.Request("DELETE", f"{BASE}/rest/v1/device_links"),
        )

        client.unlink_instance("user-1", "adb:x")

    mock_delete.assert_called_once()
    args, kwargs = mock_delete.call_args
    assert args[0] == f"{BASE}/rest/v1/device_links"
    assert kwargs["params"] == {"user_id": "eq.user-1", "instance_id": "eq.adb:x"}


def test_unlink_instance_raises_on_network_failure(client):
    with patch("server.supabase_client.httpx.delete", side_effect=httpx.ConnectError("boom")):
        with pytest.raises(SupabaseUnavailable):
            client.unlink_instance("user-1", "adb:x")
