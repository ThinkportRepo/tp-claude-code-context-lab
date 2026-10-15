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

Run the commands from the repository root, one step at a time.

1. Create the Claude token and store it in `.env`:

   ```sh
   python3 lab.py tokens claude
   ```

2. Set your organization in `.env` by hand (see `.env.example`):

   ```
   LAB_GH_ORG=<organization>
   ```

3. Create a fine-grained GitHub token (valid 1 day) and store it:

   ```sh
   python3 lab.py tokens github
   ```

4. Check the token: create, push, publish and delete a test repo:

   ```sh
   python3 lab.py tokens github --check
   ```

5. Check GitHub access and create the sandbox dirs:

   ```sh
   python3 lab.py setup
   ```

6. Run one test headless and record it in `results.csv`:

   ```sh
   python3 lab.py run test1
   ```

7. Summarize `results.csv`:

   ```sh
   python3 lab.py report
   ```

8. Optional: run a test interactively in this terminal (`/exit` ends it):

   ```sh
   python3 lab.py session test3
   ```

9. When you are done: delete the lab repos and sandbox, revoke the tokens:

   ```sh
   python3 lab.py teardown --secrets
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
