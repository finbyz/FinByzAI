/* oxlint-disable react/only-export-components -- reducers and typed hooks are intentional public test seams */
import Ajv from 'ajv'
import {
  createContext,
  type Dispatch,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  useRef,
} from 'react'
import { call, mutationEnvelope, WorkflowApiError } from '../lib/api'
import { canonicalValue, removeWorkflowNodes, replaceWorkflowTrigger, sameExecutionGraph } from '../lib/workflowGraphCommands'
import type {
  NodeCatalogItem,
  SimulationResult,
  ValidationIssue,
  WorkflowEdge,
  WorkflowGraph,
  WorkflowNode,
  WorkflowPublication,
  WorkflowSettings,
} from '../types'

export interface DocumentState {
  workflowId: string
  title: string
  status: string
  publication: WorkflowPublication
  graph: WorkflowGraph | null
  savedGraph: WorkflowGraph | null
  settings: WorkflowSettings
  savedSettings: WorkflowSettings
  serverRevision: number
  validation: ValidationIssue[]
  validationFresh: boolean
  validatedRevision?: number
  validatedGraphHash?: string
  dirty: boolean
  loading: boolean
  saving: boolean
  conflict: boolean
  error?: string
  dirtySince?: number
}

export interface EditorState {
  selectedNodeId?: string
  validationOpen: boolean
  simulationOpen: boolean
  publishOpen: boolean
  runsOpen: boolean
  policiesOpen: boolean
  versionsOpen: boolean
  mode: 'edit' | 'conflict'
  simulation?: SimulationResult
  versionDiff?: { nodes: { added: string[]; removed: string[]; changed: string[] }; edges: { added: string[]; removed: string[]; changed: string[] }; settings_changed: boolean }
}

export interface HistoryState {
  past: WorkflowGraph[]
  future: WorkflowGraph[]
  lastKey?: string
  lastAt?: number
}

type DocumentAction =
  | { type: 'LOAD_START'; workflowId: string }
  | { type: 'LOAD_SUCCESS'; title: string; status: string; publication: WorkflowPublication; graph: WorkflowGraph; savedGraph?: WorkflowGraph; settings: WorkflowSettings; savedSettings?: WorkflowSettings; revision: number; validation: ValidationIssue[]; dirty?: boolean }
  | { type: 'LOAD_ERROR'; error: string }
  | { type: 'REPLACE_GRAPH'; graph: WorkflowGraph; dirty?: boolean; affectsPublication?: boolean }
  | { type: 'REPLACE_SETTINGS'; settings: WorkflowSettings; dirty?: boolean }
  | { type: 'SAVE_START' }
  | { type: 'SAVE_SUCCESS'; revision: number; graphHash: string; validation: ValidationIssue[]; publication: WorkflowPublication; graph: WorkflowGraph; settings: WorkflowSettings; affectsPublication: boolean }
  | { type: 'SAVE_ERROR'; error: string; conflict?: boolean }
  | { type: 'SET_VALIDATION'; validation: ValidationIssue[]; revision: number; graphHash: string }
  | { type: 'SET_STATUS'; status: string }

type EditorAction =
  | { type: 'SELECT'; nodeId?: string }
  | { type: 'TOGGLE'; panel: 'validationOpen' | 'simulationOpen' | 'publishOpen' | 'runsOpen' | 'policiesOpen' | 'versionsOpen'; open?: boolean }
  | { type: 'SIMULATION'; result: SimulationResult }
  | { type: 'VERSION_DIFF'; diff?: EditorState['versionDiff'] }
  | { type: 'CONFLICT' }
  | { type: 'RESOLVE_CONFLICT' }

type HistoryAction =
  | { type: 'RECORD'; graph: WorkflowGraph; key: string; at: number }
  | { type: 'UNDO'; current: WorkflowGraph }
  | { type: 'REDO'; current: WorkflowGraph }
  | { type: 'RESET' }

