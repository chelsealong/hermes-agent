import { describe, expect, it } from 'vitest'

import { chunkByLines, exceedsHighlightBudget, isAsciiDiagramLanguage } from '@/components/chat/shiki-highlighter'

describe('exceedsHighlightBudget', () => {
  it('highlights normal-sized blocks', () => {
    expect(exceedsHighlightBudget('const x = 1\n'.repeat(100))).toBe(false)
  })

  it('skips highlighting past the line budget', () => {
    expect(exceedsHighlightBudget('x\n'.repeat(5_000))).toBe(true)
  })

  it('skips highlighting past the char budget on few lines', () => {
    expect(exceedsHighlightBudget('a'.repeat(200_000))).toBe(true)
  })

  it('short-circuits on char budget before line loop', () => {
    expect(exceedsHighlightBudget('y\n'.repeat(250_000))).toBe(true)
  })
})

describe('isAsciiDiagramLanguage', () => {
  it('matches txt and console fences, case- and whitespace-insensitively', () => {
    expect(isAsciiDiagramLanguage('txt')).toBe(true)
    expect(isAsciiDiagramLanguage('console')).toBe(true)
    expect(isAsciiDiagramLanguage('TXT')).toBe(true)
    expect(isAsciiDiagramLanguage(' console ')).toBe(true)
  })

  it('does not match normal source-code or log languages', () => {
    expect(isAsciiDiagramLanguage('python')).toBe(false)
    expect(isAsciiDiagramLanguage('bash')).toBe(false)
    expect(isAsciiDiagramLanguage('json')).toBe(false)
    expect(isAsciiDiagramLanguage(undefined)).toBe(false)
    expect(isAsciiDiagramLanguage('')).toBe(false)
  })
})

describe('chunkByLines', () => {
  it('keeps a small block as a single chunk', () => {
    const code = 'a\nb\nc'
    expect(chunkByLines(code, 200)).toEqual([{ text: code, lines: 3 }])
  })

  it('splits a large block and reconstructs it losslessly', () => {
    const code = Array.from({ length: 1000 }, (_, i) => `line ${i}`).join('\n')
    const chunks = chunkByLines(code, 200)

    expect(chunks).toHaveLength(5)
    expect(chunks.map(chunk => chunk.text).join('\n')).toBe(code)
    expect(chunks.reduce((sum, chunk) => sum + chunk.lines, 0)).toBe(1000)
  })
})
