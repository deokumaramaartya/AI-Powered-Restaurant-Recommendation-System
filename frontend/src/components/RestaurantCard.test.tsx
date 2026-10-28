import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { makeRecommendation } from '../test/fixtures'
import { RestaurantCard } from './RestaurantCard'

describe('RestaurantCard', () => {
  it('renders the five required fields', () => {
    const recommendation = makeRecommendation(1, {
      name: 'Example Trattoria',
      cuisines: ['Italian', 'Continental'],
      rating: 4.4,
      cost_for_two: 1100,
    }, 'Rated 4.4 from over 2,000 votes and serves Italian within your budget.')
    render(<RestaurantCard recommendation={recommendation} />)

    expect(screen.getByRole('heading', { name: 'Example Trattoria' })).toBeInTheDocument()
    expect(screen.getByTestId('cuisines')).toHaveTextContent('Italian · Continental')
    expect(screen.getByLabelText('Rated 4.4 out of 5')).toHaveTextContent('4.4')
    expect(screen.getByTestId('cost')).toHaveTextContent('₹1,100 for two')
    expect(screen.getByTestId('explanation')).toHaveTextContent(
      'Rated 4.4 from over 2,000 votes and serves Italian within your budget.',
    )
  })

  it('shows the rank, restaurant type, address, and service badges', () => {
    render(<RestaurantCard recommendation={makeRecommendation(2)} />)

    expect(screen.getByLabelText('Rank 2')).toHaveTextContent('02')
    expect(screen.getByTestId('rest-type')).toHaveTextContent('Casual Dining')
    expect(screen.getByText('100 Feet Road, Indiranagar, Bangalore')).toBeInTheDocument()
    const badges = within(screen.getByRole('list', { name: 'Services' }))
    expect(badges.getByText('Online order')).toBeInTheDocument()
    expect(badges.getByText('Table booking')).toBeInTheDocument()
  })

  it('always shows the rating with one decimal place', () => {
    render(<RestaurantCard recommendation={makeRecommendation(1, { rating: 4 })} />)
    expect(screen.getByLabelText('Rated 4.0 out of 5')).toHaveTextContent('4.0')
  })

  it('formats cost with the rupee symbol and Indian digit grouping', () => {
    const { rerender } = render(<RestaurantCard recommendation={makeRecommendation(1, { cost_for_two: 1200 })} />)
    expect(screen.getByTestId('cost')).toHaveTextContent('₹1,200 for two')

    rerender(<RestaurantCard recommendation={makeRecommendation(1, { cost_for_two: 120000 })} />)
    expect(screen.getByTestId('cost')).toHaveTextContent('₹1,20,000 for two')
  })

  it('hides optional fields that are missing instead of printing null', () => {
    render(<RestaurantCard recommendation={makeRecommendation(1, { rest_type: null, votes: 0 })} />)

    expect(screen.queryByTestId('rest-type')).not.toBeInTheDocument()
    expect(screen.queryByTestId('votes')).not.toBeInTheDocument()
    expect(document.body).not.toHaveTextContent(/null|undefined/)
  })

  it('marks a service that is not offered', () => {
    render(<RestaurantCard recommendation={makeRecommendation(1, { online_order: false, book_table: true })} />)
    const services = within(screen.getByRole('list', { name: 'Services' })).getAllByRole('listitem')

    expect(services.map((item) => item.textContent)).toEqual(['No Online order', 'Table booking'])
    expect(services[0]).toHaveClass('pill--off')
    expect(services[1]).toHaveClass('pill--on')
  })

  it('pads the rank to two digits', () => {
    const { rerender } = render(<RestaurantCard recommendation={makeRecommendation(3)} />)
    expect(screen.getByLabelText('Rank 3')).toHaveTextContent('03')

    rerender(<RestaurantCard recommendation={makeRecommendation(10)} />)
    expect(screen.getByLabelText('Rank 10')).toHaveTextContent(/10$/)
  })

  it('renders HTML in names and explanations as plain text', () => {
    const name = '<img src=x onerror="alert(1)">Cafe'
    const explanation = '<script>alert("x")</script> Great coffee.'
    const { container } = render(<RestaurantCard recommendation={makeRecommendation(1, { name }, explanation)} />)

    expect(screen.getByRole('heading')).toHaveTextContent(name)
    expect(screen.getByTestId('explanation')).toHaveTextContent(explanation)
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('script')).toBeNull()
  })
})
