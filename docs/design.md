# Claude Code Context Lab — Design

This document describes what the lab measures, how it isolates each run,
how it handles credentials, and how it turns transcripts into numbers.
The reasons behind individual choices are in [decisions.md](decisions.md);
how the lab was built is in
[implementation-history.md](implementation-history.md).

## Goal

The lab measures how much context and how many tokens Claude Code uses for
the same task under different configurations. It runs on a Claude
**subscription**, not on API billing.

The repository is public so you can read it and follow the measurement.
Using or reproducing the code requires permission from Thinkport GmbH (see
[LICENSE](../LICENSE)).

The lab has two modes for the same measurement:

- **Automated** — run one or all tests headless (`claude -p`), as often as
  needed, and collect comparable numbers.
- **Interactive** — start a session for one test, check that its profile is
  set up as intended (`/plugin`, `/context`), and work with it live.

### Non-goals

- Statistics beyond median, minimum and maximum over repeated runs.
- Cost in money. A subscription is not billed per token. The lab reports
  the API price of the same tokens (`api_equiv_usd`) as a reference only.
- Comparing models by default. Every run pins its model; a model
  comparison is possible by giving tests different models.
- Comparing accounts. Results are compared within one person's runs.

## The task

Every test asks Claude to build a "Hello GitHub Pages" site in an empty
repository, publish it with GitHub Pages, and link it in the repository's
About section.

A run reaches the goal (`goal_ok`) when both hold:

1. The GitHub Pages site is live: the URL answers HTTP 200 and the page
   contains "Hello GitHub Pages".
2. The repository's About → Website field (`homepage`) is the Pages URL.
   The comparison ignores surrounding whitespace, one trailing slash,
   `http` vs `https`, and the case of the host name.

## Test matrix

Three profiles × two prompts = six tests, defined in `lab/matrix.conf`:

```
# id     profile      prompt        [model]
test1    clean        simple
test2    clean        well-defined
test3    superpowers  simple
test4    superpowers  well-defined
test5    dev-stack    simple
test6    dev-stack    well-defined
```

Test ids must match `test[A-Za-z0-9_-]*`, profile names `[a-z0-9][a-z0-9-]*`.
This keeps every repository named `lab-test…`, which teardown can find, and
no id can point a sandbox path outside `sandbox/`.

`REPEAT=n` or `--repeat n` runs each selected test n times, each from a
fresh state.

### Prompts

| Prompt | File | Content |
|---|---|---|
| `simple` | `lab/prompts/simple.txt` | One sentence: create the app in the `xyz` directory, publish it with GitHub Pages, link it in the About section. |
| `well-defined` | `well-defined-prompt` | The same task with explicit requirements, branch-based Pages publishing, the About link, and verification steps. |

Both prompts name the directory `xyz`. Each test therefore places its
repository at `sandbox/<test>/xyz` and starts Claude in `sandbox/<test>`,
so the prompts stay unchanged.

Automated runs need answers to questions Claude asks on the way. The
follow-up lines in `lab/prompts/followups.txt` are the same for every
profile:

```
Go with your recommendation for every open question and continue without asking.
Continue until the site is published on GitHub Pages and verified, as the task describes.
```

### Profiles

All plugins come from `claude-plugins-official`, the marketplace that ships
with Claude Code, so no extra marketplace is needed.

| Profile | Plugins |
|---|---|
| `clean` | none |
| `superpowers` | `superpowers` |
| `dev-stack` | `superpowers`, `frontend-design`, `microsoft-docs`, `playwright` |

`dev-stack` stands for a typical developer setup, built from the most
installed plugins in the official marketplace. Install counts and token
estimates come from Claude Code's plugin catalog (2026-10-09):

| Plugin | Unique installs | Catalog estimate, always-on tokens | Why it is in `dev-stack` |
|---|---|---|---|
| `superpowers` | 1.15 M | 843 + SessionStart hook | Workflow skills |
| `frontend-design` | 1.29 M | 83 | Most installed plugin; the task produces an HTML page |
| `microsoft-docs` | 31.5 k | MCP tools, not estimated | Docs-lookup MCP server (Microsoft Learn) that works without a login |
| `playwright` | 367 k | MCP tools, not estimated | Browser for checking the published page |

The catalog does not estimate MCP tool definitions; measuring them is one
of the things the lab is for.

Rejected for `dev-stack`:

- `vercel` — about 4.2 k always-on tokens would dominate the profile.
- `pr-review-toolkit` — no use for this task.
- `github` — an MCP server that duplicates the `gh` CLI, which every
  profile already uses.
