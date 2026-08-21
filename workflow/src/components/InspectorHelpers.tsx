/* oxlint-disable react/only-export-components -- shared inspector primitives intentionally live together */
import {
  BellRing,
  CheckCircle2,
	ChevronDown,
  Clock3,
  DatabaseZap,
  GitBranch,
  Info,
  ListTodo,
  Link2,
  MessageSquareText,
  Plus,
  Play,
  Sparkles,
  Trash2,
  UserRoundPlus,
} from 'lucide-react'
import { type ReactNode, useCallback } from 'react'
import { AsyncCombobox, type ComboboxOption } from './AsyncCombobox'
import { MultiValueInput } from './MultiValueInput'
import { searchLink } from '../lib/api'
import { emptyPredicate, nodeOutputPaths, parseAssignments, parseCondition, type NodeOutputCatalog } from '../lib/inspectorAuthoring'
import type { ConditionExpression, ConditionGroup, ConditionPredicate, FieldCatalogItem, NodeConfig, NodeType, WorkflowAssignment, WorkflowNode, WorkflowValueSpec } from '../types'

export const inputClass = 'frappe-control px-3 py-2 text-xs'
export const labelClass = 'text-heading mb-1.5 block text-[11px] font-semibold'

export const nodeLabels: Record<NodeType, string> = {
  'trigger.manual': 'Manual enrollment',
  'trigger.document_insert': 'Record created',
  'trigger.document_change': 'Record changed',
	'trigger.filter_criteria': 'When filter criteria is met',
	'trigger.event': 'When an event occurs',
	'trigger.any': 'Any of multiple triggers',
	'condition.if_else': 'If / else paths',
	'condition.random_split': 'Random percentage split',
	'delay.fixed': 'Wait for a duration',
	'delay.drip': 'Drip in batches',
  'delay.until_date': 'Wait until a date',
  'transform.value': 'Transform a value',
  'action.update_record': 'Update this record',
  'action.create_record': 'Create a record',
  'action.create_todo': 'Create a ToDo',
  'action.add_comment': 'Add a comment',
	'action.create_note': 'Create a note',
	'action.copy_record': 'Copy record',
	'action.merge_contact': 'Merge contact',
	'action.unassign_record': 'Remove assigned users',
	'action.verify_email': 'Verify email format',
	'action.mark_communications_read': 'Mark conversations read',
	'action.remove_from_workflow': 'Remove from workflow',
	'action.complete_goal': 'Complete goal',
	'action.go_to': 'Go to step',
  'action.notify_user': 'Notify a user',
  'action.send_email': 'Send an email',
  'action.send_sms': 'Send SMS via Frappe',
  'action.webhook': 'Send a webhook',
	'action.instagram_message': 'Send Instagram message',
	'action.asana': 'Asana task / project',
	'action.call_subflow': 'Run another workflow',
  'action.numeric_adjust': 'Numeric adjust',
  'action.manage_association': 'Manage association',
  'action.round_robin': 'Round robin assign',
  'action.delete_record': 'Delete record',
  'trigger.schedule': 'Scheduled trigger',
  'trigger.webhook': 'Incoming webhook',
  'condition.switch': 'Value branch',
  'condition.deduplicate': 'Deduplicate',
	'delay.until_event': 'Wait until event',
  'delay.business_hours': 'Business hours',
  'transform.associated_record': 'Associated record',
  'transform.child_records': 'Child records',
  'end.complete': 'Workflow complete',
}

