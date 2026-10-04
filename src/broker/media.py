"""Event-loop-owned media admission; decisions never await network I/O."""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable
import uuid

from broker.limits import BrokerLimits
from broker.registry import HostConnection, Registry
from broker.turn_credentials import issue_turn_credentials
from remote_protocol import MediaAuthorization, MediaBundles, MediaCancellation, SessionIceBundle

if TYPE_CHECKING:
    from broker.app import BrokerSettings

MAX_SESSION_HISTORY = 4096


class MediaError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass
class Admission:
    installation_id: str
    host_epoch: int
    viewer_id: str
    device_id: str
    session_id: str
    generation: int
    routing_id: str
    setup_deadline: float
    expires_at: int
    bundles: MediaBundles = field(repr=False)
    established: bool = False
    canceled: bool = False
    grace_deadline: float | None = None
    negotiation_id: str | None = None


class MediaAdmission:
    def __init__(self, settings: 'BrokerSettings', limits: BrokerLimits,
                 registry: Registry, clock: Callable[[], float]):
        self.settings = settings
        self.limits = limits
        self.registry = registry
        self.clock = clock
        self.admissions: dict[str, Admission] = {}
        # One revision tombstone per installation, without ICE secrets or tokens.
        self.latest = {}
        # Fail closed at the per-epoch bound; never evict replay protection.
        self.session_history: dict[str, tuple[int, set[str]]] = {}
        self.cancellations = []

    def authorize(self, host: HostConnection, request: MediaAuthorization) -> MediaBundles:
        now = self.clock()
        self.sweep(now)
        pending = self.registry.pending_requests.get(request.payload['routing_id'])
        if (self.registry.get_host(host.installation_id) is not host or pending is None
                or pending.future.done() or pending.media_authorized
                or pending.op not in {'select', 'renew'}
                or (pending.installation_id, pending.host_epoch) != (host.installation_id, host.epoch)
                or now >= pending.received_at + 30):
            raise MediaError('invalid_request')
        viewer = pending.viewer
        if (self.registry.viewers.get(viewer.viewer_id) is not viewer or not viewer.authenticated
                or not viewer.device_id or viewer.epoch != host.epoch):
            raise MediaError('not_paired')
        previous = self.admissions.get(host.installation_id)
        latest = self.latest.get(host.installation_id)
        session_id, generation = request.payload['session_id'], request.payload['generation']
        history_epoch, history = self.session_history.get(host.installation_id, (host.epoch, set()))
        if history_epoch != host.epoch:
            history = set()
        if session_id in history or (latest and ((latest[4] == host.epoch and generation <= latest[1]) or session_id == latest[0])):
            raise MediaError('stale_generation')
        if previous and previous.host_epoch != host.epoch:
            raise MediaError('busy')
        if pending.op == 'renew':
            # The PC may release its old native peer before requesting admission.
            if not latest or (pending.payload['session_id'], pending.payload['generation'],
                              viewer.viewer_id, viewer.device_id, host.epoch) != latest:
                raise MediaError('stale_generation')
        if previous is None and len(self.admissions) >= self.settings.max_active_streams:
            raise MediaError('quota_exceeded')
        if len(history) >= MAX_SESSION_HISTORY:
            raise MediaError('quota_exceeded')
        if not self.limits.check_credential_issuance(host.installation_id):
            raise MediaError('quota_exceeded')
        bundles = MediaBundles(**{
            endpoint: self._bundle(host.installation_id, session_id, endpoint, now)
            for endpoint in ('host', 'viewer')
        })
        self.admissions[host.installation_id] = Admission(
            host.installation_id, host.epoch, viewer.viewer_id, viewer.device_id,
            session_id, generation, pending.routing_id, pending.received_at + 30,
            bundles.host.expires_at, bundles,
        )
        self.latest[host.installation_id] = (session_id, generation, viewer.viewer_id, viewer.device_id, host.epoch)
        history.add(session_id)
        self.session_history[host.installation_id] = (host.epoch, history)
        pending.media_authorized = True
        return bundles

    def _bundle(self, installation_id, session_id, endpoint, now):
        servers = []
        if self.settings.stun_urls:
            servers.append({'urls': list(self.settings.stun_urls)})
        relay = self.settings.relay_available and bool(self.settings.turn_urls)
        if relay:
            credentials = issue_turn_credentials(self.settings.turn_shared_secret, installation_id, session_id, endpoint, now)
            servers.append({'urls': list(self.settings.turn_urls), 'username': credentials.username,
                            'credential': credentials.credential})
        return SessionIceBundle(ice_servers=servers, expires_at=int(now) + 3600,
                                renew_after=3300, relay_available=relay)

    def release(self, host: HostConnection, session_id: str, generation: int) -> bool:
        if self.registry.get_host(host.installation_id) is not host:
            return False
        current = self.admissions.get(host.installation_id)
        if current:
            # A newly authenticated host can confirm cleanup of its exact predecessor.
            if (current.session_id, current.generation) != (session_id, generation):
                return False
            del self.admissions[host.installation_id]
            return True
        latest = self.latest.get(host.installation_id)
        return latest is not None and latest[:2] == (session_id, generation)

    def matching(self, pending):
        current = self.admissions.get(pending.installation_id)
        if current is None or current.canceled or (current.host_epoch, current.viewer_id, current.device_id) != (
            pending.host_epoch, pending.viewer.viewer_id, pending.viewer.device_id
        ):
            return None
        if pending.op in {'select', 'renew'}:
            return current if current.routing_id == pending.routing_id else None
        if (pending.payload.get('session_id'), pending.payload.get('generation')) == (current.session_id, current.generation):
            return current
        return None

    def track_request(self, pending):
        current = self.matching(pending)
        pending.media_owned = current is not None
        if current and pending.op == 'negotiate':
            if current.established or current.negotiation_id is not None:
                raise MediaError('busy')
            current.negotiation_id = pending.routing_id
            return current.setup_deadline
        return None

    def delivered(self, pending, reply):
        current = self.matching(pending)
        if current and reply.ok and pending.op == 'negotiate':
            current.established = True
        elif current and pending.op == 'close' and reply.ok:
            del self.admissions[pending.installation_id]

    def cancel(self, viewer, routing_id=None):
        # If the PC is unreachable, its working allocation keeps the grace slot.
        if not self.registry.is_valid_host(viewer.installation_id, viewer.epoch):
            return
        current = self.admissions.get(viewer.installation_id)
        owns_current = current is not None and (current.host_epoch, current.viewer_id) == (viewer.epoch, viewer.viewer_id)
        if routing_id is None and not owns_current and not viewer.media_requested:
            return
        if current and (current.host_epoch, current.viewer_id) == (viewer.epoch, viewer.viewer_id):
            if routing_id is None or routing_id in {current.routing_id, current.negotiation_id}:
                current.canceled = True
        if self.registry.is_valid_host(viewer.installation_id, viewer.epoch):
            self._queue_cancel(viewer.installation_id, viewer.epoch, viewer.viewer_id, routing_id)

    def cancel_request(self, pending):
        if pending.op in {'select', 'renew', 'negotiate', 'close'}:
            self.cancel(pending.viewer, pending.routing_id)

    def _queue_cancel(self, installation_id, epoch, viewer_id, routing_id=None):
        payload = dict(installation_id=installation_id, host_epoch=epoch, viewer_id=viewer_id)
        if routing_id is not None:
            payload['routing_id'] = routing_id
        self.cancellations.append(MediaCancellation(v=1, id=str(uuid.uuid4()), op='media_cancel', payload=payload))

    def take_cancellations(self):
        messages, self.cancellations = self.cancellations, []
        return messages

    def cancellation_delivered(self, message):
        payload = message.payload
        current = self.admissions.get(payload['installation_id'])
        if (current and current.canceled
                and (current.host_epoch, current.viewer_id) == (payload['host_epoch'], payload['viewer_id'])
                and (payload.get('routing_id') is None or payload['routing_id'] in {current.routing_id, current.negotiation_id})):
            del self.admissions[current.installation_id]

    def host_disconnected(self, host):
        current = self.admissions.get(host.installation_id)
        if current and current.host_epoch == host.epoch and current.grace_deadline is None:
            current.grace_deadline = self.clock() + 60

    def sweep(self, now: float) -> None:
        for installation_id, current in list(self.admissions.items()):
            deadline = min(current.expires_at, current.grace_deadline or float('inf'))
            if not current.established:
                deadline = min(deadline, current.setup_deadline)
            if now >= deadline:
                del self.admissions[installation_id]
                if self.registry.is_valid_host(installation_id, current.host_epoch):
                    self._queue_cancel(installation_id, current.host_epoch, current.viewer_id, current.routing_id)
