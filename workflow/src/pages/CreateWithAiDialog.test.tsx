import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  call: vi.fn(),
  searchDoctypes: vi.fn(),
}))

vi.mock('../lib/api', async (importOriginal) => ({
  ...await importOriginal<typeof import('../lib/api')>(),
  call: mocks.call,
  searchDoctypes: mocks.searchDoctypes,
}))

import { CreateWithAiDialog } from './WorkflowPages'

describe('CreateWithAiDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.sessionStorage.clear()
    mocks.call.mockResolvedValue({ workflow: 'AWF-NEW-AI-1' })
    mocks.searchDoctypes.mockResolvedValue([
      { name: 'Lead', label: 'Lead', module: 'CRM' },
      { name: 'Sales Invoice', label: 'Sales Invoice', module: 'Accounts' },
    ])
  })

  it('renders the AI workflow studio creation modal', () => {
    render(<CreateWithAiDialog close={vi.fn()} created={vi.fn()} />)
    expect(screen.getByRole('heading', { name: 'New with AI' })).toBeInTheDocument()
    expect(screen.getByText('AI Workflow Studio')).toBeInTheDocument()
    expect(screen.getByText('✨ Lead SLA & Routing')).toBeInTheDocument()
  })

  it('pre-fills the prompt and doctype when clicking a starter idea chip', () => {
    render(<CreateWithAiDialog close={vi.fn()} created={vi.fn()} />)
    const starterChip = screen.getByText('✨ Lead SLA & Routing')
    fireEvent.click(starterChip)

    const textarea = screen.getByPlaceholderText(/When a Sales Invoice is overdue/i)
    expect((textarea as HTMLTextAreaElement).value).toContain('Lead is created with high priority')
  })

  it('creates the workflow, stores prompt in sessionStorage, and calls created callback', async () => {
    const created = vi.fn()
    const close = vi.fn()
    render(<CreateWithAiDialog close={close} created={created} />)

    // Click starter chip to populate
    fireEvent.click(screen.getByText('✨ Lead SLA & Routing'))

    // Submit form
    const submitBtn = screen.getByRole('button', { name: /Create with AI/i })
    fireEvent.click(submitBtn)

    await waitFor(() => expect(created).toHaveBeenCalledWith('AWF-NEW-AI-1'))
    expect(mocks.call).toHaveBeenCalledWith(
      'create_workflow',
      expect.objectContaining({
        envelope: expect.objectContaining({
          payload: expect.objectContaining({
            primary_doctype: 'Lead',
            title: 'Lead SLA & Escalation',
            trigger_type: 'trigger.any',
          }),
        }),
      }),
      true
    )

    // Check sessionStorage
    expect(window.sessionStorage.getItem('finbyz:ai_initial_prompt:AWF-NEW-AI-1')).toContain('Lead is created with high priority')
  })
})
