import type { DocTypeCatalogItem, FieldCatalogResponse, LinkSearchResult, MetadataPermissionType } from '../types'

const API_ROOT = '/api/method/finbyzai.workflow_builder.api'

export class WorkflowApiError extends Error {
  status: number
  code: string
  retryable: boolean
  traceId?: string

  constructor(message: string, status: number, code = 'WF_REQUEST_FAILED', retryable = false, traceId?: string) {
    super(message)
    this.name = 'WorkflowApiError'
    this.status = status
    this.code = code
    this.retryable = retryable
    this.traceId = traceId
  }
}

function csrfToken() {
  return window.frappe?.boot?.csrf_token || window.csrf_token || ''
}

function safeServerMessage(body: Record<string, unknown>): string | undefined {
  const raw = body._server_messages
  if (typeof raw !== 'string') return undefined
  try {
    const messages = JSON.parse(raw) as string[]
    const last = JSON.parse(messages.at(-1) || '{}') as { message?: string }
    return last.message
  } catch {
    return undefined
  }
}

async function request<T>(url: URL, args: Record<string, unknown>, mutation: boolean, signal?: AbortSignal): Promise<T> {
  const init: RequestInit = { credentials: 'same-origin', headers: { Accept: 'application/json' }, signal }
  if (mutation) {
    init.method = 'POST'
    init.headers = { ...init.headers, 'Content-Type': 'application/json', 'X-Frappe-CSRF-Token': csrfToken() }
    init.body = JSON.stringify(args)
  } else {
    Object.entries(args).forEach(([key, value]) => {
      if (value !== undefined && value !== null) url.searchParams.set(key, String(value))
    })
  }
  const response = await fetch(url, init)
  const body = (await response.json().catch(() => ({}))) as Record<string, unknown>
  if (!response.ok || body.exc) {
    const exception = (body.workflow_error as { code?: string; retryable?: boolean; trace_id?: string; explanation?: string } | undefined) || {}
    throw new WorkflowApiError(
      exception.explanation || safeServerMessage(body) || String(body.message || 'Workflow request failed'),
      response.status,
      exception.code || String(body.exc_type || 'WF_REQUEST_FAILED'),
      Boolean(exception.retryable),
      exception.trace_id,
    )
  }
  return body.message as T
}

export async function call<T>(method: string, args: Record<string, unknown> = {}, mutation = false, signal?: AbortSignal): Promise<T> {
  return request<T>(new URL(`${API_ROOT}.${method}`, window.location.origin), args, mutation, signal)
}

export function mutationEnvelope(
  workflowId: string,
  payload: Record<string, unknown>,
  draftRevision?: number,
  idempotencyKey: string = crypto.randomUUID(),
) {
  return {
    envelope: {
      workflow_id: workflowId,
      draft_revision: draftRevision,
      idempotency_key: idempotencyKey,
      payload,
    },
  }
}

type MetadataCacheEntry<T> = { pending: Promise<T>; expiresAt: number }

const METADATA_CACHE_TTL_MS = 60_000
const doctypeSearchCache = new Map<string, MetadataCacheEntry<DocTypeCatalogItem[]>>()
const fieldCatalogCache = new Map<string, MetadataCacheEntry<FieldCatalogResponse>>()
const linkSearchCache = new Map<string, MetadataCacheEntry<LinkSearchResult[]>>()

function rememberMetadata<T>(cache: Map<string, MetadataCacheEntry<T>>, key: string, pending: Promise<T>, limit: number) {
  if (cache.size >= limit) cache.delete(cache.keys().next().value as string)
  cache.set(key, { pending, expiresAt: Date.now() + METADATA_CACHE_TTL_MS })
}

export function invalidateMetadataCaches() {
  doctypeSearchCache.clear()
  fieldCatalogCache.clear()
  linkSearchCache.clear()
}

export function searchDoctypes(permissionType: MetadataPermissionType, search = '', workflowId?: string): Promise<DocTypeCatalogItem[]> {
  const normalized = search.trim()
  const key = `${workflowId || 'session'}:${permissionType}:${normalized.toLocaleLowerCase()}`
  const cached = doctypeSearchCache.get(key)
  if (cached && cached.expiresAt > Date.now()) return cached.pending
  doctypeSearchCache.delete(key)
  if (!doctypeSearchCache.has(key)) {
    const pending = call<{ rows: DocTypeCatalogItem[] }>('get_doctypes', {
      permission_type: permissionType,
      search: normalized,
      start: 0,
      page_length: 20,
      workflow_id: workflowId,
    }).then((result) => result.rows).catch((error) => {
      doctypeSearchCache.delete(key)
      throw error
    })
    rememberMetadata(doctypeSearchCache, key, pending, 150)
  }
  return doctypeSearchCache.get(key)!.pending
}

export function fetchFieldCatalog(doctype: string, permissionType: MetadataPermissionType, workflowId?: string): Promise<FieldCatalogResponse> {
  const key = `${workflowId || 'session'}:${permissionType}:${doctype}`
  const cached = fieldCatalogCache.get(key)
  if (cached && cached.expiresAt > Date.now()) return cached.pending
  fieldCatalogCache.delete(key)
  if (!fieldCatalogCache.has(key)) {
    const pending = call<FieldCatalogResponse>('get_fields', { doctype, permission_type: permissionType, workflow_id: workflowId }).catch((error) => {
      fieldCatalogCache.delete(key)
      throw error
    })
    rememberMetadata(fieldCatalogCache, key, pending, 50)
  }
  return fieldCatalogCache.get(key)!.pending
}

export function searchLink(
  doctype: string,
  search = '',
  options: { filters?: Record<string, unknown>; referenceDoctype?: string; linkFieldname?: string; pageLength?: number } = {},
): Promise<LinkSearchResult[]> {
  const normalized = search.trim()
  const filters = options.filters || {}
  const pageLength = options.pageLength || 10
  const key = JSON.stringify([doctype, normalized.toLocaleLowerCase(), filters, options.referenceDoctype, options.linkFieldname, pageLength])
  const cached = linkSearchCache.get(key)
  if (cached && cached.expiresAt > Date.now()) return cached.pending
  linkSearchCache.delete(key)
  if (!linkSearchCache.has(key)) {
    const args: Record<string, unknown> = {
      doctype,
      txt: normalized,
      page_length: pageLength,
      filters: JSON.stringify(filters),
      reference_doctype: options.referenceDoctype,
      link_fieldname: options.linkFieldname,
    }
    const pending = request<LinkSearchResult[]>(
      new URL('/api/method/frappe.desk.search.search_link', window.location.origin),
      args,
      true, // Always POST — ensures CSRF token is sent regardless of query length
    ).catch((error) => {
      linkSearchCache.delete(key)
      throw error
    })
    rememberMetadata(linkSearchCache, key, pending, 150)
  }
  return linkSearchCache.get(key)!.pending
}
