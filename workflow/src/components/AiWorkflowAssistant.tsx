import {
  AlertTriangle,
  ArrowUp,
  Bot,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Clock,
  ExternalLink,
  GitBranch,
  Layers,
  LoaderCircle,
  Maximize2,
  Minimize2,
  Sparkles,
  User,
  WandSparkles,
  X,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { call, mutationEnvelope } from '../lib/api'
import { useWorkflowActions, useWorkflowDocument, useWorkflowEditor, type EditorState } from '../state/WorkflowContext'
import type { ValidationIssue, WorkflowGraph } from '../types'
import { useDialogA11y } from './useDialogA11y'
import { nodeLabels } from './InspectorHelpers'

interface AuthoringStatus {
  available: boolean
  reason?: string
  max_prompt_characters: number
  primary_doctype?: string
  suggestions: string[]
}

export interface DraftProposal {
  reply_type?: 'proposal'
  summary: string
  assumptions: string[]
  warnings: string[]
  graph: WorkflowGraph
  issues: ValidationIssue[]
  graph_hash: string
  node_count: number
  latency_ms: number
  model: string
  agent?: string
  mode?: string
  usage: { input_tokens: number; output_tokens: number; total_tokens: number }
  mutated: false
  published: false
}

export interface ClarifyReply {
  reply_type: 'question'
  message: string
  questions: string[]
  suggestions?: string[]
}

type ConverseResponse = DraftProposal | ClarifyReply

interface ServerChatTurn {
  role: 'user' | 'assistant'
  text: string
  reply_type?: 'question' | 'proposal'
  questions?: string[]
  timestamp?: string
}

interface NodeDiffItem {
  id: string
  type: string
  label: string
  summary?: string
}

interface GraphDiffSummary {
  added: NodeDiffItem[]
  modified: NodeDiffItem[]
  removed: NodeDiffItem[]
}

interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  scope?: string
  timestamp: number
  proposal?: DraftProposal
  replyType?: 'question' | 'proposal'
  questions?: string[]
  applied?: boolean
  diff?: GraphDiffSummary
  error?: string
  /** Answers the user typed against this turn's questions, by question index. */
  answered?: Record<number, string>
}

const MAX_STORED_TURNS = 30

/** The agent writes light markdown ("**Trigger**", numbered steps). Render just
 *  bold and line breaks - no library, no raw HTML, so nothing can be injected. */
function RichText({ text }: { text: string }) {
  const lines = String(text || '').split(/\r?\n|(?<=\.)\s(?=\d+\.\s)/)
  return (
    <>
      {lines.map((line, lineIndex) => (
        <span key={lineIndex} className="block">
          {line.split(/(\*\*[^*]+\*\*)/g).map((part, partIndex) =>
            part.startsWith('**') && part.endsWith('**') && part.length > 4 ? (
              <b key={partIndex} className="text-heading font-semibold">
                {part.slice(2, -2)}
              </b>
            ) : (
              <span key={partIndex}>{part}</span>
            )
          )}
        </span>
      ))}
    </>
  )
}

/** A set of short, question-mark-free options ("Yes, build it") is a choice to
 *  click, not something to type an answer to. */
function isChoiceSet(questions: string[]): boolean {
  return (
    questions.length > 0 &&
    questions.length <= 4 &&
    questions.every((q) => q.trim().length <= 28 && !q.includes('?'))
  )
}

const magic = 'btn-core btn-magic'

function getNodeLabel(type: string): string {
  return (nodeLabels as Record<string, string>)[type] || type.replace(/^(action|trigger|condition|delay|transform)\./, '').replace(/_/g, ' ')
}

function calculateGraphDiff(baseline?: WorkflowGraph, proposed?: WorkflowGraph): GraphDiffSummary {
  const currentNodes = baseline?.nodes || []
  const proposedNodes = proposed?.nodes || []
  const currentMap = new Map(currentNodes.map((n) => [n.id, n]))
  const proposedMap = new Map(proposedNodes.map((n) => [n.id, n]))

  const added: NodeDiffItem[] = []
  const modified: NodeDiffItem[] = []
  const removed: NodeDiffItem[] = []

  for (const node of proposedNodes) {
    const existing = currentMap.get(node.id)
    if (!existing) {
      added.push({ id: node.id, type: node.type, label: getNodeLabel(node.type) })
    } else if (
      existing.type !== node.type ||
      JSON.stringify(existing.config || {}) !== JSON.stringify(node.config || {})
    ) {
      modified.push({ id: node.id, type: node.type, label: getNodeLabel(node.type) })
    }
  }

  for (const node of currentNodes) {
    if (!proposedMap.has(node.id)) {
      removed.push({ id: node.id, type: node.type, label: getNodeLabel(node.type) })
    }
  }

  return { added, modified, removed }
}

function useSafeEditor(): EditorState | undefined {
  try {
    return useWorkflowEditor()
  } catch {
    return undefined
  }
}

