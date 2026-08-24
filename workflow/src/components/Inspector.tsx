import {
  AlertTriangle,
  CheckCircle2,
	ChevronDown,
	ChevronUp,
	Copy,
	Ellipsis,
	ExternalLink,
	Eye,
	GripVertical,
  Network,
  Info,
	LoaderCircle,
	MailCheck,
	Monitor,
	PanelRightOpen,
	PanelRightClose,
  Plus,
  Settings2,
	Sparkles,
	Smartphone,
  Trash2,
  X,
} from 'lucide-react'
import { type KeyboardEvent, type PointerEvent as ReactPointerEvent, type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { AsyncCombobox, type ComboboxOption } from './AsyncCombobox'
import { MultiValueInput } from './MultiValueInput'
import { HelpTooltip } from './HelpTooltip'
import { DeleteWorkflowStepButton } from './DeleteWorkflowStepButton'
import { useDialogA11y } from './useDialogA11y'
import { call, fetchFieldCatalog, invalidateMetadataCaches, searchDoctypes, searchLink } from '../lib/api'
import { availableOutputNodes, conditionToFilterGroups, emptyPredicate, filterGroupsToCondition, isRequiredAuthoringValueMissing, outputCatalog, parseCondition, parseWebhookPayload, type NodeOutputCatalog } from '../lib/inspectorAuthoring'
import { useWorkflowActions, useWorkflowDocument, useWorkflowEditor } from '../state/WorkflowContext'
import type { BusinessEventType, ConditionExpression, ConditionPredicate, FieldCatalogItem, NodeCatalogItem, NodeConfig, ValidationIssue, WorkflowNode, WorkflowObjectProfile, WorkflowValueSpec } from '../types'

import {
  inputClass,
  labelClass,
  nodeLabels,
  nodeIcons,
  InspectorSection,
  Hint,
  FieldPicker,
  ConditionEditor,
	ConditionExpressionEditor,
  AssignmentEditor,
  ValueSourceEditor,
} from './InspectorHelpers'

const durationUnits = [
	{ value: 'seconds', label: 'Seconds', multiplier: 1 },
	{ value: 'minutes', label: 'Minutes', multiplier: 60 },
	{ value: 'hours', label: 'Hours', multiplier: 3600 },
	{ value: 'days', label: 'Days', multiplier: 86400 },
	{ value: 'weeks', label: 'Weeks', multiplier: 604800 },
] as const
const businessDayUnit = { value: 'business_days', label: 'Business days', multiplier: 86400 } as const

interface InspectorProps {
	width: number
	minWidth: number
	maxWidth: number
	expanded: boolean
	onWidthChange(width: number): void
	onToggleExpanded(): void
}

export function InspectorResizeHandle({ width, minWidth, maxWidth, onWidthChange }: Pick<InspectorProps, 'width' | 'minWidth' | 'maxWidth' | 'onWidthChange'>) {
	const startResize = (event: ReactPointerEvent<HTMLButtonElement>) => {
		event.preventDefault()
		event.currentTarget.focus()
		const startX = event.clientX
		const startWidth = width
		document.body.classList.add('inspector-resizing')
		const resize = (moveEvent: PointerEvent) => onWidthChange(startWidth + startX - moveEvent.clientX)
		const stop = () => {
			document.body.classList.remove('inspector-resizing')
			window.removeEventListener('pointermove', resize)
			window.removeEventListener('pointerup', stop)
			window.removeEventListener('pointercancel', stop)
			window.removeEventListener('blur', stop)
		}
		window.addEventListener('pointermove', resize)
		window.addEventListener('pointerup', stop)
		window.addEventListener('pointercancel', stop)
		window.addEventListener('blur', stop)
	}
	const resizeWithKeyboard = (event: KeyboardEvent<HTMLButtonElement>) => {
		if (event.key === 'ArrowLeft') onWidthChange(width + 24)
		else if (event.key === 'ArrowRight') onWidthChange(width - 24)
		else if (event.key === 'Home') onWidthChange(minWidth)
		else if (event.key === 'End') onWidthChange(maxWidth)
		else return
		event.preventDefault()
	}
	return <button type="button" className="inspector-resize-handle max-lg:hidden" role="separator" aria-label="Resize step settings sidebar" aria-orientation="vertical" aria-valuemin={minWidth} aria-valuemax={maxWidth} aria-valuenow={width} aria-valuetext={`${width} pixels wide`} title="Drag this edge left or right · double-click to reset" onPointerDown={startResize} onKeyDown={resizeWithKeyboard} onDoubleClick={() => onWidthChange(420)}><span className="inspector-resize-grip"><GripVertical size={15} /></span></button>
}

function inferredDuration(seconds: number) {
	for (const unit of [...durationUnits].reverse()) {
		if (seconds >= unit.multiplier && seconds % unit.multiplier === 0) return { duration: seconds / unit.multiplier, unit: unit.value }
	}
	return { duration: seconds, unit: 'seconds' }
}

function DurationEditor({ seconds, initialUnit, allowBusinessDays = false, onChange }: { seconds: number; initialUnit?: string; allowBusinessDays?: boolean; onChange(seconds: number, duration: number, unit: string): void }) {
	const inferred = inferredDuration(seconds || 3600)
	const units = allowBusinessDays ? [...durationUnits, businessDayUnit] : durationUnits
	const [unit, setUnit] = useState(initialUnit === 'business_days' && allowBusinessDays ? 'business_days' : inferred.unit)
	const multiplier = units.find((item) => item.value === unit)?.multiplier || 1
	const duration = seconds / multiplier
	const updateDuration = (nextDuration: number, nextUnit = unit) => {
		const nextMultiplier = units.find((item) => item.value === nextUnit)?.multiplier || 1
		onChange(Math.round(nextDuration * nextMultiplier), nextDuration, nextUnit)
	}
	return <div className="grid grid-cols-[minmax(0,1fr)_130px] gap-2"><div><label className={labelClass}>Duration</label><input type="number" min={unit === 'seconds' ? 60 : 1} step={1} className={inputClass} value={Number.isFinite(duration) ? duration : 1} onChange={(event) => updateDuration(Number(event.target.value))} /></div><div><label className={labelClass}>Unit</label><select className={inputClass} value={unit} onChange={(event) => { const nextUnit = event.target.value; setUnit(nextUnit as typeof unit); updateDuration(duration, nextUnit) }}>{units.map((item) => <option value={item.value} key={item.value}>{item.label}</option>)}</select></div></div>
}

interface EmailTemplateSummary {
	name: string
	subject: string
	mode: string
	preheader?: string
	reference_doctype?: string
	builder_route?: string
	desk_route: string
}

interface EmailPreviewResult {
	subject: string
	html: string
	preheader?: string
	email_template?: string
	bytes: number
}

export function SendEmailEditor({
	config,
	workflowId,
	primaryDoctype,
	update,
	recipientEditor,
	subjectOverrideEditor,
	subjectEditor,
	messageEditor,
}: {
	config: NodeConfig
	workflowId: string
	primaryDoctype: string
	update(config: NodeConfig, key: string): void
	recipientEditor: ReactNode
	subjectOverrideEditor: ReactNode
	subjectEditor: ReactNode
	messageEditor: ReactNode
}) {
	const contentMode = String(config.content_mode || (config.email_template ? 'template' : 'inline'))
	const templateName = String(config.email_template || '')
	const [template, setTemplate] = useState<EmailTemplateSummary | null>(null)
	const [sampleRecord, setSampleRecord] = useState('')
	const [preview, setPreview] = useState<EmailPreviewResult | null>(null)
	const [previewLoading, setPreviewLoading] = useState(false)
	const [previewError, setPreviewError] = useState('')
	const [previewDevice, setPreviewDevice] = useState<'desktop' | 'mobile'>('desktop')
	const [testRecipient, setTestRecipient] = useState(window.frappe?.boot?.user?.includes('@') ? window.frappe.boot.user : '')
	const [testState, setTestState] = useState('')
	const [creating, setCreating] = useState(false)
	const [newTemplateName, setNewTemplateName] = useState('')
	const [newTemplateSubject, setNewTemplateSubject] = useState('')
	const canDesign = new Set(window.frappe?.boot?.roles || []).has('Email Designer') || new Set(window.frappe?.boot?.roles || []).has('System Manager')
	const closePreview = useCallback(() => setPreview(null), [])
	const previewDialogRef = useDialogA11y(Boolean(preview), closePreview, 'Email preview')

	useEffect(() => {
		setTemplate(null)
		if (!templateName || contentMode !== 'template') return
		let active = true
		void call<EmailTemplateSummary>('get_workflow_email_template', { workflow_id: workflowId, template_name: templateName })
			.then((value) => { if (active) setTemplate(value) })
			.catch(() => { if (active) setTemplate(null) })
		return () => { active = false }
	}, [contentMode, templateName, workflowId])

	useEffect(() => {
		if (primaryDoctype === 'Lead' || !String(config.subscription_topic || '').trim()) return
		const nextConfig = { ...config }
		delete nextConfig.subscription_topic
		update(nextConfig, 'subscription_topic:remove-inapplicable')
	}, [config, primaryDoctype, update])

	const previewEmail = async () => {
		setPreviewLoading(true)
		setPreviewError('')
		try {
			setPreview(await call<EmailPreviewResult>('preview_workflow_email', { workflow_id: workflowId, config, record_name: sampleRecord || undefined }, true))
		} catch (error) {
			setPreviewError(error instanceof Error ? error.message : 'Unable to preview this email')
		} finally {
			setPreviewLoading(false)
		}
	}
	const sendTest = async () => {
		setTestState('Sending…')
		try {
			const result = await call<{ recipient: string; email_queue: string }>('send_workflow_test_email', { workflow_id: workflowId, config, recipient: testRecipient, record_name: sampleRecord || undefined }, true)
			setTestState(`Queued for ${result.recipient}`)
		} catch (error) {
			setTestState(error instanceof Error ? error.message : 'Test email failed')
		}
	}
	const createTemplate = async () => {
		setCreating(true)
		setTestState('')
		try {
			const result = await call<EmailTemplateSummary>('create_workflow_email_template', { workflow_id: workflowId, template_name: newTemplateName, subject: newTemplateSubject }, true)
			setTemplate(result)
			update({ ...config, content_mode: 'template', email_template: result.name }, 'email_template:create')
			setNewTemplateName('')
			setNewTemplateSubject('')
			if (result.builder_route) window.open(result.builder_route, '_blank', 'noopener,noreferrer')
		} catch (error) {
			setTestState(error instanceof Error ? error.message : 'Unable to create visual template')
		} finally {
			setCreating(false)
		}
	}

	return <>
		<InspectorSection title="Email content" description="Use the shared visual Email Template Builder for reusable branded emails, or compose a quick one-off message.">
			<div className="grid grid-cols-2 gap-2">
				<button type="button" className={`rounded-xl border p-3 text-left ${contentMode === 'template' ? 'border-brand-300 bg-brand-50 text-brand-800 dark:bg-brand-500/10 dark:text-brand-200' : 'border-[var(--border-color)]'}`} onClick={() => update({ ...config, content_mode: 'template' }, 'content_mode')}><strong className="block text-[10.5px]">Email Template</strong><span className="mt-1 block text-[9px] opacity-75">Visual builder, reusable</span></button>
				<button type="button" className={`rounded-xl border p-3 text-left ${contentMode === 'inline' ? 'border-brand-300 bg-brand-50 text-brand-800 dark:bg-brand-500/10 dark:text-brand-200' : 'border-[var(--border-color)]'}`} onClick={() => update({ ...config, content_mode: 'inline', subject: config.subject || { kind: 'literal', value: '' }, message: config.message || { kind: 'literal', value: '' } }, 'content_mode')}><strong className="block text-[10.5px]">Quick email</strong><span className="mt-1 block text-[9px] opacity-75">Simple one-off content</span></button>
			</div>
			{contentMode === 'template' ? <>
				<div data-config-path="email_template"><label className={labelClass}>Automated Email Template</label><AsyncCombobox ariaLabel="Automated Email Template" value={templateName} onChange={(email_template) => update({ ...config, content_mode: 'template', email_template }, 'email_template')} loadOptions={(search) => call<{ rows: EmailTemplateSummary[] }>('list_email_templates', { workflow_id: workflowId, search, page_length: 30 }).then((result) => result.rows.map((row) => ({ value: row.name, label: row.name, description: `${row.mode} · ${row.subject || 'No subject'}` })))} placeholder="Search standard and visual templates…" /></div>
				{template && <div className="rounded-xl border border-emerald-200 bg-emerald-50/70 p-3 dark:border-emerald-800 dark:bg-emerald-500/10"><div className="flex items-start justify-between gap-3"><div className="min-w-0"><span className="text-[8.5px] font-bold uppercase tracking-[0.12em] text-emerald-700 dark:text-emerald-300">{template.mode} template</span><strong className="text-heading mt-1 block truncate text-[10.5px]">{template.subject}</strong>{template.preheader && <span className="text-muted mt-1 line-clamp-2 block text-[9px]">{template.preheader}</span>}</div><a className="icon-button shrink-0" href={template.builder_route || template.desk_route} target="_blank" rel="noreferrer" aria-label={template.builder_route ? 'Open visual email builder' : 'Open Email Template'} title={template.builder_route ? 'Open visual email builder' : 'Open Email Template'}><ExternalLink size={13} /></a></div></div>}
				{subjectOverrideEditor}
				{canDesign && <details className="rounded-xl border border-dashed border-[var(--border-color)] p-3"><summary className="cursor-pointer text-[10px] font-bold text-brand-700 dark:text-brand-300">Create a new visual email</summary><div className="mt-3 space-y-2"><div><label className={labelClass}>Template name</label><input className={inputClass} value={newTemplateName} onChange={(event) => setNewTemplateName(event.target.value)} placeholder="Welcome email" /></div><div><label className={labelClass}>Starting subject</label><input className={inputClass} value={newTemplateSubject} onChange={(event) => setNewTemplateSubject(event.target.value)} placeholder="Welcome to our company" /></div><button type="button" className="btn-core btn-secondary w-full !text-[10px]" disabled={creating || !newTemplateName.trim() || !newTemplateSubject.trim()} onClick={() => void createTemplate()}>{creating ? <LoaderCircle className="animate-spin" size={12} /> : <Sparkles size={12} />}Create and open visual builder</button></div></details>}
			</> : <>{subjectEditor}{messageEditor}<label className="text-body flex items-center gap-2 text-[10.5px]"><input type="checkbox" checked={Boolean(config.raw_html)} onChange={(event) => update({ ...config, raw_html: event.target.checked ? 1 : 0 }, 'raw_html')} />Treat message as prebuilt HTML</label></>}
		</InspectorSection>
		<InspectorSection title="Recipient and sending details" description="Choose who receives the message and optionally override workflow-level sender defaults.">
			{recipientEditor}
			{primaryDoctype === 'Lead' ? <div data-config-path="subscription_topic"><label className={labelClass}>Reach subscription topic <span className="font-normal">(optional)</span></label><AsyncCombobox ariaLabel="Reach subscription topic" value={String(config.subscription_topic || '')} onChange={(subscription_topic) => update({ ...config, subscription_topic }, 'subscription_topic')} loadOptions={(search) => searchLink('Subscription Topic', search, { filters: { disabled: 0 }, pageLength: 20 }).then((rows) => rows.map((row) => ({ value: row.value, label: row.label || row.value, description: row.description })))} placeholder="Search active Reach topics…" /><Hint>Global Frappe and CRM opt-outs are always enforced. Choose a topic to also respect this Lead’s FinbyzReach campaign preferences. Workflow emails do not alter Reach’s preference page.</Hint></div> : <Hint>The unsubscribe link globally opts out this recipient and triggers Email unsubscribed for this exact {primaryDoctype || 'record'}. Existing global and record-specific opt-outs are also enforced. FinbyzReach topics are Lead-only.</Hint>}
			<div className="grid grid-cols-2 gap-2"><div><label className={labelClass}>From name <span className="font-normal">(optional)</span></label><input className={inputClass} value={String(config.sender_name || '')} onChange={(event) => update({ ...config, sender_name: event.target.value }, 'sender_name')} placeholder="Workflow default" /></div><div><label className={labelClass}>From email <span className="font-normal">(optional)</span></label><AsyncCombobox ariaLabel="From email" value={String(config.sender_email || '')} onChange={(sender_email) => update({ ...config, sender_email }, 'sender_email')} loadOptions={(search) => call<{ rows: ComboboxOption[] }>('list_email_senders', { search, page_length: 20 }).then((result) => result.rows)} placeholder="Use workflow default" /></div></div>
			<div><label className={labelClass}>Reply-To <span className="font-normal">(optional)</span></label><input type="email" className={inputClass} value={String(config.reply_to || '')} onChange={(event) => update({ ...config, reply_to: event.target.value }, 'reply_to')} placeholder="replies@example.com" /></div>
		</InspectorSection>
		<InspectorSection title="Preview and test" description={`Personalize the email using a real ${primaryDoctype || 'record'}, preview desktop/mobile output, then send a controlled test to yourself.`}>
			<div><label className={labelClass}>Preview as {primaryDoctype || 'record'} <span className="font-normal">(optional)</span></label><AsyncCombobox ariaLabel={`Preview as ${primaryDoctype || 'record'}`} value={sampleRecord} onChange={setSampleRecord} loadOptions={(search) => searchLink(primaryDoctype, search, { pageLength: 15 }).then((rows) => rows.map((row) => ({ value: row.value, label: row.label || row.value, description: row.description })))} placeholder={`Search ${primaryDoctype || 'records'}…`} /></div>
			<div className="grid grid-cols-2 gap-2"><button type="button" className="btn-core btn-secondary !text-[10px]" disabled={previewLoading || (contentMode === 'template' && !templateName)} onClick={() => void previewEmail()}>{previewLoading ? <LoaderCircle className="animate-spin" size={12} /> : <Eye size={12} />}Preview email</button><button type="button" className="btn-core btn-secondary !text-[10px]" disabled={!testRecipient.trim() || (contentMode === 'template' && !templateName)} onClick={() => void sendTest()}><MailCheck size={12} />Send test</button></div>
			<div><label className={labelClass}>Send test to</label><input type="email" className={inputClass} value={testRecipient} onChange={(event) => setTestRecipient(event.target.value)} placeholder="you@example.com" /></div>
			{testState && <p className={`rounded-lg border px-2.5 py-2 text-[9.5px] ${testState.startsWith('Queued') ? 'border-emerald-200 bg-emerald-50 text-emerald-800 dark:bg-emerald-500/10 dark:text-emerald-200' : 'border-amber-200 bg-amber-50 text-amber-800 dark:bg-amber-500/10 dark:text-amber-200'}`}>{testState}</p>}
			{previewError && <p className="rounded-lg border border-red-200 bg-red-50 px-2.5 py-2 text-[9.5px] text-red-700 dark:bg-red-500/10 dark:text-red-300">{previewError}</p>}
			<Hint>Test sends are prefixed with [TEST], bypass workflow enrollment, never send to the enrolled record automatically, and are limited to 10 per user every 10 minutes.</Hint>
		</InspectorSection>
		{preview && createPortal(<div className="dialog-backdrop fixed inset-0 z-[100] grid place-items-center p-4" role="dialog" aria-modal="true" aria-label="Email preview" onMouseDown={(event) => { if (event.target === event.currentTarget) closePreview() }}><div ref={previewDialogRef} tabIndex={-1} className="dialog-card flex h-[min(860px,92vh)] w-[min(1100px,96vw)] flex-col overflow-hidden rounded-2xl"><header className="flex items-center justify-between gap-3 border-b border-[var(--border-color)] px-4 py-3"><div className="min-w-0"><span className="text-light text-[9px] font-bold uppercase tracking-[0.12em]">Email preview</span><h3 className="text-heading truncate text-sm font-bold">{preview.subject}</h3>{preview.preheader && <p className="text-muted mt-0.5 truncate text-[10px]">{preview.preheader}</p>}</div><div className="flex items-center gap-2"><div className="flex rounded-lg border border-[var(--border-color)] p-1"><button type="button" className={`icon-button !size-7 ${previewDevice === 'desktop' ? '!bg-brand-50 !text-brand-700' : ''}`} onClick={() => setPreviewDevice('desktop')} aria-label="Desktop preview"><Monitor size={13} /></button><button type="button" className={`icon-button !size-7 ${previewDevice === 'mobile' ? '!bg-brand-50 !text-brand-700' : ''}`} onClick={() => setPreviewDevice('mobile')} aria-label="Mobile preview"><Smartphone size={13} /></button></div><button type="button" className="icon-button" onClick={closePreview} aria-label="Close email preview"><X size={16} /></button></div></header><div className="min-h-0 flex-1 overflow-auto bg-slate-100 p-5 dark:bg-slate-950"><iframe sandbox="" title="Rendered email" className="mx-auto block h-full min-h-[620px] rounded-xl border-0 bg-white shadow-xl transition-[width]" style={{ width: previewDevice === 'mobile' ? 390 : '100%', maxWidth: previewDevice === 'mobile' ? 390 : 900 }} srcDoc={preview.html} /></div></div></div>, document.body)}
	</>
}

function EventTopicPicker({ value, events, onChange }: { value: string; events: BusinessEventType[]; onChange(value: string): void }) {
	const known = events.some((event) => event.topic === value)
	return <select aria-label="Event" className={inputClass} value={value} onChange={(event) => onChange(event.target.value)}><option value="">Choose an event…</option>{!known && value && <option value={value}>{value} (custom/legacy)</option>}{Array.from(new Set(events.map((event) => event.category))).map((category) => <optgroup label={category} key={category}>{events.filter((event) => event.category === category).map((event) => <option value={event.topic} key={event.topic}>{event.label}</option>)}</optgroup>)}</select>
}

function EventContextNotice({ topic, events, usage, sourceMode = 'enrolled_record' }: { topic: string; events: BusinessEventType[]; usage: 'trigger' | 'wait'; sourceMode?: 'enrolled_record' | 'action_output' }) {
	const definition = events.find((event) => event.topic === topic)
	if (!definition) return null
	const connected = definition.producer_status === 'native'
	return <div className="mt-2"><Hint title={sourceMode === 'action_output' ? 'How this event is matched' : connected ? `Connected through ${definition.source_app || 'FinbyzAI'}` : 'Integration setup required'} defaultOpen={!connected}>
		<span className="block">{sourceMode === 'action_output' ? 'Only an event identifying the exact record or message from the selected earlier action can release this wait.' : definition.record_resolution}</span>
		{connected && <span className="mt-1 block"><strong>Connected source:</strong> {definition.source_app || 'FinbyzAI'}. {definition.setup_note}</span>}
		{definition.producer_status === 'integration_required' && <span className="mt-1 block"><strong>Setup required:</strong> {definition.setup_note || 'An integration must produce this event.'}</span>}
		{usage === 'trigger' && definition.trigger_alternative && <span className="mt-1 block"><strong>Native option:</strong> {definition.trigger_alternative}</span>}
	</Hint></div>
}

function fieldsForEvent(topic: string, events: BusinessEventType[]): FieldCatalogItem[] {
	const definition = events.find((event) => event.topic === topic)
	return (definition?.filter_fields || []).map((field) => ({
		...field,
		required: false,
		read_only: true,
		allow_on_submit: false,
	}))
}

function EventCriteriaEditor({ topic, value, events, onChange }: { topic: string; value: unknown; events: BusinessEventType[]; onChange(value: ConditionExpression | null): void }) {
	const fields = fieldsForEvent(topic, events)
	if (!topic) return null
	if (!fields.length) return <Hint>This event does not expose additional refinement properties.</Hint>
	if (topic === 'email.unsubscribed') {
		const allowReachTopic = fields.some((field) => field.fieldname === 'subscription_topic')
		const expression = value ? parseCondition(value) : null
		const predicates = expression?.kind === 'all' ? expression.children : expression ? [expression] : []
		const simple = predicates.every((item) => item.kind === 'predicate' && item.operator === 'eq' && ['email_type', 'subscription_topic'].includes(item.field))
		if (simple) {
			const simplePredicates = predicates as ConditionPredicate[]
			const scope = String(simplePredicates.find((item) => item.field === 'email_type')?.value || 'any')
			const subscriptionTopic = String(simplePredicates.find((item) => item.field === 'subscription_topic')?.value || '')
			const commit = (nextScope: string, nextTopic = subscriptionTopic) => {
				if (nextScope === 'any') return onChange(null)
				const scopePredicate: ConditionPredicate = { kind: 'predicate', field: 'email_type', operator: 'eq', value: nextScope }
				if (nextScope !== 'topic' || !nextTopic) return onChange(scopePredicate)
				onChange({ kind: 'all', children: [scopePredicate, { kind: 'predicate', field: 'subscription_topic', operator: 'eq', value: nextTopic }] })
			}
			return <div className="space-y-3"><div><label className={labelClass}>Which unsubscribe should enroll?</label><select aria-label="Unsubscribe scope" className={inputClass} value={scope} onChange={(event) => commit(event.target.value, '')}><option value="any">Any unsubscribe</option><option value="global">Global unsubscribe</option><option value="record">This record only</option>{allowReachTopic && <option value="topic">A Finbyz Reach topic</option>}</select></div>{allowReachTopic && scope === 'topic' && <div><label className={labelClass}>Subscription topic <span className="font-normal">(optional)</span></label><AsyncCombobox ariaLabel="Subscription topic" value={subscriptionTopic} onChange={(nextTopic) => commit('topic', nextTopic)} loadOptions={(search) => searchLink('Subscription Topic', search, { filters: { disabled: 0 }, pageLength: 20 }).then((rows) => rows.map((row) => ({ value: row.value, label: row.label || row.value, description: row.description })))} placeholder="Any active Reach topic" /><Hint>Leave blank for any topic opt-out, or select one campaign topic.</Hint></div>}</div>
		}
	}
	if (!value) return <button type="button" className="btn-core btn-secondary w-full !text-[10px]" onClick={() => onChange(emptyPredicate())}><Plus size={12} />Add event criteria</button>
	return <div className="space-y-2"><div className="flex items-center justify-between"><span className={labelClass}>Event criteria</span><button type="button" className="btn-core btn-ghost !min-h-7 !px-2 !text-[9px]" onClick={() => onChange(null)}>Clear</button></div><ConditionExpressionEditor expression={parseCondition(value)} fields={fields} depth={0} onChange={onChange} /></div>
}

export function AbandonedCartTimingEditor({ config, onChange }: { config: NodeConfig; onChange(config: NodeConfig): void }) {
	const unit = String(config.abandoned_after_unit || 'hours') === 'days' ? 'days' : 'hours'
	const rawValue = Number(config.abandoned_after_value ?? 24)
	const value = Number.isFinite(rawValue) ? rawValue : 24
	const max = unit === 'days' ? 90 : 2160
	const changeUnit = (nextUnit: 'hours' | 'days') => {
		const hours = value * (unit === 'days' ? 24 : 1)
		const nextValue = nextUnit === 'days' ? Math.max(1, Math.ceil(hours / 24)) : hours
		onChange({ ...config, abandoned_after_value: nextValue, abandoned_after_unit: nextUnit })
	}
	return <div className="rounded-lg border border-[var(--border-color)] bg-[var(--subtle-fg)] p-3"><label className={labelClass}>Mark the cart abandoned after</label><div className="mt-1.5 grid grid-cols-[minmax(0,1fr)_120px] gap-2"><input aria-label="Cart idle duration" type="number" min={1} max={max} step={1} className={inputClass} value={value} onChange={(event) => onChange({ ...config, abandoned_after_value: Number(event.target.value), abandoned_after_unit: unit })} /><select aria-label="Cart idle duration unit" className={inputClass} value={unit} onChange={(event) => changeUnit(event.target.value as 'hours' | 'days')}><option value="hours">Hours</option><option value="days">Days</option></select></div><Hint>The hourly scheduler enrolls the Customer after this cart has remained unchanged for the selected time. Placing the order prevents enrollment.</Hint></div>
}

interface EnrollmentEventEntry {
	id: string
	event_topic: string
	event_filter?: ConditionExpression | null
	abandoned_after_value?: number
	abandoned_after_unit?: 'hours' | 'days'
}

function EventEnrollmentEditor({ config, typeVersion, events, recordFields, primaryDoctype, update }: { config: NodeConfig; typeVersion: number; events: BusinessEventType[]; recordFields: FieldCatalogItem[]; primaryDoctype?: string; update(config: NodeConfig, key: string): void }) {
	const { selectedTriggerGroupId } = useWorkflowEditor()
	const legacy = typeVersion < 2
	const entries = legacy
		? [{ id: 'legacy-event', event_topic: String(config.event_topic || ''), event_filter: config.event_filter as ConditionExpression | null }]
		: (Array.isArray(config.events) ? config.events : []).filter((entry): entry is EnrollmentEventEntry => Boolean(entry && typeof entry === 'object'))
	const setEntries = (next: EnrollmentEventEntry[], key: string) => {
		if (legacy) {
			const entry = next[0] || { id: 'legacy-event', event_topic: '', event_filter: null }
			const { id: _entryId, ...entryConfig } = entry
			update({ ...config, ...entryConfig, event_filter: entry.event_filter || null }, key)
			return
		}
		update({ ...config, events: next }, key)
	}
	const selectedIndex = Math.max(0, entries.findIndex((entry) => entry.id === selectedTriggerGroupId))
	const selectedEntry = entries[selectedIndex] || { id: 'event-1', event_topic: '', event_filter: null }
	return <>
		<InspectorSection title={`Configure event ${selectedIndex + 1} of ${entries.length}`} description={`Each event card is an independent OR enrollment path for this ${primaryDoctype || 'record'}. Filters here refine only this event.`}>
			<section className="space-y-3" data-config-path={legacy ? 'event_topic' : `events.${selectedIndex}`}>
				<div><label className={labelClass}>What should happen?</label><EventTopicPicker value={selectedEntry.event_topic} events={events} onChange={(event_topic) => setEntries(entries.map((item, index) => index === selectedIndex ? { ...item, event_topic, event_filter: null } : item), `events:${selectedIndex}:topic`)} /><EventContextNotice topic={selectedEntry.event_topic} events={events} usage="trigger" /></div>
				{selectedEntry.event_topic === 'commerce.order.abandoned' && <AbandonedCartTimingEditor config={selectedEntry as unknown as NodeConfig} onChange={(next) => setEntries(entries.map((item, index) => index === selectedIndex ? { ...item, ...next } as EnrollmentEventEntry : item), `events:${selectedIndex}:abandonment`)} />}
				<EventCriteriaEditor topic={selectedEntry.event_topic} value={selectedEntry.event_filter} events={events} onChange={(event_filter) => setEntries(entries.map((item, index) => index === selectedIndex ? { ...item, event_filter } : item), `events:${selectedIndex}:filter`)} />
			</section>
			<Hint>Only occurrences received after this workflow version is published can enroll a record. Customer Portal and Aircall events use their installed adapters and exact record mappings.</Hint>
		</InspectorSection>
		<InspectorSection title="Only enroll records that also match" description="Optional record-property filters are checked at the moment the event occurs.">
			{config.condition ? <div className="space-y-2"><div className="flex justify-end"><button type="button" className="btn-core btn-ghost !min-h-7 !px-2 !text-[9px]" onClick={() => update({ ...config, condition: null }, 'condition:clear')}>Clear record filters</button></div><ConditionExpressionEditor expression={parseCondition(config.condition)} fields={recordFields} primaryDoctype={primaryDoctype} depth={0} onChange={(condition) => update({ ...config, condition }, 'condition:tree')} /></div> : <button type="button" className="btn-core btn-secondary w-full !text-[10px]" onClick={() => update({ ...config, condition: emptyPredicate() }, 'condition:add')}><Plus size={12} />Add record filter</button>}
		</InspectorSection>
	</>
}

interface EnrollmentTriggerEntry { id: string; type: string; config: NodeConfig }

function MultiTriggerEditor({ config, typeVersion, events, fields, primaryDoctype, update }: { config: NodeConfig; typeVersion: number; events: BusinessEventType[]; fields: FieldCatalogItem[]; primaryDoctype?: string; update(config: NodeConfig, key: string): void }) {
	const { selectedTriggerGroupId } = useWorkflowEditor()
	const entries = (Array.isArray(config.triggers) ? config.triggers : []).filter((entry): entry is EnrollmentTriggerEntry => Boolean(entry && typeof entry === 'object'))
	const setEntries = (next: EnrollmentTriggerEntry[], key: string) => update({ ...config, triggers: next }, key)
	const replace = (index: number, values: Partial<EnrollmentTriggerEntry>) => setEntries(entries.map((entry, entryIndex) => entryIndex === index ? { ...entry, ...values } : entry), `triggers:${index}`)
	const defaultConfig = (type: string): NodeConfig => type === 'trigger.document_change' ? { watch_fields: [], condition: null } : type === 'trigger.event' ? { event_topic: '', event_filter: null, condition: null } : { condition: null }
	const selectedIndex = Math.max(0, entries.findIndex((entry) => entry.id === selectedTriggerGroupId))
	const selectedEntry = entries[selectedIndex]
	if (!selectedEntry) return <InspectorSection title="Event enrollment" description="Choose Add new trigger on the canvas to select the first enrollment event."><Hint>No trigger is configured yet. The workflow cannot be published until one is selected.</Hint></InspectorSection>
	const entryConfig = selectedEntry.config || {}
	return <InspectorSection title={`Configure trigger ${selectedIndex + 1} of ${entries.length}`} description={`This is one independent OR enrollment path for the same ${primaryDoctype || 'record'}. Its filters apply only to this trigger.`}>
		<div className="space-y-3" data-config-path={`triggers.${selectedIndex}`}>
			<div><label className={labelClass}>Enrollment moment</label><select className={inputClass} value={selectedEntry.type} onChange={(event) => replace(selectedIndex, { type: event.target.value, config: defaultConfig(event.target.value) })}><option value="trigger.document_insert">{primaryDoctype || 'Record'} is created</option><option value="trigger.document_change">{primaryDoctype || 'Record'} changes</option>{typeVersion < 2 && <option value="trigger.filter_criteria">Record meets criteria (legacy mixed mode)</option>}<option value="trigger.event">Installed business event occurs</option></select></div>
			{selectedEntry.type === 'trigger.document_change' && <div><label className={labelClass}>Only when these fields change <span className="text-muted font-normal">(optional)</span></label><MultiValueInput values={(Array.isArray(entryConfig.watch_fields) ? entryConfig.watch_fields : []).map(String)} onChange={(watch_fields) => replace(selectedIndex, { config: { ...entryConfig, watch_fields } })} loadOptions={async (search) => fields.filter((field) => !search || `${field.label} ${field.fieldname}`.toLowerCase().includes(search.toLowerCase())).map((field) => ({ value: field.fieldname, label: field.label, description: field.fieldtype }))} placeholder="Any field change" ariaLabel={`Trigger ${selectedIndex + 1} watched fields`} /></div>}
			{selectedEntry.type === 'trigger.event' && <div><label className={labelClass}>Business event</label><EventTopicPicker value={String(entryConfig.event_topic || '')} events={events} onChange={(event_topic) => replace(selectedIndex, { config: { ...entryConfig, event_topic, event_filter: null } })} /><EventContextNotice topic={String(entryConfig.event_topic || '')} events={events} usage="trigger" />{String(entryConfig.event_topic || '') === 'commerce.order.abandoned' && <div className="mt-3"><AbandonedCartTimingEditor config={entryConfig} onChange={(config) => replace(selectedIndex, { config })} /></div>}<div className="mt-3"><EventCriteriaEditor topic={String(entryConfig.event_topic || '')} value={entryConfig.event_filter} events={events} onChange={(event_filter) => replace(selectedIndex, { config: { ...entryConfig, event_filter } })} /></div></div>}
			<div><div className="mb-2 flex items-center justify-between"><label className={labelClass}>Record filters <span className="text-muted font-normal">(optional)</span></label>{Boolean(entryConfig.condition) && <button type="button" className="btn-core btn-ghost !min-h-7 !px-2 !text-[9px]" onClick={() => replace(selectedIndex, { config: { ...entryConfig, condition: null } })}>Clear</button>}</div>{entryConfig.condition ? <ConditionExpressionEditor expression={parseCondition(entryConfig.condition)} fields={fields} primaryDoctype={primaryDoctype} depth={0} onChange={(condition) => replace(selectedIndex, { config: { ...entryConfig, condition } })} /> : <button type="button" className="btn-core btn-secondary w-full !text-[10px]" onClick={() => replace(selectedIndex, { config: { ...entryConfig, condition: emptyPredicate() } })}><Plus size={12} />Add record filter</button>}</div>
		</div>
		<Hint>Use Add new trigger on the canvas for another OR path. Historical events do not enroll records; publish first or use a controlled backfill.</Hint>
	</InspectorSection>
}

export function EventWaitEditor({ config, typeVersion, events, outputNodes, update, nodeId, primaryDoctype, timeoutPathConnected = false }: { config: NodeConfig; typeVersion: number; events: BusinessEventType[]; outputNodes: WorkflowNode[]; update(config: NodeConfig, key: string): void; nodeId: string; primaryDoctype?: string; timeoutPathConnected?: boolean }) {
	const dataSource = String(config.data_source || (config.event_source ? 'action_output' : 'enrolled_record')) as 'enrolled_record' | 'action_output'
	const topic = String(config.event_topic || '')
	const visibleEvents = events.filter((event) => (event.source_modes || ['enrolled_record']).includes(dataSource))
	const definition = events.find((event) => event.topic === topic)
	const sourceNodeTypes = definition?.source_node_types || []
	const sourceSteps = outputNodes.filter((candidate) => sourceNodeTypes.includes(candidate.type))
	const source = config.event_source && typeof config.event_source === 'object' && !Array.isArray(config.event_source) ? config.event_source as Partial<WorkflowValueSpec> & { node_id?: string } : null
	const sourceNodeId = source?.node_id || ''
	const timeoutMode = String(config.timeout_mode || 'duration') as 'duration' | 'indefinite'
	const [timeoutChange, setTimeoutChange] = useState<'single-path' | 'indefinite' | null>(null)
	useEffect(() => setTimeoutChange(null), [nodeId])
	const requestTimeoutChange = (change: 'single-path' | 'indefinite') => {
		if (timeoutPathConnected) {
			setTimeoutChange(change)
			return
		}
		update({ ...config, timeout_mode: change === 'indefinite' ? 'indefinite' : 'duration', branch_on_timeout: 0 }, change === 'indefinite' ? 'timeout_mode' : 'branch_on_timeout')
	}
	const confirmTimeoutChange = () => {
		if (!timeoutChange) return
		update({ ...config, timeout_mode: timeoutChange === 'indefinite' ? 'indefinite' : 'duration', branch_on_timeout: 0 }, timeoutChange === 'indefinite' ? 'timeout_mode' : 'branch_on_timeout')
		setTimeoutChange(null)
	}
	const chooseDataSource = (nextSource: 'enrolled_record' | 'action_output') => update({
		...config,
		data_source: nextSource,
		event_topic: '',
		event_filter: null,
		event_source: null,
		event_source_doctype: null,
	}, 'data_source')
	const chooseEvent = (value: string) => update({ ...config, event_topic: value, event_filter: null, event_source: null, event_source_doctype: null }, 'event_topic')
	const chooseSource = (sourceId: string) => {
		const step = outputNodes.find((candidate) => candidate.id === sourceId)
		if (!step) {
			update({ ...config, event_source: null, event_source_doctype: null }, 'event_source')
			return
		}
		const email = step.type === 'action.send_email'
		update({
			...config,
			event_source: { kind: 'node_output', node_id: step.id, path: email ? 'email_queue' : 'name' },
			event_source_doctype: email
				? { kind: 'literal', value: 'Email Queue' }
				: { kind: 'node_output', node_id: step.id, path: 'doctype' },
		}, 'event_source')
	}
	const sourceLabel = (step: WorkflowNode, index: number) => {
		if (step.type === 'action.send_email') return `${nodeLabels[step.type]}${step.config.email_template ? `: ${String(step.config.email_template)}` : ` ${index + 1}`}`
		if (step.type === 'action.create_record') return `Create ${String(step.config.target_doctype || 'record')}`
		if (step.type === 'action.create_todo') return `Create ToDo${step.config.description ? `: ${String(step.config.description)}` : ''}`
		return `${nodeLabels[step.type] || step.type} ${index + 1}`
	}
	return <InspectorSection title="Wait until an event occurs" description="The event must happen while this record is waiting. Earlier activity does not release the delay.">
		<div data-config-path="data_source"><label className={labelClass}>Event belongs to</label><select aria-label="Event belongs to" className={inputClass} value={dataSource} onChange={(event) => chooseDataSource(event.target.value as 'enrolled_record' | 'action_output')}><option value="enrolled_record">This {primaryDoctype || 'workflow record'}</option><option value="action_output">Record or message from an earlier action</option></select></div>
		<div data-config-path="event_topic"><label className={labelClass}>Event</label><EventTopicPicker value={topic} events={visibleEvents} onChange={chooseEvent} /><EventContextNotice topic={topic} events={events} usage="wait" sourceMode={dataSource} /></div>
		{dataSource === 'action_output' && topic && <div data-config-path="event_source"><label className={labelClass}>Earlier action</label><select aria-label="Earlier action" className={inputClass} value={sourceNodeId} onChange={(event) => chooseSource(event.target.value)}><option value="">Choose an earlier action…</option>{sourceSteps.map((step, index) => <option value={step.id} key={step.id}>{sourceLabel(step, index)}</option>)}</select>{!sourceSteps.length && <Hint>Add a compatible action before this wait. Email events use Send email; record updates use Create/Copy record; task completion uses Create ToDo.</Hint>}</div>}
		<EventCriteriaEditor topic={topic} value={config.event_filter} events={events} onChange={(event_filter) => update({ ...config, event_filter }, 'event_filter')} />
		<div data-config-path="timeout_mode"><label className={labelClass}>Maximum wait</label><div className="grid grid-cols-2 gap-2"><button type="button" className={`btn-core ${timeoutMode === 'duration' ? 'btn-primary' : 'btn-secondary'} !text-[10px]`} onClick={() => update({ ...config, timeout_mode: 'duration' }, 'timeout_mode')}>For a set time</button><button type="button" className={`btn-core ${timeoutMode === 'indefinite' ? 'btn-primary' : 'btn-secondary'} !text-[10px]`} disabled={typeVersion < 2} onClick={() => requestTimeoutChange('indefinite')}>As long as possible</button></div></div>
		{timeoutMode === 'duration' && <div data-config-path="timeout_seconds"><DurationEditor key={`${nodeId}-timeout`} seconds={Number(config.timeout_seconds || 86400)} onChange={(timeout_seconds, timeout_duration, timeout_unit) => update({ ...config, timeout_seconds, timeout_duration, timeout_unit }, 'timeout')} /></div>}
		{typeVersion >= 2 && timeoutMode === 'duration' && <label className="text-body flex items-start gap-2 text-[11px]"><input type="checkbox" className="mt-0.5" checked={Boolean(config.branch_on_timeout)} onChange={(event) => event.target.checked ? update({ ...config, branch_on_timeout: 1 }, 'branch_on_timeout') : requestTimeoutChange('single-path')} /><span><strong className="text-heading block">Create a separate timeout path</strong><span className="text-muted block text-[9.5px] leading-4">Leave off when both outcomes should continue to the same next action.</span></span></label>}
		{timeoutChange && <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-amber-900 dark:border-amber-800 dark:bg-amber-500/10 dark:text-amber-200"><strong className="block text-[10.5px]">Remove the connected timeout path?</strong><p className="mt-1 text-[9.5px] leading-4">Actions used only by that path will be removed. Any later steps shared with the event path stay in place.</p><div className="mt-2 flex justify-end gap-2"><button type="button" className="btn-core btn-ghost !min-h-7 !text-[9px]" onClick={() => setTimeoutChange(null)}>Keep path</button><button type="button" className="btn-core btn-secondary !min-h-7 !text-[9px]" onClick={confirmTimeoutChange}>Remove path</button></div></div>}
		{timeoutPathConnected && !timeoutChange && <Hint title="About the timeout path">Switching to one path or an indefinite wait asks for confirmation before removing actions unique to the timeout path.</Hint>}
		{!timeoutPathConnected && <Hint>{timeoutMode === 'indefinite' ? 'Only a new matching event releases this wait.' : config.branch_on_timeout ? 'Event and timeout continue on separate paths.' : 'The event or elapsed maximum wait continues to the same next action.'}</Hint>}
	</InspectorSection>
}

interface CriteriaBranch {
	handle: string
	name: string
	condition: ConditionExpression | null
}

function BranchFilterGroupsEditor({ value, fields, primaryDoctype, outputNodes, outputPaths, onChange }: { value: ConditionExpression | null; fields: FieldCatalogItem[]; primaryDoctype?: string; outputNodes: WorkflowNode[]; outputPaths: NodeOutputCatalog; onChange(condition: ConditionExpression): void }) {
	const groups = conditionToFilterGroups(value)
	if (!groups) return <div className="space-y-3 rounded-xl border border-amber-200 bg-amber-50/70 p-3 dark:border-amber-800 dark:bg-amber-500/10"><div><strong className="text-amber-800 text-[10.5px] dark:text-amber-200">Advanced criteria from an older draft</strong><p className="mt-1 text-[9.5px] leading-4 text-amber-700 dark:text-amber-300">This path still runs unchanged. New paths use simple AND groups separated by OR.</p></div><ConditionExpressionEditor expression={parseCondition(value)} fields={fields} primaryDoctype={primaryDoctype} outputNodes={outputNodes} outputPaths={outputPaths} depth={0} onChange={onChange} /><button type="button" className="btn-core btn-secondary w-full !text-[10px]" onClick={() => onChange(filterGroupsToCondition([[emptyPredicate()]]))}>Replace with simple filter groups</button></div>
	const commit = (next: ConditionPredicate[][]) => onChange(filterGroupsToCondition(next))
	return <div className="space-y-2.5">{groups.map((group, groupIndex) => <div key={groupIndex}>{groupIndex > 0 && <div className="relative my-3 flex items-center justify-center"><span className="absolute inset-x-0 h-px bg-[var(--border-color)]" /><strong className="relative rounded-full bg-[var(--card-bg)] px-3 py-1 text-[9px] text-brand-700 dark:text-brand-200">OR</strong></div>}<section className="branch-filter-group"><div className="mb-2.5 flex items-start justify-between gap-3"><div><strong className="text-heading block text-[10.5px]">Group {groupIndex + 1}</strong><span className="text-muted mt-0.5 block text-[9px]">All conditions in this group must match</span></div>{groups.length > 1 && <button type="button" className="icon-button !size-7 hover:!text-red-600" onClick={() => commit(groups.filter((_, index) => index !== groupIndex))} aria-label={`Remove filter group ${groupIndex + 1}`}><Trash2 size={12} /></button>}</div><div className="space-y-2.5">{group.map((predicate, predicateIndex) => <div className="branch-condition-row" key={predicateIndex}><div className="mb-1 flex items-center justify-between"><span className="text-light text-[8.5px] font-bold">Condition {predicateIndex + 1}</span>{group.length > 1 && <button type="button" className="icon-button !size-6 hover:!text-red-600" onClick={() => commit(groups.map((item, index) => index === groupIndex ? item.filter((_, conditionIndex) => conditionIndex !== predicateIndex) : item))} aria-label={`Remove condition ${predicateIndex + 1}`}><Trash2 size={11} /></button>}</div><ConditionExpressionEditor expression={predicate} fields={fields} primaryDoctype={primaryDoctype} outputNodes={outputNodes} outputPaths={outputPaths} depth={1} onChange={(condition) => { if (condition.kind !== 'predicate') return; commit(groups.map((item, index) => index === groupIndex ? item.map((candidate, conditionIndex) => conditionIndex === predicateIndex ? condition : candidate) : item)) }} /></div>)}</div><button type="button" className="btn-core btn-ghost mt-2.5 w-full !text-[10px]" onClick={() => commit(groups.map((item, index) => index === groupIndex ? [...item, emptyPredicate()] : item))}><Plus size={12} />AND condition</button></section></div>)}<button type="button" className="btn-core btn-secondary w-full !text-[10px]" onClick={() => commit([...groups, [emptyPredicate()]])}><Plus size={12} />OR group</button></div>
}

function CriteriaBranchesEditor({ config, fields, primaryDoctype, outputNodes, outputPaths, update, removeBranch }: { config: NodeConfig; fields: FieldCatalogItem[]; primaryDoctype?: string; outputNodes: WorkflowNode[]; outputPaths: NodeOutputCatalog; update(config: NodeConfig, key: string): void; removeBranch(handle: string, branches: CriteriaBranch[]): void }) {
	const branches = (Array.isArray(config.branches) ? config.branches : []).filter((branch): branch is CriteriaBranch => Boolean(branch && typeof branch === 'object'))
	const [expandedHandle, setExpandedHandle] = useState<string | null>(() => branches[0]?.handle || null)
	const setBranches = (next: CriteriaBranch[], key: string) => update({ ...config, branches: next }, key)
	const conditionCount = (condition: ConditionExpression | null): number => !condition ? 0 : condition.kind === 'predicate' ? (condition.field || condition.source ? 1 : 0) : condition.children.reduce((total, child) => total + conditionCount(child), 0)
	const move = (index: number, offset: number) => {
		const target = index + offset
		if (target < 0 || target >= branches.length) return
		const next = [...branches]
		;[next[index], next[target]] = [next[target], next[index]]
		setBranches(next, 'branches:reorder')
	}
	return <InspectorSection title="Choose the path for each record" description="Paths are checked from top to bottom. The first matching path is used; everyone else follows None.">
		<Hint>Conditions in one group use AND. Add another group for OR. Put the most specific path first; no manual else condition is needed.</Hint>
		<div className="space-y-2" data-config-path="branches">{branches.map((branch, index) => {
			const expanded = expandedHandle === branch.handle
			const count = conditionCount(branch.condition)
			return <section className={`min-w-0 rounded-xl border ${expanded ? 'border-brand-300 bg-brand-50/30 dark:border-brand-700 dark:bg-brand-500/5' : 'border-[var(--border-color)] bg-[var(--subtle-fg)]'}`} key={branch.handle}>
				<div className="flex min-w-0 items-center gap-2 p-3">
					<button type="button" className="min-w-0 flex-1 text-left" onClick={() => setExpandedHandle(expanded ? null : branch.handle)} aria-expanded={expanded}>
						<span className="flex items-center gap-2"><span className="grid size-5 shrink-0 place-items-center rounded-full bg-brand-100 text-[9px] font-bold text-brand-700 dark:bg-brand-500/20 dark:text-brand-200">{index + 1}</span><strong className="text-heading truncate text-[11px]">{branch.name || `Path ${index + 1}`}</strong><ChevronDown className={`ml-auto shrink-0 transition ${expanded ? 'rotate-180' : ''}`} size={14} /></span>
						<span className="text-muted mt-1 block pl-7 text-[9.5px]">Checked {index === 0 ? 'first' : `${index + 1}${index === 1 ? 'nd' : index === 2 ? 'rd' : 'th'}`} · {count || 'No'} condition{count === 1 ? '' : 's'}</span>
					</button>
					<div className="flex gap-1"><button type="button" className="icon-button !size-8" disabled={index === 0} onClick={() => move(index, -1)} aria-label={`Check ${branch.name || `path ${index + 1}`} earlier`} title="Check earlier"><ChevronUp size={13} /></button><button type="button" className="icon-button !size-8" disabled={index === branches.length - 1} onClick={() => move(index, 1)} aria-label={`Check ${branch.name || `path ${index + 1}`} later`} title="Check later"><ChevronDown size={13} /></button><button type="button" className="icon-button !size-8 hover:!text-red-600" disabled={branches.length === 1} onClick={() => removeBranch(branch.handle, branches.filter((_, branchIndex) => branchIndex !== index))} aria-label={`Remove ${branch.name || `path ${index + 1}`}`}><Trash2 size={13} /></button></div>
				</div>
				{expanded && <div className="space-y-3 border-t border-[var(--border-color)] p-3"><label><span className={labelClass}>Path name</span><input className={inputClass} maxLength={80} value={branch.name} onChange={(event) => setBranches(branches.map((item, branchIndex) => branchIndex === index ? { ...item, name: event.target.value } : item), `branches:${index}:name`)} placeholder={`Path ${index + 1}`} /></label><div><span className={labelClass}>Who should follow this path?</span><BranchFilterGroupsEditor value={branch.condition} fields={fields} primaryDoctype={primaryDoctype} outputNodes={outputNodes} outputPaths={outputPaths} onChange={(condition) => setBranches(branches.map((item, branchIndex) => branchIndex === index ? { ...item, condition } : item), `branches:${index}:condition`)} /></div></div>}
			</section>
		})}<button type="button" className="btn-core btn-secondary w-full !text-[10px]" disabled={branches.length >= 20} onClick={() => { const handle = `branch-${crypto.randomUUID()}`; setBranches([...branches, { handle, name: `Path ${branches.length + 1}`, condition: filterGroupsToCondition([[emptyPredicate()]]) }], 'branches:add'); setExpandedHandle(handle) }}><Plus size={12} />Add another path ({branches.length}/20)</button></div>
		<div className="flex items-start gap-2 border-t border-[var(--border-color)] px-1 pt-3 text-[10px] text-[var(--text-muted)]"><CheckCircle2 className="mt-0.5 shrink-0 text-emerald-600" size={13} /><span><strong className="text-heading block">None — everyone else</strong>Automatic final path when no path above matches.</span></div>
	</InspectorSection>
}

function JsonPayloadEditor({ value, onChange }: { value: unknown; onChange(value: unknown): void }) {
  const serialize = (candidate: unknown) => typeof candidate === 'string' ? candidate : JSON.stringify(candidate || {}, null, 2)
  const [textValue, setTextValue] = useState(() => serialize(value))
  const [error, setError] = useState('')

  useEffect(() => setTextValue(serialize(value)), [value])

  const updateValue = (raw: string) => {
    setTextValue(raw)
    const parsed = parseWebhookPayload(raw)
    setError(parsed.error)
    onChange(parsed.value)
  }

  return (
    <div>
      <label className={labelClass}>JSON payload</label>
      <textarea
        className={`${inputClass} min-h-32 font-mono ${error ? '!border-red-400' : ''}`}
        value={textValue}
        onChange={(event) => updateValue(event.target.value)}
        aria-invalid={Boolean(error)}
        aria-describedby={error ? 'webhook-payload-error' : undefined}
      />
      {error && <p id="webhook-payload-error" className="mt-1.5 text-[10px] text-red-600">{error}</p>}
    </div>
  )
}

export function Inspector({ width, minWidth, maxWidth, expanded, onWidthChange, onToggleExpanded }: InspectorProps) {
  const { graph, workflowId, validation } = useWorkflowDocument()
  const { selectedNodeId } = useWorkflowEditor()
  const actions = useWorkflowActions()
  const node = useMemo(() => graph?.nodes.find((item) => item.id === selectedNodeId), [graph?.nodes, selectedNodeId])
  const selectedNodeType = node?.type
  const primaryDoctype = graph?.primary_doctype
  const [readFields, setReadFields] = useState<FieldCatalogItem[]>([])
  const [writeFields, setWriteFields] = useState<FieldCatalogItem[]>([])
  const [targetFields, setTargetFields] = useState<FieldCatalogItem[]>([])
  const [targetFieldsDoctype, setTargetFieldsDoctype] = useState('')
  const [targetMetadataLoading, setTargetMetadataLoading] = useState(false)
  const [nodeTypes, setNodeTypes] = useState<NodeCatalogItem[]>([])
	const [triggerEventTypes, setTriggerEventTypes] = useState<BusinessEventType[]>([])
	const [waitEventTypes, setWaitEventTypes] = useState<BusinessEventType[]>([])
	const [eventObjectProfile, setEventObjectProfile] = useState<WorkflowObjectProfile | null>(null)
  const [metadataEpoch, setMetadataEpoch] = useState(0)
  const lastMetadataRefresh = useRef(0)
  const mandatoryFieldsInitialisedFor = useRef('')
  const outputPaths = useMemo(() => outputCatalog(nodeTypes), [nodeTypes])
  const outputNodes = useMemo(() => availableOutputNodes(graph, selectedNodeId || '', outputPaths), [graph, outputPaths, selectedNodeId])
  const [metadataIssues, setMetadataIssues] = useState<Record<'read' | 'write' | 'target', string>>({ read: '', write: '', target: '' })
	const canToggleWidth = expanded || maxWidth > width + 24
  const loadTargetDoctypes = useCallback((search: string): Promise<ComboboxOption[]> => {
    void metadataEpoch // Re-query an open combobox after metadata invalidation.
    return searchDoctypes('create', search, workflowId).then((rows) => rows.map((row) => ({ value: row.name, label: row.label || row.name, description: row.module })))
  }, [metadataEpoch, workflowId])
  const loadReadableDoctypes = useCallback((search: string): Promise<ComboboxOption[]> => {
    void metadataEpoch // Re-query an open combobox after metadata invalidation.
    return searchDoctypes('read', search, workflowId).then((rows) => rows.map((row) => ({ value: row.name, label: row.label || row.name, description: row.module })))
  }, [metadataEpoch, workflowId])
  const loadUsers = useCallback((search: string): Promise<ComboboxOption[]> => searchLink('User', search, { filters: { enabled: 1 } }).then((rows) => rows.map((row) => ({ value: row.value, label: row.label || row.value, description: row.description }))), [])
  const loadUserGroups = useCallback((search: string): Promise<ComboboxOption[]> => searchLink('User Group', search).then((rows) => rows.map((row) => ({ value: row.value, label: row.label || row.value, description: row.description }))), [])
  const loadSecrets = useCallback((search: string): Promise<ComboboxOption[]> => call<{ rows: Array<{ name: string; title: string; auth_type: string; allowed_hosts?: string }> }>('get_integration_secrets', { search }).then((result) => result.rows.map((row) => ({ value: row.name, label: row.title, description: `${row.auth_type} · ${row.allowed_hosts || 'No hosts configured'}` }))), [])

  useEffect(() => {
    const refreshMetadata = () => {
      const now = Date.now()
      if (now - lastMetadataRefresh.current < 1_000) return
      lastMetadataRefresh.current = now
      invalidateMetadataCaches()
      setMetadataEpoch((current) => current + 1)
    }
    const refreshVisibleMetadata = () => {
      if (document.visibilityState === 'visible') refreshMetadata()
    }
    window.addEventListener('focus', refreshMetadata)
    document.addEventListener('visibilitychange', refreshVisibleMetadata)
    return () => {
      window.removeEventListener('focus', refreshMetadata)
      document.removeEventListener('visibilitychange', refreshVisibleMetadata)
    }
  }, [])

	useEffect(() => {
		let active = true
		setTriggerEventTypes([])
		setWaitEventTypes([])
		setEventObjectProfile(null)
		if (!primaryDoctype || !selectedNodeType) return () => { active = false }
		type EventCatalogResponse = { event_types: BusinessEventType[]; object_profile: WorkflowObjectProfile }
		const needsTriggerCatalog = ['trigger.document_insert', 'trigger.document_change', 'trigger.event', 'trigger.any'].includes(selectedNodeType)
		const needsWaitCatalog = selectedNodeType === 'delay.until_event'
		if (!needsTriggerCatalog && !needsWaitCatalog) return () => { active = false }
		const triggerRequest = needsTriggerCatalog
			? call<EventCatalogResponse>('get_event_types', { primary_doctype: primaryDoctype, usage: 'trigger' })
			: Promise.resolve<EventCatalogResponse>({ event_types: [], object_profile: null as unknown as WorkflowObjectProfile })
		const waitRequest = needsWaitCatalog
			? call<EventCatalogResponse>('get_event_types', { primary_doctype: primaryDoctype, usage: 'wait' })
			: Promise.resolve<EventCatalogResponse>({ event_types: [], object_profile: null as unknown as WorkflowObjectProfile })
		void Promise.all([triggerRequest, waitRequest]).then(([triggerResult, waitResult]) => {
			if (!active) return
			setTriggerEventTypes(triggerResult.event_types || [])
			setWaitEventTypes(waitResult.event_types || [])
			setEventObjectProfile(triggerResult.object_profile || waitResult.object_profile || null)
		}).catch(() => {
			if (!active) return
			setTriggerEventTypes([])
			setWaitEventTypes([])
			setEventObjectProfile(null)
		})
		return () => { active = false }
	}, [primaryDoctype, selectedNodeType])

  useEffect(() => {
    let active = true
    void call<{ node_types: NodeCatalogItem[] }>('get_node_types').then((result) => {
      if (active) setNodeTypes(result.node_types || [])
    }).catch(() => {
      if (active) setNodeTypes([])
    })
    return () => { active = false }
  }, [])

  useEffect(() => {
    if (!primaryDoctype) return
    let active = true
    setReadFields([])
    setWriteFields([])
    setMetadataIssues((current) => ({ ...current, read: '', write: '' }))
    const loadFields = async (permissionType: 'read' | 'write', setter: (fields: FieldCatalogItem[]) => void) => {
      try {
        const result = await fetchFieldCatalog(primaryDoctype, permissionType, workflowId)
        if (!active) return
        setter(result.available ? result.fields : [])
        setMetadataIssues((current) => ({ ...current, [permissionType]: result.available ? '' : result.explanation || 'Metadata unavailable' }))
      } catch (reason) {
        if (!active) return
        setter([])
        setMetadataIssues((current) => ({ ...current, [permissionType]: reason instanceof Error ? reason.message : 'Unable to load field metadata' }))
      }
    }
    void loadFields('read', setReadFields)
    void loadFields('write', setWriteFields)
    return () => { active = false }
  }, [metadataEpoch, primaryDoctype, workflowId])

  useEffect(() => {
    if (node?.type !== 'action.create_record') {
      setTargetMetadataLoading(false)
      return
    }
    let active = true
    setTargetFields([])
    setTargetFieldsDoctype('')
    setMetadataIssues((current) => ({ ...current, target: '' }))
    const target = String(node.config.target_doctype || '')
    if (!target) {
      setTargetMetadataLoading(false)
      return () => { active = false }
    }
    setTargetMetadataLoading(true)
    void fetchFieldCatalog(target, 'create', workflowId)
      .then((result) => {
        if (!active) return
        setTargetFields(result.available ? result.fields : [])
        setTargetFieldsDoctype(result.available ? target : '')
        setMetadataIssues((current) => ({ ...current, target: result.available ? '' : result.explanation || 'Target metadata unavailable' }))
      })
      .catch((reason: unknown) => {
        if (!active) return
        setTargetFields([])
        setTargetFieldsDoctype('')
        setMetadataIssues((current) => ({ ...current, target: reason instanceof Error ? reason.message : 'Unable to load target fields' }))
      })
      .finally(() => {
        if (active) setTargetMetadataLoading(false)
      })
    return () => { active = false }
  }, [metadataEpoch, node?.id, node?.type, node?.config.target_doctype, workflowId])

  useEffect(() => {
    if (node?.type !== 'action.create_record') {
      mandatoryFieldsInitialisedFor.current = ''
      return
    }
    const targetDoctype = String(node.config.target_doctype || '')
    const initialisationKey = `${node.id}:${targetDoctype}`
    if (!targetDoctype || !targetFields.length || targetFieldsDoctype !== targetDoctype) return
    if (mandatoryFieldsInitialisedFor.current === initialisationKey) return
    mandatoryFieldsInitialisedFor.current = initialisationKey
    const assignments = Array.isArray(node.config.assignments) ? node.config.assignments.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === 'object')) : []
    const assigned = new Set(assignments.map((assignment) => String(assignment.field || '')).filter(Boolean))
    const missing = targetFields.filter((field) => {
      const assignable = field.capabilities ? field.capabilities.assignment_scalar || field.capabilities.assignment_collection : !['Table', 'Table MultiSelect'].includes(field.fieldtype)
      return assignable && (field.required || Boolean(field.mandatory_depends_on)) && (field.default == null || field.default === '') && !assigned.has(field.fieldname)
    })
    if (!missing.length) return
    actions.updateNode(node.id, {
      ...node.config,
      assignments: [...assignments, ...missing.map((field) => ({ field: field.fieldname, value: { kind: 'literal', value: '' } }))],
    }, `node:${node.id}:mandatory-fields`)
  }, [actions, node, targetFields, targetFieldsDoctype])

  if (!node) {
    return (
	  <aside className="editor-side-panel relative flex h-full min-w-0 items-center justify-center border-l border-[var(--border-color)] bg-white/70 p-7 text-center backdrop-blur-2xl dark:bg-[#18212b]/80 max-lg:hidden">
		<InspectorResizeHandle width={width} minWidth={minWidth} maxWidth={maxWidth} onWidthChange={onWidthChange} />
		<button type="button" className="icon-button absolute right-3 top-3" disabled={!canToggleWidth} onClick={onToggleExpanded} aria-label={expanded ? 'Restore step settings sidebar width' : 'Expand step settings sidebar'} title={expanded ? 'Restore sidebar width' : canToggleWidth ? 'Expand sidebar' : 'Sidebar is at the widest size for this window'}>{expanded ? <PanelRightClose size={15} /> : <PanelRightOpen size={15} />}</button>
        <div className="max-w-[220px]">
          <span className="magic-orb mx-auto"><Settings2 size={20} /></span>
          <h2 className="text-heading mt-4 text-sm font-bold">Configure a step</h2>
          <p className="text-muted mt-1.5 text-[11px] leading-[18px]">Select any card on the canvas to edit its criteria, values, and behavior.</p>
          <div className="mt-5 rounded-lg border border-dashed border-[var(--dark-border-color)] px-3 py-2 text-[10px] font-medium text-[var(--text-light)]">Your changes save automatically</div>
        </div>
      </aside>
    )
  }

  const update = (config: NodeConfig, key: string) => actions.updateNode(node.id, config, `node:${node.id}:${key}`)
  const config = node.config
  const roundRobinAssignmentType = String(config.assignment_type || 'group')
  const conditionFields = readFields.filter((field) => field.capabilities ? field.capabilities.condition_scalar || field.capabilities.condition_collection : !['Table', 'Table MultiSelect'].includes(field.fieldtype))
  const scalarReadFields = readFields.filter((field) => field.capabilities?.scalar_read ?? !['Table', 'Table MultiSelect'].includes(field.fieldtype))
  const assignmentFields = writeFields.filter((field) => field.capabilities ? field.capabilities.assignment_scalar || field.capabilities.assignment_collection : !['Table', 'Table MultiSelect'].includes(field.fieldtype))
  const activeTargetFields = targetFieldsDoctype === String(config.target_doctype || '') ? targetFields : []
  const targetAssignmentFields = activeTargetFields
    .filter((field) => field.capabilities ? field.capabilities.assignment_scalar || field.capabilities.assignment_collection : !['Table', 'Table MultiSelect'].includes(field.fieldtype))
    .sort((left, right) => Number(Boolean(right.required || right.mandatory_depends_on)) - Number(Boolean(left.required || left.mandatory_depends_on)))
  const unsupportedMandatoryTargetFields = activeTargetFields.filter((field) => {
    const assignable = field.capabilities ? field.capabilities.assignment_scalar || field.capabilities.assignment_collection : !['Table', 'Table MultiSelect'].includes(field.fieldtype)
    return !assignable && (field.required || Boolean(field.mandatory_depends_on)) && (field.default == null || field.default === '')
  })
  const selectedChildTable = readFields.find((field) => field.fieldname === String(config.child_table_field || ''))
  const childFields: FieldCatalogItem[] = (selectedChildTable?.child_fields || []).map((field) => ({
    ...field,
    read_only: false,
    allow_on_submit: false,
  }))
  const required = nodeTypes.find((item) => item.type === node.type)?.authoring_schema?.required || []
  const readPath = (path: string) => path.split('.').reduce<unknown>((value, key) => value && typeof value === 'object' ? (value as Record<string, unknown>)[key] : undefined, config)
  const missingRequired = required.filter((item) => isRequiredAuthoringValueMissing(readPath(item.path)))
  const configPath = (path?: string) => path?.includes('.config.') ? path.split('.config.', 2)[1] : path?.replace(/^config\./, '')
  const serverNodeIssues = validation.filter((issue) => issue.node_id === node.id)
  const serverIssuePaths = new Set(serverNodeIssues.map((issue) => configPath(issue.path)).filter(Boolean))
  const localRequiredIssues: ValidationIssue[] = missingRequired
    .filter((item) => !serverIssuePaths.has(item.path))
    .map((item) => ({ severity: 'error', code: 'REQUIRED_SETTING', message: `${item.label} is required`, path: `config.${item.path}`, node_id: node.id }))
  const nodeIssues = [...localRequiredIssues, ...serverNodeIssues]
	const groupedNodeIssues = Array.from(nodeIssues.reduce((groups, issue) => {
		const key = `${issue.code}:${issue.message}`
		const current = groups.get(key)
		groups.set(key, current ? { issue: current.issue, count: current.count + 1 } : { issue, count: 1 })
		return groups
	}, new Map<string, { issue: ValidationIssue; count: number }>()).values())
  const focusIssue = (issue: ValidationIssue) => {
    const path = configPath(issue.path)
    if (!path) return
    const target = Array.from(document.querySelectorAll<HTMLElement>('[data-config-path]')).find((element) => element.dataset.configPath === path)
    target?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    const focusable = target?.querySelector('input, textarea, select, button, [contenteditable="true"]') as HTMLElement | null
    focusable?.focus()
  }
  const NodeIcon = nodeIcons[node.type] || Sparkles
  const text = (key: string, label: string, multiline = false, placeholder?: string) => (
    <div data-config-path={key}>
      <label className={labelClass}>{label}</label>
      {multiline ? (
        <textarea className={`${inputClass} min-h-24 resize-y`} rows={4} placeholder={placeholder} value={String(config[key] || '')} onChange={(event) => update({ ...config, [key]: event.target.value }, key)} />
      ) : (
        <input className={inputClass} placeholder={placeholder} value={String(config[key] || '')} onChange={(event) => update({ ...config, [key]: event.target.value }, key)} />
      )}
    </div>
  )
  const bindingValue = (key: string): WorkflowValueSpec => {
    const value = config[key]
    if (value && typeof value === 'object' && !Array.isArray(value) && ['literal', 'record_field', 'node_output'].includes(String((value as { kind?: unknown }).kind))) return value as WorkflowValueSpec
    return { kind: 'literal', value: '' }
  }
  const bindingEditor = (key: string, label: string) => <div data-config-path={key}><label className={labelClass}>{label}</label><ValueSourceEditor assignment={{ field: key, value: bindingValue(key) }} sourceFields={readFields} outputNodes={outputNodes} outputPaths={outputPaths} onChange={(assignment) => update({ ...config, [key]: assignment.value }, key)} /></div>

  return (
	<aside className="editor-side-panel relative flex h-full max-h-full min-h-0 min-w-0 flex-col overflow-visible border-l border-[var(--border-color)] bg-white/70 backdrop-blur-2xl dark:bg-[#121b23]/80 max-lg:absolute max-lg:inset-y-0 max-lg:right-0 max-lg:z-40 max-lg:w-full sm:max-lg:w-96 max-lg:shadow-2xl">
	  <InspectorResizeHandle width={width} minWidth={minWidth} maxWidth={maxWidth} onWidthChange={onWidthChange} />
      <div className="z-10 flex shrink-0 items-start justify-between border-b border-[var(--border-color)] bg-white/50 px-5 py-4 backdrop-blur-md dark:bg-[#18212b]/50">
        <div className="flex w-full min-w-0 items-start gap-3">
          <button className="icon-button shrink-0 lg:hidden" onClick={() => actions.select()} aria-label="Close inspector"><X size={16} /></button>
          <span className="grid size-9 shrink-0 place-items-center rounded-[10px] bg-brand-50 text-brand-600 dark:bg-brand-500/10"><NodeIcon size={17} /></span>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5 text-[9px] font-bold uppercase tracking-[0.13em] text-brand-600"><Sparkles size={10} /> Step settings</div>
            <h2 className="text-heading mt-0.5 truncate text-[13px] font-bold">{nodeLabels[node.type] || node.type}</h2>
			<p className="text-light mt-0.5 truncate text-[9px]">{node.type === 'condition.if_else' ? 'Decide which path each record follows' : nodeTypes.find((item) => item.type === node.type)?.description || 'Configure this workflow step'}</p>
          </div>
			<div className="flex shrink-0 gap-1">
			  <button type="button" className="icon-button" disabled={!canToggleWidth} onClick={onToggleExpanded} aria-label={expanded ? 'Restore step settings sidebar width' : 'Expand step settings sidebar'} title={expanded ? 'Use standard panel width' : 'Make panel wider'}>{expanded ? <PanelRightClose size={15} /> : <PanelRightOpen size={15} />}</button>
			  {node.id !== graph?.start_node_id && <details className="inspector-step-menu"><summary className="icon-button" aria-label="More step actions" title="More step actions"><Ellipsis size={16} /></summary><div className="inspector-step-menu__popover"><button type="button" onClick={(event) => { actions.copyNode(node.id); (event.currentTarget.closest('details') as HTMLDetailsElement).open = false }}><Copy size={14} />Copy this step</button><button type="button" onClick={(event) => { actions.duplicateNode(node.id); (event.currentTarget.closest('details') as HTMLDetailsElement).open = false }}><Plus size={14} />Duplicate below</button><button type="button" onClick={(event) => { actions.copySection(node.id); (event.currentTarget.closest('details') as HTMLDetailsElement).open = false }}><Network size={14} />Copy this path</button><button type="button" onClick={(event) => { actions.duplicateSection(node.id); (event.currentTarget.closest('details') as HTMLDetailsElement).open = false }}><Network size={14} />Duplicate this path</button><DeleteWorkflowStepButton node={node} data-danger="true"><Trash2 size={14} />Delete this step</DeleteWorkflowStepButton></div></details>}
			</div>
        </div>
      </div>

	  <div className="inspector-scroll-region min-h-0 min-w-0 flex-1 space-y-3 overflow-y-scroll overflow-x-hidden px-5 py-4" onWheel={(event) => event.stopPropagation()}>
        {metadataIssues.read && <p className="flex gap-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-[10.5px] leading-4 text-amber-800 dark:border-amber-900 dark:bg-amber-500/10 dark:text-amber-300"><Info className="mt-0.5 shrink-0" size={13} />{metadataIssues.read} Existing graph data remains visible, but field editing and publishing are blocked.</p>}
		{nodeIssues.length > 0 && <section aria-label="Step validation" className="rounded-xl border border-red-200 bg-red-50/80 p-3 dark:border-red-900 dark:bg-red-500/10"><p className="flex items-center gap-2 text-[10.5px] font-bold text-red-700 dark:text-red-300"><AlertTriangle size={13} />Fix {nodeIssues.length} issue{nodeIssues.length === 1 ? '' : 's'} in this step</p><p className="mt-1 text-[9.5px] text-red-600 dark:text-red-300">Select an issue to jump to the first affected condition.</p><div className="mt-2 space-y-1">{groupedNodeIssues.map(({ issue, count }, index) => <button type="button" className="flex w-full items-start gap-2 rounded-lg px-2 py-1.5 text-left text-[10px] leading-4 text-red-700 transition hover:bg-red-100 focus:outline-none focus:ring-2 focus:ring-red-300 dark:text-red-300 dark:hover:bg-red-500/10" onClick={() => focusIssue(issue)} key={`${issue.code}-${issue.path || ''}-${index}`}><span className="mt-1 size-1 shrink-0 rounded-full bg-current" /><span>{issue.message}{count > 1 ? <span className="ml-1 font-bold">({count} conditions)</span> : null}{issue.line ? <span className="ml-1 font-semibold">Line {issue.line}:{issue.column || 1}</span> : null}</span></button>)}</div></section>}
		{node.type === 'trigger.document_insert' && <ConditionEditor config={config} fields={conditionFields} update={update} primaryDoctype={primaryDoctype} title={`When a ${primaryDoctype || 'record'} is created`} description={eventObjectProfile?.native_event_guidance.created || `This native trigger listens directly for new ${primaryDoctype || 'records'}. Add optional criteria to limit who enters.`} />}
		{node.type === 'trigger.document_change' && <><InspectorSection title="Which changes should count?" description="Optionally choose fields. Leave this empty to react to any permitted field change."><div className="flex flex-wrap gap-1.5">{(Array.isArray(config.watch_fields) ? config.watch_fields : []).map((fieldname) => <button type="button" className="rounded-full border border-brand-200 bg-brand-50 px-2.5 py-1 text-[9.5px] font-semibold text-brand-700 dark:bg-brand-500/10" onClick={() => update({ ...config, watch_fields: (config.watch_fields as unknown[]).filter((item) => item !== fieldname) }, 'watch_fields:remove')} title="Remove watched field" key={String(fieldname)}>{scalarReadFields.find((field) => field.fieldname === fieldname)?.label || String(fieldname)} ×</button>)}</div><div><label className={labelClass}>Add a field to watch</label><FieldPicker value="" fields={scalarReadFields.filter((field) => !(Array.isArray(config.watch_fields) ? config.watch_fields : []).includes(field.fieldname))} onChange={(value) => value && update({ ...config, watch_fields: [...(Array.isArray(config.watch_fields) ? config.watch_fields : []), value] }, 'watch_fields:add')} /></div><Hint>Enrollment requires one selected field to have changed. The optional criteria below then checks the record's current state.</Hint></InspectorSection><ConditionEditor config={config} fields={conditionFields} update={update} primaryDoctype={primaryDoctype} title={`What state should the ${primaryDoctype || 'record'} be in?`} description={eventObjectProfile?.native_event_guidance.changed || 'Add optional current-state criteria after the selected field change.'} /></>}
		{node.type === 'trigger.filter_criteria' && <ConditionEditor config={config} fields={conditionFields} update={update} primaryDoctype={primaryDoctype} title={`When a ${primaryDoctype || 'record'} meets criteria`} description={`Choose the business state that enrolls this ${primaryDoctype || 'record'}. For example, a Lead can enter when Qualification status equals Qualified.`} />}
		{node.type === 'condition.if_else' && node.type_version === 1 && <ConditionEditor config={config} fields={conditionFields} update={update} primaryDoctype={primaryDoctype} title="Who follows the Yes path?" description="This older two-path step sends matching records to Yes and everyone else to No. Saving the draft upgrades it to named paths automatically." />}
		{node.type === 'condition.if_else' && node.type_version >= 2 && <CriteriaBranchesEditor config={config} fields={conditionFields} outputNodes={outputNodes} outputPaths={outputPaths} update={update} primaryDoctype={primaryDoctype} removeBranch={(handle, branches) => actions.updateNodeAndRemoveEdges(node.id, { ...config, branches }, (graph?.edges || []).filter((edge) => edge.source === node.id && edge.source_handle === handle).map((edge) => edge.id), `node:${node.id}:branches:remove`)} />}
		{node.type === 'condition.random_split' && <InspectorSection title="Random percentage split" description="Send each enrolled record to one named path. The same run always receives the same path, including after retries."><div className="space-y-2" data-config-path="branches">{(Array.isArray(config.branches) ? config.branches : []).map((candidate, index) => { const branch = candidate as Record<string, unknown>; const branches = Array.isArray(config.branches) ? config.branches as Array<Record<string, unknown>> : []; return <div className="grid grid-cols-[1fr_88px_auto] items-end gap-2 rounded-xl border border-[var(--border-color)] p-3" key={String(branch.handle || index)}><div><label className={labelClass}>Path {index + 1} name</label><input className={inputClass} value={String(branch.name || '')} onChange={(event) => update({ ...config, branches: branches.map((item, itemIndex) => itemIndex === index ? { ...item, name: event.target.value } : item) }, `branches:${index}:name`)} /></div><div><label className={labelClass}>Percent</label><input className={inputClass} type="number" min="0.01" max="100" step="0.01" value={Number(branch.percentage || 0)} onChange={(event) => update({ ...config, branches: branches.map((item, itemIndex) => itemIndex === index ? { ...item, percentage: Number(event.target.value) } : item) }, `branches:${index}:percentage`)} /></div><button type="button" className="icon-button mb-0.5 hover:!text-red-600" disabled={branches.length <= 2} aria-label={`Remove path ${index + 1}`} onClick={() => actions.updateNodeAndRemoveEdges(node.id, { ...config, branches: branches.filter((_, itemIndex) => itemIndex !== index) }, (graph?.edges || []).filter((edge) => edge.source === node.id && edge.source_handle === branch.handle).map((edge) => edge.id), `node:${node.id}:random-path:remove`)}><Trash2 size={13} /></button></div> })}</div><div className="flex items-center justify-between gap-3"><button type="button" className="btn-core btn-secondary !text-[10px]" disabled={(Array.isArray(config.branches) ? config.branches.length : 0) >= 20} onClick={() => { const branches = Array.isArray(config.branches) ? config.branches as Array<Record<string, unknown>> : []; const id = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`; update({ ...config, branches: [...branches, { handle: `split-${id}`, name: `Group ${branches.length + 1}`, percentage: 0 }] }, 'branches:add') }}><Plus size={12} />Add path</button><span className={`text-[10px] font-bold ${Math.abs((Array.isArray(config.branches) ? config.branches : []).reduce((sum, item) => sum + Number((item as Record<string, unknown>).percentage || 0), 0) - 100) < 0.000001 ? 'text-emerald-600' : 'text-red-600'}`}>Total {(Array.isArray(config.branches) ? config.branches : []).reduce((sum, item) => sum + Number((item as Record<string, unknown>).percentage || 0), 0)}%</span></div><Hint>Percentages must total 100. Selection uses a deterministic hash of the run and step, so retries cannot move a record between experiment groups.</Hint></InspectorSection>}
		{node.type === 'trigger.manual' && <InspectorSection title="Manual enrollment" description="Operators decide exactly which record enters this workflow."><Hint>Enroll individual records from Run history. Foundation allows one enrollment for each workflow and record.</Hint></InspectorSection>}
		{node.type === 'delay.fixed' && <InspectorSection title="Delay settings" description="Choose a readable unit; the run is stored durably while it waits."><DurationEditor key={node.id} seconds={Number(config.seconds || 3600)} initialUnit={String(config.duration_unit || '')} allowBusinessDays onChange={(seconds, duration, unit) => update({ ...config, seconds, duration, duration_unit: unit }, 'duration')} /><Hint>Business days skip Saturday and Sunday. Maximum duration is one year.</Hint></InspectorSection>}
		{node.type === 'delay.drip' && <InspectorSection title="Drip records in batches" description="Release only the selected number of records, then wait before opening the next durable batch."><div><label className={labelClass}>Records per batch</label><input type="number" min={1} max={10000} className={inputClass} value={Number(config.batch_size || 100)} onChange={(event) => update({ ...config, batch_size: Number(event.target.value) }, 'batch_size')} /></div><DurationEditor key={`${node.id}-interval`} seconds={Number(config.interval_seconds || 3600)} onChange={(interval_seconds, interval_duration, interval_unit) => update({ ...config, interval_seconds, interval_duration, interval_unit }, 'interval')} /><Hint>Reservations are transaction-locked per published version and step. Restarts do not lose queued batches.</Hint></InspectorSection>}
		{node.type === 'delay.until_date' && <InspectorSection title="Wait until a date and time" description="Resume at a fixed moment or at the value stored in a permitted record field."><div><label className={labelClass}>Date/time source</label><select className={inputClass} value={String(config.mode || (config.datetime ? 'literal' : 'field'))} onChange={(event) => update({ ...config, mode: event.target.value }, 'mode')}><option value="literal">Specific date and time</option><option value="field">Date from the enrolled record</option></select></div>{String(config.mode || (config.datetime ? 'literal' : 'field')) === 'literal' ? <div data-config-path="datetime"><label className={labelClass}>Date and time</label><input type="datetime-local" className={inputClass} value={String(config.datetime || '')} onChange={(event) => update({ ...config, datetime: event.target.value }, 'datetime')} /></div> : <div data-config-path="field"><label className={labelClass}>Record field</label><FieldPicker fields={scalarReadFields.filter((item) => ['Date', 'Datetime'].includes(item.fieldtype))} value={String(config.field || '')} onChange={(value) => update({ ...config, field: value }, 'field')} /></div>}<Hint>A past date continues immediately. Future dates use the same durable timer recovery as duration waits.</Hint></InspectorSection>}
		{node.type === 'transform.value' && <InspectorSection title="Transform a reusable value" description="Create output for later actions without changing the enrolled record."><div><label className={labelClass}>Operation</label><select className={inputClass} value={String(config.operation || 'coalesce')} onChange={(event) => update({ ...config, operation: event.target.value }, 'operation')}><option value="coalesce">First non-empty value</option><option value="concat">Join values</option><option value="upper">Uppercase</option><option value="lower">Lowercase</option><option value="parse_number">Convert text to number</option><option value="format_number">Format number</option><option value="format_phone">Format phone (E.164)</option><option value="format_currency">Format currency</option><option value="random_number">Generate deterministic random number</option><option value="math">Math calculation</option></select></div>{config.operation !== 'random_number' && <div data-config-path="values" className="space-y-2">{(Array.isArray(config.values) ? config.values : []).map((candidate, index) => { const value = candidate && typeof candidate === 'object' && !Array.isArray(candidate) ? candidate as WorkflowValueSpec : { kind: 'literal', value: candidate } as WorkflowValueSpec; return <div className="rounded-xl border border-[var(--border-color)] p-3" key={index}><div className="mb-2 flex items-center justify-between"><span className="text-light text-[9px] font-bold uppercase">Input {index + 1}</span><button type="button" className="icon-button !size-7 hover:!text-red-600" onClick={() => update({ ...config, values: (config.values as unknown[]).filter((_, itemIndex) => itemIndex !== index) }, `values:${index}:remove`)} aria-label={`Remove input ${index + 1}`}><Trash2 size={13} /></button></div><ValueSourceEditor assignment={{ field: String(index), value }} sourceFields={readFields} outputNodes={outputNodes} outputPaths={outputPaths} onChange={(assignment) => update({ ...config, values: (config.values as unknown[]).map((item, itemIndex) => itemIndex === index ? assignment.value : item) }, `values:${index}`)} /></div> })}<button type="button" className="btn-core btn-secondary w-full !text-[10px]" onClick={() => update({ ...config, values: [...(Array.isArray(config.values) ? config.values : []), { kind: 'literal', value: '' }] }, 'values:add')}><Plus size={12} />Add input</button></div>}{config.operation === 'concat' && <div><label className={labelClass}>Separator</label><input className={inputClass} value={String(config.separator || '')} onChange={(event) => update({ ...config, separator: event.target.value }, 'separator')} /></div>}{['format_number', 'format_currency'].includes(String(config.operation)) && <div className="grid grid-cols-2 gap-2"><div><label className={labelClass}>Decimal places</label><input type="number" min="0" max="12" className={inputClass} value={Number(config.decimals ?? 2)} onChange={(event) => update({ ...config, decimals: Number(event.target.value) }, 'decimals')} /></div>{config.operation === 'format_currency' && <div><label className={labelClass}>Currency code/symbol</label><input className={inputClass} value={String(config.currency || '')} onChange={(event) => update({ ...config, currency: event.target.value }, 'currency')} placeholder="EUR" /></div>}</div>}{config.operation === 'format_phone' && <div><label className={labelClass}>Default country code</label><input className={inputClass} value={String(config.country_code || '')} onChange={(event) => update({ ...config, country_code: event.target.value }, 'country_code')} placeholder="49" /></div>}{config.operation === 'math' && <div><label className={labelClass}>Calculation</label><select className={inputClass} value={String(config.math_operation || 'add')} onChange={(event) => update({ ...config, math_operation: event.target.value }, 'math_operation')}><option value="add">Add</option><option value="subtract">Subtract</option><option value="multiply">Multiply</option><option value="divide">Divide</option><option value="modulo">Modulo</option><option value="power">Power</option></select></div>}{config.operation === 'random_number' && <div className="grid grid-cols-2 gap-2"><div><label className={labelClass}>Minimum</label><input type="number" className={inputClass} value={Number(config.minimum ?? 0)} onChange={(event) => update({ ...config, minimum: Number(event.target.value) }, 'minimum')} /></div><div><label className={labelClass}>Maximum</label><input type="number" className={inputClass} value={Number(config.maximum ?? 100)} onChange={(event) => update({ ...config, maximum: Number(event.target.value) }, 'maximum')} /></div><label className="text-body col-span-2 flex items-center gap-2 text-[11px]"><input type="checkbox" checked={Boolean(config.integer ?? true)} onChange={(event) => update({ ...config, integer: event.target.checked ? 1 : 0 }, 'integer')} />Whole numbers only</label></div>}<Hint>This step only creates output named value. Use that output in a later update/send/create action when you want to persist or communicate it.</Hint></InspectorSection>}
        {node.type === 'action.update_record' && <InspectorSection title={`Update ${primaryDoctype || 'record'}`} description="Add one or more permission-safe scalar or Table MultiSelect field changes.">{metadataIssues.write && <Hint>{metadataIssues.write} This action cannot publish for the current user.</Hint>}<AssignmentEditor config={config} fields={assignmentFields} sourceFields={readFields} outputNodes={outputNodes} outputPaths={outputPaths} update={update} referenceDoctype={primaryDoctype} /></InspectorSection>}
        {node.type === 'action.create_record' && <InspectorSection title="Create a business record" description="Choose the record type, then map every value Frappe needs to insert it successfully."><div data-config-path="target_doctype"><label className={labelClass}>Target DocType <span className="text-red-500">*</span></label><AsyncCombobox ariaLabel="Target DocType" value={String(config.target_doctype || '')} onChange={(value) => update({ ...config, target_doctype: value, assignments: [] }, 'target_doctype')} loadOptions={loadTargetDoctypes} placeholder="Search creatable DocTypes…" /></div>{metadataIssues.target && <Hint>{metadataIssues.target} Choose another target before publishing.</Hint>}{unsupportedMandatoryTargetFields.length > 0 && <div className="rounded-xl border border-red-200 bg-red-50 p-3 text-red-700 dark:border-red-900 dark:bg-red-500/10 dark:text-red-300"><p className="flex items-center gap-2 text-[10.5px] font-bold"><AlertTriangle size={13} />This DocType cannot be created safely by this step</p><p className="mt-1 text-[9.5px] leading-4">It requires complex child data that the field mapper does not support: {unsupportedMandatoryTargetFields.map((field) => field.label).join(', ')}.</p><p className="mt-1 text-[9.5px] font-semibold">Choose a different target DocType. Publishing is blocked to prevent a runtime failure.</p></div>}{config.target_doctype ? targetMetadataLoading ? <div className="rounded-xl border border-[var(--border-color)] bg-[var(--subtle-fg)] px-4 py-6 text-center" role="status"><LoaderCircle className="mx-auto animate-spin text-brand-500" size={18} /><p className="text-heading mt-2 text-[10.5px] font-bold">Checking required fields…</p><p className="text-muted mt-1 text-[9.5px]">Loading permissions and creation rules for {String(config.target_doctype)}.</p></div> : <div data-config-path="assignments"><AssignmentEditor config={config} fields={targetAssignmentFields} sourceFields={readFields} outputNodes={outputNodes} outputPaths={outputPaths} update={update} referenceDoctype={String(config.target_doctype || '')} createMode /></div> : <div className="rounded-xl border border-dashed border-[var(--dark-border-color)] bg-[var(--subtle-fg)] px-4 py-7 text-center"><Sparkles className="mx-auto text-brand-500" size={18} /><p className="text-heading mt-2 text-[11px] font-bold">Choose what to create</p><p className="text-muted mx-auto mt-1 max-w-56 text-[9.5px] leading-4">After choosing a DocType, mandatory fields appear automatically and are checked before publishing.</p></div>}</InspectorSection>}
        {node.type === 'action.create_todo' && <InspectorSection title="ToDo details" description="Create linked follow-up work for a Frappe user."><div><label className={labelClass}>Assign to user</label><AsyncCombobox ariaLabel="Assign to user" value={String(config.allocated_to || '')} onChange={(value) => update({ ...config, allocated_to: value }, 'allocated_to')} loadOptions={loadUsers} placeholder="Search enabled users…" /></div>{text('description', 'Task description', true, 'What needs to happen next?')}<div><label className={labelClass}>Priority</label><select className={inputClass} value={String(config.priority || 'Medium')} onChange={(event) => update({ ...config, priority: event.target.value }, 'priority')}><option>Low</option><option>Medium</option><option>High</option></select></div></InspectorSection>}
        {node.type === 'action.add_comment' && <InspectorSection title="Timeline comment" description={`Add an auditable comment to the enrolled ${primaryDoctype || 'record'}.`}>{text('content', 'Comment', true, 'Write a helpful timeline note…')}</InspectorSection>}
		{node.type === 'action.create_note' && <InspectorSection title="Create Desk Note" description={`Create a note with a link back to this ${primaryDoctype || 'record'}.`}>{text('title', 'Note title', false, 'Follow-up note')}{text('content', 'Note content', true, 'Write the note…')}</InspectorSection>}
		{node.type === 'action.copy_record' && <InspectorSection title="Copy enrolled record" description={`Create a new ${primaryDoctype || 'record'} from the current values.`}><Hint>Frappe copy rules clear identity and no-copy fields. The execution user needs create permission, and normal validation still applies.</Hint></InspectorSection>}
		{node.type === 'action.merge_contact' && <InspectorSection title="Merge Contact" description="Merge this Contact into one unambiguous older canonical Contact."><div><label className={labelClass}>Identity fields</label><div className="space-y-2">{[['email_id', 'Email address'], ['phone', 'Phone'], ['mobile_no', 'Mobile number']].map(([value, label]) => { const selected = (Array.isArray(config.match_fields) ? config.match_fields : []).includes(value); return <label className="text-body flex items-center gap-2 text-[11px]" key={value}><input type="checkbox" checked={selected} onChange={() => { const fields = new Set(Array.isArray(config.match_fields) ? config.match_fields as string[] : []); if (selected) fields.delete(value); else fields.add(value); update({ ...config, match_fields: [...fields] }, 'match_fields') }} />{label}</label> })}</div></div><div><label className={labelClass}>Matching rule</label><select className={inputClass} value={String(config.match_mode || 'all')} onChange={(event) => update({ ...config, match_mode: event.target.value }, 'match_mode')}><option value="all">All selected fields match</option><option value="any">Any selected field matches</option></select></div><p className="rounded-lg border border-red-200 bg-red-50 p-3 text-[10px] leading-4 text-red-700 dark:border-red-900 dark:bg-red-500/10 dark:text-red-300">This is destructive: links move to the canonical Contact and the enrolled Contact is removed. Ambiguous matches fail safely.</p></InspectorSection>}
		{node.type === 'action.unassign_record' && <InspectorSection title="Remove assigned users" description="Close every open Frappe assignment linked to this record."><Hint>The action closes assignment ToDos; it does not change the document owner field.</Hint></InspectorSection>}
		{node.type === 'action.verify_email' && <InspectorSection title="Verify email format" description="Validate an email value without sending a message."><ValueSourceEditor assignment={{ field: 'email', value: (config.email as WorkflowValueSpec) || { kind: 'literal', value: '' } }} sourceFields={readFields} outputNodes={outputNodes} outputPaths={outputPaths} onChange={(assignment) => update({ ...config, email: assignment.value }, 'email')} /><Hint>Outputs valid and reason. This validates syntax; use a controlled provider webhook when mailbox deliverability verification is required.</Hint></InspectorSection>}
		{node.type === 'action.mark_communications_read' && <InspectorSection title="Mark conversations read" description="Mark received Frappe Communications linked to this record as seen."><Hint>This updates only the enrolled record's received Communication rows.</Hint></InspectorSection>}
		{node.type === 'action.remove_from_workflow' && <InspectorSection title="Remove from workflow" description="Cancel active runs for this record in the selected workflow."><div><label className={labelClass}>Target workflow</label><AsyncCombobox ariaLabel="Target workflow" value={String(config.target_workflow || 'current')} onChange={(value) => update({ ...config, target_workflow: value || 'current' }, 'target_workflow')} loadOptions={(search) => call<{ rows: Array<{ name: string; title: string }> }>('list_workflows', { search, primary_doctype: primaryDoctype, page_length: 20 }).then((r) => [{ value: 'current', label: 'This workflow', description: 'End this path' }, ...r.rows.filter((row) => row.name !== workflowId).map((row) => ({ value: row.name, label: row.title, description: row.name }))])} placeholder="Choose workflow…" /></div><Hint>Choosing this workflow ends the current path after cancelling any parallel active runs for the same record.</Hint></InspectorSection>}
		{node.type === 'action.complete_goal' && <InspectorSection title="Complete goal" description="Record a named goal and end this path successfully.">{text('goal', 'Goal name', false, 'Goal reached')}<Hint>You can also use the workflow-wide goal condition when completion should be driven by record fields.</Hint></InspectorSection>}
		{node.type === 'action.go_to' && <InspectorSection title="Go to an existing step" description="Continue at one existing step so paths can converge without duplicating actions."><div><label className={labelClass}>Destination step</label><select className={inputClass} value={String(config.target_node_id || '')} onChange={(event) => update({ ...config, target_node_id: event.target.value }, 'target_node_id')}><option value="">Choose a step…</option>{graph?.nodes.filter((candidate) => candidate.id !== node.id && candidate.id !== graph.start_node_id).map((candidate) => <option value={candidate.id} key={candidate.id}>{nodeLabels[candidate.type]} · {candidate.id.slice(0, 8)}</option>)}</select></div><Hint>The server treats this as a real graph link, rejects loops, and does not allow a second outgoing edge from this step.</Hint></InspectorSection>}
		{node.type === 'action.notify_user' && <InspectorSection title="Internal notification" description="Send a notification inside Frappe without exposing record data externally."><div><label className={labelClass}>Audience</label><select className={inputClass} value={String(config.audience || 'specific')} onChange={(event) => update({ ...config, audience: event.target.value }, 'audience')}><option value="specific">Specific user</option><option value="assigned">Users assigned to this record</option><option value="all">All enabled system users</option></select></div>{String(config.audience || 'specific') === 'specific' && <div><label className={labelClass}>Recipient user</label><AsyncCombobox ariaLabel="Recipient user" value={String(config.for_user || '')} onChange={(value) => update({ ...config, for_user: value }, 'for_user')} loadOptions={loadUsers} placeholder="Search enabled users…" /></div>}{text('subject', 'Subject', false, 'What happened?')}{text('message', 'Message', true, 'Add useful context for the recipient…')}{config.audience === 'all' && <Hint>Safety limit: at most 500 enabled System Users receive one notification each.</Hint>}</InspectorSection>}
        {node.type === 'action.send_email' && <SendEmailEditor config={config} workflowId={workflowId} primaryDoctype={primaryDoctype || ''} update={update} recipientEditor={bindingEditor('recipient', 'Recipient email')} subjectOverrideEditor={bindingEditor('subject_override', 'Subject override (optional)')} subjectEditor={bindingEditor('subject', 'Subject')} messageEditor={bindingEditor('message', 'Message')} />}
        {node.type === 'action.send_sms' && <InspectorSection title="Consent-aware SMS" description="Submit synchronously through Frappe SMS Settings and report the gateway response.">{bindingEditor('recipient', 'Recipient mobile')}{bindingEditor('message', 'Message')}{text('purpose', 'Consent purpose', false, 'workflow')}<label className="text-body flex items-center gap-2 text-[11px]"><input type="checkbox" checked={Boolean(config.require_consent ?? true)} onChange={(event) => update({ ...config, require_consent: event.target.checked ? 1 : 0 }, 'require_consent')} />Require a current consent grant <HelpTooltip label="Consent requirement" content="When enabled, the recipient needs a current GRANTED SMS consent record for the same purpose. A missing, expired, denied, or revoked grant blocks submission." /></label><Hint>Native Frappe SMS Settings remain authoritative.</Hint></InspectorSection>}
        {node.type === 'action.webhook' && <InspectorSection title="Controlled webhook" description="POST JSON only to an exact, allowlisted public HTTPS hostname."><div><label className={labelClass}>Integration secret</label><AsyncCombobox ariaLabel="Integration secret" value={String(config.integration_secret || '')} onChange={(value) => update({ ...config, integration_secret: value }, 'integration_secret')} loadOptions={loadSecrets} placeholder="Search enabled secrets…" /></div>{text('url', 'HTTPS endpoint', false, 'https://api.example.com/events')}<JsonPayloadEditor key={`${node.id}-payload`} value={config.payload} onChange={(value) => update({ ...config, payload: value }, 'payload')} />{text('purpose', 'Purpose', false, 'workflow')}<Hint>Redirects, IP literals, private networks, and non-allowlisted hosts are blocked. Every request includes an idempotency key.</Hint></InspectorSection>}
		{node.type === 'action.instagram_message' && <InspectorSection title="Instagram Direct message" description="Send through an approved Meta messaging endpoint using a controlled bearer integration secret."><div><label className={labelClass}>Meta integration secret</label><AsyncCombobox ariaLabel="Meta integration secret" value={String(config.integration_secret || '')} onChange={(value) => update({ ...config, integration_secret: value }, 'integration_secret')} loadOptions={loadSecrets} placeholder="Search enabled secrets…" /></div>{text('url', 'Meta Graph API endpoint', false, 'https://graph.facebook.com/v23.0/me/messages')}{bindingEditor('recipient_id', 'Instagram-scoped recipient ID')}{bindingEditor('message', 'Message')}{text('purpose', 'Consent purpose', false, 'workflow')}<label className="text-body flex items-center gap-2 text-[11px]"><input type="checkbox" checked={Boolean(config.require_consent ?? true)} onChange={(event) => update({ ...config, require_consent: event.target.checked ? 1 : 0 }, 'require_consent')} />Require a current Instagram consent grant</label><Hint>The endpoint hostname must be allowlisted. Simulation never sends; runtime records one idempotent external effect and the provider response hash.</Hint></InspectorSection>}
		{node.type === 'action.asana' && <InspectorSection title="Asana task / project" description="Use the installed Asana Integration credentials and expose the created or updated GID for later steps."><div><label className={labelClass}>Operation</label><select className={inputClass} value={String(config.operation || 'create_task')} onChange={(event) => update({ ...config, operation: event.target.value }, 'operation')}><option value="create_task">Create task</option><option value="update_task">Update task</option><option value="create_subtask">Create subtask</option><option value="create_project">Create project</option></select></div>{['update_task', 'create_subtask'].includes(String(config.operation)) && bindingEditor('target_gid', config.operation === 'create_subtask' ? 'Parent task GID' : 'Task GID')}<JsonPayloadEditor key={`${node.id}-asana-payload`} value={config.payload} onChange={(value) => update({ ...config, payload: value }, 'payload')} /><Hint>Use Asana API field names such as name, notes, due_on, assignee, projects, custom_fields, completed, team, or privacy_setting. Record and prior-step value bindings are resolved immediately before the call.</Hint></InspectorSection>}
		{node.type === 'trigger.schedule' && <InspectorSection title="Scheduled trigger" description="Publish this workflow, then create and enable its durable schedule from the Enrollment page."><Hint>Schedules own their timezone, audience filters, frequency, catch-up policy, overlap policy, version policy, and processing limits.</Hint></InspectorSection>}
		{node.type === 'trigger.webhook' && <InspectorSection title="Incoming webhook" description="Publish this workflow, then create its managed endpoint from Enrollment operations."><Hint>The endpoint authenticates before configuration lookup, maps one exact permitted record, requires an idempotency key, and writes a durable outbox event. Secrets are only shown when created or rotated.</Hint></InspectorSection>}
		{node.type === 'trigger.event' && <EventEnrollmentEditor config={config} typeVersion={node.type_version} events={triggerEventTypes} recordFields={conditionFields} primaryDoctype={primaryDoctype} update={update} />}
		{node.type === 'trigger.any' && <MultiTriggerEditor config={config} typeVersion={node.type_version} events={triggerEventTypes} fields={conditionFields} primaryDoctype={primaryDoctype} update={update} />}
        {node.type === 'condition.switch' && <InspectorSection title="Value branch" description="Route to different paths based on a single scalar field's exact value."><div><label className={labelClass}>Branch field</label><FieldPicker fields={readFields.filter((field) => field.capabilities?.switch ?? !['Table', 'Table MultiSelect'].includes(field.fieldtype))} value={String(config.field || "")} onChange={(value) => update({ ...config, field: value }, "field")} /></div><div><label className={labelClass}>Cases (one value per line)</label><textarea className={inputClass} rows={5} value={(Array.isArray(config.cases) ? config.cases : []).map((item) => typeof item === 'object' && item ? String((item as Record<string, unknown>).value || '') : String(item || '')).join("\n")} onChange={(event) => { const previous = Array.isArray(config.cases) ? config.cases : []; const cases = event.target.value.split("\n").map((value) => value.trim()).filter(Boolean).map((value, index) => ({ value, handle: typeof previous[index] === 'object' && previous[index] ? String((previous[index] as Record<string, unknown>).handle || `case-${index + 1}`) : `case-${index + 1}` })); update({ ...config, cases }, "cases") }} placeholder="Open" /></div><Hint>Each case value creates a separate edge port. The default edge fires when no case matches.</Hint></InspectorSection>}
		{node.type === 'condition.deduplicate' && <InspectorSection title="Deduplicate" description="Check whether another record matches one or more selected scalar fields."><div><label className={labelClass}>Match fields</label><MultiValueInput values={(node.type_version >= 2 ? (Array.isArray(config.match_fields) ? config.match_fields : []) : [String(config.match_field || '')]).map(String).filter(Boolean)} onChange={(values) => update(node.type_version >= 2 ? { ...config, match_fields: values, match_mode: String(config.match_mode || 'all') } : { ...config, match_field: values[0] || '' }, 'match_fields')} loadOptions={async (search) => readFields.filter((field) => (field.capabilities?.deduplicate ?? !['Table', 'Table MultiSelect'].includes(field.fieldtype)) && (!search || `${field.label} ${field.fieldname}`.toLowerCase().includes(search.toLowerCase()))).map((field) => ({ value: field.fieldname, label: field.label, description: field.fieldtype }))} placeholder="Search permitted fields…" ariaLabel="Deduplication fields" /></div>{node.type_version >= 2 && <div><label className={labelClass}>Matching rule</label><select className={inputClass} value={String(config.match_mode || 'all')} onChange={(event) => update({ ...config, match_mode: event.target.value }, 'match_mode')}><option value="all">All selected fields must match (AND)</option><option value="any">Any selected field may match (OR)</option></select></div>}<Hint>Empty values never match. Outputs: duplicate_name, is_duplicate, and matched_fields.</Hint></InspectorSection>}
		{node.type === 'delay.until_event' && <EventWaitEditor config={config} typeVersion={node.type_version} events={waitEventTypes} outputNodes={outputNodes} update={update} nodeId={node.id} primaryDoctype={primaryDoctype} timeoutPathConnected={Boolean(graph?.edges.some((edge) => edge.source === node.id && edge.source_handle === 'timeout'))} />}
        {node.type === 'delay.business_hours' && <InspectorSection title="Business hours" description="Resume only during allowed working hours to avoid interrupting customers."><div><label className={labelClass}>Holiday calendar (optional)</label><input className={inputClass} value={String(config.calendar || '')} onChange={(event) => update({ ...config, calendar: event.target.value }, 'calendar')} placeholder="Holiday List name" /></div><div><label className={labelClass}>Timezone</label><input className={inputClass} value={String(config.timezone || 'UTC')} onChange={(event) => update({ ...config, timezone: event.target.value }, 'timezone')} placeholder="Asia/Kolkata" /></div><div className="grid grid-cols-2 gap-2"><div><label className={labelClass}>Starts</label><input type="time" className={inputClass} value={String(config.start_time || '09:00')} onChange={(event) => update({ ...config, start_time: event.target.value }, 'start_time')} /></div><div><label className={labelClass}>Ends</label><input type="time" className={inputClass} value={String(config.end_time || '17:00')} onChange={(event) => update({ ...config, end_time: event.target.value }, 'end_time')} /></div></div><div><label className={labelClass}>Working days</label><div className="grid grid-cols-4 gap-1.5">{['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map((label, day) => { const selected = (Array.isArray(config.weekdays) ? config.weekdays : [0, 1, 2, 3, 4]).includes(day); return <label className={`cursor-pointer rounded-lg border px-2 py-2 text-center text-[9px] font-bold ${selected ? 'border-brand-300 bg-brand-50 text-brand-700 dark:bg-brand-500/10' : 'border-[var(--border-color)] text-[var(--text-muted)]'}`} key={label}><input type="checkbox" className="sr-only" checked={selected} onChange={() => { const days = new Set(Array.isArray(config.weekdays) ? config.weekdays as number[] : [0, 1, 2, 3, 4]); if (selected) days.delete(day); else days.add(day); update({ ...config, weekdays: [...days].sort() }, 'weekdays') }} />{label}</label> })}</div></div><Hint>The durable timer converts this local window into the site timezone and skips non-working days and holidays.</Hint></InspectorSection>}
        {node.type === 'transform.associated_record' && <InspectorSection title="Associated record" description="Fetch a value from a linked record without changing the enrolled document."><div><label className={labelClass}>Link field on this record</label><FieldPicker fields={scalarReadFields.filter((f) => f.fieldtype === 'Link')} value={String(config.reference_field || '')} onChange={(value) => update({ ...config, reference_field: value }, 'reference_field')} /></div><div><label className={labelClass}>Field to read from linked record</label><input className={inputClass} value={String(config.fetch_field || '')} onChange={(event) => update({ ...config, fetch_field: event.target.value }, 'fetch_field')} placeholder="status" /></div><Hint>Outputs: value (the fetched value), linked_name (the linked record name).</Hint></InspectorSection>}
        {node.type === 'transform.child_records' && <InspectorSection title="Child records" description="Read permitted values from ordinary child tables or Table MultiSelect rows."><div><label className={labelClass}>Child table field</label><FieldPicker fields={readFields.filter((f) => f.capabilities?.child_collection ?? ['Table', 'Table MultiSelect'].includes(f.fieldtype))} value={String(config.child_table_field || '')} onChange={(value) => update({ ...config, child_table_field: value, fetch_field: '' }, 'child_table_field')} /></div><div><label className={labelClass}>Field to read from each child row</label><FieldPicker fields={childFields} value={String(config.fetch_field || '')} onChange={(value) => update({ ...config, fetch_field: value }, 'fetch_field')} /></div><Hint>Outputs: values (array), count (number of rows). Table MultiSelect exposes its linked values without flattening them into a comma-separated string.</Hint></InspectorSection>}
        {node.type === 'action.call_subflow' && <InspectorSection title="Call subflow" description="Execute another published workflow as a nested subflow."><div><label className={labelClass}>Subflow workflow</label><AsyncCombobox ariaLabel="Subflow workflow" value={String(config.subflow_id || '')} onChange={(value) => update({ ...config, subflow_id: value }, 'subflow_id')} loadOptions={(search) => call<{ rows: Array<{ name: string; title: string }> }>('list_workflows', { search, status: 'ACTIVE', primary_doctype: primaryDoctype, exclude_workflow: workflowId, page_length: 20 }).then((r) => r.rows.map((row) => ({ value: row.name, label: row.title, description: row.name })))} placeholder="Search active compatible workflows…" /></div><label className="text-body flex items-center gap-2 text-[11px]"><input type="checkbox" checked={Boolean(config.wait_for_completion ?? true)} onChange={(event) => update({ ...config, wait_for_completion: event.target.checked ? 1 : 0 }, 'wait_for_completion')} />Wait for subflow to complete before continuing</label><Hint>The server rejects missing, inactive, mismatched, self-referencing, and cyclic subflows. Outputs: run_id, status.</Hint></InspectorSection>}
        {node.type === 'action.numeric_adjust' && <InspectorSection title="Numeric adjust" description="Safely increment or decrement a number field without reading it first."><div><label className={labelClass}>Target field</label><FieldPicker fields={writeFields.filter((f) => ['Int', 'Float', 'Currency', 'Percent'].includes(f.fieldtype))} value={String(config.field || '')} onChange={(value) => update({ ...config, field: value }, 'field')} /></div><div><label className={labelClass}>Operation</label><select className={inputClass} value={String(config.operation || 'add')} onChange={(event) => update({ ...config, operation: event.target.value }, 'operation')}><option value="add">Add</option><option value="subtract">Subtract</option><option value="multiply">Multiply</option><option value="set">Set to exact value</option></select></div><div><label className={labelClass}>Amount</label><input type="number" className={inputClass} value={Number(config.amount ?? 1)} onChange={(event) => update({ ...config, amount: Number(event.target.value) }, 'amount')} /></div><Hint>Outputs: new_value, field. The operation is atomic — safe for concurrent workflow runs.</Hint></InspectorSection>}
        {node.type === 'action.manage_association' && <InspectorSection title="Manage association" description="Idempotently link or unlink this record with another."><div><label className={labelClass}>Target DocType</label><AsyncCombobox ariaLabel="Target DocType" value={String(config.target_doctype || '')} onChange={(value) => update({ ...config, target_doctype: value }, 'target_doctype')} loadOptions={loadReadableDoctypes} placeholder="Search readable DocTypes…" /></div><div><label className={labelClass}>Target record name</label><input className={inputClass} value={String(config.target_name || '')} onChange={(event) => update({ ...config, target_name: event.target.value }, 'target_name')} placeholder="Lead-0001" /></div><div><label className={labelClass}>Link field name</label><input className={inputClass} value={String(config.link_field || '')} onChange={(event) => update({ ...config, link_field: event.target.value }, 'link_field')} placeholder="custom_linked_doc" /></div><div><label className={labelClass}>Operation</label><select className={inputClass} value={String(config.operation || 'link')} onChange={(event) => update({ ...config, operation: event.target.value }, 'operation')}><option value="link">Link</option><option value="unlink">Unlink</option></select></div><Hint>Idempotent — re-linking an already linked record is safe. Outputs: target_name, operation.</Hint></InspectorSection>}
        {node.type === 'action.round_robin' && (
          <InspectorSection title={node.type_version === 1 ? 'Legacy deterministic assignment' : 'Atomic round robin assignment'} description={node.type_version === 1 ? 'Preserved version-1 behavior assigns the same record deterministically.' : 'Rotate atomically across currently enabled members, including concurrent runs.'}>
            <div className="mb-3">
              <label className={labelClass}>Assignment type</label>
              <select className={inputClass} value={roundRobinAssignmentType} onChange={(e) => update({ ...config, assignment_type: e.target.value, group: '', users: [] }, 'assignment_type')}>
                <option value="users">Specific Users</option>
                <option value="group">User Group</option>
              </select>
            </div>
            {roundRobinAssignmentType === 'group' ? (
              <div>
                <label className={labelClass}>User Group</label>
                <AsyncCombobox ariaLabel="Round robin User Group" value={String(config.group || '')} onChange={(group) => update({ ...config, group }, 'group')} loadOptions={loadUserGroups} placeholder="Search User Groups…" />
              </div>
            ) : (
              <div>
                <label className={labelClass}>Specific Users</label>
                <MultiValueInput values={(Array.isArray(config.users) ? config.users : []).map(String)} onChange={(users) => update({ ...config, users }, 'users')} loadOptions={loadUsers} placeholder="Add enabled users…" ariaLabel="Round robin users" />
              </div>
            )}
            <Hint>{node.type_version === 1 ? 'Legacy node: the same record always maps to the same member.' : 'Version 2: a locked workflow-version cursor provides true rotation and retry safety.'} The selected pool type is stored explicitly, so a username can never be mistaken for a User Group. Outputs: assigned_to and group.</Hint>
          </InspectorSection>
        )}
        {node.type === 'action.delete_record' && <InspectorSection title="Delete record" description="Permanently delete the enrolled record. This action cannot be undone."><p className="flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 p-3 text-[11px] leading-4 text-red-700 dark:border-red-900 dark:bg-red-500/10 dark:text-red-300"><AlertTriangle className="mt-0.5 shrink-0" size={13} />This step permanently deletes the enrolled record. Ensure the workflow has the correct eligibility conditions before publishing.</p></InspectorSection>}
        {node.type === 'end.complete' && <InspectorSection title="Successful completion"><p className="flex items-start gap-2 rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-[11px] leading-[18px] text-emerald-800 dark:border-emerald-900 dark:bg-emerald-500/10 dark:text-emerald-300"><CheckCircle2 className="mt-0.5 shrink-0" size={15} />Records reaching this step finish their run successfully.</p></InspectorSection>}
      </div>
	  <footer className="flex shrink-0 items-center justify-between gap-3 border-t border-[var(--border-color)] bg-[var(--card-bg)] px-4 py-3"><span className={`flex min-w-0 items-center gap-1.5 text-[9.5px] font-semibold ${nodeIssues.length ? 'text-red-600' : 'text-emerald-600'}`}>{nodeIssues.length ? <AlertTriangle size={12} /> : <CheckCircle2 size={12} />}{nodeIssues.length ? `${nodeIssues.length} issue${nodeIssues.length === 1 ? '' : 's'} to fix` : 'Changes save automatically'}</span><button type="button" className="btn-core btn-primary !min-h-8 !px-4 !text-[10px]" onClick={() => actions.select()}>Done</button></footer>
    </aside>
  )
}
