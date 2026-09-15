# Repository instructions

## Agent coordination

- Use the internal collaboration tools for subagent assignments, progress updates, and results within the current task.
- Do not use the Codex app's `send_message_to_thread` tool for subagent-to-parent updates. It creates a user-visible task message and can trigger a separate model request.
- When explicitly setting a model's reasoning effort, use a value supported by that exact model. Do not send `minimal` for `gpt-6-astra`.

## Commits

- All commits must use Conventional Commits: `<type>[optional scope][!]: <description>`.
- Use `feat` for new behavior, `fix` for bug fixes, `docs` for documentation, `test` for tests, `refactor` for restructuring without behavior changes, `perf` for performance improvements, `build` for build tooling or dependencies, `ci` for automation, and `chore` for maintenance.
- Write a concise, imperative description, such as `feat(api): add health endpoint` or `docs: explain local setup`.
- Mark breaking changes with `!` and explain them in a `BREAKING CHANGE:` footer.
- Keep commits focused. Do not include unrelated changes or generated recordings, model weights, credentials, or local configuration.
- Apply the same format to pull request titles when they will become squash commit messages.
