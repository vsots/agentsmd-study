# Analysis plan

**Status: DRAFT.** Finalize after the pilot and before the core runs. Record the commit that freezes this file below; log every later change under "Deviations".

- Frozen at commit: `________`
- Date: `________`

## Questions

1. **Correctness.** Do context files change whether the agent solves the task?
2. **Speed.** Do they change wall-clock time and the number of agent turns?
3. **Cost.** Do they change output tokens and estimated cost?
4. **Behavior.** Do they change how often the agent runs tests, edits test files, errors out or hits the turn limit?

## Design

- **Repos:** the 10 marked core in `repos.toml`, each pinned to a commit.
- **Tasks:** squash-merged PRs since 2025-10-01 that touch both source and tests, with at most 4 source files and 400 changed lines. A task is kept only if at least one hidden test fails before the human fix and passes after it, in two runs.
- **Sample:** up to `____` validated tasks per repo, drawn with the study seed.
- **Arms:** `with` (repo as-is, `CLAUDE.md` importing `AGENTS.md` if needed) and `without` (all context files removed). Everything else is identical.
- **Agent:** Claude Code `____` (pinned), model `____` (pinned), max 60 turns, 30-minute timeout, permission prompts off inside the sandbox.
- **Attempts:** 5 per task per arm, in a seeded random order.

## Outcomes

- **Primary:** a trial passes when all fail-to-pass tests pass and no pass-to-pass test breaks.
- **Secondary:** wall-clock seconds, agent turns, output tokens, estimated cost (Claude Code's estimate; not billing data).
- **Behavior:** share of trials that errored, hit the turn limit, timed out, ran tests, edited test files, or edited context files.

## Analysis

- Pass rate per arm with a Wilson 95% interval.
- **Primary estimate:** the per-task difference in pass rate (with minus without), averaged across tasks, with a 95% CI from a task-level bootstrap (5,000 resamples, fixed seed). Tasks are the unit because attempts on one task are not independent.
- Sign test across tasks: how many tasks the files helped vs hurt.
- Speed and cost: for each task, the ratio of medians (with / without); report the geometric mean ratio across tasks with a task-level bootstrap CI.
- Per-repo results are reported as exploratory.

**How the result will be read** (fixed now, before seeing data):

- CI entirely within ±5 points: no meaningful effect on correctness.
- CI excludes 0: the files helped (or hurt), by about the point estimate.
- Otherwise: inconclusive at this sample size, reported as such.

## Exclusions

- Infrastructure errors (container, network, rate limits) are rerun once; if they repeat, the trial is excluded and counted in the report.
- Tasks that fail validation never enter the sample.
- No trial is dropped for any other reason. All results are published, including null and negative ones.
- Pilot trials are excluded from the main results. networkx is rerun in the main study with pinned settings.

## Deviations

_None yet._
