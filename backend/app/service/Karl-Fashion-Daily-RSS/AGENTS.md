# Repository Guidelines

## First Principles

Please think using first principles. You can't always assume you know exactly what you want and how to get it. Be deliberate, start with the original needs and problems, and if your motivation and goals are unclear, stop and discuss them with me.

## Code Style

When writing any Python code, enforce the Skill python-code-style.

## Solution Guidelines

When providing modification or refactoring solutions, the following guidelines must be followed:

- No compatibility or patching solutions are allowed.

- No over-design; maintain the shortest path implementation and do not violate the first requirement.

- No solutions beyond the scope of my requirements, such as helper or fallback solutions, as this may lead to business logic deviations.

- Ensure the solution's logic is correct; it must undergo end-to-end logical verification.

- Failfast, let it crash, let bugs be exposed earlier.


## Project Structure & Module Organization
This repository is a small Python package managed with `uv`. Keep application code under `src/deep_agents/`; current modules include shared configuration, prompts, schemas, state, and agent logic in `src/deep_agents/agents/`. Treat `.venv/` as local-only environment state and `uv.lock` as the dependency lockfile that should stay in sync with `pyproject.toml`. The current repo is intentionally lean, so add new packages under `src/deep_agents/` and place future tests in a top-level `tests/` directory.

## Build, Test, and Development Commands
Use `uv` for all local workflows:

- `uv sync`: create or update the virtual environment from `pyproject.toml` and `uv.lock`.
- `uv run python -m compileall src`: quick syntax smoke test for all source files.
- `uv run python -c "from deep_agents.configuration import Configuration; print(Configuration())"`: lightweight import/config sanity check.

There is no dedicated build script or test runner checked in yet. If you add one, document it in `README.md` and keep this file updated.

## Coding Style & Naming Conventions
Follow standard Python conventions: 4-space indentation, `snake_case` for modules/functions, `PascalCase` for classes, and explicit type hints on public functions and models. Match the existing package layout and keep files focused on one responsibility. Prefer short docstrings on models or non-obvious logic; avoid redundant comments. When adding config fields, keep names environment-variable friendly because `Configuration.from_runnable_config()` reads uppercase env vars.

## Testing Guidelines
No formal test suite exists yet, so every change should include at least one executable smoke check using `uv run`. When adding tests, use `pytest`, place files in `tests/`, and name them `test_<module>.py`. Cover configuration parsing, prompt/schema changes, and async agent flows with targeted unit tests before expanding integration coverage.

## Commit & Pull Request Guidelines
Current history uses short, lowercase, imperative commit subjects such as `init project`. Keep commit messages concise and action-oriented, for example `add configuration defaults` or `fix clarify agent import`. Pull requests should explain the purpose, list touched modules, note any new environment variables or dependency changes, and include terminal output for validation steps.

## Security & Configuration Tips
Do not commit real secrets from `.env`. Prefer environment variables for model keys, endpoints, and provider-specific settings, and document any required variables when introducing new integrations.
