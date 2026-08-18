import type { WorkflowGraph, WorkflowNode } from '../types'

export interface RunTokenTrace {
  name?: string
  node_id: string
  occurrence?: number
  status: string
  attempts: number
  error_message?: string
}

export interface ExecutedStep {
  node: WorkflowNode
  token: RunTokenTrace
}

export function projectRunTrace(graph: WorkflowGraph, tokens: RunTokenTrace[]) {
  const nodeById = new Map(graph.nodes.map((node) => [node.id, node]))
  const executed = tokens.flatMap((token) => {
    const node = nodeById.get(token.node_id)
    return node ? [{ node, token }] : []
  })
  const reached = new Set(executed.map(({ node }) => node.id))
  return {
    executed,
    unvisited: graph.nodes.filter((node) => !reached.has(node.id)),
    completed: tokens.filter((token) => token.status === 'COMPLETED').length,
    reachedCount: tokens.length,
  }
}
