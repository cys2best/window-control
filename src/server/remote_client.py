"""Bounded outbound host connection; credentials live only in first-frame auth."""

import asyncio
import logging
import queue
import random
import ssl
import threading
import uuid

from websockets.asyncio.client import connect
from remote_protocol import (Command, Reply, ErrorPayload, ErrorCode, MAX_FRAME_BYTES,
                             MAX_PENDING_REQUESTS, decode_frame, parse_reply, parse_routed_frame,
                             format_reply, DeviceInvalidated)
from server.remote_identity import service_origin
from server.remote_pairing import RemotePairing, RemotePairingError


class _NoRedirectConnect(connect):
    def process_redirect(self, exc):
        # Never authenticate an installation to a redirect target.
        return exc


class RemoteHostClient:
    def __init__(self, service_url, identity, dispatcher, *, allow_insecure_localhost=False,
                 connector=None, on_state=None, on_device_invalidated=None):
        origin = service_origin(service_url, allow_insecure_localhost=allow_insecure_localhost)
        self.url = origin.replace("https://", "wss://").replace("http://", "ws://") + "/connect"
        self.identity = identity
        self.dispatcher = dispatcher
        self.connector = connector or _NoRedirectConnect
        self.on_state = on_state
        self.on_device_invalidated = on_device_invalidated
        self.ready = asyncio.Event()
        self.last_error = ""
        self.host_epoch = 0
        self.connection = None
        self._queue = queue.Queue(MAX_PENDING_REQUESTS)
        self._lock = threading.Lock()
        self._loop = None
        self._wake = None
        self._accepting = False
        self._known_devices = set()
        self._lifecycle_ids = set()
        self.remote_pairing = RemotePairing(dispatcher.pairing, identity, service_url,
                                           publish=self.publish_pairing, close=self.close_pairing,
                                           on_error=self._pairing_error,
                                           allow_insecure_localhost=allow_insecure_localhost)
        # websockets debug frames contain secrets and SDP; disable its logger.
        self._wire_logger = logging.Logger("remote_host_wire")
        self._wire_logger.disabled = True

    def _notify(self, error=""):
        self.last_error = error
        if self.on_state is not None:
            self.on_state(self.remote_pairing if self.ready.is_set() else None, error)

    def _pairing_error(self, error):
        self._notify(str(error))
        if self._loop is not None and self.connection is not None:
            connection = self.connection
            self._loop.call_soon_threadsafe(lambda: asyncio.create_task(connection.close()))

    def _enqueue(self, raw, lifecycle_id=None):
        with self._lock:
            if not self._accepting or self._loop is None:
                raise RemotePairingError(ErrorCode.OFFLINE, "Remote host is offline")
            if lifecycle_id is not None and len(self._lifecycle_ids) >= MAX_PENDING_REQUESTS:
                raise RemotePairingError(ErrorCode.BUSY, "Remote channel queue is full")
            try:
                self._queue.put_nowait(raw)
            except queue.Full:
                raise RemotePairingError(ErrorCode.BUSY, "Remote channel queue is full") from None
            if lifecycle_id is not None:
                self._lifecycle_ids.add(lifecycle_id)
            self._loop.call_soon_threadsafe(self._wake.set)

    def _lifecycle(self, op, payload):
        command = Command(v=1, id=str(uuid.uuid4()), op=op, payload=payload)
        self._enqueue(command.model_dump_json(), command.id)

    def publish_pairing(self, invitation) -> None:
        self._lifecycle("pairing_open", {"handle": invitation.handle, "expires_at": invitation.expires_at})

    def close_pairing(self, handle) -> None:
        self._lifecycle("pairing_close", {"handle": handle})

    def invalidate_device(self, device_id) -> None:
        notification = DeviceInvalidated(v=1, id=str(uuid.uuid4()), op="device_invalidated", payload={"device_id": device_id})
        self._enqueue(notification.model_dump_json())
        if self.on_device_invalidated is not None:
            # This callback must enqueue media teardown, without blocking.
            self.on_device_invalidated(device_id)

    async def run(self, stop: asyncio.Event) -> None:
        """Server-loop lifetime; stop cancels connect, auth, backoff and dispatch."""
        worker = asyncio.create_task(self._reconnect(stop))
        stopped = asyncio.create_task(stop.wait())
        try:
            await asyncio.wait({worker, stopped}, return_when=asyncio.FIRST_COMPLETED)
            if worker.done():
                await worker
        finally:
            worker.cancel()
            stopped.cancel()
            await asyncio.gather(worker, stopped, return_exceptions=True)
            self.remote_pairing.shutdown()
            self._notify(self.last_error or "Remote host is offline")

    async def _reconnect(self, stop):
        attempt = 0
        try:
            while not stop.is_set():
                authenticated = False
                try:
                    self._loop = asyncio.get_running_loop()
                    self._wake = asyncio.Event()
                    options = dict(open_timeout=10, close_timeout=1, max_size=MAX_FRAME_BYTES,
                                   max_queue=MAX_PENDING_REQUESTS, compression=None,
                                   proxy=None, logger=self._wire_logger)
                    if self.url.startswith("wss:"):
                        options["ssl"] = ssl.create_default_context()
                    self.connection = await self.connector(self.url, **options)
                    command = Command(v=1, id=str(uuid.uuid4()), op="host_auth", payload={"installation_id": self.identity.installation_id, "credential": self.identity.credential})
                    await self.connection.send(command.model_dump_json())
                    reply = parse_reply(await asyncio.wait_for(self.connection.recv(), 10))
                    epoch = (reply.result or {}).get("host_epoch")
                    if reply.id != command.id or not reply.ok or (reply.result or {}).get("authenticated") is not True or type(epoch) is not int or epoch < 1:
                        raise ValueError("host authentication failed")
                    self.host_epoch = epoch
                    with self._lock:
                        self._accepting = True
                    self.remote_pairing.set_online(True)
                    self.ready.set()
                    authenticated = True
                    attempt = 0
                    self._notify()
                    await self._connected()
                except ssl.SSLCertVerificationError:
                    self._notify("Remote service certificate could not be verified")
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self._notify("Remote service is unavailable")
                finally:
                    self.ready.clear()
                    # First clear invitations while close can still enqueue. All
                    # work is discarded below, never replayed after reconnect.
                    self.remote_pairing.set_online(False)
                    with self._lock:
                        self._accepting = False
                        self._lifecycle_ids.clear()
                        while not self._queue.empty():
                            self._queue.get_nowait()
                    if self.connection is not None:
                        await self.connection.close()
                    self.connection = None
                    if authenticated:
                        self._notify("Remote host is offline")
                if not stop.is_set():
                    delay = (1, 2, 4, 8, 16, 30)[min(attempt, 5)]
                    attempt += 1
                    await asyncio.sleep(delay * random.uniform(.8, 1.2))
        finally:
            self._loop = None

    async def _connected(self):
        commands = {}
        failed = asyncio.get_running_loop().create_future()

        def command_done(task):
            if not task.cancelled():
                exception = task.exception()
                if exception is not None and not failed.done():
                    # Queue rejection tears down the authenticated connection.
                    failed.set_exception(exception)

        async def sender():
            while True:
                await self._wake.wait()
                self._wake.clear()
                while True:
                    try:
                        raw = self._queue.get_nowait()
                    except queue.Empty:
                        break
                    await self.connection.send(raw)

        async def revoked_devices():
            while True:
                await asyncio.sleep(1)
                current = {device.id for device in self.dispatcher.pairing.list_devices()}
                for device_id in self._known_devices - current:
                    # Enqueue failure aborts connection rather than hiding revocation.
                    self.invalidate_device(device_id)
                self._known_devices.intersection_update(current)
                self.remote_pairing.active_invitation()  # Close expired handles.

        async def execute(routed, received_at):
            command = routed.command()
            token = routed.context.token
            try:
                if command.op == "pair":
                    result = await asyncio.to_thread(self.remote_pairing.submit, command.payload["handle"], command.payload["code"], command.payload.get("device_name", ""))
                    device = self.dispatcher.pairing.device_for_token(result["token"])
                    if device is None:
                        raise RemotePairingError(ErrorCode.NOT_PAIRED, "Device was removed")
                    self._known_devices.add(device.id)
                    reply = Reply(v=1, id=command.id, ok=True, result={**result, "device_id": device.id})
                elif command.op == "viewer_auth":
                    device = self.dispatcher.pairing.device_for_token(token)
                    if device is None or command.payload["token"] != token or command.payload["installation_id"] != self.identity.installation_id:
                        raise RemotePairingError(ErrorCode.NOT_PAIRED, "Device is not paired")
                    self._known_devices.add(device.id)
                    reply = Reply(v=1, id=command.id, ok=True, result={"authenticated": True, "device_id": device.id})
                else:
                    if command.op == "negotiate":
                        remaining = command.payload["timeout_ms"] / 1000 - (asyncio.get_running_loop().time() - received_at)
                        if remaining <= 0:
                            raise asyncio.TimeoutError
                        command.payload["timeout_ms"] = max(1, int(remaining * 1000))
                        async with asyncio.timeout(remaining):
                            reply = await self.dispatcher.dispatch(command, token)
                    else:
                        async with asyncio.timeout(30):
                            reply = await self.dispatcher.dispatch(command, token)
                self._enqueue(format_reply(reply))
            except RemotePairingError as exc:
                self._enqueue(format_reply(Reply(v=1, id=command.id, ok=False, error=ErrorPayload(code=exc.code.value, message=str(exc)))))
            except asyncio.TimeoutError:
                self._enqueue(format_reply(Reply(v=1, id=command.id, ok=False, error=ErrorPayload(code="timeout", message="Remote command timed out"))))
            except Exception:
                self._enqueue(format_reply(Reply(v=1, id=command.id, ok=False, error=ErrorPayload(code="unavailable", message="Remote action failed"))))
            finally:
                commands.pop(command.id, None)

        async def receiver():
            async for raw in self.connection:
                parsed = decode_frame(raw)
                if "ok" in parsed:
                    reply = parse_reply(raw)
                    with self._lock:
                        if reply.id not in self._lifecycle_ids:
                            raise ValueError("unexpected lifecycle reply")
                        self._lifecycle_ids.remove(reply.id)
                    if not reply.ok:
                        raise ValueError("owner lifecycle command rejected")
                    continue
                routed = parse_routed_frame(raw)
                if (routed.context.installation_id, routed.context.host_epoch) != (self.identity.installation_id, self.host_epoch):
                    raise ValueError("invalid routing ownership")
                if routed.id in commands:
                    raise ValueError("duplicate active request")
                if len(commands) >= MAX_PENDING_REQUESTS:
                    self._enqueue(format_reply(Reply(v=1, id=routed.id, ok=False, error=ErrorPayload(code="busy", message="Host command queue is full"))))
                    continue
                task = asyncio.create_task(execute(routed, asyncio.get_running_loop().time()))
                commands[routed.id] = task
                task.add_done_callback(command_done)

        tasks = [asyncio.create_task(sender()), asyncio.create_task(receiver()), asyncio.create_task(revoked_devices()), failed]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                await task
        finally:
            all_tasks = tasks + list(commands.values())
            for task in all_tasks:
                task.cancel()
            await asyncio.gather(*all_tasks, return_exceptions=True)
