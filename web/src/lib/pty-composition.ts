// How long after this module sends composed text that a matching onData
// echo is still treated as the same commit rather than fresh input. Android
// Gboard has been observed to fire onData with the whole just-committed word
// shortly after the fallback timer (or a rapid-composition flush) already
// sent it — comfortably inside one browser task, well under a deliberate
// retype of the same word.
const RECENTLY_SENT_WINDOW_MS = 100;

/**
 * Delays an IME/dead-key commit just long enough for xterm to emit onData.
 *
 * xterm is authoritative when it emits the commit. Browsers/layouts where it
 * does not emit onData still forward the compositionend text on the next turn.
 * Conversely, when this module itself sends composed text (immediate flush
 * or fallback timer), a subsequent onData echo of that same text is a
 * duplicate, not new input — noteTerminalData() reports that to the caller.
 */
export function createPtyCompositionForwarder(send: (data: string) => void) {
  let pending: string | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let matchedTerminalPrefix = "";
  let sawUnrelatedTerminalData = false;
  let recentlySent: string | null = null;
  let recentlySentTimer: ReturnType<typeof setTimeout> | null = null;

  const clearPending = () => {
    pending = null;
    matchedTerminalPrefix = "";
    sawUnrelatedTerminalData = false;
    if (timer) {
      clearTimeout(timer);
      timer = null;
    }
  };

  const clearRecentlySent = () => {
    recentlySent = null;
    if (recentlySentTimer) {
      clearTimeout(recentlySentTimer);
      recentlySentTimer = null;
    }
  };

  const markSent = (text: string) => {
    send(text);
    clearRecentlySent();
    recentlySent = text;
    recentlySentTimer = setTimeout(clearRecentlySent, RECENTLY_SENT_WINDOW_MS);
  };

  return {
    onCompositionEnd(data: string | null) {
      if (!data) return;
      // Some IME/keyboard combinations (observed with Android Gboard) fire
      // compositionend twice for the same commit. Treat a repeat of the
      // still-pending text as the same event, not a second composition.
      if (data === pending) return;
      // Preserve rapid consecutive commits instead of discarding the first.
      const previous = pending;
      clearPending();
      if (previous) markSent(previous);
      pending = data;
      timer = setTimeout(() => {
        const committed = pending;
        clearPending();
        if (committed) markSent(committed);
      }, 16);
    },
    // Returns whether the caller should still forward `data` to the PTY:
    // false means this module already sent it (or is about to) and the
    // caller must not send it again.
    noteTerminalData(data: string): boolean {
      if (data.startsWith("\x1b")) return true;

      if (pending && !sawUnrelatedTerminalData) {
        // xterm may split committed text across callbacks, but only a
        // clean, leading match is authoritative. Once unrelated data
        // arrives, retain the fallback even if later callbacks happen to
        // spell the composition.
        const observed = matchedTerminalPrefix + data;
        if (observed.startsWith(pending)) {
          clearPending();
          return true;
        }
        if (pending.startsWith(observed)) {
          matchedTerminalPrefix = observed;
          return true;
        }
        sawUnrelatedTerminalData = true;
      }

      // xterm's onData can also echo text this module already sent (a
      // rapid-flush or the fallback timer), independently of any pending
      // composition above. Suppress that duplicate exactly once.
      if (recentlySent !== null && data === recentlySent) {
        clearRecentlySent();
        return false;
      }

      return true;
    },
    dispose: () => {
      clearPending();
      clearRecentlySent();
    },
  };
}
