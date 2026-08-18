import { describe, expect, it, vi } from 'vitest'
import type { WorkflowGraph } from '../types'
import {
  workflowDocumentReducer,
  workflowEditorReducer,
  workflowHistoryReducer,
  hasExecutionChanges,
  saveBeforeCheck,
  type DocumentState,
} from './WorkflowContext'
import type { WorkflowPublication } from '../types'

function graph(nodeId = 'trigger-1'): WorkflowGraph {
  return {
    schema_version: 1,
    primary_doctype: 'Lead',
    start_node_id: nodeId,
    nodes: [{ id: nodeId, type: 'trigger.manual', type_version: 1, position: { x: 0, y: 0 }, config: {} }],
    edges: [],
  }
}

const published: WorkflowPublication = {
  state: 'PUBLISHED',
  has_published_version: true,
  has_unpublished_changes: false,
  draft_matches_latest_version: true,
  latest_version: 'AWV-00001',
  latest_version_no: 1,
  active_version: 'AWV-00001',
  active_version_no: 1,
  next_version_no: 2,
}

const initial: DocumentState = {
  workflowId: 'AWF-00001',
  title: 'Test',
  status: 'DRAFT',
  publication: published,
  graph: graph(),
  savedGraph: graph(),
  settings: { reenrollment: 'NEVER', read_mode: 'CURRENT', unenroll_when_ineligible: false },
  savedSettings: { reenrollment: 'NEVER', read_mode: 'CURRENT', unenroll_when_ineligible: false },
  serverRevision: 4,
  validation: [],
  validationFresh: false,
  dirty: false,
  loading: false,
  saving: false,
  conflict: false,
}

describe('workflow document reducer', () => {
  it('isolates graph edits and accepts an authoritative save revision', () => {
    const changed = graph()
    changed.nodes[0].config = { changed: true }
    const dirty = workflowDocumentReducer(initial, { type: 'REPLACE_GRAPH', graph: changed })
    expect(dirty.dirty).toBe(true)
    expect(dirty.serverRevision).toBe(4)

    const saved = workflowDocumentReducer(dirty, { type: 'SAVE_SUCCESS', revision: 5, graphHash: 'full-hash-5', validation: [], publication: { ...published, state: 'DRAFT_CHANGES', has_unpublished_changes: true, draft_matches_latest_version: false }, graph: changed, settings: dirty.settings, affectsPublication: true })
    expect(saved.dirty).toBe(false)
    expect(saved.serverRevision).toBe(5)
    expect(saved.savedGraph?.nodes[0].config).toEqual({ changed: true })
  })

  it('keeps edits made while a save request is in flight dirty', () => {
    const sent = graph()
    const newer = graph()
    newer.nodes[0].position = { x: 240, y: 160 }
    const saving = { ...initial, graph: newer, dirty: true, saving: true, dirtySince: 100 }

    const acknowledged = workflowDocumentReducer(saving, {
      type: 'SAVE_SUCCESS',
      revision: 5,
      graphHash: 'layout-hash-5',
      validation: [],
      publication: published,
      graph: sent,
      settings: initial.settings,
      affectsPublication: false,
    })

    expect(acknowledged.graph).toBe(newer)
    expect(acknowledged.savedGraph).toBe(sent)
    expect(acknowledged.dirty).toBe(true)
    expect(acknowledged.dirtySince).toBe(100)
    expect(acknowledged.publication.has_unpublished_changes).toBe(false)
  })

  it('marks a published workflow as needing a new version as soon as it is edited', () => {
    const changed = workflowDocumentReducer(initial, {
      type: 'REPLACE_GRAPH',
      graph: { ...graph(), nodes: [{ ...graph().nodes[0], config: { changed: true } }] },
      affectsPublication: true,
    })
    expect(changed.publication.state).toBe('DRAFT_CHANGES')
    expect(changed.publication.has_unpublished_changes).toBe(true)
    expect(changed.publication.next_version_no).toBe(2)
  })

  it('keeps publication and fresh validation intact for a position-only save', () => {
    const checked = workflowDocumentReducer(initial, {
      type: 'SET_VALIDATION',
      validation: [],
      revision: 4,
      graphHash: 'published-layout-hash',
    })
    const movedGraph = graph()
    movedGraph.nodes[0].position = { x: 400, y: 250 }
    const moved = workflowDocumentReducer(checked, {
      type: 'REPLACE_GRAPH',
      graph: movedGraph,
      affectsPublication: false,
    })
    expect(moved.publication.state).toBe('PUBLISHED')
    expect(moved.validationFresh).toBe(true)

    const saved = workflowDocumentReducer(moved, {
      type: 'SAVE_SUCCESS',
      revision: 5,
      graphHash: 'new-layout-hash',
      validation: [],
      publication: published,
      graph: movedGraph,
      settings: moved.settings,
      affectsPublication: false,
    })
    expect(saved.publication.has_unpublished_changes).toBe(false)
    expect(saved.validationFresh).toBe(true)
    expect(saved.validatedRevision).toBe(5)
    expect(saved.validatedGraphHash).toBe('new-layout-hash')
  })

  it('locks a stale draft after a 409-style save failure', () => {
    const failed = workflowDocumentReducer(initial, { type: 'SAVE_ERROR', error: 'Stale revision', conflict: true })
    expect(failed.conflict).toBe(true)
    expect(failed.error).toBe('Stale revision')
  })

  it('tracks policy settings independently from graph history', () => {
    const changed = workflowDocumentReducer(initial, {
      type: 'REPLACE_SETTINGS',
      settings: { ...initial.settings, read_mode: 'ENROLLMENT_SNAPSHOT', unenroll_when_ineligible: true },
    })
    expect(changed.dirty).toBe(true)
    expect(changed.graph).toBe(initial.graph)
    expect(changed.settings.read_mode).toBe('ENROLLMENT_SNAPSHOT')
  })

  it('terminates loading and retains a retryable load error', () => {
    const loading = { ...initial, graph: null, loading: true }
    const failed = workflowDocumentReducer(loading, { type: 'LOAD_ERROR', error: 'Metadata unavailable' })
    expect(failed.loading).toBe(false)
    expect(failed.graph).toBeNull()
    expect(failed.error).toBe('Metadata unavailable')
  })

  it('invalidates publish validation whenever the graph changes', () => {
    const checked = workflowDocumentReducer({ ...initial, serverRevision: 3 }, {
      type: 'SET_VALIDATION',
      validation: [],
      revision: 3,
      graphHash: 'checked-hash',
    })
    expect(checked.validationFresh).toBe(true)
    expect(checked.validatedRevision).toBe(3)

    const changed = workflowDocumentReducer(checked, {
      type: 'REPLACE_GRAPH',
      graph: { ...graph(), nodes: [...graph().nodes] },
    })
    expect(changed.validationFresh).toBe(false)
    expect(changed.validatedGraphHash).toBeUndefined()
  })
})

