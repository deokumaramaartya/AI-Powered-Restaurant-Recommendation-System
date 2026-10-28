import { describeError } from '../errorCopy'

interface Props {
  error: unknown
  onRetry?: () => void
}

export function ErrorNotice({ error, onRetry }: Props) {
  const copy = describeError(error)
  return (
    <div className="state state--error" role="alert">
      <p className="state__eyebrow">Search failed</p>
      <h2 className="state__title">{copy.title}</h2>
      <p className="state__body">{copy.body}</p>
      {copy.reload ? (
        <button type="button" className="button button--light" onClick={() => window.location.reload()}>
          Reload page
        </button>
      ) : onRetry ? (
        <button type="button" className="button button--light" onClick={onRetry}>
          Try again
        </button>
      ) : null}
    </div>
  )
}
