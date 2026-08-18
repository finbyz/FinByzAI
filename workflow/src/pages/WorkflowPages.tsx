/* oxlint-disable react/only-export-components -- route components and their shared presentation helpers are colocated */
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  Ban,
  CalendarClock,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  CircleDot,
  Clock3,
  Copy,
  FileCheck2,
  FlaskConical,
  Gauge,
  GitCompareArrows,
  History,
  Layers3,
  LayoutDashboard,
  LoaderCircle,
  Pause,
  Play,
  Plus,
  Redo2,
  Save,
  Search,
  Send,
  Settings2,
  ShieldCheck,
  Sparkles,
  Trash2,
  Undo2,
  WandSparkles,
  Workflow,
  X,
  Zap,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { type ReactNode, useCallback, useEffect, useRef, useState } from 'react'
import { Controller, useForm } from 'react-hook-form'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { Inspector } from '../components/Inspector'
import { HelpTooltip } from '../components/HelpTooltip'
import { PolicyConditionEditor } from '../components/InspectorHelpers'
import { AsyncCombobox, type ComboboxOption } from '../components/AsyncCombobox'
import { NodeCatalog } from '../components/NodeCatalog'
import { SimulationOutcome } from '../components/SimulationOutcome'
import { ThemeToggle } from '../components/ThemeToggle'
import { useDialogA11y } from '../components/useDialogA11y'
import { WorkflowCanvas } from '../components/WorkflowCanvas'
import { call, fetchFieldCatalog, mutationEnvelope, searchDoctypes, searchLink } from '../lib/api'
import { projectRunTrace } from '../lib/runTrace'
import { useWorkflowActions, useWorkflowDocument, useWorkflowEditor, useWorkflowHistory, WorkflowProvider } from '../state/WorkflowContext'
import type { ConditionExpression, FieldCatalogItem, ManualEnrollmentResult, RunSummary, RuntimeHealth, RuntimePreflight, WorkflowGraph, WorkflowLookup, WorkflowSummary } from '../types'

const secondary = 'btn-core btn-secondary'
const primary = 'btn-core btn-primary'
const magic = 'btn-core btn-magic'
const ghost = 'btn-core btn-ghost'
const field = 'frappe-control px-3 py-2 text-sm'

export function hasRole(...roles: string[]) {
  const current = new Set(window.frappe?.boot?.roles || [])
  return current.has('System Manager') || roles.some((role) => current.has(role))
}

export function ProductBrand({ subtitle = 'Automation Studio' }: { subtitle?: string }) {
  return (
    <Link to="/" className="flex items-center gap-2.5 rounded-lg focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
      <span className="brand-mark shrink-0"><Workflow size={18} strokeWidth={2.25} /></span>
      <span className="leading-tight hidden sm:block">
        <strong className="text-heading block text-[13px] font-bold tracking-[-0.01em]">Workflow Builder</strong>
        <span className="text-light block text-[9px] font-semibold uppercase tracking-[0.12em]">{subtitle}</span>
      </span>
    </Link>
  )
}

export function Header({ children }: { children: ReactNode }) {
  return <header className="app-topbar px-4 sm:px-5">{children}</header>
}

export function DeskLink() {
  return <a className="btn-core btn-ghost" href="/app" title="Return to Frappe Desk"><LayoutDashboard size={14} /><span className="hidden sm:inline">Desk</span></a>
}

export function Status({ value }: { value: string }) {
  const tone = ['ACTIVE', 'COMPLETED', 'SUCCESS'].includes(value)
    ? 'border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-500/10 dark:text-emerald-300'
    : ['FAILED', 'DISABLED', 'CANCELLED'].includes(value)
      ? 'border-red-200 bg-red-50 text-red-700 dark:border-red-900 dark:bg-red-500/10 dark:text-red-300'
      : ['WAITING', 'PAUSED'].includes(value)
        ? 'border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-900 dark:bg-amber-500/10 dark:text-amber-300'
        : 'border-[var(--border-color)] bg-[var(--subtle-fg)] text-[var(--text-muted)]'
  return <span className={`status-pill ${tone}`}>{value}</span>
}

export function formatDate(value?: string) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(date)
}

function formatJsonList(value?: string) {
  if (!value) return ''
  try {
    const parsed: unknown = JSON.parse(value)
    return Array.isArray(parsed) ? parsed.map(String).join(', ') : ''
  } catch {
    return ''
  }
}

function safeJsonEvidence(value?: string) {
  if (!value) return ''
  try {
    return JSON.stringify(JSON.parse(value), null, 2)
  } catch {
    return 'Evidence unavailable: malformed legacy JSON.'
  }
}

interface CreateForm { title: string; primary_doctype: string; trigger_type: string }

function CreateDialog({ close, created }: { close(): void; created(id: string): void }) {
  const dialogRef = useDialogA11y(true, close)
  const { register, control, handleSubmit, formState: { isSubmitting } } = useForm<CreateForm>({ defaultValues: { primary_doctype: '', trigger_type: 'trigger.manual' } })
  const [error, setError] = useState('')
  const loadDoctypes = useCallback((search: string) => searchDoctypes('read', search).then((rows) => rows.map((row) => ({ value: row.name, label: row.label || row.name, description: row.module }))), [])
  const submit = handleSubmit(async (values) => {
    try {
      const result = await call<{ workflow: string }>('create_workflow', { envelope: { payload: values } }, true)
      created(result.workflow)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to create workflow')
    }
  })
  return (
    <div className="dialog-backdrop fixed inset-0 z-50 grid place-items-center p-4" role="dialog" aria-modal="true" aria-labelledby="create-workflow-title" onClick={(e) => { if (e.target === e.currentTarget) close() }}>
      <div ref={dialogRef} tabIndex={-1} className="dialog-card grid w-full max-w-3xl overflow-hidden rounded-2xl md:grid-cols-[250px_1fr]">
        <aside className="relative hidden overflow-hidden bg-[#192733] p-7 text-white md:block">
          <div className="absolute -right-20 -top-16 size-52 rounded-full bg-brand-500/25 blur-2xl" />
          <div className="absolute -bottom-20 -left-16 size-48 rounded-full bg-magic-500/25 blur-2xl" />
          <span className="relative grid size-11 place-items-center rounded-xl bg-white/10 ring-1 ring-white/15"><WandSparkles size={21} /></span>
          <h2 className="relative mt-6 text-xl font-bold tracking-tight">Make busywork disappear.</h2>
          <p className="relative mt-2 text-xs leading-5 text-slate-300">Start with a business record and an enrollment moment. You can shape the rest visually.</p>
          <div className="relative mt-8 space-y-3 text-[11px] text-slate-300">
            <p className="flex gap-2"><ShieldCheck className="shrink-0 text-emerald-400" size={15} />Frappe permissions stay authoritative</p>
            <p className="flex gap-2"><Layers3 className="shrink-0 text-brand-300" size={15} />Every published version is immutable</p>
            <p className="flex gap-2"><Sparkles className="shrink-0 text-violet-300" size={15} />Simulation never changes live data</p>
          </div>
        </aside>
        <form onSubmit={submit} className="bg-white/40 dark:bg-[#18212b]/40 backdrop-blur-sm p-5 sm:p-7">
          <div className="flex items-start justify-between gap-4">
            <div><p className="text-[10px] font-bold uppercase tracking-[0.14em] text-brand-600">New automation</p><h2 id="create-workflow-title" className="text-heading mt-1 text-xl font-bold tracking-tight">Create a workflow</h2><p className="text-muted mt-1 text-xs">Choose where this automation begins.</p></div>
            <button type="button" className="icon-button" onClick={close} aria-label="Close"><X size={18} /></button>
          </div>
          <div className="mt-6 space-y-4">
            <label className="text-heading block text-xs font-semibold">Workflow name<input className={`${field} mt-1.5`} placeholder="e.g. Qualify new enterprise leads" {...register('title', { required: true })} /></label>
            <label className="text-heading block text-xs font-semibold">Business DocType<span className="mt-1.5 block"><Controller control={control} name="primary_doctype" rules={{ required: true }} render={({ field: doctypeField }) => <AsyncCombobox ariaLabel="Business DocType" value={doctypeField.value} onChange={doctypeField.onChange} loadOptions={loadDoctypes} placeholder="Search DocTypes by name or module…" />}/></span><span className="text-muted mt-1.5 block text-[10px] font-normal">Only readable, automation-safe DocTypes appear here.</span></label>
            <label className="text-heading block text-xs font-semibold">How should records enroll?<select className={`${field} mt-1.5`} {...register('trigger_type')}><option value="trigger.manual">Manually, when an operator chooses</option><option value="trigger.document_insert">When a record is created</option><option value="trigger.document_change">When relevant fields change</option><option value="trigger.schedule">On a durable schedule</option></select></label>
            {error && <p className="rounded-lg border border-red-200 bg-red-50 p-3 text-xs text-red-700">{error}</p>}
          </div>
          <div className="mt-7 flex justify-end gap-2"><button type="button" className={secondary} onClick={close}>Cancel</button><button className={primary} disabled={isSubmitting}>{isSubmitting ? <LoaderCircle className="animate-spin" size={15} /> : <Plus size={15} />}Create workflow</button></div>
        </form>
      </div>
    </div>
  )
}

function DeleteWorkflowDialog({ workflow, close, deleted }: { workflow: WorkflowSummary; close(): void; deleted(): void }) {
  const dialogRef = useDialogA11y(true, close)
  const [confirmText, setConfirmText] = useState('')
  const [deleting, setDeleting] = useState(false)
  const [error, setError] = useState('')
  const remove = async () => {
    setDeleting(true)
    setError('')
    try {
      await call('delete_workflow', mutationEnvelope(workflow.name, {}), true)
      deleted()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to delete this workflow')
      setDeleting(false)
    }
  }
  return (
    <div className="dialog-backdrop fixed inset-0 z-50 grid place-items-center p-4" role="dialog" aria-modal="true" aria-labelledby="delete-workflow-title" onClick={(e) => { if (e.target === e.currentTarget) close() }}>
      <div ref={dialogRef} tabIndex={-1} className="dialog-card relative w-full max-w-md rounded-2xl p-5 sm:p-6">
        <button className="icon-button absolute right-4 top-4" onClick={close} aria-label="Close delete dialog"><X size={17} /></button>
        <span className="grid size-10 place-items-center rounded-xl bg-red-50 text-red-600 dark:bg-red-500/10"><Trash2 size={18} /></span>
        <h2 id="delete-workflow-title" className="text-heading mt-4 text-lg font-bold">Delete this draft?</h2>
        <p className="text-muted mt-2 text-xs leading-5">Only unpublished drafts with no runs can be deleted. Published history remains immutable. Type <strong className="text-heading">{workflow.name}</strong> to confirm.</p>
        <input className={`${field} mt-4`} value={confirmText} onChange={(event) => setConfirmText(event.target.value)} placeholder={workflow.name} autoFocus />
        {error && <p className="mt-3 rounded-lg border border-red-200 bg-red-50 p-3 text-xs text-red-700 dark:border-red-900 dark:bg-red-500/10 dark:text-red-300">{error}</p>}
        <div className="mt-6 flex justify-end gap-2"><button className={secondary} onClick={close} disabled={deleting}>Keep draft</button><button className="btn-core border border-red-600 bg-red-600 text-white hover:bg-red-700" disabled={confirmText !== workflow.name || deleting} onClick={() => void remove()}>{deleting ? <LoaderCircle className="animate-spin" size={14} /> : <Trash2 size={14} />}Delete draft</button></div>
      </div>
    </div>
  )
}

