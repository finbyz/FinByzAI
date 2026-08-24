import { useCallback, useEffect, useRef, useState } from 'react'
import { ConfirmDialog, type ConfirmDialogOptions } from './ConfirmDialog'

export function useConfirmDialog() {
  const [options, setOptions] = useState<ConfirmDialogOptions>()
  const resolver = useRef<((confirmed: boolean) => void) | undefined>(undefined)

  const settle = useCallback((confirmed: boolean) => {
    const resolve = resolver.current
    resolver.current = undefined
    setOptions(undefined)
    resolve?.(confirmed)
  }, [])

  const ask = useCallback((next: ConfirmDialogOptions) => new Promise<boolean>((resolve) => {
    resolver.current?.(false)
    resolver.current = resolve
    setOptions(next)
  }), [])

  useEffect(() => () => {
    resolver.current?.(false)
    resolver.current = undefined
  }, [])

  return {
    ask,
    dialog: options
      ? <ConfirmDialog {...options} cancel={() => settle(false)} confirm={() => settle(true)} />
      : null,
  }
}
