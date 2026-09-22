import assert from 'node:assert/strict'

import { test } from 'vitest'

import { guardAgainstWatchStorm } from './watch-storm-guard'

function makeTimers() {
  const pending = new Map<number, () => void>()
  let nextId = 1
  let now = 0

  return {
    advance(ms: number) {
      now += ms
    },
    clearTimeout: (handle: unknown) => {
      pending.delete(handle as number)
    },
    fire() {
      const jobs = [...pending.values()]
      pending.clear()

      for (const job of jobs) {
        job()
      }
    },
    get pendingCount() {
      return pending.size
    },
    now: () => now,
    setTimeout: (fn: () => void, _ms: number) => {
      const id = nextId++
      pending.set(id, fn)

      return id
    }
  }
}

function makeNativeWatcherFactory() {
  const created: Array<{ onEvent: (...args: any[]) => void; closed: boolean }> = []

  return {
    created,
    create(onEvent: (...args: any[]) => void) {
      const handle = { onEvent, closed: false }
      created.push(handle)

      return { close: () => (handle.closed = true) }
    }
  }
}

test('guardAgainstWatchStorm forwards events under the threshold', () => {
  const timers = makeTimers()
  const native = makeNativeWatcherFactory()
  const seen: any[] = []

  guardAgainstWatchStorm(native.create, (...args) => seen.push(args), {
    maxEventsPerWindow: 5,
    timers
  })

  const [{ onEvent }] = native.created

  for (let i = 0; i < 5; i += 1) {
    onEvent('rename', 'file.txt')
  }

  assert.equal(seen.length, 5)
  assert.equal(native.created.length, 1)
  assert.equal(native.created[0].closed, false)
})

test('guardAgainstWatchStorm closes the native watcher once the event rate trips, instead of forwarding every event', () => {
  const timers = makeTimers()
  const native = makeNativeWatcherFactory()
  const seen: any[] = []

  guardAgainstWatchStorm(native.create, (...args) => seen.push(args), {
    maxEventsPerWindow: 5,
    timers
  })

  const [{ onEvent }] = native.created

  // Simulate the reported storm: hundreds of rename events within one
  // window, all in the same tick fs.watch would deliver them in.
  for (let i = 0; i < 500; i += 1) {
    onEvent('rename', 'file.txt')
  }

  assert.equal(seen.length, 5, 'downstream work must stop once the window is over threshold')
  assert.equal(native.created[0].closed, true, 'the native watcher must be closed to stop the OS-level storm')
  assert.equal(native.created.length, 1, 'must not reopen immediately into the same flood')
})

test('guardAgainstWatchStorm reopens after the cooldown once things are quiet', () => {
  const timers = makeTimers()
  const native = makeNativeWatcherFactory()
  const seen: any[] = []

  guardAgainstWatchStorm(native.create, (...args) => seen.push(args), {
    cooldownMs: 2_000,
    maxEventsPerWindow: 5,
    timers
  })

  const first = native.created[0]

  for (let i = 0; i < 10; i += 1) {
    first.onEvent('rename', 'file.txt')
  }

  assert.equal(first.closed, true)
  assert.equal(timers.pendingCount, 1)

  timers.advance(2_000)
  timers.fire()

  assert.equal(native.created.length, 2, 'a fresh native watcher opens after the cooldown')

  const second = native.created[1]
  second.onEvent('rename', 'file.txt')

  assert.equal(seen.length, 6, 'events flow normally again once reopened')
})

test('guardAgainstWatchStorm.close() tears down the cooldown timer and the native watcher', () => {
  const timers = makeTimers()
  const native = makeNativeWatcherFactory()

  const guarded = guardAgainstWatchStorm(native.create, () => {}, {
    maxEventsPerWindow: 5,
    timers
  })

  const first = native.created[0]

  for (let i = 0; i < 10; i += 1) {
    first.onEvent('rename', 'file.txt')
  }

  assert.equal(timers.pendingCount, 1)

  guarded.close()

  assert.equal(timers.pendingCount, 0)

  timers.fire()

  assert.equal(native.created.length, 1, 'close() during cooldown must not let a new watcher open')
})
