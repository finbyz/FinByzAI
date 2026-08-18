import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { AssignmentEditor, ConditionExpressionEditor } from './InspectorHelpers'
import type { FieldCatalogItem } from '../types'

const fields: FieldCatalogItem[] = [
  { fieldname: 'customer', label: 'Customer', fieldtype: 'Link', options: 'Customer', required: true, read_only: false, allow_on_submit: false },
  { fieldname: 'naming_series', label: 'Series', fieldtype: 'Select', default: 'SAL-ORD-.YYYY.-', required: true, read_only: false, allow_on_submit: false },
]

describe('Create Record field mapping', () => {
  it('promotes mandatory fields without treating Frappe defaults as missing', () => {
    render(<AssignmentEditor
      config={{ assignments: [{ field: 'customer', value: { kind: 'literal', value: '' } }] }}
      fields={fields}
      sourceFields={[]}
      outputNodes={[]}
      outputPaths={{}}
      update={vi.fn()}
      referenceDoctype="Sales Order"
      createMode
    />)

    expect(screen.getByText('Mandatory field coverage')).toBeInTheDocument()
    expect(screen.getByText('0/1 mapped')).toBeInTheDocument()
    expect(screen.getByText('Customer *')).toBeInTheDocument()
    expect(screen.getByText(/Provide a value or map a source/)).toBeInTheDocument()
  })
})

describe('Condition editor unary operators', () => {
  it('does not request a comparison value and explains blank numeric semantics', () => {
    const annualRevenue: FieldCatalogItem = {
      fieldname: 'annual_revenue',
      label: 'Annual Revenue',
      fieldtype: 'Currency',
      required: false,
      read_only: false,
      allow_on_submit: false,
    }

    render(<ConditionExpressionEditor
      expression={{ kind: 'predicate', field: 'annual_revenue', operator: 'is_not_set', value: null }}
      fields={[annualRevenue]}
      primaryDoctype="Lead"
      depth={0}
      onChange={vi.fn()}
    />)

    expect(screen.getByLabelText('Annual Revenue condition operator')).toHaveValue('is_not_set')
    expect(screen.getByRole('option', { name: 'is blank or zero' })).toBeInTheDocument()
    expect(screen.queryByPlaceholderText('Enter a value')).not.toBeInTheDocument()
    expect(screen.getByText('Frappe stores a blank currency as zero, so zero follows the blank path.')).toBeInTheDocument()
  })
})
