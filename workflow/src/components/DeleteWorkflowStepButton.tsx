import { type ButtonHTMLAttributes, type MouseEvent, type ReactNode } from 'react'
import { workflowNodeSourceHandles } from '../lib/workflowGraphCommands'
import { useWorkflowActions } from '../state/WorkflowContext'
import type { WorkflowNode } from '../types'
import { useConfirmDialog } from './useConfirmDialog'

interface DeleteWorkflowStepButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children' | 'onClick'> {
	node: WorkflowNode
	children: ReactNode
}

/** Guided deletion for the workflow canvas.
 * Linear steps are removed and their surrounding path is healed by the graph
 * command. A multi-output step owns several paths, so deleting it requires an
 * explicit destructive choice and removes only its exclusive downstream
 * section; a shared continuation after a convergence is preserved.
 */
export function DeleteWorkflowStepButton({ node, children, ...buttonProps }: DeleteWorkflowStepButtonProps) {
	const actions = useWorkflowActions()
	const confirmation = useConfirmDialog()
	const branching = workflowNodeSourceHandles(node).length > 1

	const remove = async (event: MouseEvent<HTMLButtonElement>) => {
		event.stopPropagation()
		if (branching) {
			const confirmed = await confirmation.ask({
				title: 'Delete this branch section?',
				description: 'This step has multiple paths. The step and actions used only by those paths will be deleted. A continuation shared with another path is kept.',
				confirmLabel: 'Delete branch section',
				tone: 'danger',
			})
			if (!confirmed) return
			actions.removeSection(node.id)
			return
		}
		actions.removeNode(node.id)
	}

	return <>
		<button {...buttonProps} type="button" onClick={(event) => { void remove(event) }}>{children}</button>
		{confirmation.dialog}
	</>
}
