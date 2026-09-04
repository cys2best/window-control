"""Thin REST wrapper around Supabase PostgREST for the device_links table.

JWT verification happens locally in auth.py (verify_supabase_jwt) — this
module only talks to Supabase for the one thing that needs a live round
trip: reading and writing device_links rows. Uses the service-role key
because FastAPI has already authenticated the caller and enforces
ownership itself (see the plan's "Deviation from spec" note) — this is
the same trust boundary FastAPI already holds over InstanceManager.
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

    def list_linked_instance_ids(self, user_id: str) -> list[str]:
        try:
            r = httpx.get(
                f"{self._base}/device_links",
                params={"user_id": f"eq.{user_id}", "select": "instance_id"},
                headers=self._headers, timeout=self._timeout,
            )
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise SupabaseUnavailable(str(e)) from e
        return [row["instance_id"] for row in r.json()]

    def link_instance(self, user_id: str, instance_id: str) -> bool:
        try:
            existing = httpx.get(
                f"{self._base}/device_links",
                params={"instance_id": f"eq.{instance_id}", "select": "user_id"},
                headers=self._headers, timeout=self._timeout,
            )
            existing.raise_for_status()
            if existing.json():
                return False
            r = httpx.post(
                f"{self._base}/device_links",
                json={"user_id": user_id, "instance_id": instance_id},
                headers=self._headers, timeout=self._timeout,
            )
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise SupabaseUnavailable(str(e)) from e
        return True

    def unlink_instance(self, user_id: str, instance_id: str) -> None:
        try:
            r = httpx.delete(
                f"{self._base}/device_links",
                params={"user_id": f"eq.{user_id}", "instance_id": f"eq.{instance_id}"},
                headers=self._headers, timeout=self._timeout,
            )
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise SupabaseUnavailable(str(e)) from e
