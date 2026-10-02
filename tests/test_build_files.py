import json
import re
from pathlib import Path

BUILD_DIR = Path(__file__).parent.parent / "build"
REPO_ROOT = Path(__file__).parent.parent


def test_spec_file_exists():
    assert (BUILD_DIR / "window_control.spec").exists()


def test_build_bat_exists():
    assert (BUILD_DIR / "build.bat").exists()


def test_installer_bat_exists():
    assert (BUILD_DIR / "build_installer.bat").exists()


def test_installer_iss_exists():
    assert (BUILD_DIR / "installer.iss").exists()


def test_spec_references_main():
    content = (BUILD_DIR / "window_control.spec").read_text()
    assert "main.py" in content
    assert "web" in content
    assert "assets" in content


def test_spec_stages_apps_web_export_not_legacy_client():
    content = (BUILD_DIR / "window_control.spec").read_text()
    assert "apps' / 'web' / 'out'" in content
    assert "src' / 'client'" not in content
    assert "webview" in content


def test_installer_iss_has_tailscale_check():
    content = (BUILD_DIR / "installer.iss").read_text()
    assert "Tailscale" in content
    assert "OutputBaseFilename=EmuCtrlInstaller" in content


def test_pyinstaller_contains_engine_without_legacy_media_imports():
    text = (BUILD_DIR / "window_control.spec").read_text()
    assert "assets" in text
    assert "engine" in text
    for legacy in ("aiortc", "imageio_ffmpeg", "av.codec", "aiohttp"):
        assert legacy not in text


def test_installer_owns_engine_program_firewall_rule():
    text = (BUILD_DIR / "installer.iss").read_text()
    assert "EmuCtrl-Engine" in text
    assert "assets\\engine\\engine.exe" in text
    assert "firewall delete rule" in text.lower()


def test_ci_builds_apps_web_before_packaging():
    text = (REPO_ROOT / ".github" / "workflows" / "build.yml").read_text()
    build_step_idx = text.index("npm run build -w apps/web")
    pyinstaller_idx = text.index("PyInstaller")
    assert build_step_idx < pyinstaller_idx


def test_ci_cmd_steps_call_npm_so_later_lines_still_run():
    # In a cmd script, invoking the npm.cmd shim without `call` ends the
    # script, silently skipping every line after it.
    text = (REPO_ROOT / ".github" / "workflows" / "build.yml").read_text()
    npm_lines = [line.strip() for line in text.splitlines()
                 if line.strip().startswith(("npm ", "call npm "))]
    assert npm_lines
    assert all(line.startswith("call npm ") for line in npm_lines), npm_lines


def test_build_bat_builds_apps_web_before_packaging():
    text = (BUILD_DIR / "build.bat").read_text()
    build_step_idx = text.index("npm run build -w apps/web")
    pyinstaller_idx = text.index("pyinstaller window_control.spec")
    assert build_step_idx < pyinstaller_idx


def test_ci_runs_unfiltered_engine_suite_without_relay():
    text = (REPO_ROOT / ".github" / "workflows" / "build.yml").read_text()
    assert "engine_tests.exe" in text
    assert "--gtest_filter" not in text
    assert "LASTEXITCODE" in text
    for removed in ("infra/vps/signaling", "SIGNALING_TLS", "ENGINE_TEST_WSS_PORT",
                    "SSL_CERT_FILE", "signaling relay"):
        assert removed not in text, removed


def test_ci_runs_engine_tests_between_build_and_staging():
    text = (REPO_ROOT / ".github" / "workflows" / "build.yml").read_text()
    build = text.index("cmake --build engine")
    tests = text.index("engine_tests.exe")
    stage = text.index("Stage engine.exe")
    assert build < tests < stage


