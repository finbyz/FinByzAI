import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ call: vi.fn() }))

vi.mock('../lib/api', async (importOriginal) => ({
	...await importOriginal<typeof import('../lib/api')>(),
	call: mocks.call,
}))

import { DeleteWorkflowDialog } from './WorkflowPages'

describe('DeleteWorkflowDialog', () => {
	beforeEach(() => {
		mocks.call.mockReset().mockResolvedValue({ deleted: true })
		window.frappe = { boot: { roles: ['System Manager'], user: 'Administrator' } } as never
	})

	it('requires the workflow id and explicitly requests history deletion for a System Manager', async () => {
		const deleted = vi.fn()
		render(<DeleteWorkflowDialog workflow={{ name: 'AWF-9', title: 'Published flow', status: 'DISABLED', latest_version: 3, primary_doctype: 'Lead' } as never} close={vi.fn()} deleted={deleted} />)

		expect(screen.getByRole('heading', { name: 'Permanently delete workflow?' })).toBeInTheDocument()
		const button = screen.getByRole('button', { name: 'Delete permanently' })
		expect(button).toBeDisabled()
		fireEvent.change(screen.getByRole('textbox'), { target: { value: 'AWF-9' } })
		fireEvent.click(button)

		await waitFor(() => expect(deleted).toHaveBeenCalled())
		expect(mocks.call).toHaveBeenCalledWith(
			'delete_workflow',
			expect.objectContaining({ envelope: expect.objectContaining({ workflow_id: 'AWF-9', payload: { delete_history: 1 } }) }),
			true,
		)
	})
})
