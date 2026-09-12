import { cleanup, fireEvent, render } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { registry } from '@/contrib/registry'
import { $paneStates } from '@/store/panes'

import { group, split, type SplitNode } from '../model'
import { $hiddenTreePanes, $layoutTree } from '../store'

import { TreeSplit } from './tree-split'

class TestResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

const disposers: (() => void)[] = []

beforeAll(() => {
  vi.stubGlobal('ResizeObserver', TestResizeObserver)
  vi.stubGlobal('CSS', { ...globalThis.CSS, escape: (value: string) => value })

  // jsdom does no layout, so both the container's rect and the probe
  // resolveCssPx measures a declared width with report 0 unless something
  // derives a rect from inline pixel styles instead.
  Element.prototype.getBoundingClientRect = function (this: HTMLElement) {
    const width = Number.parseFloat(this.style.width) || 0
    const height = Number.parseFloat(this.style.height) || 0

    return { bottom: height, height, left: 0, right: width, toJSON: () => ({}), top: 0, width, x: 0, y: 0 } as DOMRect
  }
})

beforeEach(() => {
  window.localStorage.clear()
  $hiddenTreePanes.set(new Set())
  $paneStates.set({})

  disposers.push(
    registry.register({ area: 'panes', data: { placement: 'main' }, id: 'chat', render: () => null, title: 'Chat' }),
    // A mixed main-plus-sized stack: 'browser' declares a width but shares its
    // zone with the width-less 'chat-b', so the ZONE stays flex-at-heart
    // (fixedTrackSize returns null) even though one tenant declares a size.
    registry.register({
      area: 'panes',
      data: { placement: 'main' },
      id: 'chat-b',
      render: () => null,
      title: 'Chat B'
    }),
    registry.register({
      area: 'panes',
      data: { placement: 'right', width: '150px' },
      id: 'browser',
      render: () => null,
      title: 'Browser'
    }),
    registry.register({ area: 'panes', data: { placement: 'main' }, id: 'files', render: () => null, title: 'Files' })
  )
})

afterEach(() => {
  cleanup()
  $layoutTree.set(null)
  $paneStates.set({})
  disposers.splice(0).forEach(dispose => dispose())
})

function row(): SplitNode {
  const tree = $layoutTree.get()

  if (!tree || tree.type !== 'split') {
    throw new Error('expected root row split')
  }

  return tree
}

describe('TreeSplit double-click sash', () => {
  it('equalizes every flex track in the split instead of pinning to a tenant declared width', () => {
    const tree = split(
      'row',
      [
        group(['chat'], { id: 'chat-zone' }),
        group(['chat-b', 'browser'], { id: 'mixed-zone' }),
        group(['files'], { id: 'files-zone' })
      ],
      [3, 1, 2],
      'root-row'
    )

    $layoutTree.set(tree)

    render(<TreeSplit node={tree} root rootRow />)

    const container = document.querySelector<HTMLElement>('[data-tree-split="root-row"]')!
    container.style.width = '800px'

    // The sash between chat-zone (index 0) and mixed-zone (index 1).
    const sash = document.querySelectorAll('[role="separator"]')[0]!
    fireEvent.doubleClick(sash)

    // Distributing evenly means every flex track lands on the same weight —
    // not "the clicked seam's neighbor re-derives its weight from a tenant's
    // declared width while the third, untouched zone keeps its old weight."
    expect(row().weights).toEqual([1, 1, 1])
  })
})
