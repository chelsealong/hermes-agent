// @vitest-environment jsdom
import { AssistantRuntimeProvider, useExternalStoreRuntime } from '@assistant-ui/react'
import type { ThreadMessageLike } from '@assistant-ui/react'
import { act, cleanup, fireEvent, render } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n'
import { mainComposerScope } from '@/store/composer'
import { $convertLargePastesToAttachment, setConvertLargePastesToAttachment } from '@/store/large-paste-attachment'

import { LARGE_PASTE_ATTACHMENT_THRESHOLD } from './large-paste'
import { composerPlainText, RICH_INPUT_SLOT } from './rich-editor'
import type { ChatBarState } from './types'

import { ChatBar } from './index'

afterEach(cleanup)

// THE INVARIANT: "convert large pastes to attachments" off means a large
// paste always lands inline, exactly like a small one. #117159 asked for a
// way to opt out of the automatic .txt-attachment conversion so a deliberately
// pasted large block of prompt text stays part of the message.
const LARGE_TEXT = 'a'.repeat(LARGE_PASTE_ATTACHMENT_THRESHOLD + 1)

const state: ChatBarState = {
  model: { canSwitch: false, model: '', provider: '' },
  tools: { enabled: false, label: '' },
  voice: { enabled: false, active: false }
}

function Harness({ onAttachPastedText }: { onAttachPastedText: (text: string) => Promise<boolean> | boolean }) {
  const runtime = useExternalStoreRuntime({
    convertMessage: (message: ThreadMessageLike) => message,
    isRunning: false,
    messages: [] as ThreadMessageLike[],
    onNew: async () => {}
  })

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <MemoryRouter>
        <I18nProvider configClient={null} initialLocale="en">
          <ChatBar
            busy={false}
            disabled={false}
            gateway={null}
            onAttachPastedText={onAttachPastedText}
            onCancel={vi.fn()}
            onSubmit={vi.fn(async () => true)}
            state={state}
          />
        </I18nProvider>
      </MemoryRouter>
    </AssistantRuntimeProvider>
  )
}

function pasteInto(editor: HTMLElement, text: string) {
  Object.defineProperty(editor, 'isContentEditable', { configurable: true, value: true })
  editor.focus()

  const event = new Event('paste', { bubbles: true, cancelable: true }) as ClipboardEvent

  Object.defineProperty(event, 'clipboardData', {
    value: {
      getData: (type: string) => (type === 'text' || type === 'text/plain' ? text : ''),
      files: [],
      items: []
    }
  })

  act(() => {
    fireEvent(editor, event)
  })

  return event
}

describe('large-paste-to-attachment toggle', () => {
  afterEach(() => {
    mainComposerScope.clear()
    setConvertLargePastesToAttachment(true)
  })

  it('attaches a large paste when the setting is on (default)', () => {
    expect($convertLargePastesToAttachment.get()).toBe(true)

    const onAttachPastedText = vi.fn(() => true)
    const { container } = render(<Harness onAttachPastedText={onAttachPastedText} />)
    const editor = container.querySelector<HTMLElement>(`[data-slot="${RICH_INPUT_SLOT}"]`)!

    pasteInto(editor, LARGE_TEXT)

    expect(onAttachPastedText).toHaveBeenCalledWith(LARGE_TEXT)
    expect(composerPlainText(editor)).not.toContain(LARGE_TEXT)
  })

  it('keeps a large paste inline and skips the attachment when the setting is off', () => {
    setConvertLargePastesToAttachment(false)

    const onAttachPastedText = vi.fn(() => true)
    const { container } = render(<Harness onAttachPastedText={onAttachPastedText} />)
    const editor = container.querySelector<HTMLElement>(`[data-slot="${RICH_INPUT_SLOT}"]`)!

    pasteInto(editor, LARGE_TEXT)

    expect(onAttachPastedText).not.toHaveBeenCalled()
    expect(composerPlainText(editor)).toContain(LARGE_TEXT)
  })
})
