import { describe, expect, it } from 'vitest'

import { $expandAsciiDiagrams, applyExpandAsciiDiagramsFromConfig } from './code-block-prefs'

describe('applyExpandAsciiDiagramsFromConfig', () => {
  it('defaults to false when the key is absent (backend default applies)', () => {
    applyExpandAsciiDiagramsFromConfig({ display: {} })
    expect($expandAsciiDiagrams.get()).toBe(false)

    applyExpandAsciiDiagramsFromConfig(null)
    expect($expandAsciiDiagrams.get()).toBe(false)
  })

  it('picks up the configured value when enabled', () => {
    applyExpandAsciiDiagramsFromConfig({ display: { code_blocks: { expand_ascii_diagrams: true } } })
    expect($expandAsciiDiagrams.get()).toBe(true)
  })

  it('coerces truthy/falsy non-boolean values like the backend does', () => {
    applyExpandAsciiDiagramsFromConfig({ display: { code_blocks: { expand_ascii_diagrams: 'true' } } })
    expect($expandAsciiDiagrams.get()).toBe(true)

    applyExpandAsciiDiagramsFromConfig({ display: { code_blocks: { expand_ascii_diagrams: '' } } })
    expect($expandAsciiDiagrams.get()).toBe(false)
  })
})