export function WorkflowListPage() {
  const [rows, setRows] = useState<WorkflowSummary[]>([])
  const [runtime, setRuntime] = useState<RuntimeHealth>()
  const [creating, setCreating] = useState(false)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [hasMoreWorkflows, setHasMoreWorkflows] = useState(false)
  const [workflowTotals, setWorkflowTotals] = useState({ total: 0, active: 0, paused: 0 })
  const [workflowSearch, setWorkflowSearch] = useState('')
  const [appliedWorkflowSearch, setAppliedWorkflowSearch] = useState('')
  const workflowRequestSequence = useRef(0)
  const [error, setError] = useState('')
  const [deleting, setDeleting] = useState<WorkflowSummary>()
  const navigate = useNavigate()
  const load = useCallback(async (start = 0, append = false, signal?: AbortSignal) => {
	const sequence = ++workflowRequestSequence.current
	if (append) setLoadingMore(true)
	else setLoading(true)
    setError('')
    try {
      const [value, health] = await Promise.all([
		call<{ rows: WorkflowSummary[]; has_more: boolean; total_count: number; status_counts: { ACTIVE: number; PAUSED: number } }>('list_workflows', { search: appliedWorkflowSearch, start, page_length: 50 }, false, signal),
		call<RuntimeHealth>('get_runtime_health', {}, false, signal),
      ])
	  if (sequence !== workflowRequestSequence.current) return
	  setRows((current) => append ? [...current, ...value.rows] : value.rows)
	  setHasMoreWorkflows(value.has_more)
	  setWorkflowTotals({ total: value.total_count, active: value.status_counts.ACTIVE, paused: value.status_counts.PAUSED })
      setRuntime(health)
    } catch (reason) {
	  if (!signal?.aborted && sequence === workflowRequestSequence.current) setError(reason instanceof Error ? reason.message : 'Unable to load workflows')
    } finally {
	  if (sequence === workflowRequestSequence.current) { setLoading(false); setLoadingMore(false) }
    }
  }, [appliedWorkflowSearch])
  useEffect(() => {
	const controller = new AbortController()
	void load(0, false, controller.signal)
	return () => controller.abort()
  }, [load])
  useEffect(() => {
	const timer = window.setTimeout(() => setAppliedWorkflowSearch(workflowSearch.trim()), 300)
	return () => window.clearTimeout(timer)
  }, [workflowSearch])
  const canBuild = hasRole('Automation Builder', 'Automation Publisher')
  const canOperate = hasRole('Automation Operator', 'Automation Publisher')
  const metrics: Array<{ label: string; value: number; icon: LucideIcon; tone: string }> = [
    { label: 'Workflows', value: workflowTotals.total, icon: Layers3, tone: 'text-magic-500' },
    { label: 'Active', value: workflowTotals.active, icon: Zap, tone: 'text-emerald-500' },
    { label: 'Paused', value: workflowTotals.paused, icon: Pause, tone: 'text-amber-500' },
  ]
  const setWorkflowState = async (workflowId: string, status: 'ACTIVE' | 'PAUSED' | 'DISABLED') => {
    if (status === 'DISABLED' && !window.confirm('Disable this workflow? New enrollments stop and all active runs and timers are cancelled. Published history remains available.')) return
    try {
      await call('set_state', mutationEnvelope(workflowId, { status }), true)
      await load()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to change workflow state')
    }
  }
  const cloneWorkflow = async (row: WorkflowSummary) => {
    try {
      const result = await call<{ workflow: string }>('clone_workflow', mutationEnvelope(row.name, { title: `Copy of ${row.title}` }), true)
      navigate(`/${result.workflow}`)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to clone workflow')
    }
  }
  return (
    <div className="app-shell">
      <Header>
        <ProductBrand />
        <div className="flex items-center gap-2">
          <div className="hidden text-right sm:block"><p className="text-heading text-[11px] font-semibold">{window.frappe?.boot?.user}</p><p className="text-light text-[9px]">{window.frappe?.boot?.site_name}</p></div>
          {canOperate && <Link className={secondary} to="/operations"><Activity size={14} /><span className="hidden sm:inline">Operations</span></Link>}
          <DeskLink />
          <ThemeToggle />
          {canBuild && <Link className={secondary} to="/templates"><Layers3 size={14} /><span className="hidden sm:inline">Templates</span></Link>}
          {canBuild && <button className={primary} onClick={() => setCreating(true)}><Plus size={15} /><span className="hidden sm:inline">Create workflow</span></button>}
        </div>
      </Header>
      <main className="mx-auto max-w-7xl px-4 pb-12 pt-6 sm:px-6 lg:px-8">
        <section className="hero-glow surface animate-enter rounded-2xl px-6 py-7 sm:px-8 sm:py-8">
          <div className="flex flex-col justify-between gap-6 md:flex-row md:items-center">
            <div className="max-w-2xl">
              <div className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-[0.14em] text-brand-600"><Sparkles size={13} />Automation command center</div>
              <h1 className="text-heading mt-2 text-2xl font-bold tracking-[-0.025em] sm:text-[30px]">Build reliable journeys for every record.</h1>
              <p className="text-muted mt-2 max-w-xl text-sm leading-6">Design, test, publish, and operate durable Frappe automations from one visual workspace.</p>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 sm:gap-3">
              {metrics.map(({ label, value, icon: Icon, tone }) => (
                <div className="metric-card min-w-24 rounded-xl p-3.5" key={label}><Icon className={tone} size={15} /><strong className="text-heading mt-3 block text-xl leading-none">{value}</strong><span className="text-muted mt-1 block text-[10px] font-medium">{label}</span></div>
              ))}
            </div>
          </div>
        </section>

        {runtime && (
          <section className={`mt-4 grid gap-3 rounded-xl border p-4 shadow-sm md:grid-cols-[minmax(0,1fr)_auto] md:items-center ${runtime.healthy ? 'border-emerald-200 bg-emerald-50/75 dark:border-emerald-900 dark:bg-emerald-500/10' : 'border-amber-200 bg-amber-50/80 dark:border-amber-900 dark:bg-amber-500/10'}`} aria-label="Automation runtime health">
            <div className="flex min-w-0 items-start gap-3">
              <span className={`grid size-9 shrink-0 place-items-center rounded-xl ${runtime.healthy ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300' : 'bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300'}`}>{runtime.healthy ? <ShieldCheck size={17} /> : <AlertTriangle size={17} />}</span>
              <div><div className="flex flex-wrap items-center gap-2"><h2 className="text-heading text-xs font-bold">Automation runtime</h2><HelpTooltip label="Runtime health" content="Health includes Redis availability, outbox age and failures, recent failed runs, stale or orphaned active runs, open incidents, and open dead letters." /><Status value={runtime.enabled ? 'ACTIVE' : 'DISABLED'} /></div><p className="text-muted mt-1 text-[10px] leading-4">{runtime.enabled ? `${runtime.active_subscriptions} active event subscription${runtime.active_subscriptions === 1 ? '' : 's'} · durable outbox is authoritative` : 'Execution is safely disabled for rollout. Drafting and simulation remain available.'}{runtime.quarantined ? ` ${runtime.quarantined.toLocaleString()} unsafe legacy events are quarantined.` : ''}{runtime.reasons.length ? ` Attention: ${runtime.reasons.join(', ')}.` : ''}</p></div>
            </div>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 sm:gap-0 sm:divide-x divide-[var(--border-color)] rounded-lg border border-[var(--border-color)] bg-white/40 dark:bg-transparent px-2 py-2.5 text-center shadow-sm">
              <div className="px-3"><strong className="text-heading block text-sm">{runtime.outbox.PENDING}</strong><span className="text-light text-[8px] font-bold uppercase tracking-wider">Ready</span></div>
              <div className="px-3"><strong className="text-heading block text-sm">{runtime.outbox.PROCESSING}</strong><span className="text-light text-[8px] font-bold uppercase tracking-wider">Leased</span></div>
              <div className="px-3"><strong className={`block text-sm ${runtime.outbox.FAILED ? 'text-amber-600' : 'text-heading'}`}>{runtime.outbox.FAILED}</strong><span className="text-light text-[8px] font-bold uppercase tracking-wider">Retry</span></div>
              <div className="px-3"><strong className={`block text-sm ${runtime.outbox.DEAD ? 'text-red-600' : 'text-heading'}`}>{runtime.outbox.DEAD}</strong><span className="text-light text-[8px] font-bold uppercase tracking-wider">Dead</span></div>
            </div>
          </section>
        )}

        <section className="mt-7">
          <div className="mb-3 flex items-end justify-between gap-4">
			<div><h2 className="text-heading text-base font-bold">Your workflows</h2><p className="text-muted mt-0.5 text-xs">Drafts, active automations, and operational controls.</p></div>
			<div className="flex items-center gap-3"><div className="relative w-56"><Search className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--text-light)]" size={13} /><input className="frappe-control h-9 w-full pl-9 text-xs" type="search" value={workflowSearch} onChange={(event) => setWorkflowSearch(event.target.value)} placeholder="Search workflows…" /></div><span className="text-light text-[10px]">{rows.length} shown</span></div>
          </div>
          <div className="surface-flat overflow-hidden rounded-xl">
            {loading ? (
              <div className="grid min-h-52 place-items-center"><LoaderCircle className="animate-spin text-brand-500" /></div>
            ) : error ? (
              <div className="px-6 py-12 text-center"><AlertTriangle className="mx-auto text-red-500" size={22} /><h3 className="text-heading mt-3 text-sm font-bold">Unable to load workflows</h3><p className="text-muted mx-auto mt-1 max-w-md text-xs">{error}</p><button className={`${primary} mt-4`} onClick={() => void load()}>Try again</button></div>
            ) : rows.length ? (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[780px] text-left text-xs">
                  <thead className="bg-[var(--subtle-fg)] text-[9px] font-bold uppercase tracking-[0.11em] text-[var(--text-light)]"><tr><th className="px-5 py-3">Workflow</th><th className="px-5 py-3">Business object</th><th className="px-5 py-3">Status</th><th className="px-5 py-3">Last updated</th><th className="px-5 py-3">Controls</th><th className="w-12" /></tr></thead>
                  <tbody>{rows.map((row) => (
                    <tr className="table-row" key={row.name}>
                      <td className="px-5 py-4"><div className="flex items-center gap-3"><span className="grid size-9 shrink-0 place-items-center rounded-[10px] bg-brand-50 text-brand-600 dark:bg-brand-500/10"><Workflow size={16} /></span><div>{canBuild ? <Link className="text-heading font-bold hover:text-brand-600" to={`/${row.name}`}>{row.title}</Link> : <Link className="text-heading font-bold hover:text-brand-600" to={`/${row.name}/runs`}>{row.title}</Link>}<p className="text-light mt-0.5 text-[9px]">{row.name}</p></div></div></td>
                      <td className="px-5 py-4"><span className="rounded-md border border-[var(--border-color)] bg-[var(--subtle-fg)] px-2 py-1 text-[10px] font-semibold">{row.primary_doctype}</span></td>
                      <td className="px-5 py-4"><Status value={row.status} /></td>
                      <td className="text-muted px-5 py-4 text-[10px]">{formatDate(row.modified)}</td>
                      <td className="px-5 py-4"><div className="flex gap-1.5">{canBuild && <button className={secondary} onClick={() => void cloneWorkflow(row)} title="Create an independent draft with fresh node and edge IDs"><Copy size={13} />Clone</button>}{canOperate && row.active_version && <>{row.trigger_type === 'trigger.manual' ? <Link className={secondary} to={`/${row.name}/runs`} title="Enroll a record manually and inspect runs"><Play size={13} />Enroll</Link> : <Link className={secondary} to={`/${row.name}/enrollment`} title={row.trigger_type === 'trigger.schedule' ? 'Manage schedules and backfills' : 'Run version-pinned backfills'}><CalendarClock size={13} />{row.trigger_type === 'trigger.schedule' ? 'Schedules' : 'Backfill'}</Link>}{row.status === 'ACTIVE' ? <button className={secondary} title="Stop new enrollments while preserving resumable runtime state" onClick={() => void setWorkflowState(row.name, 'PAUSED')}><Pause size={13} />Pause</button> : row.status === 'PAUSED' ? <button className={primary} title="Revalidate the pinned version and resume execution" onClick={() => void setWorkflowState(row.name, 'ACTIVE')}><Play size={13} />Resume</button> : null}{row.status !== 'DISABLED' && <button className={ghost} title="Cancel active runs and timers; published history remains" onClick={() => void setWorkflowState(row.name, 'DISABLED')}><Ban size={13} />Disable</button>}</>}{canBuild && row.status === 'DRAFT' && !row.latest_version && (row.owner === window.frappe?.boot?.user || hasRole('System Manager')) && <button className="btn-core btn-ghost text-red-600 hover:!bg-red-50 dark:hover:!bg-red-500/10" title="Delete this never-published, never-executed draft" onClick={() => setDeleting(row)}><Trash2 size={13} />Delete</button>}</div></td>
                      <td><Link className="icon-button" aria-label={`Open ${row.title}`} to={canBuild ? `/${row.name}` : `/${row.name}/runs`}><ChevronRight size={17} /></Link></td>
                    </tr>
                  ))}</tbody>
                </table>
				{hasMoreWorkflows && <div className="border-t border-[var(--border-color)] p-3 text-center"><button className={secondary} disabled={loadingMore} onClick={() => void load(rows.length, true)}>{loadingMore && <LoaderCircle className="animate-spin" size={13} />}Load more workflows</button></div>}
              </div>
            ) : (
              <div className="px-6 py-16 text-center"><span className="magic-orb mx-auto"><Sparkles size={20} /></span><h3 className="text-heading mt-4 text-base font-bold">Your first automation starts here</h3><p className="text-muted mx-auto mt-1 max-w-sm text-xs leading-5">Choose a Frappe DocType, define an enrollment trigger, and build the journey visually.</p>{canBuild && <button className={`${primary} mt-5`} onClick={() => setCreating(true)}><Plus size={15} />Create workflow</button>}</div>
            )}
          </div>
        </section>
      </main>
      {creating && <CreateDialog close={() => setCreating(false)} created={(id) => navigate(`/${id}`)} />}
      {deleting && <DeleteWorkflowDialog workflow={deleting} close={() => setDeleting(undefined)} deleted={() => { setDeleting(undefined); void load() }} />}
    </div>
  )
}

