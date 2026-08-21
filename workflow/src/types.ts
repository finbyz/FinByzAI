export type NodeType =
  | 'trigger.schedule'
  | 'trigger.webhook'
  | 'condition.switch'
	| 'condition.random_split'
  | 'condition.deduplicate'
  | 'delay.until_event'
  | 'delay.business_hours'
  | 'transform.associated_record'
  | 'transform.child_records'
  | 'action.call_subflow'
  | 'action.numeric_adjust'
  | 'action.manage_association'
  | 'action.round_robin'
  | 'action.delete_record'
  | 'trigger.manual'
  | 'trigger.document_insert'
  | 'trigger.document_change'
	| 'trigger.filter_criteria'
  | 'trigger.event'
	| 'trigger.any'
  | 'condition.if_else'
  | 'delay.fixed'
	| 'delay.drip'
  | 'delay.until_date'
  | 'transform.value'
  | 'action.update_record'
  | 'action.create_record'
  | 'action.create_todo'
  | 'action.add_comment'
  | 'action.notify_user'
  | 'action.send_email'
  | 'action.send_sms'
	| 'action.instagram_message'
	| 'action.asana'
  | 'action.webhook'
	| 'action.copy_record'
	| 'action.merge_contact'
	| 'action.unassign_record'
	| 'action.create_note'
	| 'action.verify_email'
	| 'action.mark_communications_read'
	| 'action.remove_from_workflow'
	| 'action.complete_goal'
	| 'action.go_to'
  | 'end.complete'

export type Position = { x: number; y: number }
export type NodeConfig = Record<string, unknown>

export interface ConditionPredicate {
  kind: 'predicate'
  field: string
  source?: WorkflowValueSpec
  operator: string
  value?: unknown
}

export interface ConditionGroup {
  kind: 'all' | 'any' | 'not'
  children: ConditionExpression[]
}

export type ConditionExpression = ConditionPredicate | ConditionGroup

export interface WorkflowSettings {
  reenrollment?: 'NEVER' | 'AFTER_COMPLETION' | 'ALWAYS'
  read_mode?: 'CURRENT' | 'ENROLLMENT_SNAPSHOT'
  unenroll_when_ineligible?: boolean
  goal_condition?: ConditionExpression | null
  eligibility_condition?: ConditionExpression | null
	execution_window?: {
		enabled: boolean
		timezone: string
		start_time: string
		end_time: string
		weekdays: number[]
		calendar?: string
	}
	communication?: {
		default_sender_name?: string
		default_sender_email?: string
		default_sms_sender?: string
		stop_on_response?: boolean
		mark_responses_read?: boolean
	}
}

export type WorkflowValueSpec =
  | { kind: 'literal'; value: unknown }
  | { kind: 'record_field'; field: string }
  | { kind: 'node_output'; node_id: string; path: string }

export interface WorkflowAssignment {
  field: string
  operation?: 'set' | 'clear' | 'append' | 'remove'
  value: WorkflowValueSpec
}

export interface WorkflowNode {
  id: string
  type: NodeType
  type_version: 1 | 2
  position: Position
  config: NodeConfig
}

export interface WorkflowEdge {
  id: string
  source: string
  source_handle: string
  target: string
}

export interface WorkflowGraph {
  schema_version: 1
  primary_doctype: string
  start_node_id: string
  nodes: WorkflowNode[]
  edges: WorkflowEdge[]
}

export interface ValidationIssue {
  severity: 'error' | 'warning'
  code: string
  message: string
  path?: string
  node_id?: string
  line?: number
  column?: number
}

export interface WorkflowSummary {
  name: string
  title: string
	folder?: string
  primary_doctype: string
  status: string
  active_version?: string
  trigger_type?: NodeType
  latest_version?: number
  execution_user: string
  modified: string
  owner: string
}

export interface WorkflowPublication {
  state: 'NEVER_PUBLISHED' | 'DRAFT_CHANGES' | 'READY_TO_ACTIVATE' | 'PUBLISHED'
  has_published_version: boolean
  has_unpublished_changes: boolean
  draft_matches_latest_version: boolean
  latest_version?: string
  latest_version_no: number
  active_version?: string
  active_version_no?: number
  next_version_no: number
}

