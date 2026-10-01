from pathlib import Path

import pytest

ENGINE = Path(__file__).parent.parent / "engine"

REMOVED = (
    "src/signaling_client.h",
    "src/signaling_client.cpp",
    "src/public_signaling.h",
    "src/public_signaling.cpp",
    "test/test_signaling_client.cpp",
    "test/test_public_signaling.cpp",
    "test/signaling_test_utils.h",
)

# Names that only the deleted files defined or used.
DEAD_NAMES = (
    "signaling_client.h",
    "public_signaling.h",
    "signaling_test_utils.h",
    "SignalingClient",
    "PublicSignalingBridge",
    "ENGINE_SIGNALING",
    "ENGINE_SESSION",
    "ENGINE_PUBLIC_ICE_SERVERS",
    "websocketpp",
)


def _source_files():
    for folder in ("src", "test"):
        for path in sorted((ENGINE / folder).rglob("*")):
            if path.suffix in {".cpp", ".h"}:
                yield path


@pytest.mark.parametrize("relative", REMOVED)
def test_removed_engine_file_is_gone(relative):
    assert not (ENGINE / relative).exists(), relative


def test_nothing_in_engine_sources_references_the_removed_code():
    for path in _source_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for name in DEAD_NAMES:
            assert name not in text, f"{path.relative_to(ENGINE)} still mentions {name}"


def test_cmake_and_vcpkg_carry_no_signaling_dependencies():
    cmake = (ENGINE / "CMakeLists.txt").read_text(encoding="utf-8")
    vcpkg = (ENGINE / "vcpkg.json").read_text(encoding="utf-8")
    for token in ("signaling", "websocketpp", "find_package(asio", "asio::asio",
                  "ASIO_STANDALONE", "crypt32"):
        assert token not in cmake, token
    for token in ("websocketpp", '"asio"'):
        assert token not in vcpkg, token


def test_main_keeps_the_local_whep_wiring():
    main = (ENGINE / "src" / "main.cpp").read_text(encoding="utf-8")
    assert "ENGINE_WHEP_CAPABILITY_SECRET" in main
    assert "ENGINE_LOCAL_ICE_SERVERS" in main
    assert "WhepHandler" in main


def test_cmake_keeps_the_dependencies_local_whep_needs():
    cmake = (ENGINE / "CMakeLists.txt").read_text(encoding="utf-8")
    for token in ("libdatachannel", "nlohmann_json", "httplib", "OpenSSL",
                  "ws2_32.lib", "engine_tests"):
        assert token in cmake, token


REPO = Path(__file__).parent.parent

REMOVED_VERIFIER_FILES = (
    "scripts/verify_engine_cutover.py",
    "scripts/verify_python_orchestration.py",
    "scripts/verify_frontend_cutover.py",
    "scripts/verify_lib.py",
    "scripts/measure_engine_cutover.py",
    "engine/verify-engine-cutover.ps1",
    "engine/verify-python-orchestration.ps1",
    "engine/verify-frontend-cutover.ps1",
    "engine/measure-engine-cutover.ps1",
    "engine/test/cutover_metrics_page.html",
    "engine/test/python_orchestration_verifier.html",
    "tests/test_engine_cutover_verifier.py",
    "tests/test_windows_verifier.py",
    "tests/test_frontend_cutover_verifier.py",
    "tests/test_measure_engine_cutover.py",
    "tests/test_verify_lib.py",
)


@pytest.mark.parametrize("relative", REMOVED_VERIFIER_FILES)
def test_removed_verifier_files_are_gone(relative):
    assert not (REPO / relative).exists(), relative


def test_kept_verification_entry_points_remain():
    assert (REPO / "scripts" / "verify_all.py").exists()
    assert (REPO / "engine" / "verify-all.ps1").exists()
    assert (REPO / "engine" / "test.ps1").exists()


def test_websockets_is_no_longer_a_dependency():
    for name in ("pyproject.toml", "requirements.txt"):
        assert "websockets" not in (REPO / name).read_text(encoding="utf-8").lower(), name


