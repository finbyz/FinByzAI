import { describe, expect, it } from 'vitest'
import type { NodeCatalogItem, WorkflowGraph } from '../types'
import { arrangeWorkflowGraph, catalogNode, duplicateWorkflowNode, duplicateWorkflowSection, insertWorkflowNode, reachableWorkflowNodeIds, relocateWorkflowNode, removeWorkflowNodes, replaceWorkflowTrigger, sameExecutionGraph, suggestedNodePlacement, upgradeLegacyIfElseBranches, workflowNodeContinuationHandle, workflowNodeSourceHandles, workflowNodeVisualWidth, workflowSectionNodeIds } from './workflowGraphCommands'

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

	it('uses the catalog runtime version when changing to a newer trigger contract', () => {
		const eventTrigger: NodeCatalogItem = {
			...manualTrigger,
			type: 'trigger.event',
			type_version: 2,
			label: 'When an event occurs',
			default_config: { events: [{ id: 'event-1', event_topic: '', event_filter: null }], condition: null },
		}
		const replaced = replaceWorkflowTrigger(graph, eventTrigger)
		expect(replaced.nodes[0].type_version).toBe(2)
		expect(replaced.nodes[0].config).toEqual(eventTrigger.default_config)
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

describe('upgradeLegacyIfElseBranches', () => {
	it('maps yes/no edges to a named criteria branch and permanent none path', () => {
		const legacy: WorkflowGraph = {
			...graph,
			nodes: [
				graph.nodes[0],
				{ id: 'branch', type: 'condition.if_else', type_version: 1, position: { x: 220, y: 220 }, config: { condition: { kind: 'predicate', field: 'status', operator: 'eq', value: 'Open' } } },
				graph.nodes[1],
				{ ...graph.nodes[1], id: 'end-2' },
			],
			edges: [
				{ id: 'start', source: 'trigger-1', source_handle: 'default', target: 'branch' },
				{ id: 'yes', source: 'branch', source_handle: 'true', target: 'end-1' },
				{ id: 'no', source: 'branch', source_handle: 'false', target: 'end-2' },
			],
		}
		const upgraded = upgradeLegacyIfElseBranches(legacy)
		expect(upgraded.nodes[1]).toMatchObject({
			type_version: 2,
			config: { branches: [{ handle: 'criteria-met', name: 'Criteria met', condition: legacy.nodes[1].config.condition }] },
		})
		expect(upgraded.edges[1].source_handle).toBe('criteria-met')
		expect(upgraded.edges[2].source_handle).toBe('none')
		expect(upgradeLegacyIfElseBranches(upgraded)).toBe(upgraded)
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

describe('workflowNodeVisualWidth', () => {
	it('reserves one structural canvas slot for every trigger plus Add trigger', () => {
		const triggers = Array.from({ length: 10 }, (_, index) => ({ id: `trigger-${index}`, type: 'trigger.document_insert', config: { condition: null } }))
		const trigger = {
			id: 'start',
			type: 'trigger.any',
			type_version: 2,
			position: { x: 0, y: 0 },
			config: { triggers },
		} as WorkflowGraph['nodes'][number]
		expect(workflowNodeVisualWidth(trigger)).toBe(2376)
		expect(workflowNodeVisualWidth({ ...trigger, config: { triggers: [triggers[0]] } })).toBe(432)
	})

	it('counts incomplete persisted cards so draft triggers never overflow the convergence boundary', () => {
		const trigger = {
			id: 'start',
			type: 'trigger.any',
			type_version: 2,
			position: { x: 0, y: 0 },
			config: { triggers: [
				{ id: 'created', type: 'trigger.document_insert', config: { condition: null } },
				{ id: 'event-draft', type: 'trigger.event', config: { event_topic: '' } },
			] },
		} as WorkflowGraph['nodes'][number]
		expect(workflowNodeVisualWidth(trigger)).toBe(648)
	})
})

describe('fast graph authoring commands', () => {
  const action: NodeCatalogItem = {
    type: 'action.add_comment',
    label: 'Add comment',
    category: 'Actions',
    description: 'Add a comment.',
    default_config: { content: '' },
    output_paths: ['comment'],
  }

  it('inserts a catalog node into an edge atomically', () => {
    const node = catalogNode(action, 'comment-1', { x: 210, y: 120 })
    const inserted = insertWorkflowNode(graph, node, { position: node.position, edgeId: 'edge-1' }, 'edge-2')
    expect(inserted.edges).toEqual([
      { id: 'edge-1', source: 'trigger-1', source_handle: 'default', target: 'comment-1' },
      { id: 'edge-2', source: 'comment-1', source_handle: 'default', target: 'end-1' },
    ])
    expect(inserted.nodes.at(-1)).toEqual(node)
  })

  it('auto-connects after a selected leaf', () => {
    const leaf = { ...graph, nodes: graph.nodes.slice(0, 1), edges: [] }
    const node = catalogNode(action, 'comment-1', { x: 220, y: 220 })
    const inserted = insertWorkflowNode(leaf, node, { position: node.position, afterNodeId: 'trigger-1' }, 'edge-new')
    expect(inserted.edges[0]).toEqual({ id: 'edge-new', source: 'trigger-1', source_handle: 'default', target: 'comment-1' })
  })

	it('auto-connects after a version-2 event delay when outcome branches are disabled', () => {
		const wait = { id: 'wait', type: 'delay.until_event', type_version: 2, position: { x: 200, y: 200 }, config: { event_topic: 'email.clicked', timeout_seconds: 3600, branch_on_timeout: 0 } } as const
		const sourceGraph = { ...graph, nodes: [graph.nodes[0], wait], edges: [{ id: 'wait-edge', source: 'trigger-1', source_handle: 'default', target: wait.id }] } as WorkflowGraph
		const node = catalogNode(action, 'comment-after-wait', { x: 220, y: 360 })
		const inserted = insertWorkflowNode(sourceGraph, node, { position: node.position, afterNodeId: wait.id }, 'comment-edge')
		expect(inserted.edges).toContainEqual({ id: 'comment-edge', source: wait.id, source_handle: 'default', target: node.id })
	})

	it('connects a dropped node to the exact unconnected branch output', () => {
		const branch = { id: 'branch', type: 'condition.if_else', type_version: 2, position: { x: 200, y: 200 }, config: { branches: [{ handle: 'vip', name: 'VIP', condition: { kind: 'predicate', field: 'status', operator: 'eq', value: 'VIP' } }] } } as const
		const sourceGraph = { ...graph, nodes: [graph.nodes[0], branch], edges: [{ id: 'branch-edge', source: 'trigger-1', source_handle: 'default', target: branch.id }] } as WorkflowGraph
		const node = catalogNode(action, 'vip-comment', { x: 220, y: 360 })
		const inserted = insertWorkflowNode(sourceGraph, node, { position: node.position, afterNodeId: branch.id, sourceHandle: 'vip' }, 'vip-edge')
		expect(inserted.edges).toContainEqual({ id: 'vip-edge', source: branch.id, source_handle: 'vip', target: node.id })
	})

	it('suggests the exact first unfinished output of a selected multi-path branch', () => {
		const branch = { id: 'branch', type: 'condition.if_else', type_version: 2, position: { x: 200, y: 200 }, config: { branches: [{ handle: 'german', name: 'German', condition: {} }, { handle: 'english', name: 'English', condition: {} }] } } as const
		const sourceGraph = {
			...graph,
			nodes: [graph.nodes[0], branch, graph.nodes[1]],
			edges: [
				{ id: 'start', source: 'trigger-1', source_handle: 'default', target: branch.id },
				{ id: 'german-path', source: branch.id, source_handle: 'german', target: 'end-1' },
			],
		} as WorkflowGraph
		expect(workflowNodeSourceHandles(branch)).toEqual(['german', 'english', 'none'])
		expect(suggestedNodePlacement(sourceGraph, branch.id)).toMatchObject({ afterNodeId: branch.id, sourceHandle: 'english' })
	})

	it('suggests the visually last unfinished path when nothing is selected', () => {
		const lower = { id: 'lower', type: 'action.add_comment', type_version: 1, position: { x: 200, y: 600 }, config: {} } as const
		const sourceGraph = { ...graph, nodes: [graph.nodes[0], lower], edges: [{ id: 'start', source: 'trigger-1', source_handle: 'default', target: lower.id }] } as WorkflowGraph
		expect(suggestedNodePlacement(sourceGraph)).toMatchObject({ afterNodeId: lower.id, sourceHandle: 'default' })
	})

	it('inserts into the selected incoming edge when every path is already connected', () => {
		expect(suggestedNodePlacement(graph, 'end-1')).toMatchObject({ edgeId: 'edge-1' })
	})

	it('does not create a disconnected node or put a terminal action in the middle of a path', () => {
		const ordinary = catalogNode(action, 'orphan', { x: 800, y: 800 })
		expect(insertWorkflowNode(graph, ordinary, { position: ordinary.position }, 'unused')).toBe(graph)
		const terminal = { ...ordinary, id: 'go-to', type: 'action.go_to' as const }
		expect(insertWorkflowNode(graph, terminal, { position: terminal.position, edgeId: 'edge-1' }, 'unused')).toBe(graph)
	})

	it('preserves the existing journey on the safe fallback output when inserting a branch', () => {
		const branch = { id: 'branch-new', type: 'condition.if_else', type_version: 2, position: { x: 200, y: 200 }, config: { branches: [{ handle: 'qualified', name: 'Qualified', condition: {} }] } } as const
		expect(workflowNodeContinuationHandle(branch)).toBe('none')
		const inserted = insertWorkflowNode(graph, branch, { position: branch.position, edgeId: 'edge-1' }, 'none-edge')
		expect(inserted.edges).toContainEqual({ id: 'edge-1', source: 'trigger-1', source_handle: 'default', target: branch.id })
		expect(inserted.edges).toContainEqual({ id: 'none-edge', source: branch.id, source_handle: 'none', target: 'end-1' })
	})

	it('arranges connected branches into lanes and separates legacy orphan nodes', () => {
		const branch = { id: 'branch-layout', type: 'condition.if_else', type_version: 2, position: { x: 0, y: 0 }, config: { branches: [{ handle: 'one', name: 'Path 1', condition: {} }, { handle: 'two', name: 'Path 2', condition: {} }] } } as const
		const child = { id: 'child-layout', type: 'action.add_comment', type_version: 1, position: { x: 0, y: 0 }, config: {} } as const
		const orphan = { id: 'orphan-layout', type: 'action.add_comment', type_version: 1, position: { x: 0, y: 0 }, config: {} } as const
		const sourceGraph: WorkflowGraph = {
			...graph,
			nodes: [graph.nodes[0], branch, child, orphan],
			edges: [
				{ id: 'start-layout', source: 'trigger-1', source_handle: 'default', target: branch.id },
				{ id: 'path-one', source: branch.id, source_handle: 'one', target: child.id },
			],
		}
		expect(reachableWorkflowNodeIds(sourceGraph)).toEqual(new Set(['trigger-1', branch.id, child.id]))
		const arranged = arrangeWorkflowGraph(sourceGraph)
		const positions = Object.fromEntries(arranged.nodes.map((node) => [node.id, node.position]))
		expect(positions['trigger-1'].y).toBeLessThan(positions[branch.id].y)
		expect(positions[branch.id].y).toBeLessThan(positions[child.id].y)
		expect(positions[orphan.id].x).toBeGreaterThan(Math.max(positions['trigger-1'].x, positions[branch.id].x, positions[child.id].x))
		expect(sameExecutionGraph(sourceGraph, arranged)).toBe(true)
	})

  it('duplicates configuration with a fresh identity and preserves the journey', () => {
    const source = { id: 'comment-source', type: 'action.add_comment', type_version: 1, position: { x: 200, y: 200 }, config: { content: 'Follow up' } } as const
    const sourceGraph = { ...graph, nodes: [graph.nodes[0], source], edges: [{ id: 'source-edge', source: 'trigger-1', source_handle: 'default', target: source.id }] } as WorkflowGraph
    const duplicated = duplicateWorkflowNode(sourceGraph, source, 'comment-copy', { position: { x: 220, y: 360 }, afterNodeId: source.id }, 'copy-edge')
    expect(duplicated.nodes.at(-1)?.id).toBe('comment-copy')
    expect(duplicated.nodes.at(-1)?.config).toEqual({ content: 'Follow up' })
    expect(duplicated.edges.at(-1)).toEqual({ id: 'copy-edge', source: source.id, source_handle: 'default', target: 'comment-copy' })
  })

	it('moves a simple action to another edge and heals its former path', () => {
		const moving = catalogNode(action, 'moving', { x: 200, y: 200 })
		const target = catalogNode(action, 'target', { x: 500, y: 200 })
		const sourceGraph: WorkflowGraph = {
			...graph,
			nodes: [graph.nodes[0], moving, graph.nodes[1], target],
			edges: [
				{ id: 'before-moving', source: 'trigger-1', source_handle: 'default', target: 'moving' },
				{ id: 'after-moving', source: 'moving', source_handle: 'default', target: 'end-1' },
				{ id: 'target-edge', source: 'target', source_handle: 'default', target: 'end-1' },
			],
		}
		const moved = relocateWorkflowNode(sourceGraph, 'moving', 'target-edge', { x: 420, y: 320 }, 'moved-edge')
		expect(moved.edges).toContainEqual({ id: 'before-moving', source: 'trigger-1', source_handle: 'default', target: 'end-1' })
		expect(moved.edges).toContainEqual({ id: 'target-edge', source: 'target', source_handle: 'default', target: 'moving' })
		expect(moved.edges).toContainEqual({ id: 'moved-edge', source: 'moving', source_handle: 'default', target: 'end-1' })
	})

	it('copies an exclusive connected branch without duplicating its shared convergence', () => {
		const branchGraph: WorkflowGraph = {
			...graph,
			nodes: [
				graph.nodes[0],
				{ id: 'branch', type: 'condition.if_else', type_version: 2, position: { x: 200, y: 180 }, config: { branches: [{ handle: 'yes', name: 'Yes', condition: { kind: 'predicate', field: 'status', operator: 'eq', value: 'Open' } }] } },
				{ id: 'yes-1', type: 'action.add_comment', type_version: 1, position: { x: 100, y: 330 }, config: { content: 'one' } },
				{ id: 'yes-2', type: 'action.add_comment', type_version: 1, position: { x: 100, y: 460 }, config: { content: 'two' } },
				{ id: 'no-1', type: 'action.add_comment', type_version: 1, position: { x: 320, y: 330 }, config: { content: 'none' } },
				{ id: 'shared', type: 'action.add_comment', type_version: 1, position: { x: 220, y: 600 }, config: { content: 'shared' } },
			],
			edges: [
				{ id: 'start', source: 'trigger-1', source_handle: 'default', target: 'branch' },
				{ id: 'yes-a', source: 'branch', source_handle: 'yes', target: 'yes-1' },
				{ id: 'yes-b', source: 'yes-1', source_handle: 'default', target: 'yes-2' },
				{ id: 'yes-shared', source: 'yes-2', source_handle: 'default', target: 'shared' },
				{ id: 'none-a', source: 'branch', source_handle: 'none', target: 'no-1' },
				{ id: 'none-shared', source: 'no-1', source_handle: 'default', target: 'shared' },
			],
		}
		expect(workflowSectionNodeIds(branchGraph, 'yes-1')).toEqual(['yes-1', 'yes-2'])
		let nodeCounter = 0
		let edgeCounter = 0
		const copied = duplicateWorkflowSection(branchGraph, 'yes-1', { position: { x: 520, y: 330 }, edgeId: 'none-a' }, () => `copy-${++nodeCounter}`, () => `copy-edge-${++edgeCounter}`)
		expect(copied.rootId).toBe('copy-1')
		expect(copied.graph.nodes.filter((node) => node.id.startsWith('copy-')).map((node) => node.config.content)).toEqual(['one', 'two'])
		expect(copied.graph.nodes.filter((node) => node.id === 'shared')).toHaveLength(1)
		expect(copied.graph.edges).toContainEqual({ id: 'none-a', source: 'branch', source_handle: 'none', target: 'copy-1' })
		expect(copied.graph.edges.some((edge) => edge.source === 'copy-2' && edge.target === 'no-1')).toBe(true)
	})
})
