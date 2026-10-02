import json
import secrets
import hashlib
import uuid
import base64
from pathlib import Path
from pydantic import BaseModel
import tempfile
import os

class InstallationIdentity(BaseModel):
    installation_id: str
    credential: str

class InstallationStore:
    def __init__(self, storage_path: str | Path):
        self.storage_path = Path(storage_path)
    
    def _read_data(self) -> dict:
        if not self.storage_path.exists():
            return {}
        try:
            return json.loads(self.storage_path.read_text())
        except Exception:
            return {}

    def _write_data(self, data: dict):
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(dir=self.storage_path.parent)
        with os.fdopen(tmp_fd, "w") as f:
            json.dump(data, f)
        os.replace(tmp_path, self.storage_path)

    def _hash_credential(self, credential: str) -> str:
        return hashlib.sha256(credential.encode()).hexdigest()

    def register(self) -> InstallationIdentity:
        inst_id = uuid.uuid4().hex
        cred_bytes = secrets.token_bytes(32)
        credential = base64.urlsafe_b64encode(cred_bytes).decode().rstrip('=')
        
        digest = self._hash_credential(credential)
        data = self._read_data()
        data[inst_id] = {"digest": digest}
        self._write_data(data)
        
        return InstallationIdentity(installation_id=inst_id, credential=credential)

    def authenticate(self, id: str, credential: str) -> bool:
        data = self._read_data()
        if id not in data:
            return False
        
        stored_digest = data[id].get("digest")
        if not stored_digest:
            return False
            
        provided_digest = self._hash_credential(credential)
        return secrets.compare_digest(stored_digest, provided_digest)
        
    def exists(self, id: str) -> bool:
        return id in self._read_data()

    def revoke(self, id: str, credential: str) -> bool:
        if not self.authenticate(id, credential):
            return False
            
        data = self._read_data()
        if id in data:
            del data[id]
            self._write_data(data)
        return True
