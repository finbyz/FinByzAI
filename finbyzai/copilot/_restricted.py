"""RestrictedPython pieces the copilot sandbox needs that Frappe v15 does not expose.

v16's `frappe.utils.safe_exec` publishes `SAFE_DATA_UTILS`, `SAFE_EXCEPTIONS` and
`_compile_code`, and ships `frappe.utils.inplacevar.protected_inplacevar`. On v15 none
of those exist — the same values are built inline inside `get_safe_globals()`, and
augmented assignment is not guarded at all. This module reconstructs them from the v15
primitives so `sandbox.py` reads the same on both branches.
"""

import inspect

import frappe
import frappe.exceptions
import frappe.utils.data
from RestrictedPython import compile_restricted
from frappe.utils.safe_exec import VALID_UTILS, FrappeTransformer

# The date/number/string helpers Frappe considers safe to hand to a server script.
# v15 filters `frappe.utils.data` through VALID_UTILS in `add_data_utils()`; same set.
SAFE_DATA_UTILS = {
	name: value for name, value in frappe.utils.data.__dict__.items() if name in VALID_UTILS
}

# Every exception class on `frappe.exceptions`, so sandboxed code can catch
# frappe.ValidationError and friends. v15 does this via `add_module_properties()`.
SAFE_EXCEPTIONS = {
	name: value
	for name, value in frappe.exceptions.__dict__.items()
	if not name.startswith("_") and inspect.isclass(value) and issubclass(value, Exception)
}


def compile_code(code: str, filename: str):
	"""Compile under RestrictedPython with Frappe's own AST policy.

	v15 compiles inline inside `safe_exec()`; this is that call, lifted out.
	"""
	return compile_restricted(code, filename=filename, policy=FrappeTransformer)


# ── augmented assignment guard ────────────────────────────────────────────────
# `x += 1` compiles to `_inplacevar_('+=', x, 1)`. Without a guard, an in-place slot on
# an attacker-reachable object would run arbitrary code, so only list and set — whose
# in-place slots are plain mutation — may use theirs. Everything else falls back to the
# binary operator, which cannot mutate in place. This mirrors Zope's
# AccessControl.ZopeGuards.protected_inplacevar, which is what Frappe v16 vendored.

_VALID_INPLACE_TYPES = (list, set)

_INPLACE_SLOTS = {
	"+=": "__iadd__",
	"-=": "__isub__",
	"*=": "__imul__",
	"/=": "__itruediv__",
	"//=": "__ifloordiv__",
	"%=": "__imod__",
	"**=": "__ipow__",
	">>=": "__irshift__",
	"<<=": "__ilshift__",
	"&=": "__iand__",
	"^=": "__ixor__",
	"|=": "__ior__",
	"@=": "__imatmul__",
}

_INPLACE_OPS = {
	"+=": lambda x, y: x + y,
	"-=": lambda x, y: x - y,
	"*=": lambda x, y: x * y,
	"/=": lambda x, y: x / y,
	"//=": lambda x, y: x // y,
	"%=": lambda x, y: x % y,
	"**=": lambda x, y: x**y,
	">>=": lambda x, y: x >> y,
	"<<=": lambda x, y: x << y,
	"&=": lambda x, y: x & y,
	"^=": lambda x, y: x ^ y,
	"|=": lambda x, y: x | y,
	"@=": lambda x, y: x @ y,
}


def protected_inplacevar(op: str, var, expr):
	"""Apply an augmented assignment, refusing in-place slots outside list/set."""
	slot = _INPLACE_SLOTS.get(op)
	if slot is None:
		raise SyntaxError(f"Augmented assignment {op} is not allowed in untrusted code")

	if hasattr(var, slot) and not isinstance(var, _VALID_INPLACE_TYPES):
		cls = getattr(var, "__class__", None) or type(var)
		raise TypeError(
			f"Augmented assignment to {cls.__name__} objects is not allowed in untrusted code"
		)

	return _INPLACE_OPS[op](var, expr)