- `context7` — its remote MCP server needs an OAuth login and never
  started in a headless run, so it measured nothing. `microsoft-docs`
  replaced it.
- Other MCP plugins need a login (for example github, linear, gitlab,
  vercel), an extra tool (`uv`, `bun`, `docker`, `php`), or overlap with
  playwright (chrome-devtools-mcp).

Every profile's `settings.json` holds the plugin list (`enabledPlugins`) and
the shared permission lists (see [Permissions](#permissions)). `dev-stack`
also allows its two MCP servers.

### Model

Every run passes `--model`. Without it, the setup token runs on the
account's default model, which Anthropic can change, and runs would no
longer be comparable.

- Default: `claude-haiku-5-5`, the cheapest current model. At API list
  prices in October 2026 it costs $0.10 / $0.50 per million input/output
  tokens, against $2 / $10 for Sonnet 5.5 and $4 / $20 for Opus 5.5.
- Precedence: the `model` column in `matrix.conf`, then `LAB_MODEL`, then
  the default.
- How much each model counts against the subscription's usage limits is
  not published. The lab records the 5-hour limit before and after every
  run, so the effect is measured rather than assumed.
- Trade-off: Haiku may fail the multi-step task more often than larger
  models. `goal_ok` makes that visible, and a test can switch model.

## What is the same in every profile

These parts of the context are the same in every profile of one person's
runs. They shift all results by the same amount and do not distort the
comparison:

- Claude Code's own system prompt and built-in tools.
- Claude Code's built-in plugins (for example `cc-plugin-agents-md`,
  `cc-plugin-telemetry`), which load in every profile, `clean` included.
- Organization-managed policy settings, if the account has any.

claude.ai connectors attached to the account (for example Asana or
Microsoft 365) are absent in every profile: the setup token covers model
requests only and does not load them
([Claude Code authentication](https://code.claude.com/docs/en/authentication)).
Results therefore do not depend on what an account has connected.

The usage-limit values (`limit_5h_*`) are account-wide. Any other Claude
session on the same account moves them too, so they are meaningful only
without concurrent use.

## Implementation

Python ≥ 3.11, standard library only. There is no `pip install`, so the lab
runs right after `git clone`. The entry point is `lab.py` with
subcommands; tests use the built-in `unittest`.

Bash with `jq` was rejected: JSONL parsing with de-duplication and
statistics, subprocess timeouts (`timeout` is not on macOS by default), and
the token flows are fragile in shell, BSD/GNU tool differences hurt
portability, and testing would need an extra tool.

### Prerequisites outside the repository

Besides Python, `git`, `gh` and `claude`, the `dev-stack` profile needs
two things on the machine:

- Node.js with `npx`, which starts the MCP servers of its plugins.
- Google Chrome, for the `playwright` plugin. Its MCP server uses the
  installed Google Chrome. The lab is not a Node project, so it does not
  install a browser itself.

Preflight checks `npx`. `lab.py setup` checks for Google Chrome whenever the
matrix contains a `dev-stack` test, and prints a warning, not an error, if
it is missing: the app bundle in `/Applications` or `~/Applications` on
macOS, or `google-chrome`, `google-chrome-stable` or `chrome` on `PATH`
elsewhere. Without Chrome, `dev-stack` runs may fail to open a browser.

### Layout

The code calls the directory that holds `lab.py` the **lab root**
(`lab_root`).

```
lab.py                       # entry point: tokens | setup | run | session | report | teardown
lab/
├── matrix.conf              # one line per test: <id> <profile> <prompt> [model]
├── profiles/<name>/settings.json
├── prompts/
│   ├── simple.txt
│   └── followups.txt        # fixed answers for automated runs
├── labkit/                  # Python package
│   ├── env.py               # .env loading and writing (mode 600)
│   ├── preflight.py         # checks before every command except tokens and report
│   ├── config.py            # matrix, profiles, prompts, follow-ups
│   ├── tokens.py            # `tokens` commands
│   ├── github_auth.py       # PAT form URL, permission checklist, browser
│   ├── github.py            # gh/git wrapper: repos, Pages, goal check, S5 check
│   ├── sandbox.py           # test dirs, config dirs, git boundary, plugins
│   ├── limits.py            # usage-limit probe and gate
│   ├── runner.py            # headless and interactive runs
│   ├── metrics.py           # transcripts → metrics
│   ├── results.py           # results.csv schema and IO
│   ├── report.py            # results.csv → comparison tables
│   ├── statusline.py        # live context/token display
│   └── teardown.py          # remove repos, sandbox, secrets
└── tests/                   # unittest, transcript fixtures, a fake `claude`
well-defined-prompt          # the well-defined prompt
.env.example                 # documents every variable, no values
.env                         # gitignored; created by `lab.py tokens`
results.csv                  # gitignored; one row per run
sandbox/                     # gitignored; created by setup and runs
├── .gh/                     # empty gh config dir for the lab's token
└── test1/
    ├── .git                 # boundary file: `gitdir: /nonexistent`
    ├── config/              # CLAUDE_CONFIG_DIR, rebuilt for every run
    ├── xyz/                 # the test repo, remote <org>/lab-test1
    └── results/<timestamp>/ # stream-<n>.jsonl, stderr-<n>.txt, statusline.json, projects/
```

## Isolation

### A fresh Claude config dir per run

Every run gets a new `CLAUDE_CONFIG_DIR` (`sandbox/<test>/config`). It
contains only:

- `settings.json`: the profile's settings plus the lab's status line;
- `.claude.json`: onboarding marked as done, a theme, and the test directory
  marked as trusted, so an interactive session starts without dialogs.

Nothing is read from or written to the user's own Claude config.

Listing plugins in `enabledPlugins` alone does not install them in a fresh
config dir: headless Claude Code fetches the marketplace but installs
nothing. Before the first turn, each run therefore:

1. adds the official marketplace
   (`claude plugin marketplace add anthropics/claude-plugins-official`), or
   updates it if it is already known;
2. installs each of the profile's plugins
   (`claude plugin install <plugin> --scope user`);
3. puts back any profile setting the installer changed.

A plugin that asks to run a marketplace-declared command is never accepted
automatically; the run stops and names the plugin. Installation time does
not count toward the run's timeout or duration. The installed version of
each plugin is recorded with the run, since marketplace plugins update
independently of the lab.

### The lab repo stays invisible to Claude

Claude starts in `sandbox/<test>`, which lies inside the lab's own git
repository. Claude Code would find that repository by itself and put its
branch, recent commits and status into the session context, and key its
auto-memory to it. It ignores `GIT_CEILING_DIRECTORIES` (observed with
Claude Code 2.1.296).

Each run therefore writes a `.git` **file** containing
`gitdir: /nonexistent` into `sandbox/<test>`. Claude Code stops its search
there and sees a neutral, constant git state (branch `HEAD`, clean, no
commits, about 65 tokens). Memory is keyed to the test directory, and the
nested repository `sandbox/<test>/xyz` still works normally.

This relies on observed, undocumented behavior. A unit test checks the
boundary, and the runner warns after a run if a transcript reports the lab
repository's branch. `GIT_CEILING_DIRECTORIES` stays set for git itself.

Rejected after live probes:

- An empty `git init` repository in `sandbox/<test>`: it leaks the global
  git user name and lists `xyz/` as untracked.
- `CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS=1` / `includeGitInstructions: false`
  ([env vars](https://code.claude.com/docs/en/env-vars)): removes the status
  snapshot but keeps the lab repository as project root (branch, memory
  path), and also drops Claude Code's commit and PR instructions, which
  makes runs less realistic.
- Moving the sandbox to a temporary directory: works, but the sandbox was
  to stay inside the repository.

Preflight also warns if a `CLAUDE.md`, `CLAUDE.local.md` or
`.claude/CLAUDE.md` exists in a parent directory of the sandbox (other
than `~/.claude/CLAUDE.md`), since Claude Code may load these as memory,
which skews token counts. A parent `.claude/` directory that holds only
settings does not warn: Claude Code reads `.claude/settings*.json` only in
the project directory itself.

### Git and gh config isolation

- `GIT_CONFIG_GLOBAL=/dev/null` and `GIT_CONFIG_NOSYSTEM=1`: the user's own
  git settings (identity, credential helpers such as `osxkeychain`) never
  apply, and the GitHub token is never stored in a keychain.
- Each test repository gets a lab identity and the
  `gh auth git-credential` credential helper in its repo-local config.
- `gh` runs with an empty config dir (`sandbox/.gh`) and
  `GH_PROMPT_DISABLED=1`, so the user's own `gh` login is never used.
- Child processes start from an environment without any `CLAUDE*`,
  `ANTHROPIC*` or GitHub token variable; the lab adds back only what the
  child needs.

### Other details

- Headless runs get stdin from `/dev/null`; otherwise `claude -p` waits
  3 seconds for piped input on every call.
- `--bare` is not usable: it accepts only `ANTHROPIC_API_KEY` or an
  `apiKeyHelper` and never reads the subscription login.
- The status line is configured the same way in every profile. It renders
  outside the model context and adds no tokens.

## Secrets and authentication

### Variables

| Variable | Purpose | Secret |
|---|---|---|
| `CLAUDE_CODE_OAUTH_TOKEN` | Claude subscription credential for all runs | yes |
| `GH_TOKEN` | GitHub credential for `gh` and `git push` over HTTPS | yes |
| `GH_TOKEN_EXPIRES_AT` | expiry of `GH_TOKEN`, checked by preflight | no |
| `LAB_GH_ORG` | GitHub organization that holds the test repositories | no |
| `LAB_MODEL` | optional default model | no |
| `LAB_BROWSER` | optional browser for logins; read from the environment only | no |
| `REPEAT` | optional default number of runs per test | no |

- `lab.py tokens …` creates the credentials and writes them to `.env`
  (mode `600`, gitignored). `LAB_GH_ORG` is set by hand.
- **Variables already set in the environment win** over `.env`, so the
  values can come from another source (for example a password manager's
  CLI or direnv).
- Preflight refuses to run if `.env` is tracked by git or not mode `600`.
- Secrets reach child processes only through environment variables, never
  as command-line arguments, and are printed only masked.

### Why a dedicated GitHub organization

The lab requires a new, empty GitHub organization in which the user is an
admin. The organization is the scope: a token limited to it reaches only
lab repositories, even with "All repositories" access, so no per-repository
selection is needed and nothing outside the lab is exposed. Repositories
can be deleted and recreated for an empty start on every run.

An existing, non-empty organization is not supported. Fine-grained tokens
and GitHub App installations cannot be limited by a name prefix, only to
repositories selected one by one, and a recreated repository falls out of
that selection. `gh` cannot create organizations, so this is a one-time
step in the browser.

### Claude: setup token

`lab.py tokens claude` runs `claude setup-token` (one browser approval) and
stores the printed token as `CLAUDE_CODE_OAUTH_TOKEN`. One token serves
every test directory, with no refresh-token rotation between them.

- **Lifetime: one year, not configurable.** No subscription credential with
  a shorter lifetime is documented
  ([Claude Code authentication](https://code.claude.com/docs/en/authentication)).
  Short use is achieved by revoking it after the lab.
- The command runs in a 500-column pseudo-terminal, so the terminal never
  wraps the token across lines. The lab masks the token in what reaches the
  screen (best effort) and clears the screen and scrollback once it is
  stored.
- It runs in a throwaway config dir, so no existing Claude login is
  touched.
- The token belongs to the account signed in **in the browser**. With
  several Claude accounts, `LAB_BROWSER` selects a browser for the login
  (for example a private window).

Rejected:

- `claude auth login` per test directory: one browser approval per test,
  an undocumented lifetime, and server-side revocation on logout is not
  documented either.
- Copying an existing Claude login into each config dir: OAuth refresh
  tokens rotate, so once one copy refreshes, the others become invalid, and
  the user's own login could be logged out during a presentation.
- macOS Keychain as storage: macOS-only and harder to set up. The
  "environment wins" rule still allows it.

### GitHub: fine-grained personal access token

`lab.py tokens github` opens GitHub's form for a fine-grained token,
pre-filled through URL parameters:

- a name with a random suffix (token names must be unique per account) and
  a description;
- resource owner `LAB_GH_ORG`;
- the shortest expiry, **1 day**;
- repository permissions: Administration, Contents, Pages, Workflows
  (read and write); Actions, Metadata (read-only).

*Repository access* has no URL parameter: the user selects **All
repositories**. GitHub sometimes drops pre-filled values, so the lab prints
a checklist to compare against the form. The user clicks *Generate token*
and pastes it; the input is hidden. The lab stores it as `GH_TOKEN` with
`GH_TOKEN_EXPIRES_AT` = now + 24 hours. GitHub offers no API to create a
fine-grained token, so this takes one browser step per lab day.

`gh` reads the token from `GH_TOKEN`; `git push` uses it through
`gh auth git-credential`.

- **Workflows (write)** lets a run push `.github/workflows/*`. Claude may
  choose to deploy Pages through GitHub Actions, and without this
  permission GitHub rejects the push: the run would fail because of the
  lab's token, not because of the profile under test. Trade-off: the token
  can create workflows that run code in the organization's repositories.
  That is acceptable in a dedicated, empty organization with a one-day
  token, not in a real one.
- **Actions (read)** only reads deployment status; the lab never starts
  workflows itself.

`lab.py tokens github --check` runs the whole repository lifecycle once:
create `lab-s5check`, commit, push over HTTPS, enable Pages, check that the
page serves the text, and delete the repository. It prints one line per
step. The code calls this the S5 check, after the spike (S5) that first
answered whether a token can do all of this.

Rejected:

- A GitHub App issuing 8-hour user tokens through the device flow. Both it
  and the PAT passed the lifecycle check in a live comparison, but the App
  needs three one-time browser steps (create, enable device flow, install)
  plus a daily device code, against one pre-filled form for the PAT. Its
  shorter lifetime did not outweigh that, since teardown revokes the PAT
  anyway.
- `gh auth login` in a separate config dir: broad OAuth scopes, no expiry,
  and `gh auth logout` does not revoke the token.
- App installation tokens (1 hour): they need the App's private key, a
  credential that never expires.

### Revocation

`lab.py teardown --secrets` revokes both tokens. A token counts as
**revoked only when a check with the old token proves it**. Anything else
is reported with the masked token and the page where the user can check or
revoke it, and teardown exits with status 1.

- **GitHub:** the lab calls `POST /credentials/revoke`
  ([revoke API](https://docs.github.com/en/rest/credentials/revoke)), which
  needs no other credential. Its answer (202 Accepted) proves nothing, so
  the lab then calls `GET /user` with the old token: 401 means revoked.
  While the token still answers 200, it is checked up to four times, five
  seconds apart.
- **Claude:** there is no CLI or API to revoke a setup token
  ([anthropics/claude-code#48373](https://github.com/anthropics/claude-code/issues/48373)).
  The lab opens the claude.ai settings page that lists Claude Code tokens
  and asks the user to revoke the lab's entries. It then makes one minimal
  `claude -p` call with the old token. Only an unambiguous authentication
  failure counts as revoked: a `401`, `authentication_error`,
  "invalid bearer token", "invalid x-api-key", "invalid api key", "token
  has been revoked", "OAuth token has expired" or "OAuth token is invalid"
  in the call's result or stderr. A bad request, a network error, a usage
  limit or an overload says nothing about the token. A community report
  says deleted tokens can stay active
  ([#57400](https://github.com/anthropics/claude-code/issues/57400)), which
  is why the check exists.

Each token is removed from `.env` in every outcome, since the lab no longer
needs it. The exception: the GitHub token is kept when GitHub was skipped
or a repository could not be deleted, because a second teardown needs it.
`.env` is deleted once no settings are left in it.

## Run lifecycle

`lab.py run` for one run of one test:

1. **Preflight** — tools, `.env`, credentials and the organization setting
   exist, and the GitHub token outlives the run timeout. Otherwise the lab
   stops with a specific message before anything is reset. Preflight runs
   once in `lab.py`, before every command except `tokens` (which creates
   the values it checks) and `report` (which reads a local file only).
2. **Usage-limit gate** — a minimal probe call (`claude -p "Reply with OK"`
   on `claude-haiku-5-5`, throwaway config dir, no plugins) reads the
   current rate-limit event. The lab prints how much of the 5-hour limit is
   left. **Below 10 % left, or with an unknown limit, the test starts only
   after the user confirms.** Without a terminal the answer is no. There is
   no CLI command to read the limit without an API call; the probe costs
   one small Haiku call, and the run under test never sees it. Its value is
   the run's `limit_5h_start_pct`.
3. **Reset** — rebuild `sandbox/<test>/config` from the profile and install
   its plugins; delete the GitHub repository `lab-<test>` if present and
   recreate it (public, Pages off); re-initialize `sandbox/<test>/xyz` with
   the remote set and no commits.
4. **First turn** — `claude -p "<prompt>" --model <model> --permission-mode
   default --output-format stream-json --verbose` in `sandbox/<test>`.
5. **Follow-ups** — while the goal is not met, `followups.txt` has lines
   left, and a session ID is known, the lab continues with
   `claude -p "<next line>" --resume <session_id> …`. Before deciding, it
   checks the goal. If Pages is configured, it waits up to 180 s, but only
   while the site is not yet live: a deployment in progress must not
   trigger a follow-up, which would cost that profile extra tokens. Once
   the page shows the text, a missing About link is decided at once,
   because the link only comes from an action by Claude.
6. **Verify** — if the last check found no Pages site but one is
   configured now, the lab gives it the same 180 s build time. The result
   is `goal_ok`.
7. **Collect** — copy the transcripts to `results/<timestamp>/`, compute
   the metrics, and append one row to `results.csv`.

A per-run timeout (default 20 minutes) ends a hung run. Each headless
`claude` call runs in a process group of its own, so a timeout or Ctrl+C
also ends the processes it started (MCP servers, browsers). Every started
run writes a row, also on timeout, failure or interrupt. `duration_s`
counts from the first turn: neither the reset nor plugin installation
counts against a profile.

Status of a headless run, in this order: `failed` (the lab itself hit an
error), `timeout`, `ok` (goal met), `denied` (goal not met and Claude hit a
permission denial), otherwise `failed`.

`lab.py session <test>` does steps 1–3, then starts `claude` interactively
with the prompt as its first message. After `/exit` it does steps 6–7.
An interactive run is `ok` if the goal is met, else `failed`; it has no
timeout. `lab.py session --no-prompt <test>` starts without the prompt, to
inspect the profile, and records nothing.

## Permissions

All profiles share base allow and deny lists; `dev-stack` adds two MCP
allow rules:

- `allow`: `Read`, `Edit`, `Write`, `WebFetch`, `Bash(git:*)`,
  `Bash(gh:*)`, `Bash(curl:*)`, `Bash(ls:*)`, `Bash(cat:*)`, `Bash(pwd)`,
  `Bash(mkdir:*)`. `dev-stack` adds two allow rules, one per MCP server.
- `deny`: `Bash(cd:*)`. Claude Code checks each part of a compound command,
  so `cd xyz && git status` is denied as a whole.

All profiles run with `--permission-mode default`, passed explicitly in
both modes: interactive sessions otherwise start in auto mode, and auto
mode lets different tool calls through. There is no `bypassPermissions`:
equal rights per profile keep the comparison fair, and Claude cannot act
outside the lists without a prompt (interactive) or a denial (headless).

**Denials are part of the measurement.** The lists are not tuned until a
profile runs without them.

## Metrics

### Sources

The session transcripts, which are the same for interactive and headless
runs:

- main thread: `config/projects/<cwd-slug>/<session>.jsonl`;
- subagents: `config/projects/<cwd-slug>/<session>/subagents/agent-<id>.jsonl`;
- `cost-state` entries in the main transcript: cumulative `modelUsage` and
  `totalCostUSD` per `claude` process, subagents included.

The config dir is new for every run, so every main transcript in it belongs
to the run.

### Fields

| Field | Definition |
|---|---|
| `mode`, `model` | `headless` or `interactive`; the pinned model |
| `ctx_start` | `input + cache_read + cache_creation` tokens of the first API call of the main thread |
| `ctx_end` | the same, for the last API call of the main thread |
| `ctx_growth` | `ctx_end - ctx_start`: how much the task added to the context |
| `tokens_in`, `tokens_out`, `cache_read`, `cache_write` | session totals, subagents included, from `modelUsage` |
| `sub_calls`, `sub_ctx_start_max`, `sub_ctx_growth_max` | number of subagent API calls, largest subagent start context, largest subagent growth (last − first call) |
| `api_calls` | API calls of the main thread |
| `turns` | prompts Claude received: the task prompt plus follow-ups |
| `duration_s` | wall time from the first turn |
| `limit_5h_start_pct`, `limit_5h_end_pct` | 5-hour usage-limit percentage before and after the run (1 % resolution) |
| `goal_ok` | the goal check (see [The task](#the-task)) |
| `status` | `ok`, `failed`, `timeout`, `denied` |
| `api_equiv_usd` | API-equivalent cost, reference only, from `totalCostUSD` |
| `plugin_versions` | installed plugin versions |

`limit_5h_end_pct` comes from the last rate-limit event of a headless run,
or from the last status-line data of an interactive session.

### How totals are computed

- Totals come from `modelUsage`, not from adding up transcript entries. In
  an early test, the de-duplicated per-call sums matched `modelUsage`
  exactly for input, cache read and cache write, but output was 666 against
  880 (spike, 2026-10-12, `claude-haiku-5-5`): per-call output counts in the
  transcript are incomplete. Per-call
  values are used only for context size, where the input side is exact.
- A run with follow-ups is several `claude` processes. Each `cost-state`
  carries a `startTime`; totals are the sum of the last `cost-state` per
  `startTime`, each summed over all models.
- After each run the lab cross-checks these totals against the stream's
  `result` events and warns if they differ.

### Findings from the live runs

- **Duplicates.** Every API response appears more than once in the
  transcript. Per-call values are de-duplicated by `message.id`, keeping
  the last entry.
- **Resumed sessions.** `--resume` keeps the session ID and continues its
  cumulative usage: the follow-up processes append to the same transcript,
  their `cost-state` entries carry the same `startTime`, and each process's
  `result` event reports the session total so far. Cross-checks therefore
  take the last `result` per session, never a sum over processes.
- **Synthetic entries.** API errors and usage-limit notices appear as
  assistant entries with model `<synthetic>`. They are not API calls and
  are skipped, so they do not distort `api_calls` or `ctx_end`.
- **Turns.** Claude Code also writes internal notifications as `user`
  entries. `turns` counts only `user` entries whose `promptSource` is set
  and is not `system`.
- **Deferred MCP tools.** Only the names of MCP tools enter the context at
  start. Claude Code loads a tool's definition through `ToolSearch` when it
  is needed, which is why an MCP server adds little to the starting
  context.
- **Base contexts differ by mode.** In early tests (spikes, 2026-10-12,
  `claude-haiku-5-5`), an interactive session started at about 31.1 k tokens
  and a headless one at about 18.8 k, for the same model and an empty
  directory. The report never ranks one mode
  against the other.
- **Subagents start smaller.** Subagents started through the Task tool run
  inside the parent session with their own context (about 11 k at start;
  spike, 2026-10-12, `claude-haiku-5-5`), and only their final answer flows
  back into the parent's context.

### Report

`lab.py report` prints three tables from `results.csv`. Groups are
mode × model × profile × prompt; with more than one run, cells show the
median followed by min–max. The tables fit a terminal of about 120
columns, so mode and model appear as section headings.

1. **Runs** — per profile and prompt: `n` runs, `ok` (status ok), `goal`
   (runs with `goal_ok`), and `ctx_start`, `ctx_growth`, `tokens_out`,
   `cache_read`, `limit_used` (5-hour limit end minus start).
2. **Starting point and growth per context** — per profile and model:
   start and growth of the interactive main session, the headless main
   session, and subagents. This supports the decision whether to hand work
   to a subagent.
3. **Token consumption** — input, cache write, cache read, output, their
   total, and the API-equivalent cost. Cache reads dominate the total:
   every API call re-reads the whole cached context, so the total is
   roughly the number of calls times the average context size.

A `results.csv` written by an older lab version with other columns is
refused when a run tries to append to it. Move it aside (for example to
`results-<date>.csv`, which git ignores).

### Status line

`lab/labkit/statusline.py` shows the current context
(`context_window.current_usage`, `used_percentage`), the session tokens,
and the 5-hour limit used, for example
`ctx 31.1k (3%) · in 31.1k out 4 · 5h 12% used`. It also saves the last
status data for the interactive run's `limit_5h_end_pct`.

## Teardown

`lab.py teardown` lists every `lab-test*` repository in the organization,
the `lab-s5check` repository if present, and all directories under
`sandbox/` (including run transcripts). It deletes only after explicit
confirmation (or `--yes`); without a terminal it stops.

Teardown has its own, smaller set of requirements, since it must work with
whatever is left:

- It needs neither the Claude token nor Node.js, nor a readable matrix.
- With a missing, expired or rejected GitHub token it skips the repository
  part and says how to get a new token, but still removes the sandbox and,
  with `--secrets`, still runs the Claude revocation step.
- It exits with status 1 if anything asked for was not done: declined,
  skipped, a deletion failed, or a token is not proven revoked.

## Measurements

### Dry run (2026-10-13, `claude-haiku-5-5`, headless)

> **Measured before the goal included the About link.** At the time, the
> simple prompt only asked to create the app, the well-defined prompt did
> not mention the About section, and success meant "site live with the
> text". New runs measure a slightly larger task, so their numbers are not
> directly comparable with these. The durations also include the repo reset
> and an unconditional 180 s Pages wait, so they are not comparable with
> current runs.

`REPEAT=1 python3 lab.py run all`: headless, `claude-haiku-5-5`, one run per
test. These are single observations, not medians.

| Test | Profile | Prompt | Status | Duration | Turns | API calls | ctx start | ctx growth | Output | API-equiv. |
|---|---|---|---|---|---|---|---|---|---|---|
| test1 | clean | simple | ok | 74 s | 2 | 17 | 18.8k | +6.3k | 4.9k | $0.009 |
| test2 | clean | well-defined | ok | 157 s | 1 | 32 | 19.3k | +16.8k | 9.4k | $0.018 |
| test3 | superpowers | simple | denied | 225 s | 3 | 9 | 20.9k | +5.8k | 4.8k | $0.007 |
| test4 | superpowers | well-defined | ok | 122 s | 1 | 23 | 21.5k | +9.7k | 6.1k | $0.013 |
| test5 | dev-stack | simple | denied | 249 s | 3 | 12 | 22.8k | +9.0k | 7.4k | $0.009 |
| test6 | dev-stack | well-defined | ok | 148 s | 1 | 30 | 23.4k | +14.9k | 8.1k | $0.018 |

- Durations exclude plugin installation; `denied` runs include the final
  180 s Pages wait.
- The six runs moved the 5-hour limit by about 1–2 %.
- Both `denied` runs used the simple prompt: Claude kept trying
  `cd …/xyz && git …`, which the deny list blocks, and stopped instead of
  switching to `git -C xyz`.

An interactive check (`session --no-prompt test3`) showed no onboarding,
Haiku 5.5, only superpowers (15 skills) plus the built-in plugins,
permission mode `default`, and a working status line. `/context` before the
first message estimated 26.2 k tokens: system prompt 3 k, system tools
18.1 k, skills 3.4 k, messages 1.7 k. Tool definitions are the largest share
of the base context.

### Representative run (2026-10-13, `claude-haiku-5-5`, headless, `REPEAT=3`)

> **Measured before the goal included the About link.** At the time, the
> simple prompt only asked to create the app, the well-defined prompt did
> not mention the About section, and success meant "site live with the
> text". New runs measure a slightly larger task, so their numbers are not
> directly comparable with these.

`REPEAT=3 python3 lab.py run all`: 18 headless runs on `claude-haiku-5-5`,
16 `ok`, 2 `denied` (one each in clean/simple and superpowers/simple). No
run saw the lab repository and no run had a totals mismatch. Medians, with
min–max in brackets:

| Profile | Prompt | ok | ctx start | ctx growth | Output | Total tokens | API-equiv. |
|---|---|---|---|---|---|---|---|
| clean | simple | 2/3 | 18.8k | 7.9k (5.1k–10.3k) | 6.1k | 438k (179k–524k) | $0.010 |
| clean | well-defined | 3/3 | 19.3k | 18.0k (15.2k–20.0k) | 10.0k | 874k (690k–1.5M) | $0.017 |
| superpowers | simple | 2/3 | 20.9k | 8.0k (6.3k–18.3k) | 6.2k | 497k (252k–1.4M) | $0.011 |
| superpowers | well-defined | 3/3 | 21.5k | 12.5k (11.4k–12.8k) | 7.7k | 700k (630k–813k) | $0.013 |
| dev-stack | simple | 3/3 | 22.8k | 13.9k (10.7k–20.0k) | 9.6k | 752k (735k–1.2M) | $0.015 |
| dev-stack | well-defined | 3/3 | 23.4k | 16.5k (11.5k–18.1k) | 9.3k | 1.1M (637k–1.2M) | $0.019 |

What the numbers show:

- **The setup fixes the starting context.** It was identical in all three
  runs of each test: clean about 19 k, superpowers +2.1 k, dev-stack +4.0 k.
  The well-defined prompt adds about 0.6 k.
- **Behavior, not the setup, drives growth and consumption.** Within one
  test the total varies by more than fivefold (superpowers/simple:
  252 k–1.4 M). Cache reads are about 94–98 % of every total (94.3–97.8 %
  per run): each API call re-reads the whole context, so the number of
  calls drives consumption.
- **The well-defined prompt** costs more tokens (it asks for verification)
  but succeeded in every run; the simple prompt failed twice on the `cd`
  deny rule.
- **Cost of the series:** about 3 % of the 5-hour limit on the subscription
  plan used for the measurement, and $0.005–$0.026
  per run at API prices on Haiku.
- Three runs per test are still few, and the ranges are wide. The report's
  min–max values make that visible.

## Open items

- A new `REPEAT=3` measurement with the About-link goal.
- Interactive runs were checked by hand but not measured in a series.
- License and copyright holder (see [decisions.md](decisions.md#open-decisions)).
