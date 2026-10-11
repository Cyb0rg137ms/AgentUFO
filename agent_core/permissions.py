"""Least-privilege filesystem and MCP capability policies.

This is an application-layer authorization guard, not an OS security boundary.
Use OS permissions and an isolated runtime for defense in depth.
"""
from __future__ import annotations
import fnmatch
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

class PermissionDenied(PermissionError):
    pass

@dataclass(frozen=True)
class WorkspacePolicy:
    root: Path
    read_globs: tuple[str, ...] = ("**", "*")
    write_globs: tuple[str, ...] = ("**", "*")
    deny_globs: tuple[str, ...] = (".env", "**/.env", "**/.env.*", "**/.git/**", "**/id_rsa*", "**/*.pem", "**/*.key")
    allow_delete: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", Path(self.root).resolve())

    def resolve(self, path: str | Path, operation: str = "read") -> Path:
        raw = Path(path)
        if any(part == ".." for part in raw.parts):
            raise PermissionDenied("Path traversal is denied")
        candidate = raw if raw.is_absolute() else self.root / raw
        target = candidate.resolve(strict=False)
        if not target.is_relative_to(self.root) or target == self.root:
            raise PermissionDenied("Path is outside the authorized workspace")
        rel = target.relative_to(self.root).as_posix()
        if any(fnmatch.fnmatch(rel, pat) for pat in self.deny_globs):
            raise PermissionDenied("Path matches a protected-file policy")
        patterns = self.read_globs if operation == "read" else self.write_globs
        if operation == "delete" and not self.allow_delete:
            raise PermissionDenied("Delete operations require explicit policy approval")
        if operation not in {"read", "write", "delete"}:
            raise PermissionDenied(f"Unknown filesystem operation: {operation}")
        if not any(fnmatch.fnmatch(rel, pat) for pat in patterns):
            raise PermissionDenied(f"{operation.title()} access is not granted for {rel}")
        return target

@dataclass
class MCPToolPolicy:
    """Default-deny registry for tools that may read, mutate, or access networks."""
    allowed_tools: set[str] = field(default_factory=set)
    approval_required: set[str] = field(default_factory=lambda: {"write_file", "delete_file", "shell", "network", "execute_code"})
    denied_tools: set[str] = field(default_factory=set)

    def authorize(self, tool_name: str, *, approved: bool = False) -> None:
        # For qualified outbound names (server:tool), destructive-operation rules
        # apply to the tool suffix as well as any fully qualified policy entry.
        base_name = tool_name.rsplit(":", 1)[-1]
        if tool_name in self.denied_tools or base_name in self.denied_tools:
            raise PermissionDenied(f"MCP tool {tool_name!r} is explicitly denied")
        if tool_name not in self.allowed_tools:
            raise PermissionDenied(f"MCP tool {tool_name!r} is not allowlisted")
        if (tool_name in self.approval_required or base_name in self.approval_required) and not approved:
            raise PermissionDenied(f"MCP tool {tool_name!r} requires explicit approval")


class AuthorizedMCPDispatcher:
    """Execute registered MCP-like handlers only after policy authorization.

    This helper is an enforcement point for integrations; callers should route every
    invocation through ``call`` instead of invoking handler functions directly.
    """
    def __init__(self, policy: MCPToolPolicy | None = None) -> None:
        self.policy = policy or MCPToolPolicy()
        self._handlers: dict[str, object] = {}

    def register(self, name: str, handler) -> None:
        if not name or not callable(handler):
            raise ValueError("A non-empty tool name and callable handler are required")
        if name in self._handlers:
            raise ValueError(f"Tool already registered: {name}")
        self._handlers[name] = handler

    def call(self, name: str, arguments: dict, *, approved: bool = False):
        self.policy.authorize(name, approved=approved)
        handler = self._handlers.get(name)
        if handler is None:
            raise KeyError(f"No handler registered for authorized tool {name!r}")
        if not isinstance(arguments, dict):
            raise TypeError("MCP tool arguments must be an object")
        return handler(**arguments)


class AuthorizedMCPClient:
    """Policy-enforcing adapter for outbound MCP client integrations.

    The supplied ``call_tool`` callback must be the actual MCP client's tool-call
    method. Every invocation is authorized using the fully qualified ``server:tool``
    key before the network call is made. The adapter is intentionally fail-closed;
    do not expose the underlying client directly to agent code.
    """
    def __init__(self, call_tool, policy: MCPToolPolicy | None = None) -> None:
        if not callable(call_tool):
            raise TypeError("call_tool must be a callable MCP client operation")
        self._call_tool = call_tool
        self.policy = policy or MCPToolPolicy()

    def call(self, server_name: str, tool_name: str, arguments: dict, *, approved: bool = False):
        if not server_name or not tool_name or not isinstance(arguments, dict):
            raise ValueError("server_name, tool_name, and object arguments are required")
        qualified_name = f"{server_name}:{tool_name}"
        self.policy.authorize(qualified_name, approved=approved)
        # Authorization precedes dispatch; exceptions from the MCP transport propagate.
        return self._call_tool(server_name, tool_name, arguments)
