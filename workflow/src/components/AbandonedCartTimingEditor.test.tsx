import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { AbandonedCartTimingEditor } from './Inspector'

describe('AbandonedCartTimingEditor', () => {
	it('defaults legacy triggers to 24 hours and saves a custom hour threshold', () => {
		const onChange = vi.fn()
		render(<AbandonedCartTimingEditor config={{ event_topic: 'commerce.order.abandoned' }} onChange={onChange} />)

		expect(screen.getByRole('spinbutton', { name: 'Cart idle duration' })).toHaveValue(24)
		expect(screen.getByRole('combobox', { name: 'Cart idle duration unit' })).toHaveValue('hours')

		fireEvent.change(screen.getByRole('spinbutton', { name: 'Cart idle duration' }), { target: { value: '6' } })
		expect(onChange).toHaveBeenCalledWith({
			event_topic: 'commerce.order.abandoned',
			abandoned_after_value: 6,
			abandoned_after_unit: 'hours',
		})
	})

	it('converts an equivalent hour threshold when the user switches to days', () => {
		const onChange = vi.fn()
		render(<AbandonedCartTimingEditor config={{ event_topic: 'commerce.order.abandoned', abandoned_after_value: 48, abandoned_after_unit: 'hours' }} onChange={onChange} />)

		fireEvent.change(screen.getByRole('combobox', { name: 'Cart idle duration unit' }), { target: { value: 'days' } })
		expect(onChange).toHaveBeenCalledWith({
			event_topic: 'commerce.order.abandoned',
			abandoned_after_value: 2,
			abandoned_after_unit: 'days',
		})
	})
})