const initialDocument: DocumentState = {
  workflowId: '',
  title: '',
  status: 'DRAFT',
  publication: {
    state: 'NEVER_PUBLISHED',
    has_published_version: false,
    has_unpublished_changes: true,
    draft_matches_latest_version: false,
    latest_version_no: 0,
    next_version_no: 1,
  },
  graph: null,
  savedGraph: null,
  settings: { reenrollment: 'NEVER', read_mode: 'CURRENT', unenroll_when_ineligible: false },
  savedSettings: { reenrollment: 'NEVER', read_mode: 'CURRENT', unenroll_when_ineligible: false },
  serverRevision: 0,
  validation: [],
  validationFresh: false,
  dirty: false,
  loading: true,
  saving: false,
  conflict: false,
}

const initialEditor: EditorState = {
  validationOpen: false,
  simulationOpen: false,
  publishOpen: false,
  runsOpen: false,
  policiesOpen: false,
  versionsOpen: false,
  mode: 'edit',
}

const initialHistory: HistoryState = { past: [], future: [] }

function withUnpublishedChanges(publication: WorkflowPublication): WorkflowPublication {
  return {
    ...publication,
    state: publication.has_published_version ? 'DRAFT_CHANGES' : 'NEVER_PUBLISHED',
    has_unpublished_changes: true,
    draft_matches_latest_version: false,
  }
}

export function hasExecutionChanges(
  graph: WorkflowGraph | null,
  savedGraph: WorkflowGraph | null,
  settings: WorkflowSettings,
  savedSettings: WorkflowSettings,
): boolean {
  return !graph
    || !savedGraph
    || !sameExecutionGraph(savedGraph, graph)
    || canonicalValue(savedSettings) !== canonicalValue(settings)
}

export function workflowDocumentReducer(state: DocumentState, action: DocumentAction): DocumentState {
  switch (action.type) {
    case 'LOAD_START':
      return { ...initialDocument, workflowId: action.workflowId, loading: true }
    case 'LOAD_SUCCESS':
      return {
        ...state,
        title: action.title,
        status: action.status,
        publication: action.publication,
        graph: action.graph,
        savedGraph: action.savedGraph || action.graph,
        settings: action.settings,
        savedSettings: action.savedSettings || action.settings,
        serverRevision: action.revision,
        validation: action.validation,
        validationFresh: false,
        validatedRevision: undefined,
        validatedGraphHash: undefined,
        dirty: Boolean(action.dirty),
        dirtySince: action.dirty ? Date.now() : undefined,
        loading: false,
        saving: false,
        conflict: false,
        error: undefined,
      }
    case 'LOAD_ERROR':
      return { ...state, loading: false, saving: false, error: action.error }
    case 'REPLACE_GRAPH':
      return {
        ...state,
        graph: action.graph,
        publication: action.affectsPublication === false ? state.publication : withUnpublishedChanges(state.publication),
        dirty: action.dirty ?? true,
        dirtySince: state.dirtySince ?? Date.now(),
        validation: action.affectsPublication === false ? state.validation : [],
        validationFresh: action.affectsPublication === false ? state.validationFresh : false,
        validatedRevision: action.affectsPublication === false ? state.validatedRevision : undefined,
        validatedGraphHash: action.affectsPublication === false ? state.validatedGraphHash : undefined,
        error: undefined,
      }
    case 'REPLACE_SETTINGS':
      return {
        ...state,
        settings: action.settings,
        publication: {
          ...state.publication,
          state: state.publication.has_published_version ? 'DRAFT_CHANGES' : 'NEVER_PUBLISHED',
          has_unpublished_changes: true,
          draft_matches_latest_version: false,
        },
        dirty: action.dirty ?? true,
        dirtySince: state.dirtySince ?? Date.now(),
        validation: [],
        validationFresh: false,
        validatedRevision: undefined,
        validatedGraphHash: undefined,
        error: undefined,
      }
    case 'SAVE_START':
      return { ...state, saving: true, error: undefined }
    case 'SAVE_SUCCESS': {
      const changedWhileSaving = state.graph !== action.graph || state.settings !== action.settings
      const executionChangedWhileSaving = Boolean(
        state.graph
        && (!sameExecutionGraph(state.graph, action.graph) || canonicalValue(state.settings) !== canonicalValue(action.settings)),
      )
      const preserveValidation = !action.affectsPublication && !executionChangedWhileSaving && state.validationFresh
      return {
        ...state,
        serverRevision: action.revision,
        validation: action.validation,
        publication: executionChangedWhileSaving ? withUnpublishedChanges(action.publication) : action.publication,
        validationFresh: preserveValidation,
        validatedRevision: preserveValidation ? action.revision : undefined,
        validatedGraphHash: preserveValidation ? action.graphHash : undefined,
        savedGraph: action.graph,
        savedSettings: action.settings,
        dirty: changedWhileSaving,
        dirtySince: changedWhileSaving ? state.dirtySince ?? Date.now() : undefined,
        saving: false,
        conflict: false,
      }
    }
    case 'SAVE_ERROR':
      return { ...state, saving: false, error: action.error, conflict: Boolean(action.conflict) }
    case 'SET_VALIDATION':
      return {
        ...state,
        validation: action.validation,
        validationFresh: action.revision === state.serverRevision,
        validatedRevision: action.revision,
        validatedGraphHash: action.graphHash,
      }
    case 'SET_STATUS':
      return { ...state, status: action.status }
  }
}

