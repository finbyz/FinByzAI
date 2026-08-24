import { AlertTriangle, CheckCircle2, ShieldAlert } from 'lucide-react'
import { type ReactNode, useId } from 'react'
import { createPortal } from 'react-dom'
import { useDialogA11y } from './useDialogA11y'

export interface ConfirmDialogOptions {
  title: string
  description: ReactNode
  confirmLabel?: string
  cancelLabel?: string
  tone?: 'danger' | 'warning' | 'primary'
}

interface ConfirmDialogProps extends ConfirmDialogOptions {
  cancel(): void
  confirm(): void
}

export function ConfirmDialog({
  title,
  description,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  tone = 'warning',
  cancel,
  confirm,
}: ConfirmDialogProps) {
  const titleId = useId()
  const descriptionId = useId()
  const dialogRef = useDialogA11y(true, cancel, title)
  const icon = tone === 'danger' ? <ShieldAlert size={19} /> : tone === 'primary' ? <CheckCircle2 size={19} /> : <AlertTriangle size={19} />
  const iconTone = tone === 'danger'
    ? 'bg-red-50 text-red-600 dark:bg-red-500/10 dark:text-red-300'
    : tone === 'primary'
      ? 'bg-brand-50 text-brand-600 dark:bg-brand-500/10 dark:text-brand-300'
      : 'bg-amber-50 text-amber-600 dark:bg-amber-500/10 dark:text-amber-300'
  const confirmClass = tone === 'danger'
    ? 'btn-core border border-red-600 bg-red-600 text-white hover:bg-red-700 focus-visible:ring-red-500'
    : 'btn-core btn-primary'

  return createPortal(
    <div
      className="dialog-backdrop fixed inset-0 z-[110] grid place-items-center p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      aria-describedby={descriptionId}
      onMouseDown={(event) => { if (event.target === event.currentTarget) cancel() }}
    >
      <div ref={dialogRef} tabIndex={-1} className="dialog-card w-full max-w-md overflow-hidden rounded-2xl">
        <div className="p-5 sm:p-6">
          <div className="flex items-start gap-3.5">
            <span className={`grid size-10 shrink-0 place-items-center rounded-xl ${iconTone}`}>{icon}</span>
            <div className="min-w-0 pt-0.5">
              <h2 id={titleId} className="text-heading text-base font-bold tracking-tight">{title}</h2>
              <div id={descriptionId} className="text-muted mt-1.5 text-xs leading-5">{description}</div>
            </div>
          </div>
        </div>
        <div className="flex justify-end gap-2 border-t border-[var(--border-color)] bg-[var(--subtle-fg)] px-5 py-3.5 sm:px-6">
          <button type="button" className="btn-core btn-secondary" onClick={cancel} autoFocus>{cancelLabel}</button>
          <button type="button" className={confirmClass} onClick={confirm}>{confirmLabel}</button>
        </div>
      </div>
    </div>,
    document.body,
  )
}
