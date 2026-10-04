"""Ephemeral routing state scoped to installation, connection epoch and viewer."""

import asyncio
from collections import OrderedDict
from dataclasses import dataclass, field
import time
import uuid

from remote_protocol import MAX_PENDING_REQUESTS, Reply, ErrorPayload, ErrorCode


MAX_SETTLED_REQUESTS = 2048
SETTLED_REQUEST_TTL_SECONDS = 60


class HostConnection:
    def __init__(self, websocket, installation_id, epoch):
        self.websocket = websocket
        self.installation_id = installation_id
        self.epoch = epoch
        self.send_lock = asyncio.Lock()
        self.queued_sends = 0


class ViewerConnection:
    def __init__(self, websocket, viewer_id, installation_id, epoch):
        self.websocket = websocket
        self.viewer_id = viewer_id
        self.installation_id = installation_id
        self.epoch = epoch
        self.token = ""
        self.device_id = None
        self.authenticated = False
        self.media_requested = False
        self.pending_requests = set()
        self.send_lock = asyncio.Lock()
        self.queued_sends = 0


@dataclass
class PendingRequest:
    routing_id: str
    request_id: str
    viewer: ViewerConnection
    installation_id: str
    host_epoch: int
    future: asyncio.Future
    op: str = ""
    payload: dict = field(default_factory=dict, repr=False)
    received_at: float = 0.0
    media_authorized: bool = False
    media_owned: bool = False


class Registry:
    def __init__(self):
        self.hosts = {}
        self.current_epoch = {}
        self.viewers = {}
        self.pending_requests = {}
        self.invitations = {}
        self.settled_requests = OrderedDict()

    def register_host(self, installation_id, websocket):
        self.invalidate_installation(installation_id)
        epoch = self.current_epoch[installation_id]
        conn = HostConnection(websocket, installation_id, epoch)
        self.hosts[installation_id] = conn
        return conn

    def remove_host(self, host):
        if self.hosts.get(host.installation_id) is host:
            self.invalidate_installation(host.installation_id)

    def register_viewer(self, viewer_id, installation_id, websocket, epoch):
        conn = ViewerConnection(websocket, viewer_id, installation_id, epoch)
        self.viewers[viewer_id] = conn
        return conn

    def remove_viewer(self, viewer_id):
        viewer = self.viewers.pop(viewer_id, None)
        if viewer:
            for pending in list(self.pending_requests.values()):
                if pending.viewer is viewer:
                    self.drop_pending(pending)
                    pending.future.cancel()
            viewer.token = ""

    def add_pending_request(self, viewer, request_id, *, op="", payload=None, received_at=None):
        if request_id in viewer.pending_requests or len(viewer.pending_requests) >= MAX_PENDING_REQUESTS:
            return None
        # Bound aggregate work retained by a host, even with many viewers.
        if sum(len(v.pending_requests) for v in self.viewers.values() if v.installation_id == viewer.installation_id) >= 256:
            return None
        routing_id = str(uuid.uuid4())
        pending = PendingRequest(routing_id, request_id, viewer, viewer.installation_id, viewer.epoch, asyncio.get_running_loop().create_future(),
                                 op, dict(payload or {}), time.time() if received_at is None else received_at)
        viewer.pending_requests.add(request_id)
        self.pending_requests[routing_id] = pending
        return pending

    def drop_pending(self, pending, *, retain_reservation=False):
        if not retain_reservation:
            pending.viewer.pending_requests.discard(pending.request_id)
        if self.pending_requests.get(pending.routing_id) is pending:
            del self.pending_requests[pending.routing_id]
            self._prune_settled()
            self.settled_requests[pending.routing_id] = (
                pending.installation_id, pending.host_epoch,
                time.monotonic() + SETTLED_REQUEST_TTL_SECONDS,
            )
            while len(self.settled_requests) > MAX_SETTLED_REQUESTS:
                self.settled_requests.popitem(last=False)

    def _prune_settled(self):
        now = time.monotonic()
        while self.settled_requests:
            first = next(iter(self.settled_requests))
            if self.settled_requests[first][2] > now:
                break
            self.settled_requests.popitem(last=False)

    def is_settled_request(self, routing_id, installation_id, epoch):
        self._prune_settled()
        settled = self.settled_requests.get(routing_id)
        return (self.is_valid_host(installation_id, epoch) and settled is not None
                and settled[:2] == (installation_id, epoch))

    def resolve_pending_request(self, routing_id, installation_id, epoch):
        pending = self.pending_requests.get(routing_id)
        # Ownership MUST be checked before removing another installation's work.
        if pending is None or (pending.installation_id, pending.host_epoch) != (installation_id, epoch) or not self.is_valid_host(installation_id, epoch):
            return None
        self.drop_pending(pending, retain_reservation=True)
        return pending

    def get_host(self, installation_id):
        return self.hosts.get(installation_id)

    def is_valid_host(self, installation_id, epoch):
        host = self.hosts.get(installation_id)
        return host is not None and host.epoch == epoch

    def open_invitation(self, host, handle, expires_at):
        if not self.is_valid_host(host.installation_id, host.epoch) or not time.time() < expires_at <= time.time() + 300:
            return False
        self.invitations = {key: value for key, value in self.invitations.items() if value[2] > time.time()}
        existing = self.invitations.get(handle)
        if existing and existing[:2] != (host.installation_id, host.epoch):
            return False
        # A PC has one current pairing window.
        self.close_installation_invitations(host.installation_id)
        self.invitations[handle] = (host.installation_id, host.epoch, expires_at)
        return True

    def close_invitation(self, host, handle):
        current = self.invitations.get(handle)
        if current and current[:2] == (host.installation_id, host.epoch):
            del self.invitations[handle]
        # In-flight pair futures remain valid: PC single-use consumption closes
        # admission before returning the authorized token.

    def installation_for_handle(self, handle):
        invitation = self.invitations.get(handle)
        if not invitation:
            return None
        installation_id, epoch, expiry = invitation
        if expiry <= time.time() or not self.is_valid_host(installation_id, epoch):
            self.invitations.pop(handle, None)
            return None
        return installation_id

    def close_installation_invitations(self, installation_id):
        self.invitations = {key: value for key, value in self.invitations.items() if value[0] != installation_id}

    def invalidate_installation(self, installation_id):
        self.current_epoch[installation_id] = self.current_epoch.get(installation_id, 0) + 1
        self.hosts.pop(installation_id, None)
        self.close_installation_invitations(installation_id)
        for viewer in self.viewers.values():
            if viewer.installation_id == installation_id:
                viewer.authenticated = False
                viewer.token = ""
        for pending in list(self.pending_requests.values()):
            if pending.installation_id == installation_id:
                self.drop_pending(pending)
                if not pending.future.done():
                    pending.future.set_result(Reply(v=1, id=pending.routing_id, ok=False, error=ErrorPayload(code=ErrorCode.OFFLINE.value, message="Host disconnected; authenticate again")))

    def invalidate_device(self, host, device_id):
        affected = []
        for viewer in self.viewers.values():
            if (viewer.installation_id, viewer.epoch, viewer.device_id) == (host.installation_id, host.epoch, device_id):
                viewer.authenticated = False
                viewer.token = ""
                affected.append(viewer)
        for pending in list(self.pending_requests.values()):
            if pending.viewer in affected:
                self.drop_pending(pending)
                if not pending.future.done():
                    pending.future.set_result(Reply(v=1, id=pending.routing_id, ok=False, error=ErrorPayload(code="not_paired", message="Device was removed")))
        return affected
