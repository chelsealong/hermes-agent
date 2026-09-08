"""Regression for #106025: install.sh's connectivity check must not treat
duckduckgo.com the same as pypi.org.

pypi.org is what the install itself depends on. duckduckgo.com is only used
by the web-search tool at runtime, so a network that blocks it while
pypi.org is healthy (observed on some residential networks) should get an
informational note, not the "connectivity check failed"-style hard warning —
that warning sends users off debugging a network that is actually fine for
the install.

This test drives the real ``check_network_prerequisites()`` out of
``scripts/install.sh`` with a stubbed ``curl``, rather than asserting on a
copy, so the guard cannot drift away from the test.
"""

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "scripts" / "install.sh"


def _extract_function(name: str) -> str:
    text = INSTALL_SH.read_text(encoding="utf-8")
    match = re.search(rf"^{re.escape(name)}\(\) \{{\n.*?^\}}\n", text, re.M | re.S)
    assert match, f"could not locate {name}() in scripts/install.sh"
    return match.group(0)


def _run(curl_stub: str) -> subprocess.CompletedProcess:
    body = _extract_function("check_network_prerequisites")
    script = f"""
set -e
DISTRO=linux
log_info() {{ echo "INFO: $*"; }}
log_warn() {{ echo "WARN: $*"; }}
log_success() {{ echo "SUCCESS: $*"; }}
{curl_stub}
{body}
check_network_prerequisites
"""
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


# curl(1) is invoked as: curl -fsSI --max-time 8 "$url"
DUCKDUCKGO_ONLY_DOWN = """
curl() {
    for a in "$@"; do
        case "$a" in
            *duckduckgo.com*) return 1 ;;
            *pypi.org*) return 0 ;;
        esac
    done
    return 0
}
"""

PYPI_DOWN = """
curl() {
    for a in "$@"; do
        case "$a" in
            *pypi.org*) return 1 ;;
            *duckduckgo.com*) return 0 ;;
        esac
    done
    return 0
}
"""


def test_duckduckgo_only_down_is_informational_not_a_hard_warning() -> None:
    result = _run(DUCKDUCKGO_ONLY_DOWN)
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "SUCCESS: Internet connectivity looks good" in out
    assert "WARN: Could not reach https://duckduckgo.com/" not in out
    assert "WARN: Network checks failed" not in out
    assert "INFO: Could not reach https://duckduckgo.com/" in out


def test_pypi_down_is_still_a_hard_warning() -> None:
    result = _run(PYPI_DOWN)
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "WARN: Could not reach https://pypi.org/simple/" in out
    assert "WARN: Network checks failed" in out
