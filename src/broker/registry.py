import asyncio
from typing import Dict, Any, Optional
from pydantic import BaseModel

class HostConnection:
    def __init__(self, websocket: Any, installation_id: str, epoch: int):
        self.websocket = websocket
        self.installation_id = installation_id
        self.epoch = epoch

class ViewerConnection:
    def __init__(self, websocket: Any, viewer_id: str, installation_id: str):
        self.websocket = websocket
        self.viewer_id = viewer_id
        self.installation_id = installation_id
        # pending requests: map request_id -> ViewerConnection
        self.pending_requests: set[str] = set()
        self.ip: str = getattr(getattr(websocket, "client", None), "host", "127.0.0.1")

class Registry:
    def __init__(self):
        self.hosts: Dict[str, HostConnection] = {}
        self.current_epoch: Dict[str, int] = {}
        # viewer_id -> ViewerConnection
        self.viewers: Dict[str, ViewerConnection] = {}
        # request_id -> ViewerConnection
        self.pending_requests: Dict[str, ViewerConnection] = {}
    
    def register_host(self, installation_id: str, websocket: Any) -> HostConnection:
        epoch = self.current_epoch.get(installation_id, 0) + 1
        self.current_epoch[installation_id] = epoch
        
        conn = HostConnection(websocket, installation_id, epoch)
        self.hosts[installation_id] = conn
        return conn

    def register_viewer(self, viewer_id: str, installation_id: str, websocket: Any) -> ViewerConnection:
        conn = ViewerConnection(websocket, viewer_id, installation_id)
        self.viewers[viewer_id] = conn
        return conn

    def remove_viewer(self, viewer_id: str):
        if viewer_id in self.viewers:
            conn = self.viewers[viewer_id]
            for req_id in list(conn.pending_requests):
                self.pending_requests.pop(req_id, None)
            del self.viewers[viewer_id]

    def add_pending_request(self, viewer: ViewerConnection, request_id: str) -> bool:
        if len(viewer.pending_requests) >= 32:
            return False
        if request_id in self.pending_requests:
            # duplicate request ID
            return False
            
        viewer.pending_requests.add(request_id)
        self.pending_requests[request_id] = viewer
        return True
        
    def resolve_pending_request(self, request_id: str) -> Optional[ViewerConnection]:
        viewer = self.pending_requests.pop(request_id, None)
        if viewer and request_id in viewer.pending_requests:
            viewer.pending_requests.remove(request_id)
        return viewer

    def get_host(self, installation_id: str) -> Optional[HostConnection]:
        return self.hosts.get(installation_id)

    def is_valid_host(self, installation_id: str, epoch: int) -> bool:
        current = self.current_epoch.get(installation_id)
        return current == epoch
        
    def invalidate_installation(self, installation_id: str):
        self.current_epoch[installation_id] = self.current_epoch.get(installation_id, 0) + 1
        if installation_id in self.hosts:
            del self.hosts[installation_id]
