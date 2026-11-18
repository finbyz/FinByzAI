import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ call: vi.fn(), searchLink: vi.fn().mockResolvedValue([]) }))

vi.mock('../lib/api', () => ({
	call: mocks.call,
	fetchFieldCatalog: vi.fn(),
	invalidateMetadataCaches: vi.fn(),
	searchDoctypes: vi.fn(),
	searchLink: mocks.searchLink,
}))

import { SendEmailEditor } from './Inspector'

function editor(config: Record<string, unknown>) {
	return render(
		<SendEmailEditor
			config={config}
			workflowId="AUTO-WORKFLOW-1"
			primaryDoctype="Lead"
			update={vi.fn()}
			recipientEditor={<div>Recipient binding</div>}
			subjectOverrideEditor={<div>Subject override binding</div>}
			subjectEditor={<div>Inline subject binding</div>}
			messageEditor={<div>Inline message binding</div>}
		/>,
	)
}

describe('email type on a workflow email step', () => {
	beforeEach(() => {
		vi.clearAllMocks()
		window.frappe = { boot: { user: 'designer@example.com', roles: ['Email Designer'] } }
	})

	it('says which type the selected email brings', async () => {
		mocks.call.mockResolvedValue({ name: 'Lead welcome', subject: 'Hi', mode: 'Visual', email_type: 'Newsletter', desk_route: '/app/email-template/Lead%20welcome' })
		editor({ content_mode: 'template', email_template: 'Lead welcome' })
		await waitFor(() => expect(screen.getByTestId('email-type-from-template')).toHaveTextContent('Newsletter'))
		expect(screen.queryByTestId('email-type-missing')).toBeNull()
	})

	it('warns that publishing is blocked when no type is set anywhere', async () => {
		mocks.call.mockResolvedValue({ name: 'Lead welcome', subject: 'Hi', mode: 'Visual', email_type: '', desk_route: '/app/email-template/Lead%20welcome' })
		editor({ content_mode: 'template', email_template: 'Lead welcome' })
		await waitFor(() => expect(screen.getByTestId('email-type-missing')).toHaveTextContent('cannot be published'))
	})

	it('shows nothing extra once a type is chosen on the step', async () => {
		mocks.call.mockResolvedValue(null)
		editor({ content_mode: 'inline', subscription_topic: 'Newsletter' })
		expect(screen.queryByTestId('email-type-missing')).toBeNull()
		expect(screen.queryByTestId('email-type-from-template')).toBeNull()
	})
})
