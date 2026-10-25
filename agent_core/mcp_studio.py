"""
mcp_studio.py
=============
Model Context Protocol (MCP) Server Developer Studio & Sandbox Tester.

Enables users to visually design, scaffold, test, and export their own
custom MCP servers compatible with Claude Desktop, Cursor, Antigravity, and FastMCP.
Provides JSON-RPC 2.0 protocol testing within the isolated execution sandbox.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from agent_core.sandbox import CodeExecutionSandbox


@dataclass
class MCPToolParameter:
    name: str
    param_type: str  # "string", "number", "boolean", "array", "object"
    description: str
    required: bool = True
    default: Optional[Any] = None

    def to_schema_prop(self) -> Dict[str, Any]:
        prop: Dict[str, Any] = {
            "type": self.param_type,
            "description": self.description,
        }
        if self.default is not None:
            prop["default"] = self.default
        return prop


@dataclass
class MCPToolDefinition:
    name: str
    description: str
    parameters: List[MCPToolParameter] = field(default_factory=list)
    handler_code: str = ""  # Python function body

    def to_mcp_tool_schema(self) -> Dict[str, Any]:
        """Generates standard Anthropic / MCP JSON Schema for tools/list."""
        properties: Dict[str, Any] = {}
        required: List[str] = []
        for p in self.parameters:
            properties[p.name] = p.to_schema_prop()
            if p.required:
                required.append(p.name)

        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        }


@dataclass
class MCPResourceDefinition:
    uri: str
    name: str
    description: str
    mime_type: str = "application/json"
    generator_code: str = ""

    def to_mcp_resource_schema(self) -> Dict[str, Any]:
        return {
            "uri": self.uri,
            "name": self.name,
            "description": self.description,
            "mimeType": self.mime_type,
        }


@dataclass
class MCPServerDefinition:
    server_name: str
    version: str = "1.0.0"
    description: str = "Custom MCP Server created with Agent-IDE Studio"
    tools: List[MCPToolDefinition] = field(default_factory=list)
    resources: List[MCPResourceDefinition] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "server_name": self.server_name,
            "version": self.version,
            "description": self.description,
            "tools": [
                {
                    "name": t.name,
                    "description": t.description,
                    "parameters": [asdict(p) for p in t.parameters],
                    "handler_code": t.handler_code,
                    "schema": t.to_mcp_tool_schema(),
                }
                for t in self.tools
            ],
            "resources": [
                {
                    "uri": r.uri,
                    "name": r.name,
                    "description": r.description,
                    "mime_type": r.mime_type,
                    "generator_code": r.generator_code,
                    "schema": r.to_mcp_resource_schema(),
                }
                for r in self.resources
            ],
        }


class MCPServerScaffolder:
    """
    Generates production-grade standalone Python FastMCP server code
    and Claude Desktop / Antigravity integration configuration.
    """

    @staticmethod
    def generate_python_server(server_def: MCPServerDefinition) -> str:
        """Produces a standalone FastMCP Python script."""
        import keyword

        for tool in server_def.tools:
            if not tool.name.isidentifier() or keyword.iskeyword(tool.name):
                raise ValueError(f"Invalid MCP tool name: {tool.name!r}")
            for parameter in tool.parameters:
                if not parameter.name.isidentifier() or keyword.iskeyword(parameter.name):
                    raise ValueError(f"Invalid MCP parameter name: {parameter.name!r}")
        lines = [
            '"""Generated MCP server by AgentUFO."""',
            f"# Server: {server_def.server_name!r} v{server_def.version!r}",
            f"# Description: {server_def.description!r}",
            "",
            "from mcp.server.fastmcp import FastMCP",
            "import json",
            "import sys",
            "",
            f"mcp = FastMCP({json.dumps(server_def.server_name)})",
            "",
        ]

        # Add tools
        for tool in server_def.tools:
            docstring = f"    {json.dumps(tool.description)}"
            params_str = ", ".join(
                [f"{p.name}: {MCPServerScaffolder._py_type(p.param_type)}" for p in tool.parameters]
            )

            lines.append("@mcp.tool()")
            lines.append(f"def {tool.name}({params_str}) -> str:")
            lines.append(docstring)
            if tool.handler_code.strip():
                for hline in tool.handler_code.strip().split("\n"):
                    lines.append(f"    {hline}")
            else:
                lines.append(
                    f'    return json.dumps({{"status": "success", "result": f"Executed {tool.name}"}})'
                )
            lines.append("")

        # Add resources
        for res in server_def.resources:
            lines.append(f"@mcp.resource({json.dumps(res.uri)})")
            lines.append(f"def get_{res.name.lower().replace(' ', '_')}() -> str:")
            lines.append(f"    {json.dumps(res.description)}")
            if res.generator_code.strip():
                for gline in res.generator_code.strip().split("\n"):
                    lines.append(f"    {gline}")
            else:
                lines.append(
                    f'    return json.dumps({{"uri": {json.dumps(res.uri)}, "name": {json.dumps(res.name)}}})'
                )
            lines.append("")

        lines.extend(
            [
                'if __name__ == "__main__":',
                '    print(f"Starting MCP Server {mcp.name} on stdio transport...", file=sys.stderr)',
                '    mcp.run(transport="stdio")',
                "",
            ]
        )

        return "\n".join(lines)

    @staticmethod
    def generate_claude_desktop_config(
        server_def: MCPServerDefinition, script_path: str = "server.py"
    ) -> str:
        """Generates Claude Desktop configuration snippet."""
        cfg = {"mcpServers": {server_def.server_name: {"command": "python", "args": [script_path]}}}
        return json.dumps(cfg, indent=2)

    @staticmethod
    def _py_type(param_type: str) -> str:
        mapping = {
            "string": "str",
            "number": "float",
            "integer": "int",
            "boolean": "bool",
            "array": "list",
            "object": "dict",
        }
        return mapping.get(param_type.lower(), "Any")


class MCPSandboxTester:
    """
    Exercises JSON-RPC 2.0 tool contracts inside the execution sandbox
    to test custom user-written MCP handlers safely with isolated stdout/stderr.
    """

    def __init__(
        self, sandbox: Optional[CodeExecutionSandbox] = None, *, allow_unsafe_host: bool = False
    ) -> None:
        # Host execution is available only through an explicit development opt-in.
        self.sandbox = sandbox or CodeExecutionSandbox(
            timeout_seconds=5.0, mode="host" if allow_unsafe_host else "container"
        )

    def test_tool_execution(
        self, tool: MCPToolDefinition, arguments: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Executes the tool's handler code in Python sandbox with simulated JSON-RPC inputs.
        """
        t0 = time.perf_counter()
        arg_json = json.dumps(arguments)

        import keyword

        valid_params = [
            p.name
            for p in tool.parameters
            if p.name.isidentifier() and not keyword.iskeyword(p.name)
        ]
        if len(valid_params) != len(tool.parameters):
            return {
                "tool_name": tool.name,
                "status": "error",
                "exit_code": 1,
                "elapsed_ms": 0,
                "stdout": "",
                "stderr": "Invalid parameter name in tool schema",
                "timed_out": False,
                "jsonrpc": {
                    "jsonrpc": "2.0",
                    "error": {"code": -32602, "message": "Invalid parameter name in tool schema"},
                    "id": 1,
                },
            }
        required_params = [p.name for p in tool.parameters if p.required]
        signature = ", ".join(f"{name}=None" for name in valid_params)
        test_script = f"""
import json
import sys

# Injected Tool Arguments
args = json.loads({repr(arg_json)})
required = set({required_params!r})
allowed = set({valid_params!r})
missing = sorted(required - set(args))
unknown = sorted(set(args) - allowed)
if missing or unknown:
    print("---JSONRPC_START---")
    print(json.dumps({{"jsonrpc":"2.0","error":{{"code":-32602,"message":f"Missing required arguments: {{missing}}; unknown arguments: {{unknown}}"}},"id":1}}))
    print("---JSONRPC_END---")
    sys.exit(1)

def run_handler({signature}):
{self._indent(tool.handler_code, 4)}

try:
    result = run_handler(**args)
    response = {{
        "jsonrpc": "2.0",
        "result": {{
            "content": [
                {{"type": "text", "text": str(result)}}
            ]
        }},
        "id": 1
    }}
    print("---JSONRPC_START---")
    print(json.dumps(response))
    print("---JSONRPC_END---")
except Exception as e:
    err_response = {{
        "jsonrpc": "2.0",
        "error": {{
            "code": -32000,
            "message": str(e)
        }},
        "id": 1
    }}
    print("---JSONRPC_START---")
    print(json.dumps(err_response))
    print("---JSONRPC_END---")
    sys.exit(1)
"""
        success, logs = self.sandbox.execute_script(test_script)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        jsonrpc_response: Optional[Dict[str, Any]] = None
        if "---JSONRPC_START---" in logs:
            try:
                part = logs.split("---JSONRPC_START---")[1].split("---JSONRPC_END---")[0].strip()
                jsonrpc_response = json.loads(part)
            except Exception:
                pass

        return {
            "tool_name": tool.name,
            "status": "success" if success else "error",
            "exit_code": 0 if success else 1,
            "elapsed_ms": round(elapsed_ms, 2),
            "stdout": logs,
            "stderr": "" if success else logs,
            "timed_out": "Process timed out" in logs,
            "jsonrpc": jsonrpc_response
            or {
                "jsonrpc": "2.0",
                "error": {"code": -32603, "message": logs or "Execution failed"},
                "id": 1,
            },
        }

    @staticmethod
    def _indent(code: str, spaces: int = 4) -> str:
        prefix = " " * spaces
        if not code.strip():
            return f"{prefix}return 'Success (Default empty handler)'"
        lines = []
        for line in code.split("\n"):
            lines.append(f"{prefix}{line}")
        return "\n".join(lines)


