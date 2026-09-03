"""Supabase email/password sign-in for the desktop tray.

Caches the returned session locally so the tray doesn't prompt on every
launch (see LoginDialog). Does not yet drive any /instances/{id}/link
call — see the spec's deferred "tray auto-attribution" item.
"""

import json
import os

import httpx

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QLineEdit, QPushButton,
)


class AuthError(Exception):
    pass


def sign_in(supabase_url: str, anon_key: str, email: str, password: str) -> dict:
    try:
        r = httpx.post(
            f"{supabase_url}/auth/v1/token?grant_type=password",
            json={"email": email, "password": password},
            headers={"apikey": anon_key},
            timeout=10.0,
        )
    except Exception as e:
        raise AuthError(f"Network error: {e}") from e

    body = r.json()
    if r.status_code != 200 or "access_token" not in body:
        raise AuthError(body.get("error_description", body.get("msg", "Sign-in failed")))
    return body


def _session_path() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "WindowControl", "session.json")


def load_cached_session() -> dict | None:
    path = _session_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def save_session(session: dict) -> None:
    path = _session_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(session, f)


class LoginDialog(QDialog):
    def __init__(self, supabase_url: str, anon_key: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Sign in")
        self._supabase_url = supabase_url
        self._anon_key = anon_key
        self.session: dict | None = None

        layout = QVBoxLayout(self)
        self._email = QLineEdit(placeholderText="Email")
        self._password = QLineEdit(placeholderText="Password")
        self._password.setEchoMode(QLineEdit.Password)
        self._error = QLabel("")
        self._error.setStyleSheet("color: red;")
        submit = QPushButton("Sign in")
        submit.clicked.connect(self._submit)

        for widget in (self._email, self._password, self._error, submit):
            layout.addWidget(widget)

    def _submit(self):
        try:
            self.session = sign_in(
                self._supabase_url, self._anon_key,
                self._email.text(), self._password.text(),
            )
        except AuthError as e:
            self._error.setText(str(e))
            return
        save_session(self.session)
        self.accept()
