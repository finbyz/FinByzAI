import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { useConfirmDialog } from './useConfirmDialog'

function Harness({ decided }: { decided(value: boolean): void }) {
  const confirmation = useConfirmDialog()
  const [pending, setPending] = useState(false)
  const request = async () => {
    setPending(true)
    decided(await confirmation.ask({
      title: 'Disable workflow?',
      description: 'New enrollments will stop.',
      confirmLabel: 'Disable workflow',
      tone: 'danger',
    }))
    setPending(false)
  }
  return <><button onClick={() => void request()}>Open</button>{pending && <span>Awaiting decision</span>}{confirmation.dialog}</>
}

describe('useConfirmDialog', () => {
  it('renders a themed accessible dialog and resolves confirmation', async () => {
    const decided = vi.fn()
    render(<Harness decided={decided} />)

    fireEvent.click(screen.getByRole('button', { name: 'Open' }))
    const dialog = screen.getByRole('dialog', { name: 'Disable workflow?' })
    expect(dialog.parentElement).toBe(document.body)
    expect(screen.getByText('New enrollments will stop.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus()

    fireEvent.click(screen.getByRole('button', { name: 'Disable workflow' }))
    await waitFor(() => expect(decided).toHaveBeenCalledWith(true))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('resolves false on Escape', async () => {
    const decided = vi.fn()
    render(<Harness decided={decided} />)
    fireEvent.click(screen.getByRole('button', { name: 'Open' }))
    fireEvent.keyDown(document, { key: 'Escape' })
    await waitFor(() => expect(decided).toHaveBeenCalledWith(false))
  })
})
