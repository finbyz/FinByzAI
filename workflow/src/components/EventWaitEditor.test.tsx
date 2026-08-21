import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { BusinessEventType, WorkflowNode } from '../types'
import { EventWaitEditor } from './Inspector'

const events: BusinessEventType[] = [
	{
		topic: 'record.updated',
		label: 'Record updated',
		category: 'Record activity',
		description: 'Updated',
		available_for: ['wait'],
		source_modes: ['enrolled_record', 'action_output'],
		source_node_types: ['action.create_record', 'action.copy_record'],
		producer_status: 'native',
		record_resolution: 'The exact enrolled record is matched.',
	},
	{
		topic: 'email.opened',
		label: 'Email opened',
		category: 'Email',
		description: 'Opened',
		available_for: ['wait'],
		source_modes: ['enrolled_record', 'action_output'],
		source_node_types: ['action.send_email'],
		producer_status: 'native',
		record_resolution: 'The provider event resolves to the enrolled record.',
	},
	{
		topic: 'email.unsubscribed',
		label: 'Email unsubscribed',
		category: 'Email',
		description: 'Unsubscribed',
		available_for: ['wait'],
		source_modes: ['enrolled_record', 'action_output'],
		source_node_types: ['action.send_email'],
		producer_status: 'native',
		record_resolution: 'The unsubscribe event resolves to the enrolled record.',
		filter_fields: [
			{ fieldname: 'email_type', label: 'Unsubscribe scope', fieldtype: 'Select', options: 'global\nrecord\ntopic' },
			{ fieldname: 'subscription_topic', label: 'Reach subscription topic', fieldtype: 'Link', options: 'Subscription Topic' },
		],
	},
]

const outputNodes: WorkflowNode[] = [
	{ id: 'create', type: 'action.create_record', type_version: 1, position: { x: 0, y: 0 }, config: {} },
	{ id: 'send', type: 'action.send_email', type_version: 2, position: { x: 0, y: 0 }, config: {} },
]

describe('EventWaitEditor', () => {
	it('starts with an object-aware data source instead of a Lead-specific event list', () => {
		const update = vi.fn()
		render(<EventWaitEditor config={{ data_source: 'enrolled_record', event_topic: '' }} typeVersion={2} events={events} outputNodes={outputNodes} update={update} nodeId="wait" primaryDoctype="Opportunity" />)

		expect(screen.getByRole('option', { name: 'This Opportunity' })).toBeInTheDocument()
		expect(screen.queryByText(/Lead joined a list/i)).not.toBeInTheDocument()
		fireEvent.change(screen.getByLabelText('Event belongs to'), { target: { value: 'action_output' } })
		expect(update).toHaveBeenCalledWith(expect.objectContaining({ data_source: 'action_output', event_topic: '', event_source: null }), 'data_source')
	})

	it('binds email events to the exact message from an earlier Send email action', () => {
		const update = vi.fn()
		render(<EventWaitEditor config={{ data_source: 'action_output', event_topic: 'email.opened', timeout_mode: 'duration', timeout_seconds: 3600 }} typeVersion={2} events={events} outputNodes={outputNodes} update={update} nodeId="wait" primaryDoctype="Opportunity" />)

		const source = screen.getByLabelText('Earlier action')
		expect(source).toHaveTextContent('Send an email')
		expect(source).not.toHaveTextContent('Create record')
		fireEvent.change(source, { target: { value: 'send' } })
		expect(update).toHaveBeenCalledWith(expect.objectContaining({
			event_source: { kind: 'node_output', node_id: 'send', path: 'email_queue' },
			event_source_doctype: { kind: 'literal', value: 'Email Queue' },
		}), 'event_source')
	})

	it('supports indefinite waits and confirms removal of a connected timeout path', () => {
		const update = vi.fn()
		const { rerender } = render(<EventWaitEditor config={{ data_source: 'enrolled_record', event_topic: 'record.updated', timeout_mode: 'duration', timeout_seconds: 3600 }} typeVersion={2} events={events} outputNodes={outputNodes} update={update} nodeId="wait" primaryDoctype="Customer" />)

		fireEvent.click(screen.getByRole('button', { name: 'As long as possible' }))
		expect(update).toHaveBeenCalledWith(expect.objectContaining({ timeout_mode: 'indefinite', branch_on_timeout: 0 }), 'timeout_mode')

		rerender(<EventWaitEditor config={{ data_source: 'enrolled_record', event_topic: 'record.updated', timeout_mode: 'duration', timeout_seconds: 3600, branch_on_timeout: 1 }} typeVersion={2} events={events} outputNodes={outputNodes} update={update} nodeId="wait" primaryDoctype="Customer" timeoutPathConnected />)
		update.mockClear()
		fireEvent.click(screen.getByRole('button', { name: 'As long as possible' }))
		expect(update).not.toHaveBeenCalled()
		expect(screen.getByText(/Remove the connected timeout path/i)).toBeInTheDocument()
		fireEvent.click(screen.getByRole('button', { name: 'Remove path' }))
		expect(update).toHaveBeenCalledWith(expect.objectContaining({ timeout_mode: 'indefinite', branch_on_timeout: 0 }), 'timeout_mode')
	})

	it('offers a direct global, record, or Reach-topic unsubscribe scope', () => {
		const update = vi.fn()
		const { rerender } = render(<EventWaitEditor config={{ data_source: 'enrolled_record', event_topic: 'email.unsubscribed', event_filter: null, timeout_mode: 'indefinite' }} typeVersion={2} events={events} outputNodes={outputNodes} update={update} nodeId="wait" primaryDoctype="Lead" />)

		const scope = screen.getByLabelText('Unsubscribe scope')
		expect(scope).toHaveValue('any')
		fireEvent.change(scope, { target: { value: 'topic' } })
		expect(update).toHaveBeenCalledWith(expect.objectContaining({
			event_filter: { kind: 'predicate', field: 'email_type', operator: 'eq', value: 'topic' },
		}), 'event_filter')
		rerender(<EventWaitEditor config={{ data_source: 'enrolled_record', event_topic: 'email.unsubscribed', event_filter: { kind: 'predicate', field: 'email_type', operator: 'eq', value: 'topic' }, timeout_mode: 'indefinite' }} typeVersion={2} events={events} outputNodes={outputNodes} update={update} nodeId="wait" primaryDoctype="Lead" />)
		expect(screen.getByLabelText('Subscription topic')).toBeInTheDocument()
	})
})
