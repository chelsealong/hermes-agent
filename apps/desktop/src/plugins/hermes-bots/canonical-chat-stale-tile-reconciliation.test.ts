/**
 * A Bot Chat compression can leave two tabs captioned "Bot Chat" (hermes-agent#120810).
 *
 * The guard that fronts an already-open tile as the SAME conversation
 * (`intent: 'in-place'`) matches through sidebar lineage metadata that a
 * hidden canonical Bot Chat never reaches — so a tile left over from before a
 * compression rotation is invisible to it. The roster click already runs a
 * stale-tile probe first (`focusExistingBotTab`) and discards such a tile
 * before opening, but the two opens that skip that click — the
 * roster-activity refresh and `session.reclaimed` — call
 * `openBotCanonicalChat` directly, so they never ran the probe.
 *
 * This pins `openStoredBotChat` running that same probe itself, against the
 * registry id and resolved tip it is about to open, so every open path
 * reconciles a stale tile rather than only the click path.
 */

import { beforeEach, expect, it, vi } from 'vitest'

const { hostMock, requestForBotMock } = vi.hoisted(() => ({
  hostMock: {
    focusOpenWorkspaceSession: vi.fn(
      (_ownerKey: string, _isStale: (tile: { storedSessionId: string; workspaceTabTitle?: string }) => boolean, _canonicalIds: readonly string[]) => null as null | string
    ),
    openSession: vi.fn(),
    request: vi.fn()
  },
  requestForBotMock: vi.fn()
}))

vi.mock('@hermes/plugin-sdk', () => ({
  BOT_CHAT_SESSION_HYDRATION_TIMEOUT_MS: 15_000,
  host: hostMock
}))

vi.mock('./routing', () => ({
  backendTargetProfile: (route: { targetProfile?: string } | null, name: string) => route?.targetProfile ?? name,
  botConnectionRoute: () => null,
  botRosterMeta: () => ({}),
  botWorkspaceOwnerKey: (bot: { name?: string } | null) => `bot:${bot?.name || ''}`,
  requestForBot: requestForBotMock
}))

vi.mock('./data', () => ({
  $botMeta: { get: () => ({}), set: vi.fn() },
  botMetaKey: (bot: { name?: string }) => bot?.name ?? '',
  botOwner: (owner: string) => ({ bot: { name: owner }, key: owner, name: owner, route: null }),
  persistBotMetaSnapshot: vi.fn(),
  saveBotMeta: vi.fn()
}))

vi.mock('./shared', () => ({ getPluginCtx: () => null }))

async function loadModule() {
  vi.resetModules()

  return import('./canonical-chat')
}

beforeEach(() => {
  vi.clearAllMocks()
  hostMock.openSession.mockResolvedValue(undefined)
  hostMock.focusOpenWorkspaceSession.mockReturnValue(null)
})

it('reconciles a stale tile against the registry id and tip before opening', async () => {
  requestForBotMock.mockImplementation(async (_bot: unknown, method: string) =>
    method === 'session.list'
      ? { sessions: [{ id: 'root-1', message_count: 40, resolved_id: 'tip-9', root_title: 'Bot Chat' }] }
      : {}
  )

  const { openBotCanonicalChat } = await loadModule()
  await openBotCanonicalChat('ops')

  expect(hostMock.focusOpenWorkspaceSession).toHaveBeenCalledTimes(1)
  const [ownerKey, isStale, canonicalIds] = hostMock.focusOpenWorkspaceSession.mock.calls[0]

  expect(ownerKey).toBe('bot:ops')
  expect(canonicalIds).toEqual(expect.arrayContaining(['root-1', 'tip-9']))

  // A tile left over from before the compression rotation: titled "Bot Chat",
  // but at neither the registry root nor the new tip.
  expect(isStale({ storedSessionId: 'pre-compression-tip', workspaceTabTitle: 'Bot Chat' })).toBe(true)
  // The tile the caller is about to open (or already open at) is never stale.
  expect(isStale({ storedSessionId: 'tip-9', workspaceTabTitle: 'Bot Chat' })).toBe(false)
  // A side thread the user opened with `+` is never touched by this probe.
  expect(isStale({ storedSessionId: 'side-thread', workspaceTabTitle: 'a side chat' })).toBe(false)

  // The reconciliation runs before the open that would otherwise mint (or
  // front) a second tab beside the undiscarded stale one.
  expect(hostMock.focusOpenWorkspaceSession.mock.invocationCallOrder[0]).toBeLessThan(
    hostMock.openSession.mock.invocationCallOrder[0]
  )
})

it('reconciles on the background-refresh path too, not only the first-ever open', async () => {
  // Simulates the roster-activity refresh / session.reclaimed callers, which
  // (unlike a roster click) never ran focusExistingBotTab's own probe first.
  requestForBotMock.mockImplementation(async (_bot: unknown, method: string) =>
    method === 'session.list' ? { sessions: [{ id: 'forever-chat', message_count: 5, title: 'Bot Chat' }] } : {}
  )

  const { openBotCanonicalChat } = await loadModule()
  await openBotCanonicalChat('ops')
  await openBotCanonicalChat('ops')

  expect(hostMock.focusOpenWorkspaceSession).toHaveBeenCalledTimes(2)
})
