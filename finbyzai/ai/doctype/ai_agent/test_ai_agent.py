# Copyright (c) 2025, Finbyz Tech Pvt Ltd and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase


class TestAIAgent(FrappeTestCase):
	def test_max_iterations_defaults_to_25_when_not_set(self):
		agent = frappe.get_doc(
			{
				"doctype": "AI Agent",
				"title": "Agent without max iterations",
				"agent_type": "Gemini Cache Agent",
			}
		)
		agent.max_iterations = None

		agent.validate()

		self.assertEqual(agent.max_iterations, 25)

	def test_max_iterations_defaults_to_25_when_zero(self):
		agent = frappe.get_doc(
			{
				"doctype": "AI Agent",
				"title": "Agent with zero max iterations",
				"agent_type": "Gemini Cache Agent",
				"max_iterations": 0,
			}
		)

		agent.validate()

		self.assertEqual(agent.max_iterations, 25)

	def test_temperature_is_capped_at_one(self):
		agent = frappe.get_doc(
			{
				"doctype": "AI Agent",
				"title": "Agent with high temperature",
				"agent_type": "Gemini Cache Agent",
				"temperature": 7,
			}
		)

		agent.validate()

		self.assertEqual(agent.temperature, 1)

	def test_zero_temperature_remains_valid(self):
		agent = frappe.get_doc(
			{
				"doctype": "AI Agent",
				"title": "Agent with zero temperature",
				"agent_type": "Gemini Cache Agent",
				"temperature": 0,
			}
		)

		agent.validate()

		self.assertEqual(agent.temperature, 0)
