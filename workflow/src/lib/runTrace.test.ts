import { describe, expect, it } from 'vitest'
import type { WorkflowGraph } from '../types'
import { projectRunTrace } from './runTrace'

const graph: WorkflowGraph = {
  schema_version: 1,
  primary_doctype: 'Lead',
  start_node_id: 'trigger',
  nodes: [
    { id: 'trigger', type: 'trigger.manual', type_version: 1, position: { x: 0, y: 0 }, config: {} },
    { id: 'no', type: 'end.complete', type_version: 1, position: { x: 100, y: 0 }, config: {} },
    { id: 'yes', type: 'action.create_todo', type_version: 1, position: { x: 100, y: 100 }, config: {} },
  ],
  edges: [],
}

describe('projectRunTrace', () => {
  it('uses durable token order and excludes untouched branches from progress', () => {
    const result = projectRunTrace(graph, [
      { name: 'token-1', node_id: 'trigger', status: 'COMPLETED', attempts: 1 },
      { name: 'token-2', node_id: 'yes', status: 'WAITING', attempts: 1 },
    ])
    expect(result.executed.map((step) => step.node.id)).toEqual(['trigger', 'yes'])
    expect(result.unvisited.map((node) => node.id)).toEqual(['no'])
    expect(result.completed).toBe(1)
    expect(result.reachedCount).toBe(2)
  })
})
