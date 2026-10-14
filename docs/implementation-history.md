# Implementation history

This file summarizes how the lab was built: the tasks in order, what each
delivered, and what changed once the lab ran against real accounts. The
resulting design is in [design.md](design.md); every ruling and user
decision is in [decisions.md](decisions.md).

## How it was built

The lab was designed, planned and built with the
[superpowers](https://github.com/obra/superpowers) skills: brainstorming
for the design, a written plan, and subagent-driven development. A
coordinating agent handed each task to an implementing agent and had the
result checked by a separate review agent (spec compliance, then code
quality) before the next task started. Findings were either fixed in a
fix round or recorded as deferred; a whole-branch review and one fix wave
came at the end.

Steps that need a browser or spend subscription usage were run by a
person together with the coordinator (R2). Commit trailers name the model
that wrote each change, as `Assisted-by: <model> (code generation)` (user
decision, R7); there is no fixed trailer and no `Co-Authored-By` line.

Global rules from the plan, kept throughout:

- Python ≥ 3.11, standard library only; tests with `unittest`.
- Every model call passes `--model` and `--permission-mode default`; never
  `bypassPermissions`, never `--bare`.
- Secrets reach child processes only through environment variables, never
  argv, and are printed only masked.
- One shared permission list for all profiles; denials are measured.

## Tasks

### Task 1 — GitHub App token and lifecycle check (spike S5)

Delivered `.env` loading and editing, a `gh`/`git` wrapper scoped to one
organization, and `lab.py tokens github --check`: create a repository,
commit, push over HTTPS, enable Pages, check the page, delete the
repository. The first token flow was a GitHub App with 8-hour user tokens
through the device flow.

Live: the check passed with the App token in 50 s. A defect surfaced live:
GitHub App names may have at most 34 characters (fixed with Task 1b, R10).

### Task 1b — Fine-grained PAT

Planned only as a fallback if the App failed. It ran because the person
asked for it after the App flow needed too many browser steps (user
decision, R9). Delivered a pre-filled PAT form URL and a hidden paste of
the token, stored with a 24-hour expiry.

Live: the check passed with the PAT in 60 s, and the organization was
empty afterwards. The form kept every pre-filled value except *Repository
access*, which has no URL parameter.

### Task 1c — PAT only

After the comparison, the user chose the PAT only. The App flow and its
options were removed (R11), and the PAT gained the Workflows (write)
permission (user decision).

Live: token names must be unique per account, and the form sometimes drops
pre-filled permissions. The token name got a random suffix, and the lab
now prints a checklist to compare against the form.

### Task 2 — Preflight

Delivered the checks that run once before every command that touches
anything: tools on `PATH`, `.env` mode `600` and not tracked by git, the
Claude token, the organization, and a GitHub token that outlives the run
timeout. Messages name the command that fixes the problem and never
contain a secret. A warning flags Claude project files in parent
directories of the sandbox.

### Task 3 — Matrix, profiles, prompts

Delivered `lab/matrix.conf`, the three profiles with their shared
permission lists, the follow-up prompts, and the parser with model
precedence (column, then `LAB_MODEL`, then the default) and line-numbered
errors.

### Task 4 — Sandbox and status line

Delivered the per-test directories, the isolated child environment, the
fresh config dir with seeded onboarding and trust, the status-line script,
and the repository reset. A review found that the reset's directory guard
could wipe another test's checkout; it now accepts only the test's own
directory (R14).

### Task 5 — Metrics from transcripts

Delivered metric extraction from transcripts against recorded, sanitized
fixtures: de-duplication by message ID, totals from `cost-state` per
process, subagent context, and turn counting that skips internal
notifications. A review found that synthetic assistant entries (API errors,
limit notices) counted as API calls with zero context; they are now
skipped.

### Task 6 — Results file and report

Delivered `results.csv` with a fixed schema and the comparison report
(median, min–max). The table was narrowed and restructured to fit a
120-column terminal at realistic magnitudes (R15–R18).

### Task 7 — Usage-limit gate and headless runner

Delivered the probe and gate, `lab.py run`, and a fake `claude` for tests.
Every run writes a row, also on timeout, failure and Ctrl+C.

The first live runs changed several things:

- **Pages workflow builds.** Claude chose to deploy through GitHub Actions.
  For such sites the Pages API reports `status: null` while the site is
  live, so the check never succeeded. The check now accepts workflow builds
  once the page shows the text.
- **Resumed sessions.** `--resume` keeps the session ID and continues its
  cumulative usage. The transcript totals were right; the cross-check
  against the stream's `result` events added processes up and was fixed.
- **`.git` boundary.** Claude Code ignored `GIT_CEILING_DIRECTORIES` and saw
  the lab repository's branch, recent commits and memory path. After probes
  of several alternatives, each test directory got a `.git` boundary file
  (user decision, R22). The runner now warns if a run still sees the lab
  repository, and saves each turn's stderr (R23).
- **Plugin install.** Plugins listed in `enabledPlugins` were not installed
  in a fresh config dir. Each run now installs them with
  `claude plugin install` before the first turn, outside the run's timeout.
- **Marketplace add.** `plugin install` does not fetch a marketplace, and a
  fresh config dir knows none. Each run now adds (or updates) the official
  marketplace first.
- **context7 swap.** context7's MCP server needed an OAuth login and never
  started. It was replaced by `microsoft-docs` (user decision); the MCP
  allow rules were confirmed from a live run. MCP tools turned out to be
  deferred: only their names enter the starting context.

### Task 8 — Interactive runner

Delivered `lab.py session`, which hands the terminal to `claude` and records
the run after `/exit`, with the end limit taken from the saved status line.
`--no-prompt` starts an inspection session that records nothing (R25).

Live: the person checked a `superpowers` session by hand: no onboarding,
the pinned model, only the profile's plugins plus Claude Code's built-in
ones, permission mode `default`, a working status line.

### Task 9 — Setup, report, teardown

Delivered `lab.py setup` (organization access, Playwright browser, sandbox
directories, the matrix and the limit), `lab.py report`, and
`lab.py teardown` with token revocation.

### Task 10 — Dry run

All six tests ran once headless: four `ok`, two `denied` by the `cd` deny
rule. The results are in [design.md](design.md#dry-run-2026-10-13).

## Final review and fix wave

A review of the whole branch found one critical issue: the Claude
revocation check ran in a config dir that did not exist and could report a
live token as revoked. Further findings: teardown required credentials it
had just removed, test ids could point outside the sandbox, `tokens claude`
showed the new token on screen, newcomers lacked guidance, and the report
had no token-consumption table.

The fix wave delivered:

- a teardown that works with whatever is left, with its own smaller set of
  requirements;
- validated test ids and profile names, and guards before anything is
  deleted;
- token masking on screen and a cleared screen after `tokens claude`;
- step-by-step `--help` text, a commented `.env.example` and remedies in
  error messages;
- the token-consumption table, and "5h …% used" in the status line;
- process groups for headless runs, so a timeout also ends the processes
  Claude started;
- tests that tell apart which duplicate entry wins.

After the fix wave, all 18 runs of a `REPEAT=3` measurement were made
([design.md](design.md#representative-run-2026-10-13-repeat3)).

## Round 2 — goal change and publishing

- **Goal change.** The goal now includes linking the site in the
  repository's About section (user decision). Both prompts ask for it, and
  the success field is `goal_ok` (R31). The `REPEAT=3` numbers were measured
  before this change.
- **Proven revocation.** A token counts as revoked only when a check with
  the old token proves it (user decision): an unambiguous authentication
  failure for Claude, HTTP 401 from `GET /user` for GitHub (R32). Teardown
  removes the tokens from `.env` in every outcome but exits 1 unless both
  are proven revoked (R33).
- **Shorter waits.** The goal check waits only while the site is not yet
  live (R34).
- **Naming.** `root` became `lab_root` (user decision).
- **Publishing.** Personal and local details were removed from the current
  files; the design, plan and rulings were condensed into the three files
  in `docs/`; the repository got a README and boilerplate files (user
  decision).
