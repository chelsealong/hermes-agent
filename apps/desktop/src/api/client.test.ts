import { afterEach, describe, expect, it, vi } from 'vitest'

import { HermesGateway } from './client'

interface ListenerEntry {
  callback: (event: any) => void
  once: boolean
}

class FakeSocket {
  static readonly CLOSED = 3
  static readonly OPEN = 1

  readonly sent: string[] = []
  readyState = FakeSocket.OPEN
  private listeners = new Map<string, ListenerEntry[]>()

  addEventListener(type: string, callback: (event: any) => void, options?: AddEventListenerOptions): void {
    const entries = this.listeners.get(type) ?? []

    entries.push({ callback, once: Boolean(options?.once) })
    this.listeners.set(type, entries)
  }

  close(): void {
    if (this.readyState === FakeSocket.CLOSED) {
      return
    }

    this.readyState = FakeSocket.CLOSED
    this.emit('close', { code: 1000 })
  }

  emit(type: string, event: any = {}): void {
    const entries = [...(this.listeners.get(type) ?? [])]

    for (const entry of entries) {
      entry.callback(event)

      if (entry.once) {
        this.removeEventListener(type, entry.callback)
      }
    }
  }

  message(frame: unknown): void {
    this.emit('message', { data: JSON.stringify(frame) })
  }

  removeEventListener(type: string, callback: (event: any) => void): void {
    this.listeners.set(
      type,
      (this.listeners.get(type) ?? []).filter(entry => entry.callback !== callback)
    )
  }

  send(payload: string): void {
    this.sent.push(payload)
  }
}

describe('HermesGateway heartbeat deadline', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.useRealTimers()
  })

  it('survives a 120s compaction stall that produces no inbound frames', async () => {
    vi.useFakeTimers()
    const socket = new FakeSocket()
    vi.stubGlobal(
      'WebSocket',
      Object.assign(
        function (this: unknown) {
          return socket
        },
        { OPEN: FakeSocket.OPEN }
      )
    )

    const gateway = new HermesGateway()
    const connected = gateway.connect('ws://gateway.test/api/ws')

    socket.emit('open')
    await connected
    socket.message({
      jsonrpc: '2.0',
      method: 'event',
      params: { type: 'gateway.ready', payload: { heartbeat: true } }
    })

    // Preflight compaction on a large session keeps the backend busy for
    // 90-120s (#108325) with no inbound frame at all, ping included. The
    // connection must still be alive when the backend resumes.
    await vi.advanceTimersByTimeAsync(120_000)

    expect(gateway.connectionState).toBe('open')
    expect(socket.readyState).toBe(FakeSocket.OPEN)
  })
})
