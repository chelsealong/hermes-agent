"""Regression for #116809: install_system_packages() probed ffmpeg/ripgrep
with `command -v`, which only sees PATH. On a fresh install `setup_path()`
(which appends the command link dir to PATH) hasn't run yet, so a binary the
user pre-staged directly in the link dir (e.g. a static ffmpeg dropped into
~/.local/bin to avoid a huge distro package) was reported missing and the
installer offered to pull it via the system package manager anyway.

`install_system_packages()` must also probe the command link dir directly,
not just PATH.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
INSTALL_SH = REPO_ROOT / "scripts" / "install.sh"


def _extract_function(src: str, name: str) -> str:
    match = re.search(rf"^{name}\(\) \{{\n(.*?)^\}}", src, re.S | re.M)
    assert match is not None, f"{name}() not found in scripts/install.sh"
    return match.group(1)


def _extract_resolve_cmd_path() -> str:
    src = INSTALL_SH.read_text(encoding="utf-8")
    return _extract_function(src, "_resolve_cmd_path")


def _extract_install_system_packages() -> str:
    src = INSTALL_SH.read_text(encoding="utf-8")
    return _extract_function(src, "install_system_packages")


def _run(tmp_path: Path, *, stage_ffmpeg_in_link_dir: bool) -> tuple[int, str, str]:
    link_dir = tmp_path / "link_dir"
    link_dir.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    if stage_ffmpeg_in_link_dir:
        ffmpeg = link_dir / "ffmpeg"
        ffmpeg.write_text("#!/bin/sh\necho 'ffmpeg version 7.1-static'\n", encoding="utf-8")
        ffmpeg.chmod(0o755)

    # A fully curated PATH: only the specific real coreutils the function body
    # needs (awk/head, for parsing `ffmpeg -version`) are linked in, plus
    # stubs for every command that could install or elevate. No real
    # ffmpeg/rg/apt/sudo from the host is reachable, so this can never fall
    # through to an actual package-manager invocation regardless of the host.
    for tool in ("head", "awk"):
        real = shutil.which(tool)
        assert real is not None, f"{tool} not found on host PATH"
        os.symlink(real, bin_dir / tool)

    def _stub(name: str, body: str) -> None:
        stub = bin_dir / name
        stub.write_text(f"#!/bin/sh\n{body}", encoding="utf-8")
        stub.chmod(0o755)

    _stub("id", "echo 1000\n")  # never root
    for cmd in ("sudo", "apt", "apt-get", "dpkg", "pacman", "dnf", "brew", "cargo"):
        _stub(cmd, "echo \"unexpected call: $0 $*\" >&2\nexit 1\n")

    env = os.environ.copy()
    env["PATH"] = str(bin_dir)

    driver = tmp_path / "driver.sh"
    driver.write_text(
        "#!/usr/bin/env bash\n"
        "set -uo pipefail\n"
        "OS=linux\n"
        "DISTRO=ubuntu\n"
        "IS_INTERACTIVE=false\n"
        "log_info()    { printf 'INFO %s\\n' \"$*\"; }\n"
        "log_success() { printf 'OK %s\\n' \"$*\"; }\n"
        "log_warn()    { printf 'WARN %s\\n' \"$*\"; }\n"
        "log_error()   { printf 'ERROR %s\\n' \"$*\" >&2; }\n"
        "prompt_yes_no() { return 1; }\n"
        "show_manual_install_hint() { log_info \"install $1 manually\"; }\n"
        f"get_command_link_dir() {{ echo {link_dir}; }}\n"
        "_resolve_cmd_path() {\n"
        f"{_extract_resolve_cmd_path()}"
        "}\n"
        "install_system_packages() {\n"
        f"{_extract_install_system_packages()}"
        "}\n"
        # ripgrep isn't part of this bug — keep it satisfied via PATH so only
        # the ffmpeg probe is under test.
        "rg() { echo 'ripgrep 14.0.0'; }\n"
        "install_system_packages\n"
        'printf "HAS_FFMPEG=%s\\n" "$HAS_FFMPEG"\n',
        encoding="utf-8",
    )

    bash = shutil.which("bash")
    assert bash is not None, "bash not found on host PATH"
    proc = subprocess.run(
        [bash, str(driver)],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
        timeout=30,
    )
    return proc.returncode, proc.stdout, proc.stderr


def test_prestaged_ffmpeg_in_link_dir_is_found_before_path_is_updated(tmp_path: Path) -> None:
    """The bug: PATH doesn't have the link dir yet (setup_path hasn't run),
    but a working ffmpeg is already sitting in it. This must be detected and
    the package manager must not be invoked."""
    code, stdout, stderr = _run(tmp_path, stage_ffmpeg_in_link_dir=True)

    assert code == 0, stderr
    assert "ffmpeg 7.1-static found" in stdout, stdout
    assert "HAS_FFMPEG=true" in stdout, stdout
    assert "ffmpeg not installed" not in stdout, stdout


def test_missing_ffmpeg_still_reported_missing(tmp_path: Path) -> None:
    """Sanity check: when ffmpeg genuinely isn't anywhere, behavior is
    unchanged — it's reported missing and a manual-install hint is shown,
    with no package-manager command available to invoke."""
    code, stdout, stderr = _run(tmp_path, stage_ffmpeg_in_link_dir=False)

    assert code == 0, stderr
    assert "HAS_FFMPEG=false" in stdout, stdout
    assert "ffmpeg not installed" in stdout, stdout
