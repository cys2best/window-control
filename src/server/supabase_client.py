"""Thin REST wrapper around Supabase PostgREST for the `installs` table.

JWT verification happens locally in auth.py (verify_supabase_jwt) -- this
module only talks to Supabase for the one thing that needs a live round
trip: registering which account owns this PC install. Uses the
service-role key because FastAPI has already authenticated the caller.
"""

import httpx


class SupabaseUnavailable(Exception):
    pass


class SupabaseClient:
    def __init__(self, url: str, service_role_key: str, timeout: float = 5.0):
        self._base = url.rstrip("/") + "/rest/v1"
        self._headers = {
            "apikey": service_role_key,
            "Authorization": f"Bearer {service_role_key}",
            "Content-Type": "application/json",
        }
        self._timeout = timeout

    def upsert_install(self, user_id: str, public_key: str) -> None:
        try:
            r = httpx.post(
                f"{self._base}/installs",
                params={"on_conflict": "public_key"},
                json={"public_key": public_key, "user_id": user_id},
                headers={**self._headers, "Prefer": "resolution=merge-duplicates"},
                timeout=self._timeout,
            )
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise SupabaseUnavailable(str(e)) from e
