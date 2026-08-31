"""Typed HTTP client for engine.exe's loopback /admin endpoint.

The engine.exe Windows process runs a local HTTP listener on localhost:{admin_port}
with endpoints for:
  - GET /health: returns health status (state, generation, dimensions)
  - POST /reconnect: triggers scrcpy reconnect
  - POST /keyframe: requests IDR keyframe

This module provides a strongly-typed client that maps these HTTP calls to
Python methods and cleanly separates three failure modes:
  - EngineAdminUnavailable: transport-level failure (connection refused, timeout)
  - EngineAdminProtocolError: engine responded but with bad/unexpected data
  - ReconnectRejected: engine rejected reconnect due to stale generation (normal)
"""

import json
from dataclasses import dataclass
from typing import Literal, Optional

import httpx


@dataclass(frozen=True)
class EngineHealth:
    """Current health status of the engine process."""
    state: Literal["connected", "stalled", "disconnected"]
    generation: int
    width: int
    height: int


class EngineAdminUnavailable(RuntimeError):
    """Transport-level failure: admin port is unreachable.

    Raised when:
      - Connection refused (port not listening)
      - Connection timeout
      - Socket/network error

    Indicates the engine process may be dead or the port is wrong.
    """
    pass


class EngineAdminProtocolError(RuntimeError):
    """Protocol-level failure: engine responded but with bad/unexpected data.

    Raised when:
      - Response is not valid JSON
      - Response JSON is missing required fields
      - Response field types are wrong
      - Response HTTP status is unexpectedly 4xx/5xx (except 409 for reconnect)

    Indicates a protocol mismatch or engine bug, not a network problem.
    """
    pass


class ReconnectRejected(RuntimeError):
    """Reconnect was rejected by engine due to stale generation.

    This is a normal application-level rejection when the requested generation
    is behind the engine's current generation. The current generation is available
    as an attribute.
    """
    def __init__(self, current_generation: int):
        self.current_generation = current_generation
        super().__init__(f"reconnect rejected at generation {current_generation}")