export interface CanvasMetric {
  node_id: string
  reached: number
  ready: number
  running: number
  waiting: number
  completed: number
  failed: number
  cancelled: number
  branches: Record<string, number>
}

export interface CanvasMetricsResponse {
  workflow_id: string
  workflow_version?: string
  total_enrollments: number
  nodes: CanvasMetric[]
}

export interface NodeCatalogItem {
  type: NodeType
  label: string
  category: string
  description: string
  default_config: NodeConfig
  type_version?: 1 | 2
  legacy_runtime_enabled?: boolean
  legacy_disabled_reason?: string
  authoring_hidden?: boolean | number
  authoring_tier?: 'core' | 'advanced' | 'danger'
  available?: boolean
  unavailable_reason?: string | null
  authoring_schema?: { required: Array<{ path: string; label: string }> }
  output_paths: string[]
}

export interface BusinessEventType {
  topic: string
  label: string
  category: string
  description: string
	filter_fields?: Array<Pick<FieldCatalogItem, 'fieldname' | 'label' | 'fieldtype' | 'options'>>
	available_for?: Array<'trigger' | 'wait'>
	source_modes?: Array<'enrolled_record' | 'action_output'>
	source_node_types?: NodeType[]
	producer_status?: 'native' | 'integration_required'
	source_app?: string
	setup_note?: string
	trigger_alternative?: string
	record_resolution?: string
}

export interface WorkflowObjectProfile {
	primary_doctype: string
	label: string
	traits: string[]
	native_event_guidance: {
		created: string
		changed: string
	}
}

export interface FieldCapabilities {
  scalar_read: boolean
  collection_read: boolean
  condition_scalar: boolean
  condition_collection: boolean
  assignment_scalar: boolean
  assignment_collection: boolean
  child_collection: boolean
  switch: boolean
  deduplicate: boolean
  snapshot: boolean
}

export interface FieldCatalogItem {
  fieldname: string
  label: string
  fieldtype: string
  options?: string
  required: boolean
  read_only: boolean
  allow_on_submit: boolean
  description?: string
  default?: unknown
  depends_on?: string
  mandatory_depends_on?: string
  ignore_user_permissions?: boolean
  capabilities?: FieldCapabilities
  child_doctype?: string
  child_fields?: Array<Pick<FieldCatalogItem, 'fieldname' | 'label' | 'fieldtype' | 'options' | 'required'>>
  link_fieldname?: string
  link_doctype?: string
  unsupported_reason?: string
}

export type MetadataPermissionType = 'read' | 'write' | 'create'

export interface DocTypeCatalogItem {
  name: string
  label: string
  module: string
  is_submittable: boolean
  permission_type: MetadataPermissionType
}

export interface FieldCatalogResponse {
  doctype: string
  permission_type: MetadataPermissionType
  available: boolean
  reason_code?: string
  explanation?: string
  fields: FieldCatalogItem[]
  excluded_field_count: number
}

export interface LinkSearchResult {
  value: string
  label?: string
  description?: string
}

export interface WorkflowLookup {
  name: string
  title: string
  primary_doctype: string
  status: string
  active_version?: string
  trigger_type?: NodeType
  runtime_allowed?: boolean
}

export interface SimulationResult {
  valid: boolean
  issues: ValidationIssue[]
  path: Array<{ node_id: string; type: NodeType; status: string; confidence?: 'observed' | 'predicted' | 'skipped'; output: Record<string, unknown>; note?: string }>
  mutated: false
  completed?: boolean
}

export interface RunSummary {
  name: string
  workflow_version: string
  record_doctype: string
  record_name: string
  source: string
  status: string
  started_at?: string
  completed_at?: string
  error_code?: string
  modified: string
}

export interface RuntimeHealth {
  enabled: boolean
  healthy: boolean
  active_subscriptions: number
  quarantined: number
  oldest_ready_age_seconds: number
  default_queue_count: number | null
  dispatcher_status: string | null
  queue_available: boolean
  runs: {
    active: number
    recent_failed: number
    stale_active: number
    orphaned_active: number
    failure_window_hours: number
  }
  open_incidents: number
  open_dead_letters: number
  reasons: string[]
  outbox: {
    PENDING: number
    PROCESSING: number
    PROCESSED: number
    FAILED: number
    DEAD: number
  }
}

