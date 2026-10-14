# Decisions

This file lists the decisions made while building the lab: what was
decided, why, and what it costs if the decision turns out wrong. The
design that results from them is in [design.md](design.md).

Each entry is marked:

- **User decision** — decided by the person who commissioned the lab.
- **Implementation decision** — decided during implementation, within the
  design, by the coordinating developer or agent.

The lab was built by AI coding agents under a coordinating agent, with a
person deciding direction and running the steps that need a browser or the
subscription (see [implementation-history.md](implementation-history.md)).
The rulings R1–R36 and R38–R42 are numbered in the order they were made.
R37 was a ruling on the review process and is not listed. Decisions the
user made without a ruling number follow after them.

## Rulings

### R1 — Pinned flags apply to model calls only

**Implementation decision.**

- **Decided:** "every `claude` call passes `--model` and
  `--permission-mode default`" applies to calls that reach the model
  (headless runs, the usage-limit probe, interactive sessions). CLI
  subcommands such as `setup-token` and `plugin install` do not accept or
  need these flags.
- **Why:** the rule exists to keep runs comparable; subcommands do not run
  a model.
- **Cost if wrong:** none; a reviewer may flag a subcommand without the
  flags.

### R2 — A person runs the live steps

**Implementation decision.**

- **Decided:** steps that need a browser or spend subscription usage (the
  live GitHub check, the first live runs, the interactive check, the dry
  run) are run by a person together with the coordinating agent, after the
  code review. Code changes they require go back to the implementing agent
  as a fix round. Pure result records in the design document are written by
  the coordinator.
- **Why:** an agent cannot click through a browser login, and it should not
  spend the subscription without being asked.
- **Cost if wrong:** some result notes in the design were written without a
  separate review.

### R3 — One helper for the shared gh/git environment

**Implementation decision.**

- **Decided:** the environment for the `claude` child reuses the helper that
  builds the `gh`/`git` environment, instead of repeating the same
  variables.
- **Why:** avoids two copies of the same isolation rules.
- **Cost if wrong:** minor coupling between the sandbox and GitHub modules.

### R4 — Tests compare resolved paths

**Implementation decision.**

- **Decided:** path assertions compare `Path.resolve()` results.
- **Why:** on macOS the temporary directory is reached through a symlink,
  so unresolved and resolved paths differ.
- **Cost if wrong:** none.

### R5 — The fake `claude` finds the status-line path like Claude Code does

**Implementation decision.**

- **Decided:** the test double for `claude` finds the save path for the
  status-line data the same way Claude Code does: from the `statusLine`
  command in the `settings.json` the lab generates for each run
  (`sandbox/<test>/config/settings.json`).
- **Why:** keeps the fake honest about the real wiring; a wrong path in the
  generated settings makes the tests fail.
- **Cost if wrong:** the fake is slightly more complex.

### R6 — Work on a branch in the main checkout

**Implementation decision.**

- **Decided:** development happens on a feature branch in the main
  checkout, not in a separate git worktree.
- **Why:** the live steps need the existing `.env`, which is gitignored and
  therefore missing in a new worktree, and the person runs the commands in
  that directory.
- **Cost if wrong:** less isolation from uncommitted local files.

### R7 — Credit AI assistance with `Assisted-by` only

**User decision.**

- **Decided:** each commit written with AI help carries an
  `Assisted-by: <model> (<role>)` trailer that names the model that
  actually wrote the change, for example
  `Assisted-by: Claude Sonnet 5.5 (code generation)`. The plan's fixed
  Opus trailer was replaced. AI is never credited with `Co-Authored-By`.
- **Why:** a trailer should name who actually worked on a change.
- **Cost if wrong:** trailers differ between commits, depending on the
  model.

### R8 — Verify the test repo deletion independently

**Implementation decision.**

- **Decided:** for the first live GitHub lifecycle check, the deletion of
  the test repository was confirmed with a separate read-only call, and the
  check was re-run once on a Pages failure. Two known weaknesses were not
  fixed first: a "404" text match that could report a failed deletion as
  done, and no retry when the Pages CDN lags behind.
