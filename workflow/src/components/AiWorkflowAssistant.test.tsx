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
    localStorage.clear()
    mocks.call.mockImplementation((method: string) => {
      if (method === 'get_ai_workflow_authoring_status') return Promise.resolve({ available: true, max_prompt_characters: 6000, primary_doctype: 'Lead', suggestions: ['When a Lead is created, add a task.'] })
      if (method === 'get_ai_workflow_chat') return Promise.resolve({ turns: [] })
      if (method === 'accept_ai_workflow_proposal') return Promise.resolve({ recorded: true })
      if (method === 'converse_ai_workflow_draft') return Promise.resolve({ reply_type: 'proposal', summary: 'Adds a follow-up comment.', assumptions: [], warnings: [], graph: proposedGraph, issues: [], graph_hash: 'hash', node_count: 2, latency_ms: 1200, model: 'Authoring Model', usage: { input_tokens: 100, output_tokens: 50, total_tokens: 150 }, mutated: false, published: false })
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

  it('displays the graph diff badges showing added steps', async () => {
    render(<AiWorkflowAssistant />)
    fireEvent.click(screen.getByRole('button', { name: /Build with AI/ }))
    const input = await screen.findByRole('textbox')
    fireEvent.change(input, { target: { value: 'When manually enrolled, add a follow-up comment.' } })
    fireEvent.click(screen.getByRole('button', { name: 'Generate workflow draft' }))

    expect(await screen.findByText('Adds a follow-up comment.')).toBeInTheDocument()
    expect(await screen.findByText('+1 added')).toBeInTheDocument()
  })

  it('supports multi-turn conversation refinements', async () => {
    render(<AiWorkflowAssistant />)
    fireEvent.click(screen.getByRole('button', { name: /Build with AI/ }))
    const input = await screen.findByRole('textbox')
    
    // Turn 1
    fireEvent.change(input, { target: { value: 'When manually enrolled, add a follow-up comment.' } })
    fireEvent.click(screen.getByRole('button', { name: 'Generate workflow draft' }))
    expect(await screen.findByText('Adds a follow-up comment.')).toBeInTheDocument()

    // Turn 2 mock
    mocks.call.mockImplementation((method: string) => {
      if (method === 'get_ai_workflow_authoring_status') {
        return Promise.resolve({ available: true, max_prompt_characters: 6000, primary_doctype: 'Lead', suggestions: ['When a Lead is created, add a task.'] })
      }
      if (method === 'get_ai_workflow_chat') return Promise.resolve({ turns: [] })
      if (method === 'converse_ai_workflow_draft') {
        return Promise.resolve({
          reply_type: 'proposal',
          summary: 'Added 2 day delay before comment.',
          assumptions: [],
          warnings: [],
          graph: {
            ...proposedGraph,
            nodes: [
              ...proposedGraph.nodes,
              { id: 'delay', type: 'delay.fixed', type_version: 1, position: { x: 0, y: 100 }, config: { seconds: 172800 } },
            ],
          },
          issues: [],
          graph_hash: 'hash2',
          node_count: 3,
          latency_ms: 1100,
          model: 'Authoring Model',
          usage: { input_tokens: 120, output_tokens: 60, total_tokens: 180 },
          mutated: false,
          published: false,
        })
      }
      return Promise.reject(new Error('Unexpected'))
    })

    fireEvent.change(input, { target: { value: 'Now add a 2 day delay before the comment.' } })
    fireEvent.click(screen.getByRole('button', { name: 'Generate workflow draft' }))

    expect(await screen.findByText('Added 2 day delay before comment.')).toBeInTheDocument()
    // Both user prompts should remain in the chat stream
    expect(screen.getByText('When manually enrolled, add a follow-up comment.')).toBeInTheDocument()
    expect(screen.getByText('Now add a 2 day delay before the comment.')).toBeInTheDocument()
  })

  it('resumes the saved conversation for this user and workflow, with no reset button', async () => {
    // A stale local cache must not win over the server thread.
    localStorage.setItem('finbyz:workflow_ai_turns:AWF-1', JSON.stringify([
      { id: 'old', role: 'user', content: 'stale local turn', timestamp: 1 },
    ]))
    mocks.call.mockImplementation((method: string) => {
      if (method === 'get_ai_workflow_authoring_status') return Promise.resolve({ available: true, max_prompt_characters: 6000, primary_doctype: 'Lead', suggestions: [] })
      if (method === 'get_ai_workflow_chat') return Promise.resolve({ turns: [
        { role: 'user', text: 'notify the owner on new leads' },
        { role: 'assistant', text: 'Here is the plan. Shall I build this?', reply_type: 'question', questions: ['Yes, build it'] },
      ] })
      return Promise.reject(new Error('Unexpected method'))
    })

    render(<AiWorkflowAssistant />)
    fireEvent.click(screen.getByRole('button', { name: /Build with AI/ }))

    expect(await screen.findByText('notify the owner on new leads')).toBeInTheDocument()
    expect(screen.getByText('Here is the plan. Shall I build this?')).toBeInTheDocument()
    expect(screen.queryByText('stale local turn')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Reset conversation/i })).not.toBeInTheDocument()
  })

  async function askTwoQuestions() {
    mocks.call.mockImplementation((method: string) => {
      if (method === 'get_ai_workflow_authoring_status') return Promise.resolve({ available: true, max_prompt_characters: 6000, primary_doctype: 'Lead', suggestions: ['When a Lead is created, add a task.'] })
      if (method === 'get_ai_workflow_chat') return Promise.resolve({ turns: [] })
      if (method === 'converse_ai_workflow_draft') return Promise.resolve({ reply_type: 'question', message: 'What should trigger this?', questions: ['Which event starts it?', 'Who gets notified?'] })
      return Promise.reject(new Error('Unexpected method'))
    })
    render(<AiWorkflowAssistant />)
    fireEvent.click(screen.getByRole('button', { name: /Build with AI/ }))
    const input = await screen.findByRole('textbox')
    fireEvent.change(input, { target: { value: 'Set up some automation for leads.' } })
    fireEvent.click(screen.getByRole('button', { name: 'Generate workflow draft' }))
    expect(await screen.findByText('What should trigger this?')).toBeInTheDocument()
    return screen.getAllByPlaceholderText('Your answer…') as HTMLInputElement[]
  }

  it('answers questions inline, and a second answer never clears the first', async () => {
    const boxes = await askTwoQuestions()
    expect(boxes).toHaveLength(2)

    fireEvent.change(boxes[0], { target: { value: 'When a Lead is created' } })
    fireEvent.change(boxes[1], { target: { value: 'the account owner' } })

    // The regression: filling the second box must not wipe the first.
    expect(boxes[0].value).toBe('When a Lead is created')
    expect(boxes[1].value).toBe('the account owner')

    fireEvent.click(screen.getByRole('button', { name: /Send answers/i }))
    await waitFor(() => {
      const sent = mocks.call.mock.calls.filter((c) => c[0] === 'converse_ai_workflow_draft')
      const last = sent[sent.length - 1][1] as { payload: { message: string } }
      expect(last.payload.message).toContain('When a Lead is created')
      expect(last.payload.message).toContain('the account owner')
    })
    expect(mocks.replaceGraph).not.toHaveBeenCalled()
  })

  it('keeps the typed answers visible after sending them', async () => {
    const boxes = await askTwoQuestions()
    fireEvent.change(boxes[0], { target: { value: 'When a Lead is created' } })
    fireEvent.change(boxes[1], { target: { value: 'the account owner' } })
    fireEvent.click(screen.getByRole('button', { name: /Send answers/i }))

    // The answers must still read back on the question turn, not blank out.
    expect(await screen.findByText('When a Lead is created')).toBeInTheDocument()
    expect(screen.getByText('the account owner')).toBeInTheDocument()
    // The answered turn's own inputs are replaced by the values; only the new
    // question turn still offers empty boxes (2, not 4).
    expect(screen.getAllByPlaceholderText('Your answer…')).toHaveLength(2)
  })

  it('renders **bold** markdown from the agent without showing asterisks', async () => {
    mocks.call.mockImplementation((method: string) => {
      if (method === 'get_ai_workflow_authoring_status') return Promise.resolve({ available: true, max_prompt_characters: 6000, primary_doctype: 'Lead', suggestions: [] })
      if (method === 'get_ai_workflow_chat') return Promise.resolve({ turns: [] })
      if (method === 'converse_ai_workflow_draft') return Promise.resolve({ reply_type: 'question', message: 'Plan: 1. **Trigger**: a new Lead. 2. **Action**: add a task.', questions: ['Yes, build it', 'Change something'] })
      return Promise.reject(new Error('Unexpected method'))
    })
    render(<AiWorkflowAssistant />)
    fireEvent.click(screen.getByRole('button', { name: /Build with AI/ }))
    const input = await screen.findByRole('textbox')
    fireEvent.change(input, { target: { value: 'Add a task for new leads.' } })
    fireEvent.click(screen.getByRole('button', { name: 'Generate workflow draft' }))

    expect(await screen.findByText('Trigger')).toBeInTheDocument()
    expect(screen.getByText('Trigger').tagName).toBe('B')
    expect(screen.queryByText(/\*\*Trigger\*\*/)).not.toBeInTheDocument()
  })

  it('sends a single answer without the question prefix', async () => {
    mocks.call.mockImplementation((method: string) => {
      if (method === 'get_ai_workflow_authoring_status') return Promise.resolve({ available: true, max_prompt_characters: 6000, primary_doctype: 'Lead', suggestions: [] })
      if (method === 'get_ai_workflow_chat') return Promise.resolve({ turns: [] })
      if (method === 'converse_ai_workflow_draft') return Promise.resolve({ reply_type: 'question', message: 'One thing:', questions: ['Who should the task go to?'] })
      return Promise.reject(new Error('Unexpected method'))
    })
    render(<AiWorkflowAssistant />)
    fireEvent.click(screen.getByRole('button', { name: /Build with AI/ }))
    const input = await screen.findByRole('textbox')
    fireEvent.change(input, { target: { value: 'Create a task for new leads.' } })
    fireEvent.click(screen.getByRole('button', { name: 'Generate workflow draft' }))

    const box = await screen.findByPlaceholderText('Your answer…')
    fireEvent.change(box, { target: { value: 'aman@example.com' } })
    fireEvent.click(screen.getByRole('button', { name: /Send answer/i }))
    await waitFor(() => {
      const sent = mocks.call.mock.calls.filter((c) => c[0] === 'converse_ai_workflow_draft')
      const last = sent[sent.length - 1][1] as { payload: { message: string } }
      expect(last.payload.message).toBe('aman@example.com')
    })
  })

  it('renders short options as one-click choices, not answer boxes', async () => {
    mocks.call.mockImplementation((method: string) => {
      if (method === 'get_ai_workflow_authoring_status') return Promise.resolve({ available: true, max_prompt_characters: 6000, primary_doctype: 'Lead', suggestions: [] })
      if (method === 'get_ai_workflow_chat') return Promise.resolve({ turns: [] })
      if (method === 'converse_ai_workflow_draft') return Promise.resolve({ reply_type: 'question', message: 'Here is the plan.', questions: ['Yes, build it', 'Change something'] })
      return Promise.reject(new Error('Unexpected method'))
    })
    render(<AiWorkflowAssistant />)
    fireEvent.click(screen.getByRole('button', { name: /Build with AI/ }))
    const input = await screen.findByRole('textbox')
    fireEvent.change(input, { target: { value: 'Create a task for new leads.' } })
    fireEvent.click(screen.getByRole('button', { name: 'Generate workflow draft' }))

    const choice = await screen.findByRole('button', { name: 'Yes, build it' })
    expect(screen.queryByPlaceholderText('Your answer…')).not.toBeInTheDocument()
    fireEvent.click(choice)
    await waitFor(() => {
      const sent = mocks.call.mock.calls.filter((c) => c[0] === 'converse_ai_workflow_draft')
      const last = sent[sent.length - 1][1] as { payload: { message: string } }
      expect(last.payload.message).toBe('Yes, build it')
    })
  })
})
