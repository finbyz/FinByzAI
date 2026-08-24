import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
	call: vi.fn().mockResolvedValue({ rows: [] }),
	toggle: vi.fn(),
	updateSettings: vi.fn(),
}))

vi.mock('../lib/api', async (importOriginal) => ({
	...await importOriginal<typeof import('../lib/api')>(),
	call: mocks.call,
}))

vi.mock('../state/WorkflowContext', () => ({
	useWorkflowActions: () => ({ toggle: mocks.toggle, updateSettings: mocks.updateSettings }),
	useWorkflowDocument: () => ({ workflowId: 'AWF-TEST', settings: { communication: {} } }),
	useWorkflowEditor: () => ({ selectedNodeId: undefined }),
	useWorkflowHistory: () => ({ past: [], future: [] }),
	WorkflowProvider: ({ children }: { children: ReactNode }) => children,
}))

import { CommunicationSettingsButton, EditorMoreMenu, EnrollmentFrequencyButton } from './WorkflowPages'

describe('EditorMoreMenu overlays', () => {
	it('portals the menu and opened utility drawer outside the scrolling editor toolbar', async () => {
		render(<EditorMoreMenu />)

		fireEvent.click(screen.getByRole('button', { name: /More/ }))
		const menu = screen.getByRole('menu')
		expect(menu.parentElement).toBe(document.body)
		expect(menu).toHaveAttribute('data-open', 'true')

		fireEvent.click(within(menu).getByRole('button', { name: /Connections/ }))
		const dialog = screen.getByRole('dialog', { name: 'Workflow connections' })
		expect(dialog.parentElement).toBe(document.body)
		expect(menu).toHaveAttribute('data-open', 'false')
		expect(within(dialog).getByRole('heading', { name: 'Connections' })).toBeInTheDocument()

		fireEvent.click(within(dialog).getByRole('button', { name: 'Close workflow connections' }))
		await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Workflow connections' })).not.toBeInTheDocument())
	})
})

describe('Editor header dialogs', () => {
	it('portals Communication settings to the document body so the sticky header cannot clip it', () => {
		render(<CommunicationSettingsButton />)

		fireEvent.click(screen.getByRole('button', { name: /Communication/ }))
		const dialog = screen.getByRole('dialog', { name: 'Senders and response handling' })
		expect(dialog.parentElement).toBe(document.body)
		expect(within(dialog).getByText('The configured provider must allow this sender identity.')).toBeInTheDocument()
	})

	it('lets a published workflow create a re-enrollment settings change without opening the publish dialog first', () => {
		render(<EnrollmentFrequencyButton />)

		fireEvent.click(screen.getByRole('button', { name: /Enrollment frequency/ }))
		const dialog = screen.getByRole('dialog', { name: 'Enrollment frequency' })
		fireEvent.click(within(dialog).getByRole('radio', { name: /Every matching event/ }))

		expect(mocks.updateSettings).toHaveBeenCalledWith({ communication: {}, reenrollment: 'ALWAYS' })
	})
})