- **Why:** both only make failures harder to diagnose; the independent
  check removes the risk of a false success for this check.
- **Cost if wrong:** one extra manual check.

### R9 — Build and live-test the PAT flow although the App flow passed

**User decision.**

- **Decided:** the fine-grained PAT flow (Task 1b), planned only as a
  fallback, was built and tested live although the GitHub App flow had
  passed the lifecycle check.
- **Why:** the App flow needed too many browser steps; the PAT was the
  alternative with fewer steps, and the two could be compared live.
- **Cost if wrong:** none; the code was already planned.

### R10 — Fix the App name length with Task 1b

**Implementation decision.**

- **Decided:** a defect found live (GitHub App names may have at most 34
  characters) was fixed in the same round as the PAT flow.
- **Why:** both changes touch the same module, and one review covers both.
- **Cost if wrong:** a slightly larger review.

### R11 — Remove the App flow as a small task of its own

**Implementation decision.**

- **Decided:** after the user chose the PAT only, removing the App flow ran
  as an extra task, reviewed like any other. The coordinator updated the
  design and the plan to record the user's decision.
- **Why:** the removal touches code and tests in several places and needs
  its own review.
- **Cost if wrong:** the documents were edited without a separate review;
  the final review covered them.

### R12 — Drop a `Co-Authored-By` line from a commit

**Implementation decision** (applies R7).

- **Decided:** one implementing agent added a `Co-Authored-By` line from
  its tool's default attribution. The commit, not yet pushed, was amended
  to remove it; the content stayed the same. Later task briefs said
  explicitly: no `Co-Authored-By` line.
- **Why:** the project credits AI with `Assisted-by` only.
- **Cost if wrong:** none; only a local commit message changed.

### R13 — `LAB_MODEL` from `.env` counts

**Implementation decision.**

- **Decided:** commands that run tests read the matrix with the merged
  configuration (`.env` overlaid by the environment), so `LAB_MODEL` in
  `.env` takes effect. The check whether Node.js is needed may read the
  environment alone, since it ignores the model.
- **Why:** a setting documented in `.env.example` must work from `.env`.
- **Cost if wrong:** `LAB_MODEL` in `.env` would be silently ignored.

### R14 — The repo reset refuses any directory but the test's own

**Implementation decision.**

- **Decided:** the repository reset takes the lab root as a required
  argument and refuses to run unless the directory to wipe is exactly
  `sandbox/<test>/xyz` of that test.
- **Why:** one equality check covers both a path outside the sandbox and a
  directory of another test. A review had found that the earlier,
  structural check could wipe any `…/sandbox/*/xyz`.
- **Cost if wrong:** the function signature differs from the plan; every
  caller must pass the lab root.

### R15 — Fewer report columns

**Implementation decision.**

- **Decided:** the main report table shows only mode, model, profile,
  prompt, run counts, `ctx_start`, `ctx_growth`, `tokens_out`, `cache_read`
  and `limit_used`, to fit a terminal of about 120 columns.
- **Why:** the report is shown live to an audience.
- **Cost if wrong:** some fields are only visible in `results.csv`.

### R16 — Mode and model as section headings

**Implementation decision.**

- **Decided:** the report shows mode and model as section headings instead
  of columns.
- **Why:** keeps the table within 120 columns and reads better on a
  projector.
- **Cost if wrong:** the layout differs from a flat table.

### R17 — Number format in the report

**Implementation decision.**

- **Decided:** below 1,000 as an integer; 1 k–99.9 k with one decimal
  (`31.1k`); 100 k–999 k as an integer (`450k`); from one million with one
  decimal (`1.2M`). Width tests use realistic magnitudes.
- **Why:** an earlier width test used small numbers and missed that
  realistic cache reads overflowed 120 columns.
- **Cost if wrong:** less precision for values of 100 k and more (±0.5 k).

### R18 — Choose the number format on the rounded value

**Implementation decision.**

