import json
import secrets
import hashlib
import uuid
import base64
from pathlib import Path
from pydantic import BaseModel
import tempfile
import os
import re
import threading
from contextlib import suppress


class InstallationStoreError(RuntimeError):
    pass

class InstallationIdentity(BaseModel):
    installation_id: str
    credential: str

class InstallationStore:
    def __init__(self, storage_path: str | Path):
        self.storage_path = Path(storage_path)
        # One store owner in one broker process; all transactions share this lock.
        self._lock = threading.RLock()
    
    def _read_data(self) -> dict:
        try:
            raw = self.storage_path.read_text()
        except FileNotFoundError:
            return {}
        except OSError:
            raise InstallationStoreError("installation storage unavailable") from None
        except UnicodeError:
            raise InstallationStoreError("installation storage invalid") from None
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            raise InstallationStoreError("installation storage invalid") from None
        if not isinstance(data, dict):
            raise InstallationStoreError("installation storage invalid")
        for installation_id, record in data.items():
            if (re.fullmatch(r"[0-9a-f]{32}", installation_id) is None
                    or not isinstance(record, dict) or set(record) != {"digest"}
                    or not isinstance(record["digest"], str)
                    or re.fullmatch(r"[0-9a-f]{64}", record["digest"]) is None):
                raise InstallationStoreError("installation storage invalid")
        return data

    def _write_data(self, data: dict):
        tmp_path = None
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_fd, tmp_path = tempfile.mkstemp(dir=self.storage_path.parent)
            with os.fdopen(tmp_fd, "w") as f:
                json.dump(data, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.storage_path)
        except OSError:
            raise InstallationStoreError("installation storage unavailable") from None
        finally:
            if tmp_path is not None:
                with suppress(OSError):
                    os.unlink(tmp_path)

    def _hash_credential(self, credential: str) -> str:
        return hashlib.sha256(credential.encode()).hexdigest()

    def register(self) -> InstallationIdentity:
        with self._lock:
            inst_id = uuid.uuid4().hex
            cred_bytes = secrets.token_bytes(32)
            credential = base64.urlsafe_b64encode(cred_bytes).decode().rstrip('=')

            digest = self._hash_credential(credential)
            data = self._read_data()
            data[inst_id] = {"digest": digest}
            self._write_data(data)

            return InstallationIdentity(installation_id=inst_id, credential=credential)

    def authenticate(self, id: str, credential: str) -> bool:
        with self._lock:
            data = self._read_data()
            if id not in data:
                return False

            stored_digest = data[id]["digest"]
            provided_digest = self._hash_credential(credential)
            return secrets.compare_digest(stored_digest, provided_digest)
        
    def exists(self, id: str) -> bool:
        with self._lock:
            return id in self._read_data()

    def revoke(self, id: str, credential: str) -> bool:
        with self._lock:
            if not self.authenticate(id, credential):
                return False

            data = self._read_data()
            if id in data:
                del data[id]
                self._write_data(data)
            return True