def test_engine_overlay_selects_libnice():
    overlay_dir = REPO_ROOT / "engine" / "ports" / "libdatachannel"
    portfile = overlay_dir / "portfile.cmake"
    port_vcpkg = overlay_dir / "vcpkg.json"
    ci_workflow = (REPO_ROOT / ".github" / "workflows" / "build.yml").read_text()

    assert overlay_dir.is_dir(), "libdatachannel overlay port directory must exist"
    assert portfile.is_file(), "overlay portfile.cmake must exist"
    assert port_vcpkg.is_file(), "overlay vcpkg.json must exist"

    portfile_text = portfile.read_text()
    assert "-DUSE_NICE=ON" in portfile_text, "portfile must configure libdatachannel with USE_NICE=ON"
    assert "-DUSE_JUICE=OFF" in portfile_text, "portfile must disable USE_JUICE"

    port_vcpkg_text = port_vcpkg.read_text()
    assert "libnice" in port_vcpkg_text, "overlay vcpkg.json must depend on libnice"

    assert "VCPKG_OVERLAY_PORTS" in ci_workflow, "CI build must pass VCPKG_OVERLAY_PORTS"
    assert "engine/ports" in ci_workflow or "engine\\ports" in ci_workflow, "CI build must specify engine/ports overlay"
    assert "engine/ports" in ci_workflow and "hashFiles" in ci_workflow, "CI vcpkg cache key must invalidate on overlay changes"


def test_engine_overlay_uses_verified_release_archive():
    # Independently verified against the official v0.21.1 archive and the
    # vcpkg 0.21.1 port tree f5218e93bae8971d509fd04910f9778004e58bce.
    portfile = (REPO_ROOT / "engine/ports/libdatachannel/portfile.cmake").read_text()
    manifest = json.loads((REPO_ROOT / "engine/ports/libdatachannel/vcpkg.json").read_text())
    assert manifest.get("version-semver", manifest.get("version")) == "0.21.1"
    assert re.search(r"SHA512\s+([a-f0-9]{128})", portfile).group(1) == (
        "67119f6d1280593696f71dc550ceba1076066d0f55ebf10b527bf1c75e5e9571be18e5d9"
        "732e43a2aa4d5106a49a0676e70938d35d3eb4005cf17612f8836c52"
    )


def test_engine_overlay_resolves_system_dependencies_from_vcpkg():
    overlay = REPO_ROOT / "engine/ports/libdatachannel"
    portfile = (overlay / "portfile.cmake").read_text()
    manifest = json.loads((overlay / "vcpkg.json").read_text())
    dependencies = {d if isinstance(d, str) else d["name"] for d in manifest["dependencies"]}
    assert {"libnice", "openssl", "plog", "usrsctp"} <= dependencies
    assert "libjuice" not in dependencies
    assert "-DPREFER_SYSTEM_LIB=ON" in portfile
    assert "srtp NO_MEDIA" in portfile
    for patch in ("fix-for-vcpkg.patch", "fix_dependency.patch", "fix_srtp.patch"):
        assert patch in portfile
        assert (overlay / patch).is_file()
    assert "find_package(libSRTP CONFIG REQUIRED)" in (overlay / "fix_srtp.patch").read_text()
    assert "-DPKG_CONFIG_EXECUTABLE=${PKGCONFIG}" in portfile


def test_ci_pins_vcpkg_scripts_and_caches_manifest_inputs():
    workflow = (REPO_ROOT / ".github/workflows/build.yml").read_text()
    assert re.search(r"VCPKG_REVISION: [a-f0-9]{40}", workflow)
    assert "repository: microsoft/vcpkg" in workflow
    assert "ref: ${{ env.VCPKG_REVISION }}" in workflow
    assert "bootstrap-vcpkg.bat" in workflow
    cache_key = next(line for line in workflow.splitlines() if line.strip().startswith("key: vcpkg-"))
    for value in ("env.VCPKG_REVISION", "engine/vcpkg.json", "engine/vcpkg-configuration.json", "engine/ports/**"):
        assert value in cache_key
    assert "engine/build/vcpkg_installed" in workflow
    assert "C:\\vcpkg" not in workflow
    assert ".vcpkg/scripts/buildsystems/vcpkg.cmake" in workflow


def test_ci_stages_manifest_runtime_dlls_before_native_tests():
    workflow = (REPO_ROOT / ".github/workflows/build.yml").read_text()
    copy = workflow.index("Copy-Item engine\\build\\vcpkg_installed\\x64-windows\\bin\\*.dll")
    assert workflow.index("cmake --build engine") < copy < workflow.index("Run engine_tests")
    assert "Copy-Item engine\\build\\Release\\*.dll engine\\dist\\" in workflow

