import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ThemeToggle } from './ThemeToggle'

describe('ThemeToggle', () => {
  beforeEach(() => {
    localStorage.clear()
    document.documentElement.removeAttribute('data-theme')
    document.documentElement.removeAttribute('data-theme-mode')
    window.frappe = { boot: { desk_theme: 'Dark' } }
    Object.defineProperty(window, 'matchMedia', {
      configurable: true,
      value: vi.fn().mockReturnValue({
        matches: false,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      }),
    })
  })

  afterEach(cleanup)

  it('starts from the Frappe desk preference and cycles through automatic mode', () => {
    render(<ThemeToggle />)
    expect(document.documentElement.dataset.theme).toBe('dark')
    expect(document.documentElement.dataset.themeMode).toBe('dark')

    fireEvent.click(screen.getByRole('button', { name: 'Theme: dark' }))
    expect(document.documentElement.dataset.themeMode).toBe('automatic')
    expect(document.documentElement.dataset.theme).toBe('light')
  })
})
