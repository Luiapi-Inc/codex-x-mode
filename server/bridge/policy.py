"""Gateway tool visibility and server-side admission policy (never client-supplied)."""
from .core import Fault


def permitted_tool_names(config, tools, *, catalog=None):
    """Return exact allowed names, or fail closed for an invalid configured policy.

    Legacy configs without the v1 field preserve their existing capabilities.
    New v1 setups use {"mode": "read-only"} until an operator explicitly grants tools.
    """
    available = {item["name"]: item for item in tools}
    full_catalog = {tool["name"] for tool in (catalog if catalog is not None else tools)}
    if "mcp_policy" not in config:
        if config.get("config_schema_version") == 1:
            raise Fault(503, "v1 MCP tool policy is required")
        return frozenset(available)
    policy = config["mcp_policy"]
    if not isinstance(policy, dict):
        raise Fault(503, "Invalid MCP tool policy")
    mode = policy.get("mode")
    if mode == "read-only" and set(policy) == {"mode"}:
        return frozenset(
            name for name, item in available.items()
            if item.get("annotations", {}).get("readOnlyHint") is True
        )
    if mode == "explicit" and set(policy) == {"mode", "allowed_tools"}:
        allowed = policy["allowed_tools"]
        if (
            not isinstance(allowed, list)
            or not all(isinstance(name, str) and name in full_catalog for name in allowed)
            or len(set(allowed)) != len(allowed)
        ):
            raise Fault(503, "Invalid MCP tool policy")
        return frozenset(name for name in available if name in allowed)
    raise Fault(503, "Invalid MCP tool policy")
