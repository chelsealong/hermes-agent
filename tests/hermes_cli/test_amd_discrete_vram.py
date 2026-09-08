"""A discrete AMD GPU with no NVIDIA device present must not be classified as
unified memory. Before this probe existed, ``probe_budget()`` fell through to
the RAM-as-UMA branch on any machine without an NVIDIA device, so a 24 GiB
Radeon card in a 62.4 GiB box budgeted the full 62.4 GiB as "GPU memory" and
recommended models that do not fit on the card (issue #105710)."""

from __future__ import annotations

from pathlib import Path

import hermes_cli.local_runtime.hardware as hw

GIB = 1 << 30


def _no_nvidia(monkeypatch):
    monkeypatch.setattr(hw, "_pool_probe_cache", None)
    monkeypatch.setattr(hw, "_nvidia_vram", lambda: None)
    monkeypatch.setattr(hw, "_device_pool_view", lambda: None)


# ── _amd_vram: reading the sysfs VRAM bar ─────────────────────


def _write_card(root: Path, name: str, *, vendor: str, total: int | None = None,
                 used: int | None = None):
    device = root / name / "device"
    device.mkdir(parents=True)
    (device / "vendor").write_text(vendor)
    if total is not None:
        (device / "mem_info_vram_total").write_text(str(total))
    if used is not None:
        (device / "mem_info_vram_used").write_text(str(used))


def test_amd_vram_reads_the_sysfs_bar(tmp_path, monkeypatch):
    _write_card(tmp_path, "card0", vendor="0x1002",
                total=25753026560, used=1494384640)
    monkeypatch.setattr(hw, "_DRM_SYSFS_ROOT", tmp_path)
    assert hw._amd_vram() == (25753026560, 25753026560 - 1494384640)


def test_amd_vram_skips_non_amd_vendor(tmp_path, monkeypatch):
    _write_card(tmp_path, "card0", vendor="0x10de", total=24 * GIB, used=0)
    monkeypatch.setattr(hw, "_DRM_SYSFS_ROOT", tmp_path)
    assert hw._amd_vram() is None


def test_amd_vram_none_when_no_drm_cards(tmp_path, monkeypatch):
    monkeypatch.setattr(hw, "_DRM_SYSFS_ROOT", tmp_path)
    assert hw._amd_vram() is None


def test_amd_vram_none_when_missing_and_no_such_sysfs_root(monkeypatch):
    monkeypatch.setattr(hw, "_DRM_SYSFS_ROOT", Path("/does/not/exist"))
    assert hw._amd_vram() is None


# ── probe_budget wiring ───────────────────────────────────────


def test_discrete_amd_card_budgets_from_device_query(monkeypatch):
    """The RX 7900 XTX shape from #105710: a 24.0 GiB card in a 62.4 GiB box.
    24.0 / 62.4 = 0.38, far below the RAM-carve gate, so this must budget
    like a discrete NVIDIA card, not RAM-as-UMA."""
    _no_nvidia(monkeypatch)
    ram_total = 67004100608  # 62.4 GiB
    total, free = 25753026560, 25753026560 - 1494384640  # 24.0 GiB, 1.4 GiB used
    monkeypatch.setattr(hw, "_amd_vram", lambda: (total, free))
    monkeypatch.setattr(hw, "_ram_bytes", lambda: (ram_total, 60 * GIB))

    b = hw.probe_budget(planning=True)
    assert b.uma is False
    assert b.total_device_bytes == total
    margin = max(hw._MARGIN_FLOOR, int(total * hw._MARGIN_FRACTION))
    assert b.usable_vram_bytes == total - margin
    assert b.ram_available_bytes == ram_total
    # The whole point: the budget must not dwarf the card the way RAM-as-UMA did.
    assert b.usable_vram_bytes < total


def test_discrete_amd_card_live_uses_device_free(monkeypatch):
    _no_nvidia(monkeypatch)
    total, free = 24 * GIB, 22 * GIB
    monkeypatch.setattr(hw, "_amd_vram", lambda: (total, free))
    monkeypatch.setattr(hw, "_ram_bytes", lambda: (62 * GIB, 58 * GIB))

    b = hw.probe_budget(planning=False)
    assert b.uma is False
    margin = max(hw._MARGIN_FLOOR, int(total * hw._MARGIN_FRACTION))
    assert b.usable_vram_bytes == free - margin
    assert b.ram_available_bytes == 58 * GIB


def test_bios_carve_apu_stays_uma(monkeypatch):
    """A carve that tracks system RAM (the Strix Halo shape in #102593) must
    keep today's RAM-as-UMA behavior, not be treated as a discrete card."""
    _no_nvidia(monkeypatch)
    ram_total = 32 * GIB
    monkeypatch.setattr(hw, "_amd_vram", lambda: (26 * GIB, 26 * GIB))  # 0.81 of RAM
    monkeypatch.setattr(hw, "_ram_bytes", lambda: (ram_total, 20 * GIB))

    b = hw.probe_budget(planning=True)
    assert b.uma is True
    assert b.total_device_bytes == ram_total


def test_no_amd_device_falls_back_to_uma(monkeypatch):
    """No NVIDIA and no AMD device (e.g. Apple Silicon, or amdgpu sysfs
    unavailable): unchanged RAM-as-UMA behavior."""
    _no_nvidia(monkeypatch)
    ram_total = 32 * GIB
    monkeypatch.setattr(hw, "_amd_vram", lambda: None)
    monkeypatch.setattr(hw, "_ram_bytes", lambda: (ram_total, 20 * GIB))

    b = hw.probe_budget(planning=True)
    assert b.uma is True
    assert b.total_device_bytes == ram_total
