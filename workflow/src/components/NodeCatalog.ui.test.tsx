import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  call: vi.fn(),
  actions: { addNode: vi.fn(), cancelInsert: vi.fn(), replaceTrigger: vi.fn(), select: vi.fn() },
	comparison: { insertion: undefined as undefined | { edgeId: string; position: { x: number; y: number }; label?: string } },
}))

vi.mock('../lib/api', () => ({ call: mocks.call }))
vi.mock('../state/WorkflowContext', () => ({
  useWorkflowActions: () => mocks.actions,
  useWorkflowEditor: () => mocks.comparison,
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
	{ type: 'action.delete_record', label: 'Delete record', category: 'Actions', description: 'End this path by deleting the record.' },
  ],
}

describe('NodeCatalog UI states', () => {
  beforeEach(() => {
	vi.clearAllMocks()
	mocks.comparison.insertion = undefined
  })

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

	it('explains guided placement and prevents terminal actions from splitting an edge', async () => {
	  mocks.comparison.insertion = { edgeId: 'edge-1', position: { x: 100, y: 200 }, label: 'Between these steps' }
	  mocks.call.mockResolvedValue(catalog)
	  render(<NodeCatalog />)

	  expect(screen.getByText('Choose a step')).toBeInTheDocument()
	  expect(screen.getByText('It will be connected automatically')).toBeInTheDocument()
	  expect(screen.getByText('Between these steps')).toBeInTheDocument()
	  expect(await screen.findByRole('button', { name: /Delete record/ })).toBeDisabled()

	  fireEvent.click(screen.getByRole('button', { name: /Notify user/ }))
	  expect(mocks.actions.addNode).toHaveBeenCalledWith(expect.objectContaining({ type: 'action.notify_user' }))
	})
})