export interface TransportReadiness {
  configured: boolean
  provider_count: number
  live_verified: boolean
  message: string
}

export interface RuntimePreflight {
  ready: boolean
  issues: Array<{ code: string; message: string }>
  workers: number
  health: RuntimeHealth
  transports: {
    email: TransportReadiness
    sms: TransportReadiness
    webhook: TransportReadiness
  }
}

export interface EnrollmentVersion {
  name: string
  version_no: number
  published_at: string
  published_by: string
  execution_user: string
  graph_hash: string
}

export interface EnrollmentOverview {
  workflow: WorkflowLookup & { active_version?: string }
  versions: EnrollmentVersion[]
  fields: FieldCatalogItem[]
  system_timezone: string
  runtime_allowed: boolean
}

export interface BackfillPreview {
  workflow_id: string
  workflow_version: string
  version_no: number
  primary_doctype: string
  execution_user: string
  snapshot_at: string
  estimated_count: number
  unbounded_count: number
  sample_records: string[]
  filters: unknown[]
  receipt: {
    workflow_id: string
    workflow_version: string
    snapshot_at: string
    filters: unknown[]
    max_records: number
    expires_at: string
    signature: string
  }
}

export interface BackfillMutationResult {
  backfill_id: string
  status: string
  workflow_version: string
  estimated_count: number
  dry_run: boolean
}

export interface ScheduleMutationResult {
  schedule_id: string
  enabled: boolean
  next_run_at: string
}

export interface InboundWebhookRow {
  name: string
  title: string
  workflow_version: string
  enabled: 0 | 1
  auth_type: 'HMAC SHA256' | 'Bearer'
  record_doctype: string
  record_identity_field: string
  payload_record_path: string
  payload_fields_json?: string | unknown[]
  payload_filters_json?: string | unknown
  idempotency_path: string
  max_request_bytes: number
  requests_per_minute: number
  endpoint: string
  last_received_at?: string
  last_result?: string
  modified: string
}

export interface ManualEnrollmentResult {
  workflow_id: string
  run_id?: string
  enrolled: boolean
}

export interface BackfillRow {
  name: string
  workflow_version: string
  source: 'BACKFILL' | 'SCHEDULE'
  schedule?: string
  status: 'QUEUED' | 'RUNNING' | 'PAUSED' | 'COMPLETED' | 'FAILED' | 'CANCELLED'
  batch_size: number
  records_per_minute: number
  max_records: number
  estimated_count: number
  processed_count: number
  enrolled_count: number
  failed_count: number
  dry_run: 0 | 1
  snapshot_at: string
  next_batch_at?: string
  started_at?: string
  last_heartbeat_at?: string
  completed_at?: string
  error_message?: string
  creation: string
}

export interface ScheduleRow {
  name: string
  enabled: 0 | 1
  frequency: 'ONCE' | 'HOURLY' | 'DAILY' | 'WEEKLY' | 'MONTHLY' | 'ANNUAL' | 'DATE_FIELD'
  recurrence_json?: string | { monthly_mode?: 'DAY' | 'FIRST_WEEKDAY' | 'LAST_WEEKDAY'; day?: number; weekday?: number; month?: number; date_field?: string; date_field_type?: 'Date' | 'Datetime' }
  timezone: string
  version_policy: 'ACTIVE_AT_RUN' | 'PINNED'
  workflow_version?: string
  catch_up_policy: 'RUN_ONCE' | 'SKIP'
  overlap_policy: 'SKIP' | 'QUEUE'
  filters_json?: string | unknown[]
  batch_size: number
  records_per_minute: number
  max_records: number
  next_run_at: string
  last_run_at?: string
  last_backfill_job?: string
  has_history: boolean
  modified: string
}

declare global {
  interface Window {
    csrf_token?: string
    frappe?: {
      boot?: {
        csrf_token?: string
        site_name?: string
        user?: string
        roles?: string[]
        socketio_port?: string | number
        system_timezone?: string
        desk_theme?: 'Light' | 'Dark' | 'Automatic'
      }
    }
  }
}
