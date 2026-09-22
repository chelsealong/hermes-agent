// Guards a raw directory watcher (`fs.watch`) against a rename-event storm.
//
// On Windows, a churning subtree can drive `ReadDirectoryChangesW` into
// emitting on the order of 100k+ `rename` events/second (#118974). Almost all
// of that cost is kernel-side IOCP completion handling for each notification,
// so it happens no matter how cheap the JS callback is — a JS-level debounce
// on the *downstream* work does nothing to stop it. The only thing that has
// been shown to stop it is closing the watch handle itself, so this guard
// counts events per short window and, once the rate trips a threshold, closes
// the native watcher immediately and reopens it after a cooldown of quiet.
//
// Pure and Node-free (the native watcher and timers are both injected) so it
// can be unit-tested without a real filesystem, mirroring stream-throttle.ts.

export interface StormGuardTimers {
  setTimeout(fn: () => void, ms: number): unknown
  clearTimeout(handle: unknown): void
  now(): number
}

export interface StormGuardOptions {
  /** Events are counted in rolling windows of this length. */
  windowMs?: number
  /** Event count within one `windowMs` window that trips the guard. */
  maxEventsPerWindow?: number
  /** How long the native watcher stays closed once tripped. */
  cooldownMs?: number
  timers?: StormGuardTimers
}

export interface NativeWatcher {
  close(): void
}

export interface StormGuardedWatch {
  close(): void
}

const DEFAULT_WINDOW_MS = 200
const DEFAULT_MAX_EVENTS_PER_WINDOW = 500
const DEFAULT_COOLDOWN_MS = 2_000

const realTimers: StormGuardTimers = {
  setTimeout: (fn, ms) => setTimeout(fn, ms),
  clearTimeout: handle => clearTimeout(handle as never),
  now: () => Date.now()
}

/** Wraps `createNativeWatcher` (e.g. `onEvent => fs.watch(dir, onEvent)`).
 * While the event rate stays under the threshold, every native event is
 * forwarded to `onEvent` unchanged. Once the rate trips, the native watcher
 * is closed right away and a fresh one is opened after `cooldownMs` of
 * quiet — so a sustained storm keeps the watcher closed (and the OS-level
 * cost gone) instead of reopening straight into the same flood. */
export function guardAgainstWatchStorm(
  createNativeWatcher: (onEvent: (...args: any[]) => void) => NativeWatcher,
  onEvent: (...args: any[]) => void,
  options: StormGuardOptions = {}
): StormGuardedWatch {
  const windowMs = options.windowMs ?? DEFAULT_WINDOW_MS
  const maxEventsPerWindow = options.maxEventsPerWindow ?? DEFAULT_MAX_EVENTS_PER_WINDOW
  const cooldownMs = options.cooldownMs ?? DEFAULT_COOLDOWN_MS
  const timers = options.timers ?? realTimers

  let closed = false
  let tripped = false
  let native: NativeWatcher | null = null
  let windowStart = timers.now()
  let windowCount = 0
  let cooldownTimer: unknown = null

  function openNative() {
    native = createNativeWatcher(handleEvent)
  }

  function closeNative() {
    if (native) {
      native.close()
      native = null
    }
  }

  function handleEvent(...args: any[]) {
    if (closed || tripped) {
      return
    }

    const now = timers.now()

    if (now - windowStart > windowMs) {
      windowStart = now
      windowCount = 0
    }

    windowCount += 1

    if (windowCount > maxEventsPerWindow) {
      tripped = true
      closeNative()

      cooldownTimer = timers.setTimeout(() => {
        cooldownTimer = null
        tripped = false
        windowStart = timers.now()
        windowCount = 0

        if (!closed) {
          openNative()
        }
      }, cooldownMs)

      return
    }

    onEvent(...args)
  }

  openNative()

  return {
    close() {
      closed = true

      if (cooldownTimer !== null) {
        timers.clearTimeout(cooldownTimer)
        cooldownTimer = null
      }

      closeNative()
    }
  }
}
