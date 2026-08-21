import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ComponentProps } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
	call: vi.fn(),
	actions: {
		beginInsert: vi.fn(),
		select: vi.fn(),
		selectTrigger: vi.fn(),
		updateNode: vi.fn(),
		replaceTrigger: vi.fn(),
	},
}))

vi.mock('@xyflow/react', () => ({
	Handle: () => null,
	Position: { Bottom: 'bottom' },
	MarkerType: { ArrowClosed: 'arrowclosed' },
	BackgroundVariant: { Dots: 'dots' },
}))
vi.mock('../lib/api', () => ({ call: mocks.call }))
vi.mock('../state/WorkflowContext', () => ({
	useWorkflowActions: () => mocks.actions,
	useWorkflowEditor: () => ({ selectedNodeId: 'start', selectedTriggerGroupId: undefined }),
	useWorkflowDocument: () => ({}),
}))

import { EnrollmentBoundary, nodeSummary, VirtualEndCard } from './WorkflowCanvas'

const eventCatalog = {
	event_types: [{
		topic: 'crm.call.inbound',
		label: 'Inbound call received',
		category: 'CRM events',
		description: 'Aircall matched an inbound call to this record.',
		producer_status: 'native',
		source_app: 'Aircall',
	}],
}

function renderBoundary(workflowNode: Record<string, unknown> = {
	id: 'start',
	type: 'trigger.any',
	type_version: 2,
	position: { x: 0, y: 0 },
	config: { triggers: [{ id: 'trigger-1', type: 'trigger.event', config: { event_topic: '', event_filter: null, condition: null } }] },
}) {
	const props = {
		id: 'start',
		data: {
			workflowNode,
			primaryDoctype: 'Lead',
			issueCount: 1,
		},
	} as unknown as ComponentProps<typeof EnrollmentBoundary>
	return render(<EnrollmentBoundary {...props} />)
}

describe('EnrollmentBoundary multi-trigger authoring', () => {
	beforeEach(() => {
		vi.clearAllMocks()
		mocks.call.mockResolvedValue(eventCatalog)
	})

	it('shows only Add new trigger for an empty placeholder and persists the chosen event into that slot', async () => {
		renderBoundary()
		expect(screen.queryByText('Choose an event')).not.toBeInTheDocument()

		fireEvent.click(screen.getByRole('button', { name: /Add new trigger/ }))
		fireEvent.click(await screen.findByRole('button', { name: /Inbound call received/ }))

		await waitFor(() => expect(mocks.actions.updateNode).toHaveBeenCalledWith(
			'start',
			expect.objectContaining({ triggers: [expect.objectContaining({ type: 'trigger.event', config: expect.objectContaining({ event_topic: 'crm.call.inbound' }) })] }),
			'node:start:triggers',
		))
		expect(mocks.actions.selectTrigger).toHaveBeenCalledWith('start', expect.stringMatching(/^trigger-/))
	})

	it('creates a native record-created OR card through the same chooser', async () => {
		renderBoundary()
		fireEvent.click(screen.getByRole('button', { name: /Add new trigger/ }))
		fireEvent.click(screen.getByRole('button', { name: /Lead created/ }))

		await waitFor(() => expect(mocks.actions.updateNode).toHaveBeenCalledWith(
			'start',
			expect.objectContaining({ triggers: [expect.objectContaining({ type: 'trigger.document_insert', config: { condition: null } })] }),
			'node:start:triggers',
		))
	})

	it('promotes an existing single created trigger and preserves it when a second trigger is added', async () => {
		renderBoundary({ id: 'start', type: 'trigger.document_insert', type_version: 1, position: { x: 0, y: 0 }, config: { condition: { kind: 'predicate', field: 'status', operator: 'eq', value: 'Open' } } })
		expect(screen.getByText('Lead created')).toBeInTheDocument()
		fireEvent.click(screen.getByRole('button', { name: /Add new trigger/ }))
		fireEvent.click(screen.getByRole('button', { name: /Lead changed/ }))

		await waitFor(() => expect(mocks.actions.replaceTrigger).toHaveBeenCalledWith(expect.objectContaining({
			type: 'trigger.any',
			type_version: 2,
			default_config: {
				triggers: [
					expect.objectContaining({ type: 'trigger.document_insert', config: expect.objectContaining({ condition: expect.objectContaining({ field: 'status' }) }) }),
					expect.objectContaining({ type: 'trigger.document_change', config: { watch_fields: [], condition: null } }),
				],
			},
		})))
	})

	it('appends further cards after the workflow is already in multi-trigger mode', async () => {
		renderBoundary({
			id: 'start',
			type: 'trigger.any',
			type_version: 2,
			position: { x: 0, y: 0 },
			config: { triggers: [{ id: 'created', type: 'trigger.document_insert', config: { condition: null } }] },
		})
		fireEvent.click(screen.getByRole('button', { name: /Add new trigger/ }))
		fireEvent.click(screen.getByRole('button', { name: /Lead changed/ }))

		await waitFor(() => expect(mocks.actions.updateNode).toHaveBeenCalledWith(
			'start',
			expect.objectContaining({ triggers: [
				expect.objectContaining({ id: 'created', type: 'trigger.document_insert' }),
				expect.objectContaining({ type: 'trigger.document_change' }),
			] }),
			'node:start:triggers',
		))
	})

	it('renders every trigger as a separate connected card instead of one enrollment container', () => {
		const { container } = renderBoundary({
			id: 'start',
			type: 'trigger.any',
			type_version: 2,
			position: { x: 0, y: 0 },
			config: { triggers: [
				{ id: 'created', type: 'trigger.document_insert', config: { condition: null } },
				{ id: 'changed', type: 'trigger.document_change', config: { watch_fields: ['status'], condition: null } },
			] },
		})

		expect(container.querySelectorAll('.enrollment-trigger-card')).toHaveLength(2)
		expect(container.querySelectorAll('.enrollment-trigger-card__icon')).toHaveLength(2)
		expect(container.querySelector('.enrollment-boundary__connector')).toBeInTheDocument()
		expect(screen.getByRole('button', { name: /Add new trigger/ })).toBeInTheDocument()
		expect(screen.queryByText('All trigger paths join here')).not.toBeInTheDocument()
	})
})

