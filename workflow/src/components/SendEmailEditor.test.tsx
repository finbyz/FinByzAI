import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
	call: vi.fn(),
	searchLink: vi.fn().mockResolvedValue([]),
}))

vi.mock('../lib/api', () => ({
	call: mocks.call,
	fetchFieldCatalog: vi.fn(),
	invalidateMetadataCaches: vi.fn(),
	searchDoctypes: vi.fn(),
	searchLink: mocks.searchLink,
}))

import { SendEmailEditor } from './Inspector'

describe('SendEmailEditor', () => {
	beforeEach(() => {
		vi.clearAllMocks()
		window.frappe = {
			boot: {
				user: 'designer@example.com',
				roles: ['Email Designer'],
			},
		}
		mocks.call.mockImplementation((method: string) => {
			if (method === 'get_workflow_email_template') {
				return Promise.resolve({
					name: 'Lead welcome',
					subject: 'Welcome {{ lead_name }}',
					mode: 'Visual',
					preheader: 'A short welcome',
					builder_route: '/builder?template=Lead%20welcome',
					desk_route: '/app/email-template/Lead%20welcome',
				})
			}
			if (method === 'preview_workflow_email') {
				return Promise.resolve({
					subject: 'Welcome Ada',
					html: '<html><body>Welcome Ada</body></html>',
					preheader: 'A short welcome',
					email_template: 'Lead welcome',
					bytes: 42,
				})
			}
			if (method === 'send_workflow_test_email') {
				return Promise.resolve({ recipient: 'designer@example.com', email_queue: 'EMAIL-QUEUE-1' })
			}
			if (method === 'list_email_templates') return Promise.resolve({ rows: [] })
			throw new Error(`Unexpected API call: ${method}`)
		})
	})

	it('uses the shared visual template and supports preview and controlled test sending', async () => {
		const update = vi.fn()
		render(
			<SendEmailEditor
				config={{
					content_mode: 'template',
					email_template: 'Lead welcome',
					recipient: { kind: 'record_field', field: 'email_id' },
				}}
				workflowId="AUTO-WORKFLOW-1"
				primaryDoctype="Lead"
				update={update}
				recipientEditor={<div>Recipient binding</div>}
				subjectOverrideEditor={<div>Subject override binding</div>}
				subjectEditor={<div>Inline subject binding</div>}
				messageEditor={<div>Inline message binding</div>}
			/>,
		)

		expect(await screen.findByText('Welcome {{ lead_name }}')).toBeInTheDocument()
		expect(screen.queryByText(/Require current email consent/i)).not.toBeInTheDocument()
		expect(screen.getByRole('link', { name: 'Open visual email builder' })).toHaveAttribute(
			'href',
			'/builder?template=Lead%20welcome',
		)

		fireEvent.click(screen.getByRole('button', { name: 'Preview email' }))
		const dialog = await screen.findByRole('dialog', { name: 'Email preview' })
		expect(dialog).toHaveTextContent('Welcome Ada')
		expect(screen.getByTitle('Rendered email')).toHaveAttribute(
			'srcdoc',
			'<html><body>Welcome Ada</body></html>',
		)
		fireEvent.click(screen.getByRole('button', { name: 'Mobile preview' }))
		expect(screen.getByTitle('Rendered email')).toHaveStyle({ width: '390px' })
		fireEvent.keyDown(document, { key: 'Escape' })
		await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Email preview' })).not.toBeInTheDocument())

		fireEvent.click(screen.getByRole('button', { name: 'Send test' }))
		expect(await screen.findByText('Queued for designer@example.com')).toBeInTheDocument()
		expect(mocks.call).toHaveBeenCalledWith(
			'send_workflow_test_email',
			expect.objectContaining({
				workflow_id: 'AUTO-WORKFLOW-1',
				recipient: 'designer@example.com',
			}),
			true,
		)
	})

	it('keeps quick inline email available for one-off content', () => {
		const update = vi.fn()
		render(
			<SendEmailEditor
				config={{ content_mode: 'template', email_template: '' }}
				workflowId="AUTO-WORKFLOW-1"
				primaryDoctype="Lead"
				update={update}
				recipientEditor={<div>Recipient binding</div>}
				subjectOverrideEditor={<div>Subject override binding</div>}
				subjectEditor={<div>Inline subject binding</div>}
				messageEditor={<div>Inline message binding</div>}
			/>,
		)

		fireEvent.click(screen.getByRole('button', { name: /Quick email/ }))
		expect(update).toHaveBeenCalledWith(
			expect.objectContaining({
				content_mode: 'inline',
				subject: { kind: 'literal', value: '' },
				message: { kind: 'literal', value: '' },
			}),
			'content_mode',
		)
	})
})
