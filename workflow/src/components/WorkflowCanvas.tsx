import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
  applyNodeChanges,
  type Connection,
  type Node,
  type NodeChange,
  type NodeProps,
  type ReactFlowInstance,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { AlertTriangle, CheckCircle2, MousePointer2, Sparkles, Zap } from 'lucide-react'
import { memo, useEffect, useMemo, useRef, useState } from 'react'
import { useWorkflowActions, useWorkflowDocument, useWorkflowEditor } from '../state/WorkflowContext'
import type { NodeType, WorkflowNode } from '../types'
import { nodeLabels, nodeIcons } from './InspectorHelpers'

type FlowNode = Node<{ workflowNode: WorkflowNode; primaryDoctype: string; issueCount: number }, 'workflow'>

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

function nodeSummary(node: WorkflowNode, primaryDoctype: string) {
  const config = node.config
  if (node.type === 'trigger.manual') return `Enroll a ${primaryDoctype} from run history`
  if (node.type === 'trigger.document_insert') return `When a new ${primaryDoctype} matches`
  if (node.type === 'trigger.document_change') return `When relevant ${primaryDoctype} fields change`
	if (node.type === 'trigger.schedule') return 'Run through a durable Enrollment schedule'
  if (node.type === 'condition.if_else') {
    const condition = config.condition as Record<string, unknown> | undefined
    return conditionSummary(condition)
  }
  if (node.type === 'condition.switch') return config.field ? `Branch on ${String(config.field)}` : 'Branch based on exact field value'
  if (node.type === 'condition.deduplicate') return config.match_field ? `Check if ${String(config.match_field)} already exists` : 'Check for existing records'
  if (node.type === 'delay.fixed') {
    const seconds = Number(config.seconds || 3600)
    return seconds >= 3600 && seconds % 3600 === 0 ? `Continue after ${seconds / 3600} hour${seconds === 3600 ? '' : 's'}` : `Continue after ${seconds} seconds`
  }
  if (node.type === 'delay.until_date') return config.field ? `Continue when ${String(config.field)} is reached` : 'Choose a date or datetime field'
  if (node.type === 'delay.until_event') return config.event_topic ? `Wait for ${String(config.event_topic)}` : 'Wait for an external event'
  if (node.type === 'delay.business_hours') return config.timezone ? `Wait for business hours in ${String(config.timezone)}` : 'Resume during allowed working hours'
  if (node.type === 'transform.value') return `Produce a reusable ${String(config.operation || 'coalesced')} value`
  if (node.type === 'transform.associated_record') return config.fetch_field ? `Read ${String(config.fetch_field)} from linked record` : 'Fetch a value from a linked record'
  if (node.type === 'transform.child_records') return config.fetch_field ? `Read ${String(config.fetch_field)} from child rows` : 'Aggregate a value from child table rows'
  if (node.type === 'action.update_record') return 'Apply permitted field changes'
  if (node.type === 'action.create_record') return config.target_doctype ? `Create ${String(config.target_doctype)}` : 'Choose a permitted DocType'
  if (node.type === 'action.create_todo') return config.description ? String(config.description) : 'Assign follow-up work'
  if (node.type === 'action.add_comment') return config.content ? String(config.content) : 'Write to the record timeline'
  if (node.type === 'action.notify_user') return config.subject ? String(config.subject) : 'Send an internal notification'
  if (node.type === 'action.send_email') return 'Queue a consent-aware Frappe email'
  if (node.type === 'action.send_sms') return 'Submit a consent-aware SMS to the configured gateway'
  if (node.type === 'action.webhook') return config.url ? `POST to ${String(config.url)}` : 'Choose an allowlisted HTTPS endpoint'
  if (node.type === 'action.call_subflow') return config.subflow_id ? `Call ${String(config.subflow_id)}` : 'Execute another workflow as a subflow'
  if (node.type === 'action.numeric_adjust') return config.field ? `${String(config.operation || 'add').charAt(0).toUpperCase() + String(config.operation || 'add').slice(1)} on ${String(config.field)}` : 'Increment or decrement a number field'
  if (node.type === 'action.manage_association') return config.target_doctype ? `${String(config.operation || 'link').charAt(0).toUpperCase() + String(config.operation || 'link').slice(1)} ${String(config.target_doctype)}` : 'Link or unlink records'
  if (node.type === 'action.round_robin') return config.group ? `Assign to ${String(config.group)}` : 'Distribute ownership across a group'
  if (node.type === 'action.delete_record') return 'Permanently delete the enrolled record'
  return 'This path ends successfully'
}

