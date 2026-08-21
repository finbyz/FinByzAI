import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { AsyncCombobox } from './AsyncCombobox'

describe('AsyncCombobox', () => {
  it('searches, supports keyboard selection, and commits the underlying value', async () => {
    const user = userEvent.setup()
    const loadOptions = vi.fn().mockResolvedValue([
      { value: 'LEAD-0001', label: 'Megasol Lead', description: 'LEAD-0001' },
      { value: 'LEAD-0002', label: 'Second Lead', description: 'LEAD-0002' },
    ])
    const onChange = vi.fn()
    function Harness() {
      const [value, setValue] = useState('')
      return <AsyncCombobox ariaLabel="Lead record" value={value} onChange={(next, option) => { setValue(next); onChange(next, option) }} loadOptions={loadOptions} debounceMs={0} />
    }
    render(<Harness />)

    const input = screen.getByRole('combobox', { name: 'Lead record' })
    await user.click(input)
    await user.type(input, 'mega')
    expect(await screen.findByRole('option', { name: /Megasol Lead/ })).toBeInTheDocument()
    await user.keyboard('{ArrowDown}{Enter}')

    expect(onChange).toHaveBeenLastCalledWith('LEAD-0001', expect.objectContaining({ label: 'Megasol Lead' }))
    expect(input).toHaveValue('Megasol Lead')
  })

  it('renders results in a body portal so parent overflow cannot clip them', async () => {
    const user = userEvent.setup()
    const { container } = render(
      <div className="overflow-hidden">
        <AsyncCombobox
          ariaLabel="Portal search"
          value=""
          onChange={vi.fn()}
          loadOptions={() => Promise.resolve([{ value: 'LEAD-0001', label: 'Visible lead' }])}
          debounceMs={0}
        />
      </div>,
    )

    await user.click(screen.getByRole('combobox', { name: 'Portal search' }))
    const listbox = await screen.findByRole('listbox')

    expect(document.body).toContainElement(listbox)
    expect(container).not.toContainElement(listbox)
    expect(screen.getByRole('option', { name: /Visible lead/ })).toBeVisible()
  })

  it('keeps the committed value while searching and only clears through the clear button', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    const { rerender } = render(
      <AsyncCombobox
        ariaLabel="Target DocType"
        value="Lead"
        onChange={onChange}
        loadOptions={() => Promise.resolve([{ value: 'Customer', label: 'Customer' }])}
        debounceMs={0}
      />,
    )

    const input = screen.getByRole('combobox', { name: 'Target DocType' })
    await user.click(input)
    await user.clear(input)
    await user.type(input, 'Cus')
    expect(onChange).not.toHaveBeenCalled()

    await user.keyboard('{Escape}')
    expect(input).toHaveValue('Lead')

    rerender(
      <AsyncCombobox
        ariaLabel="Target DocType"
        value="Lead"
        onChange={onChange}
        loadOptions={() => Promise.resolve([])}
        debounceMs={0}
      />,
    )
    await user.click(screen.getByRole('button', { name: 'Clear selection' }))
    expect(onChange).toHaveBeenCalledWith('')
  })
})