export const nodeIcons: Record<NodeType, typeof Play> = {
  'trigger.manual': Play,
  'trigger.document_insert': UserRoundPlus,
  'trigger.document_change': DatabaseZap,
	'trigger.filter_criteria': DatabaseZap,
	'trigger.event': BellRing,
	'trigger.any': GitBranch,
  'condition.if_else': GitBranch,
	'condition.random_split': GitBranch,
	'delay.drip': Clock3,
  'delay.fixed': Clock3,
  'delay.until_date': Clock3,
  'transform.value': Sparkles,
  'action.update_record': DatabaseZap,
  'action.create_record': UserRoundPlus,
  'action.create_todo': ListTodo,
  'action.add_comment': MessageSquareText,
	'action.create_note': MessageSquareText,
	'action.copy_record': UserRoundPlus,
	'action.merge_contact': UserRoundPlus,
	'action.unassign_record': UserRoundPlus,
	'action.verify_email': CheckCircle2,
	'action.mark_communications_read': MessageSquareText,
	'action.remove_from_workflow': Trash2,
	'action.complete_goal': CheckCircle2,
	'action.go_to': GitBranch,
  'action.notify_user': BellRing,
  'action.send_email': BellRing,
  'action.send_sms': MessageSquareText,
  'action.webhook': DatabaseZap,
	'action.instagram_message': MessageSquareText,
	'action.asana': ListTodo,
  'action.call_subflow': Sparkles,
  'action.numeric_adjust': DatabaseZap,
  'action.manage_association': UserRoundPlus,
  'action.round_robin': UserRoundPlus,
  'action.delete_record': Trash2,
  'trigger.schedule': Play,
  'trigger.webhook': Link2,
  'condition.switch': GitBranch,
  'condition.deduplicate': GitBranch,
  'delay.until_event': Clock3,
  'delay.business_hours': Clock3,
  'transform.associated_record': Sparkles,
  'transform.child_records': ListTodo,
  'end.complete': CheckCircle2,
}

const equalityOperators = [['eq', 'is equal to'], ['ne', 'is not equal to']] as const
const numericFieldTypes = ['Int', 'Float', 'Currency', 'Percent']

function zeroRepresentsBlank(field?: FieldCatalogItem) {
  return Boolean(field && numericFieldTypes.includes(field.fieldtype) && (field.default == null || field.default === ''))
}

function setOperatorsFor(field?: FieldCatalogItem) {
  return zeroRepresentsBlank(field)
    ? [['is_set', 'has a non-zero value'], ['is_not_set', 'is blank or zero']] as const
    : [['is_set', 'is set'], ['is_not_set', 'is not set']] as const
}

function operatorsFor(field?: FieldCatalogItem) {
  const fieldtype = field?.fieldtype
  if (fieldtype === 'Table MultiSelect') {
    return [
      ['contains_any', 'contains any of'],
      ['contains_all', 'contains all of'],
      ['contains_none', 'contains none of'],
      ...setOperatorsFor(field),
    ] as const
  }
  const operators: Array<readonly [string, string]> = [...equalityOperators]
  if (['Int', 'Float', 'Currency', 'Percent', 'Date', 'Datetime', 'Time'].includes(fieldtype || '')) {
    operators.push(['gt', 'is greater than'], ['gte', 'is at least'], ['lt', 'is less than'], ['lte', 'is at most'])
  }
  if (['Data', 'Small Text', 'Text', 'Long Text'].includes(fieldtype || '')) {
    operators.push(['contains', 'contains'], ['not_contains', 'does not contain'])
  }
  operators.push(['in', 'is any of'], ['not_in', 'is none of'], ...setOperatorsFor(field))
  return operators
}

export function InspectorSection({ title, description, children }: { title: string; description?: string; children: ReactNode }) {
  return (
    <section className="inspector-section">
      <h3 className="text-heading text-xs font-bold">{title}</h3>
      {description && <p className="text-muted mt-1 text-[10.5px] leading-4">{description}</p>}
      <div className="mt-3 space-y-3.5">{children}</div>
    </section>
  )
}

export function Hint({ children, title = 'How this works', defaultOpen = false }: { children: ReactNode; title?: string; defaultOpen?: boolean }) {
  return <details className="inspector-help" open={defaultOpen || undefined}><summary><Info size={13} /><span>{title}</span><ChevronDown size={13} /></summary><div>{children}</div></details>
}

