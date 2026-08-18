import { X } from 'lucide-react'
import { type KeyboardEvent, useCallback, useMemo, useState } from 'react'
import { AsyncCombobox, type ComboboxOption } from './AsyncCombobox'

interface MultiValueInputProps {
  values: string[]
  onChange(values: string[]): void
  loadOptions?: (search: string) => Promise<ComboboxOption[]>
  placeholder?: string
  ariaLabel?: string
}

function normalize(values: string[]): string[] {
  const seen = new Set<string>()
  return values.flatMap((value) => {
    const normalized = value.trim()
    if (!normalized || seen.has(normalized)) return []
    seen.add(normalized)
    return [normalized]
  })
}

export function MultiValueInput({
  values,
  onChange,
  loadOptions,
  placeholder = 'Add a value…',
  ariaLabel = 'Multiple values',
}: MultiValueInputProps) {
  const selected = useMemo(() => normalize(values), [values])
  const [draft, setDraft] = useState('')
  const add = (value: string) => onChange(normalize([...selected, value]))
  const remove = (value: string) => onChange(selected.filter((item) => item !== value))
  const commitDraft = () => {
    const candidates = draft.split(',')
    if (candidates.some((item) => item.trim())) onChange(normalize([...selected, ...candidates]))
    setDraft('')
  }
  const handleKey = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter' || event.key === ',') {
      event.preventDefault()
      commitDraft()
    } else if (event.key === 'Backspace' && !draft && selected.length) {
      remove(selected.at(-1)!)
    }
  }
  const availableOptions = useCallback(async (search: string) => {
    if (!loadOptions) return []
    const rows = await loadOptions(search)
    const chosen = new Set(selected)
    return rows.filter((row) => !chosen.has(row.value))
  }, [loadOptions, selected])

  return (
    <div className="space-y-2">
      {selected.length > 0 && (
        <div className="flex flex-wrap gap-1.5" aria-label={`${ariaLabel} selected values`}>
          {selected.map((value) => (
            <span className="inline-flex max-w-full items-center gap-1 rounded-full border border-brand-200 bg-brand-50 px-2 py-1 text-[10px] font-medium text-brand-700 dark:border-brand-500/30 dark:bg-brand-500/10 dark:text-brand-200" key={value}>
              <span className="truncate">{value}</span>
              <button type="button" className="grid size-4 shrink-0 place-items-center rounded-full hover:bg-brand-100 dark:hover:bg-brand-500/20" aria-label={`Remove ${value}`} onClick={() => remove(value)}><X size={10} /></button>
            </span>
          ))}
        </div>
      )}
      {loadOptions ? (
        <AsyncCombobox
          ariaLabel={ariaLabel}
          value=""
          onChange={(value) => { if (value) add(value) }}
          loadOptions={availableOptions}
          placeholder={placeholder}
          emptyMessage="No additional permitted values found"
        />
      ) : (
        <input
          className="frappe-control px-3 py-2 text-xs"
          aria-label={ariaLabel}
          placeholder={placeholder}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={handleKey}
          onBlur={commitDraft}
        />
      )}
      <p className="text-light text-[9.5px]">{loadOptions ? 'Search and add one or more values.' : 'Press Enter or comma to add each value; Backspace removes the last pill.'}</p>
    </div>
  )
}
