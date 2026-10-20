# The test matrix

`lab/matrix.conf` lists the tests, one per line. Columns are separated by
spaces; lines starting with `#` are comments.

```
id  profile  [prompt]  [model]  [effort]
```

- `id`: the test name. It must start with `test`, followed by letters,
  digits, `_` or `-`. The test works in `sandbox/<id>` and uses the GitHub
  repository `lab-<id>`.
- `profile`: a directory in `lab/profiles/`. Its `settings.json` lists the
  plugins; `mcp.json`, if present, adds MCP servers.
  - `clean`: no plugins, the baseline.
  - `superpowers`: the `superpowers` skill plugin.
  - `dev-stack`: a typical developer setup: four plugins, two with MCP servers.
  - `dev-messy`: twelve plugins and nine more MCP servers, heavily overlapping.

  `dev-stack` and `dev-messy` need Node.js (`npx` starts their MCP
  servers) and Google Chrome (the playwright MCP server drives it).
  Without `npx` the servers do not start; without Chrome the browser tools
  fail. Either way a run does not fail but measures a different session,
  so `setup`, `run` and `session` stop with an error if either is missing
  while the matrix lists a test with one of these profiles, also when the
  selected test uses another profile. A new profile with MCP servers
  belongs in `MCP_PROFILES` in `lab.py`.
- `prompt`: a file path relative to the lab root (the directory with
  `lab.py`). Without the column, or with `-`, the test uses
  `prompt-simple.md`. [prompts-and-goal.md](prompts-and-goal.md)
  describes the prompt files and the goal they have to match.
- `model`: a model ID. Without the column, or with `-`, the test uses
  `LAB_MODEL` from `.env`, else `claude-haiku-5-5`.
- `effort`: `low`, `medium`, `high`, `xhigh` or `max`, passed to Claude as
  `--effort`. Without it the lab passes no `--effort`.

Use `-` to skip a column and still fill a later one. An error in the file
names the file and line.

## Running tests

Run one test with `python3 lab.py run <id>`, all tests with
`python3 lab.py run all`. `REPEAT=n` in `.env`, or `--repeat n`, runs each
test n times. Each run adds a row to `results.csv` in the lab root;
`python3 lab.py report` summarizes all recorded runs. Every run uses the
English locale `en_US.UTF-8`, whatever the machine's, so the operator's
language does not decide the language of the app Claude builds.

Each example below is a complete `lab/matrix.conf`, so `run all` runs
exactly its tests. To keep the existing tests, add the lines instead and
run the new tests by id.

## Example 1: compare profiles

The four profiles on the same prompt, model and effort; `dev-stack` and
`dev-messy` need Node.js and Google Chrome:

```
test-profile-clean        clean        -  claude-haiku-5-5  low
test-profile-superpowers  superpowers  -  claude-haiku-5-5  low
test-profile-dev-stack    dev-stack    -  claude-haiku-5-5  low
test-profile-dev-messy    dev-messy    -  claude-haiku-5-5  low
```

```sh
python3 lab.py run all --repeat 3
python3 lab.py report
```

## Example 2: compare effort

The same model and prompt at three effort levels:

```
test-effort-low     clean  -  claude-haiku-5-5  low
test-effort-medium  clean  -  claude-haiku-5-5  medium
test-effort-high    clean  -  claude-haiku-5-5  high
```

```sh
python3 lab.py run all --repeat 3
python3 lab.py report
```

## Example 3: compare models

Three models at the same effort and prompt:

```
test-model-haiku   clean  -  claude-haiku-5-5   low
test-model-sonnet  clean  -  claude-sonnet-5-5  low
test-model-opus    clean  -  claude-opus-5-5    low
```

```sh
python3 lab.py run all --repeat 3
python3 lab.py report
```

## Example 4: compare prompts

The short prompt against the detailed one, on the same model and effort:

```
test-prompt-simple        clean  prompt-simple.md        claude-haiku-5-5  low
test-prompt-well-defined  clean  prompt-well-defined.md  claude-haiku-5-5  low
```

```sh
python3 lab.py run all --repeat 3
python3 lab.py report
```
