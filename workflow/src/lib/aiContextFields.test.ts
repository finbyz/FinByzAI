import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { FieldCatalogItem } from '../types'

const fetchFieldCatalog = vi.fn()
vi.mock('./api', () => ({ fetchFieldCatalog: (...args: unknown[]) => fetchFieldCatalog(...args) }))

const { loadAiContextFieldOptions } = await import('./inspectorAuthoring')

const field = (fieldname: string, fieldtype: string, options?: string): FieldCatalogItem => ({
  fieldname,
  label: fieldname.replace('_', ' '),
  fieldtype,
  options,
  required: false,
  read_only: false,
  allow_on_submit: false,
})

const readFields = [field('customer', 'Link', 'Customer'), field('transaction_date', 'Date'), field('items', 'Table', 'Sales Order Item')]

describe('loadAiContextFieldOptions', () => {
  beforeEach(() => fetchFieldCatalog.mockReset())

  it('lists record fields and hints how to reach linked fields', async () => {
    const options = await loadAiContextFieldOptions('', readFields, 'WF-1')
    expect(options.map((option) => option.value)).toEqual(['customer', 'transaction_date'])
    expect(options[0].description).toContain('customer.')
    expect(fetchFieldCatalog).not.toHaveBeenCalled()
  })

  it('lists linked document fields as link.field paths', async () => {
    fetchFieldCatalog.mockResolvedValue({ fields: [field('customer_group', 'Link', 'Customer Group'), field('territory', 'Link', 'Territory'), field('__record_link', 'Data')] })
    const options = await loadAiContextFieldOptions('customer.grou', readFields, 'WF-1')
    expect(fetchFieldCatalog).toHaveBeenCalledWith('Customer', 'read', 'WF-1')
    expect(options.map((option) => option.value)).toEqual(['customer.customer_group'])
  })

  it('returns nothing for a dot after a field that is not a Link', async () => {
    expect(await loadAiContextFieldOptions('transaction_date.x', readFields)).toEqual([])
    expect(fetchFieldCatalog).not.toHaveBeenCalled()
  })
})
