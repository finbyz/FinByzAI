# Copyright (c) 2026, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

"""Copilot AI Tool entry points.

Each file in this package is one AI Tool record: the file name matches the
tool name (snake-cased), and the callable exported as ``{name}_tool`` is
what the AI Tool controller imports via ``get_tool_path()``.

Every tool also registers itself with ``finbyzai.copilot.registry`` via the
``@tool`` decorator so the copilot runner can call it through the guard,
look up ``confirm``/``asks`` metadata, and bind it to the LLM schema.
"""