export function FieldPicker({ value, fields, onChange }: { value?: string; fields: FieldCatalogItem[]; onChange(value: string): void }) {
  const loadOptions = useCallback((search: string): Promise<ComboboxOption[]> => {
    const needle = search.trim().toLocaleLowerCase()
    return Promise.resolve(fields
      .filter((field) => !needle || field.label.toLocaleLowerCase().includes(needle) || field.fieldname.toLocaleLowerCase().includes(needle))
      .slice(0, 30)
      .map((field) => ({ value: field.fieldname, label: field.label, description: `${field.fieldtype} · ${field.fieldname}` })))
  }, [fields])
  return (
    <AsyncCombobox ariaLabel="Record field" value={value || ''} disabled={!fields.length} onChange={onChange} loadOptions={loadOptions} placeholder={fields.length ? 'Search permitted fields…' : 'No permitted fields available'} />
  )
}

export function TypedValueInput({ field, value, onChange, referenceDoctype, multiple = false }: { field?: FieldCatalogItem; value: unknown; onChange(value: unknown): void; referenceDoctype?: string; multiple?: boolean }) {
  const loadLinks = useCallback((search: string): Promise<ComboboxOption[]> => {
    const linkDoctype = field?.fieldtype === 'Table MultiSelect' ? field.link_doctype : field?.fieldtype === 'Link' ? field.options : undefined
    if (!linkDoctype) return Promise.resolve([])
    return searchLink(linkDoctype, search, {
      referenceDoctype: field?.fieldtype === 'Table MultiSelect' ? field.child_doctype : referenceDoctype,
      linkFieldname: field?.fieldtype === 'Table MultiSelect' ? field.link_fieldname : field?.fieldname,
    })
      .then((rows) => rows.map((row) => ({ value: row.value, label: row.label || row.value, description: row.description })))
  }, [field?.child_doctype, field?.fieldname, field?.fieldtype, field?.link_doctype, field?.link_fieldname, field?.options, referenceDoctype])
  if (multiple || field?.fieldtype === 'Table MultiSelect') {
    const values = Array.isArray(value) ? value.map(String) : []
    const searchable = field?.fieldtype === 'Link' || field?.fieldtype === 'Table MultiSelect'
    return <MultiValueInput ariaLabel={`${field?.label || 'Field'} values`} values={values} onChange={onChange} loadOptions={searchable ? loadLinks : undefined} placeholder={searchable ? `Search ${field?.link_doctype || field?.options}…` : 'Add a value…'} />
  }
  if (field?.fieldtype === 'Link' && field.options) {
    return <AsyncCombobox ariaLabel={`${field.label} value`} value={String(value || '')} onChange={onChange} loadOptions={loadLinks} placeholder={`Search ${field.options}…`} />
  }
  if (field?.fieldtype === 'Select') {
    const options = String(field.options || '').split('\n')
    return <select className={inputClass} value={String(value ?? '')} onChange={(event) => onChange(event.target.value)}>{options.map((option, index) => <option value={option} key={`${option}-${index}`}>{option || 'Not set'}</option>)}</select>
  }
  if (field?.fieldtype === 'Check') {
    return <select className={inputClass} value={String(value ?? '')} onChange={(event) => onChange(event.target.value === '' ? null : Number(event.target.value))}><option value="">Not set</option><option value="1">Yes</option><option value="0">No</option></select>
  }
  const type = field?.fieldtype === 'Date' ? 'date' : field?.fieldtype === 'Datetime' ? 'datetime-local' : field?.fieldtype === 'Time' ? 'time' : ['Int', 'Float', 'Currency', 'Percent'].includes(field?.fieldtype || '') ? 'number' : 'text'
  return <input type={type} className={inputClass} placeholder="Enter a value" value={String(value ?? '')} onChange={(event) => onChange(event.target.value)} />
}

