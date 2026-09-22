import { act, cleanup } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { createClientSessionState } from '@/lib/chat-runtime'

import { type MessageStreamHarness, renderMessageStream } from './test-harness'

const SID = 'session-1'

let stream: MessageStreamHarness

const start = () => act(() => stream.handleEvent({ payload: {}, session_id: SID, type: 'message.start' }))

const reasoningDelta = (text: string) =>
  act(() => stream.handleEvent({ payload: { text }, session_id: SID, type: 'reasoning.delta' }))

const complete = (text: string) =>
  act(() => stream.handleEvent({ payload: { text }, session_id: SID, type: 'message.complete' }))

describe('useMessageStream hydration gate on reasoning-only turns', () => {
  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it('keeps the streamed reasoning visible instead of hydrating a blank DB row (#118755)', async () => {
    // A reasoning-only provider (local Qwen via llama.cpp, thinking mode on)
    // puts the whole answer in reasoning_content and leaves the terminal
    // frame's `content` empty. The window demonstrably saw assistant payload
    // this turn (the reasoning deltas), so re-hydrating from stored history
    // must not replace the live bubble with the empty DB row.
    const hydrateFromStoredSession = vi.fn<() => Promise<void>>(async () => undefined)

    stream = renderMessageStream(SID, {
      hydrateFromStoredSession,
      states: new Map([[SID, createClientSessionState('stored-session-1')]])
    })

    await start()
    await reasoningDelta('the user wants...')
    await complete('')

    expect(hydrateFromStoredSession).not.toHaveBeenCalled()
    expect(stream.reasoningText()).toBe('the user wants...')
  })

  it('still hydrates an empty completion when the window never saw any assistant payload (#88036)', async () => {
    const hydrateFromStoredSession = vi.fn<() => Promise<void>>(async () => undefined)

    stream = renderMessageStream(SID, {
      hydrateFromStoredSession,
      states: new Map([[SID, createClientSessionState('stored-session-1')]])
    })

    await start()
    await complete('')

    expect(hydrateFromStoredSession).toHaveBeenCalledWith(3, 'stored-session-1', SID)
  })
})
