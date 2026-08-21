import {
  AlertTriangle,
  ArrowRight,
  Database,
  Clock3,
  GitBranch,
  LoaderCircle,
  Search,
  SlidersHorizontal,
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
  Logic: GitBranch,
  Actions: Zap,
  Data: Database,
  External: ShieldCheck,
  Delays: Clock3,
}
const categoryColors: Record<string, string> = {
  Triggers: 'text-emerald-500',
  Logic: 'text-brand-500',
  Actions: 'text-magic-500',
  Data: 'text-cyan-600',
  External: 'text-amber-500',
  Delays: 'text-cyan-600',
}
const RECENT_ACTIONS_KEY = 'finbyz-workflow-recent-actions'

function CatalogAction({ item, betweenSteps, onChoose, onRemember }: { item: NodeCatalogItem; betweenSteps: boolean; onChoose(): void; onRemember(): void }) {
  const TypeIcon = resolveNodeTypeIcon(item.type)
  const unavailableBetweenSteps = Boolean(betweenSteps && ['end.complete', 'action.delete_record', 'action.go_to'].includes(item.type))
  const unavailable = item.available === false
  return <button
    title={unavailable ? item.unavailable_reason || 'This action is not available for this workflow' : unavailableBetweenSteps ? 'This step ends a path and can only be added at the end' : item.description}
    disabled={unavailableBetweenSteps || unavailable}
    draggable={!unavailableBetweenSteps && !unavailable}
    onDragStart={(event) => {
      onRemember()
      event.dataTransfer.effectAllowed = 'copy'
      event.dataTransfer.setData('application/x-finbyz-workflow-node', JSON.stringify(item))
      event.dataTransfer.setData('text/plain', item.label)
    }}
    onClick={() => { if (!unavailableBetweenSteps && !unavailable) onChoose() }}
    className="catalog-item group flex w-full items-start gap-3 px-2.5 py-2.5 text-left disabled:cursor-not-allowed disabled:opacity-45"
  >
    <span className="mt-0.5 grid size-8 shrink-0 place-items-center rounded-lg border border-[var(--border-color)] bg-white/50 text-brand-600 shadow-sm transition group-hover:border-brand-200 group-hover:bg-brand-50 dark:bg-white/10 dark:group-hover:bg-brand-500/10"><TypeIcon size={15} /></span>
    <span className="min-w-0">
      <span className="flex items-center gap-2"><span className="text-heading block text-xs font-semibold leading-5">{item.label}</span>{item.authoring_tier === 'danger' && <span className="rounded bg-red-50 px-1.5 py-0.5 text-[8px] font-bold uppercase text-red-600 dark:bg-red-500/15 dark:text-red-300">Destructive</span>}{unavailable && <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[8px] font-bold uppercase text-slate-500 dark:bg-white/10 dark:text-slate-300">Unavailable</span>}</span>
      <span className="text-muted mt-0.5 line-clamp-2 block text-[10.5px] leading-4">{unavailable ? item.unavailable_reason || item.description : item.description}</span>
    </span>
  </button>
}

export function NodeCatalog() {
  const [items, setItems] = useState<NodeCatalogItem[]>([])
  const [loading, setLoading] = useState(true)
  const [loadAttempt, setLoadAttempt] = useState(0)
  const [query, setQuery] = useState('')
  const [error, setError] = useState('')
  const [delayChooserOpen, setDelayChooserOpen] = useState(false)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [recentTypes, setRecentTypes] = useState<string[]>(() => {
    try { return JSON.parse(window.localStorage.getItem(RECENT_ACTIONS_KEY) || '[]') as string[] } catch { return [] }
  })
  const actions = useWorkflowActions()
  const { workflowId } = useWorkflowDocument()
  const { insertion, catalogOpen } = useWorkflowEditor()

  useEffect(() => {
    let active = true
    setLoading(true)
    setError('')
    void call<{ node_types: NodeCatalogItem[] }>('get_node_types', { workflow_id: workflowId })
      .then((value) => { if (active) setItems(value.node_types) })
      .catch((reason: unknown) => { if (active) setError(reason instanceof Error ? reason.message : 'Unable to load actions') })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [loadAttempt, workflowId])

  useEffect(() => {
    if (!catalogOpen) return
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
		actions.toggle('catalogOpen', false)
        actions.cancelInsert()
      }
    }
    document.addEventListener('keydown', closeOnEscape)
    return () => document.removeEventListener('keydown', closeOnEscape)
  }, [actions, catalogOpen])

  useEffect(() => {
    if (!insertion) return
	actions.toggle('catalogOpen', true)
    setQuery('')
  }, [actions, insertion])

  const grouped = useMemo(() => {
    const searching = Boolean(query.trim())
    const visible = items.filter((item) => {
      if (item.type.startsWith('trigger.') || item.authoring_hidden) return false
      if (!`${item.label} ${item.description}`.toLowerCase().includes(query.toLowerCase())) return false
      return searching || showAdvanced || (item.authoring_tier || 'core') === 'core'
    })
    return visible.reduce<Record<string, NodeCatalogItem[]>>((groups, item) => {
      groups[item.category] = [...(groups[item.category] || []), item]
      return groups
    }, {})
  }, [items, query, showAdvanced])
  const advancedCount = useMemo(() => items.filter((item) => !item.type.startsWith('trigger.') && !item.authoring_hidden && (item.authoring_tier || 'core') !== 'core').length, [items])
  const quickItems = useMemo(() => {
    const available = items.filter((item) => !item.type.startsWith('trigger.') && !item.authoring_hidden && item.available !== false && (item.authoring_tier || 'core') === 'core' && item.category !== 'Delays')
    const recent = recentTypes.flatMap((type) => available.find((item) => item.type === type) || [])
    if (recent.length) return recent.slice(0, 4)
    const recommended = ['action.update_record', 'action.send_email', 'condition.if_else', 'action.create_todo']
    return recommended.flatMap((type) => available.find((item) => item.type === type) || []).slice(0, 4)
  }, [items, recentTypes])
  const remember = (type: string) => setRecentTypes((current) => {
    const next = [type, ...current.filter((item) => item !== type)].slice(0, 6)
    window.localStorage.setItem(RECENT_ACTIONS_KEY, JSON.stringify(next))
    return next
  })
  const choose = (item: NodeCatalogItem) => {
    remember(item.type)
    actions.addNode(item)
    actions.toggle('catalogOpen', false)
  }
  return (
    <>
      {catalogOpen && <button type="button" className="absolute inset-0 z-[35] hidden bg-slate-950/25 backdrop-blur-[1px] max-lg:block" onClick={() => { actions.toggle('catalogOpen', false); actions.cancelInsert() }} aria-label="Close step catalog" />}
      <aside aria-label="Workflow step catalog" aria-hidden={!catalogOpen} className={`editor-side-panel flex flex-col h-full overflow-hidden border-r border-[var(--border-color)] bg-white/70 dark:bg-[#18212b]/80 backdrop-blur-2xl transition-[transform,visibility] max-lg:absolute max-lg:inset-y-0 max-lg:left-0 max-lg:z-40 max-lg:w-full sm:max-lg:w-80 max-lg:shadow-2xl ${catalogOpen ? 'visible translate-x-0' : 'invisible -translate-x-full'}`}>
        <div className="shrink-0 sticky top-0 z-10 border-b border-[var(--border-color)] bg-white/50 dark:bg-[#18212b]/50 px-4 pb-4 pt-4 backdrop-blur-md">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-heading text-[13px] font-bold">{insertion ? 'Choose a step' : 'Add a step'}</p>
              <p className="text-muted mt-0.5 text-[11px]">{insertion ? 'It will be connected automatically' : 'Use a + on the canvas or drag a step'}</p>
            </div>
            <div className="flex items-center gap-2">
              <span className="magic-orb !size-8 !rounded-[9px]"><Sparkles size={14} /></span>
			  <button className="icon-button" onClick={() => { actions.toggle('catalogOpen', false); actions.cancelInsert() }} aria-label="Close catalog"><X size={16} /></button>
            </div>
          </div>
        <label className="relative mt-3 block">
          <Search className="text-light pointer-events-none absolute left-3 top-1/2 -translate-y-1/2" size={14} />
          <input className="frappe-control h-9 pl-9 pr-3 text-xs" aria-label="Search workflow steps" placeholder="What should happen next?" value={query} onChange={(event) => setQuery(event.target.value)} autoFocus={catalogOpen} />
        </label>
      </div>

      <div className="flex-1 overflow-y-auto px-3 pb-8 pt-2">
		{insertion && <div className="mx-1 mt-2 flex items-center gap-2 rounded-xl border border-brand-200 bg-brand-50/80 px-3 py-2.5 text-brand-800 dark:border-brand-500/30 dark:bg-brand-500/10 dark:text-brand-200"><span className="grid size-6 shrink-0 place-items-center rounded-full bg-brand-600 text-white"><Plus size={13} /></span><span className="min-w-0 flex-1"><strong className="block text-[10.5px]">Adding here</strong><span className="block truncate text-[9.5px] opacity-80">{insertion.label || 'Selected workflow path'}</span></span><button type="button" className="rounded-md px-2 py-1 text-[9.5px] font-bold hover:bg-brand-100 dark:hover:bg-white/10" onClick={() => actions.cancelInsert()}>Cancel</button></div>}
        {loading && <div className="px-3 py-12 text-center" role="status"><LoaderCircle className="mx-auto animate-spin text-brand-500" size={20} /><p className="text-heading mt-3 text-xs font-semibold">Loading workflow steps…</p><p className="text-muted mt-1 text-[11px]">Checking the actions available to this site.</p></div>}
        {!loading && error && <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-[10.5px] leading-4 text-amber-800 dark:border-amber-900 dark:bg-amber-500/10 dark:text-amber-300" role="alert"><div className="flex gap-2"><AlertTriangle className="mt-0.5 shrink-0" size={13} /><span>{error}</span></div><button type="button" className="mt-2 rounded-md border border-amber-300 bg-white/70 px-2.5 py-1 text-[10px] font-bold dark:bg-white/5" onClick={() => setLoadAttempt((attempt) => attempt + 1)}>Try again</button></div>}
        {!loading && !error && !query && advancedCount > 0 && <button type="button" className="text-muted mx-1 mt-3 flex w-[calc(100%-0.5rem)] items-center gap-2 rounded-lg border border-[var(--border-color)] px-3 py-2 text-left text-[10.5px] font-semibold hover:bg-slate-50 dark:hover:bg-white/5" aria-expanded={showAdvanced} onClick={() => { setShowAdvanced((value) => !value); setDelayChooserOpen(false) }}><SlidersHorizontal size={13} /><span className="flex-1">{showAdvanced ? 'Hide advanced actions' : 'Show advanced actions'}</span><span className="text-light">{advancedCount}</span></button>}
        {!loading && !error && !query && !delayChooserOpen && quickItems.length > 0 && <section className="mt-4"><div className="mb-1.5 flex items-center gap-2 px-2"><Sparkles className="text-brand-500" size={12} /><h3 className="text-brand-500 text-[10px] font-bold uppercase tracking-[0.13em]">{recentTypes.length ? 'Recently used' : 'Recommended'}</h3></div><div className="space-y-0.5">{quickItems.map((item) => <CatalogAction key={`quick-${item.type}`} item={item} betweenSteps={Boolean(insertion?.edgeId)} onRemember={() => remember(item.type)} onChoose={() => choose(item)} />)}</div></section>}
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
                {category === 'Delays' && !query && !delayChooserOpen ? <button type="button" className="catalog-item group flex w-full items-start gap-3 px-2.5 py-2.5 text-left" onClick={() => setDelayChooserOpen(true)}><span className="mt-0.5 grid size-8 shrink-0 place-items-center rounded-lg border border-[var(--border-color)] bg-white/50 text-cyan-600 shadow-sm dark:bg-white/10"><Clock3 size={15} /></span><span><strong className="text-heading block text-xs font-semibold">Delay</strong><span className="text-muted mt-0.5 block text-[10.5px]">Choose a duration, date, event, day/time, or business window</span></span><ArrowRight className="text-light ml-auto mt-2" size={13} /></button> : rows.map((item) => <CatalogAction key={item.type} item={item} betweenSteps={Boolean(insertion?.edgeId)} onRemember={() => remember(item.type)} onChoose={() => choose(item)} />)}
                {category === 'Delays' && delayChooserOpen && !query && <button type="button" className="text-muted ml-2 mt-1 text-[10px] font-semibold" onClick={() => setDelayChooserOpen(false)}>← Back to Delay</button>}
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
    {!catalogOpen && (
      <button className="absolute bottom-6 right-4 z-30 flex size-12 items-center justify-center rounded-full bg-brand-500 text-white shadow-xl transition-transform hover:scale-105 hover:bg-brand-600 lg:hidden" onClick={() => actions.toggle('catalogOpen', true)} aria-label="Open catalog">
        <Plus size={22} />
      </button>
    )}
    </>
  )
}
