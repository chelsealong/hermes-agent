import { describe, expect, it } from 'vitest'

import { $connection } from '@/store/session'

import { host } from './index'

describe('host.state.localFiles', () => {
  it('is true when there is no connection yet (the local-by-default boot state)', () => {
    $connection.set(null)

    expect(host.state.localFiles.get()).toBe(true)
  })

  it('is true for a local connection and flips to false for a remote one', () => {
    $connection.set({ mode: 'local' } as never)
    expect(host.state.localFiles.get()).toBe(true)

    $connection.set({ mode: 'remote' } as never)
    expect(host.state.localFiles.get()).toBe(false)

    $connection.set(null)
  })
})
