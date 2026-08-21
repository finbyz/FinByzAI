import type { NodeCatalogItem, Position, WorkflowGraph, WorkflowNode } from '../types'

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
	    type_version: item.type_version || 1,
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

/** Convert editable legacy yes/no branches without changing their routing meaning.
 * Published versions remain immutable; only the working draft uses this helper.
 */
export function upgradeLegacyIfElseBranches(graph: WorkflowGraph): WorkflowGraph {
	const legacyIds = new Set(graph.nodes.filter((node) => node.type === 'condition.if_else' && node.type_version < 2).map((node) => node.id))
	if (!legacyIds.size) return graph
	return {
		...graph,
		nodes: graph.nodes.map((node) => legacyIds.has(node.id) ? {
			...node,
			type_version: 2,
			config: {
				branches: [{ handle: 'criteria-met', name: 'Criteria met', condition: structuredClone(node.config.condition || null) }],
			},
		} : node),
		edges: graph.edges.map((edge) => !legacyIds.has(edge.source) ? edge : {
			...edge,
			source_handle: edge.source_handle === 'true' ? 'criteria-met' : edge.source_handle === 'false' ? 'none' : edge.source_handle,
		}),
	}
}

export interface NodePlacement {
  position: Position
  edgeId?: string
  afterNodeId?: string
	sourceHandle?: string
}

export function catalogNode(item: NodeCatalogItem, id: string, position: Position): WorkflowNode {
  return {
    id,
    type: item.type,
    type_version: item.type_version || 1,
    position,
    config: structuredClone(item.default_config || {}),
  }
}

export function canUseDefaultOutput(node?: WorkflowNode): boolean {
	return Boolean(node && !node.type.startsWith('condition.') && (node.type !== 'delay.until_event' || (node.type_version >= 2 && !node.config.branch_on_timeout)) && !['end.complete', 'action.delete_record', 'action.go_to'].includes(node.type))
}

export function workflowNodeSourceHandles(node?: WorkflowNode): string[] {
	if (!node || ['end.complete', 'action.delete_record', 'action.go_to'].includes(node.type)) return []
	if (node.type === 'condition.if_else') {
		return node.type_version >= 2
			? [...(Array.isArray(node.config.branches) ? node.config.branches : []).flatMap((branch) => typeof branch === 'object' && branch ? [String((branch as Record<string, unknown>).handle || '')] : []), 'none'].filter(Boolean)
			: ['true', 'false']
	}
	if (node.type === 'condition.random_split') return (Array.isArray(node.config.branches) ? node.config.branches : []).flatMap((branch) => typeof branch === 'object' && branch ? [String((branch as Record<string, unknown>).handle || '')] : []).filter(Boolean)
	if (node.type === 'condition.deduplicate') return ['duplicate', 'unique']
	if (node.type === 'condition.switch') return [...(Array.isArray(node.config.cases) ? node.config.cases : []).flatMap((item) => typeof item === 'object' && item ? [String((item as Record<string, unknown>).handle || '')] : []), 'default'].filter(Boolean)
	if (node.type === 'delay.until_event' && (node.type_version < 2 || node.config.branch_on_timeout)) return ['event', 'timeout']
	return ['default']
}

/** Output that preserves the existing journey when a new branching step splits
 * an edge. The other outputs remain visibly unfinished and ready for authoring.
 */
export function workflowNodeContinuationHandle(node?: WorkflowNode): string | undefined {
	if (!node) return undefined
	if (canUseDefaultOutput(node)) return 'default'
	if (node.type === 'condition.if_else' || node.type === 'condition.switch') return node.type === 'condition.if_else' && node.type_version < 2 ? 'false' : node.type === 'condition.if_else' ? 'none' : 'default'
	if (node.type === 'condition.deduplicate') return 'unique'
	if (node.type === 'delay.until_event') return 'event'
	if (node.type === 'condition.random_split') return workflowNodeSourceHandles(node)[0]
	return undefined
}

export function reachableWorkflowNodeIds(graph: WorkflowGraph): Set<string> {
	const reachable = new Set<string>()
	const pending = [graph.start_node_id]
	while (pending.length) {
		const nodeId = pending.shift()
		if (!nodeId || reachable.has(nodeId)) continue
		reachable.add(nodeId)
		for (const edge of graph.edges) if (edge.source === nodeId) pending.push(edge.target)
	}
	return reachable
}

function enrollmentTriggerCardCount(node: WorkflowNode) {
	if (node.type === 'trigger.any') return (Array.isArray(node.config.triggers) ? node.config.triggers : []).filter((entry) => entry && typeof entry === 'object').length
	if (node.type === 'trigger.event' && Array.isArray(node.config.events)) return node.config.events.filter((entry) => entry && typeof entry === 'object').length
	return 1
}

