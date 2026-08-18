import { describe, expect, it } from 'vitest'
import type { FieldCatalogItem } from '../types'
import { backfillPayload, filterPayload, operatorsFor, schedulePayload, type FilterRule } from './enrollmentFilters'

const fields: FieldCatalogItem[] = [
  { fieldname: 'status', label: 'Status', fieldtype: 'Select', required: false, read_only: false, allow_on_submit: false },
  { fieldname: 'annual_revenue', label: 'Annual Revenue', fieldtype: 'Currency', required: false, read_only: false, allow_on_submit: false },
  { fieldname: 'disabled', label: 'Disabled', fieldtype: 'Check', required: false, read_only: false, allow_on_submit: false },
]

describe('enrollment filters', () => {
  it('serializes list, numeric, and checkbox values with their metadata types', () => {
    const rules: FilterRule[] = [
      { id: '1', field: 'status', operator: 'in', value: 'Open, Qualified' },
      { id: '2', field: 'annual_revenue', operator: '>=', value: '25000' },
      { id: '3', field: 'disabled', operator: '=', value: '0' },
    ]
    expect(filterPayload(rules, fields)).toEqual([
      ['status', 'in', ['Open', 'Qualified']],
      ['annual_revenue', '>=', 25000],
      ['disabled', '=', 0],
    ])
  })

  it('only exposes ordered comparisons for typed numeric fields', () => {
    expect(operatorsFor(fields[1])).toContain('>=')
    expect(operatorsFor(fields[1])).not.toContain('like')
  })

  it('maps enrollment controls to the exact backend payload names', () => {
    const filters = filterPayload([{ id: '1', field: 'status', operator: '=', value: 'Open' }], fields)
    const settings = { filters, batchSize: 75, recordsPerMinute: 900, maxRecords: 500 }
    expect(backfillPayload(settings, true)).toEqual({ filters: [['status', '=', 'Open']], batch_size: 75, records_per_minute: 900, max_records: 500, dry_run: 1 })
    expect(schedulePayload({ ...settings, frequency: 'DAILY', nextRunAt: '2026-08-12T09:00', timezone: 'Asia/Kolkata', versionPolicy: 'PINNED', workflowVersion: 'AWV-2', catchUpPolicy: 'RUN_ONCE', overlapPolicy: 'SKIP' })).toEqual({
      filters: [['status', '=', 'Open']], batch_size: 75, records_per_minute: 900, max_records: 500,
      frequency: 'DAILY', next_run_at: '2026-08-12T09:00', timezone: 'Asia/Kolkata', version_policy: 'PINNED', workflow_version: 'AWV-2', catch_up_policy: 'RUN_ONCE', overlap_policy: 'SKIP',
    })
  })
})
