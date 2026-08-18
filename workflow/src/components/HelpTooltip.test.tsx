import '@testing-library/jest-dom/vitest'
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { HelpTooltip } from './HelpTooltip'

describe('HelpTooltip', () => {
  it('provides keyboard-accessible contextual help', () => {
    render(<HelpTooltip label="Runtime health" content="Explains every health signal." />)

    expect(screen.getByLabelText('Runtime health: Explains every health signal.')).toHaveAttribute('tabindex', '0')
    expect(screen.getByRole('tooltip')).toHaveTextContent('Explains every health signal.')
  })
})
  