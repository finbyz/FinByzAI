import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { WorkflowGraph, WorkflowPublication } from '../types'

const { callMock } = vi.hoisted(() => ({ callMock: vi.fn() }))

vi.mock('../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/api')>()
  return { ...actual, call: callMock }
})

import { useWorkflowDocument, WorkflowProvider } from './WorkflowContext'

const graph: WorkflowGraph = {
  schema_version: 1,
  primary_doctype: 'Lead',
  start_node_id: 'trigger',
  nodes: [{ id: 'trigger', type: 'trigger.manual', type_version: 1, position: { x: 0, y: 0 }, config: {} }],
  edges: [],
}

const publication: WorkflowPublication = {
  state: 'NEVER_PUBLISHED',
  has_published_version: false,
  has_unpublished_changes: true,
  draft_matches_latest_version: false,
  latest_version_no: 0,
  next_version_no: 1,
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}

function response(title: string) {
  return {
    workflow: { title, status: 'DRAFT' },
    publication,
    draft: { draft_revision: 0, graph, settings: {}, validation: [] },
  }
}

function Probe() {
  const document = useWorkflowDocument()
  return <span>{document.workflowId}:{document.title}</span>
}

describe('WorkflowProvider loading', () => {
  beforeEach(() => {
    callMock.mockReset()
    localStorage.clear()
  })

  it('ignores an older workflow response that finishes after navigation', async () => {
    const first = deferred<ReturnType<typeof response>>()
    const second = deferred<ReturnType<typeof response>>()
    callMock.mockImplementation((_method: string, args: { workflow_id: string }) => (
      args.workflow_id === 'AWF-OLD' ? first.promise : second.promise
    ))

    const { rerender } = render(<WorkflowProvider workflowId="AWF-OLD"><Probe /></WorkflowProvider>)
    rerender(<WorkflowProvider workflowId="AWF-NEW"><Probe /></WorkflowProvider>)

    second.resolve(response('New workflow'))
    expect(await screen.findByText('AWF-NEW:New workflow')).toBeInTheDocument()

    first.resolve(response('Old workflow'))
    await Promise.resolve()
    expect(screen.getByText('AWF-NEW:New workflow')).toBeInTheDocument()
    expect(screen.queryByText('AWF-NEW:Old workflow')).not.toBeInTheDocument()
  })
})
