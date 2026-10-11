# Performance and resource profile

## Changes in this revision

- Routine conversational requests now bypass the expensive pondering, topology-search, and decomposition passes unless the prompt explicitly requests deep analysis. Normal chat uses a 2,048-token output ceiling; explicit deep analysis uses 4,096. Provider-specific `_safe_llm_max_tokens` may lower these caps further.
- Generic chat prompts no longer trigger a web search merely because they contain phrases such as `what is`, `compare`, or `how to`. Network search is limited to explicit current-information/search wording.
- Ordinary chat no longer writes every user prompt and response into holographic memory. Recent conversation history is capped at 60 records and the context builder uses a bounded recent subset.
- A short-lived health snapshot cache avoids repeatedly probing Ollama and providers on every dashboard refresh.
- Run events are compact, capped at 250 per run, and high-frequency progress events are persisted at most every 1.5 seconds. Lifecycle changes and failures still persist immediately.
- Coordinator telemetry is cached for 0.8 seconds during progress updates; the UI polls run status every 1.8 seconds rather than every 1.1 seconds.
- Workspace fingerprints are bounded by directory depth and count, and common dependency/build/vendor directories are excluded.
- When an existing project test suite is detected, the builder skips an additional LLM call to generate a redundant Python fixture. The review gate still runs project verification.
- MCP handler testing now binds declared arguments correctly, validates required/unknown arguments, and generated Python identifiers are validated before source generation.

## What has not been measured

No provider-specific latency benchmark, peak-RSS benchmark, GPU/VRAM benchmark, or parallel-agent throughput benchmark was run in this environment. These changes remove known redundant work and bound internal state, but real performance depends on provider latency, local model size/context, repository shape, and test-suite duration. Do not interpret these changes as a guaranteed percentage speedup.

## Parallel execution status

The verified crew remains serial: Architect → Builder → Reviewer. It deliberately does not launch independent split-work builders concurrently because multi-file merge/rollback, per-file review gates, and cross-process ownership need a separate verified implementation. Extra seats are not advertised as parallel workers in this build.

## MCP status

The MCP window generates a FastMCP stdio server and client config, tests tool handlers with a bounded subprocess runner, and can save generated files to `agentufo_mcp/` under the active workspace. The optional runtime dependency is listed in `requirements-mcp.txt`. It does not automatically launch unknown MCP processes or connect arbitrary remote MCP endpoints.
