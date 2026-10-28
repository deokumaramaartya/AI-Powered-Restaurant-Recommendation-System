import type { CSSProperties } from 'react'
import { formatCostForTwo, formatNumber, formatRank, formatRating } from '../format'
import type { Recommendation } from '../types'

interface Props {
  recommendation: Recommendation
}

function Service({ label, available }: { label: string; available: boolean }) {
  return (
    <li className={available ? 'pill pill--on' : 'pill pill--off'}>
      {available ? null : <span className="visually-hidden">No </span>}
      {label}
    </li>
  )
}

export function RestaurantCard({ recommendation }: Props) {
  const { rank, restaurant, explanation } = recommendation
  const headingId = `restaurant-${restaurant.id}`
  const place = restaurant.address || restaurant.location
  const hasVotes = restaurant.votes > 0
  const rating = formatRating(restaurant.rating)

  return (
    <article className="card" aria-labelledby={headingId} style={{ '--i': rank - 1 } as CSSProperties}>
      <div className="card__stub" aria-label={`Rank ${rank}`}>
        <span className="card__stub-label" aria-hidden="true">
          Rank
        </span>
        <span className="card__rank">{formatRank(rank)}</span>
      </div>

      <div className="card__body">
        <header className="card__header">
          <div className="card__title">
            <h3 id={headingId} className="card__name">
              {restaurant.name}
            </h3>
            <p className="card__cuisines" data-testid="cuisines">
              {restaurant.cuisines.join(' · ')}
            </p>
          </div>
          <div className="card__score">
            <p className="card__rating" aria-label={`Rated ${rating} out of 5`}>
              {rating}
              <span className="card__rating-scale">/5</span>
            </p>
            {hasVotes ? (
              <p className="card__votes" data-testid="votes">
                {formatNumber(restaurant.votes)} votes
              </p>
            ) : null}
          </div>
        </header>

        <div className="card__facts">
          <p className="pill pill--strong" data-testid="cost">
            {formatCostForTwo(restaurant.cost_for_two)}
          </p>
          {restaurant.rest_type ? (
            <p className="pill" data-testid="rest-type">
              {restaurant.rest_type}
            </p>
          ) : null}
          <ul className="card__services" aria-label="Services">
            <Service label="Online order" available={restaurant.online_order} />
            <Service label="Table booking" available={restaurant.book_table} />
          </ul>
        </div>

        <div className="card__why">
          <p className="card__why-label">Why it fits</p>
          <p className="card__explanation" data-testid="explanation">
            {explanation}
          </p>
        </div>

        {place ? (
          <p className="card__address">
            <span className="card__address-label">Address · </span>
            {place}
          </p>
        ) : null}
      </div>
    </article>
  )
}
