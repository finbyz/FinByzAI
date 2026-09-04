import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  call: vi.fn(),
  replaceGraph: vi.fn(),
}))

vi.mock('../lib/api', () => ({
  call: mocks.call,
  mutationEnvelope: (workflowId: string, payload: unknown) => ({ workflow_id: workflowId, payload }),
}))
vi.mock('../state/WorkflowContext', () => ({
  useWorkflowDocument: () => ({
    workflowId: 'AWF-1',
    graph: {
      schema_version: 1,
      primary_doctype: 'Lead',
      start_node_id: 'start',
      nodes: [{ id: 'start', type: 'trigger.manual', type_version: 1, position: { x: 0, y: 0 }, config: {} }],
      edges: [],
    },
  }),
  useWorkflowActions: () => ({ replaceGraph: mocks.replaceGraph }),
}))

import { AiWorkflowAssistant } from './AiWorkflowAssistant'

const proposedGraph = {
  schema_version: 1 as const,
  primary_doctype: 'Lead',
  start_node_id: 'start',
  nodes: [
    { id: 'start', type: 'trigger.manual' as const, type_version: 1 as const, position: { x: 0, y: 0 }, config: {} },
    { id: 'comment', type: 'action.add_comment' as const, type_version: 1 as const, position: { x: 0, y: 200 }, config: { content: 'Follow up' } },
  ],
  edges: [{ id: 'edge-1', source: 'start', source_handle: 'default', target: 'comment' }],
}

describe('AI workflow assistant', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.call.mockImplementation((method: string) => {
      if (method === 'get_ai_workflow_authoring_status') return Promise.resolve({ available: true, max_prompt_characters: 6000, primary_doctype: 'Lead', suggestions: ['When a Lead is created, add a task.'] })
      if (method === 'generate_ai_workflow_draft') return Promise.resolve({ summary: 'Adds a follow-up comment.', assumptions: [], warnings: [], graph: proposedGraph, issues: [], graph_hash: 'hash', node_count: 2, latency_ms: 1200, model: 'Authoring Model', usage: { input_tokens: 100, output_tokens: 50, total_tokens: 150 }, mutated: false, published: false })
      return Promise.reject(new Error('Unexpected method'))
    })
  })

  it('generates a preview and applies it only after explicit review', async () => {
    render(<AiWorkflowAssistant />)
    fireEvent.click(screen.getByRole('button', { name: /Build with AI/ }))
    const input = await screen.findByRole('textbox')
    fireEvent.change(input, { target: { value: 'When manually enrolled, add a follow-up comment.' } })
    fireEvent.click(screen.getByRole('button', { name: 'Generate workflow draft' }))

    expect(await screen.findByText('Adds a follow-up comment.')).toBeInTheDocument()
    expect(mocks.replaceGraph).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: /Apply draft to canvas/ }))
    expect(mocks.replaceGraph).toHaveBeenCalledWith(proposedGraph, 'ai-generated-draft')
    expect(screen.getByText(/Undo restores the previous graph/)).toBeInTheDocument()
  })

  it('shows fail-closed setup guidance when authoring is disabled', async () => {
    mocks.call.mockResolvedValueOnce({ available: false, reason: 'Disabled by administrator', max_prompt_characters: 6000, suggestions: [] })
    render(<AiWorkflowAssistant />)
    fireEvent.click(screen.getByRole('button', { name: /Build with AI/ }))
    await waitFor(() => expect(screen.getByText('Disabled by administrator')).toBeInTheDocument())
    expect(screen.queryByRole('button', { name: 'Generate workflow draft' })).not.toBeInTheDocument()
  })

  it('uses the DocType-specific suggestion as the prompt example', async () => {
    mocks.call.mockResolvedValueOnce({ available: true, max_prompt_characters: 6000, primary_doctype: 'Lead', suggestions: ['When a new Lead is created, create a follow-up ToDo.'] })
    render(<AiWorkflowAssistant />)
    fireEvent.click(screen.getByRole('button', { name: /Build with AI/ }))
    expect(await screen.findByPlaceholderText('Example: When a new Lead is created, create a follow-up ToDo.')).toBeInTheDocument()
  })
})
