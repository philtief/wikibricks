## Summary

<!-- One paragraph describing what this PR changes and why. -->

## Changes

<!-- Bullet list of user-visible changes. -->

-
-

## Test plan

<!-- How did you verify this? Include failing-then-passing test output for bug fixes. -->

- [ ] `uv run pytest` passes
- [ ] `uv run ruff check src tests scripts` passes
- [ ] Manually tested against a workspace (if deploy path touched)

## Checklist

- [ ] Added or updated tests
- [ ] Updated `CHANGELOG.md` under `[Unreleased]`
- [ ] Updated `README.md` / `AGENTS.md` if behaviour or config surfaces changed
- [ ] No hardcoded workspace IDs, tokens, or FEVM paths
- [ ] No LLM calls added under `src/wikibricks/`
