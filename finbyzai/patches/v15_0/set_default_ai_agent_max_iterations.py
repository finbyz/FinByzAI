import frappe


DEFAULT_MAX_ITERATIONS = 25
MAX_TEMPERATURE = 1


def execute() -> None:
	"""Normalize iteration and temperature limits on existing AI Agents."""
	if not frappe.db.table_exists("AI Agent"):
		return

	agent = frappe.qb.DocType("AI Agent")
	(
		frappe.qb.update(agent)
		.set(agent.max_iterations, DEFAULT_MAX_ITERATIONS)
		.where((agent.max_iterations.isnull()) | (agent.max_iterations == 0))
	).run()
	(
		frappe.qb.update(agent)
		.set(agent.temperature, MAX_TEMPERATURE)
		.where(agent.temperature > MAX_TEMPERATURE)
	).run()
