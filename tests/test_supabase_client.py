import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import httpx
import pytest
from unittest.mock import patch

from server.supabase_client import SupabaseClient, SupabaseUnavailable

BASE = "https://project.supabase.co"


@pytest.fixture
def client():
    return SupabaseClient(BASE, "service-role-key")


def test_upsert_install_posts_with_merge_on_conflict(client):
    with patch("server.supabase_client.httpx.post") as mock_post:
        mock_post.return_value = httpx.Response(
            201, json=[{"public_key": "pub-1", "user_id": "user-1"}],
            request=httpx.Request("POST", f"{BASE}/rest/v1/installs"),
        )

        client.upsert_install("user-1", "pub-1")

    mock_post.assert_called_once()
    args, kwargs = mock_post.call_args
    assert args[0] == f"{BASE}/rest/v1/installs"
    assert kwargs["params"] == {"on_conflict": "public_key"}
    assert kwargs["json"] == {"public_key": "pub-1", "user_id": "user-1"}
    assert kwargs["headers"]["Prefer"] == "resolution=merge-duplicates"


def test_upsert_install_raises_on_network_failure(client):
    with patch("server.supabase_client.httpx.post", side_effect=httpx.ConnectError("boom")):
        with pytest.raises(SupabaseUnavailable):
            client.upsert_install("user-1", "pub-1")
