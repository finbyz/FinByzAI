import { AlertTriangle, ArrowLeft, Copy, Download, Layers3, LoaderCircle, Search, Sparkles, Upload } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ThemeToggle } from '../components/ThemeToggle'
import { call, mutationEnvelope } from '../lib/api'
import { DeskLink, Header, ProductBrand, hasRole } from './WorkflowPages'

interface TemplateSummary {
  name: string
  title: string
  category: string
  description?: string
  primary_doctype: string
  preview_image?: string
}

const PAGE_LENGTH = 24

export function TemplateGalleryPage() {
  const [rows, setRows] = useState<TemplateSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [hasMore, setHasMore] = useState(false)
  const [error, setError] = useState('')
  const [search, setSearch] = useState('')
  const [busyTemplate, setBusyTemplate] = useState('')
  const [importing, setImporting] = useState(false)
  const requestSequence = useRef(0)
  const navigate = useNavigate()
  const canManage = hasRole('Automation Publisher')

  const load = useCallback(async (start = 0, append = false, signal?: AbortSignal) => {
    const sequence = ++requestSequence.current
    if (append) setLoadingMore(true)
    else setLoading(true)
    setError('')
    try {
      const value = await call<{ rows: TemplateSummary[]; has_more: boolean }>(
        'list_templates',
        { search: search.trim(), start, page_length: PAGE_LENGTH },
        false,
        signal,
      )
      if (sequence !== requestSequence.current) return
      setRows((current) => append ? [...current, ...value.rows] : value.rows)
      setHasMore(value.has_more)
    } catch (reason) {
      if (signal?.aborted || sequence !== requestSequence.current) return
      setError(reason instanceof Error ? reason.message : 'Unable to load templates')
    } finally {
      if (sequence === requestSequence.current) {
        setLoading(false)
        setLoadingMore(false)
      }
    }
  }, [search])

  useEffect(() => {
    const controller = new AbortController()
    const timer = window.setTimeout(() => void load(0, false, controller.signal), 300)
    return () => {
      window.clearTimeout(timer)
      controller.abort()
    }
  }, [load])

  const createFromTemplate = async (template: TemplateSummary) => {
    if (busyTemplate) return
    setBusyTemplate(template.name)
    setError('')
    try {
      const result = await call<{ workflow: string }>('create_workflow_from_template', mutationEnvelope('', { id: template.name }), true)
      navigate(`/${result.workflow}`)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to create workflow from template')
    } finally {
      setBusyTemplate('')
    }
  }

  const exportTemplate = async (template: TemplateSummary) => {
    if (busyTemplate) return
    setBusyTemplate(template.name)
    setError('')
    try {
      const packageJson = await call<string>('export_template', { template_name: template.name })
      const link = document.createElement('a')
      link.href = URL.createObjectURL(new Blob([packageJson], { type: 'application/json' }))
      link.download = `${template.name}.workflow-template.json`
      link.click()
      URL.revokeObjectURL(link.href)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to export template')
    } finally {
      setBusyTemplate('')
    }
  }

  const importTemplate = async (file?: File) => {
    if (!file || importing) return
    setImporting(true)
    setError('')
    try {
      await call('import_template', { json_data: await file.text() }, true)
      await load(0)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to import template')
    } finally {
      setImporting(false)
    }
  }

  return (
    <div className="app-shell">
      <Header>
        <ProductBrand subtitle="Template Gallery" />
        <div className="flex items-center gap-2">
          <Link className="btn-core btn-ghost" to="/"><ArrowLeft size={14} /><span className="hidden sm:inline">Back to Workflows</span></Link>
          <DeskLink />
          <ThemeToggle />
        </div>
      </Header>
      <main className="mx-auto max-w-7xl px-4 pb-12 pt-6 sm:px-6 lg:px-8">
        <section className="hero-glow surface animate-enter rounded-2xl px-6 py-7 sm:px-8 sm:py-8">
          <div className="flex flex-col justify-between gap-6 md:flex-row md:items-center">
            <div className="max-w-2xl">
              <div className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-[0.14em] text-brand-600"><Sparkles size={13} />Curated Gallery</div>
              <h1 className="text-heading mt-2 text-2xl font-bold tracking-[-0.025em] sm:text-[30px]">Start with a proven pattern.</h1>
              <p className="text-muted mt-2 max-w-xl text-sm leading-6">Browse strictly validated, unsigned workflow templates managed by trusted Publishers.</p>
            </div>
            {canManage && <label className="btn-core btn-secondary cursor-pointer">{importing ? <LoaderCircle className="animate-spin" size={14} /> : <Upload size={14} />}Import package<input className="hidden" type="file" accept="application/json,.json" disabled={importing} onChange={(event) => { void importTemplate(event.target.files?.[0]); event.currentTarget.value = '' }} /></label>}
          </div>
        </section>

        <section className="mt-7">
          <div className="mb-4 flex items-center justify-between gap-4">
            <h2 className="text-heading text-base font-bold">Available Templates</h2>
            <div className="relative w-64"><Search className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--text-muted)]" size={14} /><input type="search" placeholder="Search templates..." className="frappe-control w-full py-1.5 pl-9 pr-3 text-sm" value={search} onChange={(event) => setSearch(event.target.value)} /></div>
          </div>
          {error && <div className="mb-4 rounded-lg border border-red-200 bg-red-50 p-4 text-xs text-red-600 dark:border-red-900 dark:bg-red-500/10">{error}</div>}
          {loading ? <div className="grid min-h-52 place-items-center"><LoaderCircle className="animate-spin text-brand-500" /></div> : rows.length ? (
            <>
              <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
                {rows.map((row) => <div key={row.name} className="surface-flat flex flex-col overflow-hidden rounded-xl border border-[var(--border-color)] transition-shadow hover:shadow-md">
                  <div className="relative aspect-video w-full bg-slate-100 dark:bg-slate-800">{row.preview_image ? <img src={row.preview_image} alt={row.title} className="absolute inset-0 h-full w-full object-cover" /> : <div className="grid h-full place-items-center text-[var(--text-muted)]"><Layers3 size={32} opacity={0.2} /></div>}<span className="absolute left-3 top-3 rounded-md bg-white/90 px-2 py-1 text-[9px] font-bold uppercase tracking-wider text-slate-800">{row.category}</span></div>
                  <div className="flex flex-1 flex-col p-4"><h3 className="text-heading font-bold">{row.title}</h3><p className="text-muted mt-1 line-clamp-2 flex-1 text-xs">{row.description || 'No description provided.'}</p><div className="mt-4 flex flex-wrap items-center justify-between gap-2 border-t border-[var(--border-color)] pt-4"><span className="rounded bg-[var(--subtle-fg)] px-2 py-1 text-[10px] font-semibold text-[var(--text-muted)]">{row.primary_doctype}</span><div className="flex gap-2">{canManage && <button className="btn-core btn-ghost" disabled={Boolean(busyTemplate)} onClick={() => void exportTemplate(row)} aria-label={`Export ${row.title}`}><Download size={13} /></button>}<button className="btn-core btn-secondary" disabled={Boolean(busyTemplate)} onClick={() => void createFromTemplate(row)}>{busyTemplate === row.name ? <LoaderCircle className="animate-spin" size={13} /> : <Copy size={13} />}Use template</button></div></div></div>
                </div>)}
              </div>
              {hasMore && <div className="mt-6 text-center"><button className="btn-core btn-secondary" disabled={loadingMore} onClick={() => void load(rows.length, true)}>{loadingMore && <LoaderCircle className="animate-spin" size={14} />}Load more</button></div>}
            </>
          ) : error ? <div className="px-6 py-12 text-center"><AlertTriangle className="mx-auto text-red-500" size={22} /><h3 className="text-heading mt-3 text-sm font-bold">Unable to load templates</h3><button className="btn-core btn-primary mt-4" onClick={() => void load()}>Try again</button></div> : <div className="surface-flat rounded-xl px-6 py-16 text-center"><Layers3 className="mx-auto text-[var(--text-muted)]" /><h3 className="text-heading mt-4 text-base font-bold">No templates found</h3></div>}
        </section>
      </main>
    </div>
  )
}
