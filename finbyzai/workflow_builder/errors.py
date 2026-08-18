from __future__ import annotations

import frappe


class AutomationError(frappe.ValidationError):
	http_status_code = 422
	code = "WF_VALIDATION_ERROR"
	retryable = False

	def __init__(self, message, *, path=None, trace_id=None):
		super().__init__(message)
		self.path = path
		self.trace_id = trace_id or frappe.generate_hash(length=20)
		if getattr(frappe.local, "response", None) is not None:
			frappe.local.response["workflow_error"] = self.as_dict()

	def as_dict(self):
		return {
			"code": self.code,
			"status": self.http_status_code,
			"retryable": self.retryable,
			"path": self.path,
			"explanation": str(self),
			"trace_id": self.trace_id,
		}


class AutomationConflictError(AutomationError):
	http_status_code = 409
	code = "WF_CONFLICT"


class AutomationPermissionError(frappe.PermissionError):
	code = "WF_PERMISSION_DENIED"
	retryable = False

	def __init__(self, message, *, path=None, trace_id=None):
		super().__init__(message)
		self.path = path
		self.trace_id = trace_id or frappe.generate_hash(length=20)
		if getattr(frappe.local, "response", None) is not None:
			frappe.local.response["workflow_error"] = {
				"code": self.code,
				"status": self.http_status_code,
				"retryable": self.retryable,
				"path": self.path,
				"explanation": str(self),
				"trace_id": self.trace_id,
			}


class AutomationCancelledError(AutomationError):
	http_status_code = 409
	code = "WF_CANCELLED"


class AutomationTransientError(AutomationError):
	http_status_code = 503
	code = "WF_TRANSIENT"
	retryable = True
