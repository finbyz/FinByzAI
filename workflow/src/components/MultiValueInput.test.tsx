import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { MultiValueInput } from './MultiValueInput'

describe('MultiValueInput', () => {
  it('creates deduplicated pills and supports keyboard removal', () => {
    const onChange = vi.fn()
    const { rerender } = render(<MultiValueInput values={[]} onChange={onChange} />)
    const input = screen.getByLabelText('Multiple values')
    fireEvent.change(input, { target: { value: 'Price, Timing, Price' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onChange).toHaveBeenLastCalledWith(['Price', 'Timing'])

    rerender(<MultiValueInput values={['Price', 'Timing']} onChange={onChange} />)
    fireEvent.keyDown(screen.getByLabelText('Multiple values'), { key: 'Backspace' })
    expect(onChange).toHaveBeenLastCalledWith(['Price'])
  })

  it('removes a selected pill explicitly', () => {
    const onChange = vi.fn()
    render(<MultiValueInput values={['Price', 'Timing']} onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: 'Remove Price' }))
    expect(onChange).toHaveBeenCalledWith(['Timing'])
  })
})
