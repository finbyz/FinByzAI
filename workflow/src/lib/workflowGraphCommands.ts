import type { NodeCatalogItem, WorkflowGraph } from '../types'

function canonicalize(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalize)
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, item]) => [key, canonicalize(item)]),
    )
  }
  return value
}

export function canonicalValue(value: unknown): string {
  return JSON.stringify(canonicalize(value))
}

export function executionGraphValue(graph: WorkflowGraph): unknown {
  return {
    ...graph,
    nodes: graph.nodes
      .map(({ position: _position, ...node }) => node)
      .sort((left, right) => left.id.localeCompare(right.id) || canonicalValue(left).localeCompare(canonicalValue(right))),
    edges: [...graph.edges]
      .sort((left, right) => left.id.localeCompare(right.id) || canonicalValue(left).localeCompare(canonicalValue(right))),
  }
}

export function sameExecutionGraph(left: WorkflowGraph, right: WorkflowGraph): boolean {
  return canonicalValue(executionGraphValue(left)) === canonicalValue(executionGraphValue(right))
}

export function replaceWorkflowTrigger(graph: WorkflowGraph, item: NodeCatalogItem): WorkflowGraph {
  if (!item.type.startsWith('trigger.')) return graph

  const triggerIndex = graph.nodes.findIndex((node) => node.id === graph.start_node_id && node.type.startsWith('trigger.'))
  if (triggerIndex < 0 || graph.nodes[triggerIndex].type === item.type) return graph

  const nodes = [...graph.nodes]
  nodes[triggerIndex] = {
    ...nodes[triggerIndex],
    type: item.type,
    type_version: 1,
    config: structuredClone(item.default_config),
  }

  return { ...graph, nodes }
}

export function removeWorkflowNodes(graph: WorkflowGraph, nodeIds: string[]): WorkflowGraph {
  const removed = new Set(nodeIds.filter((nodeId) => nodeId !== graph.start_node_id))
  if (!removed.size || !graph.nodes.some((node) => removed.has(node.id))) return graph
  return {
    ...graph,
    nodes: graph.nodes.filter((node) => !removed.has(node.id)),
    edges: graph.edges.filter((edge) => !removed.has(edge.source) && !removed.has(edge.target)),
  }
}