/** Keep React Flow rendering and automatic layout on the same node width. */
export function workflowNodeVisualWidth(node: WorkflowNode): number {
	if (['trigger.document_insert', 'trigger.document_change', 'trigger.event', 'trigger.any'].includes(node.type)) {
		// Every enrollment trigger is a real canvas card and the final slot is the
		// Add trigger card. Do not cap this width: a cap creates an inner scrollbar
		// that cuts through the derived convergence line. React Flow already owns
		// canvas panning and zooming, so the enrollment boundary must expose its
		// complete structural width to layout and edge calculations.
		const items = enrollmentTriggerCardCount(node) + 1
		return Math.max(432, items * 216)
	}
	const outputs = workflowNodeSourceHandles(node)
	return outputs.length > 3 ? Math.min(1440, Math.max(252, outputs.length * 84)) : node.type.startsWith('trigger.') ? 310 : 252
}

/** Produce a readable top-down journey without requiring authors to manually
 * line up branches. Missing branch outputs reserve their own lane so END points
 * do not overlap connected children. Legacy unreachable nodes are moved into a
 * separate column where their disconnected state is visually honest.
 */
export function arrangeWorkflowGraph(graph: WorkflowGraph): WorkflowGraph {
	const nodeById = new Map(graph.nodes.map((node) => [node.id, node]))
	if (!nodeById.has(graph.start_node_id)) return graph
	const horizontalGap = 320
	const verticalGap = 250
	const positions = new Map<string, Position>()
	const visiting = new Set<string>()
	let leafIndex = 0

	const place = (nodeId: string, depth: number): number => {
		const existing = positions.get(nodeId)
		if (existing) return existing.x + workflowNodeVisualWidth(nodeById.get(nodeId) as WorkflowNode) / 2
		const node = nodeById.get(nodeId)
		if (!node || visiting.has(nodeId)) return leafIndex++ * horizontalGap
		visiting.add(nodeId)
		const handles = workflowNodeSourceHandles(node)
		const centers = handles.length ? handles.map((handle) => {
			const edge = graph.edges.find((candidate) => candidate.source === nodeId && candidate.source_handle === handle)
			return edge && !visiting.has(edge.target) ? place(edge.target, depth + 1) : leafIndex++ * horizontalGap
		}) : [leafIndex++ * horizontalGap]
		visiting.delete(nodeId)
		const center = (centers[0] + centers[centers.length - 1]) / 2
		positions.set(nodeId, { x: center - workflowNodeVisualWidth(node) / 2, y: 80 + depth * verticalGap })
		return center
	}

	place(graph.start_node_id, 0)
	const reachable = reachableWorkflowNodeIds(graph)
	const reachablePositions = [...positions.values()]
	const minimumX = Math.min(...reachablePositions.map((position) => position.x), 80)
	const shiftX = minimumX < 80 ? 80 - minimumX : 0
	const maximumX = Math.max(...graph.nodes.filter((node) => reachable.has(node.id)).map((node) => (positions.get(node.id)?.x || 0) + workflowNodeVisualWidth(node) + shiftX), 332)
	graph.nodes.filter((node) => !reachable.has(node.id)).forEach((node, index) => {
		positions.set(node.id, { x: maximumX + 220, y: 80 + index * 220 })
	})

	let changed = false
	const nodes = graph.nodes.map((node) => {
		const position = positions.get(node.id)
		if (!position) return node
		const shifted = reachable.has(node.id) ? { x: position.x + shiftX, y: position.y } : position
		if (node.position.x === shifted.x && node.position.y === shifted.y) return node
		changed = true
		return { ...node, position: shifted }
	})
	return changed ? { ...graph, nodes } : graph
}

/** Pick a deterministic HubSpot-style insertion point so catalogue clicks never
 * create an unconnected step. Prefer the selected node, then the visually last
 * unfinished path. If every path is connected, insert into a selected or
 * visually last edge.
 */