export function ConditionExpressionEditor({ expression, fields, primaryDoctype, outputNodes = [], outputPaths = {}, depth, onChange, onRemove }: { expression: ConditionExpression; fields: FieldCatalogItem[]; primaryDoctype?: string; outputNodes?: WorkflowNode[]; outputPaths?: NodeOutputCatalog; depth: number; onChange(expression: ConditionExpression): void; onRemove?: () => void }) {
  if (expression.kind === 'predicate') {
	const outputSource = expression.source?.kind === 'node_output' ? expression.source : undefined
	const selectedOutputNode = outputSource ? outputNodes.find((node) => node.id === outputSource.node_id) : undefined
    const selectedField = outputSource ? undefined : fields.find((field) => field.fieldname === expression.field)
    const operators = operatorsFor(selectedField)
    const patch = (values: Partial<ConditionPredicate>) => onChange({ ...expression, ...values })
    return (
      <div className="condition-rule min-w-0">
        <div className="grid min-w-0 gap-2.5">
		  <div className="flex items-center justify-between"><span className="text-light text-[9px] font-bold uppercase tracking-wider">Condition</span>{onRemove && <button type="button" className="icon-button !size-7 hover:!text-red-600" onClick={onRemove} aria-label="Remove condition"><Trash2 size={13} /></button>}</div>
		  {outputNodes.length > 0 && <select className={inputClass} aria-label="Condition data source" value={outputSource ? 'node_output' : 'record_field'} onChange={(event) => event.target.value === 'node_output' ? patch({ field: '', source: { kind: 'node_output', node_id: '', path: '' }, operator: 'eq', value: null }) : patch({ source: undefined, field: '', operator: 'eq', value: null })}><option value="record_field">Enrolled record field</option><option value="node_output">Earlier action output</option></select>}
		  {outputSource ? <div className="grid gap-1.5 sm:grid-cols-2"><select className={inputClass} value={outputSource.node_id} onChange={(event) => { const selected = outputNodes.find((node) => node.id === event.target.value); patch({ source: { kind: 'node_output', node_id: event.target.value, path: nodeOutputPaths(selected, outputPaths)[0] || '' } }) }}><option value="">Choose earlier action</option>{outputNodes.map((node) => <option value={node.id} key={node.id}>{nodeLabels[node.type] || node.type}</option>)}</select><input className={inputClass} list={selectedOutputNode ? `condition-output-${selectedOutputNode.id}` : undefined} value={outputSource.path} placeholder="Output path" onChange={(event) => patch({ source: { ...outputSource, path: event.target.value } })} />{selectedOutputNode && <datalist id={`condition-output-${selectedOutputNode.id}`}>{nodeOutputPaths(selectedOutputNode, outputPaths).map((path) => <option value={path} key={path} />)}</datalist>}</div> : <FieldPicker fields={fields} value={expression.field} onChange={(fieldname) => { const nextField = fields.find((field) => field.fieldname === fieldname); patch({ field: fieldname, source: undefined, operator: nextField?.fieldtype === 'Table MultiSelect' ? 'contains_any' : 'eq', value: nextField?.fieldtype === 'Table MultiSelect' ? [] : null }) }} />}
          <select aria-label={`${selectedField?.label || 'Field'} condition operator`} className={inputClass} value={expression.operator} onChange={(event) => patch({ operator: event.target.value, value: ['in', 'not_in', 'contains_any', 'contains_all', 'contains_none'].includes(event.target.value) ? [] : null })}>
            {operators.map(([value, label]) => <option value={value} key={value}>{label}</option>)}
          </select>
          {!['is_set', 'is_not_set'].includes(expression.operator) && <TypedValueInput field={selectedField} value={expression.value} onChange={(value) => patch({ value })} referenceDoctype={primaryDoctype} multiple={['in', 'not_in', 'contains_any', 'contains_all', 'contains_none'].includes(expression.operator)} />}
          {['is_set', 'is_not_set'].includes(expression.operator) && zeroRepresentsBlank(selectedField) && <p className="text-muted text-[10px] leading-4">Frappe stores a blank {selectedField?.fieldtype.toLowerCase()} as zero, so zero follows the blank path.</p>}
        </div>
      </div>
    )
  }
  const changeKind = (kind: ConditionGroup['kind']) => onChange({ kind, children: kind === 'not' ? [expression.children[0] || emptyPredicate()] : expression.children.length ? expression.children : [emptyPredicate()] })
  const replaceChild = (index: number, child: ConditionExpression) => onChange({ ...expression, children: expression.children.map((current, childIndex) => childIndex === index ? child : current) })
  const removeChild = (index: number) => {
    const children = expression.children.filter((_, childIndex) => childIndex !== index)
    onChange({ ...expression, children: children.length ? children : [emptyPredicate()] })
  }
  return (
    <div className="condition-group min-w-0" data-depth={depth}>
      <div className="mb-2.5 flex min-w-0 flex-wrap items-center gap-2">
		<select className="frappe-control !min-h-8 min-w-0 flex-1 px-2 text-[10px] font-bold" value={expression.kind} onChange={(event) => changeKind(event.target.value as ConditionGroup['kind'])} aria-label="How should these conditions be combined?"><option value="all">All conditions must match (AND)</option><option value="any">Any condition can match (OR)</option><option value="not">None may match (NOT)</option></select>
        <span className="text-light shrink-0 text-[9px]">{expression.children.length} {expression.children.length === 1 ? 'condition' : 'conditions'}</span>
        {onRemove && <button type="button" className="icon-button !ml-auto !size-7 hover:!text-red-600" onClick={onRemove} aria-label="Remove condition group"><Trash2 size={13} /></button>}
      </div>
      <div className="space-y-2">
		{expression.children.map((child, index) => <ConditionExpressionEditor key={`${depth}-${index}`} expression={child} fields={fields} primaryDoctype={primaryDoctype} outputNodes={outputNodes} outputPaths={outputPaths} depth={depth + 1} onChange={(next) => replaceChild(index, next)} onRemove={expression.kind === 'not' ? undefined : () => removeChild(index)} />)}
      </div>
	  {expression.kind !== 'not' && <div className="mt-2.5 flex min-w-0 flex-wrap gap-1.5"><button type="button" className="btn-core btn-secondary min-w-0 flex-1 !min-h-8 !px-2.5 !text-[10px]" onClick={() => onChange({ ...expression, children: [...expression.children, emptyPredicate()] })}><Plus className="shrink-0" size={12} />Add condition</button><button type="button" className="btn-core btn-ghost min-w-0 flex-1 !min-h-8 !px-2.5 !text-[10px]" disabled={depth >= 4} title={depth >= 4 ? 'Maximum editor nesting reached' : 'Advanced: add a nested condition group'} onClick={() => onChange({ ...expression, children: [...expression.children, { kind: 'all', children: [emptyPredicate()] }] })}><GitBranch className="shrink-0" size={12} />Advanced group</button></div>}
    </div>
  )
}