def test_signaling_relay_and_its_fixtures_are_gone():
    import subprocess

    tracked = subprocess.run(
        ["git", "ls-files", "infra", "engine/test/tls"],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout.split()
    assert tracked == [], tracked


def test_root_package_has_no_signaling_workspace_or_script():
    import json

    package = json.loads((REPO / "package.json").read_text(encoding="utf-8"))
    assert "infra/vps/signaling" not in package["workspaces"]
    assert "test:signaling" not in package["scripts"]

    # Verify the lock file has no stale relay entry
    lock = json.loads((REPO / "package-lock.json").read_text(encoding="utf-8"))
    assert "infra/vps/signaling" not in lock["packages"]
    assert "node_modules/jose" not in lock["packages"]


def test_verify_all_does_not_run_or_install_the_relay():
    text = (REPO / "scripts" / "verify_all.py").read_text(encoding="utf-8")
    for removed in ("test:signaling", "signaling", "Signaling", "jose", "infra"):
        assert removed not in text, removed
    assert "pair.html" in text


LIVE_DOCS = (
    "README.md",
    "CHECKLIST.md",
    "MEMORY.md",
    "docs/WINDOWS_MANUAL_VALIDATION.md",
    "docs/TROUBLESHOOTING.md",
    "engine/BUILD_WINDOWS.md",
    "engine/test/README_e2e.md",
)

DEAD_DOC_TERMS = (
    "infra/vps/signaling",
    "test:signaling",
    "VPS_SIGNALING_URL",
    "ENGINE_SIGNALING",
    "ENGINE_TEST_WSS_PORT",
    "verify-engine-cutover",
    "verify-python-orchestration",
    "verify-frontend-cutover",
    "measure-engine-cutover",
    "verify_engine_cutover",
    "verify_python_orchestration",
    "verify_frontend_cutover",
    "websocketpp",
    "coturn",
    "TURN_",
)


@pytest.mark.parametrize("relative", LIVE_DOCS)
def test_live_docs_do_not_describe_removed_infrastructure(relative):
    text = (REPO / relative).read_text(encoding="utf-8")
    for term in DEAD_DOC_TERMS:
        assert term not in text, f"{relative} still mentions {term}"


@pytest.mark.parametrize("relative", (
    "engine/test/README.md",
    "engine/test/README_engine_cutover.md",
    "engine/test/README_python_orchestration.md",
))
def test_removed_engine_docs_are_gone(relative):
    assert not (REPO / relative).exists(), relative


@pytest.mark.parametrize("relative", LIVE_DOCS)
def test_live_docs_do_not_link_to_removed_docs(relative):
    text = (REPO / relative).read_text(encoding="utf-8")
    for link in ("test/README.md", "README_engine_cutover", "README_python_orchestration"):
        assert link not in text, f"{relative} still links to {link}"


def test_memory_has_no_signaling_lesson_and_changelog_records_the_release():
    assert "Signaling Tests" not in (REPO / "MEMORY.md").read_text(encoding="utf-8")
    changelog = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## [v3.2.0]" in changelog
    assert changelog.index("## [v3.2.0]") < changelog.index("## [v3.1.2]")


CURRENT_DOCS = (
    "README.md",
    "CHECKLIST.md",
    "docs/WINDOWS_MANUAL_VALIDATION.md",
    "docs/TROUBLESHOOTING.md",
)

REMOVED_FEATURE_TERMS = (
    "Supabase",
    "supabase",
    "Account A",
    "Account B",
    "login.html",
    "/login",
    "Log in with",
    "TURN",
)

# CHECKLIST.md ends with a dated v3.1.0 sign-off record that legitimately names
# the old Supabase check; everything before it must be current.
HISTORICAL_RECORD_MARKER = "### 2026-09-07 01:00"


@pytest.mark.parametrize("relative", CURRENT_DOCS)
def test_current_docs_do_not_describe_removed_features(relative):
    text = (REPO / relative).read_text(encoding="utf-8")
    text = text.split(HISTORICAL_RECORD_MARKER)[0]
    for term in REMOVED_FEATURE_TERMS:
        assert term not in text, f"{relative} still mentions {term}"


def test_engine_test_scripts_use_modules_that_exist():
    import re

    script = (REPO / "engine" / "test.ps1").read_text(encoding="utf-8")
    readme = (REPO / "engine" / "test" / "README_e2e.md").read_text(encoding="utf-8")
    assert "scrcpy_session" not in script
    assert "scrcpy_session" not in readme
    imports = re.findall(r"from server\.(\w+) import ([\w, ]+)", script)
    assert imports, "test.ps1 no longer imports from server"
    for module, names in imports:
        source = REPO / "src" / "server" / f"{module}.py"
        assert source.exists(), f"server.{module} does not exist"
        text = source.read_text(encoding="utf-8")
        for name in (n.strip() for n in names.split(",")):
            assert f"def {name}" in text, f"server.{module} defines no {name}"