function EditorHeader() {
  const doc = useWorkflowDocument()
  const history = useWorkflowHistory()
  const actions = useWorkflowActions()
  const canPublish = hasRole('Automation Publisher')
  const publication = doc.publication
  const triggerType = doc.graph?.nodes.find((node) => node.id === doc.graph?.start_node_id)?.type
  const showEnrollmentPage = triggerType !== 'trigger.manual'
  const enrollmentLabel = triggerType === 'trigger.schedule' ? 'Schedules' : 'Backfill'
  const publishedAndCurrent = publication.has_published_version && !publication.has_unpublished_changes && publication.state !== 'READY_TO_ACTIVATE'
  const publishLabel = publication.has_published_version
    ? publication.has_unpublished_changes
      ? 'Review changes & publish'
      : `Published v${publication.latest_version_no}`
    : 'Review & publish'
  return (
    <>
      <Header>
        <div className="flex min-w-0 items-center gap-3">
          <ProductBrand subtitle="Visual editor" />
          <span className="hidden h-7 w-px bg-[var(--border-color)] sm:block" />
          <Link to="/" className="icon-button hidden sm:grid" aria-label="Back to workflows"><ArrowLeft size={16} /></Link>
          <div className="min-w-0"><div className="flex items-center gap-2"><h1 className="text-heading truncate text-[13px] font-bold">{doc.title}</h1><Status value={doc.status} />{publication.has_published_version && <span className="status-pill border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-500/10 dark:text-emerald-300">Published v{publication.latest_version_no}</span>}{publication.has_unpublished_changes && publication.has_published_version && <span className="status-pill border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-900 dark:bg-amber-500/10 dark:text-amber-300">Draft changes</span>}</div><p className="text-light mt-0.5 truncate text-[9px]">{doc.graph?.primary_doctype} workflow · {doc.workflowId}{publication.has_unpublished_changes ? ` · publish as v${publication.next_version_no}` : publication.active_version_no ? ` · active v${publication.active_version_no}` : ''}</p></div>
        </div>
        <div className="flex items-center gap-1"><DeskLink /><ThemeToggle /></div>
      </Header>
      <nav className="hide-scrollbar relative z-30 flex min-h-[52px] items-center gap-4 overflow-x-auto border-b border-[var(--border-color)] bg-white/50 dark:bg-[#18212b]/50 backdrop-blur-md px-4 shadow-[0_2px_8px_rgb(25_39_51_/_0.035)] sm:px-5">
        <div className="flex h-[52px] shrink-0 items-center gap-1">
          <button className="relative flex h-full items-center gap-2 px-3 text-[11px] font-bold text-brand-600 after:absolute after:inset-x-2 after:bottom-0 after:h-0.5 after:rounded-full after:bg-brand-500"><Layers3 size={14} />Build</button>
          {publication.has_published_version ? <><Link className="btn-core btn-ghost" to={`/${doc.workflowId}/runs`}><History size={14} />{triggerType === 'trigger.manual' ? 'Enroll & runs' : 'Runs'}</Link>{showEnrollmentPage && <Link className="btn-core btn-ghost" to={`/${doc.workflowId}/enrollment`}><CalendarClock size={14} />{enrollmentLabel}</Link>}</> : <span className="ml-1 rounded-lg border border-dashed border-[var(--border-color)] px-2.5 py-1.5 text-[9.5px] font-semibold text-[var(--text-light)]">Publish to operate</span>}
          <button className="btn-core btn-ghost" title="Configure eligibility, goals, read semantics, and suppression rules" onClick={() => actions.toggle('policiesOpen', true)}><Settings2 size={14} />Policies</button>
          <button className="btn-core btn-ghost" title="Compare immutable published versions or restore one into the draft" onClick={() => actions.toggle('versionsOpen', true)}><GitCompareArrows size={14} />Versions</button>
        </div>
        <div className="ml-auto flex shrink-0 items-center gap-1.5">
          <button className="icon-button" disabled={!history.past.length} onClick={actions.undo} aria-label="Undo"><Undo2 size={15} /></button>
          <button className="icon-button" disabled={!history.future.length} onClick={actions.redo} aria-label="Redo"><Redo2 size={15} /></button>
          <span className="mx-1 h-5 w-px bg-[var(--border-color)]" />
          <span className={`mr-1 flex items-center gap-1.5 text-[10px] font-medium ${doc.dirty ? 'text-amber-600' : 'text-[var(--text-light)]'}`}><CircleDot size={11} />{doc.saving ? 'Saving…' : doc.dirty ? 'Unsaved changes' : `Saved · r${doc.serverRevision}`}</span>
          <button className={secondary} title="Persist this draft revision" onClick={() => void actions.save()} disabled={!doc.dirty}><Save size={14} />Save</button>
          <button className={secondary} title="Save pending edits, then validate that exact server revision" onClick={() => void actions.validate()}><FileCheck2 size={14} />Check</button>
          <button className={magic} title="Simulate selected nodes without changing live data" onClick={() => actions.toggle('simulationOpen', true)}><FlaskConical size={14} />Test</button>
          {canPublish && <button className={publishedAndCurrent ? secondary : primary} disabled={publishedAndCurrent} title={publishedAndCurrent ? 'The saved draft already matches the latest published version' : publication.state === 'READY_TO_ACTIVATE' ? `Review and activate published version ${publication.latest_version_no}` : `Review and publish this draft as version ${publication.next_version_no}`} onClick={() => actions.toggle('publishOpen', true)}>{publishedAndCurrent ? <CheckCircle2 size={14} /> : <Send size={14} />}{publishLabel}</button>}
        </div>
      </nav>
    </>
  )
}

