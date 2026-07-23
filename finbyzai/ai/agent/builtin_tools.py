"""Compile declarative provider built-in tools into LiteLLM request arguments."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import json
import re
from typing import Any, Callable

import frappe
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError


FUNCTION_TOOL = "Function"
PROVIDER_BUILTIN_TOOL = "Provider Built-in"
PROVIDER_NATIVE_STRATEGY = "Provider Native Tool"

_EXACT_PLACEHOLDER = re.compile(
    r"^\s*{{\s*(config|agent|model|provider)(?:\.([A-Za-z0-9_.-]+))?\s*}}\s*$"
)
_PLACEHOLDER = re.compile(
    r"{{\s*(config|agent|model|provider)(?:\.([A-Za-z0-9_.-]+))?\s*}}"
)


@dataclass
class CompiledBuiltinTools:
    """Provider arguments and metadata compiled from an agent's selected tools."""

    model_kwargs: dict[str, Any] = field(default_factory=dict)
    provider_tools: list[dict[str, Any]] = field(default_factory=list)
    response_mappings: list[dict[str, Any]] = field(default_factory=list)
    tool_keys: list[str] = field(default_factory=list)

    def as_model_kwargs(self) -> dict[str, Any]:
        result = deepcopy(self.model_kwargs)
        if self.provider_tools:
            if "tools" in result:
                raise ValueError("Compiled built-in tool arguments contain duplicate tools")
            result["tools"] = deepcopy(self.provider_tools)
        return result


class BuiltinToolCompiler:
    """Resolve AI Tool provider mappings without provider-specific Python branches."""

    def __init__(
        self,
        agent_doc,
        llm_doc,
        *,
        tool_loader: Callable[[str], Any] | None = None,
        has_function_tools: bool = False,
    ):
        self.agent_doc = agent_doc
        self.llm_doc = llm_doc
        self.provider = getattr(llm_doc, "provider", None)
        self.model = getattr(llm_doc, "name", None)
        self.tool_loader = tool_loader or self._load_tool
        self.has_function_tools = has_function_tools

    @staticmethod
    def _load_tool(name: str):
        return frappe.get_doc("AI Tool", name)

    def compile(self) -> CompiledBuiltinTools:
        compiled = CompiledBuiltinTools()
        selected = []

        for row in getattr(self.agent_doc, "tools", None) or []:
            if not _is_enabled(getattr(row, "enabled", 1)):
                continue
            tool_doc = self.tool_loader(row.tool)
            tool_type = getattr(tool_doc, "tool_type", None) or FUNCTION_TOOL
            if tool_type != PROVIDER_BUILTIN_TOOL:
                continue
            if not _is_enabled(getattr(tool_doc, "enabled", 1)):
                _fail(f"AI Tool {tool_doc.name} is disabled")
            selected.append((row, tool_doc))

        selected_keys = {tool.tool_key for _, tool in selected}
        for row, tool_doc in selected:
            config = _json_object(getattr(row, "configuration", None), "Configuration")
            schema = _json_object(
                getattr(tool_doc, "configuration_schema", None),
                f"Configuration Schema for {tool_doc.name}",
            )
            config = _apply_defaults(config, schema)
            _validate_config(tool_doc.name, config, schema)

            mapping = self._find_mapping(tool_doc)
            rules = _json_object(
                getattr(mapping, "compatibility_rules", None),
                f"Compatibility Rules for {tool_doc.name}",
            )
            self._validate_compatibility(tool_doc, mapping, rules, selected_keys)

            template = _json_object(
                mapping.request_template,
                f"Request Template for {tool_doc.name}",
            )
            context = {
                "config": config,
                "agent": self.agent_doc,
                "model": self.llm_doc,
                "provider": self.provider,
            }
            rendered = _render(template, context)

            if mapping.transport_strategy == PROVIDER_NATIVE_STRATEGY:
                compiled.provider_tools.append(rendered)
            else:
                _deep_merge(compiled.model_kwargs, rendered, tool_doc.tool_key)

            response_mapping = _json_object(
                getattr(mapping, "response_mapping", None),
                f"Response Mapping for {tool_doc.name}",
            )
            if response_mapping:
                compiled.response_mappings.append(
                    {"tool_key": tool_doc.tool_key, **response_mapping}
                )
            compiled.tool_keys.append(tool_doc.tool_key)

        return compiled

    def _find_mapping(self, tool_doc):
        provider_mappings = getattr(tool_doc, "provider_mappings", None) or []
        provider_matches = []
        for mapping in provider_mappings:
            if not _is_enabled(getattr(mapping, "enabled", 1)):
                continue
            if mapping.provider != self.provider:
                continue
            provider_matches.append(mapping)
            if not mapping.model_pattern or re.search(mapping.model_pattern, self.model or ""):
                return mapping

        if provider_matches:
            _fail(
                f"AI Tool {tool_doc.name} does not support model {self.model}. "
                f"Update its provider mapping or select another model."
            )
        _fail(f"AI Tool {tool_doc.name} has no enabled mapping for provider {self.provider}")

    def _validate_compatibility(self, tool_doc, mapping, rules, selected_keys):
        allowed_agent_types = rules.get("allowed_agent_types") or []
        if allowed_agent_types and self.agent_doc.agent_type not in allowed_agent_types:
            _fail(
                f"AI Tool {tool_doc.name} is not available for agent type "
                f"{self.agent_doc.agent_type}"
            )

        incompatible = set(rules.get("incompatible_with") or []) & selected_keys
        incompatible.discard(tool_doc.tool_key)
        if incompatible:
            _fail(
                f"AI Tool {tool_doc.name} cannot be combined with: "
                f"{', '.join(sorted(incompatible))}"
            )

        if rules.get("disallow_with_output_schema") and getattr(
            self.agent_doc, "output_schema", None
        ):
            _fail(f"AI Tool {tool_doc.name} cannot be used with structured output")

        if mapping.transport_strategy == PROVIDER_NATIVE_STRATEGY and self.has_function_tools:
            _fail(
                f"AI Tool {tool_doc.name} uses Provider Native Tool transport, which "
                "cannot currently be combined with application function tools. Use a "
                "LiteLLM parameter mapping or remove the function tools."
            )


