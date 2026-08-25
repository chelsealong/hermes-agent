import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { DescriptionSection } from './drawer'

afterEach(() => {
  cleanup()
})

// #94525 — the description and comment bodies rendered as inert plain text,
// so a URL a bot or teammate left on a card was never clickable.
describe('DescriptionSection', () => {
  it('renders a URL in the body as a clickable link', () => {
    render(<DescriptionSection body="see https://example.com/docs for the design" onSave={vi.fn()} />)

    const link = screen.getByRole('link', { name: /example\.com\/docs/ })

    expect(link.getAttribute('href')).toBe('https://example.com/docs')
  })

  it('leaves file-shaped tokens alone', () => {
    render(<DescriptionSection body="rerun build.log after editing config.yaml" onSave={vi.fn()} />)

    expect(screen.queryByRole('link')).toBeNull()
    expect(screen.getByText(/rerun build\.log after editing config\.yaml/)).not.toBeNull()
  })
})