- **Decided:** the format is chosen after rounding, so 99,999 shows as
  `100k` and 999,999 as `1.0M`, not as `100.0k` or `1000k`.
- **Why:** consistent and monotonic, without odd strings.
- **Cost if wrong:** none visible.

### R19 — No `Co-Authored-By` on request of a reviewer

**Implementation decision** (applies R7).

- **Decided:** a review agent's suggestion to add `Co-Authored-By` trailers
  was rejected.
- **Why:** the project's convention (R7) takes precedence over a tool's
  default attribution.
- **Cost if wrong:** none.

### R20 — No second Pages wait

**Implementation decision.**

- **Decided:** the final Pages check waits 180 s only if the last check in
  the follow-up loop did not already wait.
- **Why:** avoids a second three-minute pause on failed runs, with the same
  outcome.
- **Cost if wrong:** none.

### R21 — An error stops the whole batch

**Implementation decision.**

- **Decided:** an error in a run (other than Ctrl+C) stops `run all` after
  the run's row is saved.
- **Why:** continuing after, for example, an expired GitHub token would
  only produce failed runs.
- **Cost if wrong:** a transient `gh` error ends a long batch.

### R22 — Keep the sandbox in the repo, behind a `.git` boundary file

**User decision.**

- **Decided:** the sandbox stays inside the lab repository. Each test
  directory gets a `.git` file with `gitdir: /nonexistent`, which stops
  Claude Code from finding the lab repository.