function EditorPanels() {
  const doc = useWorkflowDocument()
  const editor = useWorkflowEditor()
  const actions = useWorkflowActions()
  const [record, setRecord] = useState('')
  const [reenrollment, setReenrollment] = useState<'NEVER' | 'AFTER_COMPLETION' | 'ALWAYS'>('NEVER')
  const [policyFields, setPolicyFields] = useState<FieldCatalogItem[]>([])
  const [versions, setVersions] = useState<Array<{ name: string; version_no: number; published_at: string; published_by: string }>>([])
  const [leftVersion, setLeftVersion] = useState('')
  const [rightVersion, setRightVersion] = useState('DRAFT')
  const [versionDiff, setVersionDiff] = useState<{ nodes: { added: string[]; removed: string[]; changed: string[] }; edges: { added: string[]; removed: string[]; changed: string[] }; settings_changed: boolean }>()
  const [suppressions, setSuppressions] = useState<Array<{ name: string; title: string; reason?: string; enabled: number; condition_json: string }>>([])
  const [suppressionTitle, setSuppressionTitle] = useState('')
  const [suppressionCondition, setSuppressionCondition] = useState<ConditionExpression>()
  const [panelError, setPanelError] = useState('')
  const [panelBusy, setPanelBusy] = useState('')
  const [preflight, setPreflight] = useState<RuntimePreflight>()
  useDialogA11y(editor.publishOpen, () => actions.toggle('publishOpen', false), 'Publish workflow')
  useDialogA11y(editor.policiesOpen, () => actions.toggle('policiesOpen', false), 'Workflow policies')
  useDialogA11y(editor.versionsOpen, () => actions.toggle('versionsOpen', false), 'Version comparison')
  useDialogA11y(doc.conflict, () => { void actions.resolveConflict('reload') }, 'Draft conflict')
  useEffect(() => {
    setReenrollment(doc.settings.reenrollment || 'NEVER')
  }, [doc.settings.reenrollment])
  useEffect(() => {
    if (!editor.policiesOpen || !doc.graph?.primary_doctype) return
    let active = true
    fetchFieldCatalog(doc.graph.primary_doctype, 'read', doc.workflowId).then((result) => {
      if (active) setPolicyFields(result.fields)
    }).catch((reason) => {
	  if (active) { setPolicyFields([]); setPanelError(reason instanceof Error ? reason.message : 'Unable to load policy fields') }
    })
    call<{ rows: Array<{ name: string; title: string; reason?: string; enabled: number; condition_json: string }> }>('list_suppressions', { workflow_id: doc.workflowId }).then((result) => {
      if (active) setSuppressions(result.rows)
    }).catch((reason) => { if (active) { setSuppressions([]); setPanelError(reason instanceof Error ? reason.message : 'Unable to load suppression rules') } })
    return () => { active = false }
  }, [doc.graph?.primary_doctype, doc.workflowId, editor.policiesOpen])
  useEffect(() => {
    if (!editor.versionsOpen) return
    call<{ rows: Array<{ name: string; version_no: number; published_at: string; published_by: string }> }>('get_versions', { workflow_id: doc.workflowId }).then((result) => {
      setVersions(result.rows)
      setLeftVersion((current) => current || result.rows[0]?.name || '')
    }).catch((reason) => { setVersions([]); setPanelError(reason instanceof Error ? reason.message : 'Unable to load versions') })
  }, [doc.workflowId, editor.versionsOpen])
  useEffect(() => {
    if (!editor.publishOpen) return
    let active = true
    setPanelError('')
    call<RuntimePreflight>('runtime_preflight', { workflow_id: doc.workflowId })
      .then((result) => { if (active) setPreflight(result) })
      .catch((reason) => { if (active) { setPreflight(undefined); setPanelError(reason instanceof Error ? reason.message : 'Runtime preflight failed') } })
    return () => { active = false }
  }, [doc.workflowId, editor.publishOpen])
  const compareSelectedVersions = async () => {
    if (!leftVersion) return
    try { setVersionDiff(await call('compare_versions', { workflow_id: doc.workflowId, left_version: leftVersion, right_version: rightVersion })) }
    catch (reason) { setVersionDiff(undefined); setPanelError(reason instanceof Error ? reason.message : 'Unable to compare versions') }
  }
  const restoreSelectedVersion = async () => {
    if (!leftVersion || panelBusy) return
    setPanelBusy('restore')
    setPanelError('')
    try {
      const revision = await actions.save()
      if (revision < 0) return
      await call('restore_version', mutationEnvelope(doc.workflowId, { version_id: leftVersion }, revision), true)
      await actions.reload()
      actions.toggle('versionsOpen', false)
    } catch (reason) {
      setPanelError(reason instanceof Error ? reason.message : 'Unable to restore this version')
    } finally {
      setPanelBusy('')
    }
  }
  const publishNow = async () => {
    if (panelBusy) return
    setPanelBusy('publish')
    setPanelError('')
    try {
	  const revision = await actions.save()
	  if (revision < 0) return
	  const latestPreflight = await call<RuntimePreflight>('runtime_preflight', { workflow_id: doc.workflowId })
	  setPreflight(latestPreflight)
      await actions.publish(true, reenrollment)
	} catch (reason) {
	  setPanelError(reason instanceof Error ? reason.message : 'Unable to complete publish preflight')
    } finally {
      setPanelBusy('')
    }
  }
  const updatePolicyCondition = (key: 'goal_condition' | 'eligibility_condition', value: ConditionExpression | null) => {
    actions.updateSettings({ ...doc.settings, [key]: value })
  }
  const saveSuppression = async () => {
    if (!suppressionTitle || !suppressionCondition) return
	setPanelBusy('suppression-save')
	setPanelError('')
	try {
	  await call('save_suppression', mutationEnvelope(doc.workflowId, { rule: { title: suppressionTitle, enabled: 1, priority: 100, condition: suppressionCondition } }), true)
	  setSuppressionTitle('')
	  setSuppressionCondition(undefined)
	  const result = await call<{ rows: Array<{ name: string; title: string; reason?: string; enabled: number; condition_json: string }> }>('list_suppressions', { workflow_id: doc.workflowId })
	  setSuppressions(result.rows)
	} catch (reason) {
	  setPanelError(reason instanceof Error ? reason.message : 'Unable to save suppression rule')
	} finally {
	  setPanelBusy('')
	}
  }
  const deleteSuppression = async (ruleId: string) => {
	if (panelBusy) return
	setPanelBusy(`suppression-delete:${ruleId}`)
	setPanelError('')
	try {
	  await call('delete_suppression', { workflow_id: doc.workflowId, rule_id: ruleId }, true)
	  setSuppressions((current) => current.filter((row) => row.name !== ruleId))
	} catch (reason) {
	  setPanelError(reason instanceof Error ? reason.message : 'Unable to delete suppression rule')
	} finally {
	  setPanelBusy('')
	}
  }
  const loadRecords = useCallback((search: string): Promise<ComboboxOption[]> => {
    if (!doc.graph?.primary_doctype) return Promise.resolve([])
    return searchLink(doc.graph.primary_doctype, search).then((rows) => rows.map((row) => ({ value: row.value, label: row.label || row.value, description: row.description })))
  }, [doc.graph?.primary_doctype])
  return (
    <>
	  {panelError && <div className="fixed left-1/2 top-20 z-[70] flex max-w-lg -translate-x-1/2 items-start gap-2 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-xs text-red-700 shadow-lg dark:border-red-900 dark:bg-red-950" role="alert"><AlertTriangle className="mt-0.5 shrink-0" size={14} /><span className="min-w-0 flex-1">{panelError}</span><button type="button" className="icon-button !size-6 shrink-0" onClick={() => setPanelError('')} aria-label="Dismiss error"><X size={13} /></button></div>}
      {editor.validationOpen && (
        <aside className="surface absolute bottom-4 left-4 right-4 z-30 max-h-[420px] overflow-hidden rounded-xl md:left-[296px] md:right-auto md:w-[430px]">
          <div className="flex items-start justify-between border-b border-[var(--border-color)] px-4 py-3"><div><div className="flex items-center gap-2"><span className="grid size-7 place-items-center rounded-lg bg-brand-50 text-brand-600 dark:bg-brand-500/10"><FileCheck2 size={14} /></span><h2 className="text-heading text-xs font-bold">Workflow check</h2></div><p className="text-muted ml-9 mt-0.5 text-[10px]">Publish readiness and configuration issues</p></div><button className="icon-button !size-7" onClick={() => actions.toggle('validationOpen', false)} aria-label="Close workflow check"><X size={15} /></button></div>
          <div className="max-h-72 overflow-y-auto p-3">{doc.validation.length ? <ul className="space-y-2">{doc.validation.map((issue, index) => <li className="rounded-lg border border-red-200 bg-red-50 p-3 text-[11px] leading-4 text-red-700 dark:border-red-900 dark:bg-red-500/10 dark:text-red-300" key={index}><strong>{issue.code}</strong><span className="mx-1">·</span>{issue.message}{issue.node_id && <button className="ml-2 font-bold underline" onClick={() => actions.select(issue.node_id)}>Open step</button>}</li>)}</ul> : <p className="flex items-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-[11px] text-emerald-700 dark:border-emerald-900 dark:bg-emerald-500/10 dark:text-emerald-300"><CheckCircle2 size={15} />Everything looks ready.</p>}</div>
          <div className="border-t border-[var(--border-color)] p-3"><button className={`${secondary} w-full`} onClick={() => void actions.validate()}><FileCheck2 size={14} />Check saved draft again</button></div>
        </aside>
      )}
      {editor.simulationOpen && (
        <aside className="surface absolute bottom-4 left-4 right-4 top-[126px] z-40 flex flex-col overflow-hidden rounded-xl md:left-auto md:right-[356px] md:w-[410px]">
          <div className="relative overflow-hidden border-b border-[var(--border-color)] px-5 py-4"><div className="absolute right-0 top-0 size-28 rounded-full bg-magic-500/10 blur-2xl" /><div className="relative flex justify-between"><div><div className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-[0.12em] text-magic-600"><Sparkles size={12} />Safe simulation</div><h2 className="text-heading mt-1 text-base font-bold">Test this workflow</h2><p className="text-muted mt-1 text-[10px]">See the predicted path without changing data.</p></div><button className="icon-button" onClick={() => actions.toggle('simulationOpen', false)} aria-label="Close simulation"><X size={17} /></button></div></div>
          <div className="border-b border-[var(--border-color)] p-4"><label className="text-heading text-[11px] font-semibold">{doc.graph?.primary_doctype} record</label><div className="mt-1.5 grid grid-cols-[minmax(0,1fr)_auto] gap-2"><AsyncCombobox ariaLabel={`${doc.graph?.primary_doctype} record`} value={record} onChange={setRecord} loadOptions={loadRecords} placeholder={`Search ${doc.graph?.primary_doctype || 'record'}…`} /><button className={magic} disabled={!record} onClick={() => void actions.simulate(record)}><Play size={14} />Run test</button>{editor.selectedNodeId && <button className={secondary} disabled={!record} onClick={() => void actions.testNode(record)}><FlaskConical size={14} />Test selected</button>}</div></div>
          <div className="min-h-0 flex-1 overflow-y-auto p-4"><SimulationOutcome result={editor.simulation} onSelectNode={actions.select} /></div>
        </aside>
      )}
      {editor.publishOpen && (
        <div className="dialog-backdrop fixed inset-0 z-50 grid place-items-center p-4" role="dialog" aria-modal="true" aria-labelledby="publish-workflow-title" onClick={(e) => { if (e.target === e.currentTarget) actions.toggle('publishOpen', false) }}><div className="dialog-card w-full max-w-lg overflow-hidden rounded-2xl"><div className="hero-glow relative border-b border-[var(--border-color)] px-5 py-5 sm:px-6"><button className="icon-button absolute right-4 top-4" aria-label="Close publish dialog" onClick={() => actions.toggle('publishOpen', false)}><X size={17} /></button><span className="grid size-10 place-items-center rounded-xl bg-brand-50 text-brand-600 dark:bg-brand-500/10"><Send size={18} /></span><h2 id="publish-workflow-title" className="text-heading mt-4 text-xl font-bold tracking-tight">Review changes and publish v{doc.publication.next_version_no}</h2><p className="text-muted mt-1.5 text-xs leading-5">Create an immutable version. A healthy runtime activates it immediately; otherwise it remains safely ready to activate. Existing runs stay pinned to their original version.</p></div><div className="space-y-3 p-5 sm:p-6"><div className="grid grid-cols-1 gap-3 sm:grid-cols-3"><div className="rounded-lg border border-[var(--border-color)] bg-[var(--subtle-fg)] p-3"><p className="text-light text-[9px] font-bold uppercase tracking-wider">New version</p><p className="text-heading mt-1 text-xs font-bold">v{doc.publication.next_version_no}</p></div><div className="rounded-lg border border-[var(--border-color)] bg-[var(--subtle-fg)] p-3"><p className="text-light text-[9px] font-bold uppercase tracking-wider">Business object</p><p className="text-heading mt-1 text-xs font-bold">{doc.graph?.primary_doctype}</p></div><div className="rounded-lg border border-[var(--border-color)] bg-[var(--subtle-fg)] p-3"><p className="text-light text-[9px] font-bold uppercase tracking-wider">Data semantics</p><p className="text-heading mt-1 text-xs font-bold">{doc.settings.read_mode === 'ENROLLMENT_SNAPSHOT' ? 'Pinned snapshot' : 'Live record'}</p></div></div><label className="text-heading block text-xs font-semibold">Re-enrollment policy<select className={`${field} mt-1.5`} value={reenrollment} onChange={(event) => { const value = event.target.value as typeof reenrollment; setReenrollment(value); actions.updateSettings({ ...doc.settings, reenrollment: value }) }}><option value="NEVER">Only once per workflow and record</option><option value="AFTER_COMPLETION">Again after the previous run finishes</option><option value="ALWAYS">Every distinct matching event</option></select><span className="text-muted mt-1 block text-[10px] font-normal">The event id remains the idempotency boundary in every mode.</span></label>{preflight && <div className="rounded-lg border border-[var(--border-color)] bg-[var(--subtle-fg)] p-3"><div className="flex items-center justify-between"><div className="flex items-center gap-1.5"><strong className="text-heading text-[11px]">Candidate runtime preflight</strong><HelpTooltip label="Preflight" content="Checks the saved candidate draft, execution user, workers, Redis, queues, failures, incidents, dead letters, and external providers. An unhealthy runtime holds the published version inactive." /></div><Status value={preflight.ready ? 'READY' : 'ATTENTION'} /></div><p className="text-muted mt-1 text-[10px]">{preflight.workers} worker{preflight.workers === 1 ? '' : 's'} detected{!preflight.ready ? ' · publication will be held for later activation' : ''}</p>{preflight.issues.length > 0 && <ul className="mt-2 space-y-1 text-[10px] text-amber-700 dark:text-amber-300">{preflight.issues.map((issue) => <li key={issue.code}><strong>{issue.code}:</strong> {issue.message}</li>)}</ul>}<div className="mt-2 grid grid-cols-3 gap-1.5">{(['email', 'sms', 'webhook'] as const).map((transport) => <div className="rounded-md border border-[var(--border-color)] bg-white/50 p-2 text-center dark:bg-white/5" title={preflight.transports[transport].message} key={transport}><span className="text-light block text-[8px] font-bold uppercase">{transport}</span><span className={`mt-0.5 block text-[9px] font-semibold ${preflight.transports[transport].configured ? 'text-emerald-600' : 'text-amber-600'}`}>{preflight.transports[transport].configured ? 'Configured' : 'Not configured'}</span></div>)}</div></div>}{doc.validation.length > 0 && doc.validationFresh ? <p className="flex gap-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-[11px] leading-4 text-amber-800 dark:border-amber-900 dark:bg-amber-500/10 dark:text-amber-300"><AlertTriangle className="shrink-0" size={15} />Current check issues must be resolved. The saved revision will be checked again when you publish.</p> : <p className="flex gap-2 rounded-lg border border-blue-200 bg-blue-50 p-3 text-[11px] text-blue-700 dark:border-blue-900 dark:bg-blue-500/10 dark:text-blue-300"><ShieldCheck size={15} />Save and Check run automatically against the exact revision before publication.</p>}<div className="flex justify-end gap-2 pt-2"><button className={secondary} onClick={() => actions.toggle('publishOpen', false)}>Keep editing</button><button className={primary} disabled={Boolean(panelBusy) || (!doc.publication.has_unpublished_changes && doc.publication.state !== 'READY_TO_ACTIVATE')} onClick={() => void publishNow()}>{panelBusy === 'publish' ? <LoaderCircle className="animate-spin" size={14} /> : <Send size={14} />}{preflight?.ready ? `Save, check & publish v${doc.publication.next_version_no}` : `Force publish v${doc.publication.next_version_no} (ignore warnings)`}</button></div></div></div></div>
      )}
      {editor.policiesOpen && (
        <div className="dialog-backdrop fixed inset-0 z-50 grid place-items-center p-4" onClick={(e) => { if (e.target === e.currentTarget) actions.toggle('policiesOpen', false) }}><div className="dialog-card flex max-h-[92vh] w-full max-w-5xl flex-col overflow-hidden rounded-2xl"><div className="hero-glow shrink-0 flex items-start justify-between border-b border-[var(--border-color)] px-5 py-5 sm:px-6"><div><div className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-[0.12em] text-brand-600"><Settings2 size={13} />Runtime contract</div><h2 className="text-heading mt-1 text-xl font-bold">Eligibility, goals, and data semantics</h2><p className="text-muted mt-1 text-xs">Draft policies are version-pinned; suppression rules are centrally enforced at enrollment.</p></div><button className="icon-button" onClick={() => actions.toggle('policiesOpen', false)}><X size={17} /></button></div><div className="min-h-0 flex-1 overflow-y-auto p-5 sm:p-6"><div className="grid gap-5 lg:grid-cols-2"><section className="rounded-xl border border-[var(--border-color)] bg-white/60 dark:bg-white/5 backdrop-blur-md p-4"><h3 className="text-heading text-sm font-bold">Record reads</h3><p className="text-muted mt-1 text-[10px] leading-4">Choose whether conditions and value bindings see live values or the safe scalar snapshot captured at enrollment.</p><select className={`${field} mt-4`} value={doc.settings.read_mode || 'CURRENT'} onChange={(event) => actions.updateSettings({ ...doc.settings, read_mode: event.target.value as 'CURRENT' | 'ENROLLMENT_SNAPSHOT' })}><option value="CURRENT">Live record values at every step</option><option value="ENROLLMENT_SNAPSHOT">Enrollment snapshot for the whole run</option></select><label className="mt-4 flex items-start gap-3 rounded-lg border border-[var(--border-color)] bg-[var(--subtle-fg)] p-3"><input className="mt-0.5" type="checkbox" checked={Boolean(doc.settings.unenroll_when_ineligible)} onChange={(event) => actions.updateSettings({ ...doc.settings, unenroll_when_ineligible: event.target.checked })} /><span><strong className="text-heading block text-[11px]">Stop when no longer eligible</strong><span className="text-muted mt-0.5 block text-[10px] leading-4">Before each node, re-evaluate the explicit eligibility condition—or the trigger condition when none is supplied.</span></span></label></section><section className="rounded-xl border border-[var(--border-color)] bg-white/60 dark:bg-white/5 backdrop-blur-md p-4"><div className="flex items-start justify-between"><div><h3 className="text-heading text-sm font-bold">Goal condition</h3><p className="text-muted mt-1 text-[10px] leading-4">Records already at the goal are not enrolled; active runs complete before their next node when the goal becomes true.</p></div>{doc.settings.goal_condition && <button className={ghost} onClick={() => updatePolicyCondition('goal_condition', null)}>Clear</button>}</div><div className="mt-4"><PolicyConditionEditor value={doc.settings.goal_condition} fields={policyFields} primaryDoctype={doc.graph?.primary_doctype} onChange={(value) => updatePolicyCondition('goal_condition', value)} /></div></section><section className="rounded-xl border border-[var(--border-color)] bg-white/60 dark:bg-white/5 backdrop-blur-md p-4 lg:col-span-2"><div className="flex items-start justify-between"><div><h3 className="text-heading text-sm font-bold">Explicit eligibility condition</h3><p className="text-muted mt-1 text-[10px] leading-4">Optional. Leave clear to reuse the published trigger condition.</p></div>{doc.settings.eligibility_condition && <button className={ghost} onClick={() => updatePolicyCondition('eligibility_condition', null)}>Use trigger</button>}</div><div className="mt-4"><PolicyConditionEditor value={doc.settings.eligibility_condition} fields={policyFields} primaryDoctype={doc.graph?.primary_doctype} onChange={(value) => updatePolicyCondition('eligibility_condition', value)} /></div></section><section className="rounded-xl border border-[var(--border-color)] bg-white/60 dark:bg-white/5 backdrop-blur-md p-4 lg:col-span-2"><div className="flex items-start justify-between gap-4"><div><h3 className="text-heading text-sm font-bold">Suppression rules</h3><p className="text-muted mt-1 text-[10px] leading-4">Matching records are rejected before a run or effect is created, with a durable decision reason.</p></div><span className="status-pill border-[var(--border-color)] bg-[var(--subtle-fg)] text-[var(--text-muted)]">{suppressions.length} rules</span></div>{suppressions.length > 0 && <div className="mt-3 grid gap-2">{suppressions.map((row) => <div className="flex items-center justify-between rounded-lg border border-[var(--border-color)] bg-[var(--subtle-fg)] p-3" key={row.name}><div><strong className="text-heading block text-[11px]">{row.title}</strong><span className="text-light text-[9px]">{row.name} · {row.enabled ? 'Enabled' : 'Disabled'}</span></div>{hasRole('Automation Publisher') && <button className="icon-button hover:!text-red-600" disabled={Boolean(panelBusy)} onClick={() => void deleteSuppression(row.name)} aria-label={`Delete ${row.title}`}><Trash2 size={13} /></button>}</div>)}</div>}{hasRole('Automation Publisher') && <div className="mt-4 rounded-xl border border-dashed border-[var(--border-color)] p-4"><input className={field} placeholder="Rule name, e.g. Exclude archived leads" value={suppressionTitle} onChange={(event) => setSuppressionTitle(event.target.value)} /><div className="mt-3"><PolicyConditionEditor value={suppressionCondition} fields={policyFields} primaryDoctype={doc.graph?.primary_doctype} onChange={setSuppressionCondition} /></div><button className={`${primary} mt-3`} disabled={!suppressionTitle || !suppressionCondition || Boolean(panelBusy)} onClick={() => void saveSuppression()}>{panelBusy === 'suppression-save' ? <LoaderCircle className="animate-spin" size={13} /> : <Plus size={13} />}Add suppression rule</button></div>}</section></div></div><div className="flex items-center justify-between border-t border-[var(--border-color)] px-5 py-4 sm:px-6"><p className="text-muted text-[10px]">Server metadata and execution-user permissions are validated on save and publish.</p><button className={primary} onClick={() => actions.toggle('policiesOpen', false)}>Done</button></div></div></div>
      )}
      {editor.versionsOpen && (
        <div className="dialog-backdrop fixed inset-0 z-50 grid place-items-center p-4" onClick={(e) => { if (e.target === e.currentTarget) actions.toggle('versionsOpen', false) }}><div className="dialog-card w-full max-w-3xl overflow-hidden rounded-2xl"><div className="hero-glow flex items-start justify-between border-b border-[var(--border-color)] px-5 py-5 sm:px-6"><div><div className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-[0.12em] text-magic-600"><GitCompareArrows size={13} />Immutable history</div><h2 className="text-heading mt-1 text-xl font-bold">Visual version diff</h2><p className="text-muted mt-1 text-xs">Compare stable node, edge, and policy identities without changing either version.</p></div><button className="icon-button" onClick={() => actions.toggle('versionsOpen', false)}><X size={17} /></button></div><div className="p-5 sm:p-6"><div className="grid gap-3 sm:grid-cols-[1fr_1fr_auto]"><label className="text-heading text-[10px] font-semibold">From<select className={`${field} mt-1.5`} value={leftVersion} onChange={(event) => setLeftVersion(event.target.value)}><option value="">Choose version</option>{versions.map((version) => <option value={version.name} key={version.name}>v{version.version_no} · {version.name}</option>)}</select></label><label className="text-heading text-[10px] font-semibold">To<select className={`${field} mt-1.5`} value={rightVersion} onChange={(event) => setRightVersion(event.target.value)}><option value="DRAFT">Current draft</option>{versions.map((version) => <option value={version.name} key={version.name}>v{version.version_no} · {version.name}</option>)}</select></label><button className={`${primary} self-end`} disabled={!leftVersion || leftVersion === rightVersion} onClick={() => void compareSelectedVersions()}><GitCompareArrows size={13} />Compare</button><button className={`${secondary} self-end`} disabled={!leftVersion || Boolean(panelBusy)} onClick={() => void restoreSelectedVersion()}>{panelBusy === 'restore' ? <LoaderCircle className="animate-spin" size={13} /> : <History size={13} />}Restore to draft</button></div>{versionDiff ? <div className="mt-5 grid gap-3 sm:grid-cols-3">{(['added', 'removed', 'changed'] as const).map((kind) => <div className="rounded-xl border border-[var(--border-color)] bg-[var(--subtle-fg)] p-4" key={kind}><p className="text-light text-[9px] font-bold uppercase tracking-wider">{kind}</p><strong className="text-heading mt-2 block text-2xl">{versionDiff.nodes[kind].length + versionDiff.edges[kind].length}</strong><p className="text-muted mt-1 text-[10px]">{versionDiff.nodes[kind].length} nodes · {versionDiff.edges[kind].length} edges</p></div>)}<div className="rounded-xl border border-[var(--border-color)] bg-white/60 dark:bg-white/5 backdrop-blur-md p-4 sm:col-span-3"><div className="flex items-center justify-between"><strong className="text-heading text-xs">Published policies</strong><Status value={versionDiff.settings_changed ? 'CHANGED' : 'UNCHANGED'} /></div>{(['added', 'removed', 'changed'] as const).map((kind) => versionDiff.nodes[kind].length ? <p className="text-muted mt-2 text-[10px]" key={kind}><strong className="text-heading capitalize">{kind}:</strong> {versionDiff.nodes[kind].join(', ')}</p> : null)}</div></div> : <div className="mt-8 rounded-xl border border-dashed border-[var(--border-color)] p-10 text-center"><History className="text-light mx-auto" size={22} /><p className="text-heading mt-3 text-xs font-bold">Choose two revisions to inspect</p><p className="text-muted mt-1 text-[10px]">The server compares canonical immutable graphs and settings.</p></div>}</div></div></div>
      )}
      {doc.conflict && (
        <div className="dialog-backdrop fixed inset-0 z-[60] grid place-items-center p-4"><div className="dialog-card relative w-full max-w-lg rounded-2xl p-5 sm:p-6"><button className="icon-button absolute right-4 top-4" onClick={() => void actions.resolveConflict('reload')}><X size={17} /></button><span className="grid size-10 place-items-center rounded-xl bg-amber-50 text-amber-600 dark:bg-amber-500/10"><AlertTriangle size={19} /></span><h2 className="text-heading mt-4 text-xl font-bold">This draft changed elsewhere</h2><p className="text-muted mt-2 text-xs leading-5">Editing is locked to protect both versions. Download your local graph or reload the authoritative server revision.</p><div className="mt-6 flex justify-end gap-2"><button className={secondary} onClick={() => void actions.resolveConflict('download')}>Download local JSON</button><button className={primary} onClick={() => void actions.resolveConflict('reload')}>Reload server</button></div></div></div>
      )}
    </>
  )
}

