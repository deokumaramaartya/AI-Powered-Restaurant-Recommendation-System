import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { SummaryBanner } from './SummaryBanner'

describe('SummaryBanner', () => {
  it('shows the fallback notice when source is fallback', () => {
    render(<SummaryBanner source="fallback" summary={null} />)

    expect(screen.getByText('Ranked without AI')).toBeInTheDocument()
    expect(screen.getByText(/sorted by rating and votes/)).toBeInTheDocument()
    expect(screen.queryByLabelText('AI summary')).not.toBeInTheDocument()
  })

  it('shows the LLM summary when source is llm', () => {
    render(<SummaryBanner source="llm" summary="Three strong Italian picks." />)

    expect(screen.getByLabelText('AI summary')).toHaveTextContent('Three strong Italian picks.')
    expect(screen.queryByText('Ranked without AI')).not.toBeInTheDocument()
  })

  it('renders nothing for an llm response with an empty summary', () => {
    const { container } = render(<SummaryBanner source="llm" summary="  " />)
    expect(container).toBeEmptyDOMElement()
  })

  it('renders HTML in the summary as plain text', () => {
    const { container } = render(<SummaryBanner source="llm" summary="<b>bold</b> picks" />)
    expect(screen.getByLabelText('AI summary')).toHaveTextContent('<b>bold</b> picks')
    expect(container.querySelector('b')).toBeNull()
  })
})
