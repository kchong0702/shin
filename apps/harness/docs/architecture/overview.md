# Harness architecture

The harness serves Zumi's agents over WebSocket, speaking the [AG-UI](https://docs.ag-ui.com) protocol, one app for every agent. A channel service in front of it, not built yet, will carry each user's desktop app, mobile app, WhatsApp or voice to it. Read this page before you change how the harness is put together: its layers, agents, tools or how agents are served. Follow [the coding guidelines](../development/coding-guidelines.md) for the code itself.

## Layers

| Folder        | Holds                                                                                          |
| ------------- | ---------------------------------------------------------------------------------------------- |
| `src/app`     | The FastAPI app, the dependency-injector `Container`, and the WebSocket routes that serve each agent at `/zumi/ws/<name>` |
| `src/core`    | Settings, read from `config.toml` and the environment                                          |
| `src/agents`  | One folder per agent: `profile.yaml`, `system.md`, optional `skills/` and `tools/`. Also the registry and `build_agent` |
| `src/clients` | The LLM providers and the Postgres checkpointer                                                |

## Agents

An agent is a folder under `src/agents/<name>/` with:

- `profile.yaml`, the agent's own settings. `tool_description_overrides` replaces a tool's description by tool name.
- `system.md`, the system prompt.
- `skills/<skill>/SKILL.md`, optional. A skill's `metadata.include_tools` lists tools the agent is offered only after it reads that skill.
- other modules the agent's tools share.
- `tools/<tool>.py`, optional, one file per tool. A tool that needs settings is a `<tool>_tool` function that takes them and returns a `BaseTool`, built with `StructuredTool.from_function` and an `args_schema` that forbids extra arguments. The agent's container passes the settings, so a tool never reads them itself.

`config.toml` registers each agent under `[[agents]]` with its `name`, the `path` of its `profile.yaml`, and the `family` and `model` it runs on. `[models]` lists the models each family offers, and a settings error refuses an agent on a model `[models]` does not offer. `load_agents` reads every registered agent into an `Agent`, and the container keeps one registry.

`build_agent` builds an `Agent` with deepagents' `create_deep_agent`. The agent's filesystem is read-only and rooted at its `skills/` folder, so it can read its skills and cannot change them. It is offered the `tools` it is built with, and each `skill_tools` tool once it reads a skill that lists the tool. An agent without a `skills/` folder has no skills middleware.

`llm_provider` makes the chat model of a family: `claude` on Anthropic, `openai` on OpenAI's Responses API, and `glm` on an OpenAI-compatible gateway set by `AIGW_BASE_URL` and `AIGW_API_KEY`.

## Serving agents

`add_agent_routes` serves each `ServedAgent(name, open_graph)` over a WebSocket at `/zumi/ws/<name>`. `open_graph` is an async context manager that opens the agent's graph for one run, together with anything the run holds open, such as a browser. It closes them when the run ends, including when the run fails or the client disconnects. Build the graph inside `open_graph` and ask the checkpointer's provider for the saver there: a graph built earlier would keep the in-memory saver the app starts with.

The only client is the channel service, not a browser, so a connection that sends an `Origin` header is refused before it opens, which the client sees as HTTP 403. The channel service opens one socket per user it is serving. On it, a client sends AG-UI `RunAgentInput`s and gets back each run's AG-UI events. [`ag-ui-langgraph`](https://pypi.org/project/ag-ui-langgraph/)'s `LangGraphAgent` turns the LangGraph run into those events:

- **One lifelong thread per user.** `threadId` names the user, and the agent keeps its own copy of their thread under `<agent>:<threadId>`, so two agents never share one. The harness takes only the client's last user message and gives it a new id, so a client can't edit, fork or add to the thread's history, and a reused message id adds a turn instead of rewriting the thread. A run without a `threadId` or a user message is refused.
- **One run at a time per thread.** A run waits for the thread's earlier run to finish, so two channels writing for one user at once don't lose each other's turns. The lock is per process, so the harness runs as one process.
- **Events.** A run starts with `RUN_STARTED`, streams `TEXT_MESSAGE_*` as the model writes and `TOOL_CALL_*` for each tool call and its result, and ends with `RUN_FINISHED`. A tool that returns an error message to the model is its result, and the run goes on. The harness drops `RAW` events and the `MESSAGES_SNAPSHOT` and `STATE_SNAPSHOT` that would resend the whole lifelong thread every run.
- **Errors.** A run ends with `RUN_ERROR` and a plain reason when it goes past `recursion_limit` steps, or when a tool or the graph raises: `LangGraphAgent` catches those itself. When opening the graph fails, or something the run holds open crashes mid-run, `_events` catches it with `except*`, because an MCP session's task groups wrap it in exception groups. It reports the innermost error, after a `RUN_STARTED` when the run hadn't started, so every run has exactly one start and one end. A failure while closing a run that already ended is logged, not sent.
- **Bad input.** A message that is not a `RunAgentInput`, text or bytes, gets a `RUN_ERROR`, and the socket stays open.

`LangGraphAgent` would otherwise run the graph at LangGraph's default of 25 steps, so it gets the graph's own config, which carries `recursion_limit`.

The checkpointer keeps every conversation. With `POSTGRES_URL` set, the app opens an `AsyncPostgresSaver` on that Postgres when it starts, creates its tables and swaps it into the container for as long as it runs, so conversations survive restarts. Its pool checks each connection before lending it, so a Postgres restart doesn't fail the next runs. Without `POSTGRES_URL`, the container's `InMemorySaver` keeps them until the server stops; the tests use it, and only the Postgres test reads `POSTGRES_URL`. `AsyncPostgresSaver` can only be made inside a running event loop, which is why agents ask the checkpointer's provider for it per run.

A long conversation is summarised by deepagents' summarisation middleware, which offloads the turns it summarises away to `/conversation_history/`. `build_agent` routes that path to the thread's own state, so the offloaded turns are checkpointed with the thread and never written to the agent's folder.

`Container` wires the settings, the agent registry, the checkpointer and the `agents` list. To serve a new agent, add `src/app/container/<agent>.py` with a `<Agent>Container` that takes the settings, the registry and the checkpointer as dependencies and provides the agent's `ServedAgent`. Wire it in `Container` with `providers.Container`, and add its `served` to `agents`.

## Settings

`config.toml` holds every URL, limit and model name. Environment variables with the `ZUMI_HARNESS_` prefix override it, and `__` separates nested names, such as `ZUMI_HARNESS_APP__PORT`. Secrets live only in `.env`, which git ignores. `.env.example` lists them.

## Running

From `apps/harness`:

- `uv sync` installs the dependencies.
- `docker compose up -d` starts Postgres on `127.0.0.1:5433`. Set `POSTGRES_PASSWORD` in `.env` (`openssl rand -hex 16`) and `POSTGRES_URL=postgresql://zumi:<password>@127.0.0.1:5433/zumi`.
- `uv run python -m src.main` starts the server. `GET /zumi/health` reports that it is up.
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright` and `uv run pytest` are the checks.
