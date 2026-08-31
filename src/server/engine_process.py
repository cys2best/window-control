"""Validated subprocess wrapper for engine.exe: spawn, parse its one-line
JSON ready record from stdout, and enforce a bounded startup deadline.
"""

import json
import os
import queue
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable


def _log(msg: str):
    for _p in [r"C:\ProgramData\WindowControl", r"C:\Windows\Temp"]:
        try:
            os.makedirs(_p, exist_ok=True)
            with open(os.path.join(_p, "service_crash.log"), "a") as f:
                f.write(msg + "\n")
            return
        except Exception:
            continue


@dataclass(frozen=True)
class EngineReadyRecord:
    instance_name: str
    pid: int
    whep_port: int
    admin_port: int
    generation: int
    width: int
    height: int


class EngineReadyError(RuntimeError):
    pass


_REQUIRED_INT_FIELDS = ("pid", "whep_port", "admin_port", "generation", "width", "height")


def _validate_ready_record(raw: dict, instance_name: str) -> EngineReadyRecord:
    for field in _REQUIRED_INT_FIELDS:
        value = raw.get(field)
        if not isinstance(value, int) or isinstance(value, bool):
            raise EngineReadyError(f"ready record field {field!r} is not an integer: {value!r}")

    if raw.get("instance_name") != instance_name:
        raise EngineReadyError(
            f"ready record instance_name {raw.get('instance_name')!r} "
            f"does not match requested {instance_name!r}"
        )
    if not (1 <= raw["whep_port"] <= 65535):
        raise EngineReadyError(f"ready record whep_port out of range: {raw['whep_port']}")
    if not (1 <= raw["admin_port"] <= 65535):
        raise EngineReadyError(f"ready record admin_port out of range: {raw['admin_port']}")
    if raw["pid"] <= 0:
        raise EngineReadyError(f"ready record pid is not positive: {raw['pid']}")
    if raw["width"] <= 0 or raw["height"] <= 0:
        raise EngineReadyError(
            f"ready record dimensions are not positive: {raw['width']}x{raw['height']}"
        )
    if raw["generation"] < 0:
        raise EngineReadyError(f"ready record generation is negative: {raw['generation']}")

    return EngineReadyRecord(
        instance_name=raw["instance_name"],
        pid=raw["pid"],
        whep_port=raw["whep_port"],
        admin_port=raw["admin_port"],
        generation=raw["generation"],
        width=raw["width"],
        height=raw["height"],
    )


class EngineInstance:
    def __init__(self, instance_name: str, exe_path: str, scrcpy_port: int,
                 env_overrides: dict[str, str],
                 popen: Callable = subprocess.Popen,
                 ready_timeout_seconds: float = 10.0,
                 log: Callable[[str], None] = _log,
                 clock: Callable[[], float] = time.monotonic):
        self.instance_name = instance_name
        self._exe_path = exe_path
        self.scrcpy_port = scrcpy_port
        self._env_overrides = env_overrides
        self._popen = popen
        self._ready_timeout_seconds = ready_timeout_seconds
        self._log = log
        self._clock = clock
        self._process: subprocess.Popen | None = None

    def start(self) -> EngineReadyRecord:
        child_env = os.environ.copy()
        child_env.update(self._env_overrides)
        self._process = self._popen(
            [self._exe_path, self.instance_name, str(self.scrcpy_port)],
            env=child_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        stdout_queue: queue.Queue[str | None] = queue.Queue()
        threading.Thread(
            target=self._pump_stdout, args=(stdout_queue,), daemon=True
        ).start()
        threading.Thread(
            target=self._drain_stderr, daemon=True
        ).start()

        deadline = self._clock() + self._ready_timeout_seconds
        try:
            record = self._await_ready_record(stdout_queue, deadline)
        except EngineReadyError:
            self.stop()
            raise
        return record

    def _pump_stdout(self, stdout_queue: "queue.Queue[str | None]"):
        try:
            for line in self._process.stdout:
                stdout_queue.put(line)
        except Exception:
            pass
        finally:
            stdout_queue.put(None)

    def _drain_stderr(self):
        try:
            for line in self._process.stderr:
                self._log(line.rstrip("\n"))
        except Exception:
            pass

    def _await_ready_record(self, stdout_queue: "queue.Queue[str | None]",
                             deadline: float) -> EngineReadyRecord:
        while True:
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise EngineReadyError(
                    f"engine {self.instance_name!r} did not become ready before deadline "
                    f"({self._ready_timeout_seconds}s)"
                )

            exit_code = self._process.poll()
            try:
                line = stdout_queue.get(timeout=min(remaining, 0.1))
            except queue.Empty:
                if exit_code is not None:
                    raise EngineReadyError(
                        f"engine {self.instance_name!r} exited with exit code {exit_code} "
                        "before reporting ready"
                    )
                continue

            if line is None:
                remaining = max(0.0, deadline - self._clock())
                try:
                    exit_code = self._process.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    raise EngineReadyError(
                        f"engine {self.instance_name!r} did not become ready before deadline "
                        f"({self._ready_timeout_seconds}s)"
                    )
                raise EngineReadyError(
                    f"engine {self.instance_name!r} exited with exit code {exit_code} "
                    "before reporting ready"
                )

            line = line.strip()
            if not line:
                continue

            try:
                raw = json.loads(line)
            except ValueError:
                continue
            if not isinstance(raw, dict):
                continue

            return _validate_ready_record(raw, self.instance_name)

    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def stop(self, timeout_seconds: float = 5.0) -> None:
        if self._process is None:
            return
        if self._process.poll() is not None:
            return

        self._process.terminate()
        try:
            self._process.wait(timeout=timeout_seconds)
            return
        except subprocess.TimeoutExpired:
            pass

        self._process.kill()
        try:
            self._process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            pass