function Editor() {
  const doc = useWorkflowDocument()
  const actions = useWorkflowActions()
  const primaryDoctypeIssue = doc.validation.find((issue) => issue.code === 'PRIMARY_DOCTYPE_UNAVAILABLE' || issue.code === 'DOCTYPE_MISMATCH')
  if (doc.loading) return <div className="app-shell grid h-screen place-items-center"><div className="text-center"><span className="magic-orb mx-auto"><LoaderCircle className="animate-spin" size={20} /></span><p className="text-muted mt-3 text-xs">Opening workflow canvas…</p></div></div>
  if (!doc.graph) return <div className="app-shell grid h-screen place-items-center p-6"><div className="surface max-w-md rounded-2xl p-7 text-center"><span className="mx-auto grid size-11 place-items-center rounded-xl bg-red-50 text-red-600 dark:bg-red-500/10"><AlertTriangle size={20} /></span><h1 className="text-heading mt-4 text-lg font-bold">Unable to open this workflow</h1><p className="text-muted mt-2 text-xs leading-5">{doc.error || 'The workflow draft could not be loaded.'}</p><div className="mt-5 flex justify-center gap-2"><Link className={secondary} to="/"><ArrowLeft size={14} />All workflows</Link><button className={primary} onClick={() => void actions.reload()}><Play size={14} />Try again</button></div></div></div>
  return <div className="app-shell relative flex h-screen flex-col overflow-hidden"><EditorHeader />{primaryDoctypeIssue && <div className="relative z-20 flex items-center justify-between gap-4 border-b border-amber-200 bg-amber-50 px-5 py-2.5 text-[11px] text-amber-900 dark:border-amber-900 dark:bg-amber-500/10 dark:text-amber-200"><span className="flex min-w-0 items-center gap-2"><AlertTriangle className="shrink-0" size={15} /><span><strong>This workflow needs a supported business object.</strong> {primaryDoctypeIssue.message} Its existing draft stays available for inspection, but it cannot be published.</span></span><Link className={`${secondary} shrink-0`} to="/"><Plus size={13} />Create replacement</Link></div>}{doc.error && <div className="absolute left-1/2 top-[122px] z-50 flex max-w-lg -translate-x-1/2 items-center gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-[10.5px] font-medium text-red-700 shadow-lg dark:border-red-900 dark:bg-red-500/10 dark:text-red-300"><AlertTriangle size={13} />{doc.error}</div>}<div className="relative grid min-h-0 flex-1 grid-cols-[280px_minmax(0,1fr)_360px] max-xl:grid-cols-[240px_minmax(0,1fr)_320px] max-lg:grid-cols-1 max-lg:overflow-hidden"><NodeCatalog /><WorkflowCanvas /><Inspector /></div><EditorPanels /></div>
}

export function WorkflowEditorPage() {
  const { workflowId = '' } = useParams()
  return <WorkflowProvider workflowId={workflowId}><Editor /></WorkflowProvider>
}

