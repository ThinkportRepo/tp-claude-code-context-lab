# Claude Code Context Lab

The lab measures the context size and token use of Claude Code setups. It
runs the same task under different profiles (which plugins are installed)
and prompts (short or detailed), reads the session transcripts, and
reports the numbers per profile and prompt. It is a demo for Thinkport AI
Café No. 7.

## Why it is useful

Plugins, skills and MCP servers all add to Claude Code's context. The lab
shows how much each setup adds at the start and how much the task itself
consumes, so you can compare setups with measurements instead of guesses.

## Getting started

### Prerequisites

- A Claude subscription and the Claude Code CLI (`claude`). The lab uses
  the subscription, not API billing.
- Python 3.11 or newer. The lab uses the standard library only.
- `git` and the GitHub CLI (`gh`).
- Node.js with `npx`, if `lab/matrix.conf` contains a `dev-stack` or
  `dev-messy` test (the default file does). `setup`, `run` and `session`
  need it then.
- Google Chrome, for the browser MCP servers in the `dev-stack` and
  `dev-messy` profiles. The `playwright` plugin's MCP server uses the
  installed Chrome (verified 2026-10-14). `setup` warns if it is missing.
- A new, empty GitHub organization in which you are an admin. Create it in
  the browser; `gh` cannot create organizations.

### Quick start

Run these from the repository root, in this order:

```sh
# 1. Create the Claude token (one browser approval); stored in .env.
python3 lab.py tokens claude

# 2. Add your organization to .env by hand (see .env.example):
#    LAB_GH_ORG=your-lab-org

# 3. Create a fine-grained GitHub token, valid 1 day. Set "Repository
#    access" to "All repositories" in the pre-filled form and paste the token.
python3 lab.py tokens github

# 4. Check the token: create, push, publish and delete a test repository.
python3 lab.py tokens github --check

# 5. Check access (and for Google Chrome), create the sandbox.
python3 lab.py setup

# 6. Run one test headless and record it in results.csv.
python3 lab.py run test1

# 7. Show the comparison tables.
python3 lab.py report

# 8. Optional: run a test interactively in your terminal (/exit ends it).
python3 lab.py session test3

# 9. When you are done: delete the lab's repositories and sandbox, and
#    revoke both tokens.
python3 lab.py teardown --secrets
```

Run `python3 lab.py --help`, or `python3 lab.py <command> --help`, for all
commands and options.

Before you start:

- The lab creates **public** test repositories. Use a new, empty
  organization that holds nothing else.
- The Claude token is valid for one year, the GitHub token for one day.
  Both are stored in `.env` (mode 600, not committed).
- Run `python3 lab.py teardown --secrets` after the lab to revoke the
  tokens.

### Configure the test matrix

`lab/matrix.conf` lists one test per line, with the columns
`id profile prompt [model] [effort]`. A profile is a directory in
`lab/profiles/`; the prompt is `simple` (`lab/prompts/simple.txt`) or
`well-defined` (`well-defined-prompt`). Without a model, or with `-` in
the model column, a test uses `LAB_MODEL` from `.env`, else
`claude-haiku-5-5`. `effort` is `low`, `medium`, `high`, `xhigh` or `max`;
without it the lab passes no `--effort`. `REPEAT=n` in `.env` (or
`run --repeat n`) runs each test n times.

Example; the third line sets an effort for the default model:

```
test1    clean        simple
test9    clean        simple        claude-sonnet-5   medium
test10   dev-messy    simple        -                 high
```

The profiles:

- `clean`: no plugins, the baseline.
- `superpowers`: the `superpowers` skill plugin.
- `dev-stack`: a typical developer setup of four popular plugins, two of
  them with MCP servers.
- `dev-messy`: a stress test for a setup that has grown over time: ten
  skill plugins, three MCP plugins and twelve more MCP servers, with heavy
  overlap between them.

## Documentation

- [docs/design.md](docs/design.md): what the lab measures and how,
  including the measurements
- [docs/decisions.md](docs/decisions.md): the decisions, with reasons
- [docs/implementation-history.md](docs/implementation-history.md): how
  the lab was built
- [SECURITY.md](SECURITY.md): credentials, recommendations, reporting a
  vulnerability
- [ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md)

## Help and maintainers

Ask questions and report problems in the GitHub issues of this repository.
Thinkport GmbH maintains the lab.

## License

All rights reserved (see [LICENSE](LICENSE)); the repository is public so
you can read it and follow the measurement, but using or reproducing the
code requires permission from Thinkport GmbH.
