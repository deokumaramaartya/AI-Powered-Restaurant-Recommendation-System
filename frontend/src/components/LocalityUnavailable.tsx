import { describeError } from '../errorCopy'

interface Props {
  /** The meta request failure, or null when meta loaded but has no localities. */
  error: unknown
  onRetry?: () => void
}

/** Stands in for the Locality field when localities can't be offered. */
export function LocalityUnavailable({ error, onRetry }: Props) {
  if (error === null) {
    return (
      <div className="field">
        <p className="field__label field__label--required">Locality</p>
        <div className="input input--unavailable">No localities loaded yet</div>
      </div>
    )
  }
  const copy = describeError(error)
  return (
    <div className="field" role="alert">
      <p className="field__label field__label--required">Locality</p>
      <div className="input input--unavailable input--invalid">
        Localities unavailable
        {onRetry ? (
          <button type="button" className="button button--inset" onClick={onRetry}>
            Retry
          </button>
        ) : null}
      </div>
      <p className="field__error">{copy.title}</p>
      <p className="field__hint">{copy.body}</p>
    </div>
  )
}
