"""Windows-only staged host/DPAPI smoke, without capture or device acceptance.

Run against a copied PyInstaller onedir package. The registration fixture is a
local trusted HTTPS endpoint; it is deliberately not a real remote media service.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import ssl
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import urlopen


def wait_for(check, process, description):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Packaged host exited before {description}: {process.returncode}")
        try:
            if check():
                return
        except (OSError, ValueError):
            pass
        time.sleep(0.25)
    raise RuntimeError(f"Timed out waiting for {description}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--certificate", type=Path, required=True)
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Requires a real Windows packaged host")

    # Only Windows system tools remain discoverable; no vcpkg, repo, Python or
    # developer DLL directories can satisfy the child process loader.
    clean_env = dict(os.environ, PATH=str(Path(os.environ["SystemRoot"]) / "System32"),
                     LOCALAPPDATA=str(args.package / "user-data"),
                     SSL_CERT_FILE=str(args.certificate), QT_QPA_PLATFORM="offscreen")
    identity_path = Path(clean_env["LOCALAPPDATA"]) / "EmuCtrl" / "remote_identity.json"
    credential = secrets.token_urlsafe(32)
    registration_count = 0

    class RegistrationFixture(BaseHTTPRequestHandler):
        def log_message(self, *unused):
            pass

        def do_POST(self):
            nonlocal registration_count
            if self.path != "/installations":
                self.send_error(404)
                return
            registration_count += 1
            body = json.dumps({"installation_id": "packaged-smoke", "credential": credential}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    fixture = ThreadingHTTPServer(("127.0.0.1", 0), RegistrationFixture)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(args.certificate, args.key)
    fixture.socket = tls.wrap_socket(fixture.socket, server_side=True)
    threading.Thread(target=fixture.serve_forever, daemon=True).start()
    origin = f"https://localhost:{fixture.server_port}"
    results = {"status": "fail", "staged_host_startup": False,
               "local_api_without_remote_service": False, "bundled_web_export": False,
               "packaged_dpapi_save": False, "packaged_dpapi_reload_after_restart": False,
               "capture_or_media_exercised": False, "registration_service": "local HTTPS fixture"}

    def api_ready():
        with urlopen("http://127.0.0.1:8080/instances", timeout=2) as response:
            json.loads(response.read())
            return response.status == 200

    def run_host(remote_url, check):
        env = dict(clean_env, REMOTE_SERVICE_URL=remote_url)
        process = subprocess.Popen([str(args.package / "EmuCtrl.exe")], cwd=args.package, env=env)
        try:
            wait_for(api_ready, process, "local API")
            check(process)
        finally:
            # Kill children as well (adb may otherwise keep the clean directory
            # busy). No host other than the process created by this smoke is hit.
            subprocess.run([str(Path(os.environ["SystemRoot"]) / "System32" / "taskkill.exe"),
                            "/F", "/T", "/PID", str(process.pid)], capture_output=True)
            process.wait(timeout=10)

    try:
        def local_check(process):
            with urlopen("http://127.0.0.1:8080/", timeout=2) as response:
                if response.status != 200 or b"<html" not in response.read().lower():
                    raise RuntimeError("Bundled web export was not served")
            results.update(staged_host_startup=True, local_api_without_remote_service=True,
                           bundled_web_export=True)
        run_host("", local_check)

        def save_check(process):
            wait_for(identity_path.is_file, process, "DPAPI identity save")
            if credential in identity_path.read_text():
                raise RuntimeError("Identity credential was saved in plaintext")
            # Decrypt the actual frozen-host file using real current-user DPAPI.
            # Only registration is a fixture; neither store nor protector is fake.
            from server.remote_identity import RemoteIdentityStore
            def forbid_registration(*unused):
                raise RuntimeError("Saved identity unexpectedly required registration")
            restored = RemoteIdentityStore(identity_path, request=forbid_registration).load_or_register(origin)
            if restored.credential != credential or registration_count != 1:
                raise RuntimeError("Saved DPAPI identity did not match registration")
            results["packaged_dpapi_save"] = True
        run_host(origin, save_check)

        def reload_check(process):
            # A frozen restart must load the identity and attempt WSS while the
            # fixture deliberately returns 501; observe that request explicitly.
            wait_for(lambda: fixture.connection_attempts > 0, process, "saved identity reuse")
            if registration_count != 1:
                raise RuntimeError("Packaged restart registered a second identity")
            results["packaged_dpapi_reload_after_restart"] = True

        # Observe the second launch separately: API listening alone would not
        # prove that asynchronous identity loading had completed.
        fixture.connection_attempts = 0
        def disconnected_get(handler):
            fixture.connection_attempts += 1
            handler.send_error(501)
        RegistrationFixture.do_GET = disconnected_get
        run_host(origin, reload_check)
        results["status"] = "pass"
    finally:
        fixture.shutdown()
        args.output.write_text(json.dumps(results, indent=2) + "\n")
        print(json.dumps(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
