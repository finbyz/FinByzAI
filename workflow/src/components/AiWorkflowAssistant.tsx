import { AlertTriangle, ArrowUp, CheckCircle2, LoaderCircle, Sparkles, WandSparkles, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { call, mutationEnvelope } from '../lib/api'
import { useWorkflowActions, useWorkflowDocument } from '../state/WorkflowContext'
import type { ValidationIssue, WorkflowGraph } from '../types'
import { useDialogA11y } from './useDialogA11y'

interface AuthoringStatus {
  available: boolean
  reason?: string
  max_prompt_characters: number
  primary_doctype?: string
  suggestions: string[]
}

interface DraftProposal {
  summary: string
  assumptions: string[]
  warnings: string[]
  graph: WorkflowGraph
  issues: ValidationIssue[]
  graph_hash: string
  node_count: number
  latency_ms: number
  model: string
  usage: { input_tokens: number; output_tokens: number; total_tokens: number }
  mutated: false
  published: false
}

const secondary = 'btn-core btn-secondary'
const magic = 'btn-core btn-magic'

export function AiWorkflowAssistant() {
  const doc = useWorkflowDocument()
  const actions = useWorkflowActions()
  const [open, setOpen] = useState(false)
  const [status, setStatus] = useState<AuthoringStatus>()
  const [prompt, setPrompt] = useState('')
  const [proposal, setProposal] = useState<DraftProposal>()
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [applied, setApplied] = useState(false)
  const promptRef = useRef<HTMLTextAreaElement>(null)
  const close = () => { if (!loading) setOpen(false) }
  const dialogRef = useDialogA11y(open, close, 'Build workflow with AI')

  useEffect(() => {
    if (!open || status) return
    let active = true
    void call<AuthoringStatus>('get_ai_workflow_authoring_status', { workflow_id: doc.workflowId })
      .then((result) => { if (active) setStatus(result) })
      .catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : 'Unable to load AI workflow authoring') })
    return () => { active = false }
  }, [doc.workflowId, open, status])

  useEffect(() => {
    if (open) window.setTimeout(() => promptRef.current?.focus(), 50)
  }, [open])

  const generate = async () => {
    if (!doc.graph || prompt.trim().length < 12) return
    setLoading(true)
    setError('')
    setApplied(false)
    try {
      const result = await call<DraftProposal>('generate_ai_workflow_draft', mutationEnvelope(doc.workflowId, { prompt: prompt.trim(), graph: doc.graph }), true)
      setProposal(result)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'AI could not prepare this workflow draft')
    } finally {
      setLoading(false)
    }
  }

  const apply = () => {
    if (!proposal) return
    actions.replaceGraph(proposal.graph, 'ai-generated-draft')
    setApplied(true)
  }

  const reset = () => {
    setProposal(undefined)
    setApplied(false)
    setError('')
    window.setTimeout(() => promptRef.current?.focus(), 20)
  }

  return (
    <>
      <button className={magic} onClick={() => setOpen(true)} title="Describe an automation and generate a reviewable draft"><WandSparkles size={14} /><span className="max-xl:hidden">Build with AI</span></button>
      {open && createPortal(
        <div className="dialog-backdrop fixed inset-0 z-[70] grid place-items-center p-4" role="dialog" aria-modal="true" aria-labelledby="ai-workflow-authoring-title" onClick={(event) => { if (event.target === event.currentTarget) close() }}>
          <div ref={dialogRef} tabIndex={-1} className="dialog-card w-full max-w-3xl overflow-hidden rounded-2xl">
            <header className="hero-glow relative border-b border-[var(--border-color)] px-5 py-5 sm:px-7">
              <button className="icon-button absolute right-4 top-4" onClick={close} disabled={loading} aria-label="Close AI workflow assistant"><X size={17} /></button>
              <div className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-[0.13em] text-magic-600"><Sparkles size={13} />AI workflow assistant <span className="rounded bg-magic-50 px-1.5 py-0.5 text-[8px] dark:bg-magic-500/10">Beta</span></div>
              <h2 id="ai-workflow-authoring-title" className="text-heading mt-2 text-xl font-bold">What do you want to automate?</h2>
              <p className="text-muted mt-1 text-xs">Describe the trigger, decisions, delays, and actions. AI prepares a draft; you review and apply it before saving or publishing.</p>
            </header>
            <div className="max-h-[72vh] overflow-y-auto p-5 sm:p-7">
              {!status && !error && <div className="grid min-h-44 place-items-center"><LoaderCircle className="animate-spin text-magic-500" size={22} /></div>}
              {status && !status.available && <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-500/10 dark:text-amber-200"><div className="flex gap-2"><AlertTriangle className="mt-0.5 shrink-0" size={15} /><div><strong className="block">AI workflow authoring needs setup</strong><p className="mt-1 leading-5">{status.reason}</p>{window.frappe?.boot?.roles?.includes('System Manager') && <a className="mt-3 inline-block font-bold underline" href="/app/automation-settings">Open Automation Settings</a>}</div></div></div>}
              {status?.available && !proposal && <>
                <div className="rounded-2xl border border-magic-200 bg-gradient-to-br from-white via-white to-magic-50/60 p-3 shadow-sm dark:border-magic-800 dark:from-white/5 dark:via-white/5 dark:to-magic-500/10">
                  <textarea ref={promptRef} className="text-heading min-h-32 w-full resize-y bg-transparent p-2 text-sm leading-6 outline-none placeholder:text-[var(--text-light)]" maxLength={status.max_prompt_characters} value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder={status.suggestions[0] ? `Example: ${status.suggestions[0]}` : `Describe an automation for this ${status.primary_doctype || doc.graph?.primary_doctype || 'record'}…`} onKeyDown={(event) => { if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') { event.preventDefault(); void generate() } }} />
                  <div className="flex items-center justify-between border-t border-magic-100 px-2 pt-3 dark:border-magic-900"><span className="text-light text-[9px]">{prompt.length}/{status.max_prompt_characters} · Ctrl/⌘ + Enter</span><button className={`${magic} !size-9 !rounded-full !p-0`} aria-label="Generate workflow draft" disabled={loading || prompt.trim().length < 12} onClick={() => void generate()}>{loading ? <LoaderCircle className="animate-spin" size={15} /> : <ArrowUp size={16} />}</button></div>
                </div>
                <div className="mt-4 flex flex-wrap gap-2">{status.suggestions.map((suggestion) => <button type="button" className="rounded-full border border-[var(--border-color)] bg-[var(--subtle-fg)] px-3 py-1.5 text-[10px] font-semibold text-[var(--text-muted)] hover:border-magic-300 hover:text-magic-700" key={suggestion} onClick={() => setPrompt(suggestion)}>{suggestion}</button>)}</div>
                <p className="text-muted mt-5 flex items-start gap-2 text-[10px] leading-4"><CheckCircle2 className="mt-0.5 shrink-0 text-emerald-600" size={13} />The assistant only sees this workflow’s permission-scoped node catalogue and field metadata. It cannot execute actions, access record data, or publish. Generating a draft calls the configured provider and may be billable.</p>
              </>}
              {proposal && <div className="space-y-4">
                <section className="rounded-xl border border-[var(--border-color)] bg-[var(--subtle-fg)] p-4"><div className="flex flex-wrap items-center justify-between gap-2"><div><p className="text-light text-[9px] font-bold uppercase tracking-wider">Proposed draft</p><h3 className="text-heading mt-1 text-sm font-bold">{proposal.node_count} steps prepared</h3></div><span className={`status-pill ${proposal.issues.length ? 'border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-900 dark:bg-amber-500/10 dark:text-amber-300' : 'border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-500/10 dark:text-emerald-300'}`}>{proposal.issues.length ? `${proposal.issues.length} setup item${proposal.issues.length === 1 ? '' : 's'}` : 'Ready to review'}</span></div><p className="text-muted mt-3 text-xs leading-5">{proposal.summary}</p><p className="text-light mt-2 text-[9px]">Generated in {(proposal.latency_ms / 1000).toFixed(1)}s · {proposal.model} · {proposal.usage.total_tokens.toLocaleString()} tokens</p></section>
                {proposal.assumptions.length > 0 && <section><h4 className="text-heading text-[10px] font-bold uppercase tracking-wider">Assumptions</h4><ul className="text-muted mt-2 space-y-1.5 text-[10.5px] leading-4">{proposal.assumptions.map((item, index) => <li className="flex gap-2" key={`${item}-${index}`}><span>•</span><span>{item}</span></li>)}</ul></section>}
                {(proposal.warnings.length > 0 || proposal.issues.length > 0) && <section className="rounded-xl border border-amber-200 bg-amber-50/70 p-4 dark:border-amber-900 dark:bg-amber-500/10"><h4 className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-wider text-amber-800 dark:text-amber-200"><AlertTriangle size={13} />Review before publishing</h4><ul className="mt-2 space-y-1.5 text-[10.5px] leading-4 text-amber-800 dark:text-amber-200">{proposal.warnings.map((item, index) => <li key={`warning-${index}`}>• {item}</li>)}{proposal.issues.slice(0, 12).map((issue, index) => <li key={`${issue.code}-${index}`}>• {issue.message}</li>)}</ul>{proposal.issues.length > 12 && <p className="mt-2 text-[9px] font-semibold">Plus {proposal.issues.length - 12} more items shown by Workflow Check after applying.</p>}</section>}
                {applied && <p className="flex items-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-[11px] font-semibold text-emerald-700 dark:border-emerald-900 dark:bg-emerald-500/10 dark:text-emerald-300"><CheckCircle2 size={15} />Draft applied to the canvas. Review each highlighted placeholder, then save and check. Undo restores the previous graph.</p>}
                <div className="flex flex-wrap justify-end gap-2 border-t border-[var(--border-color)] pt-4"><button className={secondary} onClick={reset} disabled={loading}>Revise request</button><button className={magic} onClick={apply} disabled={applied}><WandSparkles size={14} />{applied ? 'Applied to canvas' : 'Apply draft to canvas'}</button></div>
              </div>}
              {error && <div className="mt-4 flex gap-2 rounded-xl border border-red-200 bg-red-50 p-4 text-xs text-red-700 dark:border-red-900 dark:bg-red-500/10 dark:text-red-300" role="alert"><AlertTriangle className="mt-0.5 shrink-0" size={15} /><span>{error}</span></div>}
            </div>
          </div>
        </div>, document.body)}
    </>
  )
}