export function RunsPage() {
  const { workflowId = '' } = useParams()
  const [rows, setRows] = useState<RunSummary[]>([])
  const [recordName, setRecordName] = useState('')
  const [recordFilter, setRecordFilter] = useState('')
  const [appliedRecordFilter, setAppliedRecordFilter] = useState('')
  const [workflow, setWorkflow] = useState<WorkflowLookup>()
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [enrollmentNotice, setEnrollmentNotice] = useState<{ enrolled: boolean; message: string; runId?: string }>()
  const [enrolling, setEnrolling] = useState(false)
  const [page, setPage] = useState(0)
  const [hasMore, setHasMore] = useState(false)
  const [totalRuns, setTotalRuns] = useState(0)
  const requestSequence = useRef(0)
  const pageLength = 50
  const load = useCallback(async (pageNum = 0, signal?: AbortSignal) => {
    const sequence = ++requestSequence.current
    setLoading(true)
    setError('')
    try {
      const value = await call<{ rows: RunSummary[]; workflow: WorkflowLookup; has_more?: boolean; total_count: number }>('list_runs', { workflow_id: workflowId, record_name: appliedRecordFilter || undefined, start: pageNum * pageLength, page_length: pageLength }, false, signal)
      if (sequence !== requestSequence.current) return
      setRows(value.rows)
      setWorkflow(value.workflow)
      setHasMore(Boolean(value.has_more))
      setTotalRuns(value.total_count)
      setPage(pageNum)
    } catch (reason) {
      if (!signal?.aborted && sequence === requestSequence.current) setError(reason instanceof Error ? reason.message : 'Unable to load workflow runs')
    } finally {
      if (sequence === requestSequence.current) setLoading(false)
    }
  }, [workflowId, appliedRecordFilter])
  useEffect(() => {
	const controller = new AbortController()
	void load(0, controller.signal)
	return () => controller.abort()
  }, [load])
  useEffect(() => {
	const timer = window.setTimeout(() => setAppliedRecordFilter(recordFilter.trim()), 300)
	return () => window.clearTimeout(timer)
  }, [recordFilter])
  const loadRecords = useCallback((search: string): Promise<ComboboxOption[]> => {
    if (!workflow?.primary_doctype) return Promise.resolve([])
    return searchLink(workflow.primary_doctype, search).then((rows) => rows.map((row) => ({ value: row.value, label: row.label || row.value, description: row.description })))
  }, [workflow?.primary_doctype])
  const manualEnroll = async () => {
    if (!recordName) return
    setEnrolling(true)
    setError('')
    setEnrollmentNotice(undefined)
    try {
      const result = await call<ManualEnrollmentResult>('enroll_manual', mutationEnvelope(workflowId, { record_name: recordName }), true)
      if (result.enrolled && result.run_id) {
        setEnrollmentNotice({ enrolled: true, runId: result.run_id, message: `${recordName} enrolled successfully.` })
        setRecordName('')
        await load()
      } else {
        setEnrollmentNotice({ enrolled: false, message: `${recordName} was not enrolled. Its eligibility, suppression, or re-enrollment policy rejected this attempt.` })
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to enroll this record')
    } finally {
      setEnrolling(false)
    }
  }
  const isManualWorkflow = workflow?.trigger_type === 'trigger.manual'
  const manualReady = isManualWorkflow && workflow?.status === 'ACTIVE' && Boolean(workflow.active_version) && workflow.runtime_allowed !== false
  return (
    <div className="app-shell">
      <Header><div className="flex items-center gap-3"><ProductBrand subtitle="Run operations" /><span className="hidden h-7 w-px bg-[var(--border-color)] sm:block" /><Link className="icon-button" to={`/${workflowId}`} aria-label="Back to editor"><ArrowLeft size={16} /></Link><div className="hidden sm:block"><h1 className="text-heading text-xs font-bold">Run history</h1><p className="text-light text-[9px]">{workflowId}</p></div></div><div className="flex items-center gap-1">{workflow?.trigger_type !== 'trigger.manual' && <Link className={secondary} to={`/${workflowId}/enrollment`}><CalendarClock size={13} /><span className="hidden sm:inline">Enrollment</span></Link>}<DeskLink /><ThemeToggle /></div></Header>
      <main className="mx-auto max-w-7xl px-4 py-7 sm:px-6 lg:px-8">
        <div className="mb-5 flex items-end justify-between"><div><div className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-[0.13em] text-brand-600"><Activity size={13} />Operations</div><h2 className="text-heading mt-1 text-2xl font-bold tracking-tight">Workflow runs</h2><p className="text-muted mt-1 text-xs">Inspect enrollments, execution state, and exact node history.</p></div><span className="metric-card rounded-lg px-3 py-2 text-[10px] font-semibold"><strong className="text-heading mr-1 text-base">{totalRuns}</strong> runs</span></div>
        <div className="mb-4 flex items-center gap-2">
          <div className="relative flex-1 max-w-xs">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--text-light)]" size={14} />
            <input className="frappe-control h-9 w-full pl-9 text-xs" placeholder="Filter by record name…" value={recordFilter} onChange={(e) => setRecordFilter(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && setAppliedRecordFilter(recordFilter.trim())} />
          </div>
          <button className={secondary} onClick={() => setAppliedRecordFilter(recordFilter.trim())}>Filter</button>
        </div>
        {isManualWorkflow && <section className="hero-glow surface mb-5 flex flex-col justify-between gap-4 rounded-xl p-5 md:flex-row md:items-center"><div className="flex items-center gap-3"><span className="grid size-10 place-items-center rounded-xl bg-brand-50 text-brand-600 dark:bg-brand-500/10"><Play size={17} /></span><div><h3 className="text-heading text-sm font-bold">Enroll a record manually</h3><p className="text-muted mt-0.5 text-[10px]">The published re-enrollment policy and idempotency key are enforced server-side.</p>{workflow && !manualReady && <p className="mt-1 text-[10px] font-semibold text-amber-700 dark:text-amber-300">Activate a published workflow and enable automation before enrolling.</p>}</div></div><div className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto] gap-2 md:w-[470px]"><AsyncCombobox ariaLabel={`${workflow?.primary_doctype || 'Workflow'} record`} value={recordName} onChange={setRecordName} loadOptions={loadRecords} disabled={!workflow?.primary_doctype || !manualReady || enrolling} placeholder={workflow ? `Search ${workflow.primary_doctype} records…` : 'Loading record catalog…'} /><button className={primary} disabled={!recordName || !manualReady || enrolling} onClick={() => void manualEnroll()}>{enrolling ? <LoaderCircle className="animate-spin" size={14} /> : <Play size={14} />}{enrolling ? 'Enrolling…' : 'Enroll'}</button></div></section>}
        {enrollmentNotice && <div aria-live="polite" className={`mb-4 flex items-center justify-between gap-3 rounded-lg border px-4 py-3 text-xs ${enrollmentNotice.enrolled ? 'border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-500/10 dark:text-emerald-300' : 'border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-900 dark:bg-amber-500/10 dark:text-amber-300'}`}><span className="flex items-center gap-2">{enrollmentNotice.enrolled ? <CheckCircle2 size={14} /> : <AlertTriangle size={14} />}{enrollmentNotice.message}</span><div className="flex items-center gap-2">{enrollmentNotice.runId && <Link className={secondary} to={`/runs/${enrollmentNotice.runId}`}>View run</Link>}<button type="button" className="icon-button !size-7" onClick={() => setEnrollmentNotice(undefined)} aria-label="Dismiss enrollment result"><X size={13} /></button></div></div>}
        {error && <div className="mb-4 flex items-center justify-between gap-3 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-xs text-red-700 dark:border-red-900 dark:bg-red-500/10 dark:text-red-300"><span className="flex items-center gap-2"><AlertTriangle size={14} />{error}</span><button className={secondary} onClick={() => void load()}>Retry</button></div>}
        <section className="surface-flat overflow-hidden rounded-xl">{loading ? <div className="grid min-h-52 place-items-center"><LoaderCircle className="animate-spin text-brand-500" /></div> : rows.length ? <div className="overflow-x-auto"><table className="w-full min-w-[760px] text-left text-xs"><thead className="bg-[var(--subtle-fg)] text-[9px] font-bold uppercase tracking-[0.11em] text-[var(--text-light)]"><tr><th className="px-5 py-3">Run</th><th className="px-5 py-3">Record</th><th className="px-5 py-3">Source</th><th className="px-5 py-3">Status</th><th className="px-5 py-3">Updated</th><th /></tr></thead><tbody>{rows.map((row) => <tr className="table-row" key={row.name}><td className="px-5 py-4"><Link className="text-heading font-bold hover:text-brand-600" to={`/runs/${row.name}`}>{row.name}</Link><p className="text-light mt-0.5 text-[9px]">Version {row.workflow_version}</p></td><td className="px-5 py-4"><strong className="text-body block text-[11px]">{row.record_name}</strong><span className="text-light text-[9px]">{row.record_doctype}</span></td><td className="text-muted px-5 py-4 text-[10px]">{row.source}</td><td className="px-5 py-4"><Status value={row.status} /></td><td className="text-muted px-5 py-4 text-[10px]">{formatDate(row.modified)}</td><td><Link className="icon-button" to={`/runs/${row.name}`}><ChevronRight size={17} /></Link></td></tr>)}</tbody></table><div className="flex items-center justify-between border-t border-[var(--border-color)] px-5 py-3"><span className="text-light text-[10px]">Page {page + 1}</span><div className="flex gap-2"><button className={`${secondary} px-2 py-1`} disabled={page === 0} onClick={() => void load(page - 1)}><ChevronLeft size={14} /></button><button className={`${secondary} px-2 py-1`} disabled={!hasMore} onClick={() => void load(page + 1)}><ChevronRight size={14} /></button></div></div></div> : <div className="px-6 py-16 text-center"><span className="magic-orb mx-auto"><Gauge size={20} /></span><h3 className="text-heading mt-4 text-sm font-bold">No runs yet</h3><p className="text-muted mt-1 text-xs">Enroll a record or activate an event trigger to begin.</p></div>}</section>
      </main>
    </div>
  )
}

interface RunDetail {
  run: Record<string, unknown>
  graph: WorkflowGraph
  version_settings: Record<string, unknown>
  tokens: Array<{ name?: string; node_id: string; occurrence?: number; status: string; attempts: number; error_message?: string }>
  events: Array<Record<string, unknown>>
  attempts: Array<{ name: string; node_id: string; attempt_no: number; status: string; error_code?: string; error_message?: string; started_at?: string; completed_at?: string }>
  enrollment_decisions: Array<{ name: string; decision: string; reason_code: string; evidence_json?: string; source: string; decided_at: string }>
  policy_evaluations: Array<{ name: string; event_id: string; changed_fields_json?: string; outcome: string; reason_code: string; evaluated_at: string }>
  trace_has_more?: Partial<Record<'events' | 'attempts' | 'enrollment_decisions' | 'policy_evaluations', boolean>>
}

export function RunDetailPage() {
  const { runId = '' } = useParams()
  const [detail, setDetail] = useState<RunDetail>()
  const [error, setError] = useState('')
  const [traceLoading, setTraceLoading] = useState('')
  const load = useCallback(async () => {
    setError('')
    try {
      setDetail(await call<RunDetail>('get_run', { run_id: runId }))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to load this run')
    }
  }, [runId])
  useEffect(() => { void load() }, [load])
  if (!detail && error) return <div className="app-shell grid h-screen place-items-center p-6"><div className="surface max-w-md rounded-2xl p-7 text-center"><AlertTriangle className="mx-auto text-red-500" /><h1 className="text-heading mt-4 text-lg font-bold">Unable to load this run</h1><p className="text-muted mt-2 text-xs">{error}</p><button className={`${primary} mt-5`} onClick={() => void load()}>Try again</button></div></div>
  if (!detail) return <div className="app-shell grid h-screen place-items-center"><LoaderCircle className="animate-spin text-brand-500" /></div>
  const status = String(detail.run.status)
	const runTrace = projectRunTrace(detail.graph, detail.tokens)
  const mutateRun = async (method: 'cancel_run' | 'retry_run') => {
	if (method === 'cancel_run' && !window.confirm(`Cancel run ${runId}? Waiting tokens and timers will be cancelled.`)) return
    try {
      await call(method, { run_id: runId }, true)
      await load()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to update this run')
    }
  }
  type TraceSection = 'events' | 'attempts' | 'enrollment_decisions' | 'policy_evaluations'
  const loadMoreTrace = async (section: TraceSection) => {
    if (!detail.trace_has_more?.[section] || traceLoading) return
    setTraceLoading(section)
    try {
      const currentRows = detail[section] as Array<unknown>
      const page = await call<{ rows: Array<unknown>; has_more: boolean }>('get_run_trace', { run_id: runId, section, start: currentRows.length, page_length: 100 })
      setDetail((current) => current ? ({ ...current, [section]: [...(current[section] as Array<unknown>), ...page.rows], trace_has_more: { ...current.trace_has_more, [section]: page.has_more } } as RunDetail) : current)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : `Unable to load more ${section.replaceAll('_', ' ')}`)
    } finally {
      setTraceLoading('')
    }
  }
  const traceMore = (section: TraceSection) => detail.trace_has_more?.[section] ? <div className="border-t border-[var(--border-color)] p-3 text-center"><button className={secondary} disabled={Boolean(traceLoading)} onClick={() => void loadMoreTrace(section)}>{traceLoading === section && <LoaderCircle className="animate-spin" size={13} />}Load more</button></div> : null
  return (
    <div className="app-shell">
      <Header><div className="flex min-w-0 items-center gap-3"><ProductBrand subtitle="Execution trace" /><span className="hidden h-7 w-px bg-[var(--border-color)] sm:block" /><Link className="icon-button shrink-0" to={`/${String(detail.run.workflow)}/runs`}><ArrowLeft size={16} /></Link><div className="min-w-0"><h1 className="text-heading truncate text-xs font-bold">{runId}</h1><p className="text-light text-[9px]">Pinned version {String(detail.run.workflow_version)}</p></div></div><div className="flex shrink-0 items-center gap-1.5"><Status value={status} />{!['COMPLETED', 'FAILED', 'CANCELLED'].includes(status) && <button className={secondary} onClick={() => void mutateRun('cancel_run')}><span className="hidden sm:inline">Cancel run</span><span className="sm:hidden">Cancel</span></button>}{status === 'FAILED' && <button className={primary} onClick={() => void mutateRun('retry_run')}><Zap size={14} /><span className="hidden sm:inline">Retry failed</span><span className="sm:hidden">Retry</span></button>}<DeskLink /><ThemeToggle /></div></Header>
      <main className="mx-auto max-w-7xl px-4 py-7 sm:px-6 lg:px-8">
        {error && <div className="mb-4 flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-xs text-red-700 dark:border-red-900 dark:bg-red-500/10 dark:text-red-300"><AlertTriangle size={14} />{error}</div>}
        <div className="mb-5 grid gap-3 sm:grid-cols-3"><div className="metric-card rounded-xl p-4"><p className="text-light text-[9px] font-bold uppercase tracking-wider">Record</p><p className="text-heading mt-2 text-xs font-bold">{String(detail.run.record_name || '—')}</p><p className="text-muted mt-0.5 text-[10px]">{String(detail.run.record_doctype || '')}</p></div><div className="metric-card rounded-xl p-4"><p className="text-light text-[9px] font-bold uppercase tracking-wider">Workflow version</p><p className="text-heading mt-2 text-xs font-bold">{String(detail.run.workflow_version)}</p><p className="text-muted mt-0.5 text-[10px]">Immutable execution graph</p></div><div className="metric-card rounded-xl p-4"><p className="text-light text-[9px] font-bold uppercase tracking-wider">Reached progress</p><p className="text-heading mt-2 text-xs font-bold">{runTrace.completed} of {runTrace.reachedCount} reached steps</p><p className="text-muted mt-0.5 text-[10px]">Untaken branches are excluded</p></div></div>
        <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_390px]">
          <section className="surface-flat overflow-hidden rounded-xl"><div className="border-b border-[var(--border-color)] px-5 py-4"><div className="flex items-center gap-2"><Layers3 className="text-brand-500" size={16} /><h2 className="text-heading text-sm font-bold">Executed path</h2></div><p className="text-muted mt-1 text-[10px]">Durable run-token order from the immutable version used by this run.</p></div><div className="space-y-0 p-5">{runTrace.executed.map(({ node, token }, index) => <div className="relative flex gap-3 pb-4 last:pb-0" key={token.name || `${node.id}-${token.occurrence || index}`}>{index < runTrace.executed.length - 1 && <span className="absolute left-[15px] top-8 h-[calc(100%-20px)] w-px bg-brand-200" />}<span className="z-10 grid size-8 shrink-0 place-items-center rounded-full border border-brand-200 bg-brand-50 text-[10px] font-bold text-brand-600 dark:bg-brand-500/10">{index + 1}</span><div className="min-w-0 flex-1 rounded-xl border border-brand-200 bg-brand-50/40 p-3.5 dark:bg-brand-500/5"><div className="flex items-start justify-between gap-3"><div><strong className="text-heading block text-xs">{node.type}</strong><p className="text-light mt-0.5 text-[9px]">{node.id} · {token.attempts} attempt{token.attempts === 1 ? '' : 's'}</p></div><Status value={token.status} /></div>{token.error_message && <p className="mt-2 rounded-lg bg-red-50 p-2 text-[10px] text-red-700 dark:bg-red-500/10 dark:text-red-300">{token.error_message}</p>}</div></div>)}{!runTrace.executed.length && <p className="text-muted py-6 text-center text-xs">No execution token has reached a node yet.</p>}{runTrace.unvisited.length > 0 && <details className="mt-4 rounded-xl border border-[var(--border-color)] bg-[var(--subtle-fg)] p-3"><summary className="text-heading cursor-pointer text-[10px] font-bold">{runTrace.unvisited.length} branch node{runTrace.unvisited.length === 1 ? '' : 's'} not reached</summary><p className="text-muted mt-2 text-[9px]">{runTrace.unvisited.map((node) => node.id).join(', ')}</p></details>}</div></section>
          <aside className="surface-flat h-fit overflow-hidden rounded-xl"><div className="border-b border-[var(--border-color)] px-5 py-4"><div className="flex items-center gap-2"><Clock3 className="text-magic-500" size={16} /><h2 className="text-heading text-sm font-bold">Event timeline</h2></div><p className="text-muted mt-1 text-[10px]">Authoritative append-only run events.</p></div><ol className="max-h-[680px] space-y-0 overflow-y-auto p-5">{detail.events.map((event, index) => <li className="relative flex gap-3 pb-5 last:pb-0" key={index}>{index < detail.events.length - 1 && <span className="absolute left-[6px] top-4 h-[calc(100%-8px)] w-px bg-[var(--border-color)]" />}<span className="z-10 mt-1 size-[13px] shrink-0 rounded-full border-[3px] border-[var(--fg-color)] bg-magic-500 ring-1 ring-magic-200" /><div><strong className="text-heading block text-[11px]">{String(event.event_type)}</strong><p className="text-muted mt-1 text-[9px] leading-4">{String(event.occurred_at || '')}</p>{Boolean(event.node_id) && <p className="text-light text-[9px]">{String(event.node_id)}</p>}</div></li>)}</ol>{traceMore('events')}</aside>
        </div>
        <div className="mt-5 grid gap-5 lg:grid-cols-2"><section className="surface-flat overflow-hidden rounded-xl"><div className="border-b border-[var(--border-color)] px-5 py-4"><h2 className="text-heading text-sm font-bold">Enrollment evidence</h2><p className="text-muted mt-1 text-[10px]">Why this record entered the pinned version, without storing sensitive document values.</p></div>{detail.enrollment_decisions?.length ? <div className="divide-y divide-[var(--border-color)]">{detail.enrollment_decisions.map((row) => <div className="p-4" key={row.name}><div className="flex items-center justify-between gap-3"><div><strong className="text-heading text-[11px]">{row.reason_code}</strong><p className="text-light mt-0.5 text-[9px]">{row.source} · {formatDate(row.decided_at)}</p></div><Status value={row.decision} /></div>{row.evidence_json && <pre className="text-muted mt-2 overflow-auto rounded-lg bg-[var(--subtle-fg)] p-2 text-[9px]">{safeJsonEvidence(row.evidence_json)}</pre>}</div>)}</div> : <p className="p-8 text-center text-xs text-[var(--text-muted)]">Legacy run: no first-class decision evidence.</p>}{traceMore('enrollment_decisions')}</section><section className="surface-flat overflow-hidden rounded-xl"><div className="border-b border-[var(--border-color)] px-5 py-4"><h2 className="text-heading text-sm font-bold">Node attempts</h2><p className="text-muted mt-1 text-[10px]">Every execution and retry attempt, including permanent and ambiguous failures.</p></div>{detail.attempts?.length ? <div className="divide-y divide-[var(--border-color)]">{detail.attempts.map((row) => <div className="flex items-start justify-between gap-3 p-4" key={row.name}><div><strong className="text-heading block text-[11px]">{row.node_id} · attempt {row.attempt_no}</strong><p className="text-light mt-0.5 text-[9px]">{formatDate(row.started_at)}{row.error_code ? ` · ${row.error_code}` : ''}</p>{row.error_message && <p className="mt-1 text-[10px] text-red-600">{row.error_message}</p>}</div><Status value={row.status} /></div>)}</div> : <p className="p-8 text-center text-xs text-[var(--text-muted)]">No node attempts recorded.</p>}{traceMore('attempts')}</section></div>
        {detail.policy_evaluations?.length ? <section className="surface-flat mt-5 overflow-hidden rounded-xl"><div className="border-b border-[var(--border-color)] px-5 py-4"><div className="flex items-center gap-2"><ShieldCheck className="text-brand-500" size={16} /><h2 className="text-heading text-sm font-bold">Lifecycle reevaluations</h2></div><p className="text-muted mt-1 text-[10px]">Relevant record changes checked against the immutable policy pinned to this run.</p></div><div className="divide-y divide-[var(--border-color)]">{detail.policy_evaluations.map((row) => <div className="flex flex-col justify-between gap-3 p-4 sm:flex-row sm:items-center" key={row.name}><div><strong className="text-heading block text-[11px]">{row.reason_code}</strong><p className="text-light mt-0.5 text-[9px]">{formatDate(row.evaluated_at)} · event {row.event_id}</p>{formatJsonList(row.changed_fields_json) && <p className="text-muted mt-1 text-[10px]">Changed fields: {formatJsonList(row.changed_fields_json)}</p>}</div><Status value={row.outcome} /></div>)}</div>{traceMore('policy_evaluations')}</section> : null}
      </main>
    </div>
  )
}

