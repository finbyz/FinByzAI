import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { InspectorResizeHandle } from './Inspector'

describe('InspectorResizeHandle', () => {
  it('supports accessible keyboard resizing and reset', () => {
    const onWidthChange = vi.fn()
    render(<InspectorResizeHandle width={420} minWidth={340} maxWidth={760} onWidthChange={onWidthChange} />)

    const handle = screen.getByRole('separator', { name: 'Resize step settings sidebar' })
    expect(handle).toHaveAttribute('aria-valuenow', '420')
    fireEvent.keyDown(handle, { key: 'ArrowLeft' })
    fireEvent.keyDown(handle, { key: 'ArrowRight' })
    fireEvent.keyDown(handle, { key: 'Home' })
    fireEvent.keyDown(handle, { key: 'End' })
    fireEvent.doubleClick(handle)

    expect(onWidthChange.mock.calls.map(([width]) => width)).toEqual([444, 396, 340, 760, 420])
  })

  it('resizes from the left edge using pointer movement', () => {
    const onWidthChange = vi.fn()
    render(<InspectorResizeHandle width={420} minWidth={340} maxWidth={760} onWidthChange={onWidthChange} />)

    const handle = screen.getByRole('separator', { name: 'Resize step settings sidebar' })
    const pointerEvent = (type: string, clientX: number) => {
      const event = new Event(type, { bubbles: true })
      Object.defineProperty(event, 'clientX', { value: clientX })
      return event
    }
    fireEvent(handle, pointerEvent('pointerdown', 700))
    fireEvent(window, pointerEvent('pointermove', 650))
    fireEvent.pointerUp(window)

    expect(onWidthChange).toHaveBeenCalledWith(470)
    expect(document.body).not.toHaveClass('inspector-resizing')
  })
})
