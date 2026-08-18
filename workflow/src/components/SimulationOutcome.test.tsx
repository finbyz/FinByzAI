import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { SimulationOutcome } from './SimulationOutcome'

describe('SimulationOutcome', () => {
  it('shows configuration issues instead of a blank result', () => {
    const selectNode = vi.fn()
    render(
      <SimulationOutcome
        result={{
          valid: false,
          issues: [{ severity: 'error', code: 'MISSING_REQUIRED_CONFIG', message: 'Choose an email template.', node_id: 'email-step' }],
          path: [],
          mutated: false,
        }}
        onSelectNode={selectNode}
      />,
    )

    expect(screen.getByText('Configuration required before this test can run')).toBeInTheDocument()
    expect(screen.getByText('MISSING_REQUIRED_CONFIG')).toBeInTheDocument()
    expect(screen.getByText(/Choose an email template/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Open step' }))
    expect(selectNode).toHaveBeenCalledWith('email-step')
  })

  it('shows the evaluated path for a successful test', () => {
    render(
      <SimulationOutcome
        result={{
          valid: true,
          issues: [],
          path: [{ node_id: 'trigger', type: 'trigger.manual', status: 'SIMULATED', output: { matched: true } }],
          mutated: false,
        }}
      />,
    )

    expect(screen.getByText('trigger.manual')).toBeInTheDocument()
    expect(screen.getByText(/"matched": true/)).toBeInTheDocument()
    expect(screen.queryByLabelText('Test issues')).not.toBeInTheDocument()
  })
})
