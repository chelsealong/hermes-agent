"""The venv-currency probe must catch a project whose requires-python moved
past the interpreter the recorded venv was actually built with, even when
the dependency stamp is unchanged (issue #123201)."""
from __future__ import annotations


def _write_pyvenv(environment, version: str) -> None:
    environment.mkdir(parents=True, exist_ok=True)
    (environment / "pyvenv.cfg").write_text(f"version = {version}\n", encoding="utf-8")


def test_stale_interpreter_after_a_requires_python_bump_is_not_current(tmp_path):
    from pm.environments import install_state_dir, runtime_facts_path
    from pm.install import _runtime_state_matches
    from pm.lock import Facts

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text('[project]\nrequires-python = ">=3.14"\n', encoding="utf-8")
    environment = install_state_dir(repo) / "environments" / "existing" / "venv"
    _write_pyvenv(environment, "3.13.2")
    facts_path = runtime_facts_path(repo)
    Facts(facts_path).record_state("venv", "abc", [], environment=environment)

    fact = Facts(facts_path).get("venv")
    assert not _runtime_state_matches(fact, "abc", project_root=repo), (
        "a venv built for an interpreter that no longer satisfies requires-python must not read as current"
    )

    _write_pyvenv(environment, "3.14.1")
    fact = Facts(facts_path).get("venv")
    assert _runtime_state_matches(fact, "abc", project_root=repo)


def test_missing_requires_python_or_pyvenv_version_does_not_block_currency(tmp_path):
    from pm.environments import install_state_dir, runtime_facts_path
    from pm.install import _runtime_state_matches
    from pm.lock import Facts

    repo = tmp_path / "repo"
    repo.mkdir()
    environment = install_state_dir(repo) / "environments" / "existing" / "venv"
    environment.mkdir(parents=True)
    (environment / "pyvenv.cfg").write_text("home = test\n", encoding="utf-8")
    facts_path = runtime_facts_path(repo)
    Facts(facts_path).record_state("venv", "abc", [], environment=environment)

    fact = Facts(facts_path).get("venv")
    assert _runtime_state_matches(fact, "abc", project_root=repo)
