import { useFrappeEventListener } from "frappe-react-sdk";
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  CalendarClock,
  CheckCircle2,
  Clock3,
  Eye,
  Filter,
  FlaskConical,
  LayoutDashboard,
  LoaderCircle,
  Pause,
  Play,
  Plus,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  Trash2,
  UsersRound,
  Workflow,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  AsyncCombobox,
  type ComboboxOption,
} from "../components/AsyncCombobox";
import { useConfirmDialog } from "../components/useConfirmDialog";
import { ThemeToggle } from "../components/ThemeToggle";
import { call, mutationEnvelope, searchLink } from "../lib/api";
import {
  backfillPayload,
  filterPayload,
  operatorsFor,
  schedulePayload,
  type FilterRule,
} from "../lib/enrollmentFilters";
import type {
  BackfillMutationResult,
  BackfillPreview,
  BackfillRow,
  EnrollmentOverview,
  FieldCatalogItem,
  InboundWebhookRow,
  ScheduleRow,
  ScheduleMutationResult,
} from "../types";

const primary = "btn-core btn-primary";
const secondary = "btn-core btn-secondary";
const ghost = "btn-core btn-ghost";
const fieldClass = "frappe-control px-3 py-2 text-xs";

function statusTone(value: string) {
  if (["ACTIVE", "COMPLETED"].includes(value))
    return "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-500/10 dark:text-emerald-300";
  if (["FAILED", "CANCELLED", "DISABLED"].includes(value))
    return "border-red-200 bg-red-50 text-red-700 dark:border-red-900 dark:bg-red-500/10 dark:text-red-300";
  if (["PAUSED", "WAITING"].includes(value))
    return "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-900 dark:bg-amber-500/10 dark:text-amber-300";
  return "border-[var(--border-color)] bg-[var(--subtle-fg)] text-[var(--text-muted)]";
}

function Status({ value }: { value: string }) {
  return <span className={`status-pill ${statusTone(value)}`}>{value}</span>;
}