describe('Workflow card summaries', () => {
	const node = (type: string, config: Record<string, unknown>, typeVersion = 1) => ({ id: type, type, type_version: typeVersion, position: { x: 0, y: 0 }, config }) as Parameters<typeof nodeSummary>[0]

	it('uses readable duration and advanced-action summaries instead of a false completion message', () => {
		expect(nodeSummary(node('delay.fixed', { seconds: 172800, duration: 2, duration_unit: 'business_days' }), 'Lead')).toBe('Continue after 2 business days')
		expect(nodeSummary(node('delay.drip', { batch_size: 100, interval_seconds: 604800, interval_duration: 1, interval_unit: 'weeks' }), 'Lead')).toBe('Release 100 records every 1 week')
		expect(nodeSummary(node('action.create_note', { title: 'Call summary' }), 'Lead')).toBe('Create note: Call summary')
		expect(nodeSummary(node('action.copy_record', {}), 'Lead')).toBe('Create a new Lead from this record')
		expect(nodeSummary(node('action.asana', { operation: 'create_task' }), 'Lead')).toBe('Create an Asana task')
		expect(nodeSummary(node('action.unassign_record', {}), 'Lead')).not.toContain('ends successfully')
	})

	it('summarizes all configured deduplication fields', () => {
		expect(nodeSummary(node('condition.deduplicate', { match_fields: ['email_id', 'phone'], match_mode: 'any' }, 2), 'Lead')).toBe('Check existing Lead by email_id or phone')
	})
})

describe('Derived path ending', () => {
	it('shows a clean + to END continuation without a redundant default-path label', () => {
		const props = {
			id: 'virtual-end:start:default',
			data: {
				sourceId: 'start',
				sourceHandle: 'default',
				label: 'Next action',
				insertPosition: { x: 0, y: 250 },
			},
		} as unknown as ComponentProps<typeof VirtualEndCard>
		const { container } = render(<VirtualEndCard {...props} />)

		expect(screen.queryByText('Next action')).not.toBeInTheDocument()
		expect(screen.getByText('END')).toBeInTheDocument()
		expect(container.querySelector('.workflow-path-end__tail')).toBeInTheDocument()
	})

	it('retains meaningful branch names above unfinished branch paths', () => {
		const props = {
			id: 'virtual-end:branch:none',
			data: {
				sourceId: 'branch',
				sourceHandle: 'none',
				label: 'None',
				insertPosition: { x: 0, y: 190 },
			},
		} as unknown as ComponentProps<typeof VirtualEndCard>
		render(<VirtualEndCard {...props} />)

		expect(screen.getByText('None')).toBeInTheDocument()
	})
})