interface OutboxRow {
  name: string
  event_id: string
  event_type: string
  object_doctype: string
  object_name: string
  status: string
  attempts: number
  error_code?: string
  error_message?: string
  trace_id?: string
  creation: string
}

interface OperationsSnapshot {
  health: RuntimeHealth
  failed_attempts: Array<{ name: string; run: string; node_id: string; status: string; error_code?: string; error_message?: string }>
  due_timers: number
  ready_tokens: number
  policy_evaluations?: {
    counts: { NO_CHANGE: number; GOAL_MET: number; ELIGIBILITY_LOST: number }
    recent: Array<{ name: string; workflow: string; run: string; record_doctype: string; record_name: string; outcome: string; reason_code: string; changed_fields_json?: string; evaluated_at: string }>
  }
}

interface IncidentRow { name: string; status: string; severity: string; workflow?: string; run?: string; node_id?: string; error_code: string; occurrence_count: number; last_seen_at: string; last_message?: string }
interface DeadLetterRow { name: string; source_type: string; source_name: string; workflow?: string; run?: string; status: string; error_code: string; message?: string; attempts: number; created_at: string }
interface AnalyticsTotals { enrollments: number; suppressed: number; duplicates: number; completed_runs: number; failed_runs: number; cancelled_runs: number; retries: number }

