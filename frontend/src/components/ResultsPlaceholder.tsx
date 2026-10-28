export function IdleState() {
  return (
    <div className="state state--idle">
      <p className="state__eyebrow">Tonight’s chit · empty</p>
      <h2 className="state__title state__title--large">Your shortlist shows up here</h2>
      <p className="state__body">
        Choose a locality and hit Find. We’ll rank the best matches and explain each pick in a line or two.
      </p>
    </div>
  )
}

export function NoDataState() {
  return (
    <div className="state state--idle" role="status">
      <p className="state__eyebrow">No data yet</p>
      <h2 className="state__title">The restaurant list is empty</h2>
      <p className="state__body">
        The dataset hasn’t been loaded. Run <code>python -m scripts.ingest</code> from <code>backend/</code>, restart
        the server, then reload this page.
      </p>
    </div>
  )
}