export function workflowEditorReducer(state: EditorState, action: EditorAction): EditorState {
  switch (action.type) {
    case 'SELECT':
      return { ...state, selectedNodeId: action.nodeId }
    case 'TOGGLE':
      return { ...state, [action.panel]: action.open ?? !state[action.panel] }
    case 'SIMULATION':
      return { ...state, simulation: action.result, simulationOpen: true }
    case 'VERSION_DIFF':
      return { ...state, versionDiff: action.diff }
    case 'CONFLICT':
      return { ...state, mode: 'conflict' }
    case 'RESOLVE_CONFLICT':
      return { ...state, mode: 'edit' }
  }
}

export function workflowHistoryReducer(state: HistoryState, action: HistoryAction): HistoryState {
  switch (action.type) {
    case 'RECORD': {
      if (state.lastKey === action.key && action.at - (state.lastAt || 0) < 750) {
        return { ...state, lastAt: action.at }
      }
      return {
        past: [...state.past.slice(-49), action.graph],
        future: [],
        lastKey: action.key,
        lastAt: action.at,
      }
    }
    case 'UNDO':
      return state.past.length
        ? { past: state.past.slice(0, -1), future: [action.current, ...state.future], lastKey: undefined }
        : state
    case 'REDO':
      return state.future.length
        ? { past: [...state.past, action.current].slice(-50), future: state.future.slice(1), lastKey: undefined }
        : state
    case 'RESET':
      return initialHistory
  }
}

interface WorkflowActions {
  reload(): Promise<void>
  save(): Promise<number>
  validate(): Promise<void>
  publish(activate?: boolean, reenrollment?: 'NEVER' | 'AFTER_COMPLETION' | 'ALWAYS'): Promise<void>
  simulate(recordName: string): Promise<void>
  testNode(recordName: string): Promise<void>
  setState(status: 'ACTIVE' | 'PAUSED' | 'DISABLED'): Promise<void>
  addNode(item: NodeCatalogItem): void
  replaceTrigger(item: NodeCatalogItem): void
  updateNode(nodeId: string, config: Record<string, unknown>, commandKey?: string): void
  updateNodeVersion(nodeId: string, typeVersion: 1 | 2): void
  updateSettings(settings: WorkflowSettings): void
  moveNode(nodeId: string, position: { x: number; y: number }): void
  removeNode(nodeId: string): void
  removeNodes(nodeIds: string[]): void
  connect(edge: Omit<WorkflowEdge, 'id'>): void
  removeEdge(edgeId: string): void
  removeEdges(edgeIds: string[]): void
  select(nodeId?: string): void
  toggle(panel: 'validationOpen' | 'simulationOpen' | 'publishOpen' | 'runsOpen' | 'policiesOpen' | 'versionsOpen', open?: boolean): void
  undo: () => void
  redo: () => void
  resolveConflict: (strategy: 'reload' | 'download') => Promise<void>
  setVersionDiff: (diff?: EditorState['versionDiff']) => void
}

const DocumentContext = createContext<DocumentState | null>(null)
const EditorContext = createContext<EditorState | null>(null)
const HistoryContext = createContext<HistoryState | null>(null)
const ActionsContext = createContext<WorkflowActions | null>(null)

