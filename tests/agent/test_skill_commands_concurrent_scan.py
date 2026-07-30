"""Regression test for #74574.

``scan_skill_commands()`` resets and repopulates the module-level
``_skill_commands`` dict on every call. Two independent code paths call it
without going through the ``get_skill_commands()`` cache guard — the TUI
gateway's ``commands.catalog`` handler (runs inline on the main thread) and
its ``complete.slash`` handler (explicitly routed to a background thread
pool, see ``tui_gateway/server.py``'s ``_LONG_HANDLERS`` comment: "complete
.slash does first-call prompt_toolkit imports + a skill-dir scan"). When the
TUI fires both close together at startup, the two scans can run on separate
OS threads at the same time.

Before the fix, both threads race on the same unlocked global dict: one
thread's freshly-inserted entries look like a collision to the other
thread's still-in-progress pass over the *same* skill, so `scan_skill_commands`
logs "Skill 'x' maps to slash command /x already claimed by 'x'" — a skill
claimed by itself — even though every skill name here is distinct and
slug-unique, so that warning should be structurally impossible.
"""

import logging
import shutil
import tempfile
import textwrap
import threading
import time
from pathlib import Path

import pytest


def _write_skill(skills_dir: Path, name: str) -> None:
    skill_dir = skills_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        textwrap.dedent(
            f"""\
            ---
            name: {name}
            description: {name} skill
            ---
            body
            """
        )
    )


@pytest.fixture
def many_skills_home(monkeypatch):
    td = tempfile.mkdtemp(prefix="hermes-concurrent-skills-")
    home = Path(td)
    skills_dir = home / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    for i in range(40):
        _write_skill(skills_dir, f"demo-skill-{i:02d}")

    import tools.skills_tool as _st
    import agent.skill_commands as _sc

    monkeypatch.setattr(_st, "HERMES_HOME", home, raising=False)
    monkeypatch.setattr(_st, "SKILLS_DIR", skills_dir, raising=False)
    monkeypatch.setattr(_sc, "_skill_commands", {}, raising=False)

    # Sleep a hair on every frontmatter parse so two threads' scans actually
    # overlap in wall-clock time — without this, the OS scheduler could
    # happen to serialize fast in-memory work and hide the bug (same
    # rationale as the sleep in test_compression_concurrent_fork.py).
    orig_parse = _st._parse_frontmatter

    def _slow_parse(content):
        time.sleep(0.002)
        return orig_parse(content)

    monkeypatch.setattr(_st, "_parse_frontmatter", _slow_parse)

    yield skills_dir

    shutil.rmtree(td, ignore_errors=True)


def test_concurrent_scans_never_log_a_self_collision(many_skills_home, caplog):
    """Two threads scanning the same skills concurrently must not warn that
    a skill's slash command is 'already claimed by' that same skill.

    Every skill name here is distinct and already a clean slug, so the
    "already claimed by" warning has no legitimate reason to fire at all
    (it exists for the case where two *different* names normalize to the
    same slug). Any occurrence is the concurrent-scan corruption from #74574.
    """
    from agent.skill_commands import scan_skill_commands

    errors: list[Exception] = []

    def worker():
        try:
            for _ in range(3):
                scan_skill_commands()
        except Exception as exc:  # pragma: no cover - surfaced via assertion below
            errors.append(exc)

    with caplog.at_level(logging.WARNING, logger="agent.skill_commands"):
        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

    assert not errors, f"scan_skill_commands raised under concurrency: {errors}"

    self_collisions = [
        r.message for r in caplog.records
        if "already claimed by" in r.message and r.message.split("'")[1] == r.message.split("'")[3]
    ]
    assert not self_collisions, (
        "scan_skill_commands() logged a skill claimed by itself, which can only "
        "happen if two concurrent scans interleaved on the shared global "
        f"_skill_commands dict (see #74574): {self_collisions}"
    )

    # After all scans complete, the result must be fully consistent: every
    # one of the 40 distinct skills registered exactly once.
    from agent.skill_commands import get_skill_commands
    final = get_skill_commands()
    assert len(final) == 40


def test_slow_scan_does_not_block_a_concurrent_scan(many_skills_home):
    """A scan in progress on one thread must not stall a scan on another.

    The fix must not "solve" the log-noise bug by holding a lock across the
    whole (I/O-bound) directory walk — that would serialize concurrent scans
    and could stall the TUI gateway's single stdin-dispatch thread behind a
    background scan for the full scan duration, exactly the stall
    `_LONG_HANDLERS` (tui_gateway/server.py) exists to prevent. The lock must
    guard only the final publish step, so N concurrent scans take roughly as
    long as one scan, not N times as long.
    """
    from agent.skill_commands import scan_skill_commands

    durations: list[float] = []
    lock = threading.Lock()

    def worker():
        start = time.monotonic()
        scan_skill_commands()
        elapsed = time.monotonic() - start
        with lock:
            durations.append(elapsed)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    overall_start = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    overall_elapsed = time.monotonic() - overall_start

    # Each individual scan sleeps ~40 * 0.002s = 0.08s doing frontmatter
    # parsing. If scans were serialized behind a whole-scan lock, 4 of them
    # would take roughly 4x that (~0.32s+) end to end. Running concurrently,
    # total wall time should stay well under 2x a single scan's own work.
    single_scan_floor = 0.08
    assert overall_elapsed < single_scan_floor * 2, (
        f"4 concurrent scans took {overall_elapsed:.3f}s total, expected well "
        f"under {single_scan_floor * 2:.3f}s if they ran concurrently rather "
        "than serialized behind a lock held across the whole scan."
    )
