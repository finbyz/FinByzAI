import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { useDialogA11y } from './useDialogA11y'

function TestDialog({ label, close }: { label: string; close(): void }) {
  const ref = useDialogA11y(true, close, label)
  return <div className="dialog-backdrop"><div ref={ref} className="dialog-card"><button type="button">{label}</button></div></div>
}

function IconDialog() {
  useDialogA11y(true, () => undefined, 'Workflow policies')
  return <div className="dialog-backdrop"><div className="dialog-card"><button type="button"><svg /></button></div></div>
}

describe('useDialogA11y', () => {
  it('keeps Escape and focus handling on the topmost stacked dialog', async () => {
    const closeFirst = vi.fn()
    const closeSecond = vi.fn()
    render(<><TestDialog label="First dialog" close={closeFirst} /><TestDialog label="Second dialog" close={closeSecond} /></>)

    await waitFor(() => expect(screen.getByRole('button', { name: 'Second dialog' })).toHaveFocus())
    fireEvent.keyDown(document, { key: 'Escape' })

    expect(closeSecond).toHaveBeenCalledOnce()
    expect(closeFirst).not.toHaveBeenCalled()
  })

  it('gives an accessible name to an unlabeled dialog close button', () => {
    render(<IconDialog />)
    expect(screen.getByRole('button', { name: 'Close Workflow policies' })).toBeInTheDocument()
  })
})
