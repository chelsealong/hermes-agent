/**
 * A backend with no `display.*` methods paints the same "unavailable" pane for every
 * connection kind, but a Hermes Cloud instance has no self-update lever a user can act
 * on — the copy must not send them chasing an update that isn't theirs to run (#120852).
 */

import { render } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'

import type { RosterRow } from './types'

vi.mock('@hermes/plugin-sdk', async () => {
  const { useStore } = await import('@nanostores/react')
  const { onGatewayEvent } = await import('../../contrib/events')

  return {
    Button: () => null,
    Codicon: () => null,
    GlyphSpinner: () => null,
    Tip: () => null,
    EmptyState: ({ title, description }: { title: string; description: string }) => (
      <div>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
    ),
    useValue: useStore,
    host: { onEvent: onGatewayEvent }
  }
})
vi.mock('./routing', () => {
  const route = { connectionId: 'host-a', mode: 'remote', profile: 'ops', targetProfile: 'ops' }

  return { botConnectionRoute: () => route, resolveBotConnectionRoute: () => ({ status: 'resolved', route }) }
})
vi.mock('./data', () => ({ botSelectionKey: (bot: RosterRow) => bot.name }))
vi.mock('./i18n', () => ({
  useBots: () => ({
    screen: {
      portalUnavailable: 'Update the bot’s Hermes to use Screen',
      portalUnavailableCloud: 'This Hermes Cloud instance doesn’t support Screen yet',
      unavailableTitle: 'Screen needs a newer Hermes'
    }
  })
}))
vi.mock('./screen-connection', () => ({
  displayRequest: vi.fn(() => Promise.reject(new Error('method not found'))),
  isDisplayUnavailable: () => true,
  isEventForBotScreen: () => false,
  leaseHeldBy: () => false,
  resolveScreenWsUrl: vi.fn(),
  retainBotScreen: () => () => {},
  viewerHash: vi.fn()
}))

import { BotScreenPane } from './screen-pane'
import { $screenState, setScreenUnavailable } from './screen-state'

const remoteBot: RosterRow = { name: 'ops', sourceScoped: true, connectionId: 'host-a', connectionKind: 'remote' }
const cloudBot: RosterRow = { name: 'ops', sourceScoped: true, connectionId: 'host-a', connectionKind: 'cloud' }

beforeEach(() => {
  $screenState.set({})
})

it('tells a remote/local user to update the bot’s Hermes', () => {
  setScreenUnavailable(remoteBot)
  const view = render(<BotScreenPane bot={remoteBot} />)

  expect(view.getByText('Update the bot’s Hermes to use Screen')).toBeTruthy()
  view.unmount()
})

it('does not tell a Hermes Cloud user to update the bot’s Hermes', () => {
  setScreenUnavailable(cloudBot)
  const view = render(<BotScreenPane bot={cloudBot} />)

  expect(view.queryByText('Update the bot’s Hermes to use Screen')).toBeNull()
  expect(view.getByText('This Hermes Cloud instance doesn’t support Screen yet')).toBeTruthy()
  view.unmount()
})
