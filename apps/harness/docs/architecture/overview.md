# Harness architecture

The harness serves Shin's agents over A2A, one app for every agent. Read this page before you change how the harness is put together: its layers, agents, tools or the A2A contract. Follow [the coding guidelines](../development/coding-guidelines.md) for the code itself.

## Layers

| Folder        | Holds                                                                                          |
| ------------- | ---------------------------------------------------------------------------------------------- |
| `src/app`     | The FastAPI app                                                                                |
| `src/core`    | Settings, read from `config.toml` and the environment                                          |
| `src/agents`  | One folder per agent: `profile.yaml`, `system.md`, optional `skills/` and `tools/`. Also the registry and `build_agent` |
| `src/clients` | The LLM providers                                                                              |

## Agents

An agent is a folder under `src/agents/<name>/` with:

- `profile.yaml`, the agent's own settings. `tool_description_overrides` replaces a tool's description by tool name.
- `system.md`, the system prompt.
- `skills/<skill>/SKILL.md`, optional. A skill's `metadata.include_tools` lists tools the agent is offered only after it reads that skill.

`config.toml` registers each agent under `[[agents]]` with its `name`, the `path` of its `profile.yaml`, and the `family` and `model` it runs on. `[models]` lists the models each family offers, and a settings error refuses an agent on a model `[models]` does not offer. `load_agents` reads every registered agent once into an `Agent`.

`build_agent` builds an `Agent` with deepagents' `create_deep_agent`. The agent's filesystem is read-only and rooted at its `skills/` folder, so it can read its skills and cannot change them. It is offered the `tools` it is built with, and each `skill_tools` tool once it reads a skill that lists the tool. An agent without a `skills/` folder has no skills middleware.

`llm_provider` makes the chat model of a family: `claude` on Anthropic, `openai` on OpenAI's Responses API, and `glm` on an OpenAI-compatible gateway set by `AIGW_BASE_URL` and `AIGW_API_KEY`.

## Settings

`config.toml` holds every URL, limit and model name. Environment variables with the `SHIN_HARNESS_` prefix override it, and `__` separates nested names, such as `SHIN_HARNESS_APP__PORT`. Secrets live only in `.env`, which git ignores. `.env.example` lists them.

## Running

From `apps/harness`:

- `uv sync` installs the dependencies.
- `uv run python -m src.main` starts the server. `GET /shin/health` reports that it is up.
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright` and `uv run pytest` are the checks.
