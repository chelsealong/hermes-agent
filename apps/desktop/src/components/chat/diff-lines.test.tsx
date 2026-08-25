import { act, cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

// Regression coverage for #93479: a failed dynamic import of the lazily-loaded
// Shiki diff module (packaged asar/asar.unpacked path mismatch, or any other
// fetch failure) rejects the `React.lazy()` promise. React.Suspense only
// covers the *pending* state, so the rejection throws past it to the nearest
// error boundary — which in production is the whole workspace `ContribBoundary`
// — and blanks the transcript instead of degrading to the plain colored diff.
vi.mock('./syntax-diff', () => {
  throw new Error(
    'Failed to fetch dynamically imported module: file:///Hermes.app/Contents/Resources/app.asar/dist/assets/syntax-diff-Bo0962zh.js'
  )
})

import { ErrorBoundary } from '@/components/error-boundary'

import { FileDiffPanel } from './diff-lines'

afterEach(cleanup)

const DIFF = [
  'diff --git a/file.ts b/file.ts',
  '--- a/file.ts',
  '+++ b/file.ts',
  '@@ -1,2 +1,2 @@',
  ' const a = 1',
  '-const b = 2',
  '+const b = 3'
].join('\n')

const WORKSPACE_FALLBACK_TEXT = 'workspace failed to render'

// The failure surfaces only from console.error noise, not from the assertion.
function renderQuietly(node: Parameters<typeof render>[0]) {
  const spy = vi.spyOn(console, 'error').mockImplementation(() => undefined)

  try {
    return render(node)
  } finally {
    spy.mockRestore()
  }
}

describe('FileDiffPanel survives a failed lazy syntax-diff chunk', () => {
  it('degrades to the plain colored diff instead of taking down the surrounding boundary', async () => {
    // The throwing `vi.mock` factory above is re-invoked by Vitest's own mock
    // resolution for every caller that resolves `./syntax-diff` (it never
    // caches a rejection), so a copy of this rejection can settle outside the
    // one that `React.lazy`/`ErrorBoundary` here consumes. Left unhandled,
    // that copy surfaces later as an unhandled rejection attributed to
    // whichever unrelated test file happens to be running at the time
    // (#94415). Absorb it at the process level for the life of this test, the
    // same way profile-routing.test.ts does for its own late-rejection case.
    const unhandled: unknown[] = []
    const onUnhandled = (reason: unknown) => unhandled.push(reason)
    const existing = process.listeners('unhandledRejection')

    for (const listener of existing) {
      process.off('unhandledRejection', listener)
    }

    process.on('unhandledRejection', onUnhandled)

    try {
      const { container } = renderQuietly(
        <ErrorBoundary fallback={() => <div>{WORKSPACE_FALLBACK_TEXT}</div>} label="workspace">
          <FileDiffPanel diff={DIFF} path="file.ts" />
        </ErrorBoundary>
      )

      // The rejection settles a tick after the initial Suspense-pending render
      // (which coincidentally shows the same plain text already) — give it
      // real time to propagate before asserting nothing regressed.
      await act(() => new Promise(resolve => setTimeout(resolve, 300)))

      expect(container.textContent).toContain('const a = 1')
      expect(container.textContent).toContain('const b = 2')
      expect(container.textContent).toContain('const b = 3')
      expect(container.textContent).not.toContain(WORKSPACE_FALLBACK_TEXT)

      // Vitest's mock resolution doesn't cache a rejection, so a second,
      // later resolution of this same mocked specifier — exactly what
      // happens internally under CI's cross-file scheduling — re-invokes the
      // throwing factory and produces its own, unconsumed rejection. Force
      // that second resolution here (deliberately unhandled) so this test
      // fails without the guard below instead of only flaking in CI.
      void import('./syntax-diff')

      await new Promise(resolve => setTimeout(resolve, 200))
    } finally {
      process.off('unhandledRejection', onUnhandled)

      for (const listener of existing) {
        process.on('unhandledRejection', listener)
      }
    }

    for (const reason of unhandled) {
      // Vitest wraps a throwing mock factory in its own "There was an error
      // when mocking a module" error, with the real message on `.cause`.
      const message = `${reason instanceof Error ? reason.message : reason} ${
        reason instanceof Error && reason.cause instanceof Error ? reason.cause.message : ''
      }`

      if (!message.includes('Failed to fetch dynamically imported module')) {
        throw reason
      }
    }
  })
})
