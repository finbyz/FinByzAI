import { describe, expect, it } from 'vitest'
import type { NodeCatalogItem, WorkflowGraph } from '../types'
import { removeWorkflowNodes, replaceWorkflowTrigger, sameExecutionGraph } from './workflowGraphCommands'

const graph: WorkflowGraph = {
  schema_version: 1,
  primary_doctype: 'Lead',
  start_node_id: 'trigger-1',
  nodes: [
    { id: 'trigger-1', type: 'trigger.document_change', type_version: 1, position: { x: 80, y: 120 }, config: { condition: { kind: 'all', children: [] } } },
    { id: 'end-1', type: 'end.complete', type_version: 1, position: { x: 340, y: 120 }, config: {} },
  ],
  edges: [{ id: 'edge-1', source: 'trigger-1', source_handle: 'default', target: 'end-1' }],
}

const manualTrigger: NodeCatalogItem = {
  type: 'trigger.manual',
  label: 'Manual enrollment',
  category: 'Triggers',
  description: 'Enroll manually.',
  default_config: {},
  output_paths: [],
}

describe('replaceWorkflowTrigger', () => {
  it('preserves the stable start node, position, and journey edges while resetting config', () => {
    const replaced = replaceWorkflowTrigger(graph, manualTrigger)

    expect(replaced).not.toBe(graph)
    expect(replaced.start_node_id).toBe('trigger-1')
    expect(replaced.nodes[0]).toEqual({
      id: 'trigger-1',
      type: 'trigger.manual',
      type_version: 1,
      position: { x: 80, y: 120 },
      config: {},
    })
    expect(replaced.edges).toBe(graph.edges)
    expect(graph.nodes[0].type).toBe('trigger.document_change')
  })

  it('ignores non-trigger catalog items', () => {
    expect(replaceWorkflowTrigger(graph, { ...manualTrigger, type: 'end.complete' })).toBe(graph)
  })
})

describe('removeWorkflowNodes', () => {
  it('removes a multi-selection and every incident edge in one immutable update', () => {
    const expanded: WorkflowGraph = {
      ...graph,
      nodes: [...graph.nodes, { id: 'action-2', type: 'action.round_robin', type_version: 2, position: { x: 500, y: 120 }, config: { group: 'Sales' } }],
      edges: [...graph.edges, { id: 'edge-2', source: 'end-1', source_handle: 'default', target: 'action-2' }],
    }
    const result = removeWorkflowNodes(expanded, ['end-1', 'action-2'])
    expect(result.nodes.map((node) => node.id)).toEqual(['trigger-1'])
    expect(result.edges).toEqual([])
    expect(expanded.nodes).toHaveLength(3)
  })

  it('protects the start node and returns the same graph for a no-op', () => {
    expect(removeWorkflowNodes(graph, ['trigger-1', 'missing'])).toBe(graph)
  })
})

describe('sameExecutionGraph', () => {
  it('ignores positions and collection order while retaining runtime changes', () => {
    const layout = structuredClone(graph)
    layout.nodes.reverse()
    layout.nodes[0].position = { x: 900, y: 700 }
    expect(sameExecutionGraph(graph, layout)).toBe(true)

    layout.nodes[0].config = { changed: true }
    expect(sameExecutionGraph(graph, layout)).toBe(false)
  })
})
