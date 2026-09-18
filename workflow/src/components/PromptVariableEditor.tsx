import { Bot, ChevronDown, Copy, Sparkles, Tag, Variable } from 'lucide-react'
import { useRef, useState } from 'react'
import type { FieldCatalogItem } from '../types'

interface PromptVariableEditorProps {
	systemPrompt: string
	userPrompt: string
	onSystemPromptChange: (value: string) => void
	onUserPromptChange: (value: string) => void
	readFields: FieldCatalogItem[]
	outputNodes?: Array<{ id: string; label?: string; type?: string }>
	primaryDoctype: string
}

const TEMPLATES = [
	{
		label: 'Draft Follow-up Email',
		system: 'You are an executive assistant for Megasol. Write a concise, professional follow-up email without fluff.',
		user: 'Draft a polite follow-up email to {{ doc.customer_name || doc.name }}.\nContext: {{ doc.subject || doc.description }}\nKey points: Thank them for their time, provide clear next steps, and ask for their availability this week.',
	},
	{
		label: 'Summarize & Triage Record',
		system: 'You are an ERP data triage specialist. Summarize key issues, intent, and recommended next actions.',
		user: 'Analyze this record for {{ doc.customer_name || doc.name }}:\nSubject: {{ doc.subject || doc.title }}\nDetails: {{ doc.description || doc.notes }}\n\nProvide: 1. A 2-sentence summary, 2. Priority assessment, 3. Immediate recommended action.',
	},
	{
		label: 'Sentiment & Intent Classification',
		system: 'You are a customer feedback classifier. Classify sentiment as positive/neutral/negative and extract main intent.',
		user: 'Evaluate the sentiment and intent of this message:\n{{ doc.description || doc.subject }}\n\nOutput format:\nSentiment: [Positive | Neutral | Negative | Urgent]\nIntent: [Inquiry | Support Issue | Billing Dispute | Feature Request]\nSummary: Brief explanation.',
	},
	{
		label: 'Extract Structured Details',
		system: 'You extract clean structured values from unstructured business text.',
		user: 'Extract the following fields from this message:\n{{ doc.description || doc.subject }}\n\nExtract:\n- Contact Name\n- Phone / Email\n- Product of Interest\n- Budget or Urgency',
	},
]

