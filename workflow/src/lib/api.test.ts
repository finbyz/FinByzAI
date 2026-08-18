import { afterEach, describe, expect, it, vi } from 'vitest'
import { call, fetchFieldCatalog, invalidateMetadataCaches, mutationEnvelope, searchDoctypes, searchLink, WorkflowApiError } from './api'

afterEach(() => {
  invalidateMetadataCaches()
  vi.restoreAllMocks()
})

describe('workflow API client', () => {
  it('uses the mutation envelope and CSRF token', () => {
    const envelope = mutationEnvelope('AWF-00001', { graph: { nodes: [] } }, 12, 'request-1')
    expect(envelope.envelope).toMatchObject({ workflow_id: 'AWF-00001', draft_revision: 12, idempotency_key: 'request-1' })
  })

  it('surfaces HTTP 409 as a typed conflict', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ message: 'Stale revision', exc_type: 'WF_CONFLICT' }), { status: 409, headers: { 'Content-Type': 'application/json' } }))
    await expect(call('save_draft', {}, true)).rejects.toMatchObject({ status: 409, code: 'WF_CONFLICT' } satisfies Partial<WorkflowApiError>)
  })

  it('searches DocTypes on demand instead of loading a static catalog', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ message: { rows: [{ name: 'Lead', label: 'Lead', module: 'CRM', is_submittable: false, permission_type: 'read' }] } }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    await expect(searchDoctypes('read', 'lead-api-test')).resolves.toMatchObject([{ name: 'Lead' }])
    expect(String(fetchMock.mock.calls[0][0])).toContain('search=lead-api-test')
    expect(String(fetchMock.mock.calls[0][0])).toContain('page_length=20')
  })

  it('uses Frappe native Link search for records and users', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ message: [{ value: 'Administrator', label: 'Administrator', description: 'System User' }] }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    await expect(searchLink('User', 'admin-api-test', { filters: { enabled: 1 } })).resolves.toMatchObject([{ value: 'Administrator' }])
    expect(String(fetchMock.mock.calls[0][0])).toContain('frappe.desk.search.search_link')
    expect(fetchMock.mock.calls[0][1]?.method).toBe('POST')
    expect(String(fetchMock.mock.calls[0][1]?.body)).toContain('enabled')
  })

  it('loads field metadata under the workflow execution-user contract', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ message: { doctype: 'Sales Order API Test', available: true, fields: [] } }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    await fetchFieldCatalog('Sales Order API Test', 'create', 'AWF-API-TEST')
    const url = String(fetchMock.mock.calls[0][0])
    expect(url).toContain('workflow_id=AWF-API-TEST')
    expect(url).toContain('permission_type=create')
  })

  it('can invalidate metadata cached before a Frappe permission or schema change', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify({ message: { rows: [{ name: 'Lead' }] } }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ message: { rows: [{ name: 'Customer' }] } }), { status: 200, headers: { 'Content-Type': 'application/json' } }))

    await expect(searchDoctypes('read', 'metadata-refresh-test')).resolves.toEqual([{ name: 'Lead' }])
    await expect(searchDoctypes('read', 'metadata-refresh-test')).resolves.toEqual([{ name: 'Lead' }])
    expect(fetchMock).toHaveBeenCalledTimes(1)

    invalidateMetadataCaches()

    await expect(searchDoctypes('read', 'metadata-refresh-test')).resolves.toEqual([{ name: 'Customer' }])
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })
})
