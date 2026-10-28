import { fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { sampleMeta } from '../test/fixtures'
import { PreferenceForm } from './PreferenceForm'

function renderForm(props: Partial<Parameters<typeof PreferenceForm>[0]> = {}) {
  const onSubmit = vi.fn()
  render(<PreferenceForm meta={sampleMeta} onSubmit={onSubmit} {...props} />)
  return { onSubmit, user: userEvent.setup() }
}

const submitButton = () => screen.getByRole('button', { name: /find restaurants/i })

describe('PreferenceForm', () => {
  it('asks for a locality instead of submitting without one', async () => {
    const { user, onSubmit } = renderForm()
    await user.click(submitButton())

    expect(onSubmit).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toHaveTextContent('Pick a locality to start — everything else is optional.')
    const locality = screen.getByLabelText('Locality')
    expect(locality).toHaveAttribute('aria-invalid', 'true')
    expect(locality).toHaveFocus()
    expect(locality).toHaveAccessibleDescription(
      'Covers Bangalore localities only. Pick a locality to start — everything else is optional.',
    )

    await user.selectOptions(locality, 'BTM')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(locality).not.toHaveAttribute('aria-invalid')

    await user.click(submitButton())
    expect(onSubmit).toHaveBeenCalledTimes(1)
  })

  it('only offers locations and cuisines from meta', () => {
    renderForm()
    const locationOptions = within(screen.getByLabelText('Locality'))
      .getAllByRole('option')
      .map((option) => option.textContent)
    expect(locationOptions).toEqual(['Choose a neighbourhood', ...sampleMeta.locations])

    const cuisineOptions = within(screen.getByRole('group', { name: 'Cuisine options' }))
      .getAllByRole('checkbox')
      .map((box) => box.closest('label')?.textContent)
    expect(cuisineOptions).toEqual(sampleMeta.cuisines)
  })

  it('submits values that match the form inputs', async () => {
    const { user, onSubmit } = renderForm()

    await user.selectOptions(screen.getByLabelText('Locality'), 'Indiranagar')
    await user.click(screen.getByRole('radio', { name: /medium/i }))
    await user.click(screen.getByRole('checkbox', { name: 'Italian' }))
    await user.click(screen.getByRole('checkbox', { name: 'Continental' }))
    fireEvent.change(screen.getByLabelText('Minimum rating'), { target: { value: '4' } })
    await user.type(screen.getByLabelText(/anything else/i), '  family-friendly ')
    await user.click(within(screen.getByRole('group', { name: 'Results' })).getByRole('radio', { name: '10' }))
    await user.click(submitButton())

    expect(onSubmit).toHaveBeenCalledWith({
      location: 'Indiranagar',
      budget: 'medium',
      cuisines: ['Italian', 'Continental'],
      min_rating: 4,
      extra_preferences: 'family-friendly',
      top_k: 10,
    })
  })

  it('leaves optional fields out of the request when they are not set', async () => {
    const { user, onSubmit } = renderForm()
    await user.selectOptions(screen.getByLabelText('Locality'), 'HSR')
    await user.click(submitButton())

    expect(onSubmit).toHaveBeenCalledWith({ location: 'HSR', min_rating: 3.5, top_k: 5 })
  })

  it('disables submit while a request is in flight', async () => {
    const onSubmit = vi.fn()
    const { rerender } = render(<PreferenceForm meta={sampleMeta} onSubmit={onSubmit} />)
    const user = userEvent.setup()
    await user.selectOptions(screen.getByLabelText('Locality'), 'BTM')

    rerender(<PreferenceForm meta={sampleMeta} onSubmit={onSubmit} submitting />)
    const button = screen.getByRole('button', { name: /finding restaurants/i })
    expect(button).toBeDisabled()
    await user.click(button)
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('counts an emoji as one character, matching the backend', async () => {
    const { user } = renderForm()
    await user.click(screen.getByLabelText(/anything else/i))
    await user.paste('😀 ok')

    expect(screen.getByTestId('extra-count')).toHaveTextContent('4/300')
  })

  it('stops pasted text at 300 code points', async () => {
    const { user } = renderForm()
    const box = screen.getByLabelText<HTMLTextAreaElement>(/anything else/i)
    await user.click(box)
    await user.paste('🍕'.repeat(301))

    expect(screen.getByTestId('extra-count')).toHaveTextContent('300/300')
    expect([...box.value]).toHaveLength(300)
  })

  it('has a 0 to 5 rating slider that defaults to 3.5 and always shows its value', () => {
    renderForm()
    const slider = screen.getByLabelText('Minimum rating')
    expect(slider).toHaveAttribute('min', '0')
    expect(slider).toHaveAttribute('max', '5')
    expect(slider).toHaveAttribute('step', '0.5')
    expect(screen.getByTestId('rating-value')).toHaveTextContent('3.5+')

    fireEvent.change(slider, { target: { value: '0' } })
    expect(screen.getByTestId('rating-value')).toHaveTextContent(/^Any$/)
    expect(slider).toHaveAttribute('aria-valuetext', 'Any rating')
  })

  it('offers 3, 5 or 10 results with 5 selected', () => {
    renderForm()
    const group = within(screen.getByRole('group', { name: 'Results' }))
    expect(group.getAllByRole('radio').map((radio) => radio.closest('label')?.textContent)).toEqual(['3', '5', '10'])
    expect(group.getByRole('radio', { name: '5' })).toBeChecked()
  })

  it('filters the cuisine list by search text', async () => {
    const { user } = renderForm()
    await user.type(screen.getByLabelText('Search cuisines'), 'ind')

    const visible = within(screen.getByRole('group', { name: 'Cuisine options' }))
      .getAllByRole('checkbox')
      .map((box) => box.closest('label')?.textContent)
    expect(visible).toEqual(['North Indian', 'South Indian'])
  })

  it('keeps a selected cuisine as a removable chip after the search changes', async () => {
    const { user } = renderForm()
    await user.click(screen.getByRole('checkbox', { name: 'Mexican' }))
    await user.type(screen.getByLabelText('Search cuisines'), 'chi')
    expect(screen.queryByRole('checkbox', { name: 'Mexican' })).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Remove Mexican' }))
    expect(screen.queryByRole('button', { name: 'Remove Mexican' })).not.toBeInTheDocument()
  })

  it('shows budget tier bounds from meta', () => {
    renderForm()
    expect(screen.getByRole('radio', { name: /low/i }).closest('label')).toHaveTextContent('≤₹300')
    expect(screen.getByRole('radio', { name: /medium/i }).closest('label')).toHaveTextContent('₹301–₹600')
    expect(screen.getByRole('radio', { name: /high/i }).closest('label')).toHaveTextContent('₹601+')
  })

  it('notes that the data covers Bangalore localities only', () => {
    renderForm()
    expect(screen.getByLabelText('Locality')).toHaveAccessibleDescription('Covers Bangalore localities only.')
  })

  it('shows each 422 message beside its field and marks the input invalid', () => {
    renderForm({
      fieldErrors: [
        { field: 'min_rating', message: 'Minimum rating must be a number from 0 to 5.' },
        { field: 'extra_preferences', message: 'Extra preferences must be text of at most 300 characters.' },
      ],
    })
    const rating = screen.getByLabelText('Minimum rating')
    expect(rating).toHaveAttribute('aria-invalid', 'true')
    expect(rating).toHaveAccessibleDescription('Minimum rating must be a number from 0 to 5.')
    const extra = screen.getByLabelText(/anything else/i)
    expect(extra).toHaveAttribute('aria-invalid', 'true')
    expect(extra).toHaveAccessibleDescription(/at most 300 characters/)
    expect(screen.getByLabelText('Locality')).not.toHaveAttribute('aria-invalid')
  })

  it('keeps the locality note and adds the error to its description', () => {
    renderForm({ fieldErrors: [{ field: 'location', message: 'Choose a locality from the list.' }] })
    expect(screen.getByLabelText('Locality')).toHaveAccessibleDescription(
      'Covers Bangalore localities only. Choose a locality from the list.',
    )
  })

  it('shows group errors inside the budget and cuisine fieldsets', () => {
    renderForm({
      fieldErrors: [
        { field: 'budget', message: 'Budget must be low, medium, or high.' },
        { field: 'cuisines', message: 'Cuisines must be a list of at most 20 names.' },
      ],
    })
    expect(screen.getByRole('group', { name: 'Budget for two' })).toHaveTextContent(
      'Budget must be low, medium, or high.',
    )
    expect(screen.getByRole('group', { name: /cuisines/i })).toHaveTextContent('at most 20 names')
  })
})
