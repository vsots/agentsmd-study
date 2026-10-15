# agentsmd-study

An open study of whether `AGENTS.md` and `CLAUDE.md` files change what coding agents cost, how fast they finish, and whether they get the task right, plus the harness so any team can rerun it on its own repo.

Status: **pilot scaffold**. The pipeline runs end to end; the repos in `repos.toml` still need their install and test commands filled in one by one.

## How it works

```
mine  ->  build  ->  validate  ->  run  ->  analyze
```

1. **Mine** (`ctxstudy mine`): walk a repo's squash-merged PRs and keep small changes that touched both source and tests. The test part of the diff is hidden; the source part (the human fix) is kept only to validate the task. The task statement comes from the linked issue, else the PR description, else the commit message.
2. **Build** (`ctxstudy build`): one Docker image per repo with the repo at a pinned commit, its dependencies, and a pinned Claude Code.
3. **Validate** (`ctxstudy validate`): in that image, run the hidden tests before and after the human fix. Keep the task only if some tests fail before and pass after ("fail-to-pass"), checked twice to drop flaky tests.
4. **Run** (`ctxstudy run`): for each task, arm and attempt, start a fresh container, prepare the arm, run Claude Code headless, record its transcript and diff, then score with the hidden tests. Trial order is shuffled with a fixed seed.
5. **Analyze** (`ctxstudy analyze`): pass rates with Wilson intervals, the per-task with/without difference with a task-level bootstrap CI and a sign test, speed and cost ratios, and tail behaviors. See `ANALYSIS_PLAN.md`.

### The two arms

- **with**: the repo as-is. If it has `AGENTS.md` but no `CLAUDE.md`, a one-line `CLAUDE.md` containing `@AGENTS.md` is added, the documented way to make Claude Code load it.
- **without**: every `AGENTS.md`, `CLAUDE.md` and `CLAUDE.local.md` (any depth) and `.claude/rules/` removed.

In both arms, project settings and MCP config (`.claude/settings*.json`, `.mcp.json`) are removed so hooks and servers can't differ, and `.git` is replaced by one fresh commit so the agent can't read the future fix from history.

### Decisions worth defending

| Decision | Why | Trade-off |
| --- | --- | --- |
| Fresh container per trial | Runs can't contaminate each other | Slower start; dependencies are baked into the image to compensate |
| Egress firewall: only Anthropic reachable | The agent runs without permission prompts, so nothing else should be reachable | Tasks whose tests need the network are excluded at validation |
| Non-root agent user | Least privilege; Claude Code also refuses to skip prompts as root | None worth noting |
| Tests, not an AI judge | Anyone can check a test result | Only repos with good tests qualify |
| Repeated trials, shuffled order | Single runs mislead; shuffling spreads drift across arms | 3 to 5 times the cost |
| Stdlib-only harness | Installs anywhere, easy to audit | A bit more code for stats |

## Setup (macOS)

1. **Docker**: Docker Desktop or OrbStack, running.
2. **Python 3.11+**, then from this folder:
   ```bash
   uv venv && source .venv/bin/activate && uv pip install -e '.[dev]'
   python -m pytest        # unit tests, no Docker needed
   ```
3. **Claude Code subscription token** (Pro/Max). On your Mac:
   ```bash
   claude setup-token      # opens a browser, prints a long-lived token once
   ```
   Store it outside the repo (a password manager, or a `.envrc` that git ignores) and load it into your shell:
   ```bash
   export CLAUDE_CODE_OAUTH_TOKEN=...   # never commit this
   unset ANTHROPIC_API_KEY              # an API key would override the subscription
   ```
   The harness passes the token into each container by name, so it never appears in commands, logs or files.
4. **Optional GitHub token** for better task statements (issue and PR text instead of commit messages): a fine-grained token with read access to public repositories, exported as `GITHUB_TOKEN`.

## Pilot (networkx)

```bash
ctxstudy preflight networkx
ctxstudy mine networkx --limit 30
ctxstudy build networkx                 # first build takes a few minutes
ctxstudy validate networkx              # keeps tasks with fail-to-pass tests
ctxstudy run networkx --name pilot --tasks 8 --dry-run
ctxstudy run networkx --name pilot --tasks 8
ctxstudy analyze pilot
```

Then pin `sha` for networkx in `repos.toml`, and pin the Claude Code version and model in `study.toml` using what the pilot transcripts report.

Without Docker, you can still validate tasks against a local virtualenv that has the repo's dependencies: `ctxstudy validate networkx --executor local --python /path/to/venv/bin/python`.

## Data layout (gitignored)

```
data/clones/<repo>/               blobless clone used for mining
data/tasks/<repo>/<task>.json     task statement, hidden test patch, fail-to-pass tests
data/runs/<run>/manifest.json     exact settings for the run
data/runs/<run>/trials.jsonl      one record per trial
data/runs/<run>/transcripts/      full Claude Code event stream per trial
data/runs/<run>/diffs/            what the agent changed
data/reports/<run>.md             analysis output
```

## Using a Pro/Max plan

Claude Code's docs say subscription auth is meant for ordinary individual use, and that products or services built for other people should use API keys. Running your own study on your own plan is not addressed directly, so keep it modest: pace runs (`--pause`), let `CLAUDE_CODE_RETRY_WATCHDOG` wait out rate limits, and switch to an API key for the core study if limits bite. Anyone else rerunning this harness should use their own API key.

## Limitations

- Task statements can hint at the fix (PR descriptions often do). Both arms get the same statement, so the comparison is fair, but absolute pass rates run high.
- The `total_cost_usd` figure is Claude Code's client-side estimate at list prices, not billing data.
- Firewall rules pin Anthropic's IPs at container start; if a trial fails to connect, rerun it.
- Results describe one agent and model at one point in time.

## Sources

Claude Code docs: [headless mode](https://code.claude.com/docs/en/headless), [CLI reference](https://code.claude.com/docs/en/cli-reference), [authentication](https://code.claude.com/docs/en/authentication), [permission modes](https://code.claude.com/docs/en/permission-modes), [devcontainer](https://code.claude.com/docs/en/devcontainer), [memory](https://code.claude.com/docs/en/memory), [environment variables](https://code.claude.com/docs/en/env-vars), [setup](https://code.claude.com/docs/en/setup), [legal and compliance](https://code.claude.com/docs/en/legal-and-compliance).
