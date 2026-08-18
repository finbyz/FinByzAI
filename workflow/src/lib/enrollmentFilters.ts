import type { FieldCatalogItem } from '../types'

export type FilterRule = { id: string; field: string; operator: string; value: string }
export type EnrollmentFilter = [field: string, operator: string, value: unknown]

export interface BackfillSettings {
  filters: EnrollmentFilter[]
  batchSize: number
  recordsPerMinute: number
  maxRecords: number
}

export interface ScheduleSettings extends BackfillSettings {
  frequency: 'HOURLY' | 'DAILY' | 'WEEKLY'
  nextRunAt: string
  timezone: string
  versionPolicy: 'ACTIVE_AT_RUN' | 'PINNED'
  workflowVersion?: string
  catchUpPolicy: 'RUN_ONCE' | 'SKIP'
  overlapPolicy: 'SKIP' | 'QUEUE'
}

export function operatorsFor(field?: FieldCatalogItem) {
  if (!field) return ['=']
  if (['Int', 'Float', 'Currency', 'Percent', 'Date', 'Datetime', 'Time'].includes(field.fieldtype)) return ['=', '!=', '>', '>=', '<', '<=', 'is']
  if (field.fieldtype === 'Check') return ['=', '!=']
  return ['=', '!=', 'like', 'not like', 'in', 'not in', 'is']
}

export function filterPayload(rules: FilterRule[], fields: FieldCatalogItem[]): EnrollmentFilter[] {
  const metadata = new Map(fields.map((item) => [item.fieldname, item]))
  return rules.filter((rule) => rule.field).map((rule) => {
    const selected = metadata.get(rule.field)
    let value: unknown = rule.value
    if (['in', 'not in'].includes(rule.operator)) value = rule.value.split(',').map((item) => item.trim()).filter(Boolean)
    else if (['Int', 'Float', 'Currency', 'Percent'].includes(selected?.fieldtype || '')) value = Number(rule.value)
    else if (selected?.fieldtype === 'Check') value = rule.value === '1' ? 1 : 0
    return [rule.field, rule.operator, value] as EnrollmentFilter
  })
}

export function backfillPayload(settings: BackfillSettings, dryRun: boolean) {
  return {
    filters: settings.filters,
    batch_size: settings.batchSize,
    records_per_minute: settings.recordsPerMinute,
    max_records: settings.maxRecords,
    dry_run: dryRun ? 1 : 0,
  }
}

export function schedulePayload(settings: ScheduleSettings) {
  return {
    filters: settings.filters,
    batch_size: settings.batchSize,
    records_per_minute: settings.recordsPerMinute,
    max_records: settings.maxRecords,
    frequency: settings.frequency,
    next_run_at: settings.nextRunAt,
    timezone: settings.timezone,
    version_policy: settings.versionPolicy,
    workflow_version: settings.versionPolicy === 'PINNED' ? settings.workflowVersion : undefined,
    catch_up_policy: settings.catchUpPolicy,
    overlap_policy: settings.overlapPolicy,
  }
}
