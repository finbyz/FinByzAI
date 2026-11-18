import { AlertTriangle, FlaskConical } from 'lucide-react'
import type { SimulationResult, TestOutcome } from '../types'

export const TEST_OUTCOME_LABEL: Record<TestOutcome, string> = {
  REALLY_SENT: 'Really sent',
  SIMULATED: 'Simulated',
  SKIPPED: 'Skipped',
  FAILED: 'Failed',
}

const TEST_OUTCOME_STYLE: Record<TestOutcome, string> = {
  REALLY_SENT: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300',
  SIMULATED: 'bg-slate-100 text-slate-600 dark:bg-white/10 dark:text-slate-300',
  SKIPPED: 'bg-amber-100 text-amber-700 dark:bg-amber-500/10 dark:text-amber-300',
  FAILED: 'bg-red-100 text-red-700 dark:bg-red-500/10 dark:text-red-300',
}

interface SimulationOutcomeProps {
  result?: SimulationResult
  onSelectNode?: (nodeId: string) => void
}

export function SimulationOutcome({ result, onSelectNode }: SimulationOutcomeProps) {
  if (!result) {
    return (
      <div className="grid h-full min-h-48 place-items-center text-center">
        <div>
          <span className="magic-orb mx-auto"><FlaskConical size={19} /></span>
          <p className="text-heading mt-4 text-xs font-bold">Ready when you are</p>
          <p className="text-muted mt-1 text-[10px]">Choose a record to preview its exact path.</p>
        </div>
      </div>
    )
  }

  const issues = result.issues || []
  const path = result.path || []

  return (
    <div className="space-y-4" aria-live="polite">
      {issues.length > 0 && (
        <section aria-label="Test issues">
          <div className="mb-2 flex items-center gap-2 text-[11px] font-bold text-red-700 dark:text-red-300">
            <AlertTriangle size={14} />Configuration required before this test can run
          </div>
          <ul className="space-y-2">
            {issues.map((issue, index) => (
              <li
                className={`rounded-lg border p-3 text-[11px] leading-4 ${issue.severity === 'warning' ? 'border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-900 dark:bg-amber-500/10 dark:text-amber-300' : 'border-red-200 bg-red-50 text-red-700 dark:border-red-900 dark:bg-red-500/10 dark:text-red-300'}`}
                key={`${issue.code}:${issue.node_id || issue.path || index}`}
              >
                <strong>{issue.code}</strong><span className="mx-1">·</span>{issue.message}
                {issue.node_id && onSelectNode && (
                  <button className="ml-2 font-bold underline" onClick={() => onSelectNode(issue.node_id!)}>Open step</button>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      {result.test_mode && result.summary && (
        <p className="rounded-lg border border-[var(--border-color)] bg-[var(--subtle-fg)] p-3 text-[11px] leading-4" data-testid="test-summary">
          {(Object.keys(TEST_OUTCOME_LABEL) as TestOutcome[]).map((key) => `${result.summary?.[key] ?? 0} ${TEST_OUTCOME_LABEL[key].toLowerCase()}`).join(' · ')}
          {result.tester?.email && <span className="text-muted block">Messages went to {result.tester.email}{result.tester.phone ? ` and ${result.tester.phone}` : ''} only.</span>}
        </p>
      )}

      {path.length > 0 ? (
        <ol className="space-y-0">
          {path.map((entry, index) => (
            <li className="relative flex gap-3 pb-5 last:pb-0" key={`${entry.node_id}:${index}`}>
              {index < path.length - 1 && <span className="absolute left-[13px] top-7 h-[calc(100%-20px)] w-px bg-[var(--border-color)]" />}
              <span className="z-10 grid size-7 shrink-0 place-items-center rounded-full border border-magic-200 bg-magic-50 text-[10px] font-bold text-magic-600 dark:bg-magic-500/10">{index + 1}</span>
              <div className="min-w-0 flex-1 rounded-lg border border-[var(--border-color)] bg-white/60 p-3 backdrop-blur-md dark:bg-white/5">
                <strong className="text-heading block text-[11px]">{entry.type}</strong>
                <span className={`mt-1 inline-block rounded-full px-1.5 py-0.5 text-[8px] font-bold uppercase ${entry.status === 'FAILED' ? 'bg-red-100 text-red-700 dark:bg-red-500/10 dark:text-red-300' : 'bg-slate-100 text-slate-600 dark:bg-white/10 dark:text-slate-300'}`}>{entry.status}</span>
                {entry.test_outcome && <span className={`ml-1 mt-1 inline-block rounded-full px-1.5 py-0.5 text-[8px] font-bold uppercase ${TEST_OUTCOME_STYLE[entry.test_outcome]}`}>{TEST_OUTCOME_LABEL[entry.test_outcome]}</span>}
                {entry.test_note && <p className="text-muted mt-1 text-[9.5px] leading-4">{entry.test_note}</p>}
                {entry.note && <p className={`mt-1 text-[9.5px] leading-4 ${entry.status === 'FAILED' ? 'text-red-600 dark:text-red-300' : 'text-amber-700 dark:text-amber-300'}`}>{entry.note}</p>}
                <pre className="text-muted mt-1 max-h-32 overflow-auto whitespace-pre-wrap text-[9px] leading-4">{JSON.stringify(entry.output, null, 2)}</pre>
              </div>
            </li>
          ))}
        </ol>
      ) : issues.length === 0 ? (
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-center text-[11px] text-amber-800 dark:border-amber-900 dark:bg-amber-500/10 dark:text-amber-300">
          The test completed without an evaluated path. Check the selected record and workflow branches.
        </div>
      ) : null}
    </div>
  )
}