const WorkflowNodeCard = memo(({ data, selected }: NodeProps<FlowNode>) => {
	const node = data.workflowNode
	const branchOutputs = node.type === 'condition.if_else'
		? [{ handle: 'true', label: 'Yes' }, { handle: 'false', label: 'No' }]
		: node.type === 'condition.deduplicate'
			? [{ handle: 'duplicate', label: 'Duplicate' }, { handle: 'unique', label: 'Unique' }]
			: node.type === 'delay.until_event'
				? [{ handle: 'event', label: 'Event' }, { handle: 'timeout', label: 'Timeout' }]
				: node.type === 'condition.switch'
					? [...(Array.isArray(node.config.cases) ? node.config.cases : []).flatMap((item) => typeof item === 'object' && item ? [{ handle: String((item as Record<string, unknown>).handle || ''), label: String((item as Record<string, unknown>).value || '') }] : []), { handle: 'default', label: 'Default' }]
					: []
  const trigger = node.type.startsWith('trigger.')
  const Icon = nodeIcons[node.type] || Zap
  const kind = nodeKind(node.type)
  return (
    <article className={`workflow-node workflow-node--${kind}`} data-invalid={data.issueCount > 0 ? 'true' : 'false'} data-selected={selected ? 'true' : 'false'}>
      <span className="workflow-node__rail" aria-hidden />
      {!trigger && <Handle type="target" position={Position.Top} className="workflow-handle" />}
      <div className="px-4 pb-3.5 pt-4">
        <div className="flex items-start gap-3">
          <span className="workflow-node__icon grid size-9 shrink-0 place-items-center rounded-[10px]">
            <Icon size={17} strokeWidth={2.1} aria-hidden />
          </span>
          <div className="min-w-0 flex-1">
            <p className="workflow-node__eyebrow text-[10px] font-bold uppercase tracking-[0.12em]">{kind}{node.type === 'action.round_robin' ? node.type_version === 1 ? ' · legacy v1' : ' · rotating v2' : ''}</p>
            <h3 className="text-heading mt-0.5 truncate text-[13px] font-bold leading-5">{nodeLabels[node.type] || node.type}</h3>
          </div>
          {data.issueCount > 0 && <span className="flex shrink-0 items-center gap-1 rounded-full bg-red-50 px-2 py-1 text-[9px] font-bold text-red-600 dark:bg-red-500/15 dark:text-red-300" title={`${data.issueCount} validation issue${data.issueCount === 1 ? '' : 's'}`}><AlertTriangle size={10} />{data.issueCount}</span>}
        </div>
        <p className="text-muted mt-3 line-clamp-2 min-h-9 text-[11px] leading-[18px]">{nodeSummary(node, data.primaryDoctype)}</p>
      </div>
		{branchOutputs.length ? (
			<div className="workflow-node__footer relative flex rounded-b-xl px-2 py-2 text-[9px] font-bold uppercase tracking-[0.04em]">
				{branchOutputs.map((output, index) => <span className="min-w-0 flex-1 truncate text-center" title={output.label} key={output.handle}>{output.label}<Handle id={output.handle} type="source" position={Position.Bottom} className="workflow-handle" style={{ left: `${((index + 0.5) / branchOutputs.length) * 100}%` }} /></span>)}
			</div>
      ) : !['end.complete', 'action.delete_record'].includes(node.type) ? (
        <div className="workflow-node__footer flex items-center justify-between rounded-b-xl px-4 py-2">
          <span className="text-light text-[10px] font-medium">Next action</span>
          <span className="text-light text-[9px] font-semibold uppercase tracking-wider">Connect</span>
          <Handle id="default" type="source" position={Position.Bottom} className="workflow-handle" />
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

const nodeTypes = { workflow: WorkflowNodeCard }

export function WorkflowCanvas() {
  const { graph, validation } = useWorkflowDocument()
  const { selectedNodeId } = useWorkflowEditor()
  const actions = useWorkflowActions()
  const dragging = useRef(false)
  const flow = useRef<ReactFlowInstance<FlowNode> | null>(null)
  const mappedNodes = useMemo<FlowNode[]>(
    () => (graph?.nodes || []).map((node) => ({
      id: node.id,
      type: 'workflow',
      position: node.position || { x: 120, y: 120 },
      data: { workflowNode: node, primaryDoctype: graph?.primary_doctype || 'record', issueCount: validation.filter((issue) => issue.node_id === node.id).length },
    })),
    [graph?.nodes, graph?.primary_doctype, validation],
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

  const edges = useMemo(
    () => (graph?.edges || []).map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      sourceHandle: edge.source_handle,
      markerEnd: { type: MarkerType.ArrowClosed, width: 15, height: 15 },
      animated: selectedNodeId === edge.source || selectedNodeId === edge.target,
      style: { stroke: '#8796a5', strokeWidth: 1.65 },
    })),
    [graph?.edges, selectedNodeId],
  )

  const onNodesChange = (changes: NodeChange<FlowNode>[]) => {
	const safeChanges = changes.filter((change) => change.type !== 'remove' || change.id !== graph?.start_node_id)
    setNodes((current) => applyNodeChanges(safeChanges, current))
    actions.removeNodes(safeChanges.filter((change) => change.type === 'remove').map((change) => change.id))
  }

	const connect = (connection: Connection) => {
    if (!connection.source || !connection.target) return
    const source = graph?.nodes.find((node) => node.id === connection.source)
    const handle = connection.sourceHandle || 'default'
		const allowed = source?.type === 'condition.if_else' ? ['true', 'false']
			: source?.type === 'condition.deduplicate' ? ['duplicate', 'unique']
				: source?.type === 'delay.until_event' ? ['event', 'timeout']
					: source?.type === 'condition.switch' ? [...(Array.isArray(source.config.cases) ? source.config.cases : []).flatMap((item) => typeof item === 'object' && item ? [String((item as Record<string, unknown>).handle || '')] : []), 'default'] : ['default']
		if (!allowed.includes(handle)) return
    actions.connect({ source: connection.source, target: connection.target, source_handle: handle })
  }

  return (
    <div className="workflow-canvas relative h-full min-h-0" aria-label="Workflow canvas">
      <div className="pointer-events-none absolute left-1/2 top-4 z-10 flex -translate-x-1/2 items-center gap-2 whitespace-nowrap rounded-full border border-[var(--border-color)] bg-[var(--navbar-bg)] px-3 py-1.5 text-[11px] font-medium text-[var(--text-muted)] shadow-sm backdrop-blur-md">
        <MousePointer2 size={12} />
        <span>{graph?.nodes.length || 0} steps</span>
        <span className="hidden size-1 rounded-full bg-[var(--border-color)] sm:block" />
        <span className="hidden sm:inline">Select a step to configure</span>
        <Sparkles className="hidden text-magic-500 sm:block" size={12} />
      </div>
      <ReactFlow<FlowNode>
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.24 }}
        minZoom={0.25}
        maxZoom={1.8}
        defaultEdgeOptions={{ type: 'smoothstep' }}
        deleteKeyCode={['Backspace', 'Delete']}
        onNodesChange={onNodesChange}
        onInit={(instance) => { flow.current = instance }}
        onNodeClick={(_, node) => actions.select(node.id)}
        onPaneClick={() => actions.select()}
        onNodeDragStart={() => { dragging.current = true }}
        onNodeDragStop={(_, node) => {
          dragging.current = false
          actions.moveNode(node.id, node.position)
        }}
        onConnect={connect}
        onEdgesDelete={(deleted) => actions.removeEdges(deleted.map((edge) => edge.id))}
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