- **Why:** live probes showed that Claude Code ignores
  `GIT_CEILING_DIRECTORIES` and put the lab repository's branch and recent
  commits into the context. The boundary file hides all of that, keys
  memory to the test directory, keeps Claude Code's git instructions, and
  costs a constant ~65 tokens. The alternatives are described in
  [design.md](design.md#the-lab-repo-stays-invisible-to-claude).
- **Cost if wrong:** it relies on undocumented Claude Code behavior. A unit
  test and a warning after each run catch a regression.

### R23 — Keep `claude`'s stderr and warn about the lab repo

**Implementation decision.**

- **Decided:** two findings deferred earlier became fixes: each turn's
  stderr is saved (`stderr-<n>.txt`), and the runner warns after a run if
  a transcript shows the lab repository's branch.
- **Why:** both were needed to debug the remaining live runs.
- **Cost if wrong:** one extra fix round.

### R24 — Paths relative to the lab root; no check on a detached lab repo

**Implementation decision.**

- **Decided:** the stderr hint prints the path relative to the lab root.
  The lab-repo warning is skipped when the lab repository itself has a
  detached HEAD.
- **Why:** the path can then be opened from where `lab.py` runs. A detached
  lab repository reports `HEAD`, the sandbox's normal value, and would
  cause false alarms.
- **Cost if wrong:** none.

### R25 — `--no-prompt` records nothing; Ctrl+C belongs to Claude

**Implementation decision.**

- **Decided:** an interactive session started with `--no-prompt` writes no
  results row. While an interactive `claude` runs, the lab ignores Ctrl+C.
- **Why:** an inspection session measures nothing. Claude Code handles
  Ctrl+C itself; without this, Ctrl+C would also stop the lab.
- **Cost if wrong:** none.

### R26 — Safe defaults in report and teardown

**Implementation decision.**

- **Decided:** `report` runs without preflight, so it works without
  credentials. Teardown keeps the GitHub token (and exits 1) if a
  repository could not be deleted. A declined teardown, or one without a
  terminal to confirm, deletes nothing and exits 1.
- **Why:** the report only reads a local file; the token is needed to
  retry a deletion; nothing is deleted without a clear yes.
- **Cost if wrong:** none.

### R27 — Archive development results before the dry run

**Implementation decision.**

- **Decided:** before the dry run, the `results.csv` with development rows
  was moved to a dated file (`results-<date>.csv`, gitignored).
- **Why:** presented numbers must come from the final setup only.
- **Cost if wrong:** development rows live in a separate file.

### R28 — README later, help text now

**Implementation decision** (superseded by the user decision
[README and boilerplate](#readme-and-repository-boilerplate)).

- **Decided:** the final fix wave added `--help` text, the `.env.example`
  header and remedies in error messages, but no README. The README was
  planned for the publishing cleanup.
- **Why:** the README depends on the final state of the code.
- **Cost if wrong:** newcomers had no README until the cleanup.

### R29 — The coordinator fixes documentation drift

**Implementation decision.**

- **Decided:** differences between the design, the plan and the code that
  the final review found were fixed by the coordinator.
- **Why:** these documents record decisions the coordinator took or
  received (see R2 and R11).
- **Cost if wrong:** documents edited without a separate review.

### R30 — Report a residual revocation finding instead of fixing it

**Implementation decision** (superseded by the user decision
[Revocation counts only when proven](#revocation-counts-only-when-proven)).

- **Decided:** a residual finding was not fixed in the final fix wave but
  shown to the user with a fix proposal: the Claude revocation check took
  any message containing "invalid" as an authentication failure, so an
  "invalid model" or "invalid request" error could report a live token as
  revoked.
- **Why:** there was no further fix wave planned; the decision belonged to
  the user.
- **Cost if wrong:** until fixed, `teardown --secrets` could print
  "revoked" for an error that has nothing to do with the token.

### R31 — `pages_ok` becomes `goal_ok`

**Implementation decision** (follows the user decisions
[Goal includes the About link](#goal-includes-the-about-link) and
[Names announce their content](#names-announce-their-content)).

- **Decided:** the success field is renamed from `pages_ok` to `goal_ok`,
  and the report column from `pages` to `goal`.
- **Why:** after the goal changed, the old name no longer said what the
  field holds.
- **Cost if wrong:** results files from before the change must be moved
  aside; the lab refuses to append to them.

### R32 — A GitHub token is revoked only when `GET /user` returns 401

**Implementation decision** (applies the user decision
[Revocation counts only when proven](#revocation-counts-only-when-proven)).

- **Decided:** after the revoke request, the lab calls `GET /user` with the
  old token. Only HTTP 401 counts as revoked.
- **Why:** the revoke API answers 202 Accepted, which proves nothing.
- **Cost if wrong:** one extra API call (a few, if the token still answers
  for a moment).

### R33 — Remove tokens from `.env` in every outcome

**Implementation decision.**

- **Decided:** teardown removes each token from `.env` whatever the check
  says, but exits 1 unless both are proven revoked. It tells the user that
  the token was removed locally and where to check it.
- **Why:** the lab no longer needs the token, and the local file stays
  clean without hiding an unverified revocation.
- **Cost if wrong:** the user loses the local copy they might have used to
  re-check the token.

### R34 — Wait only for the site, not for the link

**Implementation decision.**

- **Decided:** the goal check waits only while the Pages site is not yet
  live. Once the page shows the text, a missing About link is decided at
  once.
- **Why:** the link only comes from an action by Claude; it does not appear
  on its own. This removes up to about 3 × 180 s of idle waiting per run.
- **Cost if wrong:** a link set by a slow API call at the very end of a
  turn is missed, which costs one follow-up.

### R35 — A minimal CI workflow runs the unit tests

**Implementation decision.**

- **Decided:** a GitHub Actions workflow runs
  `python3 -W error -m unittest discover -s lab/tests` on Python 3.11 and
  on the latest Python 3 (`3.x`). It has read-only permissions and does
  nothing else.
- **Why:** the README and design say the lab needs Python 3.11 or newer.
  Running the tests on 3.11 backs that claim, and `3.x` with `-W error`
  shows a new deprecation warning before users run into it.
- **Cost if wrong:** two short CI jobs per push. A failure on `3.x` can
  come from a new Python release rather than from a change in the lab.

### R36 — Pin GitHub Actions by SHA within their current major versions

**Implementation decision.**

- **Decided:** the workflow pins `actions/checkout` at v4.4.0 and
  `actions/setup-python` at v5.6.0 by commit SHA, with the tag in a
  comment. It does not upgrade to newer major versions.
- **Why:** a SHA cannot be moved like a tag, so what runs in CI only
  changes with a commit. A major upgrade can change behavior and is not a
  small fix, so it was left for a separate change.
- **Cost if wrong:** pinned versions do not get fixes on their own;
  someone has to update the SHAs.

### R38 — The stress-test profile is called `dev-messy`

**Implementation decision** (applies the user decision
[Names announce their content](#names-announce-their-content)).

- **Decided:** the profile with many overlapping skills and MCP servers is
  named `dev-messy`.
- **Why:** it is a developer setup like `dev-stack`, and the name says
  what sets it apart: it has grown without cleanup.
- **Cost if wrong:** a rename touches the profile directory,
  `matrix.conf`, `MCP_PROFILES` in `lab.py` and the docs.

### R39 — The Sonnet test uses the model ID `claude-sonnet-5`

**Implementation decision.**

- **Decided:** the user asked for a test on "Sonnet 5". `test9` passes the
  model ID of Claude Sonnet 5, `claude-sonnet-5`, literally. It is not
  `claude-sonnet-5-5`, the newer Sonnet 5.5.
- **Why:** the request named a version, and the matrix pins exact model
  IDs so that runs stay comparable when a newer model comes out.
- **Cost if wrong:** if the user meant the current Sonnet, `test9`
  measures the previous generation. The fix is one column in
  `matrix.conf`.

### R40 — Effort is an optional matrix column

**Implementation decision.**

- **Decided:** `matrix.conf` gets an optional fifth column, `effort`
  (`low`, `medium`, `high`, `xhigh`, `max`). A model column of `-` means
  the default model, so a test can set an effort alone. A test with an
  effort passes `--effort` to every call that reaches the model (headless
  turns, follow-ups, the interactive session), never to the usage-limit
  probe. A test without one passes no `--effort`. `results.csv` gets an
  `effort` column after `model`, and the report groups by it.
- **Why:** the effort level changes how much a model thinks and therefore
  its token use, so it is part of what a run measures and must not be
  mixed in one median. Passing nothing when unset keeps the existing tests
  as they were. The probe measures the account's limit, not the test.
- **Cost if wrong:** `results.csv` files from before the change have other
  columns and must be moved aside. `-` is one more rule to know when
  editing the matrix.

### R41 — Extra MCP servers are user-scope and pinned

**Implementation decision.**

- **Decided:** MCP servers that no plugin ships come from the profile's
  `mcp.json` and are written as user-scope servers into the run's fresh
  `.claude.json`. Each npm package is pinned to an exact version and
  started with `npx -y`. Only servers without a login and without tools
  beyond Node are used. Each server gets one server-level allow rule
  (`mcp__<server>`).
- **Why:** user scope is where a developer's own servers live
  (`claude mcp add --scope user`), so the run sees what such a setup sees.
  Without a pinned version, `npx` would fetch the latest release at every
  session start, and runs on different days would not be comparable.
  A server that needed a login did not start in a headless run (see
  [Replace context7 with microsoft-docs](#replace-context7-with-microsoft-docs)).
- **Cost if wrong:** pinned versions age and miss fixes until someone
  updates them. The local servers run with the user's rights, and a
  server-level allow rule allows all of a server's tools without a prompt.
  A server that can run shell commands would therefore escape the shared
  `Bash` allow and deny lists; R42 excludes the one that could. `npx`
  downloads each package on first use, which counts toward the first
  run's duration.

### R42 — MCP servers keep no state outside the run and send no telemetry

**Implementation decision** (2026-10-14, after checking the published npm
packages).

- **Decided:** an MCP server in a profile must not keep state outside the
  run or send telemetry. For `dev-messy` this means:
  - `desktop-commander` is excluded. Its telemetry is on by default, it
    writes `~/.claude-server-commander`, `~/.claude-server-commander-logs`
    and `~/.desktop-commander-device/device.json` in the user's home
    directory, and its tools run any shell command outside the shared
    `Bash` allow and deny lists.
  - `memory` stores its file in the run's config dir
    (`MEMORY_FILE_PATH={config}/mcp-memory.jsonl`). By default it writes
    it into the shared npx cache, where it would carry over to the next
    run.
  - `context7` runs with `OTEL_SDK_DISABLED=true`.
  - every run sets `CHROME_DEVTOOLS_MCP_NO_USAGE_STATISTICS=1` and
    `CHROME_DEVTOOLS_MCP_NO_UPDATE_CHECKS=1`, which covers the user-scope
    server and the `chrome-devtools-mcp` plugin's server.
- **Why:** state that survives a run makes the next run start from
  something other than a fresh setup, which skews the measurement.
  Telemetry sends data about the lab to third parties and adds network
  calls that are not part of the task.
- **Cost if wrong:** a new version of a server may add telemetry or state
  that these opt-outs do not cover; the versions are pinned, so this only
  happens when someone updates them. `dev-messy` has one overlapping
  server fewer than planned.

## Further user decisions

### Haiku as the default model

**User decision.**

- **Decided:** every run pins its model; the default is
  `claude-haiku-5-5`. A test can name another model in `matrix.conf`, or
  `LAB_MODEL` can change the default.
- **Why:** the cheapest current model, and it works with the setup token
  both headless and interactively. Pinning keeps runs comparable when the
  account's default model changes.
- **Cost if wrong:** Haiku may fail the multi-step task more often than a
  larger model; `goal_ok` makes that visible.

### Permission mode `default` with shared lists, `cd` denied

**User decision.**

- **Decided:** every profile runs with `--permission-mode default` and the
  same allow and deny lists; the deny list starts with `Bash(cd:*)`. No
  `bypassPermissions`. Denials are measured, not tuned away.
- **Why:** equal rights keep the comparison fair. Interactive sessions
  would otherwise start in auto mode, which lets different calls through.
- **Cost if wrong:** some runs fail on the deny rule (two of 18 in the
  `REPEAT=3` measurement), which is part of what is measured.

### Usage-limit gate before each test

**User decision.**

- **Decided:** before each test the lab makes one minimal probe call, prints
  how much of the 5-hour limit is left, and asks before starting when less
  than 10 % is left. The probe runs outside the test; the run under test
  never sees it.
- **Why:** a demo must not run into the limit halfway, and there is no way
  to read the limit without an API call.
- **Cost if wrong:** one small Haiku call per test.

### PAT only; the GitHub App flow is removed

**User decision.**

- **Decided:** the lab uses a fine-grained personal access token only. The
  GitHub App flow was removed after the live comparison.
- **Why:** one pre-filled form per lab day, against three one-time browser
  steps plus a daily device code for the App.
- **Cost if wrong:** the token lives 24 hours instead of the App token's
  8 hours. Teardown revokes it.

### Workflows permission for the PAT

**User decision.**

- **Decided:** the PAT gets Workflows (read and write), so a run may push
  GitHub Actions workflows. The lifecycle check does not test this.
- **Why:** Claude may choose to deploy Pages through an Actions workflow;
  without the permission the run would fail because of the lab's token.
- **Cost if wrong:** the token can create workflows that run code in the
  organization's repositories. An old token without this permission is
  not detected by any check.

### Replace context7 with microsoft-docs

**User decision.**

- **Decided:** `dev-stack` uses `microsoft-docs` (Microsoft Learn MCP)
  instead of `context7`.
- **Why:** context7's MCP server needs an OAuth login and never started
  headless, so it measured nothing. `microsoft-docs` works without a login
  and without extra tools.
- **Cost if wrong:** the profile is less close to the most-installed list.

### Revocation counts only when proven

**User decision.**

- **Decided:** a token counts as revoked only when a check with the old
  token proves it. Everything else is reported with what to check and
  where.
- **Why:** a one-year credential must not be believed dead by mistake.
- **Cost if wrong:** teardown exits 1 more often and asks the user to
  check by hand.

### Goal includes the About link

**User decision.**

- **Decided:** the goal is a live Pages site with "Hello GitHub Pages"
  **and** the site linked in the repository's About → Website field. The
  lab checks both. Both prompts were extended minimally to ask for the
  link.
- **Why:** a published site that the repository does not link to is not
  finished.
- **Cost if wrong:** measurements from before the change are not directly
  comparable with new ones.

### Names announce their content

**User decision.**

- **Decided:** names must say what they hold. The variable `root` became
  `lab_root`, and `pages_ok` became `goal_ok` (R31).
- **Why:** after changes, an old name can describe something the value no
  longer is.
- **Cost if wrong:** a rename touches many files at once.

### Publish with a single squashed commit

**User decision.**

- **Decided:** the current files are cleaned of personal and local details;
  the development history is not rewritten. The branch is squash-merged
  locally, and the main branch is pushed as one commit.
- **Why:** the development history contains local details. A squash
  publishes one clean state.
- **Cost if wrong:** the detailed commit history is not public;
  [implementation-history.md](implementation-history.md) summarizes it.

### README and repository boilerplate

**User decision.**

- **Decided:** the repository gets a README, LICENSE, SECURITY.md,
  CONTRIBUTING.md and ACKNOWLEDGMENTS.md, and all rulings are published in
  this file. This overrides R28.
- **Why:** a published lab needs a starting point and clear terms.
- **Cost if wrong:** more files to keep in sync with the code.

### Check for Google Chrome instead of installing Chromium

**User decision** (2026-10-14).

- **Decided:** `lab.py setup` no longer runs `npx playwright install
  chromium`. When the matrix contains a `dev-stack` or `dev-messy` test,
  it warns if Google Chrome is not installed.
- **Why:** the lab never uses Playwright. Only the MCP server of the
  `playwright` plugin does. It brings its own library and uses the
  installed Google Chrome (verified 2026-10-14: during a probe run the
  installed Chrome's browser binary was accessed, and Playwright's own
  Chromium was not). The installed Chromium was therefore never used, and
  the command printed a warning because the lab is not a Node project.
- **Cost if wrong:** a future version of the MCP server with another
  default browser would make these runs fail to open a browser until that
  browser is installed.

### Warn only for memory files in parent directories

**User decision** (2026-10-14).

- **Decided:** preflight warns only for `CLAUDE.md`, `CLAUDE.local.md` and
  `.claude/CLAUDE.md` in parent directories of the sandbox. A parent
  `.claude/` directory with only settings does not warn.
- **Why:** Claude Code reads `.claude/settings*.json` only in the project
  directory itself. From parent directories it loads memory files. The old
  warning fired for settings that have no effect on the runs.
- **Cost if wrong:** a future Claude Code version that reads more from
  parent directories would go unwarned.

### A stress-test profile with overlapping skills and MCP servers

**User decision** (2026-10-14).

- **Decided:** a profile with ten skill plugins and at least ten MCP
  servers, with heavy overlap, all with a developer context. The user
  asked not to be consulted on the contents; the coordinator chose them
  (see R38, R41 and R42).
- **Why:** real setups grow over time. The lab shows what such a setup
  adds to the context and whether the overlap changes the result.
- **Cost if wrong:** two more tests per `run all`, each starting fourteen
  MCP servers, and more of the usage limit spent.

### One test on Sonnet 5 at medium effort

**User decision** (2026-10-14).

- **Decided:** one test runs on Sonnet 5 at medium effort; the default
  model stays `claude-haiku-5-5` (see R39 and R40).
- **Why:** to compare a larger model at a set effort with the cheap
  default on the same task.
- **Cost if wrong:** at API prices Sonnet 5 costs 20 times as much per
  token as Haiku. How much more it counts against the usage limit is not
  published; the lab records it.

## Open decisions

- **License and copyright holder.** The repository currently states
  "Copyright (c) 2026 Thinkport GmbH. All rights reserved." No open-source
  license is granted. Whether to publish under an open-source license, and
  under which holder, is not decided yet. While it is open, the repository
  accepts issues, questions and suggestions but no code contributions (pull
  requests).