export function OperationsPage() {
  const [snapshot, setSnapshot] = useState<OperationsSnapshot>()
  const [rows, setRows] = useState<OutboxRow[]>([])
  const [status, setStatus] = useState('FAILED')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [incidents, setIncidents] = useState<IncidentRow[]>([])
  const [deadLetters, setDeadLetters] = useState<DeadLetterRow[]>([])
  const [selectedDeadLetters, setSelectedDeadLetters] = useState<Set<string>>(new Set())
  const [analytics, setAnalytics] = useState<AnalyticsTotals>()
  const [operationHasMore, setOperationHasMore] = useState({ outbox: false, incidents: false, deadLetters: false })
  const [loadingSection, setLoadingSection] = useState('')
  const [operationMutation, setOperationMutation] = useState('')
  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [operationsRes, outboxRes, incidentRes, deadLetterRes, analyticsRes] = await Promise.allSettled([
        call<OperationsSnapshot>('get_operations'),
        call<{ rows: OutboxRow[]; has_more: boolean }>('list_outbox', { status, page_length: 100 }),
        call<{ rows: IncidentRow[]; has_more: boolean }>('list_incidents', { status: 'OPEN', page_length: 50 }),
        call<{ rows: DeadLetterRow[]; has_more: boolean }>('list_dead_letters', { status: 'OPEN', page_length: 50 }),
        call<{ totals: AnalyticsTotals }>('get_automation_analytics', { days: 30 }),
      ])
      setSelected(new Set())
      setSelectedDeadLetters(new Set())
      const failures: string[] = []
      if (operationsRes.status === 'fulfilled') setSnapshot(operationsRes.value); else { setSnapshot(undefined); failures.push('runtime snapshot') }
      if (outboxRes.status === 'fulfilled') { setRows(outboxRes.value.rows); setOperationHasMore((current) => ({ ...current, outbox: outboxRes.value.has_more })) } else { setRows([]); failures.push('outbox') }
      if (incidentRes.status === 'fulfilled') { setIncidents(incidentRes.value.rows); setOperationHasMore((current) => ({ ...current, incidents: incidentRes.value.has_more })) } else { setIncidents([]); failures.push('incidents') }
      if (deadLetterRes.status === 'fulfilled') { setDeadLetters(deadLetterRes.value.rows); setOperationHasMore((current) => ({ ...current, deadLetters: deadLetterRes.value.has_more })) } else { setDeadLetters([]); failures.push('recovery queue') }
      if (analyticsRes.status === 'fulfilled') setAnalytics(analyticsRes.value.totals); else { setAnalytics(undefined); failures.push('analytics') }
      if (failures.length) setError(`Some Operations sections failed to load: ${failures.join(', ')}.`)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to load runtime operations')
    } finally {
      setLoading(false)
    }
  }, [status])
  useEffect(() => { void load() }, [load])
  const performOperation = async (key: string, task: () => Promise<void>, fallback: string) => {
	if (operationMutation) return
	setOperationMutation(key)
	setError('')
	try {
	  await task()
	} catch (reason) {
	  setError(reason instanceof Error ? reason.message : fallback)
	} finally {
	  setOperationMutation('')
	}
  }
  const loadMoreOperation = async (section: 'outbox' | 'incidents' | 'deadLetters') => {
	if (loadingSection) return
	setLoadingSection(section)
	setError('')
	try {
	  if (section === 'outbox') {
		const page = await call<{ rows: OutboxRow[]; has_more: boolean }>('list_outbox', { status, start: rows.length, page_length: 100 })
		setRows((current) => [...current, ...page.rows])
		setOperationHasMore((current) => ({ ...current, outbox: page.has_more }))
	  } else if (section === 'incidents') {
		const page = await call<{ rows: IncidentRow[]; has_more: boolean }>('list_incidents', { status: 'OPEN', start: incidents.length, page_length: 50 })
		setIncidents((current) => [...current, ...page.rows])
		setOperationHasMore((current) => ({ ...current, incidents: page.has_more }))
	  } else {
		const page = await call<{ rows: DeadLetterRow[]; has_more: boolean }>('list_dead_letters', { status: 'OPEN', start: deadLetters.length, page_length: 50 })
		setDeadLetters((current) => [...current, ...page.rows])
		setOperationHasMore((current) => ({ ...current, deadLetters: page.has_more }))
	  }
	} catch (reason) {
	  setError(reason instanceof Error ? reason.message : `Unable to load more ${section}`)
	} finally {
	  setLoadingSection('')
	}
  }
  const mutateEvent = async (method: 'retry_outbox_event' | 'discard_outbox_event', eventId: string) => {
	if (method === 'discard_outbox_event' && !window.confirm(`Discard outbox event ${eventId}? It will not be processed.`)) return
	await performOperation(`${method}:${eventId}`, async () => {
	  await call(method, { event_id: eventId }, true)
	  await load()
	}, 'Unable to update the event')
  }
  const bulkRetry = async () => {
	await performOperation('bulk-outbox-retry', async () => {
	  await call('bulk_retry_outbox', { event_ids: [...selected] }, true)
	  await load()
	}, 'Unable to retry selected events')
  }
  const toggle = (name: string) => setSelected((current) => {
    const next = new Set(current)
    if (next.has(name)) next.delete(name); else next.add(name)
    return next
  })
  const recoverDeadLetter = async (name: string) => {
	await performOperation(`recover:${name}`, async () => { await call('retry_dead_letter', { dead_letter_id: name }, true); await load() }, 'Unable to recover dead letter')
  }
  const reconcileExternal = async (name: string, resolution: 'DELIVERED' | 'NOT_DELIVERED') => {
	const consequence = resolution === 'DELIVERED' ? 'mark the remote effect delivered and continue the run' : 'assert it was not delivered and retry the effect'
	if (!window.confirm(`Reconcile ${name}? This will ${consequence}.`)) return
	await performOperation(`reconcile:${name}`, async () => { await call('reconcile_dead_letter', { dead_letter_id: name, resolution }, true); await load() }, 'Unable to reconcile external effect')
  }
  const toggleDeadLetter = (name: string) => setSelectedDeadLetters((current) => {
    const next = new Set(current)
    if (next.has(name)) next.delete(name); else next.add(name)
    return next
  })
  const bulkRecoverDeadLetters = async () => {
	await performOperation('bulk-recover', async () => { await call('bulk_retry_dead_letters', { dead_letter_ids: [...selectedDeadLetters] }, true); await load() }, 'Unable to bulk recover')
  }
  const bulkDiscardDeadLetters = async () => {
	if (!window.confirm(`Discard ${selectedDeadLetters.size} selected recovery item${selectedDeadLetters.size === 1 ? '' : 's'}? They will not be retried.`)) return
	await performOperation('bulk-discard', async () => { await call('bulk_discard_dead_letters', { dead_letter_ids: [...selectedDeadLetters] }, true); await load() }, 'Unable to bulk discard')
  }
  const closeIncident = async (name: string) => {
	await performOperation(`incident:${name}`, async () => { await call('resolve_incident', { incident_id: name, resolution: 'Reviewed and resolved from command center.' }, true); await load() }, 'Unable to resolve incident')
  }
  return (
    <div className="app-shell min-h-screen">
      <Header><div className="flex items-center gap-3"><ProductBrand subtitle="Operator command center" /><Link className="icon-button" to="/" aria-label="Back to workflows"><ArrowLeft size={16} /></Link></div><div className="flex items-center gap-1"><DeskLink /><ThemeToggle /></div></Header>
      <main className="mx-auto max-w-7xl px-4 py-7 sm:px-6 lg:px-8">
        <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-end"><div><div className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-[0.13em] text-brand-600"><Gauge size={13} />Reliable operations</div><h1 className="text-heading mt-1 text-2xl font-bold tracking-tight">Automation command center</h1><p className="text-muted mt-1 text-xs">Inspect backlog, retries, timers, failed effects, and recovery evidence.</p></div><button className={secondary} onClick={() => void load()} disabled={loading}><Activity size={14} />Refresh</button></div>
        {operationMutation && <div className="mt-4 flex items-center gap-2 rounded-lg border border-brand-200 bg-brand-50 px-4 py-3 text-xs text-brand-700 dark:bg-brand-500/10"><LoaderCircle className="animate-spin" size={14} />Applying operator action…</div>}
        {error && <div className="mt-4 flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-xs text-red-700 dark:border-red-900 dark:bg-red-500/10 dark:text-red-300"><AlertTriangle size={14} />{error}</div>}
        {snapshot && <section className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-5"><div className="metric-card rounded-xl p-4"><p className="text-light text-[9px] font-bold uppercase tracking-wider">Runtime</p><div className="mt-2"><Status value={snapshot.health.enabled ? 'ACTIVE' : 'DISABLED'} /></div></div>{[['Ready events', snapshot.health.outbox.PENDING], ['Failed events', snapshot.health.outbox.FAILED], ['Ready tokens', snapshot.ready_tokens], ['Overdue timers', snapshot.due_timers]].map(([label, value]) => <div className="metric-card rounded-xl p-4" key={String(label)}><p className="text-light text-[9px] font-bold uppercase tracking-wider">{label}</p><strong className="text-heading mt-2 block text-2xl">{value}</strong></div>)}</section>}
        {analytics && <section className="mt-3 grid gap-3 sm:grid-cols-3 lg:grid-cols-6">{[['Enrollments', analytics.enrollments], ['Suppressed', analytics.suppressed], ['Duplicates', analytics.duplicates], ['Completed', analytics.completed_runs], ['Failed', analytics.failed_runs], ['Retries', analytics.retries]].map(([label, value]) => <div className="metric-card rounded-xl p-3" key={String(label)}><p className="text-light text-[8px] font-bold uppercase tracking-wider">30d {label}</p><strong className="text-heading mt-1 block text-xl">{Number(value).toLocaleString()}</strong></div>)}</section>}
        {snapshot?.policy_evaluations && <section className="mt-6 grid gap-5 xl:grid-cols-[360px_minmax(0,1fr)]"><div className="surface-flat overflow-hidden rounded-xl"><div className="border-b border-[var(--border-color)] p-4"><div className="flex items-center gap-2"><ShieldCheck className="text-brand-500" size={15} /><h2 className="text-heading text-sm font-bold">Lifecycle policies</h2></div><p className="text-muted mt-0.5 text-[10px]">Live runs reevaluated after relevant record changes.</p></div><div className="grid grid-cols-3 divide-x divide-[var(--border-color)] p-4 text-center"><div><strong className="text-heading block text-xl">{snapshot.policy_evaluations.counts.GOAL_MET.toLocaleString()}</strong><span className="text-light text-[8px] font-bold uppercase tracking-wider">Goals</span></div><div><strong className="text-heading block text-xl">{snapshot.policy_evaluations.counts.ELIGIBILITY_LOST.toLocaleString()}</strong><span className="text-light text-[8px] font-bold uppercase tracking-wider">Stopped</span></div><div><strong className="text-heading block text-xl">{snapshot.policy_evaluations.counts.NO_CHANGE.toLocaleString()}</strong><span className="text-light text-[8px] font-bold uppercase tracking-wider">No change</span></div></div></div><div className="surface-flat overflow-hidden rounded-xl"><div className="border-b border-[var(--border-color)] p-4"><h2 className="text-heading text-sm font-bold">Recent lifecycle checks</h2><p className="text-muted mt-0.5 text-[10px]">Pinned-version policy outcomes with safe changed-field evidence.</p></div>{snapshot.policy_evaluations.recent.length ? <div className="divide-y divide-[var(--border-color)]">{snapshot.policy_evaluations.recent.map((row) => <Link className="block p-4 hover:bg-[var(--subtle-fg)]" to={`/runs/${row.run}`} key={row.name}><div className="flex items-start justify-between gap-3"><div className="min-w-0"><strong className="text-heading block truncate text-[11px]">{row.record_doctype} · {row.record_name}</strong><p className="text-light mt-0.5 text-[9px]">{formatDate(row.evaluated_at)} · {row.reason_code}</p>{formatJsonList(row.changed_fields_json) && <p className="text-muted mt-1 truncate text-[10px]">Changed fields: {formatJsonList(row.changed_fields_json)}</p>}</div><Status value={row.outcome} /></div></Link>)}</div> : <div className="p-10 text-center text-xs text-[var(--text-muted)]">No lifecycle checks recorded yet.</div>}</div></section>}
        <section className="mt-6 grid gap-5 xl:grid-cols-2"><div className="surface-flat overflow-hidden rounded-xl"><div className="border-b border-[var(--border-color)] p-4"><h2 className="text-heading text-sm font-bold">Grouped incidents</h2><p className="text-muted mt-0.5 text-[10px]">Repeated failures share one stable fingerprint, occurrence counter, and operator resolution.</p></div>{incidents.length ? <div className="divide-y divide-[var(--border-color)]">{incidents.map((row) => <div className="p-4" key={row.name}><div className="flex items-start justify-between gap-3"><div><div className="flex items-center gap-2"><Status value={row.severity} /><strong className="text-heading text-[11px]">{row.error_code}</strong></div><p className="text-muted mt-1 line-clamp-2 text-[10px]">{row.last_message || 'No message'}</p><p className="text-light mt-1 text-[9px]">{row.workflow || 'Platform'} · {row.node_id || row.name} · {row.occurrence_count} occurrence{row.occurrence_count === 1 ? '' : 's'}</p></div><button className={secondary} onClick={() => void closeIncident(row.name)}><CheckCircle2 size={12} />Resolve</button></div></div>)}</div> : <div className="p-10 text-center text-xs text-[var(--text-muted)]">No open incidents.</div>}{operationHasMore.incidents && <div className="border-t border-[var(--border-color)] p-3 text-center"><button className={secondary} disabled={Boolean(loadingSection)} onClick={() => void loadMoreOperation('incidents')}>{loadingSection === 'incidents' && <LoaderCircle className="animate-spin" size={13} />}Load more incidents</button></div>}</div><div className="surface-flat overflow-hidden rounded-xl"><div className="flex items-center justify-between border-b border-[var(--border-color)] p-4"><div><h2 className="text-heading text-sm font-bold">Recovery queue</h2><p className="text-muted mt-0.5 text-[10px]">Terminal outbox, run, external, and backfill failures with source-specific recovery.</p></div><div className="flex gap-2"><button className={primary} disabled={!selectedDeadLetters.size} onClick={() => void bulkRecoverDeadLetters()}><Zap size={13} />Recover ({selectedDeadLetters.size})</button><button className={ghost} disabled={!selectedDeadLetters.size} onClick={() => void bulkDiscardDeadLetters()}><Ban size={13} />Discard</button></div></div>{deadLetters.length ? <div className="divide-y divide-[var(--border-color)]">{deadLetters.map((row) => <div className="p-4" key={row.name}><div className="flex items-start justify-between gap-3"><div className="flex items-start gap-3"><input type="checkbox" className="mt-1" checked={selectedDeadLetters.has(row.name)} onChange={() => toggleDeadLetter(row.name)} aria-label={`Select ${row.name}`} /><div><div className="flex items-center gap-2"><Status value={row.source_type} /><strong className="text-heading text-[11px]">{row.error_code}</strong></div><p className="text-muted mt-1 line-clamp-2 text-[10px]">{row.message || 'No message'}</p><p className="text-light mt-1 text-[9px]">{row.source_name} · {row.attempts} attempts</p></div></div>{row.source_type === 'EXTERNAL' ? <div className="flex gap-1"><button className={secondary} onClick={() => void reconcileExternal(row.name, 'DELIVERED')}>Delivered</button><button className={primary} onClick={() => void reconcileExternal(row.name, 'NOT_DELIVERED')}>Retry safe</button></div> : <button className={primary} onClick={() => void recoverDeadLetter(row.name)}><Zap size={12} />Recover</button>}</div></div>)}</div> : <div className="p-10 text-center text-xs text-[var(--text-muted)]">Recovery queue is clear.</div>}{operationHasMore.deadLetters && <div className="border-t border-[var(--border-color)] p-3 text-center"><button className={secondary} disabled={Boolean(loadingSection)} onClick={() => void loadMoreOperation('deadLetters')}>{loadingSection === 'deadLetters' && <LoaderCircle className="animate-spin" size={13} />}Load more recovery items</button></div>}</div></section>
        <section className="mt-6 surface-flat overflow-hidden rounded-xl"><div className="flex flex-col gap-3 border-b border-[var(--border-color)] p-4 sm:flex-row sm:items-center sm:justify-between"><div><h2 className="text-heading text-sm font-bold">Durable outbox</h2><p className="text-muted mt-0.5 text-[10px]">Database state is authoritative; operator actions are explicit and bounded.</p></div><div className="flex gap-2"><select className={`${field} !w-auto`} value={status} onChange={(event) => setStatus(event.target.value)}><option value="FAILED">Retry queue</option><option value="DEAD">Dead letters</option><option value="PENDING">Pending</option><option value="PROCESSING">Processing</option><option value="PROCESSED">Processed</option><option value="ALL">All</option></select><button className={primary} disabled={!selected.size} onClick={() => void bulkRetry()}><Zap size={13} />Retry selected ({selected.size})</button></div></div>
          {loading ? <div className="grid min-h-52 place-items-center"><LoaderCircle className="animate-spin text-brand-500" /></div> : rows.length ? <div className="overflow-x-auto"><table className="w-full min-w-[980px] text-left text-xs"><thead className="bg-[var(--subtle-fg)] text-[9px] font-bold uppercase tracking-[0.11em] text-[var(--text-light)]"><tr><th className="w-12 px-4 py-3" /><th className="px-4 py-3">Event</th><th className="px-4 py-3">Record</th><th className="px-4 py-3">Status</th><th className="px-4 py-3">Failure</th><th className="px-4 py-3">Created</th><th className="px-4 py-3">Recovery</th></tr></thead><tbody>{rows.map((row) => <tr className="table-row" key={row.name}><td className="px-4 py-3"><input type="checkbox" checked={selected.has(row.name)} disabled={!['FAILED', 'DEAD'].includes(row.status)} onChange={() => toggle(row.name)} aria-label={`Select ${row.name}`} /></td><td className="px-4 py-3"><strong className="text-heading block text-[11px]">{row.event_type}</strong><span className="text-light text-[9px]">{row.name} · attempt {row.attempts}</span></td><td className="px-4 py-3"><strong className="text-body block text-[11px]">{row.object_name}</strong><span className="text-light text-[9px]">{row.object_doctype}</span></td><td className="px-4 py-3"><Status value={row.status} /></td><td className="max-w-xs px-4 py-3"><strong className="text-[10px] text-red-600">{row.error_code || '—'}</strong><p className="text-muted mt-0.5 line-clamp-2 text-[9px]">{row.error_message || 'No failure recorded'}</p></td><td className="text-muted px-4 py-3 text-[10px]">{formatDate(row.creation)}</td><td className="px-4 py-3"><div className="flex gap-1.5">{['FAILED', 'DEAD'].includes(row.status) && <button className={secondary} onClick={() => void mutateEvent('retry_outbox_event', row.name)}><Zap size={12} />Retry</button>}{!['PROCESSING', 'PROCESSED', 'DEAD'].includes(row.status) && <button className={ghost} onClick={() => void mutateEvent('discard_outbox_event', row.name)}><Ban size={12} />Discard</button>}</div></td></tr>)}</tbody></table></div> : <div className="px-6 py-16 text-center"><span className="magic-orb mx-auto"><ShieldCheck size={20} /></span><h3 className="text-heading mt-4 text-sm font-bold">No events in this state</h3><p className="text-muted mt-1 text-xs">The selected queue is clear.</p></div>}{operationHasMore.outbox && <div className="border-t border-[var(--border-color)] p-3 text-center"><button className={secondary} disabled={Boolean(loadingSection)} onClick={() => void loadMoreOperation('outbox')}>{loadingSection === 'outbox' && <LoaderCircle className="animate-spin" size={13} />}Load more events</button></div>}
        </section>
        {snapshot?.failed_attempts.length ? <section className="mt-6 surface-flat rounded-xl p-5"><h2 className="text-heading text-sm font-bold">Failed and ambiguous effects</h2><div className="mt-3 grid gap-2">{snapshot.failed_attempts.map((attempt) => <Link className="rounded-lg border border-[var(--border-color)] bg-white/60 dark:bg-white/5 backdrop-blur-md p-3 hover:border-brand-300" to={`/runs/${attempt.run}`} key={attempt.name}><div className="flex items-center justify-between gap-3"><div><strong className="text-heading block text-[11px]">{attempt.node_id}</strong><span className="text-light text-[9px]">{attempt.run} · {attempt.error_code || 'No code'}</span></div><Status value={attempt.status} /></div></Link>)}</div></section> : null}
      </main>
    </div>
  )
}
