import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { MoveWorkflowDialog } from './WorkflowPages'

describe('MoveWorkflowDialog', () => {
	it('uses the themed dialog and submits a normalized folder without a browser prompt', async () => {
		const moved = vi.fn().mockResolvedValue(undefined)
		const close = vi.fn()
		const prompt = vi.spyOn(window, 'prompt')

		render(<MoveWorkflowDialog workflow={{ name: 'AWF-1', title: 'Lead nurture', folder: 'Sales', status: 'DRAFT', primary_doctype: 'Lead' } as never} close={close} moved={moved} />)

		const dialog = screen.getByRole('dialog', { name: 'Move workflow' })
		expect(dialog).toBeInTheDocument()
		fireEvent.change(screen.getByRole('textbox', { name: 'Folder' }), { target: { value: '  Marketing / Nurture  ' } })
		fireEvent.click(screen.getByRole('button', { name: 'Move' }))

		await waitFor(() => expect(moved).toHaveBeenCalledWith('Marketing / Nurture'))
		expect(prompt).not.toHaveBeenCalled()
	})
})