describe('separate editor and history reducers', () => {
  it('changes selection without changing document identity', () => {
    const editor = workflowEditorReducer(
      { validationOpen: false, simulationOpen: false, publishOpen: false, runsOpen: false, policiesOpen: false, versionsOpen: false, mode: 'edit' },
      { type: 'SELECT', nodeId: 'node-2' },
    )
    expect(editor.selectedNodeId).toBe('node-2')
    expect(initial.graph?.nodes).toHaveLength(1)
  })

  it('bounds history and coalesces repeated inspector commands', () => {
    let state = { past: [], future: [] } as ReturnType<typeof workflowHistoryReducer>
    state = workflowHistoryReducer(state, { type: 'RECORD', graph: graph(), key: 'node:config', at: 1000 })
    state = workflowHistoryReducer(state, { type: 'RECORD', graph: graph(), key: 'node:config', at: 1200 })
    expect(state.past).toHaveLength(1)
    state = workflowHistoryReducer(state, { type: 'RECORD', graph: graph(), key: 'node:move', at: 1300 })
    expect(state.past).toHaveLength(2)
    state = workflowHistoryReducer(state, { type: 'UNDO', current: graph() })
    expect(state.future).toHaveLength(1)
  })
})

describe('save-before-check contract', () => {
  it('checks only after save resolves and passes the exact authoritative revision', async () => {
    const order: string[] = []
    const save = vi.fn(async () => { order.push('save'); return 9 })
    const check = vi.fn(async (revision: number) => { order.push(`check:${revision}`); return { issues: [] } })
    await expect(saveBeforeCheck(save, check)).resolves.toEqual({ issues: [] })
    expect(order).toEqual(['save', 'check:9'])
  })

  it('does not check after a failed save', async () => {
    const check = vi.fn()
    await expect(saveBeforeCheck(async () => -1, check)).resolves.toBeUndefined()
    expect(check).not.toHaveBeenCalled()
  })
})

describe('execution-change detection', () => {
  it('allows a checked revision to survive layout-only autosave but not runtime or policy edits', () => {
    const saved = graph()
    const moved = structuredClone(saved)
    moved.nodes[0].position = { x: 700, y: 300 }
    expect(hasExecutionChanges(moved, saved, initial.settings, initial.savedSettings)).toBe(false)

    moved.nodes[0].config = { changed: true }
    expect(hasExecutionChanges(moved, saved, initial.settings, initial.savedSettings)).toBe(true)
    expect(hasExecutionChanges(saved, saved, { ...initial.settings, read_mode: 'ENROLLMENT_SNAPSHOT' }, initial.savedSettings)).toBe(true)
  })
})
