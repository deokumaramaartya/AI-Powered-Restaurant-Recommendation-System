import { ApiError } from './api/client'
import { isFormField } from './types'

export interface ErrorCopy {
  title: string
  body: string
  reload?: boolean
}

export function describeError(error: unknown): ErrorCopy {
  if (!(error instanceof ApiError)) {
    return { title: 'Something went wrong, try again', body: 'An unexpected error stopped the request.' }
  }
  switch (error.kind) {
    case 'network':
      return {
        title: "Can't reach the server",
        body: 'Check that the backend is running on port 8000 (uvicorn app.main:app from backend/), then try again.',
      }
    case 'unavailable':
      return { title: 'Data not loaded, run ingestion', body: error.message }
    case 'timeout':
      return {
        title: 'The server took too long to respond',
        body: 'No results came back within 20 seconds. Your preferences are still here — try again in a moment.',
      }
    case 'validation': {
      const staleLocation = error.fieldErrors.some((item) => item.field === 'location')
      if (staleLocation) {
        return {
          title: "Some preferences weren't accepted",
          body: `${error.message} The list of localities may have changed. Reload the page to refresh it.`,
          reload: true,
        }
      }
      // Messages for form fields are shown beside the field; only the rest are repeated here.
      const shownInForm = error.fieldErrors.some((item) => isFormField(item.field))
      const others = error.fieldErrors.filter((item) => !isFormField(item.field)).map((item) => item.message)
      const body = [shownInForm ? 'Check the highlighted fields in the form.' : '', ...others].filter(Boolean).join(' ')
      return { title: "Some preferences weren't accepted", body: body || error.message }
    }
    default:
      return { title: 'Something went wrong, try again', body: error.message }
  }
}
