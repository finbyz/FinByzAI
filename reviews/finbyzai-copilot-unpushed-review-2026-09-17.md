# FinByzAI Copilot Change Review

Date: 2026-09-17

## Scope

The review uses `upstream/main` at `c17d1df` as the baseline.

The local `copilot` branch has no matching remote branch. It contains 27 local commits beyond `upstream/main`.

The worktree had 226 unstaged tracked files and one untracked public HTML file. The index had no staged files.

## Findings

### 1. Critical: The Python requirements cannot resolve

Location: [`pyproject.toml:7`](../pyproject.toml#L7)

The unstaged change sets `requires-python` to `>=3.10,<3.13`.

The same file requires Frappe `>=16.0.0,<17.0.0`. The checked-out Frappe v16 package requires Python `>=3.14,<3.15`.

These ranges have no common Python version. A package resolver cannot install both packages.

Fix: Align the FinByzAI range with the Frappe v16 range.

Revalidation: Confirmed from both `pyproject.toml` files on 2026-09-17.

### 2. Critical: The repository contains an absolute `node_modules` symlink

Location: [`frontend/node_modules`](../frontend/node_modules)

Git stores this path as a symbolic link. Its target is `/home/sandeep/benches/nayla_innovation/apps/flow/node_modules`.

The link depends on one workstation and another app. A fresh checkout cannot use this path reliably.

The link can also make the build use dependencies from `flow` instead of FinByzAI.

Fix: Remove the link from Git. Install dependencies from `frontend/yarn.lock` in each environment.

Revalidation: `git ls-tree` reports mode `120000`. `readlink` reports the absolute target above.

### 3. Critical: Opening a conversation can corrupt an active run

Locations:

- [`finbyzai/copilot/api.py:199`](../finbyzai/copilot/api.py#L199)
- [`frontend/src/store.js:244`](../frontend/src/store.js#L244)
- [`finbyzai/copilot/runner.py:731`](../finbyzai/copilot/runner.py#L731)

The frontend calls `recover_conversation` whenever it switches to a conversation.

The endpoint marks every `Running` run as `Failed`. It does not check the run age, heartbeat, or RQ job state.

The original worker only stops when the database status is `Stopped`. It continues after the endpoint writes `Failed`.

The failed status also lets a new run pass `_block_while_running`. Both workers can then write to one conversation.

This can interleave assistant and tool messages. It can also overwrite the failed status with `Completed`.

Fix: Recover only a verified stale job. Use a heartbeat or check the RQ job state and timeout.

Revalidation: Confirmed through the frontend call, endpoint filter, worker stop check, and completion update.

### 4. Critical security issue: Count and existence tools bypass row permissions

Locations:

- [`finbyzai/copilot/tools/data.py:187`](../finbyzai/copilot/tools/data.py#L187)
- [`finbyzai/copilot/tools/data.py:337`](../finbyzai/copilot/tools/data.py#L337)
- [`finbyzai/copilot/sandbox.py:166`](../finbyzai/copilot/sandbox.py#L166)

The code checks DocType permission and then calls `frappe.db.count()` or `frappe.db.exists()`.

These database methods do not apply row-level permissions. `exists()` calls `get_value(..., ignore=True)`.

The count method builds a query without permission filtering. It exposes counts for records the user cannot read.

The sandbox also exposes both unsafe helpers as permission-respecting functions.

Fix: Use permission-aware queries with `ignore_permissions=False`. Apply the same rule to scope draft counts.

Revalidation: Confirmed against the checked-out Frappe implementations in `frappe/database/database.py`.

### 5. High: Every migration can re-enable Copilot

Location: [`finbyzai/copilot/setup.py:63`](../finbyzai/copilot/setup.py#L63)

The `after_migrate` setup sets `enabled` to `1` whenever the saved value is false.

An administrator cannot keep Copilot disabled across a migration.

Fix: Set the default only when the settings record is first created. Preserve later administrator choices.

Revalidation: Confirmed in `_ensure_settings()` and the `after_migrate` hook.

### 6. High: Concurrent requests can create two active runs

Locations:

- [`finbyzai/copilot/api.py:44`](../finbyzai/copilot/api.py#L44)
- [`finbyzai/copilot/api.py:625`](../finbyzai/copilot/api.py#L625)
- [`finbyzai/copilot/doctype/copilot_run/copilot_run.json:21`](../finbyzai/copilot/doctype/copilot_run/copilot_run.json#L21)

`start_run` checks for an active run before it inserts the new run.

Two requests can pass the check before either request inserts. The schema has no constraint or lock for this rule.

Both workers can then append messages to the same conversation.

Fix: Lock the conversation during the check and insert. A database-backed or Redis-backed claim can also enforce the rule.

Revalidation: Confirmed from the request sequence and the Copilot Run schema.

### 7. Critical security issue: The public wireframe has a DOM XSS path

Locations:

- [`finbyzai/public/store_checklist_wireframe.html:1954`](../finbyzai/public/store_checklist_wireframe.html#L1954)
- [`finbyzai/public/store_checklist_wireframe.html:2139`](../finbyzai/public/store_checklist_wireframe.html#L2139)

The form accepts a task title and stores it without validation.

The renderer inserts that title into `innerHTML`. HTML event attributes can execute in the ERP origin.

The page stores the value in `localStorage`, so the payload runs again after a reload.

Fix: Create text nodes or set `textContent`. Do not interpolate user values into HTML strings.

Revalidation: Confirmed by tracing `submitAddTask()` to `renderMyAddedTasks()`.

### 8. High: The worktree contains repository-wide executable-bit changes

The unstaged diff changes 226 tracked paths from mode `100644` to `100755`.

Only `pyproject.toml` has a tracked text change. The images show binary mode changes only.

This noise hides meaningful changes and marks documents, source files, images, and lockfiles as executable.

Fix: Restore the original modes before any commit. Keep only intentional executable files at mode `100755`.

Revalidation: `git diff --summary` reports 226 mode changes. The index remains empty.

## Test and coverage result

`node test/render.mjs` passed all frontend checks against the committed Copilot bundle.

The test does not cover the backend permission, migration, recovery, or concurrent-start paths above.

No Copilot backend test files exist in this change set. Add regression tests for findings 3 through 6.

## Review result

Do not push or merge this branch until the critical findings are fixed.

Remove the mode-only changes before preparing the final commit series.
