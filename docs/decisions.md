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
The rulings R1–R34 are numbered in the order they were made. Decisions the
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

## Open decisions

- **License and copyright holder.** The repository currently states
  "Copyright (c) 2026 Thinkport GmbH. All rights reserved." No open-source
  license is granted. Whether to publish under an open-source license, and
  under which holder, is not decided yet. While it is open, the repository
  accepts issues, questions and suggestions but no code contributions (pull
  requests).
