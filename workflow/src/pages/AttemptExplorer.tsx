import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { ChevronLeft, ChevronRight, FileDown, Layers3, LoaderCircle, Search, Sparkles } from 'lucide-react'
import { Header, ProductBrand, DeskLink, Status, formatDate, hasRole } from './WorkflowPages'
import { ThemeToggle } from '../components/ThemeToggle'
import { call } from '../lib/api'
import { AsyncCombobox } from '../components/AsyncCombobox'

interface AttemptRow {
  name: string
  workflow?: string
  workflow_version?: string
  record_doctype?: string
  record_name?: string
  source?: string
  decision: string
  reason_code?: string
  evidence_json?: string
  run?: string
  decided_at: string
}

export function AttemptExplorerPage() {
  const [rows, setRows] = useState<AttemptRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [page, setPage] = useState(0)
  const [hasMore, setHasMore] = useState(false)
  const [workflow, setWorkflow] = useState('')
  const loadWorkflows = useCallback((search: string) => call<{ rows: Array<{ name: string; title: string }> }>('list_workflows', { search, page_length: 20 }).then((r) => r.rows.map((row) => ({ value: row.name, label: row.title, description: row.name }))), [])
  const [recordName, setRecordName] = useState('')
  const [decision, setDecision] = useState('')
  const [exporting, setExporting] = useState(false)
  const requestSequence = useRef(0)

  const canOperate = hasRole('Automation Operator', 'Automation Publisher')
  const pageLength = 50

  const load = useCallback(async (pageNum = 0, signal?: AbortSignal) => {
    const sequence = ++requestSequence.current
    setLoading(true)
    setError('')
    try {
      const result = await call<{ rows: AttemptRow[], has_more: boolean }>('list_enrollment_decisions', {
        workflow: workflow || undefined,
        record_name: recordName || undefined,
        decision: decision || undefined,
        start: pageNum * pageLength,
        page_length: pageLength
      }, false, signal)
      if (sequence !== requestSequence.current) return
      setRows(result.rows)
      setHasMore(result.has_more)
      setPage(pageNum)
    } catch (reason) {
      if (!signal?.aborted && sequence === requestSequence.current) setError(reason instanceof Error ? reason.message : 'Unable to load attempts')
    } finally {
      if (sequence === requestSequence.current) setLoading(false)
    }
  }, [workflow, recordName, decision])

  useEffect(() => {
    const controller = new AbortController()
    const timer = window.setTimeout(() => void load(0, controller.signal), 300)
    return () => { window.clearTimeout(timer); controller.abort() }
  }, [load])

  const downloadCsv = async () => {
    setExporting(true)
    try {
      const url = new URL(window.location.origin + '/api/method/finbyzai.workflow_builder.api.export_enrollment_decisions')
      if (workflow) url.searchParams.append('workflow', workflow)
      if (recordName) url.searchParams.append('record_name', recordName)
      if (decision) url.searchParams.append('decision', decision)
      window.open(url.toString(), '_blank')
    } finally {
      setExporting(false)
    }
  }

  return (
    <div className="app-shell">
      <Header>
        <ProductBrand subtitle="Attempt Explorer" />
        <div className="flex items-center gap-2">
          {canOperate && <Link className="btn-core btn-ghost" to="/operations"><Layers3 size={14} /><span className="hidden sm:inline">Operations</span></Link>}
          <DeskLink />
          <ThemeToggle />
        </div>
      </Header>
      <main className="mx-auto max-w-7xl px-4 pb-12 pt-6 sm:px-6 lg:px-8">
        <section className="hero-glow surface animate-enter rounded-2xl px-6 py-7 sm:px-8 sm:py-8">
          <div className="flex flex-col justify-between gap-6 md:flex-row md:items-center">
            <div className="max-w-2xl">
              <div className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-[0.14em] text-brand-600"><Sparkles size={13} />Global Explorer</div>
              <h1 className="text-heading mt-2 text-2xl font-black tracking-tight sm:text-3xl">Enrollment Attempts</h1>
              <p className="text-muted mt-2 text-sm leading-6">Search, filter, and audit every workflow evaluation across your site.</p>
            </div>
          </div>
        </section>

        <section className="mt-6 surface-flat rounded-xl overflow-hidden">
          <div className="border-b border-[var(--border-color)] px-5 py-4 bg-white/50 dark:bg-[#18212b]/50 flex flex-wrap gap-3 items-center backdrop-blur-md">
            <div className="flex-1 min-w-[200px] relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--text-light)]" size={14} />
              <input
                className="frappe-control h-9 w-full pl-9 text-xs"
                placeholder="Search record name..."
                value={recordName}
                onChange={(e) => setRecordName(e.target.value)}
              />
            </div>
            <div className="flex-1 min-w-[200px]">
              <AsyncCombobox ariaLabel="Filter by workflow" value={workflow} onChange={setWorkflow} loadOptions={loadWorkflows} placeholder="Filter by workflow…" />
            </div>
            <select className="frappe-control h-9 text-xs min-w-[120px]" value={decision} onChange={(e) => setDecision(e.target.value)}>
              <option value="">All decisions</option>
              <option value="ENROLLED">Enrolled</option>
              <option value="REJECTED">Rejected</option>
              <option value="SUPPRESSED">Suppressed</option>
              <option value="DUPLICATE">Duplicate</option>
            </select>
            <button className="btn-core btn-secondary h-9" onClick={() => void downloadCsv()} disabled={exporting}>
              {exporting ? <LoaderCircle className="animate-spin" size={14} /> : <FileDown size={14} />} Export CSV
            </button>
          </div>

          {error && <div className="m-4 p-4 text-xs text-red-600 bg-red-50 dark:bg-red-500/10 rounded-lg border border-red-200 dark:border-red-900">{error}</div>}

          {loading ? (
            <div className="grid min-h-52 place-items-center">
              <LoaderCircle className="animate-spin text-brand-500" />
            </div>
          ) : rows.length ? (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[980px] text-left text-xs">
                <thead className="bg-[var(--subtle-fg)] text-[9px] font-bold uppercase tracking-[0.11em] text-[var(--text-light)]">
                  <tr>
                    <th className="px-4 py-3">Record</th>
                    <th className="px-4 py-3">Workflow</th>
                    <th className="px-4 py-3">Decision</th>
                    <th className="px-4 py-3">Reason</th>
                    <th className="px-4 py-3">Run</th>
                    <th className="px-4 py-3">Decided At</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[var(--border-color)]">
                  {rows.map((row) => (
                    <tr className="table-row hover:bg-black/5 dark:hover:bg-white/5" key={row.name}>
                      <td className="px-4 py-3">
                        <strong className="text-heading block text-[11px]">{row.record_name}</strong>
                        <span className="text-light text-[9px]">{row.record_doctype}</span>
                      </td>
                      <td className="px-4 py-3">
                        <strong className="text-body block text-[11px]">{row.workflow || '—'}</strong>
                        <span className="text-light text-[9px]">{row.workflow_version ? `v${row.workflow_version}` : ''}</span>
                      </td>
                      <td className="px-4 py-3">
                        <Status value={row.decision} />
                      </td>
                      <td className="max-w-xs px-4 py-3">
                        <strong className="text-[10px] text-brand-600">{row.reason_code || '—'}</strong>
                        <p className="text-muted mt-0.5 line-clamp-1 text-[9px]" title={row.source}>{row.source}</p>
                      </td>
                      <td className="px-4 py-3">
                        {row.run ? <Link className="text-brand-600 hover:underline" to={`/runs/${row.run}`}>{row.run}</Link> : <span className="text-muted text-[10px]">None</span>}
                      </td>
                      <td className="text-muted px-4 py-3 text-[10px]">
                        {formatDate(row.decided_at)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="flex items-center justify-between border-t border-[var(--border-color)] px-5 py-3">
                <span className="text-light text-[10px]">Showing page {page + 1}</span>
                <div className="flex gap-2">
                  <button className="btn-core btn-secondary px-2 py-1" disabled={page === 0} onClick={() => void load(page - 1)}><ChevronLeft size={14} /></button>
                  <button className="btn-core btn-secondary px-2 py-1" disabled={!hasMore} onClick={() => void load(page + 1)}><ChevronRight size={14} /></button>
                </div>
              </div>
            </div>
          ) : (
            <div className="px-6 py-16 text-center">
              <span className="magic-orb mx-auto"><Search size={20} /></span>
              <h3 className="text-heading mt-4 text-sm font-bold">No attempts found</h3>
              <p className="text-muted mt-1 text-xs">Try adjusting your search or filters.</p>
            </div>
          )}
        </section>
      </main>
    </div>
  )
}
