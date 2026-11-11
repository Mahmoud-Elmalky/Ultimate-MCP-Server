# LM Studio Compatibility Plan

## 1. Goal

Make LM Studio the first verified MCP Host while keeping the server protocol-standard and not hard-coding host-specific behavior into domain services.

## 2. Host setup

LM Studio documentation states that the app acts as an MCP Host beginning with version 0.3.17 and supports local and remote MCP servers via its `mcp.json` configuration. Verify the user's installed LM Studio version before writing exact configuration instructions.

Example local stdio entry (adjust paths and interpreter for the user's environment):

```json
{
  "mcpServers": {
    "ultimate-mcp": {
      "command": "uv",
      "args": [
        "--directory",
        "C:/AI/Ultimate-MCP-Server",
        "run",
        "ultimate-mcp",
        "serve",
        "--profile",
        "default"
      ]
    }
  }
}
```

Do not include real secrets in this JSON. Prefer environment configuration available to the process or a local ignored `.env` file. Verify how the installed LM Studio version passes environment variables before depending on `env` fields.

## 3. Profile strategy

- Start with `default`; keep it compact.
- Use `research`, `coding`, `documents`, or `browser` for focused sessions.
- `full` exposes everything and may consume substantial context. Use it deliberately.
- Restart/reconnect after profile changes unless live tool refresh is verified.

## 4. Test matrix

At minimum, test:
1. LM Studio version and MCP Host availability.
2. MCP server starts under LM Studio without stdout protocol corruption.
3. Tools appear in the expected profile.
4. One deterministic tool call with simple string input.
5. One enum/optional-parameter tool call.
6. One multi-step task requiring two or more tools.
7. An invalid argument and a provider failure.
8. A permission-gated write action returns pending approval and does not execute.
9. After out-of-band approval, only the exact approved payload executes once.
10. A large result is bounded and offers continuation.

## 5. Model matrix

For each tested model record:
- exact model name and repository/hash if known;
- quantization and context window;
- model template/tool-call format;
- LM Studio version;
- inference settings;
- tool profile and tool count;
- whether parallel tool calls are enabled/supported;
- success, argument accuracy, task completion, error recovery, safety, tokens, and latency.

Do not assume all models support parallel calls, tool-call streaming, optional fields, nested JSON, or large descriptions equally.

## 6. Context economy

LM Studio documentation warns that some MCP servers expose excessive token-heavy tool descriptions. Therefore:
- keep default profile small;
- avoid huge parameter descriptions;
- avoid exposing synonym tools;
- cap tool output;
- use paginated outputs;
- separate domain profiles;
- benchmark tool-definition token estimates for representative tokenizers.

## 7. API integration distinction

LM Studio's application MCP Host (`mcp.json`) and its REST/OpenAI-compatible APIs are related but not identical integration paths. Do not assume every REST endpoint supports all MCP behavior. If adding a custom integration that invokes models through LM Studio APIs, document the exact endpoint and supported tool semantics separately from the desktop host.
