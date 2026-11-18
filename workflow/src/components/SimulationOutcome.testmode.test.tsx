import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { pausedMessage, publishedMessage } from '../state/WorkflowContext'
import { SimulationOutcome } from './SimulationOutcome'

describe('workflow test run (WF-08)', () => {
  it('labels every step as really sent, simulated or skipped and tells where messages went', () => {
    render(
      <SimulationOutcome
        result={{
          valid: true,
          issues: [],
          mutated: false,
          test_mode: true,
          summary: { REALLY_SENT: 1, SIMULATED: 1, SKIPPED: 1, FAILED: 0 },
          tester: { email: 'me@example.com' },
          path: [
            { node_id: 'a', type: 'action.send_email', status: 'EVALUATED', output: {}, test_outcome: 'REALLY_SENT', test_note: 'Email queued to me@example.com with [TEST] in the subject.' },
            { node_id: 'b', type: 'action.update_record', status: 'PREDICTED', output: {}, test_outcome: 'SIMULATED' },
            { node_id: 'c', type: 'delay.fixed', status: 'PREDICTED', output: {}, test_outcome: 'SKIPPED', test_note: 'Wait skipped. It would last until 2026-11-20 09:00:00.' },
          ],
        }}
      />,
    )
    expect(screen.getByTestId('test-summary')).toHaveTextContent('1 really sent · 1 simulated · 1 skipped · 0 failed')
    expect(screen.getByTestId('test-summary')).toHaveTextContent('Messages went to me@example.com only.')
    expect(screen.getByText('Really sent')).toBeInTheDocument()
    expect(screen.getByText('Simulated')).toBeInTheDocument()
    expect(screen.getByText('Skipped')).toBeInTheDocument()
    expect(screen.getByText(/It would last until 2026-11-20 09:00:00/)).toBeInTheDocument()
  })

  it('words the pause and republish messages for the editor', () => {
    expect(pausedMessage(12)).toContain('12 records are waiting')
    expect(pausedMessage(1)).toContain('1 record is waiting')
    expect(pausedMessage(0)).toContain('Publish again')
    expect(publishedMessage({ moved: 3, failed: 0, re_enrolled: 0 })).toBe('3 records continued on the new version.')
    expect(publishedMessage({ moved: 2, failed: 1, re_enrolled: 1 })).toContain('1 could not continue')
    expect(publishedMessage({ moved: 0, failed: 0, re_enrolled: 0 })).toBeUndefined()
  })
})