export function suggestedNodePlacement(graph: WorkflowGraph, selectedNodeId?: string): NodePlacement | undefined {
	const nodeById = new Map(graph.nodes.map((node) => [node.id, node]))
	const candidates = [
		...(selectedNodeId ? graph.nodes.filter((node) => node.id === selectedNodeId) : []),
		...graph.nodes
			.filter((node) => node.id !== selectedNodeId)
			.sort((left, right) => (right.position?.y || 0) - (left.position?.y || 0) || (right.position?.x || 0) - (left.position?.x || 0)),
	]
	for (const node of candidates) {
		const handles = workflowNodeSourceHandles(node)
		const handle = handles.find((candidate) => !graph.edges.some((edge) => edge.source === node.id && edge.source_handle === candidate))
		if (!handle) continue
		const index = Math.max(handles.indexOf(handle), 0)
		const width = workflowNodeVisualWidth(node)
		return {
			position: {
				x: node.position.x + ((index + 0.5) / Math.max(handles.length, 1)) * width - 126,
				y: node.position.y + 210,
			},
			afterNodeId: node.id,
			sourceHandle: handle,
		}
	}
	const selected = selectedNodeId ? nodeById.get(selectedNodeId) : undefined
	const selectedHandles = workflowNodeSourceHandles(selected)
	const edge = selectedHandles.flatMap((handle) => graph.edges.filter((candidate) => candidate.source === selectedNodeId && candidate.source_handle === handle)).at(0)
		|| (selectedNodeId ? graph.edges.find((candidate) => candidate.target === selectedNodeId) : undefined)
		|| [...graph.edges].sort((left, right) => {
			const leftTarget = nodeById.get(left.target)?.position
			const rightTarget = nodeById.get(right.target)?.position
			return (rightTarget?.y || 0) - (leftTarget?.y || 0) || (rightTarget?.x || 0) - (leftTarget?.x || 0)
		})[0]
	if (!edge) return undefined
	const source = nodeById.get(edge.source)
	const target = nodeById.get(edge.target)
	return {
		position: target
			? { x: ((source?.position.x || target.position.x) + target.position.x) / 2, y: ((source?.position.y || target.position.y - 210) + target.position.y) / 2 }
			: { x: source?.position.x || 120, y: (source?.position.y || 120) + 210 },
		edgeId: edge.id,
	}
}

export function insertWorkflowNode(
  graph: WorkflowGraph,
  node: WorkflowNode,
  placement: NodePlacement,
  newEdgeId: string,
): WorkflowGraph {
  if (node.type.startsWith('trigger.') || graph.nodes.some((item) => item.id === node.id)) return graph
  const edges = [...graph.edges]
  const requestedEdge = placement.edgeId ? edges.find((edge) => edge.id === placement.edgeId) : undefined
  const after = placement.afterNodeId ? graph.nodes.find((item) => item.id === placement.afterNodeId) : undefined
	const afterHandle = placement.sourceHandle || (canUseDefaultOutput(after) ? 'default' : undefined)
	const defaultEdge = after && afterHandle
		? edges.find((edge) => edge.source === after.id && edge.source_handle === afterHandle)
    : undefined
  const insertionEdge = requestedEdge || defaultEdge

  if (insertionEdge) {
		const continuationHandle = workflowNodeContinuationHandle(node)
		if (!continuationHandle) return graph
    return {
      ...graph,
      nodes: [...graph.nodes, node],
      edges: [
        ...edges.map((edge) => edge.id === insertionEdge.id ? { ...edge, target: node.id } : edge),
		{ id: newEdgeId, source: node.id, source_handle: continuationHandle, target: insertionEdge.target },
      ],
    }
  }

	if (after && afterHandle && !edges.some((edge) => edge.source === after.id && edge.source_handle === afterHandle)) {
    return {
      ...graph,
      nodes: [...graph.nodes, node],
			edges: [...edges, { id: newEdgeId, source: after.id, source_handle: afterHandle, target: node.id }],
    }
  }
	return graph
}

export function duplicateWorkflowNode(
  graph: WorkflowGraph,
  source: WorkflowNode,
  id: string,
  placement: NodePlacement,
  newEdgeId: string,
): WorkflowGraph {
  if (source.type.startsWith('trigger.')) return graph
  const duplicate: WorkflowNode = {
    ...structuredClone(source),
    id,
    position: placement.position,
  }
  return insertWorkflowNode(graph, duplicate, placement, newEdgeId)
}

/** Return the connected downstream section owned exclusively by rootId.
 * A convergence point with an incoming edge from outside the section is a
 * boundary, so copying one branch never duplicates another branch or a shared
 * continuation.
 */
export function workflowSectionNodeIds(graph: WorkflowGraph, rootId: string): string[] {
  const root = graph.nodes.find((node) => node.id === rootId)
  if (!root || root.type.startsWith('trigger.')) return []
  const owned = new Set([rootId])
  let changed = true
  while (changed) {
    changed = false
    for (const edge of graph.edges) {
      if (!owned.has(edge.source) || owned.has(edge.target)) continue
      const incoming = graph.edges.filter((candidate) => candidate.target === edge.target)
      if (incoming.every((candidate) => owned.has(candidate.source))) {
        owned.add(edge.target)
        changed = true
      }
    }
  }
  return graph.nodes.filter((node) => owned.has(node.id)).map((node) => node.id)
}

