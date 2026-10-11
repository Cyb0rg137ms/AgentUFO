from __future__ import annotations
import json
import unittest

from agent_core.mcp_studio import MCPServerDefinition, MCPToolDefinition, MCPToolParameter, MCPServerScaffolder, MCPSandboxTester


class MCPStudioTests(unittest.TestCase):
    def test_server_source_and_client_config_are_generated(self):
        definition = MCPServerDefinition(
            server_name="agentufo-test",
            description="Small regression fixture",
            tools=[MCPToolDefinition(
                name="hello",
                description="Return a greeting",
                parameters=[MCPToolParameter(name="name", param_type="string", description="Name", required=True)],
                handler_code='return {"message": f"hello {name}"}',
            )],
        )
        source = MCPServerScaffolder.generate_python_server(definition)
        config = json.loads(MCPServerScaffolder.generate_claude_desktop_config(definition, script_path="/workspace/agentufo_mcp/server.py"))
        compile(source, "generated_mcp_server.py", "exec")
        self.assertIn('@mcp.tool()', source)
        self.assertEqual(config["mcpServers"]["agentufo-test"]["args"], ["/workspace/agentufo_mcp/server.py"])

    def test_tool_handler_executes_in_bounded_sandbox(self):
        tool = MCPToolDefinition(
            name="hello",
            description="Return a greeting",
            parameters=[MCPToolParameter(name="name", param_type="string", description="Name", required=True)],
            handler_code='return {"message": f"hello {name}"}',
        )
        # This unit test verifies JSON-RPC/handler semantics. Production defaults to
        # container isolation; host mode is an explicit test-only opt-in here.
        result = MCPSandboxTester(allow_unsafe_host=True).test_tool_execution(tool, {"name": "AgentUFO"})
        self.assertEqual(result["status"], "success", result.get("stderr"))
        self.assertEqual(result["jsonrpc"]["jsonrpc"], "2.0")


if __name__ == "__main__":
    unittest.main()
