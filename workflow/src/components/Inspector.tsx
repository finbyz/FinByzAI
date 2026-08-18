import {
  AlertTriangle,
  CheckCircle2,
  Info,
  LoaderCircle,
  Plus,
  Settings2,
  Sparkles,
  Trash2,
  X,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AsyncCombobox, type ComboboxOption } from './AsyncCombobox'
import { HelpTooltip } from './HelpTooltip'
import { call, fetchFieldCatalog, invalidateMetadataCaches, searchDoctypes, searchLink } from '../lib/api'
import { availableOutputNodes, isRequiredAuthoringValueMissing, outputCatalog, parseWebhookPayload } from '../lib/inspectorAuthoring'
import { useWorkflowActions, useWorkflowDocument, useWorkflowEditor } from '../state/WorkflowContext'
import type { FieldCatalogItem, NodeCatalogItem, NodeConfig, ValidationIssue, WorkflowValueSpec } from '../types'

import {
  inputClass,
  labelClass,
  nodeLabels,
  nodeIcons,
  InspectorSection,
  Hint,
  FieldPicker,
  ConditionEditor,
  AssignmentEditor,
  ValueSourceEditor,
  TypedValueInput,
} from './InspectorHelpers'

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

export function Inspector() {
  const { graph, workflowId, validation } = useWorkflowDocument()
  const { selectedNodeId } = useWorkflowEditor()
  const actions = useWorkflowActions()
  const node = useMemo(() => graph?.nodes.find((item) => item.id === selectedNodeId), [graph?.nodes, selectedNodeId])
  const primaryDoctype = graph?.primary_doctype
  const [readFields, setReadFields] = useState<FieldCatalogItem[]>([])
  const [writeFields, setWriteFields] = useState<FieldCatalogItem[]>([])
  const [targetFields, setTargetFields] = useState<FieldCatalogItem[]>([])
  const [targetFieldsDoctype, setTargetFieldsDoctype] = useState('')
  const [targetMetadataLoading, setTargetMetadataLoading] = useState(false)
  const [nodeTypes, setNodeTypes] = useState<NodeCatalogItem[]>([])
  const [metadataEpoch, setMetadataEpoch] = useState(0)
  const lastMetadataRefresh = useRef(0)
  const outputPaths = useMemo(() => outputCatalog(nodeTypes), [nodeTypes])
  const outputNodes = useMemo(() => availableOutputNodes(graph, selectedNodeId || '', outputPaths), [graph, outputPaths, selectedNodeId])
  const [metadataIssues, setMetadataIssues] = useState<Record<'read' | 'write' | 'target', string>>({ read: '', write: '', target: '' })
  const loadTargetDoctypes = useCallback((search: string): Promise<ComboboxOption[]> => {
    void metadataEpoch // Re-query an open combobox after metadata invalidation.
    return searchDoctypes('create', search, workflowId).then((rows) => rows.map((row) => ({ value: row.name, label: row.label || row.name, description: row.module })))
  }, [metadataEpoch, workflowId])
  const loadReadableDoctypes = useCallback((search: string): Promise<ComboboxOption[]> => {
    void metadataEpoch // Re-query an open combobox after metadata invalidation.
    return searchDoctypes('read', search, workflowId).then((rows) => rows.map((row) => ({ value: row.name, label: row.label || row.name, description: row.module })))
  }, [metadataEpoch, workflowId])
  const loadUsers = useCallback((search: string): Promise<ComboboxOption[]> => searchLink('User', search, { filters: { enabled: 1 } }).then((rows) => rows.map((row) => ({ value: row.value, label: row.label || row.value, description: row.description }))), [])
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
    if (node?.type !== 'action.create_record' || !targetFields.length || targetFieldsDoctype !== String(node.config.target_doctype || '')) return
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
      <aside className="editor-side-panel flex h-full items-center justify-center border-l border-[var(--border-color)] bg-white/70 dark:bg-[#18212b]/80 backdrop-blur-2xl p-7 text-center max-lg:hidden">
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
    <aside className="editor-side-panel h-full overflow-y-auto border-l border-[var(--border-color)] bg-white/70 dark:bg-[#121b23]/80 backdrop-blur-2xl max-lg:absolute max-lg:inset-y-0 max-lg:right-0 max-lg:z-40 max-lg:w-full sm:max-lg:w-80 max-lg:shadow-2xl">
      <div className="sticky top-0 z-10 flex items-start justify-between border-b border-[var(--border-color)] bg-white/50 dark:bg-[#18212b]/50 px-5 py-4 backdrop-blur-md">
        <div className="flex items-start gap-3">
          <button className="icon-button shrink-0 lg:hidden" onClick={() => actions.select()} aria-label="Close inspector"><X size={16} /></button>
          <span className="grid size-9 shrink-0 place-items-center rounded-[10px] bg-brand-50 text-brand-600 dark:bg-brand-500/10"><NodeIcon size={17} /></span>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5 text-[9px] font-bold uppercase tracking-[0.13em] text-brand-600"><Sparkles size={10} /> Step settings</div>
            <h2 className="text-heading mt-0.5 truncate text-[13px] font-bold">{nodeLabels[node.type] || node.type}</h2>
            <p className="text-light mt-0.5 truncate text-[9px]">{node.id}</p>
          </div>
          {node.id !== graph?.start_node_id && (
            <button className="icon-button shrink-0 hover:!border-red-200 hover:!bg-red-50 hover:!text-red-600 dark:hover:!bg-red-500/10" onClick={() => actions.removeNode(node.id)} aria-label="Delete node"><Trash2 size={16} /></button>
          )}
        </div>
      </div>

      <div className="space-y-3 p-4">
        {metadataIssues.read && <p className="flex gap-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-[10.5px] leading-4 text-amber-800 dark:border-amber-900 dark:bg-amber-500/10 dark:text-amber-300"><Info className="mt-0.5 shrink-0" size={13} />{metadataIssues.read} Existing graph data remains visible, but field editing and publishing are blocked.</p>}
        {nodeIssues.length > 0 && <section aria-label="Step validation" className="rounded-xl border border-red-200 bg-red-50/80 p-3 dark:border-red-900 dark:bg-red-500/10"><p className="flex items-center gap-2 text-[10.5px] font-bold text-red-700 dark:text-red-300"><AlertTriangle size={13} />Fix {nodeIssues.length} issue{nodeIssues.length === 1 ? '' : 's'} in this step</p><div className="mt-2 space-y-1">{nodeIssues.map((issue, index) => <button type="button" className="flex w-full items-start gap-2 rounded-lg px-2 py-1.5 text-left text-[10px] leading-4 text-red-700 transition hover:bg-red-100 focus:outline-none focus:ring-2 focus:ring-red-300 dark:text-red-300 dark:hover:bg-red-500/10" onClick={() => focusIssue(issue)} key={`${issue.code}-${issue.path || ''}-${index}`}><span className="mt-1 size-1 shrink-0 rounded-full bg-current" /><span>{issue.message}{issue.line ? <span className="ml-1 font-semibold">Line {issue.line}:{issue.column || 1}</span> : null}</span></button>)}</div></section>}
        {(node.type === 'trigger.document_insert' || node.type === 'trigger.document_change' || node.type === 'condition.if_else') && <ConditionEditor config={config} fields={conditionFields} update={update} primaryDoctype={primaryDoctype} />}
        {node.type === 'trigger.manual' && <InspectorSection title="Manual enrollment" description="Operators decide exactly which record enters this workflow."><Hint>Enroll individual records from Run history. Foundation allows one enrollment for each workflow and record.</Hint></InspectorSection>}
        {node.type === 'delay.fixed' && <InspectorSection title="Delay settings" description="The run is stored durably while it waits; no worker remains occupied."><div><label className={labelClass}>Wait time in seconds</label><input type="number" min={60} className={inputClass} value={Number(config.seconds || 3600)} onChange={(event) => update({ ...config, seconds: Number(event.target.value) }, 'seconds')} /></div><Hint>Minimum delay is 60 seconds. Overdue timers resume safely after worker restarts.</Hint></InspectorSection>}
        {node.type === 'delay.until_date' && <InspectorSection title="Wait until a record date" description="Resume at the exact value stored in a permitted Date or Datetime field."><FieldPicker fields={scalarReadFields.filter((item) => ['Date', 'Datetime'].includes(item.fieldtype))} value={String(config.field || '')} onChange={(value) => update({ ...config, field: value }, 'field')} /><Hint>A past date continues immediately. Future dates use the same durable timer recovery as fixed delays.</Hint></InspectorSection>}
        {node.type === 'transform.value' && <InspectorSection title="Transform a reusable value" description="Create output for later actions without changing the enrolled record."><div><label className={labelClass}>Operation</label><select className={inputClass} value={String(config.operation || 'coalesce')} onChange={(event) => update({ ...config, operation: event.target.value }, 'operation')}><option value="coalesce">First non-empty value</option><option value="concat">Join values</option><option value="upper">Uppercase</option><option value="lower">Lowercase</option></select></div><div data-config-path="values" className="space-y-2">{(Array.isArray(config.values) ? config.values : []).map((candidate, index) => { const value = candidate && typeof candidate === 'object' && !Array.isArray(candidate) ? candidate as WorkflowValueSpec : { kind: 'literal', value: candidate } as WorkflowValueSpec; return <div className="rounded-xl border border-[var(--border-color)] p-3" key={index}><div className="mb-2 flex items-center justify-between"><span className="text-light text-[9px] font-bold uppercase">Input {index + 1}</span><button type="button" className="icon-button !size-7 hover:!text-red-600" onClick={() => update({ ...config, values: (config.values as unknown[]).filter((_, itemIndex) => itemIndex !== index) }, `values:${index}:remove`)} aria-label={`Remove input ${index + 1}`}><Trash2 size={13} /></button></div><ValueSourceEditor assignment={{ field: String(index), value }} sourceFields={readFields} outputNodes={outputNodes} outputPaths={outputPaths} onChange={(assignment) => update({ ...config, values: (config.values as unknown[]).map((item, itemIndex) => itemIndex === index ? assignment.value : item) }, `values:${index}`)} /></div> })}<button type="button" className="btn-core btn-secondary w-full !text-[10px]" onClick={() => update({ ...config, values: [...(Array.isArray(config.values) ? config.values : []), { kind: 'literal', value: '' }] }, 'values:add')}><Plus size={12} />Add input</button></div>{config.operation === 'concat' && <div><label className={labelClass}>Separator</label><input className={inputClass} value={String(config.separator || '')} onChange={(event) => update({ ...config, separator: event.target.value }, 'separator')} /></div>}<Hint>Inputs can be fixed values, enrolled-record fields, or guaranteed prior-step outputs.</Hint></InspectorSection>}
        {node.type === 'action.update_record' && <InspectorSection title={`Update ${primaryDoctype || 'record'}`} description="Add one or more permission-safe scalar or Table MultiSelect field changes.">{metadataIssues.write && <Hint>{metadataIssues.write} This action cannot publish for the current user.</Hint>}<AssignmentEditor config={config} fields={assignmentFields} sourceFields={readFields} outputNodes={outputNodes} outputPaths={outputPaths} update={update} referenceDoctype={primaryDoctype} /></InspectorSection>}
        {node.type === 'action.create_record' && <InspectorSection title="Create a business record" description="Choose the record type, then map every value Frappe needs to insert it successfully."><div data-config-path="target_doctype"><label className={labelClass}>Target DocType <span className="text-red-500">*</span></label><AsyncCombobox ariaLabel="Target DocType" value={String(config.target_doctype || '')} onChange={(value) => update({ ...config, target_doctype: value, assignments: [] }, 'target_doctype')} loadOptions={loadTargetDoctypes} placeholder="Search creatable DocTypes…" /></div>{metadataIssues.target && <Hint>{metadataIssues.target} Choose another target before publishing.</Hint>}{unsupportedMandatoryTargetFields.length > 0 && <div className="rounded-xl border border-red-200 bg-red-50 p-3 text-red-700 dark:border-red-900 dark:bg-red-500/10 dark:text-red-300"><p className="flex items-center gap-2 text-[10.5px] font-bold"><AlertTriangle size={13} />This DocType cannot be created safely by this step</p><p className="mt-1 text-[9.5px] leading-4">It requires complex child data that the field mapper does not support: {unsupportedMandatoryTargetFields.map((field) => field.label).join(', ')}.</p><p className="mt-1 text-[9.5px] font-semibold">Choose a different target DocType. Publishing is blocked to prevent a runtime failure.</p></div>}{config.target_doctype ? targetMetadataLoading ? <div className="rounded-xl border border-[var(--border-color)] bg-[var(--subtle-fg)] px-4 py-6 text-center" role="status"><LoaderCircle className="mx-auto animate-spin text-brand-500" size={18} /><p className="text-heading mt-2 text-[10.5px] font-bold">Checking required fields…</p><p className="text-muted mt-1 text-[9.5px]">Loading permissions and creation rules for {String(config.target_doctype)}.</p></div> : <div data-config-path="assignments"><AssignmentEditor config={config} fields={targetAssignmentFields} sourceFields={readFields} outputNodes={outputNodes} outputPaths={outputPaths} update={update} referenceDoctype={String(config.target_doctype || '')} createMode /></div> : <div className="rounded-xl border border-dashed border-[var(--dark-border-color)] bg-[var(--subtle-fg)] px-4 py-7 text-center"><Sparkles className="mx-auto text-brand-500" size={18} /><p className="text-heading mt-2 text-[11px] font-bold">Choose what to create</p><p className="text-muted mx-auto mt-1 max-w-56 text-[9.5px] leading-4">After choosing a DocType, mandatory fields appear automatically and are checked before publishing.</p></div>}</InspectorSection>}
        {node.type === 'action.create_todo' && <InspectorSection title="ToDo details" description="Create linked follow-up work for a Frappe user."><div><label className={labelClass}>Assign to user</label><AsyncCombobox ariaLabel="Assign to user" value={String(config.allocated_to || '')} onChange={(value) => update({ ...config, allocated_to: value }, 'allocated_to')} loadOptions={loadUsers} placeholder="Search enabled users…" /></div>{text('description', 'Task description', true, 'What needs to happen next?')}<div><label className={labelClass}>Priority</label><select className={inputClass} value={String(config.priority || 'Medium')} onChange={(event) => update({ ...config, priority: event.target.value }, 'priority')}><option>Low</option><option>Medium</option><option>High</option></select></div></InspectorSection>}
        {node.type === 'action.add_comment' && <InspectorSection title="Timeline comment" description={`Add an auditable comment to the enrolled ${primaryDoctype || 'record'}.`}>{text('content', 'Comment', true, 'Write a helpful timeline note…')}</InspectorSection>}
        {node.type === 'action.notify_user' && <InspectorSection title="Internal notification" description="Send a notification inside Frappe without exposing record data externally."><div><label className={labelClass}>Recipient user</label><AsyncCombobox ariaLabel="Recipient user" value={String(config.for_user || '')} onChange={(value) => update({ ...config, for_user: value }, 'for_user')} loadOptions={loadUsers} placeholder="Search enabled users…" /></div>{text('subject', 'Subject', false, 'What happened?')}{text('message', 'Message', true, 'Add useful context for the recipient…')}</InspectorSection>}
        {node.type === 'action.send_email' && <InspectorSection title="Consent-aware email" description="Queue through Frappe Email Queue. Delivery remains independently controlled by the external-actions safety switch.">{bindingEditor('recipient', 'Recipient email')}{bindingEditor('subject', 'Subject')}{bindingEditor('message', 'Message')}{text('purpose', 'Consent purpose', false, 'workflow')}<label className="text-body flex items-center gap-2 text-[11px]"><input type="checkbox" checked={Boolean(config.require_consent ?? true)} onChange={(event) => update({ ...config, require_consent: event.target.checked ? 1 : 0 }, 'require_consent')} />Require a current consent grant <HelpTooltip label="Consent requirement" content="When enabled, execution requires the latest matching consent record to be GRANTED, not expired, denied, or revoked. The workflow fails closed when no current grant exists." /></label><Hint>Unsubscribe handling and the native outgoing Email Account remain authoritative in Frappe.</Hint></InspectorSection>}
        {node.type === 'action.send_sms' && <InspectorSection title="Consent-aware SMS" description="Submit synchronously through Frappe SMS Settings and report the gateway response.">{bindingEditor('recipient', 'Recipient mobile')}{bindingEditor('message', 'Message')}{text('purpose', 'Consent purpose', false, 'workflow')}<label className="text-body flex items-center gap-2 text-[11px]"><input type="checkbox" checked={Boolean(config.require_consent ?? true)} onChange={(event) => update({ ...config, require_consent: event.target.checked ? 1 : 0 }, 'require_consent')} />Require a current consent grant <HelpTooltip label="Consent requirement" content="When enabled, the recipient needs a current GRANTED SMS consent record for the same purpose. A missing, expired, denied, or revoked grant blocks submission." /></label><Hint>Native Frappe SMS Settings remain authoritative.</Hint></InspectorSection>}
        {node.type === 'action.webhook' && <InspectorSection title="Controlled webhook" description="POST JSON only to an exact, allowlisted public HTTPS hostname."><div><label className={labelClass}>Integration secret</label><AsyncCombobox ariaLabel="Integration secret" value={String(config.integration_secret || '')} onChange={(value) => update({ ...config, integration_secret: value }, 'integration_secret')} loadOptions={loadSecrets} placeholder="Search enabled secrets…" /></div>{text('url', 'HTTPS endpoint', false, 'https://api.example.com/events')}<JsonPayloadEditor key={`${node.id}-payload`} value={config.payload} onChange={(value) => update({ ...config, payload: value }, 'payload')} />{text('purpose', 'Purpose', false, 'workflow')}<Hint>Redirects, IP literals, private networks, and non-allowlisted hosts are blocked. Every request includes an idempotency key.</Hint></InspectorSection>}
        {node.type === 'trigger.schedule' && <InspectorSection title="Scheduled trigger" description="Publish this workflow, then create and enable its durable schedule from the Enrollment page."><Hint>Schedules own their timezone, audience filters, frequency, catch-up policy, overlap policy, version policy, and processing limits.</Hint></InspectorSection>}
        {node.type === 'condition.switch' && <InspectorSection title="Value branch" description="Route to different paths based on a single scalar field's exact value."><div><label className={labelClass}>Branch field</label><FieldPicker fields={readFields.filter((field) => field.capabilities?.switch ?? !['Table', 'Table MultiSelect'].includes(field.fieldtype))} value={String(config.field || "")} onChange={(value) => update({ ...config, field: value }, "field")} /></div><div><label className={labelClass}>Cases (one value per line)</label><textarea className={inputClass} rows={5} value={(Array.isArray(config.cases) ? config.cases : []).map((item) => typeof item === 'object' && item ? String((item as Record<string, unknown>).value || '') : String(item || '')).join("\n")} onChange={(event) => { const previous = Array.isArray(config.cases) ? config.cases : []; const cases = event.target.value.split("\n").map((value) => value.trim()).filter(Boolean).map((value, index) => ({ value, handle: typeof previous[index] === 'object' && previous[index] ? String((previous[index] as Record<string, unknown>).handle || `case-${index + 1}`) : `case-${index + 1}` })); update({ ...config, cases }, "cases") }} placeholder="Open" /></div><Hint>Each case value creates a separate edge port. The default edge fires when no case matches.</Hint></InspectorSection>}
        {node.type === 'condition.deduplicate' && <InspectorSection title="Deduplicate" description="Check if an existing record already has the same scalar field value before continuing."><div><label className={labelClass}>Match field</label><FieldPicker fields={readFields.filter((field) => field.capabilities?.deduplicate ?? !['Table', 'Table MultiSelect'].includes(field.fieldtype))} value={String(config.match_field || '')} onChange={(value) => update({ ...config, match_field: value }, 'match_field')} /></div><Hint>The duplicate branch fires when another record already has the same value. Outputs: duplicate_name, is_duplicate.</Hint></InspectorSection>}
        {node.type === 'delay.until_event' && <InspectorSection title="Wait for event" description="Pause the run until a specific event occurs or the timeout elapses."><div><label className={labelClass}>Event topic</label><input className={inputClass} value={String(config.event_topic || '')} onChange={(event) => update({ ...config, event_topic: event.target.value }, 'event_topic')} placeholder="crm.lead.qualified" /></div><div><label className={labelClass}>Timeout (seconds)</label><input type="number" min={60} className={inputClass} value={Number(config.timeout_seconds || 86400)} onChange={(event) => update({ ...config, timeout_seconds: Number(event.target.value) }, 'timeout_seconds')} /></div><Hint>If the event never arrives, the timed_out output equals true and the run continues on the timeout branch.</Hint></InspectorSection>}
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
              <select className={inputClass} value={config.assignment_type === 'group' ? 'group' : 'users'} onChange={(e) => update({ ...config, assignment_type: e.target.value, group: '' }, 'assignment_type')}>
                <option value="users">Specific Users</option>
                <option value="group">User Group</option>
              </select>
            </div>
            {config.assignment_type === 'group' ? (
              <div>
                <label className={labelClass}>User Group</label>
                <TypedValueInput field={{ fieldtype: 'Link', options: 'User Group', label: 'User Group' } as any} value={String(config.group || '')} onChange={(v) => update({ ...config, group: String(v || '') }, 'group')} />
              </div>
            ) : (
              <div>
                <label className={labelClass}>Specific Users</label>
                <TypedValueInput field={{ fieldtype: 'Link', options: 'User', label: 'Specific Users' } as any} multiple value={String(config.group || '').split(',').map(s => s.trim()).filter(Boolean)} onChange={(v) => update({ ...config, group: Array.isArray(v) ? v.join(',') : String(v || '') }, 'group')} />
              </div>
            )}
            <Hint>{node.type_version === 1 ? 'Legacy node: the same record always maps to the same member.' : 'Version 2: a locked workflow-version cursor provides true rotation and retry safety.'} Outputs: assigned_to, group.</Hint>
          </InspectorSection>
        )}
        {node.type === 'action.delete_record' && <InspectorSection title="Delete record" description="Permanently delete the enrolled record. This action cannot be undone."><p className="flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 p-3 text-[11px] leading-4 text-red-700 dark:border-red-900 dark:bg-red-500/10 dark:text-red-300"><AlertTriangle className="mt-0.5 shrink-0" size={13} />This step permanently deletes the enrolled record. Ensure the workflow has the correct eligibility conditions before publishing.</p></InspectorSection>}
        {node.type === 'end.complete' && <InspectorSection title="Successful completion"><p className="flex items-start gap-2 rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-[11px] leading-[18px] text-emerald-800 dark:border-emerald-900 dark:bg-emerald-500/10 dark:text-emerald-300"><CheckCircle2 className="mt-0.5 shrink-0" size={15} />Records reaching this step finish their run successfully.</p></InspectorSection>}
      </div>
    </aside>
  )
}
