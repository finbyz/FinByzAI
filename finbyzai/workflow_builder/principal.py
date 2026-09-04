from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

import frappe
from frappe import _

from .errors import AutomationError, AutomationPermissionError


@dataclass(frozen=True, slots=True)
class AutomationPrincipal:
	"""The explicitly authorised identity for one workflow execution scope.

	This is deliberately independent of ``frappe.session.user``. Changing the
	Frappe session in a worker mutates request-local caches and lets unrelated
	code accidentally inherit the impersonated identity. Runtime code must ask
	for this principal and pass its user to every permission decision instead.
	"""

	user: str
	context: Mapping[str, Any]


_CURRENT_PRINCIPAL: ContextVar[AutomationPrincipal | None] = ContextVar(
	"finbyzai_automation_principal", default=None
)


def _assert_worker_execution() -> None:
	if not frappe.in_test and not getattr(frappe.local, "job", None):
		raise AutomationError(_("Automation actions can only execute inside an isolated background worker."))


def is_valid_execution_user(user: str) -> bool:
	user = str(user or "").strip()
	row = frappe.db.get_value("User", user, ["enabled", "user_type"], as_dict=True)
	return bool(row and row.enabled and row.user_type == "System User")


def _principal(user: str, automation_context: Mapping[str, Any] | None) -> AutomationPrincipal:
	user = str(user or "").strip()
	if not user:
		raise AutomationPermissionError(_("Workflow execution user is disabled, missing, or not a System User."))
	return AutomationPrincipal(user=user, context=MappingProxyType(dict(automation_context or {})))
									

@contextmanager
def _principal_scope(principal: AutomationPrincipal) -> Iterator[AutomationPrincipal]:
	token = _CURRENT_PRINCIPAL.set(principal)
	try:
		yield principal
	finally:
		_CURRENT_PRINCIPAL.reset(token)


@contextmanager
def execution_principal(user: str, automation_context: Mapping[str, Any] | None = None) -> Iterator[AutomationPrincipal]:
	"""Install a task-local runtime principal without mutating the Frappe session."""

	_assert_worker_execution()
	# Runtime entry points validate the pinned user before this scope is entered.
	# Keeping installation database-free avoids opening a repeatable-read snapshot
	# before concurrency primitives such as the round-robin cursor upsert.
	with _principal_scope(_principal(user, automation_context)) as principal:
		yield principal


@contextmanager
def preview_principal(user: str, automation_context: Mapping[str, Any] | None = None) -> Iterator[AutomationPrincipal]:
	"""Install a principal for an authorised, non-mutating preview request.

	This skips the worker assertion and must never wrap business-record writes.
	The workflow AI test endpoint checks caller and execution-user read access
	before entering this scope.
	"""

	if not is_valid_execution_user(user):
		raise AutomationPermissionError(_("Workflow execution user is disabled, missing, or not a System User."))
	with _principal_scope(_principal(user, automation_context)) as principal:
		yield principal


def current_principal(*, required: bool = True) -> AutomationPrincipal | None:
	principal = _CURRENT_PRINCIPAL.get()
	if required and principal is None:
		raise AutomationError(_("This operation requires an explicit workflow execution principal."))
	return principal


def current_execution_user(*, required: bool = True) -> str | None:
	principal = current_principal(required=required)
	return principal.user if principal else None


def current_automation_context() -> Mapping[str, Any]:
	principal = current_principal(required=False)
	return principal.context if principal else MappingProxyType({})


def has_execution_permission(doc, ptype: str) -> bool:
	user = current_execution_user()
	return bool(frappe.has_permission(doc.doctype, ptype=ptype, doc=doc, user=user))


def check_execution_permission(doc, ptype: str) -> None:
	if not has_execution_permission(doc, ptype):
		raise AutomationPermissionError(
			_("Workflow execution user {0} cannot {1} {2} {3}.").format(
				current_execution_user(), ptype, doc.doctype, doc.name
			)
		)