const graphSchema = {
  type: 'object',
  required: ['schema_version', 'primary_doctype', 'start_node_id', 'nodes', 'edges'],
  properties: {
    schema_version: { const: 1 },
    primary_doctype: { type: 'string', minLength: 1 },
    start_node_id: { type: 'string', minLength: 1 },
    nodes: { type: 'array', maxItems: 250 },
    edges: { type: 'array', maxItems: 500 },
  },
} as const
const validateGraphShape = new Ajv({ allErrors: true }).compile(graphSchema)

function cloneGraph(graph: WorkflowGraph): WorkflowGraph {
  return structuredClone(graph)
}

export async function saveBeforeCheck<T>(saveDraft: () => Promise<number>, checkDraft: (revision: number) => Promise<T>): Promise<T | undefined> {
  const revision = await saveDraft()
  if (revision < 0) return undefined
  return checkDraft(revision)
}

export function WorkflowProvider({ workflowId, children }: { workflowId: string; children: ReactNode }) {
  const [document, documentDispatch] = useReducer(workflowDocumentReducer, { ...initialDocument, workflowId })
  const [editor, editorDispatch] = useReducer(workflowEditorReducer, initialEditor)
  const [history, historyDispatch] = useReducer(workflowHistoryReducer, initialHistory)
  const documentRef = useRef(document)
  const historyRef = useRef(history)
  const savePromiseRef = useRef<Promise<number> | null>(null)
  documentRef.current = document
  historyRef.current = history

  const recoveryKey = useCallback(
    (revision: number) => `automation-draft:${window.frappe?.boot?.site_name || location.host}:${workflowId}:${revision}`,
    [workflowId],
  )

  const load = useCallback(async () => {
    documentDispatch({ type: 'LOAD_START', workflowId })
    try {
      const response = await call<{
        workflow: { title: string; status: string }
        publication: WorkflowPublication
        draft: { draft_revision: number; graph: WorkflowGraph; settings: WorkflowSettings; validation: ValidationIssue[] }
      }>('get_draft', { workflow_id: workflowId })
      const serverGraph = response.draft.graph
      const serverSettings = response.draft.settings || {}
      let graph = serverGraph
      let settings = serverSettings
      let recovered = false
      const raw = localStorage.getItem(recoveryKey(response.draft.draft_revision))
      if (raw) {
        try {
          const value = JSON.parse(raw) as { graph: WorkflowGraph; settings?: WorkflowSettings }
          if (validateGraphShape(value.graph)) {
            graph = value.graph
            settings = value.settings || serverSettings
            recovered = true
          }
        } catch {
          localStorage.removeItem(recoveryKey(response.draft.draft_revision))
        }
      }
      // Use the recovered graph/settings directly in the initial load dispatch so
      // the canvas renders the correct state in a single pass, avoiding a visible
      // flash where the stale server version appears before the recovered version.
      documentDispatch({
        type: 'LOAD_SUCCESS',
        title: response.workflow.title,
        status: response.workflow.status,
        publication: recovered && (
          !sameExecutionGraph(serverGraph, graph)
          || canonicalValue(serverSettings) !== canonicalValue(settings)
        ) ? withUnpublishedChanges(response.publication) : response.publication,
        graph,
        savedGraph: serverGraph,
        settings,
        savedSettings: serverSettings,
        revision: response.draft.draft_revision,
        validation: response.draft.validation || [],
        dirty: recovered,
      })
      historyDispatch({ type: 'RESET' })
      editorDispatch({ type: 'RESOLVE_CONFLICT' })
    } catch (error) {
      documentDispatch({ type: 'LOAD_ERROR', error: error instanceof Error ? error.message : 'Unable to load workflow' })
    }
  }, [recoveryKey, workflowId])

  useEffect(() => {
    void load()
  }, [load])

  const save = useCallback(() => {
    if (savePromiseRef.current) return savePromiseRef.current
    const pending = (async () => {
      let revision = documentRef.current.serverRevision
      if (!documentRef.current.dirty) return revision
      while (true) {
        const current = documentRef.current
        if (!current.graph || current.conflict) return current.serverRevision
        if (!validateGraphShape(current.graph)) {
          documentDispatch({ type: 'SAVE_ERROR', error: 'The local graph is not serializable.' })
          return revision
        }
        documentDispatch({ type: 'SAVE_START' })
        try {
          const affectsPublication = hasExecutionChanges(
            current.graph,
            current.savedGraph,
            current.settings,
            current.savedSettings,
          )
          const result = await call<{ draft_revision: number; graph_hash: string; validation: ValidationIssue[]; publication: WorkflowPublication }>(
            'save_draft',
            mutationEnvelope(workflowId, { graph: current.graph, settings: current.settings }, revision),
            true,
          )
          localStorage.removeItem(recoveryKey(revision))
          revision = result.draft_revision
          documentDispatch({
            type: 'SAVE_SUCCESS',
            revision,
            graphHash: result.graph_hash,
            validation: result.validation || [],
            publication: result.publication,
            graph: current.graph,
            settings: current.settings,
            affectsPublication,
          })
          const latest = documentRef.current
          if (latest.graph === current.graph && latest.settings === current.settings) return revision
        } catch (error) {
          const conflict = error instanceof WorkflowApiError && error.status === 409
          documentDispatch({ type: 'SAVE_ERROR', error: error instanceof Error ? error.message : 'Save failed', conflict })
          if (conflict) editorDispatch({ type: 'CONFLICT' })
          return -1
        }
      }
    })()
    savePromiseRef.current = pending
    void pending.finally(() => {
      if (savePromiseRef.current === pending) savePromiseRef.current = null
    })
    return pending
  }, [recoveryKey, workflowId])

  useEffect(() => {
    if (!document.graph || !document.dirty || document.conflict) return
    localStorage.setItem(
      recoveryKey(document.serverRevision),
      JSON.stringify({ graph: document.graph, settings: document.settings, savedAt: new Date().toISOString() }),
    )
    const elapsed = Date.now() - (document.dirtySince || Date.now())
    const timer = window.setTimeout(() => void save(), Math.max(100, Math.min(2000, 30000 - elapsed)))
    return () => window.clearTimeout(timer)
  }, [document.conflict, document.dirty, document.dirtySince, document.graph, document.settings, document.serverRevision, recoveryKey, save])

  const mutate = useCallback((next: WorkflowGraph, key: string) => {
    const current = documentRef.current
    if (!current.graph || current.conflict) return
    historyDispatch({ type: 'RECORD', graph: cloneGraph(current.graph), key, at: Date.now() })
    documentDispatch({
      type: 'REPLACE_GRAPH',
      graph: next,
      affectsPublication: !sameExecutionGraph(current.graph, next),
    })
  }, [])

  const actions = useMemo<WorkflowActions>(() => ({
    reload: load,
    save,
    async validate() {
      try {
        const result = await saveBeforeCheck(save, (revision) => call<{ issues: ValidationIssue[]; graph_hash: string }>('validate_draft', { workflow_id: workflowId, draft_revision: revision, publish: 1 }, true).then((response) => ({ ...response, revision })))
        if (!result) return
        documentDispatch({ type: 'SET_VALIDATION', validation: result.issues || [], revision: result.revision, graphHash: result.graph_hash })
        editorDispatch({ type: 'TOGGLE', panel: 'validationOpen', open: true })
      } catch (error) {
        documentDispatch({ type: 'SAVE_ERROR', error: error instanceof Error ? error.message : 'Workflow check failed' })
      }
    },
    async publish(activate = true, reenrollment = 'NEVER') {
      const checked = documentRef.current
      const activationRetry = checked.publication.state === 'READY_TO_ACTIVATE'
      if (!checked.publication.has_unpublished_changes && !activationRetry) {
        documentDispatch({ type: 'SAVE_ERROR', error: `The draft already matches published version ${checked.publication.latest_version_no}.` })
        return
      }
      const revision = await save()
      if (revision < 0) return
      try {
		const validation = await call<{ issues: ValidationIssue[]; graph_hash: string }>(
		  'validate_draft',
		  { workflow_id: workflowId, draft_revision: revision, publish: 1 },
		  true,
		)
		documentDispatch({ type: 'SET_VALIDATION', validation: validation.issues || [], revision, graphHash: validation.graph_hash })
		if (validation.issues?.length) {
		  editorDispatch({ type: 'TOGGLE', panel: 'validationOpen', open: true })
		  documentDispatch({ type: 'SAVE_ERROR', error: 'Resolve the current workflow-check issues before publishing.' })
		  return
		}
        const result = await call<{ status: string }>(
          'publish',
          mutationEnvelope(workflowId, { activate, reenrollment }, revision),
          true,
        )
        documentDispatch({ type: 'SET_STATUS', status: result.status })
        editorDispatch({ type: 'TOGGLE', panel: 'publishOpen', open: false })
        await load()
      } catch (error) {
        documentDispatch({ type: 'SAVE_ERROR', error: error instanceof Error ? error.message : 'Publish failed' })
      }
    },
    async simulate(recordName: string) {
      const current = documentRef.current
      if (!current.graph) return
      try {
        const result = await call<SimulationResult>(
          'simulate',
          mutationEnvelope(workflowId, { record_name: recordName, graph: current.graph }, current.serverRevision),
          true,
        )
        editorDispatch({ type: 'SIMULATION', result })
      } catch (error) {
        documentDispatch({ type: 'SAVE_ERROR', error: error instanceof Error ? error.message : 'Simulation failed' })
      }
    },
    async testNode(recordName: string) {
      const current = documentRef.current
      const nodeId = editor.selectedNodeId
      if (!current.graph || !nodeId) return
      try {
        const result = await call<{ valid: boolean; issues: ValidationIssue[]; node?: SimulationResult['path'][number]; mutated: false }>(
          'test_node',
          mutationEnvelope(workflowId, { record_name: recordName, node_id: nodeId, graph: current.graph }, current.serverRevision),
          true,
        )
        editorDispatch({ type: 'SIMULATION', result: { ...result, path: result.node ? [result.node] : [] } })
      } catch (error) {
        documentDispatch({ type: 'SAVE_ERROR', error: error instanceof Error ? error.message : 'Selected-node test failed' })
      }
    },
    async setState(status) {
      try {
        const result = await call<{ status: string }>(
          'set_state',
          mutationEnvelope(workflowId, { status }),
          true,
        )
        documentDispatch({ type: 'SET_STATUS', status: result.status })
      } catch (error) {
        documentDispatch({ type: 'SAVE_ERROR', error: error instanceof Error ? error.message : 'State change failed' })
      }
    },
    addNode(item) {
      const current = documentRef.current.graph
      if (!current) return
      const id = crypto.randomUUID()
      mutate(
        {
          ...current,
          nodes: [
            ...current.nodes,
            {
              id,
              type: item.type,
              type_version: item.type_version || 1,
              position: { x: 360 + current.nodes.length * 36, y: 120 + current.nodes.length * 28 },
              config: structuredClone(item.default_config),
            },
          ],
        },
        'add-node',
      )
      editorDispatch({ type: 'SELECT', nodeId: id })
    },
    replaceTrigger(item) {
      const current = documentRef.current.graph
      if (!current) return
      const next = replaceWorkflowTrigger(current, item)
      if (next === current) {
        editorDispatch({ type: 'SELECT', nodeId: current.start_node_id })
        return
      }
      mutate(next, 'replace-trigger')
      editorDispatch({ type: 'SELECT', nodeId: current.start_node_id })
    },
    updateNode(nodeId, config, commandKey = `node:${nodeId}:config`) {
      const current = documentRef.current.graph
      if (!current) return
      mutate({ ...current, nodes: current.nodes.map((node) => node.id === nodeId ? { ...node, config } : node) }, commandKey)
    },
    updateNodeVersion(nodeId, typeVersion) {
      const current = documentRef.current.graph
      if (!current) return
      mutate({ ...current, nodes: current.nodes.map((node) => node.id === nodeId ? { ...node, type_version: typeVersion } : node) }, `node:${nodeId}:version`)
    },
    updateSettings(settings) {
      if (documentRef.current.conflict) return
      documentDispatch({ type: 'REPLACE_SETTINGS', settings })
    },
    moveNode(nodeId, position) {
      const current = documentRef.current.graph
      if (!current) return
      mutate({ ...current, nodes: current.nodes.map((node) => node.id === nodeId ? { ...node, position } : node) }, `move:${nodeId}`)
    },
    removeNode(nodeId) {
      const current = documentRef.current.graph
      if (!current || nodeId === current.start_node_id) return
      mutate(
        {
          ...current,
          nodes: current.nodes.filter((node) => node.id !== nodeId),
          edges: current.edges.filter((edge) => edge.source !== nodeId && edge.target !== nodeId),
        },
        'remove-node',
      )
      editorDispatch({ type: 'SELECT' })
    },
    removeNodes(nodeIds) {
      const current = documentRef.current.graph
      if (!current || !nodeIds.length) return
      const next = removeWorkflowNodes(current, nodeIds)
      if (next === current) return
      mutate(next, 'remove-nodes')
      editorDispatch({ type: 'SELECT' })
    },
    connect(edge) {
      const current = documentRef.current.graph
      if (!current || edge.source === edge.target) return
      if (current.edges.some((item) => item.source === edge.source && item.source_handle === edge.source_handle)) return
      mutate({ ...current, edges: [...current.edges, { ...edge, id: crypto.randomUUID() }] }, 'connect')
    },
    removeEdge(edgeId) {
      const current = documentRef.current.graph
      if (current) mutate({ ...current, edges: current.edges.filter((edge) => edge.id !== edgeId) }, 'remove-edge')
    },
    removeEdges(edgeIds) {
      const current = documentRef.current.graph
      if (!current || !edgeIds.length) return
      const removed = new Set(edgeIds)
      mutate({ ...current, edges: current.edges.filter((edge) => !removed.has(edge.id)) }, 'remove-edges')
    },
    select(nodeId) {
      editorDispatch({ type: 'SELECT', nodeId })
    },
    toggle(panel, open) {
      editorDispatch({ type: 'TOGGLE', panel, open })
    },
    undo() {
      const current = documentRef.current.graph
      const state = historyRef.current
      const target = state.past.at(-1)
      if (!current || !target) return
      historyDispatch({ type: 'UNDO', current: cloneGraph(current) })
      documentDispatch({
        type: 'REPLACE_GRAPH',
        graph: cloneGraph(target),
        affectsPublication: !sameExecutionGraph(current, target),
      })
    },
    redo() {
      const current = documentRef.current.graph
      const state = historyRef.current
      const target = state.future[0]
      if (!current || !target) return
      historyDispatch({ type: 'REDO', current: cloneGraph(current) })
      documentDispatch({
        type: 'REPLACE_GRAPH',
        graph: cloneGraph(target),
        affectsPublication: !sameExecutionGraph(current, target),
      })
    },
    setVersionDiff: (diff?: EditorState['versionDiff']) => {
      editorDispatch({ type: 'VERSION_DIFF', diff })
    },
    async resolveConflict(strategy) {
      const current = documentRef.current
      if (strategy === 'download' && current.graph) {
        const blob = new Blob([JSON.stringify({ graph: current.graph, settings: current.settings }, null, 2)], { type: 'application/json' })
        const link = window.document.createElement('a')
        link.href = URL.createObjectURL(blob)
        link.download = `${workflowId}-local-draft.json`
        link.click()
        URL.revokeObjectURL(link.href)
        return
      }
      localStorage.removeItem(recoveryKey(current.serverRevision))
      await load()
    },
  }), [editor.selectedNodeId, load, mutate, recoveryKey, save, workflowId])

  const documentValue = useMemo(() => document, [document])
  const editorValue = useMemo(() => editor, [editor])
  const historyValue = useMemo(() => history, [history])

  return (
    <DocumentContext.Provider value={documentValue}>
      <EditorContext.Provider value={editorValue}>
        <HistoryContext.Provider value={historyValue}>
          <ActionsContext.Provider value={actions}>{children}</ActionsContext.Provider>
        </HistoryContext.Provider>
      </EditorContext.Provider>
    </DocumentContext.Provider>
  )
}

function requiredContext<T>(value: T | null, name: string): T {
  if (!value) throw new Error(`${name} must be used inside WorkflowProvider`)
  return value
}

export const useWorkflowDocument = () => requiredContext(useContext(DocumentContext), 'useWorkflowDocument')
export const useWorkflowEditor = () => requiredContext(useContext(EditorContext), 'useWorkflowEditor')
export const useWorkflowHistory = () => requiredContext(useContext(HistoryContext), 'useWorkflowHistory')
export const useWorkflowActions = () => requiredContext(useContext(ActionsContext), 'useWorkflowActions')

export type { Dispatch, DocumentAction, EditorAction, HistoryAction, WorkflowNode }
