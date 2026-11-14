# Harness architecture

The harness serves Shin's agents over A2A, one app for every agent. Read this page before you change how the harness is put together: its layers, agents, tools or the A2A contract. Follow [the coding guidelines](../development/coding-guidelines.md) for the code itself.

## Layers

| Folder     | Holds                                                           |
| ---------- | --------------------------------------------------------------- |
| `src/app`  | The FastAPI app                                                 |
| `src/core` | Settings, read from `config.toml` and the environment           |

## Settings

`config.toml` holds every URL, limit and model name. Environment variables with the `SHIN_HARNESS_` prefix override it, and `__` separates nested names, such as `SHIN_HARNESS_APP__PORT`. Secrets live only in `.env`, which git ignores. `.env.example` lists them.

## Running

From `apps/harness`:

- `uv sync` installs the dependencies.
- `uv run python -m src.main` starts the server. `GET /shin/health` reports that it is up.
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright` and `uv run pytest` are the checks.
