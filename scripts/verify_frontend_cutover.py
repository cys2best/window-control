"""Dependency-injected verifier for the frontend/desktop cutover surface
that scripts/verify_engine_cutover.py doesn't cover: apps/web's static
export routing, the apps/desktop pywebview shell, and the packaged
installer's frontend-specific pieces.

See docs/superpowers/specs/2026-09-06-frontend-cutover-verifier-design.md
for the full design this implements.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


GATE_NAMES: tuple[str, ...] = (
    "dev_app_health",
    "web_routes",
    "rsc_payloads",
    "instances_negotiation",
    "auth_gate",
    "offline_suites",
    "installed_app_launch",
    "frozen_selfrelaunch",
    "desktop_shell_visual",
    "supabase_two_account_flow",
    "leaked_key_forgery_check",
)

GATE_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "web_routes": ("dev_app_health",),
    "rsc_payloads": ("dev_app_health",),
    "instances_negotiation": ("dev_app_health",),
    "auth_gate": ("dev_app_health",),
    "frozen_selfrelaunch": ("installed_app_launch",),
}


class FrontendCutoverError(RuntimeError):
    pass


@dataclass
class FrontendCutoverConfig:
    repo_root: Path
    evidence_dir: Path
    web_build_dir: Path
    installer_path: Path
    port: int = 8080
    file_prompts: bool = False
    file_prompt_poll_seconds: float = 0.25
    keep_on_failure: bool = False
    skip_manual_gates: bool = False
    skip_installer: bool = False
    only: tuple[str, ...] | None = None
    from_gate: str | None = None


@dataclass
class FrontendCutoverResult:
    status: str = "PASS"
    gates: dict[str, dict[str, Any]] = field(default_factory=dict)

    def mark(
        self,
        name: str,
        status: str,
        *,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.gates[name] = {"status": status, "reason": reason, "details": details or {}}
        if status == "FAIL":
            self.status = "FAIL"
        elif status == "SKIPPED" and self.status == "PASS":
            self.status = "INCOMPLETE"

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"status": self.status, "gates": self.gates}
        return payload


def resolve_gate_selection(config: FrontendCutoverConfig) -> dict[str, str]:
    """Pure function: compute {gate_name: reason} for every gate in GATE_NAMES.

    No I/O, no side effects — this is the scheduling/dependency-closure
    logic in isolation, testable without any real gate implementation.
    """
    if config.only is not None and config.from_gate is not None:
        raise ValueError("--only and --from are mutually exclusive")

    for name in config.only or ():
        if name not in GATE_NAMES:
            raise ValueError(f"unknown gate {name!r}")
    if config.from_gate is not None and config.from_gate not in GATE_NAMES:
        raise ValueError(f"unknown gate {config.from_gate!r}")

    if config.only is None and config.from_gate is None:
        return {name: "requested" for name in GATE_NAMES}

    requested: set[str]
    not_selected_reason: str
    if config.only is not None:
        requested = set(config.only)
        not_selected_reason = "not selected this run (--only)"
    else:
        index = GATE_NAMES.index(config.from_gate)  # type: ignore[arg-type]
        requested = set(GATE_NAMES[index:])
        not_selected_reason = "not selected this run (--from)"

    # Compute the dependency closure: anything a requested gate needs,
    # transitively, that wasn't itself requested. Deterministic order
    # based on GATE_NAMES so earlier pipeline gates take precedence.
    needed: dict[str, str] = {}
    queue = [name for name in GATE_NAMES if name in requested]
    while queue:
        name = queue.pop(0)
        for dependency in GATE_DEPENDENCIES.get(name, ()):
            if dependency not in requested and dependency not in needed:
                needed[dependency] = f"auto-included as prerequisite of {name}"
                queue.append(dependency)

    selection: dict[str, str] = {}
    for name in GATE_NAMES:
        if name in requested:
            selection[name] = "requested"
        elif name in needed:
            selection[name] = needed[name]
        else:
            selection[name] = not_selected_reason
    return selection
