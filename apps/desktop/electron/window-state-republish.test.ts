import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { test } from 'vitest'

// Regression coverage for #102451: a republished connection (reconnect,
// gateway/profile switch) used to carry the window-state snapshot baked into
// startHermes()'s cached connection promise at process cold start, which
// clobbered the live `isFullscreen` flag pushed by enter/leave-full-screen.
// These handlers can't be exercised directly (main.ts has Electron
// app-lifecycle side effects at import time), so — matching the pattern in
// hardening.test.ts — assert on the source text of the fix instead.

const __dirname = path.dirname(fileURLToPath(import.meta.url))

function readMain() {
  return fs.readFileSync(path.join(__dirname, 'main.ts'), 'utf8').replace(/\r\n/g, '\n')
}

test('hermes:connection and hermes:connection:for attach a fresh getWindowState() at return time', () => {
  const source = readMain()

  for (const channel of ['hermes:connection', 'hermes:connection:for']) {
    const handlerStart = source.indexOf(`ipcMain.handle('${channel}'`)
    assert.notEqual(handlerStart, -1, `${channel} handler must exist`)
    const handlerEnd = source.indexOf('\n})', handlerStart)
    const body = source.slice(handlerStart, handlerEnd)

    assert.match(
      body,
      /\.\.\.getWindowState\(/,
      `${channel} must spread a freshly computed getWindowState(), not just the cached connection`
    )
  }
})

test('secondary chat and browser windows report fullscreen changes on themselves, not the primary window', () => {
  const source = readMain()

  for (const fn of ['function spawnSecondaryWindow', 'function spawnBrowserWindow']) {
    const fnStart = source.indexOf(fn)
    assert.notEqual(fnStart, -1, `${fn} must exist`)
    const fnEnd = source.indexOf('\nfunction ', fnStart + 1)
    const body = source.slice(fnStart, fnEnd === -1 ? undefined : fnEnd)

    assert.match(
      body,
      /win\.on\('enter-full-screen', \(\) => sendWindowStateChanged\(true, win\)\)/,
      `${fn} must target its own window, matching the instance-window path (#102451)`
    )
    assert.match(
      body,
      /win\.on\('leave-full-screen', \(\) => sendWindowStateChanged\(false, win\)\)/,
      `${fn} must target its own window, matching the instance-window path (#102451)`
    )
  }
})