export function AiWorkflowAssistant() {
  const doc = useWorkflowDocument()
  const actions = useWorkflowActions()
  const editor = useSafeEditor()

  const [open, setOpen] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const [status, setStatus] = useState<AuthoringStatus>()
  const [prompt, setPrompt] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [scopeMode, setScopeMode] = useState<'whole' | 'node'>('whole')
  const [thinkingStep, setThinkingStep] = useState(0)
  const [expandedDiffs, setExpandedDiffs] = useState<Record<string, boolean>>({})
  // Answers typed against an assistant question turn, keyed "<turnId>:<index>".
  const [answers, setAnswers] = useState<Record<string, string>>({})

  const storageKey = `finbyz:workflow_ai_turns:${doc.workflowId}`
  const [turns, setTurns] = useState<ChatMessage[]>(() => {
    try {
      const saved = window.localStorage.getItem(storageKey)
      return saved ? (JSON.parse(saved) as ChatMessage[]) : []
    } catch {
      return []
    }
  })

  const messagesEndRef = useRef<HTMLDivElement>(null)
  const promptRef = useRef<HTMLTextAreaElement>(null)

  const selectedNode = useMemo(() => {
    if (!editor?.selectedNodeId || !doc.graph) return undefined
    return doc.graph.nodes.find((n) => n.id === editor.selectedNodeId)
  }, [editor?.selectedNodeId, doc.graph])

  const selectedNodeLabel = selectedNode ? getNodeLabel(selectedNode.type) : undefined

  const close = useCallback(() => {
    if (!loading) {
      setOpen(false)
      actions.toggle?.('aiAssistantOpen', false)
    }
  }, [loading, actions])

  const dialogRef = useDialogA11y(open, close, 'Build workflow with AI')

  // Save turns to localStorage (offline cache). Bounded: keep the most recent
  // turns and drop the heavy graph payload from every proposal but the last one.
  useEffect(() => {
    if (!doc.workflowId) return
    try {
      const recent = turns.slice(-MAX_STORED_TURNS)
      let lastProposalIdx = -1
      recent.forEach((t, i) => {
        if (t.proposal) lastProposalIdx = i
      })
      const slim = recent.map((t, i) =>
        t.proposal && i !== lastProposalIdx
          ? { ...t, proposal: { ...t.proposal, graph: undefined as unknown as WorkflowGraph } }
          : t
      )
      window.localStorage.setItem(storageKey, JSON.stringify(slim))
    } catch {
      // Ignore quota errors
    }
  }, [turns, doc.workflowId, storageKey])

  // Sync external open request
  useEffect(() => {
    if (editor?.aiAssistantOpen && !open) {
      setOpen(true)
    }
  }, [editor?.aiAssistantOpen, open])

  // Auto-open if pending initial prompt exists
  useEffect(() => {
    if (!doc.workflowId) return
    const initPromptKey = `finbyz:ai_initial_prompt:${doc.workflowId}`
    if (window.sessionStorage.getItem(initPromptKey)) {
      setOpen(true)
    }
  }, [doc.workflowId])

  // Fetch authoring status on open
  useEffect(() => {
    if (!open || status) return
    let active = true
    void call<AuthoringStatus>('get_ai_workflow_authoring_status', { workflow_id: doc.workflowId })
      .then((result) => {
        if (active) setStatus(result)
      })
      .catch((reason) => {
        if (active) setError(reason instanceof Error ? reason.message : 'Unable to load AI workflow authoring')
      })
    return () => {
      active = false
    }
  }, [doc.workflowId, open, status])

  // One conversation per workflow per user: the server thread is the source of
  // truth, so reopening the panel (or opening it on another machine) resumes
  // exactly where this user left off on this workflow. The local cache only
  // carries the rich proposal payloads forward so Apply still works after a
  // reload.
  const [hydrated, setHydrated] = useState(false)
  useEffect(() => {
    if (!open || hydrated || !doc.workflowId) return
    setHydrated(true)
    void call<{ turns: ServerChatTurn[] }>('get_ai_workflow_chat', { workflow_id: doc.workflowId })
      .then((result) => {
        const server = (result?.turns || []).filter((t) => t && t.text)
        if (server.length === 0) return
        setTurns((local) =>
          server.map<ChatMessage>((t, i) => {
            const role = t.role === 'assistant' ? 'assistant' : 'user'
            const cached = local[i]
            const carry = cached && cached.role === role && cached.proposal ? cached : undefined
            return {
              id: carry?.id || `srv-${i}-${t.timestamp || i}`,
              role,
              content: t.text,
              replyType: t.reply_type,
              questions: t.reply_type === 'question' ? t.questions || [] : undefined,
              timestamp: Date.parse(t.timestamp || '') || Date.now(),
              proposal: carry?.proposal,
              diff: carry?.diff,
              applied: carry?.applied,
            }
          })
        )
      })
      .catch(() => {
        /* best effort - an empty thread or missing endpoint is fine */
      })
  }, [open, hydrated, doc.workflowId])

  // Auto focus textarea when opened
  useEffect(() => {
    if (open) window.setTimeout(() => promptRef.current?.focus(), 80)
  }, [open])

  // Thinking animation ticker
  useEffect(() => {
    if (!loading) return
    const interval = window.setInterval(() => {
      setThinkingStep((step) => (step + 1) % 3)
    }, 2400)
    return () => window.clearInterval(interval)
  }, [loading])

  // Scroll to bottom on new message
  useEffect(() => {
    if (open && turns.length > 0) {
      messagesEndRef.current?.scrollIntoView?.({ behavior: 'smooth' })
    }
  }, [turns, open, loading])

  const sendMessage = useCallback(
    async (inputPrompt: string) => {
      const cleanPrompt = inputPrompt.trim()
      if (!doc.graph || cleanPrompt.length < 2) return

      setLoading(true)
      setError('')

      const turnId = `turn-${Date.now()}`
      const isTargetingNode = scopeMode === 'node' && selectedNode
      const effectiveScope = isTargetingNode ? `${selectedNodeLabel} (${selectedNode.id})` : undefined

      let promptPayload = cleanPrompt
      if (isTargetingNode) {
        promptPayload = `[Focus edits specifically on step "${selectedNode.id}" (${selectedNodeLabel})]: ${cleanPrompt}`
      }

      // 'edit' when the canvas already has real steps, else 'generate'. The
      // server re-infers this too; sending it just improves the first turn.
      const hasExistingSteps = Boolean(doc.graph?.nodes && doc.graph.nodes.length > 1)
      const mode = hasExistingSteps ? 'edit' : 'generate'

      const userTurn: ChatMessage = {
        id: `${turnId}-user`,
        role: 'user',
        content: cleanPrompt,
        scope: effectiveScope,
        timestamp: Date.now(),
      }

      setTurns((prev) => [...prev, userTurn])
      setPrompt('')

      try {
        const result = await call<ConverseResponse>(
          'converse_ai_workflow_draft',
          mutationEnvelope(doc.workflowId, {
            message: promptPayload,
            graph: doc.graph,
            mode,
          }),
          true
        )

        if (result.reply_type === 'question') {
          setTurns((prev) => [
            ...prev,
            {
              id: `${turnId}-assistant`,
              role: 'assistant',
              content: result.message,
              replyType: 'question',
              questions: result.questions || [],
              timestamp: Date.now(),
            },
          ])
          return
        }

        const proposal = result as DraftProposal
        const diff = calculateGraphDiff(doc.graph, proposal.graph)
        setTurns((prev) => [
          ...prev,
          {
            id: `${turnId}-assistant`,
            role: 'assistant',
            content: proposal.summary,
            replyType: 'proposal',
            proposal,
            diff,
            applied: false,
            timestamp: Date.now(),
          },
        ])
      } catch (reason) {
        const message = reason instanceof Error ? reason.message : 'AI could not prepare this workflow draft'
        setError(message)
        setTurns((prev) => [
          ...prev,
          {
            id: `${turnId}-error`,
            role: 'assistant',
            content: message,
            error: message,
            timestamp: Date.now(),
          },
        ])
      } finally {
        setLoading(false)
        window.setTimeout(() => promptRef.current?.focus(), 50)
      }
    },
    [doc.graph, doc.workflowId, scopeMode, selectedNode, selectedNodeLabel]
  )

  // Initial prompt handed over from the "New with AI" dialog on the list page
  useEffect(() => {
    if (!open || !doc.workflowId) return
    const initPromptKey = `finbyz:ai_initial_prompt:${doc.workflowId}`
    const initialPrompt = window.sessionStorage.getItem(initPromptKey)
    if (initialPrompt && initialPrompt.trim().length >= 12) {
      window.sessionStorage.removeItem(initPromptKey)
      setPrompt(initialPrompt)
      window.setTimeout(() => void sendMessage(initialPrompt), 100)
    }
  }, [open, doc.workflowId, sendMessage])

  const applyProposal = (proposal: DraftProposal, turnId: string) => {
    actions.replaceGraph(proposal.graph, 'ai-generated-draft')
    setTurns((prev) =>
      prev.map((turn) => (turn.id === turnId ? { ...turn, applied: true } : turn))
    )
    void call('accept_ai_workflow_proposal', mutationEnvelope(doc.workflowId, {
      graph_hash: proposal.graph_hash,
      node_count: proposal.node_count,
    }), true).catch(() => {
      /* audit-only; a failure here must not block the user */
    })
  }

  const hasAnyAnswer = (turn: ChatMessage) =>
    (turn.questions || []).some((_, index) => (answers[`${turn.id}:${index}`] || '').trim())

  /** Send every answer typed against one question turn as a single reply, so
   *  filling in a second box never discards the first. */
  const submitAnswers = useCallback(
    async (turn: ChatMessage) => {
      const questions = turn.questions || []
      const filled = questions
        .map((question, index) => ({ question, value: (answers[`${turn.id}:${index}`] || '').trim() }))
        .filter((row) => row.value)
      if (filled.length === 0) return
      const text =
        filled.length === 1 && questions.length === 1
          ? filled[0].value
          : filled.map((row, i) => `${i + 1}. ${row.question} — ${row.value}`).join('\n')
      // Keep what was typed on the turn itself so the answered questions stay
      // readable in place instead of emptying out once the reply is sent.
      const given: Record<number, string> = {}
      questions.forEach((_, index) => {
        const value = (answers[`${turn.id}:${index}`] || '').trim()
        if (value) given[index] = value
      })
      setTurns((prev) => prev.map((t) => (t.id === turn.id ? { ...t, answered: given } : t)))
      setAnswers((prev) => {
        const next = { ...prev }
        questions.forEach((_, index) => delete next[`${turn.id}:${index}`])
        return next
      })
      await sendMessage(text)
    },
    [answers, sendMessage]
  )

  const toggleDiff = (id: string) => {
    setExpandedDiffs((prev) => ({ ...prev, [id]: !prev[id] }))
  }

  const thinkingMessages = [
    `Analyzing ${status?.primary_doctype || doc.graph?.primary_doctype || 'record'} triggers & available actions…`,
    'Designing workflow paths, decision logic, and parameters…',
    'Validating DAG integrity, placeholder constraints & safety checks…',
  ]

  return (
    <>
      <button
        className={`${magic} relative flex items-center gap-1.5 shadow-sm transition-all`}
        onClick={() => {
          setOpen(true)
          actions.toggle?.('aiAssistantOpen', true)
        }}
        title="Describe an automation and generate a reviewable draft"
      >
        <WandSparkles size={14} className="animate-pulse" />
        <span className="max-xl:hidden font-bold">Build with AI</span>
      </button>

      {open &&
        createPortal(
          <aside
            ref={dialogRef}
            tabIndex={-1}
            role="dialog"
            aria-label="AI workflow assistant"
            className={`copilot-drawer fixed top-0 right-0 bottom-0 z-[75] flex flex-col border-l border-[var(--border-color)] bg-[var(--card-bg)] shadow-2xl backdrop-blur-2xl transition-all duration-200 ease-out ${
              expanded ? 'w-[680px] max-w-full' : 'w-[450px] max-w-full'
            }`}
          >
            {/* Header */}
            <header className="relative flex flex-none items-center justify-between border-b border-[var(--border-color)] bg-[var(--card-bg)] px-5 py-4">
              <div className="flex items-center gap-3">
                <span className="grid size-8 place-items-center rounded-lg border border-[var(--border-color)] bg-[var(--subtle-fg)] text-[var(--heading-color)]">
                  <WandSparkles size={16} />
                </span>
                <div>
                  <div className="flex items-center gap-2">
                    <h2 id="ai-workflow-authoring-title" className="text-heading text-sm font-semibold tracking-tight">
                      AI Workflow Copilot
                    </h2>
                    <span className="rounded border border-[var(--border-color)] bg-[var(--subtle-fg)] px-1.5 py-0.5 text-[8.5px] font-medium tracking-wide text-[var(--text-muted)]">
                      Beta
                    </span>
                  </div>
                  <p className="text-muted mt-0.5 text-[10px]">
                    {status?.primary_doctype || doc.graph?.primary_doctype || 'Automated'} workflow assistant
                  </p>
                </div>
              </div>

              <div className="flex items-center gap-1">
                <button
                  type="button"
                  className="icon-button !size-8 hidden sm:inline-grid text-[var(--text-light)] hover:text-[var(--text-color)]"
                  onClick={() => setExpanded(!expanded)}
                  title={expanded ? 'Standard width' : 'Expand panel'}
                  aria-label={expanded ? 'Collapse panel width' : 'Expand panel width'}
                >
                  {expanded ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
                </button>
                <button
                  type="button"
                  className="icon-button !size-8 hover:!text-red-500"
                  onClick={close}
                  disabled={loading}
                  aria-label="Close AI workflow assistant"
                >
                  <X size={16} />
                </button>
              </div>
            </header>

            {/* Conversation Stream */}
            <div className="hide-scrollbar flex-1 overflow-y-auto p-4 sm:p-5 space-y-4">
              {/* Setup needed warning */}
              {status && !status.available && (
                <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-500/10 dark:text-amber-200">
                  <div className="flex gap-2.5">
                    <AlertTriangle className="mt-0.5 shrink-0 text-amber-600" size={16} />
                    <div>
                      <strong className="block font-bold">AI workflow authoring needs setup</strong>
                      <p className="mt-1 leading-relaxed text-[11px]">{status.reason}</p>
                      {window.frappe?.boot?.roles?.includes('System Manager') && (
                        <a
                          className="mt-2.5 inline-flex items-center gap-1 font-bold text-amber-900 underline dark:text-amber-100"
                          href="/app/automation-settings"
                        >
                          Open Automation Settings <ExternalLink size={11} />
                        </a>
                      )}
                    </div>
                  </div>
                </div>
              )}

              {/* Initial Empty State */}
              {status?.available && turns.length === 0 && !loading && (
                <div className="space-y-4 pt-2">
                  <div className="rounded-xl border border-[var(--border-color)] bg-[var(--subtle-fg)] p-4">
                    <div className="flex items-center gap-2 text-heading font-semibold text-xs">
                      <Sparkles size={14} className="text-[var(--text-muted)]" />
                      <span>Natural Language Assistant</span>
                    </div>
                    <h3 className="text-heading mt-2 text-sm font-semibold">What do you want to automate?</h3>
                    <p className="text-muted mt-1 text-xs leading-relaxed">
                      Describe triggers, business conditions, actions, and delays. The assistant produces an editable draft DAG for review before applying.
                    </p>
                  </div>

                  {status.suggestions.length > 0 && (
                    <div>
                      <p className="text-light mb-2 text-[10px] font-bold uppercase tracking-wider">
                        Suggested ideas for {status.primary_doctype || 'this record'}
                      </p>
                      <div className="flex flex-col gap-2">
                        {status.suggestions.map((suggestion) => (
                          <button
                            key={suggestion}
                            type="button"
                            className="flex items-center justify-between gap-2 rounded-lg border border-[var(--border-color)] bg-[var(--card-bg)] p-3 text-left text-xs text-[var(--text-color)] transition-all hover:bg-[var(--subtle-fg)] hover:border-[var(--dark-border-color)]"
                            onClick={() => {
                              setPrompt(suggestion)
                              void sendMessage(suggestion)
                            }}
                          >
                            <span className="leading-snug">{suggestion}</span>
                            <Sparkles size={13} className="shrink-0 text-[var(--text-light)]" />
                          </button>
                        ))}
                      </div>
                    </div>
                  )}

                  <div className="flex items-start gap-2 rounded-xl border border-[var(--border-color)] bg-[var(--subtle-fg)]/60 p-3 text-[10px] text-muted leading-relaxed">
                    <CheckCircle2 size={13} className="mt-0.5 shrink-0 text-emerald-600" />
                    <span>
                      The assistant only accesses permitted fields and actions for this DocType. It cannot execute actions or modify live data.
                    </span>
                  </div>
                </div>
              )}

              {/* Chat Message Turns */}
              {turns.map((turn, turnIndex) => {
                const isLastTurn = turnIndex === turns.length - 1
                if (turn.role === 'user') {
                  return (
                    <div key={turn.id} className="flex flex-col items-end space-y-1">
                      {turn.scope && (
                        <span className="flex items-center gap-1 rounded border border-[var(--border-color)] bg-[var(--subtle-fg)] px-2 py-0.5 text-[9px] font-medium text-[var(--text-muted)]">
                          <GitBranch size={10} />
                          {turn.scope}
                        </span>
                      )}
                      <div className="flex max-w-[88%] items-start gap-2">
                        <div className="rounded-xl rounded-tr-sm bg-[#192733] dark:bg-[#283848] px-3.5 py-2.5 text-xs text-white shadow-xs leading-relaxed">
                          {turn.content}
                        </div>
                        <span className="grid size-6 shrink-0 place-items-center rounded-full border border-[var(--border-color)] bg-[var(--subtle-fg)] text-[var(--text-light)]">
                          <User size={13} />
                        </span>
                      </div>
                    </div>
                  )
                }

                // Assistant Proposal Turn
                const proposal = turn.proposal
                const diff = turn.diff
                const isDiffOpen = Boolean(expandedDiffs[turn.id])

                if (!proposal) {
                  if (turn.error) {
                    return (
                      <div key={turn.id} className="flex items-start gap-2.5">
                        <span className="grid size-7 shrink-0 place-items-center rounded-lg border border-[var(--border-color)] bg-[var(--subtle-fg)] text-[var(--heading-color)]">
                          <Bot size={14} />
                        </span>
                        <div className="rounded-xl border border-red-200 bg-red-50 p-3.5 text-xs text-red-700 dark:border-red-900 dark:bg-red-500/10 dark:text-red-300">
                          {turn.content}
                        </div>
                      </div>
                    )
                  }

                  // Assistant follow-up question (or plain assistant text)
                  return (
                    <div key={turn.id} className="flex items-start gap-2.5">
                      <span className="grid size-7 shrink-0 place-items-center rounded-lg border border-[var(--border-color)] bg-[var(--subtle-fg)] text-[var(--heading-color)] mt-1">
                        <Sparkles size={14} />
                      </span>
                      <div className="flex-1 space-y-2.5 rounded-2xl border border-[var(--border-color)] bg-[var(--card-bg)] p-4 shadow-sm">
                        <p className="text-body text-xs leading-relaxed"><RichText text={turn.content} /></p>
                        {turn.questions && turn.questions.length > 0 && (
                          isChoiceSet(turn.questions) ? (
                            // Short options ("Yes, build it") - one click sends it.
                            <div className="flex flex-wrap gap-1.5 pt-1">
                              {turn.questions.map((choice, index) => (
                                <button
                                  key={`${turn.id}-c-${index}`}
                                  type="button"
                                  disabled={loading || !isLastTurn}
                                  onClick={() => void sendMessage(choice)}
                                  className={`rounded-lg border px-3 py-1.5 text-[11px] font-medium transition-all disabled:opacity-40 ${
                                    index === 0
                                      ? 'border-[var(--dark-border-color)] bg-[#192733] text-white dark:bg-[#283848]'
                                      : 'border-[var(--border-color)] bg-[var(--subtle-fg)] text-[var(--text-color)] hover:bg-[var(--control-hover-bg)]'
                                  }`}
                                >
                                  {choice}
                                </button>
                              ))}
                            </div>
                          ) : (
                            // Real questions - answer each one in place, send together.
                            <div className="space-y-2 pt-1">
                              {turn.questions.map((question, index) => {
                                const key = `${turn.id}:${index}`
                                const given = turn.answered?.[index]
                                return (
                                  <label key={key} className="block">
                                    <span className="text-muted block text-[10.5px] leading-snug">
                                      {turn.questions!.length > 1 && (
                                        <b className="text-heading mr-1">{index + 1}.</b>
                                      )}
                                      {question}
                                    </span>
                                    {given ? (
                                      <span className="mt-1 flex items-start gap-1.5 rounded-lg border border-emerald-200 bg-emerald-50 px-2.5 py-1.5 text-[11px] text-emerald-800 dark:border-emerald-900 dark:bg-emerald-500/10 dark:text-emerald-200">
                                        <Check size={12} className="mt-0.5 shrink-0" />
                                        <span className="leading-snug">{given}</span>
                                      </span>
                                    ) : (
                                      <input
                                        className="mt-1 w-full rounded-lg border border-[var(--border-color)] bg-[var(--control-bg)] px-2.5 py-1.5 text-[11px] text-heading outline-none transition-all focus:border-[var(--dark-border-color)] focus:bg-[var(--card-bg)] disabled:opacity-50"
                                        placeholder="Your answer…"
                                        value={answers[key] || ''}
                                        disabled={loading || !isLastTurn}
                                        onChange={(e) =>
                                          setAnswers((prev) => ({ ...prev, [key]: e.target.value }))
                                        }
                                        onKeyDown={(e) => {
                                          if (e.key === 'Enter') {
                                            e.preventDefault()
                                            void submitAnswers(turn)
                                          }
                                        }}
                                      />
                                    )}
                                  </label>
                                )
                              })}
                              {isLastTurn && !turn.answered && (
                                <div className="flex items-center justify-between pt-0.5">
                                  <span className="text-light text-[9.5px]">
                                    Answer here, or type below to say something else.
                                  </span>
                                  <button
                                    type="button"
                                    className={`${magic} !text-[11px] !py-1 !px-2.5`}
                                    disabled={loading || !hasAnyAnswer(turn)}
                                    onClick={() => void submitAnswers(turn)}
                                  >
                                    <ArrowUp size={12} />
                                    Send {turn.questions.length > 1 ? 'answers' : 'answer'}
                                  </button>
                                </div>
                              )}
                            </div>
                          )
                        )}
                      </div>
                    </div>
                  )
                }

                return (
                  <div key={turn.id} className="flex items-start gap-2.5">
                    <span className="grid size-7 shrink-0 place-items-center rounded-lg border border-[var(--border-color)] bg-[var(--subtle-fg)] text-[var(--heading-color)] mt-1">
                      <Sparkles size={14} />
                    </span>

                    <div className="flex-1 space-y-3 rounded-2xl border border-[var(--border-color)] bg-[var(--card-bg)] p-4 shadow-sm">
                      {/* Proposal Header */}
                      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--border-color)] pb-3">
                        <div>
                          <span className="text-[9px] font-bold uppercase tracking-wider text-[var(--text-light)]">
                            Draft Proposal
                          </span>
                          <h4 className="text-heading text-xs font-bold mt-0.5">
                            {proposal.node_count} steps prepared
                          </h4>
                        </div>

                        <span
                          className={`status-pill ${
                            proposal.issues.length
                              ? 'border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-900 dark:bg-amber-500/10 dark:text-amber-300'
                              : 'border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-500/10 dark:text-emerald-300'
                          }`}
                        >
                          {proposal.issues.length
                            ? `${proposal.issues.length} setup item${proposal.issues.length === 1 ? '' : 's'}`
                            : 'Ready to review'}
                        </span>
                      </div>

                      {/* Summary */}
                      <p className="text-body text-xs leading-relaxed font-normal"><RichText text={proposal.summary} /></p>

                      {/* Graph Diff Preview Badges */}
                      {diff && (diff.added.length > 0 || diff.modified.length > 0 || diff.removed.length > 0) && (
                        <div className="rounded-xl border border-[var(--border-color)] bg-[var(--subtle-fg)]/80 p-3">
                          <div className="flex items-center justify-between">
                            <div className="flex flex-wrap items-center gap-1.5 text-[10px]">
                              <span className="font-bold text-heading">Changes:</span>
                              {diff.added.length > 0 && (
                                <span className="diff-badge-added rounded-md px-2 py-0.5 font-bold">
                                  +{diff.added.length} added
                                </span>
                              )}
                              {diff.modified.length > 0 && (
                                <span className="diff-badge-modified rounded-md px-2 py-0.5 font-bold">
                                  ~{diff.modified.length} modified
                                </span>
                              )}
                              {diff.removed.length > 0 && (
                                <span className="diff-badge-removed rounded-md px-2 py-0.5 font-bold">
                                  -{diff.removed.length} removed
                                </span>
                              )}
                            </div>

                            <button
                              type="button"
                              onClick={() => toggleDiff(turn.id)}
                              className="text-[10px] font-semibold text-brand-600 hover:text-brand-700 dark:text-brand-400 flex items-center gap-0.5"
                            >
                              {isDiffOpen ? 'Hide' : 'Details'}
                              {isDiffOpen ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
                            </button>
                          </div>

                          {isDiffOpen && (
                            <div className="mt-2.5 space-y-1.5 border-t border-[var(--border-color)] pt-2 text-[10.5px]">
                              {diff.added.map((item) => (
                                <div key={item.id} className="flex items-center gap-2 text-emerald-700 dark:text-emerald-300">
                                  <span className="font-bold">+</span>
                                  <span>{item.label}</span>
                                  <span className="text-[9px] text-muted">({item.id})</span>
                                </div>
                              ))}
                              {diff.modified.map((item) => (
                                <div key={item.id} className="flex items-center gap-2 text-amber-700 dark:text-amber-300">
                                  <span className="font-bold">~</span>
                                  <span>{item.label}</span>
                                  <span className="text-[9px] text-muted">({item.id})</span>
                                </div>
                              ))}
                              {diff.removed.map((item) => (
                                <div key={item.id} className="flex items-center gap-2 text-rose-700 dark:text-rose-300">
                                  <span className="font-bold">-</span>
                                  <span>{item.label}</span>
                                  <span className="text-[9px] text-muted">({item.id})</span>
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      )}

                      {/* Assumptions */}
                      {proposal.assumptions.length > 0 && (
                        <div className="space-y-1">
                          <span className="text-[9.5px] font-bold uppercase tracking-wider text-light">Assumptions</span>
                          <ul className="text-muted space-y-1 text-[10.5px] leading-relaxed">
                            {proposal.assumptions.map((item, index) => (
                              <li key={`${item}-${index}`} className="flex gap-1.5">
                                <span>•</span>
                                <span>{item}</span>
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}

                      {/* Warnings / Setup items */}
                      {(proposal.warnings.length > 0 || proposal.issues.length > 0) && (
                        <div className="rounded-xl border border-amber-200 bg-amber-50/70 p-3 dark:border-amber-900 dark:bg-amber-500/10">
                          <div className="flex items-center gap-1.5 text-[10px] font-bold text-amber-800 dark:text-amber-200">
                            <AlertTriangle size={13} />
                            <span>Requires setup before publishing</span>
                          </div>
                          <ul className="mt-1.5 space-y-1 text-[10.5px] text-amber-800 dark:text-amber-200 leading-relaxed">
                            {proposal.warnings.map((item, index) => (
                              <li key={`warn-${index}`}>• {item}</li>
                            ))}
                            {proposal.issues.slice(0, 5).map((issue, index) => (
                              <li key={`iss-${index}`}>• {issue.message}</li>
                            ))}
                          </ul>
                        </div>
                      )}

                      {/* Applied feedback banner */}
                      {turn.applied && (
                        <p className="flex items-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 p-2.5 text-[11px] font-semibold text-emerald-700 dark:border-emerald-900 dark:bg-emerald-500/10 dark:text-emerald-300">
                          <CheckCircle2 size={14} className="shrink-0" />
                          Draft applied to the canvas. Review each highlighted placeholder, then save and check. Undo restores the previous graph.
                        </p>
                      )}

                      {/* Action buttons */}
                      <div className="flex flex-wrap items-center justify-between gap-2 pt-1 border-t border-[var(--border-color)]">
                        <span className="text-light text-[9px] flex items-center gap-1">
                          <Clock size={11} />
                          {(proposal.latency_ms / 1000).toFixed(1)}s · {proposal.model}
                        </span>

                        <div className="flex gap-2">
                          <button
                            type="button"
                            className={`${magic} !text-xs !py-1.5 !px-3`}
                            onClick={() => applyProposal(proposal, turn.id)}
                            disabled={turn.applied}
                          >
                            {turn.applied ? (
                              <>
                                <Check size={13} /> Applied to canvas
                              </>
                            ) : (
                              <>
                                <WandSparkles size={13} /> Apply draft to canvas
                              </>
                            )}
                          </button>
                        </div>
                      </div>
                    </div>
                  </div>
                )
              })}

              {/* Live Thinking State */}
              {loading && (
                <div className="flex items-start gap-2.5 animate-enter">
                  <span className="grid size-7 shrink-0 place-items-center rounded-lg border border-[var(--border-color)] bg-[var(--subtle-fg)] text-[var(--heading-color)]">
                    <LoaderCircle size={14} className="animate-spin text-[var(--text-muted)]" />
                  </span>
                  <div className="rounded-xl border border-[var(--border-color)] bg-[var(--subtle-fg)] p-4 w-full">
                    <div className="flex items-center gap-2 text-heading font-semibold text-xs">
                      <LoaderCircle size={13} className="animate-spin text-[var(--text-muted)]" />
                      <span>Copilot is working…</span>
                    </div>
                    <p className="text-muted mt-2 text-xs leading-relaxed transition-all duration-300">
                      {thinkingMessages[thinkingStep]}
                    </p>
                    <div className="mt-3 flex gap-1.5">
                      <span className={`h-1 flex-1 rounded-full transition-all ${thinkingStep >= 0 ? 'bg-[#192733] dark:bg-[#e7edf2]' : 'bg-[var(--border-color)]'}`} />
                      <span className={`h-1 flex-1 rounded-full transition-all ${thinkingStep >= 1 ? 'bg-[#192733] dark:bg-[#e7edf2]' : 'bg-[var(--border-color)]'}`} />
                      <span className={`h-1 flex-1 rounded-full transition-all ${thinkingStep >= 2 ? 'bg-[#192733] dark:bg-[#e7edf2]' : 'bg-[var(--border-color)]'}`} />
                    </div>
                  </div>
                </div>
              )}

              {error && !turns.some((t) => t.error === error) && (
                <div className="flex gap-2 rounded-xl border border-red-200 bg-red-50 p-3 text-xs text-red-700 dark:border-red-900 dark:bg-red-500/10 dark:text-red-300">
                  <AlertTriangle className="mt-0.5 shrink-0" size={14} />
                  <span>{error}</span>
                </div>
              )}

              <div ref={messagesEndRef} />
            </div>

            {/* Input Footer Area */}
            {status?.available && (
              <footer className="flex-none border-t border-[var(--border-color)] bg-[var(--card-bg)] p-4">
                {/* Scope selector */}
                <div className="mb-2.5 flex items-center justify-between">
                  <div className="inline-flex rounded-lg border border-[var(--border-color)] bg-[var(--control-bg)] p-0.5 text-[10.5px]">
                    <button
                      type="button"
                      onClick={() => setScopeMode('whole')}
                      className={`rounded-md px-2.5 py-1 font-medium transition-all ${
                        scopeMode === 'whole'
                          ? 'bg-[var(--card-bg)] text-heading shadow-xs'
                          : 'text-muted hover:text-heading'
                      }`}
                    >
                      Whole workflow
                    </button>
                    {selectedNode && (
                      <button
                        type="button"
                        onClick={() => setScopeMode('node')}
                        className={`flex items-center gap-1 rounded-md px-2.5 py-1 font-medium transition-all ${
                          scopeMode === 'node'
                            ? 'bg-[var(--card-bg)] text-heading shadow-xs'
                            : 'text-muted hover:text-heading'
                        }`}
                        title={`Focus prompt on step: ${selectedNodeLabel}`}
                      >
                        <Layers size={10} />
                        Step: {selectedNodeLabel}
                      </button>
                    )}
                  </div>

                  <span className="text-light text-[9.5px]">
                    {prompt.length}/{status.max_prompt_characters} · ⌘/Ctrl+Enter
                  </span>
                </div>

                {/* Textarea & Send */}
                <div className="relative rounded-xl border border-[var(--border-color)] bg-[var(--control-bg)] p-2 focus-within:border-[var(--dark-border-color)] focus-within:bg-[var(--card-bg)] transition-all">
                  <textarea
                    ref={promptRef}
                    rows={turns.length > 0 ? 2 : 3}
                    className="w-full resize-none bg-transparent p-1 text-xs leading-relaxed text-heading outline-none placeholder:text-[var(--text-light)]"
                    maxLength={status.max_prompt_characters}
                    value={prompt}
                    onChange={(e) => setPrompt(e.target.value)}
                    placeholder={
                      scopeMode === 'node' && selectedNode
                        ? `Describe what to change on step "${selectedNodeLabel}"…`
                        : status.suggestions[0]
                        ? `Example: ${status.suggestions[0]}`
                        : `Describe what to automate for this ${status.primary_doctype || 'record'}…`
                    }
                    onKeyDown={(e) => {
                      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
                        e.preventDefault()
                        void sendMessage(prompt)
                      }
                    }}
                  />

                  <div className="flex items-center justify-between pt-1 border-t border-[var(--border-color)]">
                    <span className="text-[9.5px] text-muted">
                      {turns.length > 0 ? 'Ask for refinements or additions' : 'Describe your automation'}
                    </span>
                    <button
                      type="button"
                      className={`${magic} !size-7 !rounded-lg !p-0`}
                      aria-label="Generate workflow draft"
                      disabled={loading || prompt.trim().length < 12}
                      onClick={() => void sendMessage(prompt)}
                    >
                      {loading ? (
                        <LoaderCircle className="animate-spin" size={13} />
                      ) : (
                        <ArrowUp size={14} />
                      )}
                    </button>
                  </div>
                </div>
              </footer>
            )}
          </aside>,
          document.body
        )}
    </>
  )
}
