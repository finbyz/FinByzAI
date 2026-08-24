/* oxlint-disable react/only-export-components -- exported summary logic has direct regression coverage beside the canvas */
import {
  BaseEdge,
  Background,
  BackgroundVariant,
  Controls,
  EdgeLabelRenderer,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
  applyNodeChanges,
  type Connection,
  type EdgeProps,
  type Node,
  type NodeChange,
  type NodeProps,
  type ReactFlowInstance,
  getSmoothStepPath,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { AlertTriangle, ArrowLeft, ArrowRight, CheckCircle2, Copy, LayoutTemplate, Plus, Trash2, Unplug, Zap } from 'lucide-react'
import { memo, useEffect, useMemo, useRef, useState, type CSSProperties, type DragEvent } from 'react'
import { call } from '../lib/api'
import { reachableWorkflowNodeIds, workflowNodeSourceHandles, workflowNodeVisualWidth } from '../lib/workflowGraphCommands'
import { useWorkflowActions, useWorkflowDocument, useWorkflowEditor } from '../state/WorkflowContext'
import type { BusinessEventType, CanvasMetric, CanvasMetricsResponse, NodeCatalogItem, NodeType, WorkflowNode } from '../types'
import { EnrollmentTriggerChooser, type EnrollmentTriggerChoice } from './EnrollmentTriggerChooser'
import { DeleteWorkflowStepButton } from './DeleteWorkflowStepButton'
import { nodeLabels, nodeIcons } from './InspectorHelpers'

type WorkflowFlowNode = Node<{ workflowNode: WorkflowNode; primaryDoctype: string; issueCount: number; manualConnections: boolean; connected: boolean; metric?: CanvasMetric }, 'workflow'>
type EnrollmentFlowNode = Node<{ workflowNode: WorkflowNode; primaryDoctype: string; issueCount: number; totalEnrollments?: number }, 'enrollment'>
type VirtualEndFlowNode = Node<{ sourceId: string; sourceHandle: string; label: string; insertPosition: { x: number; y: number } }, 'virtualEnd'>
type FlowNode = WorkflowFlowNode | EnrollmentFlowNode | VirtualEndFlowNode

function nodeKind(type: NodeType) {
  if (type.startsWith('trigger.')) return 'trigger'
	if (type.startsWith('condition.')) return 'logic'
  if (type.startsWith('delay.')) return 'delay'
	if (type === 'transform.value') return 'logic'
  if (type === 'end.complete') return 'end'
  return 'action'
}

const conditionOperatorLabels: Record<string, string> = {
	eq: 'equals',
	ne: 'does not equal',
	gt: 'is greater than',
	gte: 'is at least',
	lt: 'is less than',
	lte: 'is at most',
	in: 'is any of',
	not_in: 'is none of',
	contains: 'contains',
	not_contains: 'does not contain',
	contains_any: 'contains any of',
	contains_all: 'contains all of',
	contains_none: 'contains none of',
	is_set: 'is set',
	is_not_set: 'is not set',
}

function conditionSummary(condition: Record<string, unknown> | undefined) {
	if (!condition) return 'Route records using field criteria'
	if (condition.kind === 'predicate' && condition.field) {
		const operator = String(condition.operator || 'eq')
		const prefix = `${String(condition.field)} ${conditionOperatorLabels[operator] || operator}`
		return ['is_set', 'is_not_set'].includes(operator) ? prefix : `${prefix} ${String(condition.value ?? '')}`
	}
	if (['all', 'any', 'not'].includes(String(condition.kind)) && Array.isArray(condition.children)) {
		const joiner = condition.kind === 'all' ? 'all' : condition.kind === 'any' ? 'any' : 'none'
		return `Match ${joiner} of ${condition.children.length} configured rule${condition.children.length === 1 ? '' : 's'}`
	}
	return 'Route records using field criteria'
}

function nodeOutputHandles(node: WorkflowNode): Array<{ handle: string; label: string }> {
	if (node.type === 'condition.if_else') {
		return node.type_version >= 2
			? [...(Array.isArray(node.config.branches) ? node.config.branches : []).flatMap((branch) => typeof branch === 'object' && branch ? [{ handle: String((branch as Record<string, unknown>).handle || ''), label: String((branch as Record<string, unknown>).name || '') }] : []), { handle: 'none', label: 'None' }]
			: [{ handle: 'true', label: 'Yes' }, { handle: 'false', label: 'No' }]
	}
	if (node.type === 'condition.random_split') return (Array.isArray(node.config.branches) ? node.config.branches : []).flatMap((branch) => typeof branch === 'object' && branch ? [{ handle: String((branch as Record<string, unknown>).handle || ''), label: `${String((branch as Record<string, unknown>).name || '')} · ${Number((branch as Record<string, unknown>).percentage || 0)}%` }] : [])
	if (node.type === 'condition.deduplicate') return [{ handle: 'duplicate', label: 'Duplicate' }, { handle: 'unique', label: 'Unique' }]
	if (node.type === 'delay.until_event') return node.type_version >= 2 && !node.config.branch_on_timeout
		? [{ handle: 'default', label: 'Next action' }]
		: [{ handle: 'event', label: 'Event happened' }, { handle: 'timeout', label: 'Time ran out' }]
	if (node.type === 'condition.switch') return [...(Array.isArray(node.config.cases) ? node.config.cases : []).flatMap((item) => typeof item === 'object' && item ? [{ handle: String((item as Record<string, unknown>).handle || ''), label: String((item as Record<string, unknown>).value || '') }] : []), { handle: 'default', label: 'Default' }]
	if (['end.complete', 'action.delete_record', 'action.go_to'].includes(node.type)) return []
	return [{ handle: 'default', label: 'Next action' }]
}

function readableDuration(config: Record<string, unknown>, secondsKey = 'seconds', durationKey = 'duration', unitKey = 'duration_unit') {
	const unit = String(config[unitKey] || '')
	const configuredDuration = Number(config[durationKey])
	const labels: Record<string, [string, string]> = {
		seconds: ['second', 'seconds'],
		minutes: ['minute', 'minutes'],
		hours: ['hour', 'hours'],
		days: ['day', 'days'],
		weeks: ['week', 'weeks'],
		business_days: ['business day', 'business days'],
	}
	if (labels[unit] && Number.isFinite(configuredDuration) && configuredDuration > 0) {
		return `${configuredDuration} ${labels[unit][configuredDuration === 1 ? 0 : 1]}`
	}
	const seconds = Math.max(Number(config[secondsKey] || 0), 0)
	for (const [candidate, multiplier] of [['week', 604800], ['day', 86400], ['hour', 3600], ['minute', 60]] as const) {
		if (seconds >= multiplier && seconds % multiplier === 0) {
			const amount = seconds / multiplier
			return `${amount} ${candidate}${amount === 1 ? '' : 's'}`
		}
	}
	return `${seconds} second${seconds === 1 ? '' : 's'}`
}

export function nodeSummary(node: WorkflowNode, primaryDoctype: string) {
  const config = node.config
  if (node.type === 'trigger.manual') return `Enroll a ${primaryDoctype} from run history`
	  if (node.type === 'trigger.document_insert') return `When a new ${primaryDoctype} matches`
	  if (node.type === 'trigger.document_change') return `When relevant ${primaryDoctype} fields change`
	if (node.type === 'trigger.filter_criteria') return `When a ${primaryDoctype} meets the configured criteria`
	if (node.type === 'trigger.event') {
		const events = Array.isArray(config.events) ? config.events : []
		return events.length ? `When any of ${events.length} selected event${events.length === 1 ? '' : 's'} occurs` : config.event_topic ? `When ${String(config.event_topic)} is received` : 'Choose a business event'
	}
	if (node.type === 'trigger.schedule') return 'Run through a durable Enrollment schedule'
	if (node.type === 'trigger.webhook') return 'Enroll one exact record through a managed authenticated endpoint'
  if (node.type === 'condition.if_else') {
		if (node.type_version >= 2) {
			const count = Array.isArray(config.branches) ? config.branches.length : 0
			return `${count} named criteria branch${count === 1 ? '' : 'es'} plus None`
		}
    const condition = config.condition as Record<string, unknown> | undefined
    return conditionSummary(condition)
  }
	if (node.type === 'condition.random_split') return `${Array.isArray(config.branches) ? config.branches.length : 0} named percentage paths`
  if (node.type === 'condition.switch') return config.field ? `Branch on ${String(config.field)}` : 'Branch based on exact field value'
  if (node.type === 'condition.deduplicate') {
	const fields = node.type_version >= 2 && Array.isArray(config.match_fields) ? config.match_fields.map(String).filter(Boolean) : [String(config.match_field || '')].filter(Boolean)
	return fields.length ? `Check existing ${primaryDoctype} by ${fields.join(config.match_mode === 'any' ? ' or ' : ' and ')}` : 'Choose fields used to find duplicates'
  }
  if (node.type === 'delay.fixed') return `Continue after ${readableDuration({ seconds: config.seconds || 3600, duration: config.duration, duration_unit: config.duration_unit })}`
	if (node.type === 'delay.drip') return `Release ${Number(config.batch_size || 0).toLocaleString()} records every ${readableDuration({ seconds: config.interval_seconds || 3600, duration: config.interval_duration, duration_unit: config.interval_unit })}`
	if (node.type === 'delay.until_date') return config.mode === 'literal' || config.datetime ? `Continue at ${String(config.datetime || 'a specific date and time')}` : config.field ? `Continue when ${String(config.field)} is reached` : 'Choose a date/time source'
	if (node.type === 'delay.until_event') {
		if (!config.event_topic) return 'Choose the event that releases this wait'
		const event = String(config.event_topic).split('.').map((part) => part.replaceAll('_', ' ')).join(' ')
		const source = config.data_source === 'action_output' || config.event_source ? ' from an earlier action' : ` on this ${primaryDoctype}`
		return `${config.timeout_mode === 'indefinite' ? 'Wait indefinitely for' : 'Wait for'} ${event}${source}`
	}
  if (node.type === 'delay.business_hours') return config.timezone ? `Wait for business hours in ${String(config.timezone)}` : 'Resume during allowed working hours'
  if (node.type === 'transform.value') return `Produce a reusable ${String(config.operation || 'coalesced')} value`
  if (node.type === 'transform.associated_record') return config.fetch_field ? `Read ${String(config.fetch_field)} from linked record` : 'Fetch a value from a linked record'
  if (node.type === 'transform.child_records') return config.fetch_field ? `Read ${String(config.fetch_field)} from child rows` : 'Aggregate a value from child table rows'
  if (node.type === 'action.update_record') {
	const count = Array.isArray(config.assignments) ? config.assignments.length : 0
	return count ? `Apply ${count} permitted field change${count === 1 ? '' : 's'}` : 'Choose fields to update'
  }
  if (node.type === 'action.create_record') return config.target_doctype ? `Create ${String(config.target_doctype)}` : 'Choose a permitted DocType'
  if (node.type === 'action.create_todo') return config.description ? String(config.description) : 'Assign follow-up work'
  if (node.type === 'action.add_comment') return config.content ? String(config.content) : 'Write to the record timeline'
  if (node.type === 'action.notify_user') return config.subject ? String(config.subject) : 'Send an internal notification'
  if (node.type === 'action.send_email') return config.email_template ? `Send ${String(config.email_template)}` : config.content_mode === 'inline' ? 'Send a quick email' : 'Choose an Email Template'
  if (node.type === 'action.send_sms') return 'Submit a consent-aware SMS to the configured gateway'
  if (node.type === 'action.webhook') return config.url ? `POST to ${String(config.url)}` : 'Choose an allowlisted HTTPS endpoint'
  if (node.type === 'action.call_subflow') return config.subflow_id ? `Call ${String(config.subflow_id)}` : 'Execute another workflow as a subflow'
  if (node.type === 'action.numeric_adjust') return config.field ? `${String(config.operation || 'add').charAt(0).toUpperCase() + String(config.operation || 'add').slice(1)} on ${String(config.field)}` : 'Increment or decrement a number field'
  if (node.type === 'action.manage_association') return config.target_doctype ? `${String(config.operation || 'link').charAt(0).toUpperCase() + String(config.operation || 'link').slice(1)} ${String(config.target_doctype)}` : 'Link or unlink records'
  if (node.type === 'action.round_robin') return config.assignment_type === 'users'
    ? `Rotate across ${Array.isArray(config.users) ? config.users.length : 0} selected users`
    : config.group ? `Rotate through ${String(config.group)}` : 'Choose a User Group or specific users'
  if (node.type === 'action.delete_record') return 'Permanently delete the enrolled record'
  if (node.type === 'action.create_note') return config.title ? `Create note: ${String(config.title)}` : 'Create a Desk note linked to this record'
  if (node.type === 'action.copy_record') return `Create a new ${primaryDoctype} from this record`
  if (node.type === 'action.merge_contact') return Array.isArray(config.match_fields) && config.match_fields.length ? `Merge Contact by ${config.match_fields.map(String).join(config.match_mode === 'any' ? ' or ' : ' and ')}` : 'Choose Contact identity fields'
  if (node.type === 'action.unassign_record') return 'Close every open Frappe assignment for this record'
  if (node.type === 'action.verify_email') return 'Check email syntax and expose valid/reason outputs'
  if (node.type === 'action.mark_communications_read') return 'Mark linked received Communications as seen'
  if (node.type === 'action.remove_from_workflow') return config.target_workflow === 'current' || !config.target_workflow ? 'End this record’s current workflow runs' : `Remove this record from ${String(config.target_workflow)}`
  if (node.type === 'action.complete_goal') return `Mark ${String(config.goal || 'goal reached')} and end this path`
  if (node.type === 'action.go_to') return config.target_node_id ? `Continue at step ${String(config.target_node_id)}` : 'Choose an existing destination step'
  if (node.type === 'action.instagram_message') return 'Send an Instagram Direct message through Meta'
  if (node.type === 'action.asana') return ({ create_task: 'Create an Asana task', update_task: 'Update an Asana task', create_subtask: 'Create an Asana subtask', create_project: 'Create an Asana project' } as Record<string, string>)[String(config.operation || '')] || 'Choose an Asana operation'
  if (node.type === 'end.complete') return 'Legacy explicit completion marker'
  return 'Run this configured workflow step'
}

interface EnrollmentCardEntry {
	id: string
	type: string
	label: string
	config: Record<string, unknown>
}

function enrollmentCards(node: WorkflowNode, events: BusinessEventType[] = [], primaryDoctype = 'Record'): EnrollmentCardEntry[] {
	const eventLabel = (topic: string) => events.find((event) => event.topic === topic)?.label || topic.split('.').map((part) => part.replaceAll('_', ' ')).join(' ')
	if (node.type === 'trigger.event' && Array.isArray(node.config.events)) {
		return node.config.events.flatMap((entry, index) => entry && typeof entry === 'object' ? [{
			id: String((entry as Record<string, unknown>).id || `event-${index + 1}`),
			type: 'trigger.event',
			label: eventLabel(String((entry as Record<string, unknown>).event_topic || '')) || 'Choose an event',
			config: { ...(entry as Record<string, unknown>), condition: node.config.condition || null },
		}] : [])
	}
	if (node.type === 'trigger.any' && Array.isArray(node.config.triggers)) {
		return node.config.triggers.flatMap((entry, index) => entry && typeof entry === 'object' ? [{
			id: String((entry as Record<string, unknown>).id || `trigger-${index + 1}`),
			type: String((entry as Record<string, unknown>).type || ''),
			label: String((entry as Record<string, unknown>).type || '') === 'trigger.document_insert'
				? `${primaryDoctype} created`
				: String((entry as Record<string, unknown>).type || '') === 'trigger.document_change'
					? `${primaryDoctype} changed`
					: String((entry as Record<string, unknown>).type || '') === 'trigger.event'
						? eventLabel(String((((entry as Record<string, unknown>).config || {}) as Record<string, unknown>).event_topic || '')) || 'Choose an event'
						: nodeLabels[String((entry as Record<string, unknown>).type || '') as NodeType] || 'Incomplete trigger',
			config: ((entry as Record<string, unknown>).config && typeof (entry as Record<string, unknown>).config === 'object' ? (entry as Record<string, unknown>).config : {}) as Record<string, unknown>,
		}] : [])
	}
	return [{
		id: node.id,
		type: node.type,
		label: node.type === 'trigger.document_insert'
			? `${primaryDoctype} created`
			: node.type === 'trigger.document_change'
				? `${primaryDoctype} changed`
				: node.type === 'trigger.event' && node.config.event_topic
					? eventLabel(String(node.config.event_topic))
					: nodeLabels[node.type] || node.type,
		config: node.config,
	}]
}

function enrollmentCardSummary(entry: EnrollmentCardEntry, primaryDoctype: string) {
	if (entry.type === 'trigger.event') {
		if (entry.config.event_topic === 'commerce.order.abandoned') {
			const value = Number(entry.config.abandoned_after_value || 24)
			const unit = entry.config.abandoned_after_unit === 'days' ? 'day' : 'hour'
			return `After ${value} ${unit}${value === 1 ? '' : 's'} without cart changes`
		}
		return entry.config.event_filter ? conditionSummary(entry.config.event_filter as Record<string, unknown>) : 'No event filters applied'
	}
	if (entry.type === 'trigger.document_insert') return entry.config.condition ? conditionSummary(entry.config.condition as Record<string, unknown>) : 'No record filters applied'
	if (entry.type === 'trigger.document_change') return Array.isArray(entry.config.watch_fields) && entry.config.watch_fields.length ? `${entry.config.watch_fields.length} watched field${entry.config.watch_fields.length === 1 ? '' : 's'}` : `Any ${primaryDoctype} change`
	if (entry.type === 'trigger.filter_criteria') return conditionSummary(entry.config.condition as Record<string, unknown> | undefined)
	return nodeSummary({ id: entry.id, type: entry.type as NodeType, type_version: 1, config: entry.config, position: { x: 0, y: 0 } }, primaryDoctype)
}

function entryConfigured(entry: EnrollmentCardEntry) {
	if (entry.type === 'trigger.event') return Boolean(String(entry.config.event_topic || '').trim())
	if (entry.type === 'trigger.filter_criteria') return Boolean(entry.config.condition)
	return true
}

export const EnrollmentBoundary = memo(({ data }: NodeProps<EnrollmentFlowNode>) => {
	const actions = useWorkflowActions()
	const { selectedNodeId, selectedTriggerGroupId } = useWorkflowEditor()
	const [chooserOpen, setChooserOpen] = useState(false)
	const [eventTypes, setEventTypes] = useState<BusinessEventType[]>([])
	const [eventTypesLoading, setEventTypesLoading] = useState(false)
	const [eventTypesError, setEventTypesError] = useState('')
	const [eventTypesEpoch, setEventTypesEpoch] = useState(0)
	const node = data.workflowNode
	const cards = enrollmentCards(node, eventTypes, data.primaryDoctype)
	const visibleCards = cards.length === 1 && !entryConfigured(cards[0]) ? [] : cards
	const canMutateCards = ['trigger.document_insert', 'trigger.document_change', 'trigger.event', 'trigger.any'].includes(node.type)
	const hasBusinessEventCards = cards.some((entry) => entry.type === 'trigger.event' && Boolean(entry.config.event_topic))
	useEffect(() => {
		if (!chooserOpen && !hasBusinessEventCards) return
		const controller = new AbortController()
		setEventTypesLoading(true)
		setEventTypesError('')
		void call<{ event_types: BusinessEventType[] }>('get_event_types', { primary_doctype: data.primaryDoctype, usage: 'trigger' }, false, controller.signal)
			.then((result) => setEventTypes(result.event_types || []))
			.catch((error: unknown) => { if (!controller.signal.aborted) setEventTypesError(error instanceof Error ? error.message : 'Could not load installed events.') })
			.finally(() => { if (!controller.signal.aborted) setEventTypesLoading(false) })
		return () => controller.abort()
	}, [chooserOpen, data.primaryDoctype, eventTypesEpoch, hasBusinessEventCards])
	useEffect(() => {
		if (!chooserOpen) return
		const closeOnEscape = (event: KeyboardEvent) => { if (event.key === 'Escape') setChooserOpen(false) }
		document.addEventListener('keydown', closeOnEscape)
		return () => document.removeEventListener('keydown', closeOnEscape)
	}, [chooserOpen])
	useEffect(() => { if (selectedNodeId !== node.id) setChooserOpen(false) }, [node.id, selectedNodeId])
	const updateCards = (next: EnrollmentCardEntry[]) => {
		if (node.type === 'trigger.any') actions.updateNode(node.id, { ...node.config, triggers: next.map((entry) => ({ id: entry.id, type: entry.type, config: entry.config })) }, `node:${node.id}:triggers`)
		if (node.type !== 'trigger.any') actions.replaceTrigger({
			type: 'trigger.any',
			type_version: 2,
			label: 'Event triggers',
			category: 'Triggers',
			description: 'Enroll when any configured event occurs.',
			default_config: { triggers: next.map((entry) => ({ id: entry.id === node.id ? `trigger-${crypto.randomUUID()}` : entry.id, type: entry.type, config: structuredClone(entry.config) })) },
			output_paths: ['default'],
		})
	}
	const duplicate = (entry: EnrollmentCardEntry) => {
		if (cards.length >= 20) return
		const id = `${node.type === 'trigger.event' ? 'event' : 'trigger'}-${crypto.randomUUID()}`
		const cloned = structuredClone(entry)
		cloned.id = id
		if (node.type === 'trigger.event') cloned.config.id = id
		updateCards([...cards, cloned])
		actions.selectTrigger(node.id, id)
	}
	const reorder = (index: number, direction: -1 | 1) => {
		const target = index + direction
		if (target < 0 || target >= cards.length) return
		const next = [...cards]
		;[next[index], next[target]] = [next[target], next[index]]
		updateCards(next)
	}
	const remove = (entry: EnrollmentCardEntry) => {
		if (cards.length === 1) {
			const id = entry.id
			const blank = node.type === 'trigger.event'
				? { ...entry, config: { id, event_topic: '', event_filter: null } }
				: { ...entry, type: 'trigger.event', label: 'Choose an event', config: { event_topic: '', event_filter: null, condition: null } }
			updateCards([blank])
			actions.select(node.id)
			return
		}
		updateCards(cards.filter((candidate) => candidate.id !== entry.id))
		actions.select(node.id)
	}
	const choose = (choice: EnrollmentTriggerChoice) => {
		const blankIndex = cards.findIndex((candidate) => !entryConfigured(candidate))
		if (!canMutateCards || (blankIndex < 0 && cards.length >= 20)) return
		const id = `trigger-${crypto.randomUUID()}`
		const event = choice.type === 'trigger.event' ? eventTypes.find((item) => item.topic === choice.topic) : undefined
		const entry: EnrollmentCardEntry = { id, type: choice.type, label: event?.label || (choice.type === 'trigger.document_insert' ? `${data.primaryDoctype} created` : choice.type === 'trigger.document_change' ? `${data.primaryDoctype} changed` : 'Choose an event'), config: choice.type === 'trigger.document_change' ? { watch_fields: [], condition: null } : choice.type === 'trigger.event' ? { event_topic: choice.topic || '', event_filter: null, condition: null } : { condition: null } }
		updateCards(blankIndex >= 0 ? cards.map((candidate, index) => index === blankIndex ? entry : candidate) : [...cards, entry])
		setChooserOpen(false)
		actions.selectTrigger(node.id, id)
	}
	return <section className="enrollment-boundary nodrag" style={{ width: workflowNodeVisualWidth(node) } as CSSProperties} data-selected={selectedNodeId === node.id ? 'true' : 'false'} data-invalid={data.issueCount > 0 ? 'true' : 'false'}>
		{canMutateCards && visibleCards.length > 0 && <div className="enrollment-boundary__or"><span />OR<span /></div>}
		<div className="enrollment-boundary__cards" data-empty={visibleCards.length === 0 ? 'true' : 'false'} data-mutable={canMutateCards ? 'true' : 'false'}>
			<div className="enrollment-boundary__trigger-list nowheel">
			{visibleCards.map((entry, index) => { const EntryIcon = nodeIcons[entry.type as NodeType] || Zap; return <article className="enrollment-trigger-card" data-active={selectedTriggerGroupId === entry.id ? 'true' : 'false'} data-incomplete={!entryConfigured(entry) ? 'true' : 'false'} key={entry.id} onClick={(event) => { event.stopPropagation(); setChooserOpen(false); actions.selectTrigger(node.id, entry.id) }}>
				<div className="enrollment-trigger-card__top"><span className="enrollment-trigger-card__icon"><EntryIcon size={15} /></span><strong>{entry.label}</strong>{!entryConfigured(entry) && <AlertTriangle size={12} aria-label="Needs setup" />}</div>
				<p>{enrollmentCardSummary(entry, data.primaryDoctype)}</p>
				{canMutateCards && <div className="enrollment-trigger-card__actions"><span><button type="button" title="Duplicate trigger" aria-label={`Duplicate trigger ${index + 1}`} onClick={(event) => { event.stopPropagation(); duplicate(entry) }}><Copy size={12} /></button><button type="button" title={cards.length === 1 ? 'Clear final trigger' : 'Delete trigger'} aria-label={`${cards.length === 1 ? 'Clear' : 'Delete'} trigger ${index + 1}`} onClick={(event) => { event.stopPropagation(); remove(entry) }}><Trash2 size={12} /></button></span><span><button type="button" disabled={index === 0} title="Move trigger left" aria-label={`Move trigger ${index + 1} left`} onClick={(event) => { event.stopPropagation(); reorder(index, -1) }}><ArrowLeft size={12} /></button><button type="button" disabled={index === cards.length - 1} title="Move trigger right" aria-label={`Move trigger ${index + 1} right`} onClick={(event) => { event.stopPropagation(); reorder(index, 1) }}><ArrowRight size={12} /></button></span></div>}
			</article> })}
			</div>
			{canMutateCards && <div className="enrollment-add-trigger__slot"><button type="button" className="enrollment-add-trigger" disabled={visibleCards.length >= 20} aria-expanded={chooserOpen} onClick={(event) => { event.stopPropagation(); actions.select(node.id); setChooserOpen((open) => !open) }}><Plus size={17} /><span><strong>Add new trigger</strong><small>{visibleCards.length}/20 configured</small></span></button>{chooserOpen && <EnrollmentTriggerChooser primaryDoctype={data.primaryDoctype} events={eventTypes} loading={eventTypesLoading} error={eventTypesError} onChoose={choose} onClose={() => setChooserOpen(false)} onRetry={() => setEventTypesEpoch((value) => value + 1)} />}</div>}
		</div>
		<div className="enrollment-boundary__connector" aria-hidden><span /></div>
		<Handle id="default" type="source" position={Position.Bottom} className="workflow-handle workflow-handle--guided" />
	</section>
})
EnrollmentBoundary.displayName = 'EnrollmentBoundary'

const WorkflowNodeCard = memo(({ data, selected }: NodeProps<WorkflowFlowNode>) => {
	const actions = useWorkflowActions()
	const node = data.workflowNode
	const branchOutputs = ['condition.if_else', 'condition.random_split', 'condition.deduplicate', 'condition.switch'].includes(node.type) || (node.type === 'delay.until_event' && (node.type_version < 2 || Boolean(node.config.branch_on_timeout))) ? nodeOutputHandles(node) : []
	const nodeWidth = branchOutputs.length > 3 ? Math.min(1440, Math.max(252, branchOutputs.length * 84)) : undefined
  const trigger = node.type.startsWith('trigger.')
  const Icon = nodeIcons[node.type] || Zap
  const kind = nodeKind(node.type)
  return (
	    <article className={`workflow-node workflow-node--${kind}`} style={nodeWidth ? { width: nodeWidth } : undefined} data-invalid={data.issueCount > 0 ? 'true' : 'false'} data-selected={selected ? 'true' : 'false'} data-manual-links={data.manualConnections ? 'true' : 'false'} data-connected={data.connected ? 'true' : 'false'}>
      <span className="workflow-node__rail" aria-hidden />
	  <div className="workflow-node__quick-actions nodrag nopan"><button type="button" title="Clone this action" aria-label={`Clone ${nodeLabels[node.type] || node.type}`} onClick={(event) => { event.stopPropagation(); actions.duplicateNode(node.id) }}><Copy size={12} /></button><DeleteWorkflowStepButton node={node} title="Delete this action" aria-label={`Delete ${nodeLabels[node.type] || node.type}`}><Trash2 size={12} /></DeleteWorkflowStepButton></div>
      {!trigger && <Handle type="target" position={Position.Top} className={`workflow-handle ${data.manualConnections ? '' : 'workflow-handle--guided'}`} />}
      <div className="px-4 pb-3.5 pt-4">
        <div className="flex items-start gap-3">
          <span className="workflow-node__icon grid size-9 shrink-0 place-items-center rounded-[10px]">
            <Icon size={17} strokeWidth={2.1} aria-hidden />
          </span>
          <div className="min-w-0 flex-1">
            <p className="workflow-node__eyebrow text-[10px] font-bold uppercase tracking-[0.12em]">{kind}{node.type === 'action.round_robin' ? node.type_version === 1 ? ' · legacy v1' : ' · rotating v2' : ''}</p>
            <h3 className="text-heading mt-0.5 truncate text-[13px] font-bold leading-5">{nodeLabels[node.type] || node.type}</h3>
          </div>
		  {!data.connected ? <span className="workflow-node__unconnected flex shrink-0 items-center gap-1 rounded-full px-2 py-1 text-[9px] font-bold" title="This step is not connected to the enrollment trigger"><Unplug size={10} />Not connected</span> : data.issueCount > 0 && <span className="flex shrink-0 items-center gap-1 rounded-full bg-red-50 px-2 py-1 text-[9px] font-bold text-red-600 dark:bg-red-500/15 dark:text-red-300" title={`${data.issueCount} validation issue${data.issueCount === 1 ? '' : 's'}`}><AlertTriangle size={10} />{data.issueCount}</span>}
        </div>
        <p className="text-muted mt-3 line-clamp-2 min-h-9 text-[11px] leading-[18px]">{nodeSummary(node, data.primaryDoctype)}</p>
		{data.metric && data.metric.reached > 0 && <div className="workflow-node__metric" title={`${data.metric.completed} completed, ${data.metric.failed} failed, ${data.metric.waiting} waiting`}><span>{data.metric.reached.toLocaleString()} reached</span>{data.metric.failed > 0 && <span className="workflow-node__metric-error">{data.metric.failed.toLocaleString()} failed</span>}</div>}
      </div>
		{branchOutputs.length ? (
			<div className="workflow-node__footer relative flex rounded-b-xl px-2 py-2 text-[9px] font-bold uppercase tracking-[0.04em]">
				{branchOutputs.map((output, index) => {
					const branchCount = Number(data.metric?.branches?.[output.handle] || 0)
					const branchTotal = Object.values(data.metric?.branches || {}).reduce((sum, value) => sum + Number(value || 0), 0)
					const percentage = branchTotal ? Math.round((branchCount / branchTotal) * 100) : 0
					return <span className="min-w-0 flex-1 truncate text-center" title={`${output.label}${branchTotal ? ` · ${branchCount} records (${percentage}%)` : ''}`} key={output.handle}>{output.label}{branchTotal ? <small>{percentage}%</small> : null}<Handle id={output.handle} type="source" position={Position.Bottom} className={`workflow-handle ${data.manualConnections ? '' : 'workflow-handle--guided'}`} style={{ left: `${((index + 0.5) / branchOutputs.length) * 100}%` }} /></span>
				})}
			</div>
      ) : !['end.complete', 'action.delete_record', 'action.go_to'].includes(node.type) ? (
        <div className="workflow-node__footer h-2 rounded-b-xl" aria-hidden>
          <Handle id="default" type="source" position={Position.Bottom} className={`workflow-handle ${data.manualConnections ? '' : 'workflow-handle--guided'}`} />
        </div>
      ) : (
        <div className="workflow-node__footer flex items-center gap-1.5 rounded-b-xl px-4 py-2 text-[10px] font-semibold text-emerald-600">
          <CheckCircle2 size={12} /> End of path
        </div>
      )}
    </article>
  )
})
WorkflowNodeCard.displayName = 'WorkflowNodeCard'

export const VirtualEndCard = memo(({ data }: NodeProps<VirtualEndFlowNode>) => {
	const actions = useWorkflowActions()
	const showPathLabel = data.sourceHandle !== 'default' || data.label.toLowerCase() !== 'next action'
	return <div className="workflow-path-end" data-default-path={showPathLabel ? 'false' : 'true'}>
		<Handle type="target" position={Position.Top} className="workflow-virtual-target" />
		{showPathLabel && <span className="workflow-path-end__label" title={data.label}>{data.label}</span>}
		<button
			type="button"
			className="workflow-path-end__add nodrag nopan"
			title={`Add a step to ${data.label}`}
			onClick={(event) => {
				event.stopPropagation()
				actions.beginInsert({ afterNodeId: data.sourceId, sourceHandle: data.sourceHandle, position: data.insertPosition, label: `After ${data.label}` })
			}}
		>
			<Plus size={15} /><span className="sr-only">Add step</span>
		</button>
		<span className="workflow-path-end__tail" aria-hidden />
		<span className="workflow-virtual-end"><CheckCircle2 size={12} />END</span>
	</div>
})
VirtualEndCard.displayName = 'VirtualEndCard'

const nodeTypes = { workflow: WorkflowNodeCard, enrollment: EnrollmentBoundary, virtualEnd: VirtualEndCard }

const GuidedEdge = memo((props: EdgeProps) => {
	const actions = useWorkflowActions()
	const [edgePath, labelX, labelY] = getSmoothStepPath({
		sourceX: props.sourceX,
		sourceY: props.sourceY,
		sourcePosition: props.sourcePosition,
		targetX: props.targetX,
		targetY: props.targetY,
		targetPosition: props.targetPosition,
	})
	const edgeColor = typeof props.style?.stroke === 'string' ? props.style.stroke : '#ff6b47'
	return <>
		<BaseEdge id={props.id} path={edgePath} markerEnd={props.markerEnd} style={props.style} />
		<EdgeLabelRenderer>
			<button
				type="button"
				className="workflow-edge-add nodrag nopan"
				style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`, '--workflow-edge-color': edgeColor } as CSSProperties}
				aria-label="Insert a step between these steps"
				title="Insert step — connections update automatically"
				onClick={(event) => {
					event.stopPropagation()
					actions.beginInsert({ edgeId: props.id, position: { x: labelX - 126, y: labelY - 70 }, label: 'Between these steps' })
				}}
			>
				<Plus size={13} />
			</button>
		</EdgeLabelRenderer>
	</>
})
GuidedEdge.displayName = 'GuidedEdge'

const edgeTypes = { guided: GuidedEdge }

function distanceToSegment(point: { x: number; y: number }, start: { x: number; y: number }, end: { x: number; y: number }) {
	const dx = end.x - start.x
	const dy = end.y - start.y
	if (!dx && !dy) return Math.hypot(point.x - start.x, point.y - start.y)
	const ratio = Math.max(0, Math.min(1, ((point.x - start.x) * dx + (point.y - start.y) * dy) / (dx * dx + dy * dy)))
	return Math.hypot(point.x - (start.x + ratio * dx), point.y - (start.y + ratio * dy))
}

export function WorkflowCanvas() {
  const { workflowId, graph, validation, publication } = useWorkflowDocument()
  const { selectedNodeId } = useWorkflowEditor()
  const actions = useWorkflowActions()
	const manualConnections = false
	const [canvasMetrics, setCanvasMetrics] = useState<CanvasMetricsResponse | null>(null)
	useEffect(() => {
		if (!workflowId || !publication.active_version) {
			setCanvasMetrics(null)
			return
		}
		const controller = new AbortController()
		void call<CanvasMetricsResponse>('get_canvas_metrics', { workflow_id: workflowId, workflow_version: publication.active_version }, false, controller.signal)
			.then(setCanvasMetrics)
			.catch(() => setCanvasMetrics(null))
		return () => controller.abort()
	}, [publication.active_version, workflowId])
	const metricsByNode = useMemo(() => new Map((canvasMetrics?.nodes || []).map((metric) => [metric.node_id, metric])), [canvasMetrics])
	const connectedNodeIds = useMemo(() => graph ? reachableWorkflowNodeIds(graph) : new Set<string>(), [graph])
  const dragging = useRef(false)
  const flow = useRef<ReactFlowInstance<FlowNode> | null>(null)
	const implicitEnds = useMemo(() => {
		const connected = new Set((graph?.edges || []).map((edge) => `${edge.source}:${edge.source_handle}`))
		return (graph?.nodes || []).flatMap((node) => {
			const outputs = nodeOutputHandles(node)
			const width = workflowNodeVisualWidth(node)
			// Enrollment cards are taller than ordinary action cards because their
			// OR rail and convergence connector are part of the rendered boundary.
			// Keep the derived + / END target below that boundary instead of placing
			// its label on top of the horizontal join line.
			const endpointOffsetY = node.id === graph?.start_node_id ? 250 : 190
			return outputs.flatMap((output, index) => connected.has(`${node.id}:${output.handle}`) ? [] : [{
				sourceId: node.id,
				sourceHandle: output.handle,
				label: output.label,
				position: {
					x: (node.position?.x || 120) + ((index + 0.5) / outputs.length) * width - 48,
					y: (node.position?.y || 120) + endpointOffsetY,
				},
				insertPosition: {
					x: (node.position?.x || 120) + ((index + 0.5) / outputs.length) * width - 126,
					y: (node.position?.y || 120) + endpointOffsetY,
				},
			}])
		})
	}, [graph?.edges, graph?.nodes, graph?.start_node_id])
  const mappedNodes = useMemo<FlowNode[]>(
		() => [
			...(graph?.nodes || []).map((node): WorkflowFlowNode | EnrollmentFlowNode => node.id === graph?.start_node_id ? {
				id: node.id,
				type: 'enrollment',
				position: node.position || { x: 120, y: 120 },
				data: { workflowNode: node, primaryDoctype: graph?.primary_doctype || 'record', issueCount: validation.filter((issue) => issue.node_id === node.id).length, totalEnrollments: canvasMetrics?.total_enrollments },
			} : {
				id: node.id,
				type: 'workflow',
				position: node.position || { x: 120, y: 120 },
				data: { workflowNode: node, primaryDoctype: graph?.primary_doctype || 'record', issueCount: validation.filter((issue) => issue.node_id === node.id).length, manualConnections, connected: connectedNodeIds.has(node.id), metric: metricsByNode.get(node.id) },
				deletable: workflowNodeSourceHandles(node).length <= 1,
			}),
			...implicitEnds.map((endpoint): VirtualEndFlowNode => ({
				id: `virtual-end:${endpoint.sourceId}:${endpoint.sourceHandle}`,
				type: 'virtualEnd',
				position: endpoint.position,
				data: { sourceId: endpoint.sourceId, sourceHandle: endpoint.sourceHandle, label: endpoint.label, insertPosition: endpoint.insertPosition },
				draggable: false,
				selectable: false,
				deletable: false,
			})),
		],
		[canvasMetrics?.total_enrollments, connectedNodeIds, graph?.nodes, graph?.primary_doctype, graph?.start_node_id, implicitEnds, manualConnections, metricsByNode, validation],
  )
  const [nodes, setNodes] = useState(mappedNodes)

  useEffect(() => {
    if (!dragging.current) setNodes(mappedNodes)
  }, [mappedNodes])

  useEffect(() => {
    if (!selectedNodeId) return
    const selected = flow.current?.getNode(selectedNodeId)
    if (selected) void flow.current?.fitView({ nodes: [selected], padding: 0.55, duration: 260, maxZoom: 1.1 })
  }, [mappedNodes.length, selectedNodeId])

	const edges = useMemo(() => {
		const palette = ['#6d5ce8', '#078a72', '#d46a16', '#2474c5', '#b54691', '#547080']
		const edgeColor = (sourceId: string, sourceHandle: string) => {
			const source = graph?.nodes.find((node) => node.id === sourceId)
			const outputs = source ? nodeOutputHandles(source) : []
			return outputs.length > 1 ? palette[Math.max(0, outputs.findIndex((output) => output.handle === sourceHandle)) % palette.length] : '#75899b'
		}
		return [...(graph?.edges || []).map((edge) => ({
      id: edge.id,
		type: 'guided',
      source: edge.source,
      target: edge.target,
      sourceHandle: edge.source_handle,
      markerEnd: { type: MarkerType.ArrowClosed, width: 15, height: 15 },
      animated: selectedNodeId === edge.source || selectedNodeId === edge.target,
	  deletable: manualConnections,
	  style: { stroke: edgeColor(edge.source, edge.source_handle), strokeWidth: 1.8 },
			})), ...implicitEnds.map((endpoint) => ({
				id: `virtual-end-edge:${endpoint.sourceId}:${endpoint.sourceHandle}`,
				source: endpoint.sourceId,
				target: `virtual-end:${endpoint.sourceId}:${endpoint.sourceHandle}`,
				sourceHandle: endpoint.sourceHandle,
			selectable: false,
			deletable: false,
			style: { stroke: edgeColor(endpoint.sourceId, endpoint.sourceHandle), strokeWidth: 1.45, strokeDasharray: '5 5' },
		}))]
	}, [graph?.edges, graph?.nodes, implicitEnds, manualConnections, selectedNodeId])

  const onNodesChange = (changes: NodeChange<FlowNode>[]) => {
	const safeChanges = changes.filter((change) => change.type === 'add' || (!change.id.startsWith('virtual-end:') && (change.type !== 'remove' || change.id !== graph?.start_node_id)))
    setNodes((current) => applyNodeChanges(safeChanges, current))
    actions.removeNodes(safeChanges.filter((change) => change.type === 'remove').map((change) => change.id))
  }

	const connect = (connection: Connection) => {
    if (!connection.source || !connection.target) return
    const source = graph?.nodes.find((node) => node.id === connection.source)
    const handle = connection.sourceHandle || 'default'
		const allowed = source?.type === 'condition.if_else' ? source.type_version >= 2
			? [...(Array.isArray(source.config.branches) ? source.config.branches : []).flatMap((branch) => typeof branch === 'object' && branch ? [String((branch as Record<string, unknown>).handle || '')] : []), 'none']
			: ['true', 'false']
			: source?.type === 'condition.random_split' ? (Array.isArray(source.config.branches) ? source.config.branches : []).flatMap((branch) => typeof branch === 'object' && branch ? [String((branch as Record<string, unknown>).handle || '')] : [])
			: source?.type === 'condition.deduplicate' ? ['duplicate', 'unique']
			: source?.type === 'delay.until_event' ? source.type_version >= 2 && !source.config.branch_on_timeout ? ['default'] : ['event', 'timeout']
					: source?.type === 'condition.switch' ? [...(Array.isArray(source.config.cases) ? source.config.cases : []).flatMap((item) => typeof item === 'object' && item ? [String((item as Record<string, unknown>).handle || '')] : []), 'default'] : ['default']
		if (!allowed.includes(handle)) return
    actions.connect({ source: connection.source, target: connection.target, source_handle: handle })
  }

	useEffect(() => {
		const keyboardClipboard = (event: KeyboardEvent) => {
			const target = event.target as HTMLElement | null
			if (target?.matches('input, textarea, select, [contenteditable="true"]')) return
			if (!(event.ctrlKey || event.metaKey) || !selectedNodeId) return
			if (event.key.toLowerCase() === 'c') {
				event.preventDefault()
				if (event.shiftKey) actions.copySection(selectedNodeId)
				else actions.copyNode(selectedNodeId)
			}
			if (event.key.toLowerCase() === 'v') {
				event.preventDefault()
				if (event.shiftKey) actions.pasteSection()
				else actions.pasteNode()
			}
		}
		document.addEventListener('keydown', keyboardClipboard)
		return () => document.removeEventListener('keydown', keyboardClipboard)
	}, [actions, selectedNodeId])

	const dropCatalogNode = (event: DragEvent) => {
		event.preventDefault()
		const raw = event.dataTransfer.getData('application/x-finbyz-workflow-node')
		if (!raw || !flow.current || !graph) return
		let item: NodeCatalogItem
		try {
			item = JSON.parse(raw) as NodeCatalogItem
		} catch {
			return
		}
		if (item.type.startsWith('trigger.') || item.authoring_hidden) return
		const point = flow.current.screenToFlowPosition({ x: event.clientX, y: event.clientY })
		const position = { x: point.x - 126, y: point.y - 70 }
		const nearestEndpoint = implicitEnds
			.map((endpoint) => ({ endpoint, distance: Math.hypot(point.x - (endpoint.position.x + 48), point.y - (endpoint.position.y + 28)) }))
			.sort((left, right) => left.distance - right.distance)[0]
		const nodeById = new Map(graph.nodes.map((node) => [node.id, node]))
		const nearestEdge = graph.edges
			.map((edge) => {
				const sourceNode = nodeById.get(edge.source)
				const targetNode = nodeById.get(edge.target)
				if (!sourceNode?.position || !targetNode?.position) return { edge, distance: Number.POSITIVE_INFINITY }
				return { edge, distance: distanceToSegment(point, { x: sourceNode.position.x + workflowNodeVisualWidth(sourceNode) / 2, y: sourceNode.position.y + 145 }, { x: targetNode.position.x + workflowNodeVisualWidth(targetNode) / 2, y: targetNode.position.y }) }
			})
			.sort((left, right) => left.distance - right.distance)[0]
		const nearestParent = graph.nodes
			.filter((node) => node.position && point.y >= node.position.y + 80)
			.map((node) => ({ node, distance: Math.hypot(point.x - (node.position.x + workflowNodeVisualWidth(node) / 2), point.y - (node.position.y + 150)) }))
			.filter((candidate) => candidate.distance < 300)
			.sort((left, right) => left.distance - right.distance)[0]
		actions.addNode(item, {
			position,
			edgeId: nearestEndpoint?.distance < 110 ? undefined : nearestEdge?.distance < 85 ? nearestEdge.edge.id : undefined,
			afterNodeId: nearestEndpoint?.distance < 110 ? nearestEndpoint.endpoint.sourceId : nearestEdge?.distance < 85 ? undefined : nearestParent?.node.id,
			sourceHandle: nearestEndpoint?.distance < 110 ? nearestEndpoint.endpoint.sourceHandle : undefined,
		})
	}

  return (
		<div className="workflow-canvas relative h-full min-h-0" aria-label="Workflow canvas" onDragOver={(event) => { if (event.dataTransfer.types.includes('application/x-finbyz-workflow-node')) { event.preventDefault(); event.dataTransfer.dropEffect = 'copy' } }} onDrop={dropCatalogNode}>
		<div className="absolute right-4 top-4 z-20 flex items-center gap-2">
			<button type="button" className="workflow-canvas-tool" onClick={() => actions.autoArrange()} title="Arrange steps into clear branch lanes"><LayoutTemplate size={12} /> Tidy layout</button>
		</div>
      <ReactFlow<FlowNode>
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
		edgeTypes={edgeTypes}
        fitView
        fitViewOptions={{ padding: 0.24 }}
        minZoom={0.25}
        maxZoom={1.8}
        defaultEdgeOptions={{ type: 'smoothstep' }}
        deleteKeyCode={['Backspace', 'Delete']}
        onNodesChange={onNodesChange}
        onInit={(instance) => { flow.current = instance }}
			onNodeClick={(_, node) => { if (node.type === 'workflow' || node.type === 'enrollment') actions.select(node.id) }}
        onPaneClick={() => actions.select()}
        onNodeDragStart={() => { dragging.current = true }}
			onNodeDragStop={(_, node) => {
			  dragging.current = false
			  if (node.type !== 'workflow' || !graph) return
			  const point = { x: node.position.x + 126, y: node.position.y + 72 }
			  const nodeById = new Map(graph.nodes.map((item) => [item.id, item]))
			  const nearestEdge = graph.edges
				.filter((edge) => edge.source !== node.id && edge.target !== node.id)
				.map((edge) => {
				  const sourceNode = nodeById.get(edge.source)
				  const targetNode = nodeById.get(edge.target)
				  if (!sourceNode?.position || !targetNode?.position) return { edge, distance: Number.POSITIVE_INFINITY }
				  return { edge, distance: distanceToSegment(point, { x: sourceNode.position.x + workflowNodeVisualWidth(sourceNode) / 2, y: sourceNode.position.y + 145 }, { x: targetNode.position.x + workflowNodeVisualWidth(targetNode) / 2, y: targetNode.position.y }) }
				})
				.sort((left, right) => left.distance - right.distance)[0]
			  if (nearestEdge?.distance < 70) actions.relocateNode(node.id, nearestEdge.edge.id, node.position)
			  else actions.moveNode(node.id, node.position)
			}}
		nodesConnectable={manualConnections}
		edgesReconnectable={manualConnections}
		onConnect={manualConnections ? connect : undefined}
			onEdgesDelete={(deleted) => actions.removeEdges(deleted.map((edge) => edge.id).filter((id) => !id.startsWith('virtual-end-edge:')))}
        isValidConnection={(connection) => connection.source !== connection.target}
        proOptions={{ hideAttribution: true }}
      >
        <Background variant={BackgroundVariant.Dots} gap={24} size={1.25} color="var(--canvas-dot)" />
        <MiniMap className="hidden sm:block" pannable zoomable position="bottom-right" maskColor="rgb(25 39 51 / 0.06)" nodeColor="#ff8a6d" />
        <Controls position="bottom-left" showInteractive={false} />
      </ReactFlow>
    </div>
  )
}
