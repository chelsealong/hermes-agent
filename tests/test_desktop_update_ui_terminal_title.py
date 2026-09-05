"""ui.html must name the terminal outcome, not just flip a checkmark.

The shim's `done` branch settles the body class and swaps in a checkmark
glyph but never touches the heading, so a *successful* update's progress
window keeps reading "Updating Hermes" even once it has actually finished
(part of #103747; `manual` already sets the heading to "Update complete").

Drives the real `serve-ui.py` + `ui.html` -- no source reading -- and
executes the page's own `<script>` block in a plain Node `vm` context (no
jsdom: apps/desktop's devDependencies are not installed here), mocking only
the DOM surface the shim's script touches.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SHIM_DIR = REPO_ROOT / "scripts" / "desktop-update"

requires_node = pytest.mark.skipif(not shutil.which("node"), reason="requires node")

_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

const base = process.argv[2];

function makeEl(initial) {
  return {
    _text: initial || '', _html: '',
    get textContent() { return this._text; }, set textContent(v) { this._text = v; },
    get innerHTML() { return this._html; }, set innerHTML(v) { this._html = v; },
    appendChild() {}, setAttribute() {},
  };
}

const elements = {
  loader: makeEl(), glyph: makeEl(),
  title: makeEl('Updating Hermes'),
  line: makeEl('Hermes will open once done.'),
};
const bodyState = { className: '' };

const sandbox = {
  document: {
    getElementById(id) { return elements[id]; },
    body: bodyState,
    createElementNS() { return makeEl(); },
  },
  window: { requestAnimationFrame() { return 1; }, cancelAnimationFrame() {} },
  performance: { now: () => Date.now() },
  Math, Array, Number, String, setTimeout,
  fetch: (url, opts) => fetch(new URL(url, base), opts),
};

(async () => {
  const html = await (await fetch(base)).text();
  const script = html.match(/<script>([\s\S]*)<\/script>/)[1];
  vm.createContext(sandbox);
  vm.runInContext(script, sandbox, { filename: 'ui.html-script' });
  // One poll cycle (400ms interval) is enough: the fixture status is
  // already terminal from the first request.
  await new Promise(r => setTimeout(r, 700));
  console.log(JSON.stringify({ title: elements.title.textContent, bodyClass: bodyState.className }));
})().catch(e => { console.error(e); process.exit(1); });
"""


@pytest.fixture
def progress(tmp_path):
    status = tmp_path / "hermes-update-status"
    proc = subprocess.Popen(
        [
            sys.executable,
            str(SHIM_DIR / "serve-ui.py"),
            str(SHIM_DIR / "ui.html"),
            str(status),
            str(time.time()),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        port = int(proc.stdout.readline().strip())

        class Progress:
            def publish(self, state: str, message: str) -> None:
                status.write_text(json.dumps({"status": state, "message": message}), encoding="utf-8")

            @property
            def url(self) -> str:
                return f"http://127.0.0.1:{port}/"

        yield Progress()
    finally:
        proc.kill()
        proc.wait(timeout=5)


@requires_node
def test_done_status_sets_the_terminal_heading(tmp_path, progress) -> None:
    progress.publish("done", "")

    harness = tmp_path / "harness.js"
    harness.write_text(_HARNESS, encoding="utf-8")
    result = subprocess.run(
        ["node", str(harness), progress.url],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])

    assert payload["bodyClass"] == "done"
    assert payload["title"] == "Update complete"