function formatDate(value?: string) {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : new Intl.DateTimeFormat(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(parsed);
}

function nextHourInput() {
  const value = new Date(Date.now() + 60 * 60 * 1000);
  value.setMinutes(0, 0, 0);
  return new Date(value.getTime() - value.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
}

function FilterValue({
  rule,
  metadata,
  change,
}: {
  rule: FilterRule;
  metadata?: FieldCatalogItem;
  change(value: string): void;
}) {
  const loadLinks = useCallback(
    (search: string): Promise<ComboboxOption[]> => {
      if (metadata?.fieldtype !== "Link" || !metadata.options)
        return Promise.resolve([]);
      return searchLink(metadata.options, search).then((rows) =>
        rows.map((row) => ({
          value: row.value,
          label: row.label || row.value,
          description: row.description,
        })),
      );
    },
    [metadata?.fieldtype, metadata?.options],
  );
  if (rule.operator === "is")
    return (
      <select
        className={fieldClass}
        value={rule.value || "set"}
        onChange={(event) => change(event.target.value)}
      >
        <option value="set">Is set</option>
        <option value="not set">Is not set</option>
      </select>
    );
  if (
    metadata?.fieldtype === "Link" &&
    !["in", "not in", "like", "not like"].includes(rule.operator)
  )
    return (
      <AsyncCombobox
        ariaLabel={`${metadata.label} filter value`}
        value={rule.value}
        onChange={change}
        loadOptions={loadLinks}
        placeholder={`Search ${metadata.options || "records"}…`}
      />
    );
  if (metadata?.fieldtype === "Check")
    return (
      <select
        className={fieldClass}
        value={rule.value || "1"}
        onChange={(event) => change(event.target.value)}
      >
        <option value="1">Yes</option>
        <option value="0">No</option>
      </select>
    );
  if (
    metadata?.fieldtype === "Select" &&
    metadata.options &&
    !["in", "not in", "like", "not like"].includes(rule.operator)
  )
    return (
      <select
        className={fieldClass}
        value={rule.value}
        onChange={(event) => change(event.target.value)}
      >
        <option value="">Choose value</option>
        {metadata.options
          .split("\n")
          .filter(Boolean)
          .map((option) => (
            <option value={option} key={option}>
              {option}
            </option>
          ))}
      </select>
    );
  const inputType =
    metadata?.fieldtype === "Date"
      ? "date"
      : metadata?.fieldtype === "Datetime"
        ? "datetime-local"
        : ["Int", "Float", "Currency", "Percent"].includes(
              metadata?.fieldtype || "",
            )
          ? "number"
          : "text";
  return (
    <input
      type={inputType}
      className={fieldClass}
      value={rule.value}
      onChange={(event) => change(event.target.value)}
      placeholder={
        ["in", "not in"].includes(rule.operator)
          ? "Comma-separated values"
          : "Filter value"
      }
    />
  );
}

function FilterBuilder({
  fields,
  rules,
  change,
}: {
  fields: FieldCatalogItem[];
  rules: FilterRule[];
  change(rules: FilterRule[]): void;
}) {
  const metadata = useMemo(
    () => new Map(fields.map((item) => [item.fieldname, item])),
    [fields],
  );
  const update = (id: string, values: Partial<FilterRule>) =>
    change(
      rules.map((rule) => (rule.id === id ? { ...rule, ...values } : rule)),
    );
  return (
    <section className="surface-flat overflow-hidden rounded-xl">
      <div className="flex items-center justify-between border-b border-[var(--border-color)] px-5 py-4">
        <div>
          <div className="flex items-center gap-2">
            <Filter className="text-brand-500" size={15} />
            <h2 className="text-heading text-sm font-bold">Audience filters</h2>
          </div>
          <p className="text-muted mt-1 text-[10px]">
            All rules use AND logic and run under the published execution user.
          </p>
        </div>
        <button
          type="button"
          className={secondary}
          onClick={() =>
            change([
              ...rules,
              { id: crypto.randomUUID(), field: "", operator: "=", value: "" },
            ])
          }
        >
          <Plus size={13} />
          Add rule
        </button>
      </div>
      <div className="space-y-2 p-4">
        {rules.map((rule, index) => {
          const selected = metadata.get(rule.field);
          const operators = operatorsFor(selected);
          return (
            <div
              className="grid items-center gap-2 rounded-lg border border-[var(--border-color)] bg-white/40 dark:bg-transparent p-2.5 md:grid-cols-[28px_minmax(150px,1fr)_130px_minmax(160px,1fr)_34px]"
              key={rule.id}
            >
              <span className="grid size-6 place-items-center rounded-full bg-[var(--subtle-fg)] text-[9px] font-bold text-[var(--text-light)]">
                {index + 1}
              </span>
              <select
                className={fieldClass}
                value={rule.field}
                onChange={(event) =>
                  update(rule.id, {
                    field: event.target.value,
                    operator: "=",
                    value: "",
                  })
                }
              >
                <option value="">Choose a field</option>
                {fields.map((item) => (
                  <option value={item.fieldname} key={item.fieldname}>
                    {item.label} · {item.fieldtype}
                  </option>
                ))}
              </select>
              <select
                className={fieldClass}
                value={rule.operator}
                onChange={(event) =>
                  update(rule.id, {
                    operator: event.target.value,
                    value: event.target.value === "is" ? "set" : "",
                  })
                }
              >
                {operators.map((operator) => (
                  <option value={operator} key={operator}>
                    {operator}
                  </option>
                ))}
              </select>
              <FilterValue
                rule={rule}
                metadata={selected}
                change={(value) => update(rule.id, { value })}
              />
              <button
                type="button"
                className="icon-button !size-8 text-red-500"
                onClick={() =>
                  change(rules.filter((item) => item.id !== rule.id))
                }
                aria-label={`Remove filter ${index + 1}`}
              >
                <X size={14} />
              </button>
            </div>
          );
        })}
        {!rules.length && (
          <div className="rounded-lg border border-dashed border-[var(--dark-border-color)] px-4 py-8 text-center">
            <UsersRound className="text-light mx-auto" size={20} />
            <p className="text-heading mt-2 text-xs font-semibold">
              All readable records match
            </p>
            <p className="text-muted mt-1 text-[10px]">
              Add filters before previewing when the workflow should target a
              smaller audience.
            </p>
          </div>
        )}
      </div>
    </section>
  );
}

function WebhookEnrollment({
  workflowId,
  overview,
}: {
  workflowId: string;
  overview: EnrollmentOverview;
}) {
  const [rows, setRows] = useState<InboundWebhookRow[]>([]);
  const [title, setTitle] = useState("ERP inbound enrollment");
  const [authType, setAuthType] = useState<"HMAC SHA256" | "Bearer">(
    "HMAC SHA256",
  );
  const [recordPath, setRecordPath] = useState("record_id");
  const [idempotencyPath, setIdempotencyPath] = useState("event_id");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [credential, setCredential] = useState<{
    endpoint: string;
    secret: string;
  }>();
  const confirmation = useConfirmDialog();
  const load = useCallback(
    () =>
      call<{ rows: InboundWebhookRow[] }>("list_inbound_webhooks", {
        workflow_id: workflowId,
      })
        .then((result) => {
          setRows(result.rows);
          setError("");
        })
        .catch((reason) =>
          setError(
            reason instanceof Error
              ? reason.message
              : "Unable to load webhooks",
          ),
        ),
    [workflowId],
  );
  useEffect(() => {
    void load();
  }, [load]);
  const create = async () => {
    setBusy("create");
    setError("");
    setCredential(undefined);
    try {
      const result = await call<{
        name: string;
        endpoint: string;
        secret: string;
      }>(
        "create_inbound_webhook",
        mutationEnvelope(workflowId, {
          title,
          auth_type: authType,
          record_identity_field: "name",
          payload_record_path: recordPath,
          idempotency_path: idempotencyPath,
          payload_fields: [recordPath, idempotencyPath],
        }),
        true,
      );
      setCredential({
        endpoint: `${window.location.origin}${result.endpoint}`,
        secret: result.secret,
      });
      await load();
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Unable to create webhook",
      );
    } finally {
      setBusy("");
    }
  };
  const toggle = async (row: InboundWebhookRow) => {
    setBusy(row.name);
    setError("");
    try {
      await call(
        "set_inbound_webhook_enabled",
        { webhook_id: row.name, enabled: row.enabled ? 0 : 1 },
        true,
      );
      await load();
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Unable to update webhook",
      );
    } finally {
      setBusy("");
    }
  };
  const rotate = async (row: InboundWebhookRow) => {
    if (!await confirmation.ask({
      title: `Rotate the secret for ${row.title}?`,
      description: "Existing senders will stop authenticating immediately. Update every sender with the newly generated secret before its next request.",
      confirmLabel: "Rotate secret",
      tone: "danger",
    })) return;
    setBusy(`rotate:${row.name}`);
    setError("");
    try {
      const result = await call<{ secret: string }>(
        "rotate_inbound_webhook_secret",
        { webhook_id: row.name },
        true,
      );
      setCredential({
        endpoint: `${window.location.origin}${row.endpoint}`,
        secret: result.secret,
      });
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Unable to rotate secret",
      );
    } finally {
      setBusy("");
    }
  };
  return (
    <div className="app-shell min-h-screen">
      <header className="app-topbar px-4 sm:px-5">
        <div className="flex min-w-0 items-center gap-3">
          <Link to="/" className="flex items-center gap-2.5">
            <span className="brand-mark shrink-0">
              <Workflow size={18} />
            </span>
            <strong className="text-heading hidden text-[13px] sm:block">
              Workflow Builder
            </strong>
          </Link>
          <Link
            className="icon-button"
            to={`/${workflowId}`}
            aria-label="Back to editor"
          >
            <ArrowLeft size={16} />
          </Link>
          <div>
            <h1 className="text-heading text-xs font-bold">
              {overview.workflow.title}
            </h1>
            <p className="text-light text-[9px]">Incoming webhook enrollment</p>
          </div>
        </div>
        <ThemeToggle />
      </header>
      <main className="mx-auto max-w-6xl px-4 py-7 sm:px-6">
        <section className="hero-glow surface rounded-2xl p-6">
          <div className="flex items-center gap-3">
            <span className="grid size-11 place-items-center rounded-xl bg-brand-50 text-brand-600 dark:bg-brand-500/10">
              <ShieldCheck size={19} />
            </span>
            <div>
              <p className="text-[9px] font-bold uppercase tracking-[.12em] text-brand-600">
                Managed endpoint
              </p>
              <h1 className="text-heading mt-1 text-xl font-bold">
                Authenticated, exact-record enrollment
              </h1>
              <p className="text-muted mt-1 text-[10px]">
                Requests are size-limited, rate-limited, mapped to one{" "}
                {overview.workflow.primary_doctype}, deduplicated, and persisted
                to the automation outbox.
              </p>
            </div>
          </div>
        </section>
        {error && (
          <p className="mt-4 rounded-xl border border-red-200 bg-red-50 p-3 text-xs text-red-700 dark:border-red-900 dark:bg-red-500/10 dark:text-red-300">
            {error}
          </p>
        )}
        {credential && (
          <section className="mt-4 rounded-xl border border-amber-300 bg-amber-50 p-4 dark:border-amber-800 dark:bg-amber-500/10">
            <strong className="text-heading text-xs">
              Save these credentials now
            </strong>
            <p className="text-muted mt-1 text-[10px]">
              The secret is shown only for this creation or rotation response.
            </p>
            <label className="text-heading mt-3 block text-[9px] font-bold">
              Endpoint
              <input
                readOnly
                className={`${fieldClass} mt-1 w-full`}
                value={credential.endpoint}
              />
            </label>
            <label className="text-heading mt-2 block text-[9px] font-bold">
              Secret
              <input
                readOnly
                className={`${fieldClass} mt-1 w-full font-mono`}
                value={credential.secret}
              />
            </label>
          </section>
        )}
        <section className="mt-5 grid gap-5 lg:grid-cols-[380px_minmax(0,1fr)]">
          <form
            className="surface-flat rounded-xl p-5"
            onSubmit={(event) => {
              event.preventDefault();
              void create();
            }}
          >
            <h2 className="text-heading text-sm font-bold">Create endpoint</h2>
            <p className="text-muted mt-1 text-[10px]">
              Created disabled and pinned to {overview.workflow.active_version}.
            </p>
            <div className="mt-4 space-y-3">
              <label className="text-heading block text-[10px] font-semibold">
                Name
                <input
                  className={`${fieldClass} mt-1.5 w-full`}
                  value={title}
                  onChange={(event) => setTitle(event.target.value)}
                  required
                />
              </label>
              <label className="text-heading block text-[10px] font-semibold">
                Authentication
                <select
                  className={`${fieldClass} mt-1.5 w-full`}
                  value={authType}
                  onChange={(event) =>
                    setAuthType(event.target.value as typeof authType)
                  }
                >
                  <option value="HMAC SHA256">HMAC SHA256 signature</option>
                  <option value="Bearer">Bearer token</option>
                </select>
              </label>
              <label className="text-heading block text-[10px] font-semibold">
                Payload path containing {overview.workflow.primary_doctype} name
                <input
                  className={`${fieldClass} mt-1.5 w-full`}
                  value={recordPath}
                  onChange={(event) => setRecordPath(event.target.value)}
                  required
                />
              </label>
              <label className="text-heading block text-[10px] font-semibold">
                Payload idempotency path
                <input
                  className={`${fieldClass} mt-1.5 w-full`}
                  value={idempotencyPath}
                  onChange={(event) => setIdempotencyPath(event.target.value)}
                  required
                />
              </label>
            </div>
            <button
              className={`${primary} mt-5 w-full`}
              disabled={busy === "create"}
            >
              {busy === "create" ? (
                <LoaderCircle className="animate-spin" size={13} />
              ) : (
                <Plus size={13} />
              )}
              Create disabled endpoint
            </button>
          </form>
          <div className="surface-flat overflow-hidden rounded-xl">
            <div className="border-b border-[var(--border-color)] px-5 py-4">
              <h2 className="text-heading text-sm font-bold">Endpoints</h2>
              <p className="text-muted mt-1 text-[10px]">
                Raw secrets are never returned by this list.
              </p>
            </div>
            {rows.length ? (
              <div className="divide-y divide-[var(--border-color)]">
                {rows.map((row) => (
                  <article className="p-4" key={row.name}>
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <div className="flex items-center gap-2">
                          <strong className="text-heading text-xs">
                            {row.title}
                          </strong>
                          <Status value={row.enabled ? "ACTIVE" : "DISABLED"} />
                        </div>
                        <p className="text-light mt-1 text-[9px]">
                          {row.auth_type} · version {row.workflow_version} ·{" "}
                          {row.requests_per_minute}/min ·{" "}
                          {row.max_request_bytes} bytes
                        </p>
                      </div>
                      <div className="flex gap-1.5">
                        <button
                          className={row.enabled ? secondary : primary}
                          disabled={busy === row.name}
                          onClick={() => void toggle(row)}
                        >
                          {row.enabled ? (
                            <Pause size={12} />
                          ) : (
                            <Play size={12} />
                          )}
                          {row.enabled ? "Disable" : "Enable"}
                        </button>
                        <button
                          className={secondary}
                          disabled={busy === `rotate:${row.name}`}
                          onClick={() => void rotate(row)}
                        >
                          <RotateCcw size={12} />
                          Rotate secret
                        </button>
                      </div>
                    </div>
                    <label className="text-heading mt-3 block text-[9px] font-bold">
                      Endpoint
                      <input
                        readOnly
                        className={`${fieldClass} mt-1 w-full font-mono text-[9px]`}
                        value={`${window.location.origin}${row.endpoint}`}
                      />
                    </label>
                    <p className="text-muted mt-2 text-[9px]">
                      Map <strong>{row.payload_record_path}</strong> →{" "}
                      {row.record_doctype}.{row.record_identity_field};
                      idempotency from <strong>{row.idempotency_path}</strong>
                      {row.last_received_at
                        ? ` · last receipt ${formatDate(row.last_received_at)}`
                        : ""}
                    </p>
                  </article>
                ))}
              </div>
            ) : (
              <div className="p-10 text-center">
                <ShieldCheck className="text-light mx-auto" size={20} />
                <p className="text-heading mt-3 text-xs font-bold">
                  No endpoints yet
                </p>
              </div>
            )}
          </div>
        </section>
      </main>
      {confirmation.dialog}
    </div>
  );
}

