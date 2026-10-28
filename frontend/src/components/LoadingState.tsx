interface Props {
  location: string
  count?: number
}

export function LoadingState({ location, count = 3 }: Props) {
  return (
    <div className="loading" aria-busy="true" aria-label="Loading results">
      <div className="skeleton skeleton--heading" aria-hidden="true" />
      <div className="skeleton skeleton--banner" aria-hidden="true" />
      {Array.from({ length: count }, (_, index) => (
        <div key={index} className="card card--skeleton" aria-hidden="true" data-testid="skeleton-card">
          <div className="card__stub" />
          <div className="card__body">
            <div className="skeleton skeleton--name" />
            <div className="skeleton skeleton--line" />
            <div className="skeleton skeleton--why" />
          </div>
        </div>
      ))}
      <p className="loading__note">Checking menus across {location}…</p>
    </div>
  )
}
