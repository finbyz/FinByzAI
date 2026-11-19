from unittest import TestCase
from unittest.mock import patch

import frappe

from finbyzai.workflow_builder import registry
from finbyzai.workflow_builder.constants import AUTOMATION_ROLES
from finbyzai.workflow_builder.errors import AutomationPermissionError


class AutomationRoleTests(TestCase):
	def _roles(self, *roles):
		return patch.object(frappe, "get_roles", return_value=list(roles))

	def test_viewer_can_read_but_not_build_publish_operate_or_delete(self):
		with self._roles("Automation Viewer"):
			registry.require_viewer()
			for check in (registry.require_builder, registry.require_publisher, registry.require_operator, registry.require_deleter):
				with self.assertRaises(AutomationPermissionError):
					check()

	def test_builder_edits_but_cannot_publish_or_delete(self):
		with self._roles("Automation Builder"):
			registry.require_builder()
			registry.require_viewer()
			for check in (registry.require_publisher, registry.require_deleter):
				with self.assertRaises(AutomationPermissionError):
					check()

	def test_manager_deletes_but_needs_builder_or_publisher_to_edit(self):
		with self._roles("Automation Manager"):
			registry.require_deleter()
			with self.assertRaises(AutomationPermissionError):
				registry.require_builder()
		with self._roles("Automation Manager", "Automation Publisher"):
			registry.require_deleter()
			registry.require_publisher()

	def test_every_role_is_created_on_migrate(self):
		self.assertTrue({"Automation Viewer", "Automation Manager"} <= set().union(*AUTOMATION_ROLES.values()))
