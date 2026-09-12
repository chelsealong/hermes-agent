import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { atom } from 'nanostores'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { SidebarWorkspaceGroup } from './workspace-group'
import { DEFAULT_BRANCH_LABEL, type SidebarSessionGroup } from './workspace-groups'

const mockSwitchBranchInRepo = vi.fn()

afterEach(cleanup)
beforeEach(() => mockSwitchBranchInRepo.mockReset())

vi.mock('@/i18n', () => ({
  useI18n: () => ({
    t: {
      profiles: {
        switchToProfile: (name: string) => `Switch to ${name}`
      },
      sidebar: {
        newSessionIn: (label: string) => `New session in ${label}`,
        noSessions: 'No sessions yet',
        projects: {
          copyPath: 'Copy path',
          menu: 'Project actions',
          reveal: 'Reveal',
          removeWorktree: 'Remove worktree',
          toggle: (label: string, open: boolean) => `${open ? 'Show' : 'Hide'} ${label} sessions`
        }
      },
      statusStack: {
        coding: {
          switchFailed: (branch: string) => `Could not switch to ${branch}`
        }
      }
    }
  })
}))

vi.mock('./model', () => ({
  PROJECT_PREVIEW_COUNT: 3,
  SIDEBAR_GROUP_PAGE: 10,
  useWorkspaceNodeOpen: () => [true, vi.fn()]
}))

vi.mock('@/store/projects', () => ({
  copyPath: vi.fn(),
  revealPath: vi.fn(),
  switchBranchInRepo: (...args: unknown[]) => mockSwitchBranchInRepo(...args)
}))

vi.mock('@/store/coding-status', () => ({
  openWorktreeDialog: vi.fn()
}))

vi.mock('@/store/layout', () => ({
  $sidebarRowMeta: atom({}),
  setWorkspaceNodeOpen: vi.fn()
}))

vi.mock('@/store/notifications', () => ({
  notifyError: vi.fn()
}))

vi.mock('@/store/profile', () => ({
  newSessionInProfile: vi.fn(),
  pinNewChatProfile: vi.fn(),
  selectProfile: vi.fn()
}))

vi.mock('@/store/session', () => ({
  $sessionProfilesUsage: atom({})
}))

vi.mock('@/store/sidebar-sort', () => ({
  $sidebarSessionRankIds: atom({})
}))

const baseGroup: SidebarSessionGroup = {
  id: '/repo::branch::master',
  isMain: true,
  label: 'master',
  path: '/repo',
  sessions: []
}

describe('SidebarWorkspaceGroup — main-checkout "+" branch switch', () => {
  it('switches to a real recorded branch before opening a new session', async () => {
    mockSwitchBranchInRepo.mockResolvedValue(undefined)
    const onNewSession = vi.fn()

    render(<SidebarWorkspaceGroup group={baseGroup} onNewSession={onNewSession} renderRows={() => null} />)

    fireEvent.click(screen.getByRole('button', { name: 'New session in master' }))

    await waitFor(() => {
      expect(onNewSession).toHaveBeenCalledWith('/repo')
    })
    expect(mockSwitchBranchInRepo).toHaveBeenCalledWith('/repo', 'master')
  })

  it('does not switch when the label is only the unrecorded-branch fallback', async () => {
    mockSwitchBranchInRepo.mockResolvedValue(undefined)
    const onNewSession = vi.fn()

    const fallbackGroup: SidebarSessionGroup = {
      ...baseGroup,
      id: `/repo::branch::${DEFAULT_BRANCH_LABEL}`,
      label: DEFAULT_BRANCH_LABEL
    }

    render(<SidebarWorkspaceGroup group={fallbackGroup} onNewSession={onNewSession} renderRows={() => null} />)

    fireEvent.click(screen.getByRole('button', { name: `New session in ${DEFAULT_BRANCH_LABEL}` }))

    await waitFor(() => {
      expect(onNewSession).toHaveBeenCalledWith('/repo')
    })
    expect(mockSwitchBranchInRepo).not.toHaveBeenCalled()
  })
})
