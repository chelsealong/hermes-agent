import { host, type PluginOs, type PluginStorage } from '@hermes/plugin-sdk'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { bindApi } from './api'
import { AttachmentRow } from './drawer'
import type { KanbanAttachment } from './types'

// `AttachmentRow` gates the OS-native actions on `host.state.localFiles`
// (@hermes/plugin-sdk). Stubbing just that atom keeps every other SDK export
// (Button, Codicon, useKanban's i18n plumbing, …) real, matching the plugin
// fence — kanban source never imports `@/…` directly, so the test doesn't
// either. `nanostores` is imported dynamically inside the factory because
// `vi.mock` factories run before the file's own top-level imports settle.
vi.mock('@hermes/plugin-sdk', async importOriginal => {
  const [{ atom }, actual] = await Promise.all([
    import('nanostores'),
    importOriginal<Record<string, unknown>>()
  ])

  const sdkHost = actual.host as { state: Record<string, unknown> }

  return { ...actual, host: { ...sdkHost, state: { ...sdkHost.state, localFiles: atom(true) } } }
})

const setLocalFiles = (value: boolean) => (host.state.localFiles as unknown as { set: (v: boolean) => void }).set(value)

const noopStorage: PluginStorage = { get: (_key, fallback) => fallback, remove: vi.fn(), set: vi.fn() }

const noopSocket = () => () => {}

function bindOs(os: Partial<PluginOs>): () => void {
  return bindApi(async <T,>() => ({}) as T, noopStorage, noopSocket, {
    notify: vi.fn(),
    openExternal: vi.fn().mockResolvedValue(false),
    openPath: vi.fn().mockResolvedValue(false),
    revealPath: vi.fn().mockResolvedValue(false),
    writeClipboard: vi.fn().mockResolvedValue(false),
    ...os
  })
}

const WITH_PATH: KanbanAttachment = { filename: 'notes.md', id: 1, stored_path: '/tmp/kanban/notes.md' }
const WITHOUT_PATH: KanbanAttachment = { filename: 'old.txt', id: 2 }

afterEach(() => {
  cleanup()
  setLocalFiles(true)
})

describe('AttachmentRow', () => {
  it('renders the original static row when the backend predates stored_path', () => {
    const dispose = bindOs({})

    render(<AttachmentRow attachment={WITHOUT_PATH} />)

    expect(screen.getByText('old.txt')).toBeTruthy()
    expect(screen.queryAllByRole('button')).toHaveLength(0)

    dispose()
  })

  it('opens the attachment with the OS default app when the filename is clicked locally', () => {
    const openPath = vi.fn().mockResolvedValue(true)
    const dispose = bindOs({ openPath })

    render(<AttachmentRow attachment={WITH_PATH} />)
    fireEvent.click(screen.getByRole('button', { name: 'notes.md' }))

    expect(openPath).toHaveBeenCalledWith('/tmp/kanban/notes.md')

    dispose()
  })

  it('reveals the attachment in the file manager', () => {
    const revealPath = vi.fn().mockResolvedValue(true)
    const dispose = bindOs({ revealPath })

    render(<AttachmentRow attachment={WITH_PATH} />)
    fireEvent.click(screen.getByRole('button', { name: 'revealAttachment' }))

    expect(revealPath).toHaveBeenCalledWith('/tmp/kanban/notes.md')

    dispose()
  })

  it('hides Reveal and routes the filename click to in-app preview instead of the OS shell on a remote backend', () => {
    setLocalFiles(false)
    const openPath = vi.fn()
    const dispose = bindOs({ openPath })

    render(<AttachmentRow attachment={WITH_PATH} />)

    expect(screen.queryByRole('button', { name: 'revealAttachment' })).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'notes.md' }))

    expect(openPath).not.toHaveBeenCalled()

    dispose()
  })
})
