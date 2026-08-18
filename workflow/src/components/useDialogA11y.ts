import { useEffect, useRef } from 'react'

const focusableSelector = [
  'button:not([disabled])',
  'a[href]',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

const activeDialogs: symbol[] = []

export function useDialogA11y(active: boolean, close: () => void, label?: string) {
  const dialogRef = useRef<HTMLDivElement>(null)
  const instance = useRef(Symbol('dialog'))
  const closeRef = useRef(close)
  closeRef.current = close

  useEffect(() => {
    if (!active) return
    const instanceId = instance.current
    activeDialogs.push(instanceId)
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : undefined
    const candidates = Array.from(document.querySelectorAll<HTMLElement>('.dialog-backdrop .dialog-card'))
    const dialog = dialogRef.current || candidates[candidates.length - 1]
    const backdrop = dialog?.closest<HTMLElement>('.dialog-backdrop')
    if (backdrop) {
      backdrop.setAttribute('role', 'dialog')
      backdrop.setAttribute('aria-modal', 'true')
      if (label && !backdrop.hasAttribute('aria-labelledby')) backdrop.setAttribute('aria-label', label)
    }
    const firstButton = dialog?.querySelector<HTMLButtonElement>('button')
    if (firstButton && !firstButton.getAttribute('aria-label') && !firstButton.textContent?.trim()) {
      firstButton.setAttribute('aria-label', label === 'Draft conflict' ? 'Reload server version' : `Close ${label || 'dialog'}`)
    }
    const focusable = () => Array.from(dialog?.querySelectorAll<HTMLElement>(focusableSelector) || [])
    const isTopmost = () => {
      const backdrops = Array.from(document.querySelectorAll<HTMLElement>('.dialog-backdrop'))
      return activeDialogs[activeDialogs.length - 1] === instanceId && (!backdrop || backdrops[backdrops.length - 1] === backdrop)
    }
    window.requestAnimationFrame(() => {
      if (isTopmost()) (focusable()[0] || dialog)?.focus()
    })
    const onKeyDown = (event: KeyboardEvent) => {
      if (!isTopmost()) return
      if (event.key === 'Escape') {
        event.preventDefault()
        closeRef.current()
        return
      }
      if (event.key !== 'Tab') return
      const items = focusable()
      if (!items.length) {
        event.preventDefault()
        dialog?.focus()
        return
      }
      const first = items[0]
      const last = items[items.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      const stackIndex = activeDialogs.lastIndexOf(instanceId)
      if (stackIndex >= 0) activeDialogs.splice(stackIndex, 1)
      previous?.focus()
    }
  }, [active, label])

  return dialogRef
}
