import { afterEach, describe, expect, it, vi } from 'vitest'

import { $gateway } from '@/store/gateway'

import { handleDesktopBridgeEvent } from './desktop-bridge'
import type { GatewayEventContext } from './types'

function previewActRequestEvent(isActiveEvent: boolean): GatewayEventContext {
  return {
    event: { type: 'preview.act.request' },
    isActiveEvent,
    payload: { action: 'elements', request_id: 'req-1' }
  } as unknown as GatewayEventContext
}

afterEach(() => {
  $gateway.set(null as never)
  vi.clearAllMocks()
})

describe('handleDesktopBridgeEvent preview.act.request', () => {
  // #101016: the request is broadcast to every window that mounts a chat
  // root (main, HUD, session/browser popouts), and each evaluates
  // `isActiveEvent` against its OWN active session. A non-owning window used
  // to answer a refusal immediately, racing ahead of the owning window's
  // async engine load — `_block` on the backend keeps only the first
  // `preview.act.respond`, so the real answer was discarded and the owning
  // window's drive_preview call permanently failed.
  it('stays silent instead of answering a refusal when this window does not own the session', async () => {
    const request = vi.fn()
    $gateway.set({ request } as never)

    const handled = handleDesktopBridgeEvent(previewActRequestEvent(false))

    expect(handled).toBe(true)

    // Flush any microtasks a wrongly-eager answer would have queued.
    await Promise.resolve()
    await Promise.resolve()

    expect(request).not.toHaveBeenCalled()
  })

  it('still answers when this window owns the session', async () => {
    const request = vi.fn()
    $gateway.set({ request } as never)

    const handled = handleDesktopBridgeEvent(previewActRequestEvent(true))

    expect(handled).toBe(true)

    await vi.waitFor(() => expect(request).toHaveBeenCalled())

    const [method, params] = request.mock.calls[0] as [string, { request_id: string; text: string }]

    expect(method).toBe('preview.act.respond')
    expect(params.request_id).toBe('req-1')
  })
})
