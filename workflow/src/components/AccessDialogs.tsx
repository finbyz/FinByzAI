import { LoaderCircle, Trash2, X } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { call, searchLink } from '../lib/api'
import { AsyncCombobox, type ComboboxOption } from './AsyncCombobox'

const secondary = 'btn-core btn-secondary'
const primary = 'btn-core btn-primary'
const field = 'frappe-control px-3 py-2 text-sm'

function Dialog({ title, subtitle, onClose, children }: { title: string; subtitle: string; onClose(): void; children: React.ReactNode }) {
  return createPortal(
    <div className="dialog-backdrop fixed inset-0 z-50 grid place-items-center p-4" role="dialog" aria-modal="true" aria-label={title} onClick={(event) => { if (event.target === event.currentTarget) onClose() }}>
      <div className="dialog-card w-full max-w-lg overflow-hidden rounded-2xl">
        <header className="flex items-start justify-between border-b border-[var(--border-color)] px-5 py-4">
          <div><h2 className="text-heading text-sm font-bold">{title}</h2><p className="text-muted mt-1 text-[10px]">{subtitle}</p></div>
          <button type="button" className="icon-button" aria-label="Close" onClick={onClose}><X size={16} /></button>
        </header>
        <div className="space-y-3 p-5">{children}</div>
      </div>
    </div>,
    document.body,
  )
}

interface AccessRow { user: string; read: number; write: number }

/** WF-10: who may open or edit one workflow, on top of the Automation roles. */
export function WorkflowAccessDialog({ workflowId, title, onClose }: { workflowId: string; title: string; onClose(): void }) {
  const [owner, setOwner] = useState('')
  const [rows, setRows] = useState<AccessRow[]>([])
  const [user, setUser] = useState('')
  const [access, setAccess] = useState<'read' | 'edit'>('read')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const loadUsers = useCallback((search: string): Promise<ComboboxOption[]> => searchLink('User', search, { filters: { enabled: 1 } }).then((found) => found.map((row) => ({ value: row.value, label: row.label || row.value, description: row.description }))), [])
  const apply = (result: { owner: string; rows: AccessRow[] }) => { setOwner(result.owner); setRows(result.rows) }
  useEffect(() => {
    call<{ owner: string; rows: AccessRow[] }>('get_workflow_access', { workflow_id: workflowId }).then(apply).catch((reason) => setError(reason instanceof Error ? reason.message : 'Unable to load access'))
  }, [workflowId])
  const change = async (target: string, level: 'none' | 'read' | 'edit') => {
    setBusy(true)
    setError('')
    try {
      apply(await call('set_workflow_access', { workflow_id: workflowId, user: target, access: level }, true))
      setUser('')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to change access')
    } finally {
      setBusy(false)
    }
  }
  return <Dialog title="Share workflow" subtitle={`${title}. Roles decide what a person can do. Sharing gives one more person access to this workflow.`} onClose={onClose}>
    <p className="text-muted text-[10px]">Owner: <strong className="text-heading">{owner || '…'}</strong> (always has full access)</p>
    <div className="divide-y divide-[var(--border-color)] rounded-lg border border-[var(--border-color)]">
      {rows.map((row) => <div className="flex items-center justify-between gap-2 px-3 py-2" key={row.user}>
        <span className="text-body truncate text-[11px]">{row.user}</span>
        <span className="flex items-center gap-2">
          <select className={field} aria-label={`Access for ${row.user}`} value={row.write ? 'edit' : 'read'} disabled={busy} onChange={(event) => void change(row.user, event.target.value as 'read' | 'edit')}><option value="read">Can view</option><option value="edit">Can edit</option></select>
          <button type="button" className="icon-button text-red-500" aria-label={`Remove ${row.user}`} disabled={busy} onClick={() => void change(row.user, 'none')}><Trash2 size={14} /></button>
        </span>
      </div>)}
      {!rows.length && <p className="text-muted px-3 py-4 text-center text-[10px]">Not shared with anyone yet.</p>}
    </div>
    <div className="grid gap-2 sm:grid-cols-[1fr_auto_auto]">
      <AsyncCombobox ariaLabel="User to share with" value={user} onChange={setUser} loadOptions={loadUsers} placeholder="Search enabled users…" />
      <select className={field} aria-label="Access level" value={access} onChange={(event) => setAccess(event.target.value as 'read' | 'edit')}><option value="read">Can view</option><option value="edit">Can edit</option></select>
      <button type="button" className={primary} disabled={!user || busy} onClick={() => void change(user, access)}>{busy && <LoaderCircle className="animate-spin" size={13} />}Share</button>
    </div>
    {error && <p className="rounded-lg border border-red-200 bg-red-50 p-3 text-[10px] text-red-700">{error}</p>}
    <div className="flex justify-end"><button type="button" className={secondary} onClick={onClose}>Done</button></div>
  </Dialog>
}

interface ArchiveRow { name: string; file_name: string; file_url: string; file_size: number; creation: string }

/** WF-50: gzip archives of purged run logs. */
export function RunArchivesDialog({ onClose }: { onClose(): void }) {
  const [rows, setRows] = useState<ArchiveRow[] | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    call<ArchiveRow[]>('list_run_archives', {}).then(setRows).catch((reason) => setError(reason instanceof Error ? reason.message : 'Unable to load archives'))
  }, [])
  return <Dialog title="Run log archives" subtitle="Run logs older than the retention period are saved here as a private gzip file (one JSON object per line) before they are removed." onClose={onClose}>
    {!rows && !error && <div className="grid place-items-center py-6"><LoaderCircle className="animate-spin text-brand-500" /></div>}
    {rows && <div className="divide-y divide-[var(--border-color)] rounded-lg border border-[var(--border-color)]">
      {rows.map((row) => <div className="flex items-center justify-between gap-2 px-3 py-2" key={row.name}>
        <span className="min-w-0"><span className="text-body block truncate text-[11px]">{row.file_name}</span><span className="text-light text-[9px]">{new Date(row.creation.replace(' ', 'T')).toLocaleString()} · {Math.max(1, Math.round((row.file_size || 0) / 1024)).toLocaleString()} KB</span></span>
        <a className={secondary} href={row.file_url} download>Download</a>
      </div>)}
      {!rows.length && <p className="text-muted px-3 py-4 text-center text-[10px]">Nothing archived yet. The first archive is written when a run log passes the retention period.</p>}
    </div>}
    {error && <p className="rounded-lg border border-red-200 bg-red-50 p-3 text-[10px] text-red-700">{error}</p>}
    <div className="flex justify-end"><button type="button" className={secondary} onClick={onClose}>Close</button></div>
  </Dialog>
}
