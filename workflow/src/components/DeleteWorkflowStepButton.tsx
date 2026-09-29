import { ShieldAlert, Trash2, Unlink } from 'lucide-react'
import { type ButtonHTMLAttributes, type MouseEvent, type ReactNode, useId, useState } from 'react'
import { createPortal } from 'react-dom'
import { workflowNodeSourceHandles } from '../lib/workflowGraphCommands'
import { useWorkflowActions } from '../state/WorkflowContext'
import type { WorkflowNode } from '../types'
import { useDialogA11y } from './useDialogA11y'

interface DeleteWorkflowStepButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children' | 'onClick'> {
	node: WorkflowNode
	children: ReactNode
}

/** Guided deletion for the workflow canvas.
 * Every destructive action now asks the author how much of the path should be
 * removed. Deleting only one linear action heals the surrounding path. Deleting
 * following actions removes the exclusive downstream section while preserving a
 * shared continuation after a later convergence.
 */
export function DeleteWorkflowStepButton({ node, children, ...buttonProps }: DeleteWorkflowStepButtonProps) {
	const actions = useWorkflowActions()
	const [open, setOpen] = useState(false)
	const branching = workflowNodeSourceHandles(node).length > 1
	const titleId = useId()
	const descriptionId = useId()
	const dialogRef = useDialogA11y(open, () => setOpen(false), 'Delete action')

	const requestDelete = (event: MouseEvent<HTMLButtonElement>) => {
		event.stopPropagation()
		setOpen(true)
	}

	const deleteOnly = () => {
		setOpen(false)
		actions.removeNode(node.id)
	}

	const deleteFollowing = () => {
		setOpen(false)
		actions.removeSection(node.id)
	}

	return <>
		<button {...buttonProps} type="button" onClick={requestDelete}>{children}</button>
		{open && createPortal(
			<div
				className="dialog-backdrop fixed inset-0 z-[110] grid place-items-center p-4"
				role="dialog"
				aria-modal="true"
				aria-labelledby={titleId}
				aria-describedby={descriptionId}
				onMouseDown={(event) => { if (event.target === event.currentTarget) setOpen(false) }}
			>
				<div ref={dialogRef} tabIndex={-1} className="dialog-card w-full max-w-lg overflow-hidden rounded-2xl">
					<div className="p-5 sm:p-6">
						<div className="flex items-start gap-3.5">
							<span className="grid size-10 shrink-0 place-items-center rounded-xl bg-red-50 text-red-600 dark:bg-red-500/10 dark:text-red-300"><ShieldAlert size={19} /></span>
							<div className="min-w-0 pt-0.5">
								<h2 id={titleId} className="text-heading text-base font-bold tracking-tight">Delete this action?</h2>
								<div id={descriptionId} className="text-muted mt-1.5 text-xs leading-5">
									Choose whether to remove only this step or the exclusive actions that follow it on this path.
								</div>
							</div>
						</div>
						<div className="mt-5 grid gap-2">
							<button
								type="button"
								disabled={branching}
								className="flex w-full items-start gap-3 rounded-xl border border-[var(--border-color)] bg-white/70 p-3 text-left text-xs transition hover:border-brand-400 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-white/5"
								onClick={deleteOnly}
							>
								<Unlink size={16} className="mt-0.5 text-brand-500" />
								<span><strong className="text-heading block">Delete only this action</strong><span className="text-muted">Connect the previous step directly to the next step.</span>{branching && <span className="text-muted mt-1 block">Not available for multi-path actions because the editor cannot safely guess which branch should continue.</span>}</span>
							</button>
							<button
								type="button"
								className="flex w-full items-start gap-3 rounded-xl border border-red-200 bg-red-50/70 p-3 text-left text-xs transition hover:border-red-400 dark:border-red-500/30 dark:bg-red-500/10"
								onClick={deleteFollowing}
							>
								<Trash2 size={16} className="mt-0.5 text-red-600" />
								<span><strong className="block text-red-700 dark:text-red-200">Delete this action and following actions</strong><span className="text-muted">Remove the exclusive downstream section. Shared actions after a branch join are preserved.</span></span>
							</button>
						</div>
					</div>
					<div className="flex justify-end gap-2 border-t border-[var(--border-color)] bg-[var(--subtle-fg)] px-5 py-3.5 sm:px-6">
						<button type="button" className="btn-core btn-secondary" onClick={() => setOpen(false)} autoFocus>Cancel</button>
					</div>
				</div>
			</div>,
			document.body,
		)}
	</>
}
