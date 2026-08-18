import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  call: vi.fn(),
  actions: { addNode: vi.fn(), replaceTrigger: vi.fn(), select: vi.fn() },
}))

vi.mock('../lib/api', () => ({ call: mocks.call }))
vi.mock('../state/WorkflowContext', () => ({
  useWorkflowActions: () => mocks.actions,
  useWorkflowDocument: () => ({
    graph: {
      start_node_id: 'trigger',
      nodes: [{ id: 'trigger', type: 'trigger.manual', type_version: 1, position: { x: 0, y: 0 }, config: {} }],
    },
  }),
}))

import { NodeCatalog } from './NodeCatalog'

const catalog = {
  node_types: [
    { type: 'trigger.manual', label: 'Manual enrollment', category: 'Triggers', description: 'Enroll records manually.' },
    { type: 'action.notify_user', label: 'Notify user', category: 'Actions', description: 'Create an internal notification.' },
  ],
}

describe('NodeCatalog UI states', () => {
  beforeEach(() => vi.clearAllMocks())

  it('shows a real loading state instead of a false empty result', () => {
    mocks.call.mockReturnValue(new Promise(() => undefined))
    render(<NodeCatalog />)

    expect(screen.getByRole('status')).toHaveTextContent('Loading workflow steps')
    expect(screen.queryByText('No steps found')).not.toBeInTheDocument()
  })

  it('offers retry after catalog loading fails', async () => {
    mocks.call.mockRejectedValueOnce(new Error('Catalog unavailable')).mockResolvedValueOnce(catalog)
    render(<NodeCatalog />)

    expect(await screen.findByRole('alert')).toHaveTextContent('Catalog unavailable')
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))

    await waitFor(() => expect(screen.getByText('Notify user')).toBeInTheDocument())
    expect(mocks.call).toHaveBeenCalledTimes(2)
  })

  it('hides the closed mobile drawer from focus and supports scrim and Escape closing', async () => {
    mocks.call.mockResolvedValue(catalog)
    render(<NodeCatalog />)
    const catalogPanel = screen.getByLabelText('Workflow step catalog')
    expect(catalogPanel).toHaveClass('max-lg:invisible')

    fireEvent.click(screen.getByRole('button', { name: 'Open catalog' }))
    expect(catalogPanel).toHaveClass('max-lg:visible')
    expect(screen.getByRole('button', { name: 'Close step catalog' })).toBeInTheDocument()

    fireEvent.keyDown(document, { key: 'Escape' })
    await waitFor(() => expect(catalogPanel).toHaveClass('max-lg:invisible'))
  })
})
