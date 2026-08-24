import { Check, ChevronDown, LoaderCircle, Search, X } from 'lucide-react'
import { type CSSProperties, useCallback, useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

export interface ComboboxOption {
  value: string
  label: string
  description?: string
}

interface AsyncComboboxProps {
  value: string
  onChange(value: string, option?: ComboboxOption): void
  loadOptions(search: string): Promise<ComboboxOption[]>
  placeholder?: string
  emptyMessage?: string
  disabled?: boolean
  ariaLabel?: string
  debounceMs?: number
}

export function AsyncCombobox({
  value,
  onChange,
  loadOptions,
  placeholder = 'Search and choose…',
  emptyMessage = 'No permitted matches found',
  disabled = false,
  ariaLabel,
  debounceMs = 240,
}: AsyncComboboxProps) {
  const id = useId()
  const root = useRef<HTMLDivElement>(null)
  const menu = useRef<HTMLDivElement>(null)
  const requestNumber = useRef(0)
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState(value)
  const [committedValue, setCommittedValue] = useState(value)
  const [committedLabel, setCommittedLabel] = useState(value)
  const [options, setOptions] = useState<ComboboxOption[]>([])
  const [highlighted, setHighlighted] = useState(-1)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [menuStyle, setMenuStyle] = useState<CSSProperties>()

  const positionMenu = useCallback(() => {
    const rect = root.current?.getBoundingClientRect()
    if (!rect) return
    const gap = 6
    const below = window.innerHeight - rect.bottom - gap
    const above = rect.top - gap
    const openAbove = below < 260 && above > below
    setMenuStyle({
      position: 'fixed',
      left: Math.max(8, rect.left),
      width: Math.min(rect.width, window.innerWidth - Math.max(8, rect.left) - 8),
      top: openAbove ? undefined : rect.bottom + gap,
      bottom: openAbove ? window.innerHeight - rect.top + gap : undefined,
      maxHeight: Math.max(120, Math.min(320, (openAbove ? above : below) - 4)),
      zIndex: 10000,
    })
  }, [])

  useEffect(() => {
    if (value === committedValue) return
    // A parent can replace a dependent Link value through undo, record-type
    // changes, or a draft reload. A label from the old value must never survive
    // that controlled update.
    setCommittedValue(value)
    setCommittedLabel(value)
    setQuery(value)
  }, [committedValue, value])

  useEffect(() => {
    if (!open) {
      const label = value ? committedLabel || value : ''
      setQuery(label)
    }
  }, [committedLabel, open, value])

  useEffect(() => {
    if (!open || disabled) return
    const currentRequest = ++requestNumber.current
    const timer = window.setTimeout(() => {
      setLoading(true)
      setError('')
      void loadOptions(query)
        .then((rows) => {
          if (requestNumber.current !== currentRequest) return
          setOptions(rows)
          setHighlighted(-1)
        })
        .catch((reason: unknown) => {
          if (requestNumber.current !== currentRequest) return
          setOptions([])
          setError(reason instanceof Error ? reason.message : 'Unable to search')
        })
        .finally(() => {
          if (requestNumber.current === currentRequest) setLoading(false)
        })
    }, debounceMs)
    return () => window.clearTimeout(timer)
  }, [debounceMs, disabled, loadOptions, open, query])

  useEffect(() => {
    if (!open) {
      setMenuStyle(undefined)
      return
    }
    positionMenu()
    window.addEventListener('resize', positionMenu)
    window.addEventListener('scroll', positionMenu, true)
    return () => {
      window.removeEventListener('resize', positionMenu)
      window.removeEventListener('scroll', positionMenu, true)
    }
  }, [open, positionMenu])

  useEffect(() => {
    const close = (event: PointerEvent) => {
      const target = event.target as Node
      if (!root.current?.contains(target) && !menu.current?.contains(target)) setOpen(false)
    }
    document.addEventListener('pointerdown', close)
    return () => document.removeEventListener('pointerdown', close)
  }, [])

  const choose = (option: ComboboxOption) => {
    setCommittedValue(option.value)
    setCommittedLabel(option.label)
    setQuery(option.label)
    onChange(option.value, option)
    setOpen(false)
  }

  return (
    <div className="relative" ref={root}>
      <div className="relative">
        <Search className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[var(--text-light)]" size={14} aria-hidden />
        <input
          className="frappe-control min-h-10 px-9 pr-16 text-xs"
          role="combobox"
          aria-label={ariaLabel}
          aria-expanded={open}
          aria-controls={`${id}-listbox`}
          aria-activedescendant={open && highlighted >= 0 ? `${id}-option-${highlighted}` : undefined}
          aria-autocomplete="list"
          autoComplete="off"
          disabled={disabled}
          placeholder={placeholder}
          value={query}
          onFocus={() => setOpen(true)}
          onChange={(event) => {
            setQuery(event.target.value)
            setOpen(true)
          }}
          onKeyDown={(event) => {
            if (event.key === 'ArrowDown') {
              event.preventDefault()
              setOpen(true)
              setHighlighted((index) => Math.min(index + 1, options.length - 1))
            } else if (event.key === 'ArrowUp') {
              event.preventDefault()
              setHighlighted((index) => Math.max(index - 1, -1))
            } else if (event.key === 'Enter' && open && options[highlighted]) {
              event.preventDefault()
              choose(options[highlighted])
            } else if (event.key === 'Escape') {
              setOpen(false)
            }
          }}
        />
        <span className="absolute right-2 top-1/2 flex -translate-y-1/2 items-center gap-0.5">
          {loading && <LoaderCircle className="animate-spin text-brand-500" size={14} aria-label="Searching" />}
          {value && !loading && <button type="button" className="icon-button !size-7" aria-label="Clear selection" onClick={() => { setCommittedValue(''); setCommittedLabel(''); setQuery(''); onChange(''); setOpen(true) }}><X size={13} /></button>}
          {!value && !loading && <ChevronDown className="text-[var(--text-light)]" size={14} aria-hidden />}
        </span>
      </div>
      {open && menuStyle && createPortal(
        <div ref={menu} className="surface flex flex-col overflow-hidden rounded-xl shadow-workflow-panel" style={menuStyle}>
          <div id={`${id}-listbox`} role="listbox" className="min-h-0 flex-1 overflow-y-auto p-1.5">
            {error ? (
              <p className="rounded-lg px-3 py-3 text-[11px] text-red-600">{error}</p>
            ) : !loading && !options.length ? (
              <p className="text-muted rounded-lg px-3 py-3 text-[11px]">{emptyMessage}</p>
            ) : options.map((option, index) => (
              <button
                type="button"
                id={`${id}-option-${index}`}
                role="option"
                aria-selected={option.value === value}
                className={`flex w-full items-start gap-2.5 rounded-lg px-3 py-2.5 text-left transition-colors ${index === highlighted ? 'bg-brand-50 dark:bg-brand-500/10' : 'hover:bg-[var(--subtle-fg)]'}`}
                key={option.value}
                onMouseDown={(event) => event.preventDefault()}
                onMouseEnter={() => setHighlighted(index)}
                onClick={() => choose(option)}
              >
                <span className={`mt-0.5 grid size-5 shrink-0 place-items-center rounded-md ${option.value === value ? 'bg-brand-500 text-white' : 'bg-[var(--subtle-fg)] text-transparent'}`}><Check size={12} /></span>
                <span className="min-w-0 flex-1"><strong className="text-heading block truncate text-[11px] font-semibold">{option.label}</strong>{option.description && <span className="text-muted mt-0.5 block truncate text-[9.5px]">{option.description}</span>}</span>
              </button>
            ))}
          </div>
          <div className="border-t border-[var(--border-color)] bg-[var(--subtle-fg)] px-3 py-2 text-[9px] text-[var(--text-light)]">Search uses Frappe permissions and Link-field rules</div>
        </div>,
        document.body,
      )}
    </div>
  )
}
