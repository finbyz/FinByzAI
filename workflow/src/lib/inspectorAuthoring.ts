import type { ConditionExpression, ConditionGroup, ConditionPredicate, NodeCatalogItem, NodeType, WorkflowAssignment, WorkflowGraph, WorkflowNode, WorkflowValueSpec } from '../types'

export type NodeOutputCatalog = Partial<Record<NodeType, string[]>>

export function outputCatalog(nodeTypes: NodeCatalogItem[]): NodeOutputCatalog {
  return Object.fromEntries(nodeTypes.map((item) => [item.type, item.output_paths || []])) as NodeOutputCatalog
}

export function nodeOutputPaths(node: WorkflowNode | undefined, outputs: NodeOutputCatalog): string[] {
  if (!node) return []
  return outputs[node.type] || []
}

export function parseWebhookPayload(raw: string): { value: Record<string, unknown> | string; error: string } {
  try {
    const parsed = JSON.parse(raw) as unknown
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return { value: raw, error: 'Payload must be a JSON object.' }
    }
    return { value: parsed as Record<string, unknown>, error: '' }
  } catch (reason) {
    return { value: raw, error: reason instanceof Error ? reason.message : 'Payload must be valid JSON.' }
  }
}

export function isRequiredAuthoringValueMissing(value: unknown): boolean {
  if (value == null || value === '') return true
  if (Array.isArray(value)) return value.length === 0
  if (typeof value === 'object' && 'kind' in value && (value as { kind?: unknown }).kind === 'literal') {
    return isRequiredAuthoringValueMissing((value as { value?: unknown }).value)
  }
  return false
}

export function availableOutputNodes(graph: WorkflowGraph | null, currentNodeId: string, outputs: NodeOutputCatalog): WorkflowNode[] {
  if (!graph || !currentNodeId) return []
  const nodesById = new Map(graph.nodes.map((node) => [node.id, node]))
  const predecessors = new Map(graph.nodes.map((node) => [node.id, [] as string[]]))
  const adjacency = new Map(graph.nodes.map((node) => [node.id, [] as string[]]))
  const indegree = new Map(graph.nodes.map((node) => [node.id, 0]))
  for (const edge of graph.edges) {
    if (!nodesById.has(edge.source) || !nodesById.has(edge.target)) continue
    adjacency.get(edge.source)?.push(edge.target)
    predecessors.get(edge.target)?.push(edge.source)
    indegree.set(edge.target, (indegree.get(edge.target) || 0) + 1)
  }
  const queue = [...indegree].filter(([, count]) => count === 0).map(([nodeId]) => nodeId)
  const order: string[] = []
  while (queue.length) {
    const nodeId = queue.shift()!
    order.push(nodeId)
    for (const target of adjacency.get(nodeId) || []) {
      const remaining = (indegree.get(target) || 0) - 1
      indegree.set(target, remaining)
      if (!remaining) queue.push(target)
    }
  }
  if (order.length !== graph.nodes.length || !nodesById.has(graph.start_node_id)) return []
  const dominators = new Map<string, Set<string>>([[graph.start_node_id, new Set([graph.start_node_id])]])
  for (const nodeId of order) {
    if (nodeId === graph.start_node_id) continue
    const parentSets = (predecessors.get(nodeId) || []).map((parent) => dominators.get(parent)).filter((value): value is Set<string> => Boolean(value))
    const common = parentSets.length ? new Set(parentSets[0]) : new Set<string>()
    for (const parentSet of parentSets.slice(1)) {
      for (const candidate of common) if (!parentSet.has(candidate)) common.delete(candidate)
    }
    common.add(nodeId)
    dominators.set(nodeId, common)
  }
  const safeNodeIds = dominators.get(currentNodeId) || new Set<string>()
  return order
    .filter((nodeId) => nodeId !== currentNodeId && safeNodeIds.has(nodeId))
    .map((nodeId) => nodesById.get(nodeId)!)
    .filter((candidate) => nodeOutputPaths(candidate, outputs).length > 0)
}

export function emptyPredicate(): ConditionPredicate {
  return { kind: 'predicate', field: '', operator: 'eq', value: null }
}

export function parseCondition(value: unknown): ConditionExpression {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return emptyPredicate()
  const expression = value as Record<string, unknown>
  if (expression.kind === 'predicate') {
    return { kind: 'predicate', field: String(expression.field || ''), operator: String(expression.operator || 'eq'), value: expression.value }
  }
  if (['all', 'any', 'not'].includes(String(expression.kind))) {
    const kind = expression.kind as ConditionGroup['kind']
    const children = Array.isArray(expression.children) ? expression.children.map(parseCondition) : []
    return { kind, children: kind === 'not' ? [children[0] || emptyPredicate()] : children.length ? children : [emptyPredicate()] }
  }
  return emptyPredicate()
}

export function parseAssignments(value: unknown): WorkflowAssignment[] {
  if (!Array.isArray(value)) return []
  return value.flatMap((candidate) => {
    if (!candidate || typeof candidate !== 'object' || Array.isArray(candidate)) return []
    const assignment = candidate as Record<string, unknown>
    const rawValue = assignment.value
    if (!rawValue || typeof rawValue !== 'object' || Array.isArray(rawValue)) return []
    const spec = rawValue as Record<string, unknown>
    if (spec.kind === 'record_field') return [{ field: String(assignment.field || ''), value: { kind: 'record_field', field: String(spec.field || '') } as WorkflowValueSpec }]
    if (spec.kind === 'node_output') return [{ field: String(assignment.field || ''), value: { kind: 'node_output', node_id: String(spec.node_id || ''), path: String(spec.path || '') } as WorkflowValueSpec }]
    return [{ field: String(assignment.field || ''), value: { kind: 'literal', value: spec.value } as WorkflowValueSpec }]
  })
}