export function PolicyConditionEditor({ value, fields, primaryDoctype, onChange }: { value?: ConditionExpression | null; fields: FieldCatalogItem[]; primaryDoctype?: string; onChange(value: ConditionExpression): void }) {
  return <ConditionExpressionEditor expression={value || emptyPredicate()} fields={fields} primaryDoctype={primaryDoctype} depth={0} onChange={onChange} />
}

export function ConditionEditor({ config, fields, update, primaryDoctype, title = 'Enrollment criteria', description = 'Choose who enters. Use AND when every condition must match, or OR when any one condition is enough.' }: { config: NodeConfig; fields: FieldCatalogItem[]; update(config: NodeConfig, key: string): void; primaryDoctype?: string; title?: string; description?: string }) {
  const condition = parseCondition(config.condition)
  const setCondition = (expression: ConditionExpression) => update({ ...config, condition: expression }, 'condition:tree')
  return (
	<InspectorSection title={title} description={description}>
      <ConditionExpressionEditor expression={condition} fields={fields} primaryDoctype={primaryDoctype} depth={0} onChange={setCondition} />
      {condition.kind === 'predicate' && <button type="button" className="btn-core btn-secondary w-full !text-[10px]" onClick={() => setCondition({ kind: 'all', children: [condition, emptyPredicate()] })}><Plus size={12} />Add another rule</button>}
    </InspectorSection>
  )
}

