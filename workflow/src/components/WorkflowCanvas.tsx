import {
  BaseEdge,
  Background,
  BackgroundVariant,
  Controls,
  EdgeLabelRenderer,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
  applyNodeChanges,
  type Connection,
  type EdgeProps,
  type Node,
  type NodeChange,
  type NodeProps,
  type ReactFlowInstance,
  getSmoothStepPath,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { AlertTriangle, CheckCircle2, LayoutTemplate, Link2, MousePointer2, Plus, Sparkles, Unplug, Zap } from 'lucide-react'
import { memo, useEffect, useMemo, useRef, useState, type CSSProperties, type DragEvent } from 'react'
import { reachableWorkflowNodeIds } from '../lib/workflowGraphCommands'
import { useWorkflowActions, useWorkflowDocument, useWorkflowEditor } from '../state/WorkflowContext'
import type { NodeCatalogItem, NodeType, WorkflowNode } from '../types'
import { nodeLabels, nodeIcons } from './InspectorHelpers'

type WorkflowFlowNode = Node<{ workflowNode: WorkflowNode; primaryDoctype: string; issueCount: number; manualConnections: boolean; connected: boolean }, 'workflow'>
type VirtualEndFlowNode = Node<{ sourceId: string; sourceHandle: string; label: string; insertPosition: { x: number; y: number } }, 'virtualEnd'>
type FlowNode = WorkflowFlowNode | VirtualEndFlowNode

function nodeKind(type: NodeType) {
  if (type.startsWith('trigger.')) return 'trigger'
	if (type.startsWith('condition.')) return 'logic'
  if (type.startsWith('delay.')) return 'delay'
	if (type === 'transform.value') return 'logic'
  if (type === 'end.complete') return 'end'
  return 'action'
}

const conditionOperatorLabels: Record<string, string> = {
	eq: 'equals',
	ne: 'does not equal',
	gt: 'is greater than',
	gte: 'is at least',
	lt: 'is less than',
	lte: 'is at most',
	in: 'is any of',
	not_in: 'is none of',
	contains: 'contains',
	not_contains: 'does not contain',
	contains_any: 'contains any of',
	contains_all: 'contains all of',
	contains_none: 'contains none of',
	is_set: 'is set',
	is_not_set: 'is not set',
}

function conditionSummary(condition: Record<string, unknown> | undefined) {
	if (!condition) return 'Route records using field criteria'
	if (condition.kind === 'predicate' && condition.field) {
		const operator = String(condition.operator || 'eq')
		const prefix = `${String(condition.field)} ${conditionOperatorLabels[operator] || operator}`
		return ['is_set', 'is_not_set'].includes(operator) ? prefix : `${prefix} ${String(condition.value ?? '')}`
	}
	if (['all', 'any', 'not'].includes(String(condition.kind)) && Array.isArray(condition.children)) {
		const joiner = condition.kind === 'all' ? 'all' : condition.kind === 'any' ? 'any' : 'none'
		return `Match ${joiner} of ${condition.children.length} configured rule${condition.children.length === 1 ? '' : 's'}`
	}
	return 'Route records using field criteria'
}

function nodeOutputHandles(node: WorkflowNode): Array<{ handle: string; label: string }> {
	if (node.type === 'condition.if_else') {
		return node.type_version >= 2
			? [...(Array.isArray(node.config.branches) ? node.config.branches : []).flatMap((branch) => typeof branch === 'object' && branch ? [{ handle: String((branch as Record<string, unknown>).handle || ''), label: String((branch as Record<string, unknown>).name || '') }] : []), { handle: 'none', label: 'None' }]
			: [{ handle: 'true', label: 'Yes' }, { handle: 'false', label: 'No' }]
	}
	if (node.type === 'condition.random_split') return (Array.isArray(node.config.branches) ? node.config.branches : []).flatMap((branch) => typeof branch === 'object' && branch ? [{ handle: String((branch as Record<string, unknown>).handle || ''), label: `${String((branch as Record<string, unknown>).name || '')} · ${Number((branch as Record<string, unknown>).percentage || 0)}%` }] : [])
	if (node.type === 'condition.deduplicate') return [{ handle: 'duplicate', label: 'Duplicate' }, { handle: 'unique', label: 'Unique' }]
	if (node.type === 'delay.until_event') return node.type_version >= 2 && !node.config.branch_on_timeout
		? [{ handle: 'default', label: 'Next action' }]
		: [{ handle: 'event', label: 'Event happened' }, { handle: 'timeout', label: 'Time ran out' }]
	if (node.type === 'condition.switch') return [...(Array.isArray(node.config.cases) ? node.config.cases : []).flatMap((item) => typeof item === 'object' && item ? [{ handle: String((item as Record<string, unknown>).handle || ''), label: String((item as Record<string, unknown>).value || '') }] : []), { handle: 'default', label: 'Default' }]
	if (['end.complete', 'action.delete_record', 'action.go_to'].includes(node.type)) return []
	return [{ handle: 'default', label: 'Next action' }]
}

function nodeSummary(node: WorkflowNode, primaryDoctype: string) {
  const config = node.config
  if (node.type === 'trigger.manual') return `Enroll a ${primaryDoctype} from run history`
	  if (node.type === 'trigger.document_insert') return `When a new ${primaryDoctype} matches`
	  if (node.type === 'trigger.document_change') return `When relevant ${primaryDoctype} fields change`
	if (node.type === 'trigger.filter_criteria') return `When a ${primaryDoctype} meets the configured criteria`
	if (node.type === 'trigger.event') {
		const events = Array.isArray(config.events) ? config.events : []
		return events.length ? `When any of ${events.length} selected event${events.length === 1 ? '' : 's'} occurs` : config.event_topic ? `When ${String(config.event_topic)} is received` : 'Choose a business event'
	}
	if (node.type === 'trigger.schedule') return 'Run through a durable Enrollment schedule'
  if (node.type === 'condition.if_else') {
		if (node.type_version >= 2) {
			const count = Array.isArray(config.branches) ? config.branches.length : 0
			return `${count} named criteria branch${count === 1 ? '' : 'es'} plus None`
		}
    const condition = config.condition as Record<string, unknown> | undefined
    return conditionSummary(condition)
  }
	if (node.type === 'condition.random_split') return `${Array.isArray(config.branches) ? config.branches.length : 0} named percentage paths`
  if (node.type === 'condition.switch') return config.field ? `Branch on ${String(config.field)}` : 'Branch based on exact field value'
  if (node.type === 'condition.deduplicate') return config.match_field ? `Check if ${String(config.match_field)} already exists` : 'Check for existing records'
  if (node.type === 'delay.fixed') {
    const seconds = Number(config.seconds || 3600)
    return seconds >= 3600 && seconds % 3600 === 0 ? `Continue after ${seconds / 3600} hour${seconds === 3600 ? '' : 's'}` : `Continue after ${seconds} seconds`
  }
	if (node.type === 'delay.until_date') return config.mode === 'literal' || config.datetime ? `Continue at ${String(config.datetime || 'a specific date and time')}` : config.field ? `Continue when ${String(config.field)} is reached` : 'Choose a date/time source'
	if (node.type === 'delay.until_event') {
		if (!config.event_topic) return 'Choose the event that releases this wait'
		const event = String(config.event_topic).split('.').map((part) => part.replaceAll('_', ' ')).join(' ')
		const source = config.data_source === 'action_output' || config.event_source ? ' from an earlier action' : ` on this ${primaryDoctype}`
		return `${config.timeout_mode === 'indefinite' ? 'Wait indefinitely for' : 'Wait for'} ${event}${source}`
	}
  if (node.type === 'delay.business_hours') return config.timezone ? `Wait for business hours in ${String(config.timezone)}` : 'Resume during allowed working hours'
  if (node.type === 'transform.value') return `Produce a reusable ${String(config.operation || 'coalesced')} value`
  if (node.type === 'transform.associated_record') return config.fetch_field ? `Read ${String(config.fetch_field)} from linked record` : 'Fetch a value from a linked record'
  if (node.type === 'transform.child_records') return config.fetch_field ? `Read ${String(config.fetch_field)} from child rows` : 'Aggregate a value from child table rows'
  if (node.type === 'action.update_record') return 'Apply permitted field changes'
  if (node.type === 'action.create_record') return config.target_doctype ? `Create ${String(config.target_doctype)}` : 'Choose a permitted DocType'
  if (node.type === 'action.create_todo') return config.description ? String(config.description) : 'Assign follow-up work'
  if (node.type === 'action.add_comment') return config.content ? String(config.content) : 'Write to the record timeline'
  if (node.type === 'action.notify_user') return config.subject ? String(config.subject) : 'Send an internal notification'
  if (node.type === 'action.send_email') return config.email_template ? `Send ${String(config.email_template)}` : config.content_mode === 'inline' ? 'Send a quick email' : 'Choose an Email Template'
  if (node.type === 'action.send_sms') return 'Submit a consent-aware SMS to the configured gateway'
  if (node.type === 'action.webhook') return config.url ? `POST to ${String(config.url)}` : 'Choose an allowlisted HTTPS endpoint'
  if (node.type === 'action.call_subflow') return config.subflow_id ? `Call ${String(config.subflow_id)}` : 'Execute another workflow as a subflow'
  if (node.type === 'action.numeric_adjust') return config.field ? `${String(config.operation || 'add').charAt(0).toUpperCase() + String(config.operation || 'add').slice(1)} on ${String(config.field)}` : 'Increment or decrement a number field'
  if (node.type === 'action.manage_association') return config.target_doctype ? `${String(config.operation || 'link').charAt(0).toUpperCase() + String(config.operation || 'link').slice(1)} ${String(config.target_doctype)}` : 'Link or unlink records'
  if (node.type === 'action.round_robin') return config.group ? `Assign to ${String(config.group)}` : 'Distribute ownership across a group'
  if (node.type === 'action.delete_record') return 'Permanently delete the enrolled record'
  return 'This path ends successfully'
}

const WorkflowNodeCard = memo(({ data, selected }: NodeProps<WorkflowFlowNode>) => {
	const node = data.workflowNode
	const branchOutputs = ['condition.if_else', 'condition.random_split', 'condition.deduplicate', 'condition.switch'].includes(node.type) || (node.type === 'delay.until_event' && (node.type_version < 2 || Boolean(node.config.branch_on_timeout))) ? nodeOutputHandles(node) : []
	const nodeWidth = branchOutputs.length > 3 ? Math.min(1440, Math.max(252, branchOutputs.length * 84)) : undefined
  const trigger = node.type.startsWith('trigger.')
  const Icon = nodeIcons[node.type] || Zap
  const kind = nodeKind(node.type)
  return (
	    <article className={`workflow-node workflow-node--${kind}`} style={nodeWidth ? { width: nodeWidth } : undefined} data-invalid={data.issueCount > 0 ? 'true' : 'false'} data-selected={selected ? 'true' : 'false'} data-manual-links={data.manualConnections ? 'true' : 'false'} data-connected={data.connected ? 'true' : 'false'}>
      <span className="workflow-node__rail" aria-hidden />
      {!trigger && <Handle type="target" position={Position.Top} className={`workflow-handle ${data.manualConnections ? '' : 'workflow-handle--guided'}`} />}
      <div className="px-4 pb-3.5 pt-4">
        <div className="flex items-start gap-3">
          <span className="workflow-node__icon grid size-9 shrink-0 place-items-center rounded-[10px]">
            <Icon size={17} strokeWidth={2.1} aria-hidden />
          </span>
          <div className="min-w-0 flex-1">
            <p className="workflow-node__eyebrow text-[10px] font-bold uppercase tracking-[0.12em]">{kind}{node.type === 'action.round_robin' ? node.type_version === 1 ? ' · legacy v1' : ' · rotating v2' : ''}</p>
            <h3 className="text-heading mt-0.5 truncate text-[13px] font-bold leading-5">{nodeLabels[node.type] || node.type}</h3>
          </div>
		  {!data.connected ? <span className="workflow-node__unconnected flex shrink-0 items-center gap-1 rounded-full px-2 py-1 text-[9px] font-bold" title="This step is not connected to the enrollment trigger"><Unplug size={10} />Not connected</span> : data.issueCount > 0 && <span className="flex shrink-0 items-center gap-1 rounded-full bg-red-50 px-2 py-1 text-[9px] font-bold text-red-600 dark:bg-red-500/15 dark:text-red-300" title={`${data.issueCount} validation issue${data.issueCount === 1 ? '' : 's'}`}><AlertTriangle size={10} />{data.issueCount}</span>}
        </div>
        <p className="text-muted mt-3 line-clamp-2 min-h-9 text-[11px] leading-[18px]">{nodeSummary(node, data.primaryDoctype)}</p>
      </div>
		{branchOutputs.length ? (
			<div className="workflow-node__footer relative flex rounded-b-xl px-2 py-2 text-[9px] font-bold uppercase tracking-[0.04em]">
				{branchOutputs.map((output, index) => <span className="min-w-0 flex-1 truncate text-center" title={output.label} key={output.handle}>{output.label}<Handle id={output.handle} type="source" position={Position.Bottom} className={`workflow-handle ${data.manualConnections ? '' : 'workflow-handle--guided'}`} style={{ left: `${((index + 0.5) / branchOutputs.length) * 100}%` }} /></span>)}
			</div>
      ) : !['end.complete', 'action.delete_record', 'action.go_to'].includes(node.type) ? (
        <div className="workflow-node__footer flex items-center justify-between rounded-b-xl px-4 py-2">
          <span className="text-light text-[10px] font-medium">Next action</span>
          <span className="text-light text-[9px] font-semibold uppercase tracking-wider">Use + below</span>
          <Handle id="default" type="source" position={Position.Bottom} className={`workflow-handle ${data.manualConnections ? '' : 'workflow-handle--guided'}`} />
        </div>
      ) : (
        <div className="workflow-node__footer flex items-center gap-1.5 rounded-b-xl px-4 py-2 text-[10px] font-semibold text-emerald-600">
          <CheckCircle2 size={12} /> End of path
        </div>
      )}
    </article>
  )
})
WorkflowNodeCard.displayName = 'WorkflowNodeCard'

const VirtualEndCard = memo(({ data }: NodeProps<VirtualEndFlowNode>) => {
	const actions = useWorkflowActions()
	return <div className="workflow-path-end">
		<Handle type="target" position={Position.Top} className="workflow-virtual-target" />
		<span className="workflow-path-end__label" title={data.label}>{data.label}</span>
		<button
			type="button"
			className="workflow-path-end__add nodrag nopan"
			title={`Add a step to ${data.label}`}
			onClick={(event) => {
				event.stopPropagation()
				actions.beginInsert({ afterNodeId: data.sourceId, sourceHandle: data.sourceHandle, position: data.insertPosition, label: `After ${data.label}` })
			}}
		>
			<Plus size={15} /><span className="sr-only">Add step</span>
		</button>
		<span className="workflow-virtual-end"><CheckCircle2 size={12} />END</span>
	</div>
})
VirtualEndCard.displayName = 'VirtualEndCard'

const nodeTypes = { workflow: WorkflowNodeCard, virtualEnd: VirtualEndCard }

const GuidedEdge = memo((props: EdgeProps) => {
	const actions = useWorkflowActions()
	const [edgePath, labelX, labelY] = getSmoothStepPath({
		sourceX: props.sourceX,
		sourceY: props.sourceY,
		sourcePosition: props.sourcePosition,
		targetX: props.targetX,
		targetY: props.targetY,
		targetPosition: props.targetPosition,
	})
	const edgeColor = typeof props.style?.stroke === 'string' ? props.style.stroke : '#ff6b47'
	return <>
		<BaseEdge id={props.id} path={edgePath} markerEnd={props.markerEnd} style={props.style} />
		<EdgeLabelRenderer>
			<button
				type="button"
				className="workflow-edge-add nodrag nopan"
				style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`, '--workflow-edge-color': edgeColor } as CSSProperties}
				aria-label="Insert a step between these steps"
				title="Insert step — connections update automatically"
				onClick={(event) => {
					event.stopPropagation()
					actions.beginInsert({ edgeId: props.id, position: { x: labelX - 126, y: labelY - 70 }, label: 'Between these steps' })
				}}
			>
				<Plus size={13} />
			</button>
		</EdgeLabelRenderer>
	</>
})
GuidedEdge.displayName = 'GuidedEdge'

const edgeTypes = { guided: GuidedEdge }

function distanceToSegment(point: { x: number; y: number }, start: { x: number; y: number }, end: { x: number; y: number }) {
	const dx = end.x - start.x
	const dy = end.y - start.y
	if (!dx && !dy) return Math.hypot(point.x - start.x, point.y - start.y)
	const ratio = Math.max(0, Math.min(1, ((point.x - start.x) * dx + (point.y - start.y) * dy) / (dx * dx + dy * dy)))
	return Math.hypot(point.x - (start.x + ratio * dx), point.y - (start.y + ratio * dy))
}

export function WorkflowCanvas() {
  const { graph, validation } = useWorkflowDocument()
  const { selectedNodeId } = useWorkflowEditor()
  const actions = useWorkflowActions()
  const [manualConnections, setManualConnections] = useState(false)
	const connectedNodeIds = useMemo(() => graph ? reachableWorkflowNodeIds(graph) : new Set<string>(), [graph])
  const dragging = useRef(false)
  const flow = useRef<ReactFlowInstance<FlowNode> | null>(null)
	const implicitEnds = useMemo(() => {
		const connected = new Set((graph?.edges || []).map((edge) => `${edge.source}:${edge.source_handle}`))
		return (graph?.nodes || []).flatMap((node) => {
			const outputs = nodeOutputHandles(node)
			const width = outputs.length > 3 ? Math.min(1440, Math.max(252, outputs.length * 84)) : 252
			return outputs.flatMap((output, index) => connected.has(`${node.id}:${output.handle}`) ? [] : [{
				sourceId: node.id,
				sourceHandle: output.handle,
				label: output.label,
				position: {
					x: (node.position?.x || 120) + ((index + 0.5) / outputs.length) * width - 48,
					y: (node.position?.y || 120) + 190,
				},
				insertPosition: {
					x: (node.position?.x || 120) + ((index + 0.5) / outputs.length) * width - 126,
					y: (node.position?.y || 120) + 190,
				},
			}])
		})
	}, [graph?.edges, graph?.nodes])
  const mappedNodes = useMemo<FlowNode[]>(
		() => [
			...(graph?.nodes || []).map((node): WorkflowFlowNode => ({
				id: node.id,
				type: 'workflow',
				position: node.position || { x: 120, y: 120 },
				data: { workflowNode: node, primaryDoctype: graph?.primary_doctype || 'record', issueCount: validation.filter((issue) => issue.node_id === node.id).length, manualConnections, connected: connectedNodeIds.has(node.id) },
			})),
			...implicitEnds.map((endpoint): VirtualEndFlowNode => ({
				id: `virtual-end:${endpoint.sourceId}:${endpoint.sourceHandle}`,
				type: 'virtualEnd',
				position: endpoint.position,
				data: { sourceId: endpoint.sourceId, sourceHandle: endpoint.sourceHandle, label: endpoint.label, insertPosition: endpoint.insertPosition },
				draggable: false,
				selectable: false,
				deletable: false,
			})),
		],
		[connectedNodeIds, graph?.nodes, graph?.primary_doctype, implicitEnds, manualConnections, validation],
  )
  const [nodes, setNodes] = useState(mappedNodes)

  useEffect(() => {
    if (!dragging.current) setNodes(mappedNodes)
  }, [mappedNodes])

  useEffect(() => {
    if (!selectedNodeId) return
    const selected = flow.current?.getNode(selectedNodeId)
    if (selected) void flow.current?.fitView({ nodes: [selected], padding: 0.55, duration: 260, maxZoom: 1.1 })
  }, [mappedNodes.length, selectedNodeId])

	const edges = useMemo(() => {
		const palette = ['#6d5ce8', '#078a72', '#d46a16', '#2474c5', '#b54691', '#547080']
		const edgeColor = (sourceId: string, sourceHandle: string) => {
			const source = graph?.nodes.find((node) => node.id === sourceId)
			const outputs = source ? nodeOutputHandles(source) : []
			return outputs.length > 1 ? palette[Math.max(0, outputs.findIndex((output) => output.handle === sourceHandle)) % palette.length] : '#75899b'
		}
		return [...(graph?.edges || []).map((edge) => ({
      id: edge.id,
		type: 'guided',
      source: edge.source,
      target: edge.target,
      sourceHandle: edge.source_handle,
      markerEnd: { type: MarkerType.ArrowClosed, width: 15, height: 15 },
      animated: selectedNodeId === edge.source || selectedNodeId === edge.target,
	  deletable: manualConnections,
	  style: { stroke: edgeColor(edge.source, edge.source_handle), strokeWidth: 1.8 },
			})), ...implicitEnds.map((endpoint) => ({
				id: `virtual-end-edge:${endpoint.sourceId}:${endpoint.sourceHandle}`,
				source: endpoint.sourceId,
				target: `virtual-end:${endpoint.sourceId}:${endpoint.sourceHandle}`,
				sourceHandle: endpoint.sourceHandle,
			selectable: false,
			deletable: false,
			style: { stroke: edgeColor(endpoint.sourceId, endpoint.sourceHandle), strokeWidth: 1.45, strokeDasharray: '5 5' },
		}))]
	}, [graph?.edges, graph?.nodes, implicitEnds, manualConnections, selectedNodeId])

  const onNodesChange = (changes: NodeChange<FlowNode>[]) => {
	const safeChanges = changes.filter((change) => change.type === 'add' || (!change.id.startsWith('virtual-end:') && (change.type !== 'remove' || change.id !== graph?.start_node_id)))
    setNodes((current) => applyNodeChanges(safeChanges, current))
    actions.removeNodes(safeChanges.filter((change) => change.type === 'remove').map((change) => change.id))
  }

	const connect = (connection: Connection) => {
    if (!connection.source || !connection.target) return
    const source = graph?.nodes.find((node) => node.id === connection.source)
    const handle = connection.sourceHandle || 'default'
		const allowed = source?.type === 'condition.if_else' ? source.type_version >= 2
			? [...(Array.isArray(source.config.branches) ? source.config.branches : []).flatMap((branch) => typeof branch === 'object' && branch ? [String((branch as Record<string, unknown>).handle || '')] : []), 'none']
			: ['true', 'false']
			: source?.type === 'condition.random_split' ? (Array.isArray(source.config.branches) ? source.config.branches : []).flatMap((branch) => typeof branch === 'object' && branch ? [String((branch as Record<string, unknown>).handle || '')] : [])
			: source?.type === 'condition.deduplicate' ? ['duplicate', 'unique']
			: source?.type === 'delay.until_event' ? source.type_version >= 2 && !source.config.branch_on_timeout ? ['default'] : ['event', 'timeout']
					: source?.type === 'condition.switch' ? [...(Array.isArray(source.config.cases) ? source.config.cases : []).flatMap((item) => typeof item === 'object' && item ? [String((item as Record<string, unknown>).handle || '')] : []), 'default'] : ['default']
		if (!allowed.includes(handle)) return
    actions.connect({ source: connection.source, target: connection.target, source_handle: handle })
  }

	useEffect(() => {
		const keyboardClipboard = (event: KeyboardEvent) => {
			const target = event.target as HTMLElement | null
			if (target?.matches('input, textarea, select, [contenteditable="true"]')) return
			if (!(event.ctrlKey || event.metaKey) || !selectedNodeId) return
			if (event.key.toLowerCase() === 'c') {
				event.preventDefault()
				if (event.shiftKey) actions.copySection(selectedNodeId)
				else actions.copyNode(selectedNodeId)
			}
			if (event.key.toLowerCase() === 'v') {
				event.preventDefault()
				if (event.shiftKey) actions.pasteSection()
				else actions.pasteNode()
			}
		}
		document.addEventListener('keydown', keyboardClipboard)
		return () => document.removeEventListener('keydown', keyboardClipboard)
	}, [actions, selectedNodeId])

	const dropCatalogNode = (event: DragEvent) => {
		event.preventDefault()
		const raw = event.dataTransfer.getData('application/x-finbyz-workflow-node')
		if (!raw || !flow.current || !graph) return
		let item: NodeCatalogItem
		try {
			item = JSON.parse(raw) as NodeCatalogItem
		} catch {
			return
		}
		if (item.type.startsWith('trigger.') || item.authoring_hidden) return
		const point = flow.current.screenToFlowPosition({ x: event.clientX, y: event.clientY })
		const position = { x: point.x - 126, y: point.y - 70 }
		const nearestEndpoint = implicitEnds
			.map((endpoint) => ({ endpoint, distance: Math.hypot(point.x - (endpoint.position.x + 48), point.y - (endpoint.position.y + 28)) }))
			.sort((left, right) => left.distance - right.distance)[0]
		const nodeById = new Map(graph.nodes.map((node) => [node.id, node]))
		const nearestEdge = graph.edges
			.map((edge) => {
				const source = nodeById.get(edge.source)?.position
				const target = nodeById.get(edge.target)?.position
				if (!source || !target) return { edge, distance: Number.POSITIVE_INFINITY }
				return { edge, distance: distanceToSegment(point, { x: source.x + 126, y: source.y + 145 }, { x: target.x + 126, y: target.y }) }
			})
			.sort((left, right) => left.distance - right.distance)[0]
		const nearestParent = graph.nodes
			.filter((node) => node.position && point.y >= node.position.y + 80)
			.map((node) => ({ node, distance: Math.hypot(point.x - (node.position.x + 126), point.y - (node.position.y + 150)) }))
			.filter((candidate) => candidate.distance < 300)
			.sort((left, right) => left.distance - right.distance)[0]
		actions.addNode(item, {
			position,
			edgeId: nearestEndpoint?.distance < 110 ? undefined : nearestEdge?.distance < 85 ? nearestEdge.edge.id : undefined,
			afterNodeId: nearestEndpoint?.distance < 110 ? nearestEndpoint.endpoint.sourceId : nearestEdge?.distance < 85 ? undefined : nearestParent?.node.id,
			sourceHandle: nearestEndpoint?.distance < 110 ? nearestEndpoint.endpoint.sourceHandle : undefined,
		})
	}

  return (
		<div className="workflow-canvas relative h-full min-h-0" aria-label="Workflow canvas" onDragOver={(event) => { if (event.dataTransfer.types.includes('application/x-finbyz-workflow-node')) { event.preventDefault(); event.dataTransfer.dropEffect = 'copy' } }} onDrop={dropCatalogNode}>
      <div className="pointer-events-none absolute left-1/2 top-4 z-10 flex -translate-x-1/2 items-center gap-2 whitespace-nowrap rounded-full border border-[var(--border-color)] bg-[var(--navbar-bg)] px-3 py-1.5 text-[11px] font-medium text-[var(--text-muted)] shadow-sm backdrop-blur-md">
        <MousePointer2 size={12} />
        <span>{graph?.nodes.length || 0} steps</span>
        <span className="hidden size-1 rounded-full bg-[var(--border-color)] sm:block" />
				<span className="hidden sm:inline">Use + to add · connections are automatic</span>
        <Sparkles className="hidden text-magic-500 sm:block" size={12} />
      </div>
		<div className="absolute right-4 top-4 z-20 flex items-center gap-2">
			<button type="button" className="workflow-canvas-tool" onClick={() => actions.autoArrange()} title="Arrange steps into clear branch lanes"><LayoutTemplate size={12} /> Tidy layout</button>
			<button type="button" className="workflow-canvas-tool" aria-pressed={manualConnections} onClick={() => setManualConnections((enabled) => !enabled)} title="Advanced mode for drawing or deleting connections manually"><Link2 size={12} /> Manual links {manualConnections ? 'on' : 'off'}</button>
		</div>
      <ReactFlow<FlowNode>
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
		edgeTypes={edgeTypes}
        fitView
        fitViewOptions={{ padding: 0.24 }}
        minZoom={0.25}
        maxZoom={1.8}
        defaultEdgeOptions={{ type: 'smoothstep' }}
        deleteKeyCode={['Backspace', 'Delete']}
        onNodesChange={onNodesChange}
        onInit={(instance) => { flow.current = instance }}
			onNodeClick={(_, node) => { if (node.type === 'workflow') actions.select(node.id) }}
        onPaneClick={() => actions.select()}
        onNodeDragStart={() => { dragging.current = true }}
			onNodeDragStop={(_, node) => {
			  dragging.current = false
			  if (node.type !== 'workflow' || !graph) return
			  const point = { x: node.position.x + 126, y: node.position.y + 72 }
			  const nodeById = new Map(graph.nodes.map((item) => [item.id, item]))
			  const nearestEdge = graph.edges
				.filter((edge) => edge.source !== node.id && edge.target !== node.id)
				.map((edge) => {
				  const source = nodeById.get(edge.source)?.position
				  const target = nodeById.get(edge.target)?.position
				  if (!source || !target) return { edge, distance: Number.POSITIVE_INFINITY }
				  return { edge, distance: distanceToSegment(point, { x: source.x + 126, y: source.y + 145 }, { x: target.x + 126, y: target.y }) }
				})
				.sort((left, right) => left.distance - right.distance)[0]
			  if (nearestEdge?.distance < 70) actions.relocateNode(node.id, nearestEdge.edge.id, node.position)
			  else actions.moveNode(node.id, node.position)
			}}
		nodesConnectable={manualConnections}
		edgesReconnectable={manualConnections}
		onConnect={manualConnections ? connect : undefined}
			onEdgesDelete={(deleted) => actions.removeEdges(deleted.map((edge) => edge.id).filter((id) => !id.startsWith('virtual-end-edge:')))}
        isValidConnection={(connection) => connection.source !== connection.target}
        proOptions={{ hideAttribution: true }}
      >
        <Background variant={BackgroundVariant.Dots} gap={24} size={1.25} color="var(--canvas-dot)" />
        <MiniMap className="hidden sm:block" pannable zoomable position="bottom-right" maskColor="rgb(25 39 51 / 0.06)" nodeColor="#ff8a6d" />
        <Controls position="bottom-left" showInteractive={false} />
      </ReactFlow>
    </div>
  )
}
