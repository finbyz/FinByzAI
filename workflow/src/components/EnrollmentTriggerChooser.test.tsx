import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { EnrollmentTriggerChooser } from './EnrollmentTriggerChooser'

const events = [
	{
		topic: 'crm.call.inbound',
		label: 'Inbound call received',
		category: 'CRM events',
		description: 'Enroll when Aircall matches an inbound call to the exact record.',
		producer_status: 'native' as const,
		source_app: 'Aircall',
	},
	{
		topic: 'commerce.store.login',
		label: 'Signed in to customer portal',
		category: 'Commerce',
		description: 'Enroll when the customer signs in to the installed portal.',
		producer_status: 'native' as const,
		source_app: 'Customer Portal',
	},
]

describe('EnrollmentTriggerChooser', () => {
	it('offers object-native and installed integration events before creating a trigger', () => {
		const choose = vi.fn()
		render(<EnrollmentTriggerChooser primaryDoctype="Lead" events={events} loading={false} onChoose={choose} onClose={vi.fn()} onRetry={vi.fn()} />)

		fireEvent.click(screen.getByRole('button', { name: /Lead created/ }))
		expect(choose).toHaveBeenLastCalledWith({ type: 'trigger.document_insert' })

		fireEvent.click(screen.getByRole('button', { name: /Inbound call received/ }))
		expect(choose).toHaveBeenLastCalledWith({ type: 'trigger.event', topic: 'crm.call.inbound' })
		expect(screen.getByText('Connected · Aircall')).toBeInTheDocument()
		expect(screen.getByText('Connected · Customer Portal')).toBeInTheDocument()
	})

	it('searches labels, descriptions, categories, and installed source apps', () => {
		render(<EnrollmentTriggerChooser primaryDoctype="Customer" events={events} loading={false} onChoose={vi.fn()} onClose={vi.fn()} onRetry={vi.fn()} />)
		fireEvent.change(screen.getByRole('textbox', { name: 'Search enrollment triggers' }), { target: { value: 'Aircall' } })

		expect(screen.getByRole('button', { name: /Inbound call received/ })).toBeInTheDocument()
		expect(screen.queryByRole('button', { name: /Signed in to customer portal/ })).not.toBeInTheDocument()
		expect(screen.queryByRole('button', { name: /Customer created/ })).not.toBeInTheDocument()
	})

	it('can restrict a legacy business-event node to business events', () => {
		render(<EnrollmentTriggerChooser primaryDoctype="Contact" events={events} loading={false} allowRecordEvents={false} onChoose={vi.fn()} onClose={vi.fn()} onRetry={vi.fn()} />)
		expect(screen.queryByRole('button', { name: /Contact created/ })).not.toBeInTheDocument()
		expect(screen.getByRole('button', { name: /Signed in to customer portal/ })).toBeInTheDocument()
	})
})
