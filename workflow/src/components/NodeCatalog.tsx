import {
  AlertTriangle,
  ArrowRight,
  Database,
  GitBranch,
  LoaderCircle,
  Play,
  Search,
  ShieldCheck,
  Sparkles,
  Zap,
  X,
  Plus
} from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { call } from '../lib/api'
import { useWorkflowActions, useWorkflowDocument, useWorkflowEditor } from '../state/WorkflowContext'
import type { NodeCatalogItem } from '../types'
import { resolveNodeTypeIcon } from './nodeCatalogIcons'

const categoryIcons: Record<string, typeof Zap> = {
  Triggers: Play,
  Logic: GitBranch,
  Actions: Zap,
  Data: Database,
  External: ShieldCheck,
}
const categoryColors: Record<string, string> = {
  Triggers: 'text-emerald-500',
  Logic: 'text-brand-500',
  Actions: 'text-magic-500',
  Data: 'text-cyan-600',
  External: 'text-amber-500',
}

export function NodeCatalog() {
  const [items, setItems] = useState<NodeCatalogItem[]>([])
  const [loading, setLoading] = useState(true)
  const [loadAttempt, setLoadAttempt] = useState(0)
  const [query, setQuery] = useState('')
  const [error, setError] = useState('')
  const [pendingTrigger, setPendingTrigger] = useState<NodeCatalogItem | null>(null)
  const [triggerChooserOpen, setTriggerChooserOpen] = useState(false)
  const [mobileOpen, setMobileOpen] = useState(false)
  const actions = useWorkflowActions()
  const { graph } = useWorkflowDocument()
  const { insertion } = useWorkflowEditor()
  const triggerNode = graph?.nodes.find((node) => node.id === graph.start_node_id && node.type.startsWith('trigger.'))
  const triggerLabel = items.find((item) => item.type === triggerNode?.type)?.label || 'Current trigger'

  useEffect(() => {
    let active = true
    setLoading(true)
    setError('')
    void call<{ node_types: NodeCatalogItem[] }>('get_node_types')
      .then((value) => { if (active) setItems(value.node_types) })
      .catch((reason: unknown) => { if (active) setError(reason instanceof Error ? reason.message : 'Unable to load actions') })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [loadAttempt])

  useEffect(() => {
    if (!mobileOpen) return
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setMobileOpen(false)
        actions.cancelInsert()
      }
    }
    document.addEventListener('keydown', closeOnEscape)
    return () => document.removeEventListener('keydown', closeOnEscape)
  }, [actions, mobileOpen])

  useEffect(() => {
    if (!insertion) return
    setMobileOpen(true)
    setQuery('')
  }, [insertion])

  const grouped = useMemo(() => {
    const visible = items.filter((item) => !item.type.startsWith('trigger.') && !item.authoring_hidden && `${item.label} ${item.description}`.toLowerCase().includes(query.toLowerCase()))
    return visible.reduce<Record<string, NodeCatalogItem[]>>((groups, item) => {
      groups[item.category] = [...(groups[item.category] || []), item]
      return groups
    }, {})
  }, [items, query])
  const triggerItems = items.filter((item) => item.type.startsWith('trigger.') && !item.authoring_hidden)

  return (
    <>
      {mobileOpen && <button type="button" className="absolute inset-0 z-[35] hidden bg-slate-950/25 backdrop-blur-[1px] max-lg:block" onClick={() => { setMobileOpen(false); actions.cancelInsert() }} aria-label="Close step catalog" />}
      <aside aria-label="Workflow step catalog" className={`editor-side-panel flex flex-col h-full overflow-hidden border-r border-[var(--border-color)] bg-white/70 dark:bg-[#18212b]/80 backdrop-blur-2xl transition-[transform,visibility] max-lg:absolute max-lg:inset-y-0 max-lg:left-0 max-lg:z-40 max-lg:w-full sm:max-lg:w-80 max-lg:shadow-2xl ${mobileOpen ? 'max-lg:visible max-lg:translate-x-0' : 'max-lg:invisible max-lg:-translate-x-full'}`}>
        <div className="shrink-0 sticky top-0 z-10 border-b border-[var(--border-color)] bg-white/50 dark:bg-[#18212b]/50 px-4 pb-4 pt-4 backdrop-blur-md">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-heading text-[13px] font-bold">{insertion ? 'Choose a step' : 'Add a step'}</p>
              <p className="text-muted mt-0.5 text-[11px]">{insertion ? 'It will be connected automatically' : 'Use a + on the canvas or drag a step'}</p>
            </div>
            <div className="flex items-center gap-2">
              <span className="magic-orb !size-8 !rounded-[9px]"><Sparkles size={14} /></span>
              <button className="icon-button lg:hidden" onClick={() => { setMobileOpen(false); actions.cancelInsert() }} aria-label="Close catalog"><X size={16} /></button>
            </div>
          </div>
        <label className="relative mt-3 block">
          <Search className="text-light pointer-events-none absolute left-3 top-1/2 -translate-y-1/2" size={14} />
          <input className="frappe-control h-9 pl-9 pr-3 text-xs" aria-label="Search workflow steps" placeholder="Search steps" value={query} onChange={(event) => setQuery(event.target.value)} />
        </label>
      </div>

      <div className="flex-1 overflow-y-auto px-3 pb-8 pt-2">
		{insertion && <div className="mx-1 mt-2 flex items-center gap-2 rounded-xl border border-brand-200 bg-brand-50/80 px-3 py-2.5 text-brand-800 dark:border-brand-500/30 dark:bg-brand-500/10 dark:text-brand-200"><span className="grid size-6 shrink-0 place-items-center rounded-full bg-brand-600 text-white"><Plus size={13} /></span><span className="min-w-0 flex-1"><strong className="block text-[10.5px]">Adding here</strong><span className="block truncate text-[9.5px] opacity-80">{insertion.label || 'Selected workflow path'}</span></span><button type="button" className="rounded-md px-2 py-1 text-[9.5px] font-bold hover:bg-brand-100 dark:hover:bg-white/10" onClick={() => actions.cancelInsert()}>Cancel</button></div>}
        {loading && <div className="px-3 py-12 text-center" role="status"><LoaderCircle className="mx-auto animate-spin text-brand-500" size={20} /><p className="text-heading mt-3 text-xs font-semibold">Loading workflow steps…</p><p className="text-muted mt-1 text-[11px]">Checking the actions available to this site.</p></div>}
        {!loading && error && <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-[10.5px] leading-4 text-amber-800 dark:border-amber-900 dark:bg-amber-500/10 dark:text-amber-300" role="alert"><div className="flex gap-2"><AlertTriangle className="mt-0.5 shrink-0" size={13} /><span>{error}</span></div><button type="button" className="mt-2 rounded-md border border-amber-300 bg-white/70 px-2.5 py-1 text-[10px] font-bold dark:bg-white/5" onClick={() => setLoadAttempt((attempt) => attempt + 1)}>Try again</button></div>}
        {!loading && !error && !query && triggerNode && <section className="mt-3 rounded-xl border border-emerald-200 bg-emerald-50/60 p-2.5 dark:border-emerald-500/20 dark:bg-emerald-500/5"><div className="mb-2 flex items-center gap-2 px-1"><Play className="text-emerald-600" size={12} /><h3 className="text-[10px] font-bold uppercase tracking-[0.13em] text-emerald-700 dark:text-emerald-300">Enrollment trigger</h3></div><div className="flex items-center gap-2"><button type="button" className="catalog-item flex min-w-0 flex-1 items-center gap-2.5 px-2.5 py-2 text-left" onClick={() => actions.select(triggerNode.id)}><span className="grid size-8 shrink-0 place-items-center rounded-lg border border-emerald-200 bg-white/70 text-emerald-600 dark:bg-white/10"><Play size={14} /></span><span className="min-w-0"><strong className="text-heading block truncate text-[11px]">{triggerLabel}</strong><span className="text-muted block truncate text-[9.5px]">Open trigger settings</span></span></button><button type="button" className="rounded-lg border border-emerald-200 bg-white/70 px-2.5 py-2 text-[10px] font-bold text-emerald-700 dark:bg-white/5 dark:text-emerald-300" onClick={() => { setTriggerChooserOpen((current) => !current); setPendingTrigger(null) }}>{triggerChooserOpen ? 'Close' : 'Change'}</button></div>{triggerChooserOpen && <div className="mt-2 grid gap-1 border-t border-emerald-200/70 pt-2 dark:border-emerald-500/20">{triggerItems.filter((item) => item.type !== triggerNode.type).map((item) => { const TypeIcon = resolveNodeTypeIcon(item.type); return <button type="button" className="flex items-center gap-2 rounded-lg px-2 py-2 text-left hover:bg-white/70 dark:hover:bg-white/5" key={item.type} onClick={() => setPendingTrigger(item)}><TypeIcon className="shrink-0 text-emerald-600" size={13} /><span><strong className="text-heading block text-[10.5px]">{item.label}</strong><span className="text-muted line-clamp-1 text-[9px]">{item.description}</span></span></button> })}</div>}{pendingTrigger && <div className="mt-2 rounded-lg border border-brand-200 bg-white/80 p-2.5 dark:border-brand-500/30 dark:bg-slate-900/50"><p className="text-heading text-[10.5px] font-bold">Change enrollment trigger?</p><p className="text-muted mt-1 flex items-center gap-1 text-[9.5px]"><span>{triggerLabel}</span><ArrowRight size={10} /><span className="font-semibold text-brand-700 dark:text-brand-300">{pendingTrigger.label}</span></p><p className="text-muted mt-1 text-[9px] leading-4">Connections remain; trigger-specific settings reset.</p><div className="mt-2 flex justify-end gap-1.5"><button className="rounded-lg px-2 py-1 text-[9.5px] font-semibold text-[var(--text-muted)]" onClick={() => setPendingTrigger(null)}>Cancel</button><button className="rounded-lg bg-brand-600 px-2 py-1 text-[9.5px] font-bold text-white" onClick={() => { actions.replaceTrigger(pendingTrigger); setPendingTrigger(null); setTriggerChooserOpen(false) }}>Change</button></div></div>}</section>}
        {!loading && !error && Object.entries(grouped).map(([category, rows]) => {
          const CategoryIcon = categoryIcons[category] || Zap
          return (
            <section className="mt-4" key={category}>
              <div className="mb-1.5 flex items-center gap-2 px-2">
                <CategoryIcon className={categoryColors[category] || 'text-light'} size={12} />
                <h3 className={`${categoryColors[category] || 'text-light'} text-[10px] font-bold uppercase tracking-[0.13em]`}>{category}</h3>
                <span className="text-light ml-auto text-[10px]">{rows.length}</span>
              </div>
              <div className="space-y-0.5">
                {rows.map((item) => {
                  const TypeIcon = resolveNodeTypeIcon(item.type)
				  const unavailableBetweenSteps = Boolean(insertion?.edgeId && ['end.complete', 'action.delete_record', 'action.go_to'].includes(item.type))
                  return (
                    <button
                      key={item.type}
					  title={unavailableBetweenSteps ? 'This step ends a path and can only be added at the end' : item.description}
					  disabled={unavailableBetweenSteps}
					  draggable={!unavailableBetweenSteps}
                      onDragStart={(event) => {
                        event.dataTransfer.effectAllowed = 'copy'
                        event.dataTransfer.setData('application/x-finbyz-workflow-node', JSON.stringify(item))
                        event.dataTransfer.setData('text/plain', item.label)
                      }}
                      onClick={() => {
						if (unavailableBetweenSteps) return
                        actions.addNode(item)
                        setMobileOpen(false)
                      }}
					  className="catalog-item group flex w-full items-start gap-3 px-2.5 py-2.5 text-left disabled:cursor-not-allowed disabled:opacity-45"
                    >
                      <span className="mt-0.5 grid size-8 shrink-0 place-items-center rounded-lg border border-[var(--border-color)] bg-white/50 dark:bg-white/10 text-brand-600 shadow-sm transition group-hover:border-brand-200 group-hover:bg-brand-50 dark:group-hover:bg-brand-500/10">
                        <TypeIcon size={15} />
                      </span>
                      <span className="min-w-0">
                        <span className="flex items-center gap-2">
                          <span className="text-heading block text-xs font-semibold leading-5">{item.label}</span>
                        </span>
                        <span className="text-muted mt-0.5 line-clamp-2 block text-[10.5px] leading-4">{item.description}</span>
                      </span>
                    </button>
                  )
                })}
              </div>
            </section>
          )
        })}
        {!loading && !error && !Object.keys(grouped).length && (
          <div className="px-3 py-12 text-center">
            <Search className="text-light mx-auto" size={20} />
            <p className="text-heading mt-3 text-xs font-semibold">No steps found</p>
            <p className="text-muted mt-1 text-[11px]">Try a different search term.</p>
          </div>
        )}
      </div>
    </aside>
    {!mobileOpen && (
      <button className="absolute bottom-6 right-4 z-30 flex size-12 items-center justify-center rounded-full bg-brand-500 text-white shadow-xl transition-transform hover:scale-105 hover:bg-brand-600 lg:hidden" onClick={() => setMobileOpen(true)} aria-label="Open catalog">
        <Plus size={22} />
      </button>
    )}
    </>
  )
}
