# Contributing

Issues, questions and suggestions are welcome. We don't accept code
contributions (pull requests) while the license is open (see
[LICENSE](LICENSE) and the open decision in
[docs/decisions.md](docs/decisions.md#open-decisions)).

## Conventions for maintainers

- **Tests must pass:**

  ```sh
  python3 -W error -m unittest discover -s lab/tests
  ```

- **Commit messages** follow
  [Conventional Commits](https://www.conventionalcommits.org/), for example
  `fix: keep the report within 120 columns`. The body explains why.
- **AI assistance** is credited with a trailer that names the model and its
  role, for example:

  ```
  Assisted-by: Claude Opus 5.5 (code generation)
  ```

  Do not use `Co-Authored-By` for AI.
- **Python 3.11 or newer, standard library only.** The lab must run right
  after `git clone`.