def get_default_mcp_presets() -> List[MCPServerDefinition]:
    """Returns curated starter templates for building MCP servers."""
    return [
        MCPServerDefinition(
            server_name="sqlite-query-engine",
            description="Local SQLite relational database explorer with schema inspection and queries.",
            tools=[
                MCPToolDefinition(
                    name="run_sql",
                    description="Executes a SELECT query against an in-memory or file database.",
                    parameters=[
                        MCPToolParameter("query", "string", "SQL query string to run"),
                    ],
                    handler_code="import sqlite3\nconn = sqlite3.connect(':memory:')\ncur = conn.cursor()\ncur.execute('CREATE TABLE users (id INT, name TEXT)')\ncur.execute(\"INSERT INTO users VALUES (1, 'Alice'), (2, 'Bob')\")\ncur.execute(query)\nrows = cur.fetchall()\nreturn json.dumps({'rows': rows})",
                ),
                MCPToolDefinition(
                    name="get_schema",
                    description="Returns current database tables and column definitions.",
                    parameters=[],
                    handler_code="return json.dumps({'tables': ['users(id, name)', 'orders(id, user_id, amount)']})",
                ),
            ],
        ),
        MCPServerDefinition(
            server_name="weather-satellite-mcp",
            description="Meteorological observation and atmospheric forecast service.",
            tools=[
                MCPToolDefinition(
                    name="get_weather",
                    description="Retrieves current temperature, humidity, and condition for a location.",
                    parameters=[
                        MCPToolParameter("city", "string", "Name of the target city"),
                        MCPToolParameter(
                            "unit",
                            "string",
                            "Temperature unit: 'celsius' or 'fahrenheit'",
                            required=False,
                            default="celsius",
                        ),
                    ],
                    handler_code="return json.dumps({'city': city, 'temperature': 22.5, 'condition': 'Partly Cloudy', 'humidity': '62%'})",
                )
            ],
        ),
        MCPServerDefinition(
            server_name="crypto-hash-engine",
            description="Cryptographic hashing and invariant signature verifier.",
            tools=[
                MCPToolDefinition(
                    name="compute_sha256",
                    description="Computes SHA-256 digest of input text payload.",
                    parameters=[
                        MCPToolParameter("data", "string", "Data string to hash"),
                    ],
                    handler_code="import hashlib\nreturn hashlib.sha256(data.encode('utf-8')).hexdigest()",
                )
            ],
        ),
    ]