export function ValueSourceEditor({ assignment, targetField, sourceFields, outputNodes, outputPaths, referenceDoctype, onChange }: { assignment: WorkflowAssignment; targetField?: FieldCatalogItem; sourceFields: FieldCatalogItem[]; outputNodes: WorkflowNode[]; outputPaths: NodeOutputCatalog; referenceDoctype?: string; onChange(assignment: WorkflowAssignment): void }) {
  const spec = assignment.value
  const changeKind = (kind: WorkflowValueSpec['kind']) => onChange({ ...assignment, value: kind === 'literal' ? { kind, value: '' } : kind === 'record_field' ? { kind, field: '' } : { kind, node_id: '', path: 'name' } })
  const selectedOutputNode = spec.kind === 'node_output' ? outputNodes.find((node) => node.id === spec.node_id) : undefined
  const pathListId = selectedOutputNode ? `output-paths-${selectedOutputNode.id}` : undefined
  const compatibleSourceFields = sourceFields.filter((field) => targetField?.fieldtype === 'Table MultiSelect' ? field.fieldtype === 'Table MultiSelect' : field.fieldtype !== 'Table' && field.fieldtype !== 'Table MultiSelect')
  return (
    <div className="space-y-1.5">
      <select className={inputClass} value={spec.kind} onChange={(event) => changeKind(event.target.value as WorkflowValueSpec['kind'])}>
        <option value="literal">Use a fixed value</option>
        <option value="record_field">Copy from enrolled record</option>
        <option value="node_output" disabled={!outputNodes.length}>Use prior step output{outputNodes.length ? '' : ' (none available)'}</option>
      </select>
      {spec.kind === 'literal' && <TypedValueInput field={targetField} value={spec.value} onChange={(value) => onChange({ ...assignment, value: { kind: 'literal', value } })} referenceDoctype={referenceDoctype} />}
      {spec.kind === 'record_field' && <FieldPicker fields={compatibleSourceFields} value={spec.field} onChange={(field) => onChange({ ...assignment, value: { kind: 'record_field', field } })} />}
      {spec.kind === 'node_output' && (
        <div className="grid gap-1.5 sm:grid-cols-2">
          <select className={inputClass} value={spec.node_id} onChange={(event) => onChange({ ...assignment, value: { ...spec, node_id: event.target.value, path: nodeOutputPaths(outputNodes.find((node) => node.id === event.target.value), outputPaths)[0] || '' } })}>
            <option value="">Choose guaranteed prior step</option>
            {outputNodes.map((node) => <option value={node.id} key={node.id}>{nodeLabels[node.type] || node.type}</option>)}
          </select>
          <input className={inputClass} list={pathListId} value={spec.path} placeholder="Output path, e.g. name" onChange={(event) => onChange({ ...assignment, value: { ...spec, path: event.target.value } })} />
          {selectedOutputNode && <datalist id={pathListId}>{nodeOutputPaths(selectedOutputNode, outputPaths).map((path) => <option value={path} key={path} />)}</datalist>}
        </div>
      )}
    </div>
  )
}

function assignmentValueConfigured(value: WorkflowValueSpec): boolean {
  if (value.kind === 'literal') return value.value !== '' && value.value != null && (!Array.isArray(value.value) || value.value.length > 0)
  if (value.kind === 'record_field') return Boolean(value.field)
  return Boolean(value.node_id && value.path)
}

