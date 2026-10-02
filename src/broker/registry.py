"""Ephemeral routing state scoped to installation, connection epoch and viewer."""

import asyncio
from dataclasses import dataclass
import time
import uuid

from remote_protocol import MAX_PENDING_REQUESTS, Reply, ErrorPayload, ErrorCode


class HostConnection:
    def __init__(self, websocket, installation_id, epoch):
        self.websocket = websocket
        self.installation_id = installation_id
        self.epoch = epoch
        self.send_lock = asyncio.Lock()


class ViewerConnection:
    def __init__(self, websocket, viewer_id, installation_id, epoch):
        self.websocket = websocket
        self.viewer_id = viewer_id
        self.installation_id = installation_id
        self.epoch = epoch
        self.token = ""
        self.device_id = None
        self.authenticated = False
        self.pending_requests = set()
        self.send_lock = asyncio.Lock()


@dataclass
class PendingRequest:
    routing_id: str
    request_id: str
    viewer: ViewerConnection
    installation_id: str
    host_epoch: int
    future: asyncio.Future


class Registry:
    def __init__(self):
        self.hosts = {}
        self.current_epoch = {}
        self.viewers = {}
        self.pending_requests = {}
        self.invitations = {}

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

    def add_pending_request(self, viewer, request_id):
        if request_id in viewer.pending_requests or len(viewer.pending_requests) >= MAX_PENDING_REQUESTS:
            return None
        # Bound aggregate work retained by a host, even with many viewers.
        if sum(p.installation_id == viewer.installation_id for p in self.pending_requests.values()) >= 256:
            return None
        routing_id = str(uuid.uuid4())
        pending = PendingRequest(routing_id, request_id, viewer, viewer.installation_id, viewer.epoch, asyncio.get_running_loop().create_future())
        viewer.pending_requests.add(request_id)
        self.pending_requests[routing_id] = pending
        return pending

    def drop_pending(self, pending):
        if self.pending_requests.get(pending.routing_id) is pending:
            del self.pending_requests[pending.routing_id]
            pending.viewer.pending_requests.discard(pending.request_id)

    def resolve_pending_request(self, routing_id, installation_id, epoch):
        pending = self.pending_requests.get(routing_id)
        # Ownership MUST be checked before removing another installation's work.
        if pending is None or (pending.installation_id, pending.host_epoch) != (installation_id, epoch) or not self.is_valid_host(installation_id, epoch):
            return None
        self.drop_pending(pending)
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
