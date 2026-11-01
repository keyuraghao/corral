# The MCP server

Corral exposes the controller to AI agents over the Model Context Protocol. The server is a thin client of the controller's REST API, so an agent and a human using the CLI see the same nodes and the same jobs. It is a first-class surface: anything the CLI can do with the fleet, an agent can do through these tools.

## Transports

| Transport | Command | Endpoint / use |
|---|---|---|
| stdio (default) | `corral mcp` | Claude Desktop, Claude Code, Cursor, Windsurf |
| Streamable HTTP | `corral mcp --transport streamable-http --host 127.0.0.1 --port 8765` | `http://host:port/mcp` |
| SSE (legacy) | `corral mcp --transport sse --port 8765` | `/sse` |

The server drives a controller over its REST API. Point it with `--api` or the `CORRAL_API` environment variable; the default is `http://127.0.0.1:8787`.

## Registering with agents

| Agent | What to run |
|---|---|
| Claude Code | `claude mcp add --scope user corral -- corral mcp --api http://127.0.0.1:8787` |
| Claude Code (HTTP) | `claude mcp add --transport http corral http://127.0.0.1:8765/mcp` |
| Codex CLI | add a `[mcp_servers.corral]` block in `~/.codex/config.toml` |
| Gemini CLI | add `mcpServers.corral` in `~/.gemini/settings.json` |

## Tools

| Tool | What it does |
|---|---|
| `list_nodes` | every connected worker, with OS, architecture, executors and labels |
| `run` | fan a command out to every matching node, wait, and return each exit code and output |
| `submit` | like `run` but returns the job id immediately for a long job |
| `get_job` | one job with its per-node tasks, states, exit codes and output |
| `list_jobs` | recent jobs with their state and fan-out count |
| `cancel_job` | stop a running job; any container is killed and leaves nothing behind |
| `test_matrix` | run one test command across the matching nodes and report pass or fail per node |
| `reproduce_cve` | reproduce a published CVE in a disposable sandbox and return a verdict and a Markdown report |

## A typical session

1. `list_nodes` to see what the fleet can run.
2. `run` with `executor="docker"` and a selector to fan a command out and read the results.
3. `test_matrix` to check a suite across the OS matrix, or `reproduce_cve` for a defensive reproduction.
4. `get_job` to revisit any job by id.

## Isolation and authorization

Every docker job an agent starts runs in a disposable, network-isolated container unless the agent explicitly passes `network=True`. The `reproduce_cve` tool is for authorized, defensive work on infrastructure you control: it reproduces and documents a vulnerability and holds no exploit payloads of its own. When the controller is started with a token, point the server at it with a controller API that carries the token, or run the server on the same host inside the trust boundary.
