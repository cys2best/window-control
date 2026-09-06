import os
import pytest


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Ensure offline unit tests run with auth disabled by default unless explicitly enabled."""
    is_auth_test = "test_app_auth" in os.environ.get("PYTEST_CURRENT_TEST", "")
    if not is_auth_test:
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        try:
            import config
            monkeypatch.setattr(config, "SUPABASE_URL", None)
        except ImportError:
            pass
    if "AUTH_TOKEN" in os.environ:
        monkeypatch.delenv("AUTH_TOKEN", raising=False)
