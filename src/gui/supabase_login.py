"""Supabase email/password sign-in for the desktop tray.

Caches the returned session locally so the tray doesn't prompt on every
launch (see LoginDialog). Once authenticated, all discovered instances are
automatically accessible — no per-instance linking required.
"""

import json
import os
import sys

import httpx

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QLineEdit, QPushButton,
)
from PyQt5.QtCore import Qt


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
    return os.path.join(base, "EmuCtrl", "session.json")


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
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(session, f)
    except OSError as e:
        # Non-fatal: caching is a "don't prompt again next launch"
        # convenience. Sign-in itself already succeeded — don't let a
        # disk/permission error block or scare an already-authenticated
        # user. Note to stderr for debuggability only.
        print(f"[supabase_login] failed to cache session: {e}", file=sys.stderr)


class LoginDialog(QDialog):
    def __init__(self, supabase_url: str, anon_key: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Sign in")
        self.setFixedWidth(320)
        self._supabase_url = supabase_url
        self._anon_key = anon_key
        self.session: dict | None = None

        self.setStyleSheet("""
            QDialog { background: #12141a; }
            QLabel { color: #ef4444; font-size: 13px; background: transparent; }
            QLineEdit {
                background: #1b1e26;
                border: 1px solid rgba(255,255,255,0.09);
                border-radius: 4px;
                color: #e8eaed;
                font-size: 14px;
                padding: 10px;
            }
            QLineEdit:focus { border: 1px solid #6fd7d1; }
            QPushButton {
                background: #6fd7d1;
                color: #12141a;
                border: none;
                border-radius: 4px;
                font-size: 14px;
                font-weight: 600;
                padding: 10px;
            }
            QPushButton:hover { background: #8ae0db; }
            QPushButton:pressed { background: #5bc4be; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel("EmuCtrl")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("color: #e8eaed; font-size: 20px; font-weight: 600; background: transparent;")
        layout.addWidget(title)

        subtitle = QLabel("Sign in to continue")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet("color: #8a8f98; font-size: 13px; background: transparent;")
        layout.addWidget(subtitle)

        self._email = QLineEdit(placeholderText="Email")
        self._password = QLineEdit(placeholderText="Password")
        self._password.setEchoMode(QLineEdit.Password)
        self._error = QLabel("")
        self._error.setAlignment(Qt.AlignCenter)
        self._error.setWordWrap(True)
        submit = QPushButton("Sign in")
        submit.clicked.connect(self._submit)

        for widget in (self._email, self._password, submit, self._error):
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