export function duplicateWorkflowSection(
  graph: WorkflowGraph,
  rootId: string,
  placement: NodePlacement,
  nodeId: () => string,
  edgeId: () => string,
): { graph: WorkflowGraph; rootId?: string } {
  const sectionIds = new Set(workflowSectionNodeIds(graph, rootId))
  const root = graph.nodes.find((node) => node.id === rootId)
  if (!root || !sectionIds.size) return { graph }
  const ids = new Map([...sectionIds].map((id) => [id, nodeId()]))
  const offset = {
    x: placement.position.x - root.position.x,
    y: placement.position.y - root.position.y,
  }
  const clones = graph.nodes.filter((node) => sectionIds.has(node.id)).map((node) => ({
    ...structuredClone(node),
    id: ids.get(node.id)!,
    position: { x: node.position.x + offset.x, y: node.position.y + offset.y },
  }))
  const internalEdges = graph.edges.filter((edge) => sectionIds.has(edge.source) && sectionIds.has(edge.target)).map((edge) => ({
    ...structuredClone(edge),
    id: edgeId(),
    source: ids.get(edge.source)!,
    target: ids.get(edge.target)!,
  }))
  const requestedEdge = placement.edgeId ? graph.edges.find((edge) => edge.id === placement.edgeId) : undefined
  const after = placement.afterNodeId ? graph.nodes.find((node) => node.id === placement.afterNodeId) : undefined
  const afterHandle = placement.sourceHandle || (canUseDefaultOutput(after) ? 'default' : undefined)
  const insertionEdge = requestedEdge || (after && afterHandle ? graph.edges.find((edge) => edge.source === after.id && edge.source_handle === afterHandle) : undefined)
  let edges = [...graph.edges]
  const copiedRootId = ids.get(rootId)!
  if (insertionEdge) {
    edges = edges.map((edge) => edge.id === insertionEdge.id ? { ...edge, target: copiedRootId } : edge)
    const internalSources = new Set(internalEdges.map((edge) => edge.source))
    const originalBoundary = graph.edges.filter((edge) => sectionIds.has(edge.source) && !sectionIds.has(edge.target))
    const clonedBoundary = originalBoundary.map((edge) => ({ ...edge, id: edgeId(), source: ids.get(edge.source)!, target: insertionEdge.target }))
    if (!clonedBoundary.length) {
      for (const clone of clones) {
        if (!internalSources.has(clone.id) && canUseDefaultOutput(clone)) clonedBoundary.push({ id: edgeId(), source: clone.id, source_handle: 'default', target: insertionEdge.target })
      }
    }
    edges.push(...internalEdges, ...clonedBoundary)
  } else {
    edges.push(...internalEdges)
    if (after && afterHandle && !edges.some((edge) => edge.source === after.id && edge.source_handle === afterHandle)) {
      edges.push({ id: edgeId(), source: after.id, source_handle: afterHandle, target: copiedRootId })
    }
  }
  return { graph: { ...graph, nodes: [...graph.nodes, ...clones], edges }, rootId: copiedRootId }
}

export function relocateWorkflowNode(
  graph: WorkflowGraph,
  nodeId: string,
  targetEdgeId: string,
  position: Position,
  newEdgeId: string,
): WorkflowGraph {
  const node = graph.nodes.find((item) => item.id === nodeId)
  const targetEdge = graph.edges.find((edge) => edge.id === targetEdgeId)
  const incoming = graph.edges.filter((edge) => edge.target === nodeId)
  const outgoing = graph.edges.filter((edge) => edge.source === nodeId)
  if (
    !node
    || !targetEdge
    || node.type.startsWith('trigger.')
    || !canUseDefaultOutput(node)
    || incoming.length > 1
    || outgoing.length > 1
    || targetEdge.source === nodeId
    || targetEdge.target === nodeId
  ) return { ...graph, nodes: graph.nodes.map((item) => item.id === nodeId ? { ...item, position } : item) }

  let edges = graph.edges.filter((edge) => edge.source !== nodeId && edge.target !== nodeId)
  if (incoming[0] && outgoing[0]) {
    edges.push({ ...incoming[0], target: outgoing[0].target })
  }
  const insertionEdge = edges.find((edge) => edge.id === targetEdgeId)
  if (!insertionEdge) return graph
  edges = [
    ...edges.map((edge) => edge.id === insertionEdge.id ? { ...edge, target: nodeId } : edge),
    { id: newEdgeId, source: nodeId, source_handle: 'default', target: insertionEdge.target },
  ]
  return {
    ...graph,
    nodes: graph.nodes.map((item) => item.id === nodeId ? { ...item, position } : item),
    edges,
  }
}