class EngineAdminClient:
    """Typed client for engine.exe's loopback /admin HTTP listener."""

    def __init__(
        self,
        http_client: Optional[httpx.Client] = None,
        timeout_seconds: float = 5.0,
    ):
        """Initialize the admin client.

        Args:
            http_client: Optional httpx.Client to use. If not provided, one is
                created internally (and closed on __del__).
            timeout_seconds: HTTP request timeout in seconds (default 5.0).
        """
        self._http_client = http_client
        self._owns_client = http_client is None
        self._timeout = httpx.Timeout(timeout_seconds)

    def __del__(self):
        """Close the client if we created it."""
        if self._owns_client and self._http_client is not None:
            try:
                self._http_client.close()
            except Exception:
                pass

    def _get_client(self) -> httpx.Client:
        """Get or create the HTTP client."""
        if self._http_client is None:
            self._http_client = httpx.Client(timeout=self._timeout)
        return self._http_client

    def health(self, admin_port: int) -> EngineHealth:
        """Fetch current engine health status.

        Args:
            admin_port: Port number of the engine's /admin listener.

        Returns:
            EngineHealth with current state, generation, and dimensions.

        Raises:
            EngineAdminUnavailable: Port is unreachable.
            EngineAdminProtocolError: Response is invalid or malformed.
        """
        url = f"http://127.0.0.1:{admin_port}/health"
        try:
            response = self._get_client().get(url, timeout=self._timeout)
        except httpx.TransportError as e:
            raise EngineAdminUnavailable(
                f"admin port {admin_port} unreachable: {e}"
            ) from e

        # Parse and validate response
        try:
            data = response.json()
        except (json.JSONDecodeError, ValueError) as e:
            raise EngineAdminProtocolError(
                f"health response is not valid JSON: {e}"
            ) from e

        # Validate required fields and types
        try:
            state = data["state"]
            generation = data["generation"]
            width = data["width"]
            height = data["height"]

            # Type validation
            if not isinstance(state, str):
                raise TypeError(f"state must be string, got {type(state).__name__}")
            if state not in ("connected", "stalled", "disconnected"):
                raise ValueError(f"state must be one of connected/stalled/disconnected, got {state}")
            if not isinstance(generation, int):
                raise TypeError(f"generation must be int, got {type(generation).__name__}")
            if not isinstance(width, int):
                raise TypeError(f"width must be int, got {type(width).__name__}")
            if not isinstance(height, int):
                raise TypeError(f"height must be int, got {type(height).__name__}")

        except (KeyError, TypeError, ValueError) as e:
            raise EngineAdminProtocolError(
                f"health response has invalid fields: {e}"
            ) from e

        return EngineHealth(
            state=state,  # type: ignore
            generation=generation,
            width=width,
            height=height,
        )

    def reconnect(self, admin_port: int, scrcpy_port: int, generation: int) -> int:
        """Trigger a scrcpy reconnect at the given generation.

        Args:
            admin_port: Port number of the engine's /admin listener.
            scrcpy_port: Port to reconnect to for scrcpy server.
            generation: Generation number being requested.

        Returns:
            New generation number if reconnect was accepted.

        Raises:
            EngineAdminUnavailable: Port is unreachable.
            EngineAdminProtocolError: Response is invalid, malformed, or
                reflects an unexpected status (400/5xx).
            ReconnectRejected: Engine rejected reconnect (409 stale generation).
        """
        url = f"http://127.0.0.1:{admin_port}/reconnect"
        payload = {
            "scrcpy_port": scrcpy_port,
            "generation": generation,
        }

        try:
            response = self._get_client().post(
                url,
                json=payload,
                timeout=self._timeout,
            )
        except httpx.TransportError as e:
            raise EngineAdminUnavailable(
                f"admin port {admin_port} unreachable: {e}"
            ) from e

        # Handle 409 Conflict: stale generation
        if response.status_code == 409:
            try:
                data = response.json()
                current_gen = data.get("current_generation")
                if current_gen is None or not isinstance(current_gen, int):
                    raise EngineAdminProtocolError(
                        f"409 response missing valid current_generation: {data}"
                    )
                raise ReconnectRejected(current_gen)
            except (json.JSONDecodeError, ValueError) as e:
                raise EngineAdminProtocolError(
                    f"409 response is not valid JSON: {e}"
                ) from e

        # Only 200 with accepted=true is success
        if response.status_code != 200:
            raise EngineAdminProtocolError(
                f"reconnect returned unexpected status {response.status_code}"
            )

        try:
            data = response.json()
        except (json.JSONDecodeError, ValueError) as e:
            raise EngineAdminProtocolError(
                f"reconnect response is not valid JSON: {e}"
            ) from e

        # Validate response structure
        try:
            accepted = data.get("accepted")
            response_gen = data.get("generation")

            if not isinstance(accepted, bool):
                raise TypeError(f"accepted must be bool, got {type(accepted).__name__}")
            if not isinstance(response_gen, int):
                raise TypeError(f"generation must be int, got {type(response_gen).__name__}")

            if not accepted:
                raise EngineAdminProtocolError(
                    f"reconnect returned 200 but accepted=false"
                )

            # Validate response generation matches requested
            if response_gen != generation:
                raise EngineAdminProtocolError(
                    f"reconnect response generation {response_gen} does not match requested {generation}"
                )

        except (KeyError, TypeError, ValueError) as e:
            raise EngineAdminProtocolError(
                f"reconnect response has invalid fields: {e}"
            ) from e

        return response_gen

    def keyframe(self, admin_port: int) -> None:
        """Request an IDR keyframe from the engine.

        Args:
            admin_port: Port number of the engine's /admin listener.

        Returns:
            None (204 No Content expected).

        Raises:
            EngineAdminUnavailable: Port is unreachable.
            EngineAdminProtocolError: Response has unexpected status.
        """
        url = f"http://127.0.0.1:{admin_port}/keyframe"

        try:
            response = self._get_client().post(url, timeout=self._timeout)
        except httpx.TransportError as e:
            raise EngineAdminUnavailable(
                f"admin port {admin_port} unreachable: {e}"
            ) from e

        if response.status_code != 204:
            raise EngineAdminProtocolError(
                f"keyframe returned unexpected status {response.status_code}, expected 204"
            )
