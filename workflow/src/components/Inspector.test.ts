import { describe, expect, it } from 'vitest'
import type { WorkflowGraph } from '../types'
import { availableOutputNodes, conditionToFilterGroups, filterGroupsToCondition, isRequiredAuthoringValueMissing, nodeOutputPaths, parseAssignments, parseCondition, parseWebhookPayload, type NodeOutputCatalog } from '../lib/inspectorAuthoring'

const outputs: NodeOutputCatalog = {
	'condition.if_else': ['matched', 'selected_handle', 'branch_name'],
	'condition.random_split': ['selected_handle', 'branch_name', 'bucket'],
  'condition.switch': ['value', 'matched_handle'],
  'delay.business_hours': ['released', 'due_at', 'timezone'],
  'action.create_record': ['doctype', 'name'],
  'action.numeric_adjust': ['doctype', 'name', 'field', 'previous', 'new_value'],
  'action.manage_association': ['doctype', 'name', 'operation', 'target_name'],
}

const node = (id: string, type: WorkflowGraph['nodes'][number]['type']): WorkflowGraph['nodes'][number] => ({
  id,
  type,
  type_version: 1,
  position: { x: 0, y: 0 },
  config: {},
})

describe('Inspector authoring contracts', () => {
  it('uses server-provided output names for cross-node bindings', () => {
    expect(nodeOutputPaths(node('switch', 'condition.switch'), outputs)).toEqual(['value', 'matched_handle'])
    expect(nodeOutputPaths(node('numeric', 'action.numeric_adjust'), outputs)).toContain('previous')
    expect(nodeOutputPaths(node('association', 'action.manage_association'), outputs)).toContain('doctype')
		expect(nodeOutputPaths(node('hours', 'delay.business_hours'), outputs)).toEqual(['released', 'due_at', 'timezone'])
		expect(nodeOutputPaths(node('split', 'condition.random_split'), outputs)).toEqual(['selected_handle', 'branch_name', 'bucket'])
  })

  it('preserves invalid webhook JSON so publishing is blocked instead of sending stale data', () => {
    expect(parseWebhookPayload('{"event":"created"}')).toEqual({ value: { event: 'created' }, error: '' })
    expect(parseWebhookPayload('[1, 2]')).toMatchObject({ value: '[1, 2]', error: 'Payload must be a JSON object.' })
    expect(parseWebhookPayload('"event"')).toMatchObject({ value: '"event"', error: 'Payload must be a JSON object.' })
    expect(parseWebhookPayload('42')).toMatchObject({ value: '42', error: 'Payload must be a JSON object.' })
    expect(parseWebhookPayload('{invalid')).toMatchObject({ value: '{invalid' })
    expect(parseWebhookPayload('{invalid').error).not.toBe('')
  })

  it('accepts meaningful falsy literal values in required authoring fields', () => {
    expect(isRequiredAuthoringValueMissing({ kind: 'literal', value: 0 })).toBe(false)
    expect(isRequiredAuthoringValueMissing({ kind: 'literal', value: false })).toBe(false)
    expect(isRequiredAuthoringValueMissing({ kind: 'literal', value: '' })).toBe(true)
    expect(isRequiredAuthoringValueMissing({ kind: 'literal', value: [] })).toBe(true)
  })
  it('preserves nested AND, OR, and NOT expressions while repairing empty groups', () => {
    const expression = parseCondition({
      kind: 'all',
      children: [
        { kind: 'predicate', field: 'status', operator: 'eq', value: 'Open' },
        { kind: 'any', children: [{ kind: 'not', children: [{ kind: 'predicate', field: 'disabled', operator: 'eq', value: 1 }] }] },
      ],
    })

    expect(expression).toMatchObject({ kind: 'all', children: [{ field: 'status' }, { kind: 'any' }] })
    expect(parseCondition({ kind: 'any', children: [] })).toMatchObject({ kind: 'any', children: [{ kind: 'predicate' }] })
  })

	it('maps If/else criteria to simple AND groups separated by OR', () => {
		const grouped = filterGroupsToCondition([
			[{ kind: 'predicate', field: 'country', operator: 'eq', value: 'Germany' }, { kind: 'predicate', field: 'language', operator: 'eq', value: 'German' }],
			[{ kind: 'predicate', field: 'customer_type', operator: 'eq', value: 'Partner' }],
		])
		const groups = conditionToFilterGroups(grouped)
		expect(groups).toHaveLength(2)
		expect(groups?.[0].map((condition) => condition.field)).toEqual(['country', 'language'])
		expect(groups?.[1].map((condition) => condition.field)).toEqual(['customer_type'])
		expect(conditionToFilterGroups({ kind: 'not', children: [{ kind: 'predicate', field: 'disabled', operator: 'eq', value: 1 }] })).toBeNull()
	})

  it('parses multiple literal, record-field, and prior-output assignments', () => {
    expect(parseAssignments([
      { field: 'company_name', value: { kind: 'literal', value: 'Megasol' } },
      { field: 'lead_name', value: { kind: 'record_field', field: 'first_name' } },
      { field: 'source', value: { kind: 'node_output', node_id: 'create', path: 'name' } },
    ])).toEqual([
      { field: 'company_name', value: { kind: 'literal', value: 'Megasol' } },
      { field: 'lead_name', value: { kind: 'record_field', field: 'first_name' } },
      { field: 'source', value: { kind: 'node_output', node_id: 'create', path: 'name' } },
    ])
  })

  it('offers outputs only from steps guaranteed to run before a converged action', () => {
    const graph: WorkflowGraph = {
      schema_version: 1,
      primary_doctype: 'Lead',
      start_node_id: 'trigger',
      nodes: [
        node('trigger', 'trigger.manual'),
        node('guaranteed', 'action.create_record'),
        node('branch', 'condition.if_else'),
        node('true-action', 'action.create_todo'),
        node('false-action', 'action.add_comment'),
        node('current', 'action.update_record'),
      ],
      edges: [
        { id: 'e1', source: 'trigger', source_handle: 'default', target: 'guaranteed' },
        { id: 'e2', source: 'guaranteed', source_handle: 'default', target: 'branch' },
        { id: 'e3', source: 'branch', source_handle: 'true', target: 'true-action' },
        { id: 'e4', source: 'branch', source_handle: 'false', target: 'false-action' },
        { id: 'e5', source: 'true-action', source_handle: 'default', target: 'current' },
        { id: 'e6', source: 'false-action', source_handle: 'default', target: 'current' },
      ],
    }

    expect(availableOutputNodes(graph, 'current', outputs).map((item) => item.id)).toEqual(['guaranteed', 'branch'])
  })

  it('fails closed when a cyclic draft has no sound guaranteed-before ordering', () => {
    const graph: WorkflowGraph = {
      schema_version: 1,
      primary_doctype: 'Lead',
      start_node_id: 'trigger',
      nodes: [node('trigger', 'trigger.manual'), node('create', 'action.create_record'), node('current', 'action.update_record')],
      edges: [
        { id: 'e1', source: 'trigger', source_handle: 'default', target: 'create' },
        { id: 'e2', source: 'create', source_handle: 'default', target: 'current' },
        { id: 'e3', source: 'current', source_handle: 'default', target: 'create' },
      ],
    }

    expect(availableOutputNodes(graph, 'current', outputs)).toEqual([])
  })
})
