# Repository instructions

## Agent coordination

- Use the internal collaboration tools for subagent assignments, progress updates, and results within the current task.
- Do not use the Codex app's `send_message_to_thread` tool for subagent-to-parent updates. It creates a user-visible task message and can trigger a separate model request.
- When explicitly setting a model's reasoning effort, use a value supported by that exact model. Do not send `minimal` for `gpt-6-astra`.

## Layout

- `analyzer/` is the Python analyzer: the `tracen_replay` package, its `pyproject.toml`, `tests/`, `tools/` (sealing a candidate, replaying cached recordings, preparing worker inputs and recoveries, freezing and grading the final evaluation) and `lab/` (baseline audits, inventories, diagnoses and regrades). Install it with `pip install -e ./analyzer[vision]` and run the suite from the repository root with `python -X utf8 -m unittest discover -s analyzer/tests -t analyzer`.
- `cmd/tracen`, `internal/` and `api/openapi.yaml` are the Go service; `web/` is the browser client that Vite builds into `internal/webassets/dist`.
- `.local/` holds recordings, models, runs and session records and is never published.

## Commits

- All commits must use Conventional Commits: `<type>[optional scope][!]: <description>`.
- Use `feat` for new behavior, `fix` for bug fixes, `docs` for documentation, `test` for tests, `refactor` for restructuring without behavior changes, `perf` for performance improvements, `build` for build tooling or dependencies, `ci` for automation, and `chore` for maintenance.
- Write a concise, imperative description, such as `feat(api): add health endpoint` or `docs: explain local setup`.
- Mark breaking changes with `!` and explain them in a `BREAKING CHANGE:` footer.
- Keep commits focused. Do not include unrelated changes or generated recordings, model weights, credentials, or local configuration.
- Apply the same format to pull request titles when they will become squash commit messages.
