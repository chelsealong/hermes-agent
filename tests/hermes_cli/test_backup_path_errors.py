"""Regression test: `hermes backup -o <bad path>` errors cleanly (round-3 SUB-01).

Before, an unwritable/nonexistent-parent output path raised a raw
PermissionError traceback from the unguarded is_dir()/mkdir() calls. It must
print a one-line error and exit 1 instead.
"""

from argparse import Namespace
from pathlib import Path

import pytest


def _make_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    (home / "config.yaml").write_text("model: {}\n")
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


def test_backup_unwritable_parent_errors_cleanly(tmp_path, monkeypatch, capsys):
    _make_home(tmp_path, monkeypatch)
    import hermes_cli.backup as backup_mod

    # A parent directory that cannot be created (a file stands where the dir
    # would go) reliably triggers an OSError on mkdir without needing root.
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file, not a dir")
    bad_out = blocker / "sub" / "backup.zip"

    with pytest.raises(SystemExit) as exc:
        backup_mod.run_backup(Namespace(output=str(bad_out)))

    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "cannot write backup" in out.lower()
    assert "Traceback" not in out


def test_quick_backup_with_output_errors_instead_of_silently_dropping_it(
    tmp_path, monkeypatch, capsys
):
    """`hermes backup --quick -o <path>` must not exit 0 with no file written.

    Regression for #98369: --quick and --output are mutually exclusive
    (quick snapshots live under state-snapshots/, not a single file), and the
    combination used to be silently accepted — parsed, ignored, exit 0.
    """
    _make_home(tmp_path, monkeypatch)
    from hermes_cli.main import cmd_backup

    out_path = tmp_path / "requested.zip"

    with pytest.raises(SystemExit) as exc:
        cmd_backup(Namespace(quick=True, output=str(out_path), label=None))

    assert exc.value.code == 1
    assert not out_path.exists()
    err = capsys.readouterr().err
    assert "--quick" in err
    assert "--output" in err or "-o" in err
