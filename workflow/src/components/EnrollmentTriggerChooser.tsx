import { AlertCircle, FilePlus2, RefreshCw, Search, X } from 'lucide-react'
import { useMemo, useState } from 'react'
import type { BusinessEventType } from '../types'

export interface EnrollmentTriggerChoice {
	type: 'trigger.document_insert' | 'trigger.document_change' | 'trigger.event'
	topic?: string
}

interface EnrollmentTriggerChooserProps {
	primaryDoctype: string
	events: BusinessEventType[]
	loading: boolean
	error?: string
	allowRecordEvents?: boolean
	onChoose(choice: EnrollmentTriggerChoice): void
	onClose(): void
	onRetry(): void
}

function searchableEvent(event: BusinessEventType) {
	return `${event.label} ${event.description} ${event.category} ${event.source_app || ''}`.toLocaleLowerCase()
}

export function EnrollmentTriggerChooser({ primaryDoctype, events, loading, error, allowRecordEvents = true, onChoose, onClose, onRetry }: EnrollmentTriggerChooserProps) {
	const [search, setSearch] = useState('')
	const normalizedSearch = search.trim().toLocaleLowerCase()
	const filteredEvents = useMemo(() => events.filter((event) => !normalizedSearch || searchableEvent(event).includes(normalizedSearch)), [events, normalizedSearch])
	const categories = useMemo(() => Array.from(new Set(filteredEvents.map((event) => event.category))), [filteredEvents])
	const recordChoicesVisible = allowRecordEvents && (!normalizedSearch || `${primaryDoctype} created changed record`.toLocaleLowerCase().includes(normalizedSearch))

	return <section className="enrollment-trigger-chooser nodrag nopan nowheel" role="dialog" aria-label="Choose an enrollment trigger" onClick={(event) => event.stopPropagation()} onMouseDown={(event) => event.stopPropagation()}>
		<header className="enrollment-trigger-chooser__header">
			<div><strong>Choose an enrollment trigger</strong><span>Any one trigger can enroll this {primaryDoctype}.</span></div>
			<button type="button" aria-label="Close trigger chooser" onClick={onClose}><X size={15} /></button>
		</header>
		<label className="enrollment-trigger-chooser__search">
			<Search size={14} aria-hidden />
			<input autoFocus value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search record, email, Aircall, portal…" aria-label="Search enrollment triggers" />
		</label>
		<div className="enrollment-trigger-chooser__list">
			{recordChoicesVisible && <div className="enrollment-trigger-chooser__group">
				<span>Record</span>
				<button type="button" onClick={() => onChoose({ type: 'trigger.document_insert' })}><FilePlus2 size={15} /><span><strong>{primaryDoctype} created</strong><small>Enroll only when a new record is created after publication.</small></span></button>
				<button type="button" onClick={() => onChoose({ type: 'trigger.document_change' })}><RefreshCw size={15} /><span><strong>{primaryDoctype} changed</strong><small>Enroll when the record changes; watched fields and filters are optional.</small></span></button>
			</div>}
			{loading && <div className="enrollment-trigger-chooser__state" role="status"><span className="spinner" />Loading installed events…</div>}
			{!loading && error && <div className="enrollment-trigger-chooser__state" role="alert"><AlertCircle size={14} /><span>{error}</span><button type="button" onClick={onRetry}>Retry</button></div>}
			{!loading && !error && categories.map((category) => <div className="enrollment-trigger-chooser__group" key={category}>
				<span>{category}</span>
				{filteredEvents.filter((event) => event.category === category).map((event) => <button type="button" onClick={() => onChoose({ type: 'trigger.event', topic: event.topic })} key={event.topic}>
					<span className="enrollment-trigger-chooser__event-icon">{event.label.slice(0, 1).toLocaleUpperCase()}</span>
					<span><strong>{event.label}</strong><small>{event.description}</small>{event.source_app && <em>{event.producer_status === 'native' ? 'Connected' : 'Setup required'} · {event.source_app}</em>}</span>
				</button>)}
			</div>)}
			{!loading && !error && !recordChoicesVisible && filteredEvents.length === 0 && <div className="enrollment-trigger-chooser__state">No matching triggers are available for {primaryDoctype}.</div>}
		</div>
	</section>
}