def _is_enabled(value: Any) -> bool:
    return value not in (0, "0", False)


def _json_object(value: Any, label: str) -> dict[str, Any]:
    if not value:
        return {}
    if isinstance(value, dict):
        return deepcopy(value)
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        _fail(f"{label} must be valid JSON: {exc}")
    if not isinstance(parsed, dict):
        _fail(f"{label} must be a JSON object")
    return parsed


def _apply_defaults(config: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(config)
    for key, property_schema in (schema.get("properties") or {}).items():
        if key not in result and "default" in property_schema:
            result[key] = deepcopy(property_schema["default"])
        if key in result and isinstance(result[key], dict):
            result[key] = _apply_defaults(result[key], property_schema)
    return result


def _validate_config(tool_name: str, config: dict[str, Any], schema: dict[str, Any]):
    if not schema:
        return
    try:
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(config)
    except SchemaError as exc:
        _fail(f"AI Tool {tool_name} has an invalid Configuration Schema: {exc.message}")
    except ValidationError as exc:
        path = ".".join(str(part) for part in exc.absolute_path)
        location = f" at {path}" if path else ""
        _fail(f"Invalid configuration for AI Tool {tool_name}{location}: {exc.message}")


def _render(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {key: _render(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [_render(item, context) for item in value]
    if not isinstance(value, str):
        return value

    exact = _EXACT_PLACEHOLDER.match(value)
    if exact:
        return deepcopy(_resolve(exact.group(1), exact.group(2), context))

    def replace(match):
        resolved = _resolve(match.group(1), match.group(2), context)
        if isinstance(resolved, (dict, list)):
            return json.dumps(resolved, separators=(",", ":"))
        return str(resolved)

    return _PLACEHOLDER.sub(replace, value)


def _resolve(root: str, path: str | None, context: dict[str, Any]) -> Any:
    current = context[root]
    if not path:
        return current
    for part in path.split("."):
        if isinstance(current, dict):
            if part not in current:
                _fail(f"Unknown template value: {root}.{path}")
            current = current[part]
        elif hasattr(current, part) and not part.startswith("_"):
            current = getattr(current, part)
        else:
            _fail(f"Unknown template value: {root}.{path}")
    return current


def _deep_merge(target: dict[str, Any], source: dict[str, Any], tool_key: str, path=""):
    for key, value in source.items():
        current_path = f"{path}.{key}" if path else key
        if key not in target:
            target[key] = deepcopy(value)
        elif isinstance(target[key], dict) and isinstance(value, dict):
            _deep_merge(target[key], value, tool_key, current_path)
        elif target[key] != value:
            _fail(
                f"AI Tool {tool_key} conflicts with another built-in tool at "
                f"request parameter {current_path}"
            )


def _fail(message: str):
    frappe.throw(message)