export function EnrollmentPage() {
  const { workflowId = "" } = useParams();
  const [overview, setOverview] = useState<EnrollmentOverview>();
  const [backfills, setBackfills] = useState<BackfillRow[]>([]);
  const [schedules, setSchedules] = useState<ScheduleRow[]>([]);
  const [rules, setRules] = useState<FilterRule[]>([]);
  const [preview, setPreview] = useState<BackfillPreview>();
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState<{
    tone: "success" | "info";
    message: string;
  }>();
  const [batchSize, setBatchSize] = useState(100);
  const [rate, setRate] = useState(500);
  const [maxRecords, setMaxRecords] = useState(0);
  const [frequency, setFrequency] = useState<
    "ONCE" | "HOURLY" | "DAILY" | "WEEKLY" | "MONTHLY" | "ANNUAL" | "DATE_FIELD"
  >("DAILY");
  const [monthlyMode, setMonthlyMode] = useState<
    "DAY" | "FIRST_WEEKDAY" | "LAST_WEEKDAY"
  >("DAY");
  const [monthlyWeekday, setMonthlyWeekday] = useState(0);
  const [scheduleDateField, setScheduleDateField] = useState("");
  const [nextRunAt, setNextRunAt] = useState(nextHourInput);
  const [timezone, setTimezone] = useState("");
  const [versionPolicy, setVersionPolicy] = useState<
    "ACTIVE_AT_RUN" | "PINNED"
  >("ACTIVE_AT_RUN");
  const [pinnedVersion, setPinnedVersion] = useState("");
  const [catchUp, setCatchUp] = useState<"RUN_ONCE" | "SKIP">("RUN_ONCE");
  const [overlap, setOverlap] = useState<"SKIP" | "QUEUE">("SKIP");
  const confirmation = useConfirmDialog();

  const load = useCallback(
    async (silent = false) => {
      if (!silent) setLoading(true);
      try {
        const [summary, jobs, scheduleRows] = await Promise.all([
          call<EnrollmentOverview>("get_enrollment_overview", {
            workflow_id: workflowId,
          }),
          call<{ rows: BackfillRow[] }>("list_backfills", {
            workflow_id: workflowId,
            page_length: 100,
          }),
          call<{ rows: ScheduleRow[] }>("list_schedules", {
            workflow_id: workflowId,
          }),
        ]);
        setOverview(summary);
        setBackfills(jobs.rows);
        setSchedules(scheduleRows.rows);
        setTimezone((current) => current || summary.system_timezone);
        setPinnedVersion(
          (current) =>
            current ||
            summary.workflow.active_version ||
            summary.versions[0]?.name ||
            "",
        );
        setError("");
      } catch (reason) {
        setError(
          reason instanceof Error
            ? reason.message
            : "Unable to load enrollment operations",
        );
      } finally {
        if (!silent) setLoading(false);
      }
    },
    [workflowId],
  );

  useEffect(() => {
    void load();
  }, [load]);
  useEffect(() => {
    const timer = window.setInterval(() => void load(true), 10000);
    return () => window.clearInterval(timer);
  }, [load]);
  const onBackfillUpdate = useCallback(
    (event: { workflow_id?: string }) => {
      if (event.workflow_id === workflowId) void load(true);
    },
    [load, workflowId],
  );
  useFrappeEventListener("automation_backfill_updated", onBackfillUpdate);

  useEffect(() => {
    setPreview(undefined);
  }, [rules, maxRecords]);
  const filters = useMemo(
    () => filterPayload(rules, overview?.fields || []),
    [overview?.fields, rules],
  );
  const loadTimezones = useCallback(
    (search: string): Promise<ComboboxOption[]> =>
      call<{ rows: ComboboxOption[] }>("get_timezones", { search }).then(
        (result) => result.rows,
      ),
    [],
  );

  const runMutation = async <T,>(
    key: string,
    action: () => Promise<T>,
    success?: (result: T) => string,
  ) => {
    setBusy(key);
    setError("");
    setNotice(undefined);
    try {
      const result = await action();
      if (success) setNotice({ tone: "success", message: success(result) });
      await load(true);
      return result;
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Unable to update enrollment operations",
      );
      return undefined;
    } finally {
      setBusy("");
    }
  };
  const previewAudience = () =>
    runMutation("preview", async () => {
      const result = await call<BackfillPreview>(
        "preview_backfill",
        mutationEnvelope(workflowId, { filters, max_records: maxRecords }),
        true,
      );
      setPreview(result);
      return result;
    });
  const startBackfill = async (dryRun: boolean) => {
    if (!preview) return;
    if (!dryRun && !await confirmation.ask({
      title: `Start a live backfill for ${preview.estimated_count.toLocaleString()} records?`,
      description: `Eligible records will enter published version ${preview.version_no}. Workflow actions can change records and contact external services.`,
      confirmLabel: "Start live backfill",
      tone: "warning",
    })) return;
    const payload = backfillPayload(
      { filters, batchSize, recordsPerMinute: rate, maxRecords },
      dryRun,
    );
    void runMutation(
      dryRun ? "dry-run" : "backfill",
      () =>
        call<BackfillMutationResult>(
          "start_backfill",
          mutationEnvelope(workflowId, {
            ...payload,
            preview_receipt: preview.receipt,
          }),
          true,
        ),
      (result) =>
        `${dryRun ? "Dry run" : "Backfill"} ${result.backfill_id} queued for ${result.estimated_count.toLocaleString()} records.`,
    );
  };
  const controlBackfill = async (
    row: BackfillRow,
    action: "PAUSE" | "RESUME" | "CANCEL" | "RETRY",
  ) => {
    if (action === "CANCEL" && !await confirmation.ask({
      title: `Cancel backfill ${row.name}?`,
      description: "No new batches will start. The current committed batch and records already enrolled remain authoritative.",
      confirmLabel: "Cancel backfill",
      tone: "danger",
    })) return;
    void runMutation(
      `${row.name}:${action}`,
      () =>
        call<{ backfill_id: string; status: string }>(
          "control_backfill",
          { backfill_id: row.name, action },
          true,
        ),
      (result) =>
        `${result.backfill_id} is now ${result.status.toLowerCase()}.`,
    );
  };
  const createSchedule = () =>
    runMutation(
      "schedule",
      () =>
        call<ScheduleMutationResult>(
          "create_schedule",
          mutationEnvelope(
            workflowId,
            schedulePayload({
              filters,
              batchSize,
              recordsPerMinute: rate,
              maxRecords,
              frequency,
              nextRunAt,
              timezone,
              recurrence:
                frequency === "MONTHLY"
                  ? {
                      monthly_mode: monthlyMode,
                      day: Number(nextRunAt.slice(8, 10)) || 1,
                      weekday: monthlyWeekday,
                    }
                  : frequency === "ANNUAL"
                    ? {
                        month: Number(nextRunAt.slice(5, 7)) || 1,
                        day: Number(nextRunAt.slice(8, 10)) || 1,
                      }
                    : frequency === "DATE_FIELD"
                      ? { date_field: scheduleDateField }
                      : undefined,
              versionPolicy,
              workflowVersion: pinnedVersion,
              catchUpPolicy: catchUp,
              overlapPolicy: overlap,
            }),
          ),
          true,
        ),
      (result) => `Schedule ${result.schedule_id} created disabled for review.`,
    );
  const toggleSchedule = (row: ScheduleRow) =>
    runMutation(
      `schedule:${row.name}`,
      () =>
        call<ScheduleMutationResult>(
          "set_schedule_enabled",
          { schedule_id: row.name, enabled: row.enabled ? 0 : 1 },
          true,
        ),
      (result) =>
        `Schedule ${result.schedule_id} ${result.enabled ? "enabled" : "disabled"}.`,
    );
  const deleteSchedule = async (row: ScheduleRow) => {
    if (!await confirmation.ask({
      title: `Delete schedule ${row.name}?`,
      description: "This disabled schedule has no execution history and will be removed permanently.",
      confirmLabel: "Delete schedule",
      tone: "danger",
    })) return;
    void runMutation(
      `delete:${row.name}`,
      () =>
        call<{ schedule_id: string; deleted: boolean }>(
          "delete_schedule",
          { schedule_id: row.name },
          true,
        ),
      (result) => `Schedule ${result.schedule_id} deleted.`,
    );
  };

  if (loading)
    return (
      <div className="app-shell grid min-h-screen place-items-center">
        <LoaderCircle className="animate-spin text-brand-500" />
      </div>
    );
  const workflow = overview?.workflow;
  const workflowReady =
    workflow?.status === "ACTIVE" && Boolean(workflow.active_version);
  const canRun = workflowReady && Boolean(overview?.runtime_allowed);
  if (workflow?.trigger_type === "trigger.webhook" && overview)
    return <WebhookEnrollment workflowId={workflowId} overview={overview} />;
  return (
    <div className="app-shell min-h-screen">
      <header className="app-topbar px-4 sm:px-5">
        <div className="flex min-w-0 items-center gap-3">
          <Link to="/" className="flex items-center gap-2.5">
            <span className="brand-mark shrink-0">
              <Workflow size={18} />
            </span>
            <span className="hidden sm:block">
              <strong className="text-heading block text-[13px]">
                Workflow Builder
              </strong>
              <span className="text-light block text-[9px] font-semibold uppercase tracking-[0.12em]">
                Enrollment control
              </span>
            </span>
          </Link>
          <span className="hidden h-7 w-px bg-[var(--border-color)] sm:block" />
          <Link
            className="icon-button shrink-0"
            to={`/${workflowId}`}
            aria-label="Back to editor"
          >
            <ArrowLeft size={16} />
          </Link>
          <div className="hidden min-w-0 sm:block">
            <h1 className="text-heading truncate text-xs font-bold">
              {workflow?.title}
            </h1>
            <p className="text-light text-[9px]">
              {workflow?.primary_doctype} · {workflowId}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-1">
          <a className={ghost} href="/app">
            <LayoutDashboard size={14} />
            <span className="hidden sm:inline">Desk</span>
          </a>
          <ThemeToggle />
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-4 py-7 sm:px-6 lg:px-8">
        <section className="hero-glow surface rounded-2xl p-6 sm:p-7">
          <div className="flex flex-col justify-between gap-5 lg:flex-row lg:items-center">
            <div>
              <div className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-[0.14em] text-brand-600">
                <CalendarClock size={13} />
                Scheduled &amp; bulk enrollment
              </div>
              <h1 className="text-heading mt-2 text-2xl font-bold tracking-tight">
                Reach the right records, safely.
              </h1>
              <p className="text-muted mt-2 max-w-2xl text-xs leading-5">
                Preview eligible records, run version-pinned backfills, and
                schedule recurring enrollment with durable recovery controls.
              </p>
            </div>
            <div className="flex items-center gap-3 rounded-xl border border-[var(--border-color)] bg-white/40 dark:bg-transparent p-4 shadow-sm">
              <span className="grid size-10 place-items-center rounded-xl bg-brand-50 text-brand-600 dark:bg-brand-500/10">
                <ShieldCheck size={17} />
              </span>
              <div>
                <Status value={workflow?.status || "UNKNOWN"} />
                <p className="text-muted mt-1.5 text-[9px]">
                  Active version {workflow?.active_version || "not published"}
                </p>
              </div>
            </div>
          </div>
        </section>
        {error && (
          <div className="mt-4 flex items-center justify-between gap-3 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-xs text-red-700 dark:border-red-900 dark:bg-red-500/10 dark:text-red-300">
            <span className="flex items-center gap-2">
              <AlertTriangle size={14} />
              {error}
            </span>
            <button className={secondary} onClick={() => void load()}>
              Reload
            </button>
          </div>
        )}
        {notice && (
          <div
            aria-live="polite"
            className="mt-4 flex items-center justify-between gap-3 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-xs text-emerald-700 dark:border-emerald-900 dark:bg-emerald-500/10 dark:text-emerald-300"
          >
            <span className="flex items-center gap-2">
              <CheckCircle2 size={14} />
              {notice.message}
            </span>
            <button
              type="button"
              className="icon-button !size-7"
              onClick={() => setNotice(undefined)}
              aria-label="Dismiss message"
            >
              <X size={13} />
            </button>
          </div>
        )}
        {!canRun && (
          <div className="mt-4 flex items-center gap-3 rounded-xl border border-amber-200 bg-amber-50 p-4 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-500/10 dark:text-amber-300">
            <Pause size={16} />
            <div>
              <strong className="block">Enrollment controls are held</strong>
              <span className="mt-0.5 block text-[10px]">
                {workflowReady
                  ? "Workflow execution is disabled in Automation Settings."
                  : "Publish and activate this workflow before starting backfills or enabling schedules."}
              </span>
            </div>
          </div>
        )}

        <div className="mt-6 grid gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
          <FilterBuilder
            fields={overview?.fields || []}
            rules={rules}
            change={setRules}
          />
          <aside className="surface-flat rounded-xl p-5">
            <div className="flex items-center gap-2">
              <Activity className="text-magic-500" size={15} />
              <h2 className="text-heading text-sm font-bold">
                Processing limits
              </h2>
            </div>
            <div className="mt-4 grid gap-3">
              <label className="text-heading text-[10px] font-semibold">
                Batch size
                <input
                  className={`${fieldClass} mt-1.5`}
                  type="number"
                  min={1}
                  max={500}
                  value={batchSize}
                  onChange={(event) => setBatchSize(Number(event.target.value))}
                />
              </label>
              <label className="text-heading text-[10px] font-semibold">
                Records per minute
                <input
                  className={`${fieldClass} mt-1.5`}
                  type="number"
                  min={1}
                  max={10000}
                  value={rate}
                  onChange={(event) => setRate(Number(event.target.value))}
                />
              </label>
              <label className="text-heading text-[10px] font-semibold">
                Maximum records{" "}
                <span className="text-light font-normal">(0 = all)</span>
                <input
                  className={`${fieldClass} mt-1.5`}
                  type="number"
                  min={0}
                  value={maxRecords}
                  onChange={(event) =>
                    setMaxRecords(Number(event.target.value))
                  }
                />
              </label>
            </div>
            <button
              className={`${secondary} mt-4 w-full`}
              disabled={busy === "preview" || !workflow?.active_version}
              onClick={() => void previewAudience()}
            >
              {busy === "preview" ? (
                <LoaderCircle className="animate-spin" size={14} />
              ) : (
                <Eye size={14} />
              )}
              Preview audience
            </button>
          </aside>
        </div>
        {preview && (
          <section className="mt-5 grid gap-3 rounded-xl border border-magic-100 bg-magic-50/60 p-5 dark:border-magic-500/20 dark:bg-magic-500/5 md:grid-cols-[1fr_auto_auto]">
            <div>
              <div className="flex items-center gap-2">
                <FlaskConical className="text-magic-600" size={15} />
                <h2 className="text-heading text-sm font-bold">
                  Safe audience preview
                </h2>
              </div>
              <p className="text-muted mt-1 text-[10px]">
                Version {preview.version_no} · {preview.execution_user} ·
                snapshot {formatDate(preview.snapshot_at)}
              </p>
              <div className="mt-3 flex flex-wrap gap-1.5">
                {preview.sample_records.map((name) => (
                  <span
                    className="rounded-md border border-[var(--border-color)] bg-white/50 dark:bg-transparent px-2 py-1 text-[9px]"
                    key={name}
                  >
                    {name}
                  </span>
                ))}
                {!preview.sample_records.length && (
                  <span className="text-muted text-[10px]">
                    No matching records
                  </span>
                )}
              </div>
            </div>
            <div className="metric-card min-w-28 rounded-xl p-4 text-center">
              <strong className="text-heading block text-2xl">
                {preview.estimated_count.toLocaleString()}
              </strong>
              <span className="text-light text-[9px] font-bold uppercase tracking-wider">
                Will process
              </span>
            </div>
            <div className="flex flex-col justify-center gap-2">
              <button
                className={secondary}
                disabled={!preview.estimated_count || !canRun || Boolean(busy)}
                onClick={() => void startBackfill(true)}
              >
                <FlaskConical size={13} />
                Start dry run
              </button>
              <button
                className={primary}
                disabled={!preview.estimated_count || !canRun || Boolean(busy)}
                onClick={() => void startBackfill(false)}
              >
                <Play size={13} />
                Start backfill
              </button>
            </div>
          </section>
        )}

        <section className="mt-6 surface-flat overflow-hidden rounded-xl">
          <div className="flex items-center justify-between border-b border-[var(--border-color)] px-5 py-4">
            <div>
              <div className="flex items-center gap-2">
                <UsersRound className="text-brand-500" size={16} />
                <h2 className="text-heading text-sm font-bold">
                  Backfill jobs
                </h2>
              </div>
              <p className="text-muted mt-1 text-[10px]">
                Every job remains pinned to the version shown below.
              </p>
            </div>
            <button className={secondary} onClick={() => void load(true)}>
              <RefreshCw size={13} />
              Refresh
            </button>
          </div>
          {backfills.length ? (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[1020px] text-left text-xs">
                <thead className="bg-[var(--subtle-fg)] text-[9px] font-bold uppercase tracking-wider text-[var(--text-light)]">
                  <tr>
                    <th className="px-4 py-3">Job</th>
                    <th className="px-4 py-3">Version</th>
                    <th className="px-4 py-3">Progress</th>
                    <th className="px-4 py-3">Enrolled</th>
                    <th className="px-4 py-3">Status</th>
                    <th className="px-4 py-3">Updated</th>
                    <th className="px-4 py-3">Controls</th>
                  </tr>
                </thead>
                <tbody>
                  {backfills.map((row) => {
                    const total = Math.max(
                      row.estimated_count,
                      row.processed_count,
                      1,
                    );
                    const percent = Math.min(
                      100,
                      Math.round((row.processed_count / total) * 100),
                    );
                    return (
                      <tr className="table-row" key={row.name}>
                        <td className="px-4 py-3">
                          <strong className="text-heading block text-[11px]">
                            {row.name}
                          </strong>
                          <span className="text-light text-[9px]">
                            {row.dry_run ? "Dry run" : row.source}
                            {row.schedule ? ` · ${row.schedule}` : ""}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-[10px]">
                          {row.workflow_version}
                        </td>
                        <td className="px-4 py-3">
                          <div className="w-36">
                            <div className="h-1.5 overflow-hidden rounded-full bg-[var(--control-bg)]">
                              <span
                                className="block h-full rounded-full bg-brand-500"
                                style={{ width: `${percent}%` }}
                              />
                            </div>
                            <span className="text-light mt-1 block text-[9px]">
                              {row.processed_count.toLocaleString()} /{" "}
                              {row.estimated_count.toLocaleString()}
                            </span>
                          </div>
                        </td>
                        <td className="px-4 py-3">
                          <strong className="text-heading">
                            {row.enrolled_count.toLocaleString()}
                          </strong>
                        </td>
                        <td className="px-4 py-3">
                          <Status value={row.status} />
                          {row.error_message && (
                            <p
                              className="mt-1 max-w-48 truncate text-[9px] text-red-600"
                              title={row.error_message}
                            >
                              {row.error_message}
                            </p>
                          )}
                        </td>
                        <td className="text-muted px-4 py-3 text-[9px]">
                          {formatDate(row.last_heartbeat_at || row.creation)}
                        </td>
                        <td className="px-4 py-3">
                          <div className="flex gap-1">
                            {["QUEUED", "RUNNING"].includes(row.status) && (
                              <button
                                className={secondary}
                                onClick={() => controlBackfill(row, "PAUSE")}
                              >
                                <Pause size={11} />
                                Pause
                              </button>
                            )}
                            {row.status === "PAUSED" && (
                              <button
                                className={primary}
                                onClick={() => controlBackfill(row, "RESUME")}
                              >
                                <Play size={11} />
                                Resume
                              </button>
                            )}
                            {row.status === "FAILED" && (
                              <button
                                className={primary}
                                onClick={() => controlBackfill(row, "RETRY")}
                              >
                                <RotateCcw size={11} />
                                Retry
                              </button>
                            )}
                            {!["COMPLETED", "FAILED", "CANCELLED"].includes(
                              row.status,
                            ) && (
                              <button
                                className={ghost}
                                onClick={() => controlBackfill(row, "CANCEL")}
                              >
                                <X size={11} />
                                Cancel
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="px-6 py-12 text-center">
              <CheckCircle2 className="text-light mx-auto" size={20} />
              <p className="text-heading mt-3 text-xs font-bold">
                No backfills yet
              </p>
              <p className="text-muted mt-1 text-[10px]">
                Preview an audience before starting the first job.
              </p>
            </div>
          )}
        </section>

        <section className="mt-6 grid gap-5 xl:grid-cols-[420px_minmax(0,1fr)]">
          <form
            className="surface-flat rounded-xl p-5"
            onSubmit={(event) => {
              event.preventDefault();
              void createSchedule();
            }}
          >
            <div className="flex items-center gap-2">
              <CalendarClock className="text-magic-500" size={16} />
              <h2 className="text-heading text-sm font-bold">
                Create schedule
              </h2>
            </div>
            <p className="text-muted mt-1 text-[10px]">
              Uses calendar-safe local recurrence and the eligible audience
              configured above.
            </p>
            <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-1">
              <label className="text-heading text-[10px] font-semibold">
                Frequency
                <select
                  className={`${fieldClass} mt-1.5`}
                  value={frequency}
                  onChange={(event) =>
                    setFrequency(event.target.value as typeof frequency)
                  }
                >
                  <option value="ONCE">Once</option>
                  <option value="DAILY">Daily</option>
                  <option value="WEEKLY">Weekly</option>
                  <option value="MONTHLY">Monthly</option>
                  <option value="ANNUAL">Annually</option>
                  <option value="DATE_FIELD">On Date/Datetime property</option>
                  <option value="HOURLY">Hourly (ERP advanced)</option>
                </select>
              </label>
              {frequency === "DATE_FIELD" && (
                <label className="text-heading text-[10px] font-semibold">
                  Date property
                  <select
                    className={`${fieldClass} mt-1.5`}
                    required
                    value={scheduleDateField}
                    onChange={(event) => setScheduleDateField(event.target.value)}
                  >
                    <option value="">Choose Date/Datetime field</option>
                    {(overview?.fields || [])
                      .filter((item) => ["Date", "Datetime"].includes(item.fieldtype))
                      .map((item) => (
                        <option value={item.fieldname} key={item.fieldname}>{item.label}</option>
                      ))}
                  </select>
                </label>
              )}
              <label className="text-heading text-[10px] font-semibold">
                {frequency === "ONCE" ? "Run at" : "First occurrence"}
                <input
                  className={`${fieldClass} mt-1.5`}
                  type="datetime-local"
                  required
                  value={nextRunAt}
                  onChange={(event) => setNextRunAt(event.target.value)}
                />
              </label>
              {frequency === "MONTHLY" && (
                <>
                  <label className="text-heading text-[10px] font-semibold">
                    Monthly rule
                    <select
                      className={`${fieldClass} mt-1.5`}
                      value={monthlyMode}
                      onChange={(event) =>
                        setMonthlyMode(event.target.value as typeof monthlyMode)
                      }
                    >
                      <option value="DAY">
                        Same calendar day (clamp at month end)
                      </option>
                      <option value="FIRST_WEEKDAY">
                        First selected weekday
                      </option>
                      <option value="LAST_WEEKDAY">
                        Last selected weekday
                      </option>
                    </select>
                  </label>
                  {monthlyMode !== "DAY" && (
                    <label className="text-heading text-[10px] font-semibold">
                      Weekday
                      <select
                        className={`${fieldClass} mt-1.5`}
                        value={monthlyWeekday}
                        onChange={(event) =>
                          setMonthlyWeekday(Number(event.target.value))
                        }
                      >
                        {[
                          "Monday",
                          "Tuesday",
                          "Wednesday",
                          "Thursday",
                          "Friday",
                          "Saturday",
                          "Sunday",
                        ].map((day, index) => (
                          <option value={index} key={day}>
                            {day}
                          </option>
                        ))}
                      </select>
                    </label>
                  )}
                </>
              )}
              <label className="text-heading text-[10px] font-semibold sm:col-span-2 xl:col-span-1">
                Timezone
                <span className="mt-1.5 block">
                  <AsyncCombobox
                    ariaLabel="Schedule timezone"
                    value={timezone}
                    onChange={setTimezone}
                    loadOptions={loadTimezones}
                    placeholder="Search IANA timezones…"
                  />
                </span>
              </label>
              <label className="text-heading text-[10px] font-semibold">
                Version policy
                <select
                  className={`${fieldClass} mt-1.5`}
                  value={versionPolicy}
                  onChange={(event) =>
                    setVersionPolicy(event.target.value as typeof versionPolicy)
                  }
                >
                  <option value="ACTIVE_AT_RUN">
                    Active version at each occurrence
                  </option>
                  <option value="PINNED">Always use one version</option>
                </select>
              </label>
              {versionPolicy === "PINNED" && (
                <label className="text-heading text-[10px] font-semibold">
                  Pinned version
                  <select
                    className={`${fieldClass} mt-1.5`}
                    value={pinnedVersion}
                    onChange={(event) => setPinnedVersion(event.target.value)}
                  >
                    {overview?.versions.map((version) => (
                      <option value={version.name} key={version.name}>
                        Version {version.version_no} · {version.name}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              <label className="text-heading text-[10px] font-semibold">
                Missed occurrence
                <select
                  className={`${fieldClass} mt-1.5`}
                  value={catchUp}
                  onChange={(event) =>
                    setCatchUp(event.target.value as typeof catchUp)
                  }
                >
                  <option value="RUN_ONCE">
                    Run once when service returns
                  </option>
                  <option value="SKIP">Skip missed occurrences</option>
                </select>
              </label>
              <label className="text-heading text-[10px] font-semibold">
                Overlapping job
                <select
                  className={`${fieldClass} mt-1.5`}
                  value={overlap}
                  onChange={(event) =>
                    setOverlap(event.target.value as typeof overlap)
                  }
                >
                  <option value="SKIP">
                    Skip while previous job is active
                  </option>
                  <option value="QUEUE">Queue another occurrence</option>
                </select>
              </label>
            </div>
            <p className="text-muted mt-3 text-[9px] leading-4">
              Nonexistent daylight-saving times are rejected. Repeated fall-back
              times use the earlier occurrence. One-time schedules disable
              themselves after dispatch.
            </p>
            <button
              className={`${primary} mt-5 w-full`}
              disabled={!workflowReady || !timezone || busy === "schedule"}
            >
              {busy === "schedule" ? (
                <LoaderCircle className="animate-spin" size={13} />
              ) : (
                <Plus size={13} />
              )}
              Create disabled schedule
            </button>
          </form>
          <div className="surface-flat overflow-hidden rounded-xl">
            <div className="border-b border-[var(--border-color)] px-5 py-4">
              <div className="flex items-center gap-2">
                <Clock3 className="text-brand-500" size={15} />
                <h2 className="text-heading text-sm font-bold">Schedules</h2>
              </div>
              <p className="text-muted mt-1 text-[10px]">
                New schedules stay disabled until an operator reviews and
                enables them.
              </p>
            </div>
            {schedules.length ? (
              <div className="divide-y divide-[var(--border-color)]">
                {schedules.map((row) => (
                  <article
                    className="grid gap-4 p-4 md:grid-cols-[minmax(0,1fr)_auto] md:items-center"
                    key={row.name}
                  >
                    <div>
                      <div className="flex flex-wrap items-center gap-2">
                        <strong className="text-heading text-xs">
                          {row.frequency.toLowerCase()} · {row.timezone}
                        </strong>
                        <Status value={row.enabled ? "ACTIVE" : "DISABLED"} />
                      </div>
                      <p className="text-muted mt-1 text-[9px]">
                        {row.name} · next {formatDate(row.next_run_at)} ·{" "}
                        {row.version_policy === "PINNED"
                          ? row.workflow_version
                          : "active version at run"}
                      </p>
                      <p className="text-light mt-1 text-[9px]">
                        Catch-up{" "}
                        {row.catch_up_policy.toLowerCase().replace("_", " ")} ·
                        overlap {row.overlap_policy.toLowerCase()} ·{" "}
                        {row.records_per_minute}/min
                        {row.has_history ? " · execution history retained" : ""}
                      </p>
                    </div>
                    <div className="flex gap-1.5">
                      <button
                        className={row.enabled ? secondary : primary}
                        disabled={
                          busy === `schedule:${row.name}` ||
                          (!row.enabled && !canRun)
                        }
                        onClick={() => void toggleSchedule(row)}
                      >
                        {row.enabled ? <Pause size={12} /> : <Play size={12} />}
                        {row.enabled ? "Disable" : "Enable"}
                      </button>
                      {!row.enabled && !row.has_history && (
                        <button
                          className="icon-button text-red-500"
                          disabled={busy === `delete:${row.name}`}
                          onClick={() => deleteSchedule(row)}
                          aria-label={`Delete ${row.name}`}
                        >
                          <Trash2 size={14} />
                        </button>
                      )}
                    </div>
                  </article>
                ))}
              </div>
            ) : (
              <div className="px-6 py-14 text-center">
                <CalendarClock className="text-light mx-auto" size={21} />
                <p className="text-heading mt-3 text-xs font-bold">
                  No schedules configured
                </p>
                <p className="text-muted mt-1 text-[10px]">
                  Create one from the reviewed audience on the left.
                </p>
              </div>
            )}
          </div>
        </section>
      </main>
      {confirmation.dialog}
    </div>
  );
}