export function AssignmentEditor({ config, fields, sourceFields, outputNodes, outputPaths, update, referenceDoctype, createMode = false }: { config: NodeConfig; fields: FieldCatalogItem[]; sourceFields: FieldCatalogItem[]; outputNodes: WorkflowNode[]; outputPaths: NodeOutputCatalog; update(config: NodeConfig, key: string): void; referenceDoctype?: string; createMode?: boolean }) {
  const assignments = parseAssignments(config.assignments)
  const setAssignments = (next: WorkflowAssignment[], key: string) => update({ ...config, assignments: next }, key)
  const usedFields = new Set(assignments.map((assignment) => assignment.field).filter(Boolean))
  const mandatoryFields = fields.filter((field) => (field.required || Boolean(field.mandatory_depends_on)) && (field.default == null || field.default === ''))
  const conditionalFields = fields.filter((field) => Boolean(field.mandatory_depends_on))
  const completedMandatory = mandatoryFields.filter((field) => {
    const assignment = assignments.find((item) => item.field === field.fieldname)
    return assignment ? assignment.operation !== 'clear' && assignmentValueConfigured(assignment.value) : false
  })
  return (
    <>
      {createMode && fields.length > 0 && <div className={`rounded-xl border p-3 ${completedMandatory.length === mandatoryFields.length ? 'border-emerald-200 bg-emerald-50/70 dark:border-emerald-800 dark:bg-emerald-500/10' : 'border-amber-200 bg-amber-50/80 dark:border-amber-800 dark:bg-amber-500/10'}`}><div className="flex items-center justify-between gap-3"><div><p className="text-heading text-[10.5px] font-bold">Mandatory field coverage</p><p className="text-muted mt-0.5 text-[9.5px]">Required by {referenceDoctype} before a record can be created.</p></div><span className={`shrink-0 rounded-full px-2 py-1 text-[9px] font-bold ${completedMandatory.length === mandatoryFields.length ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/20 dark:text-emerald-300' : 'bg-amber-100 text-amber-800 dark:bg-amber-500/20 dark:text-amber-200'}`}>{completedMandatory.length}/{mandatoryFields.length} mapped</span></div>{mandatoryFields.length > 0 && <div className="mt-2 flex flex-wrap gap-1.5">{mandatoryFields.map((field) => { const complete = completedMandatory.includes(field); return <span className={`rounded-md border px-2 py-1 text-[9px] font-semibold ${complete ? 'border-emerald-200 bg-white/60 text-emerald-700 dark:border-emerald-800 dark:bg-white/5 dark:text-emerald-300' : 'border-amber-300 bg-white/70 text-amber-800 dark:border-amber-700 dark:bg-white/5 dark:text-amber-200'}`} key={field.fieldname}>{field.label}{complete ? ' ✓' : ' *'}</span> })}</div>}{mandatoryFields.length === 0 && <p className="mt-2 text-[9.5px] text-emerald-700 dark:text-emerald-300">No unmapped mandatory fields; Frappe defaults handle the rest.</p>}{conditionalFields.length > 0 && <details className="mt-2"><summary className="cursor-pointer text-[9.5px] font-semibold text-[var(--text-muted)]">{conditionalFields.length} conditionally mandatory field{conditionalFields.length === 1 ? '' : 's'}</summary><p className="text-muted mt-1 text-[9px] leading-4">These may become required from runtime field values: {conditionalFields.map((field) => field.label).join(', ')}. Map them when your target scenario activates their condition.</p></details>}</div>}
      {assignments.map((assignment, index) => {
        const selectedField = fields.find((field) => field.fieldname === assignment.field)
        const availableFields = fields.filter((field) => field.fieldname === assignment.field || !usedFields.has(field.fieldname))
        const replace = (next: WorkflowAssignment) => setAssignments(assignments.map((current, assignmentIndex) => assignmentIndex === index ? next : current), `assignment:${index}`)
		const operation = assignment.operation || 'set'
        const mandatoryIncomplete = Boolean(createMode && (selectedField?.required || selectedField?.mandatory_depends_on) && (selectedField.default == null || selectedField.default === '') && !assignmentValueConfigured(assignment.value))
		const mandatoryClear = Boolean(!createMode && selectedField?.required && operation === 'clear')
		return <div className={`rounded-xl border bg-white/40 p-3 dark:bg-transparent ${mandatoryIncomplete || mandatoryClear ? 'border-red-300 ring-1 ring-red-100 dark:border-red-800 dark:ring-red-900/30' : 'border-[var(--border-color)]'}`} key={index}><div className="mb-2 flex items-center justify-between"><span className="flex items-center gap-1.5 text-[9px] font-bold uppercase tracking-wider text-[var(--text-light)]">{createMode ? 'Record field' : 'Field change'} {index + 1}{(selectedField?.required || selectedField?.mandatory_depends_on) && <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[8px] text-amber-800 dark:bg-amber-500/20 dark:text-amber-200">{selectedField.required ? 'Mandatory' : 'Conditional'}</span>}</span><button type="button" className="icon-button !size-7 hover:!text-red-600" onClick={() => setAssignments(assignments.filter((_, assignmentIndex) => assignmentIndex !== index), `assignment:remove:${index}`)} aria-label={`Remove field change ${index + 1}`}><Trash2 size={13} /></button></div><div className="space-y-2"><FieldPicker fields={availableFields} value={assignment.field} onChange={(field) => { const nextField = fields.find((candidate) => candidate.fieldname === field); replace({ ...assignment, field, operation: nextField?.required && operation === 'clear' ? 'set' : assignment.operation }) }} />{!createMode && <select className={inputClass} aria-label={`Field operation ${index + 1}`} value={operation} onChange={(event) => replace({ ...assignment, operation: event.target.value as WorkflowAssignment['operation'] })}><option value="set">Set value</option><option value="clear" disabled={Boolean(selectedField?.required)}>Clear value{selectedField?.required ? ' (mandatory field)' : ''}</option>{selectedField?.fieldtype === 'Table MultiSelect' && <><option value="append">Append values</option><option value="remove">Remove values</option></>}</select>}{operation !== 'clear' && <ValueSourceEditor assignment={assignment} targetField={selectedField} sourceFields={sourceFields} outputNodes={outputNodes} outputPaths={outputPaths} referenceDoctype={referenceDoctype} onChange={replace} />}{operation === 'clear' && <Hint>{selectedField?.required ? 'This field is mandatory. Choose Set value and provide a value before publishing.' : 'This clears the field. The workflow check validates Frappe field rules before publication.'}</Hint>}{mandatoryIncomplete && <p className="flex items-center gap-1.5 text-[9.5px] font-semibold text-red-600 dark:text-red-300"><Info size={11} />Provide a value or map a source for this {selectedField?.mandatory_depends_on && !selectedField.required ? 'conditionally mandatory' : 'mandatory'} field.</p>}{mandatoryClear && <p className="flex items-center gap-1.5 text-[9.5px] font-semibold text-red-600 dark:text-red-300"><Info size={11} />A mandatory field cannot be cleared.</p>}</div></div>
      })}
	  <button type="button" className="btn-core btn-secondary w-full !text-[10px]" disabled={!fields.length || assignments.length >= fields.length} onClick={() => setAssignments([...assignments, { field: '', operation: 'set', value: { kind: 'literal', value: '' } }], 'assignment:add')}><Plus size={12} />{createMode ? 'Add another record field' : 'Add field change'}</button>
      <Hint>{createMode ? 'Mandatory fields are inserted automatically. ' : ''}Only fields writable by the workflow execution user are available. Sensitive and complex fields stay protected by Frappe.</Hint>
    </>
  )
}
