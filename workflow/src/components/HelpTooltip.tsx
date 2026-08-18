import { CircleHelp } from 'lucide-react'

export function HelpTooltip({ content, label = 'Help' }: { content: string; label?: string }) {
  return (
    <span
      className="group/help relative inline-flex shrink-0 cursor-help items-center align-middle text-[var(--text-light)] outline-none focus-visible:text-brand-600"
      tabIndex={0}
      aria-label={`${label}: ${content}`}
    >
      <CircleHelp size={13} aria-hidden="true" />
      <span
        role="tooltip"
        className="pointer-events-none absolute bottom-full left-1/2 z-[80] mb-2 hidden w-64 -translate-x-1/2 rounded-lg border border-[var(--border-color)] bg-[var(--card-bg)] px-3 py-2 text-left text-[10px] font-normal leading-4 text-[var(--text-body)] shadow-xl group-hover/help:block group-focus/help:block"
      >
        {content}
      </span>
    </span>
  )
}