export function PromptVariableEditor({
	systemPrompt,
	userPrompt,
	onSystemPromptChange,
	onUserPromptChange,
	readFields,
	outputNodes = [],
	primaryDoctype,
}: PromptVariableEditorProps) {
	const userPromptRef = useRef<HTMLTextAreaElement>(null)
	const systemPromptRef = useRef<HTMLTextAreaElement>(null)
	const [activeFieldCategory, setActiveFieldCategory] = useState<'record' | 'steps'>('record')
	const [fieldSearch, setFieldSearch] = useState('')
	const [showTemplates, setShowTemplates] = useState(false)

	const insertVariable = (variableTag: string, target: 'user' | 'system' = 'user') => {
		const textarea = target === 'user' ? userPromptRef.current : systemPromptRef.current
		const currentValue = target === 'user' ? userPrompt : systemPrompt
		const setter = target === 'user' ? onUserPromptChange : onSystemPromptChange

		if (!textarea) {
			setter(`${currentValue} ${variableTag}`)
			return
		}

		const start = textarea.selectionStart ?? currentValue.length
		const end = textarea.selectionEnd ?? currentValue.length
		const nextValue = currentValue.substring(0, start) + variableTag + currentValue.substring(end)
		setter(nextValue)

		// Restore cursor position after inserted variable
		window.setTimeout(() => {
			textarea.focus()
			textarea.setSelectionRange(start + variableTag.length, start + variableTag.length)
		}, 10)
	}

	const applyTemplate = (template: { system: string; user: string }) => {
		onSystemPromptChange(template.system)
		onUserPromptChange(template.user)
		setShowTemplates(false)
	}

	const filteredFields = readFields.filter(
		(f) => !fieldSearch || `${f.label} ${f.fieldname}`.toLowerCase().includes(fieldSearch.toLowerCase())
	)

	return (
		<div className="space-y-3.5">
			{/* Quick Template Picker */}
			<div className="relative">
				<button
					type="button"
					className="flex w-full items-center justify-between rounded-lg border border-[var(--border-color)] bg-[var(--subtle-fg)] px-3 py-1.5 text-left text-[11px] font-semibold text-heading transition-colors hover:border-brand-400"
					onClick={() => setShowTemplates(!showTemplates)}
				>
					<span className="flex items-center gap-1.5 text-brand-600 dark:text-brand-400">
						<Sparkles size={13} />
						Quick Prompt Templates
					</span>
					<ChevronDown size={13} className={`transition-transform ${showTemplates ? 'rotate-180' : ''}`} />
				</button>
				{showTemplates && (
					<div className="absolute left-0 right-0 top-full z-20 mt-1 max-h-56 overflow-y-auto rounded-xl border border-[var(--border-color)] bg-[var(--surface-overlay)] p-1.5 shadow-lg backdrop-blur">
						{TEMPLATES.map((tmpl) => (
							<button
								key={tmpl.label}
								type="button"
								className="flex w-full flex-col rounded-lg p-2 text-left transition-colors hover:bg-brand-50/80 dark:hover:bg-brand-500/10"
								onClick={() => applyTemplate(tmpl)}
							>
								<strong className="text-[11px] font-bold text-heading">{tmpl.label}</strong>
								<span className="line-clamp-1 text-[9.5px] text-muted">{tmpl.user}</span>
							</button>
						))}
					</div>
				)}
			</div>

			{/* System Prompt Instructions */}
			<div>
				<div className="mb-1 flex items-center justify-between">
					<label className="flex items-center gap-1.5 text-[10.5px] font-bold text-heading">
						<Bot size={13} className="text-magic-500" />
						System Instructions
					</label>
					<span className="text-[9.5px] text-light">Role & Guardrails</span>
				</div>
				<textarea
					ref={systemPromptRef}
					rows={3}
					className="frappe-control min-h-16 w-full resize-y rounded-lg p-2 font-mono text-[11px] leading-5 text-heading"
					placeholder="Example: You are a helpful ERP workflow assistant. Provide factual, concise responses."
					value={systemPrompt}
					onChange={(e) => onSystemPromptChange(e.target.value)}
				/>
			</div>

			{/* User Prompt with Live Variables */}
			<div>
				<div className="mb-1 flex items-center justify-between">
					<label className="flex items-center gap-1.5 text-[10.5px] font-bold text-heading">
						<Variable size={13} className="text-brand-500" />
						User Prompt (Prompt Template)
					</label>
					<span className="text-[9.5px] text-light">Supports Jinja &#123;&#123; doc.field &#125;&#125;</span>
				</div>
				<textarea
					ref={userPromptRef}
					rows={5}
					className="frappe-control min-h-24 w-full resize-y rounded-lg p-2.5 font-mono text-[11px] leading-5 text-heading"
					placeholder={`Example: Summarize the following ${primaryDoctype}:\nCustomer: {{ doc.customer_name }}\nSubject: {{ doc.subject }}\nDetails: {{ doc.description }}`}
					value={userPrompt}
					onChange={(e) => onUserPromptChange(e.target.value)}
				/>
			</div>

			{/* Variable Pills Palette */}
			<div className="rounded-xl border border-[var(--border-color)] bg-[var(--subtle-fg)] p-2.5">
				<div className="mb-2 flex items-center justify-between gap-2">
					<div className="flex items-center gap-1 rounded-lg bg-[var(--surface-ground)] p-0.5 text-[10px]">
						<button
							type="button"
							className={`rounded px-2 py-1 font-semibold transition-colors ${
								activeFieldCategory === 'record'
									? 'bg-brand-500 text-white shadow-xs'
									: 'text-muted hover:text-heading'
							}`}
							onClick={() => setActiveFieldCategory('record')}
						>
							{primaryDoctype} Fields ({readFields.length})
						</button>
						{outputNodes.length > 0 && (
							<button
								type="button"
								className={`rounded px-2 py-1 font-semibold transition-colors ${
									activeFieldCategory === 'steps'
										? 'bg-brand-500 text-white shadow-xs'
										: 'text-muted hover:text-heading'
								}`}
								onClick={() => setActiveFieldCategory('steps')}
							>
								Step Outputs ({outputNodes.length})
							</button>
						)}
					</div>
					<input
						type="search"
						className="frappe-control h-7 w-28 rounded-md px-2 text-[10px]"
						placeholder="Filter…"
						value={fieldSearch}
						onChange={(e) => setFieldSearch(e.target.value)}
					/>
				</div>

				<div className="flex flex-wrap gap-1 max-h-32 overflow-y-auto pr-1">
					{activeFieldCategory === 'record' && (
						<>
							{filteredFields.map((field) => {
								const pillTag = `{{ doc.${field.fieldname} }}`
								return (
									<button
										key={field.fieldname}
										type="button"
										draggable
										onDragStart={(e) => e.dataTransfer.setData('text/plain', pillTag)}
										onClick={() => insertVariable(pillTag, 'user')}
										title={`Click to insert ${pillTag} (${field.fieldtype})`}
										className="group flex items-center gap-1 rounded-md border border-brand-200/60 bg-brand-50/70 px-2 py-0.5 font-mono text-[10px] font-semibold text-brand-700 transition-colors hover:border-brand-400 hover:bg-brand-100 dark:border-brand-900/60 dark:bg-brand-500/10 dark:text-brand-300"
									>
										<Tag size={10} className="opacity-60" />
										<span>doc.{field.fieldname}</span>
									</button>
								)
							})}
							{filteredFields.length === 0 && (
								<p className="p-2 text-[10px] text-muted">No matching fields found.</p>
							)}
						</>
					)}

					{activeFieldCategory === 'steps' && (
						<>
							{outputNodes.map((step) => {
								const pillTag = `{{ steps.${step.id}.text }}`
								return (
									<button
										key={step.id}
										type="button"
										draggable
										onDragStart={(e) => e.dataTransfer.setData('text/plain', pillTag)}
										onClick={() => insertVariable(pillTag, 'user')}
										title={`Click to insert ${pillTag}`}
										className="flex items-center gap-1 rounded-md border border-purple-200/60 bg-purple-50/70 px-2 py-0.5 font-mono text-[10px] font-semibold text-purple-700 transition-colors hover:border-purple-400 hover:bg-purple-100 dark:border-purple-900/60 dark:bg-purple-500/10 dark:text-purple-300"
									>
										<Copy size={10} className="opacity-60" />
										<span>steps.{step.id}.text</span>
									</button>
								)
							})}
						</>
					)}
				</div>
				<p className="mt-2 text-[9px] text-light">
					Tip: Click any tag or drag it into the prompt box to interpolate dynamic data at runtime.
				</p>
			</div>
		</div>
	)
}
