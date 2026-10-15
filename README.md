# Claude Code Context Lab

The lab measures how much context and how many tokens Claude Code uses for
the same task under different setups: plugins, skills and MCP servers,
prompt, model and effort. You compare setups with measurements instead of
guesses.

## Requirements

- A Claude subscription (not API billing) and the Claude Code CLI (`claude`)
- Python 3.11 or newer (standard library only)
- `git` and the GitHub CLI (`gh`)
- A new, empty GitHub organization in which you are an admin
- Node.js with `npx` and Google Chrome, for the `dev-stack` and
  `dev-messy` profiles

## Quick start

> [!WARNING]
> The lab creates **public** test repositories, so use an organization
> that holds nothing else. The lab's tokens live in `.env`; revoke them with
> `python3 lab.py teardown --secrets` when you are done.

Run these from the repository root:

```sh
python3 lab.py tokens claude          # create the Claude token, store it in .env
# set LAB_GH_ORG=<organization> in .env by hand (see .env.example)
python3 lab.py tokens github          # create a fine-grained GitHub token (1 day), store it
python3 lab.py tokens github --check  # create, push, publish and delete a test repo
python3 lab.py setup                  # check GitHub access and create sandbox dirs
python3 lab.py run test1              # run one test headless, record it in results.csv
python3 lab.py report                 # summarize results.csv
python3 lab.py session test3          # optional: run a test interactively in this terminal
python3 lab.py teardown --secrets     # delete lab repos and sandbox, revoke the tokens
```

`python3 lab.py --help` lists all commands and options.

## Configure tests

Each line of `lab/matrix.conf` is one test: an id, a profile, and
optionally a prompt file, a model and an effort level.

```
test1    clean        prompt-simple.md
test2    clean        prompt-well-defined.md
test9    clean        prompt-simple.md        claude-sonnet-5   medium
```

[docs/matrix.md](docs/matrix.md) describes the columns and has examples.

## Documentation

- [docs/design.md](docs/design.md): what the lab measures and how, with measurements
- [docs/matrix.md](docs/matrix.md): the test matrix, with examples
- [docs/decisions.md](docs/decisions.md): the decisions, with reasons
- [docs/implementation-history.md](docs/implementation-history.md): how the lab was built

## Contributing and security

Ask questions and report problems in the GitHub issues; see
[CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md) and
[ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md). Thinkport GmbH maintains the lab.

## License

All rights reserved (see [LICENSE](LICENSE)); the repository is public so
you can read it and follow the measurement, but using or reproducing the
code requires permission from Thinkport GmbH.
