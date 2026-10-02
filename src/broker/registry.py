import asyncio
from typing import Dict, Any, Optional
from pydantic import BaseModel

class HostConnection:
    def __init__(self, websocket: Any, installation_id: str, epoch: int):
        self.websocket = websocket
        self.installation_id = installation_id
        self.epoch = epoch

class Registry:
    def __init__(self):
        self.hosts: Dict[str, HostConnection] = {}
        self.current_epoch: Dict[str, int] = {}
    
    def register_host(self, installation_id: str, websocket: Any) -> HostConnection:
        epoch = self.current_epoch.get(installation_id, 0) + 1
        self.current_epoch[installation_id] = epoch
        
        conn = HostConnection(websocket, installation_id, epoch)
        self.hosts[installation_id] = conn
        return conn

    def get_host(self, installation_id: str) -> Optional[HostConnection]:
        return self.hosts.get(installation_id)

    def is_valid_host(self, installation_id: str, epoch: int) -> bool:
        current = self.current_epoch.get(installation_id)
        return current == epoch
        
    def remove_host(self, installation_id: str, epoch: int):
        if self.is_valid_host(installation_id, epoch):
            if installation_id in self.hosts:
                del self.hosts[installation_id]
                
    def invalidate_installation(self, installation_id: str):
        # bump epoch so old connections die
        self.current_epoch[installation_id] = self.current_epoch.get(installation_id, 0) + 1
        if installation_id in self.hosts:
            del self.hosts[installation_id]

