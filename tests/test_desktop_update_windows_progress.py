"""The Windows hand-off keeps serving progress while its main thread blocks.

windows.ps1 answers /progress from a dedicated runspace precisely so the
window keeps moving through the long silent stretches (`hermes update`, pip,
the desktop rebuild) that made an 18-minute update look hung. This drives the
real script and polls the real listener; the posix half of the same contract
is covered in test_desktop_update_shim_progress.py.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from urllib.request import urlopen

import pytest

pytestmark = pytest.mark.windows_only

REPO_ROOT = Path(__file__).resolve().parent.parent
WINDOWS_UPDATE_PS1 = REPO_ROOT / "scripts" / "desktop-update" / "windows.ps1"


def _read_progress(url: str, deadline: float) -> dict[str, object]:
    """Poll /progress, retrying transient socket stalls until ``deadline``.

    A single slow answer from the PS runspace listener is NOT the bug this
    test guards (the listener can lose the CPU for seconds on a loaded CI
    runner while it still serves fine a moment later). One raw
    ``urlopen(timeout=5)`` propagating TimeoutError was exactly the Aug 2026
    flake (run 32440286339). Only a listener that stays unresponsive until
    the deadline fails the test.

    Per-attempt timeout is 1s, not 5s: a connection the kernel accepted into
    the backlog before the runspace was serving never gets answered, and a 5s
    wait on it burned half the readiness budget per attempt (two stale
    attempts = red, run 33591547099). The script's own readiness handshake
    now keeps that gap from reaching us, but the probe should not be able to
    lose the whole budget to one dead socket either way.
    """
    last_exc: Exception | None = None
    attempted = False
    while not attempted or time.monotonic() < deadline:
        attempted = True
        try:
            with urlopen(f"{url}progress", timeout=1) as response:
                return json.loads(response.read().decode("utf-8"))
        except (TimeoutError, OSError) as exc:  # transient stall — retry
            last_exc = exc
            time.sleep(0.1)
    raise AssertionError(
        f"/progress unresponsive until deadline (last error: {last_exc!r})"
    )


def test_progress_advances_while_the_orchestrator_blocks(tmp_path: Path) -> None:
    powershell = shutil.which("powershell.exe")
    assert powershell, "Windows updater tests require Windows PowerShell."

    output_path = tmp_path / "self-test-output.log"
    env = os.environ.copy()
    env["TEMP"] = str(tmp_path)
    env["TMP"] = str(tmp_path)
    # Generous hold: the assertions below must both land INSIDE it. 4s was
    # too tight for a slow runner — the second sample slid past the hold,
    # caught the cleared terminal state, and failed '' == 'Testing quiet
    # update' (PR #90358 rerun, Aug 2026). 10s left no headroom once
    # transient /progress retries entered the budget (publish wait ≤10s +
    # stability window + retry sleeps), so: 30s, and every sampling deadline
    # below is derived from the moment the held stage lands, keeping the
    # whole window comfortably inside the hold.
    env["HERMES_SELFTEST_HOLD_SECONDS"] = "30"

    with output_path.open("wb") as output:
        process = subprocess.Popen(
            [
                powershell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(WINDOWS_UPDATE_PS1),
                "-SelfTestUi",
                "-NoUi",
            ],
            stdout=output,
            stderr=subprocess.STDOUT,
            env=env,
        )

    try:
        deadline = time.monotonic() + 20
        shim_url = None
        while time.monotonic() < deadline:
            text = output_path.read_text(encoding="utf-8", errors="replace")
            match = re.search(r"SELF-TEST: shim at (http://127\.0\.0\.1:\d+/)", text)
            if match:
                shim_url = match.group(1)
                break
            if process.poll() is not None:
                break
            time.sleep(0.1)

        assert shim_url, output_path.read_text(encoding="utf-8", errors="replace")

        # The URL prints BEFORE the orchestrator publishes its held stage —
        # sampling immediately races the publish and can catch the page's
        # boot default instead ('Hermes will open once done.' ==
        # 'Testing quiet update', PR #90358 first run). Wait for the held
        # stage to actually land, THEN start the stability window.
        held_stage = "Testing quiet update"
        publish_deadline = time.monotonic() + 10
        first = _read_progress(shim_url, publish_deadline)
        while first.get("message") != held_stage and time.monotonic() < publish_deadline:
            time.sleep(0.1)
            first = _read_progress(shim_url, publish_deadline)
        assert first["message"] == held_stage, first

        time.sleep(1.5)
        second = _read_progress(shim_url, time.monotonic() + 10)

        # The stage is whatever the orchestrator last published -- it must
        # reach the page verbatim and must not churn on its own.
        assert first["status"] == "running"
        assert first["message"]
        assert second["message"] == first["message"]
        # The main thread is asleep for the whole window above. If elapsed
        # only moved when the orchestrator published, it would be frozen here
        # -- which is what a stalled update looks like to the user.
        assert int(second["elapsed_seconds"]) > int(first["elapsed_seconds"])

        assert process.wait(timeout=60) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_delayed_poll_still_observes_the_terminal_status(tmp_path: Path) -> None:
    """A poll delayed past the old fixed 900ms teardown window must still see it.

    ``Publish-UiEvent`` used to sleep a fixed 900ms after publishing a
    terminal status, then tear the listener down unconditionally -- on the
    assumption that a poll (every 400ms) always lands inside that window. A
    backgrounded tab, a slow connection, or a busy scheduler can miss it;
    every later poll then fails against a dead listener and the shim holds
    "Updating Hermes" forever even though the update already finished
    (#103747). Simulate that delay directly: wait well past the old 900ms
    bound (and comfortably under the new bounded wait) before polling, and
    confirm the listener is still up and reports the terminal state.
    """
    powershell = shutil.which("powershell.exe")
    assert powershell, "Windows updater tests require Windows PowerShell."

    output_path = tmp_path / "self-test-output.log"
    env = os.environ.copy()
    env["TEMP"] = str(tmp_path)
    env["TMP"] = str(tmp_path)
    env["HERMES_SELFTEST_HOLD_SECONDS"] = "2"

    with output_path.open("wb") as output:
        process = subprocess.Popen(
            [
                powershell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(WINDOWS_UPDATE_PS1),
                "-SelfTestUi",
                "-NoUi",
            ],
            stdout=output,
            stderr=subprocess.STDOUT,
            env=env,
        )

    try:
        deadline = time.monotonic() + 20
        shim_url = None
        while time.monotonic() < deadline:
            text = output_path.read_text(encoding="utf-8", errors="replace")
            match = re.search(r"SELF-TEST: shim at (http://127\.0\.0\.1:\d+/)", text)
            if match:
                shim_url = match.group(1)
                break
            if process.poll() is not None:
                break
            time.sleep(0.1)

        assert shim_url, output_path.read_text(encoding="utf-8", errors="replace")

        # $hold (2s) plus a margin well past the OLD fixed 900ms teardown
        # window, and comfortably under the new bounded wait -- so this
        # fails on the old code (listener already gone) and passes on the
        # fixed one (listener waits for actual delivery).
        time.sleep(5.0)
        served = _read_progress(shim_url, time.monotonic() + 5)
        assert served["status"] == "done", served

        assert process.wait(timeout=15) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
