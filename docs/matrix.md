# The test matrix

`lab/matrix.conf` lists the tests, one per line. Columns are separated by
spaces; lines starting with `#` are comments.

```
id  profile  [prompt]  [model]  [effort]
```

- `id`: the test name. It must start with `test`, followed by letters,
  digits, `_` or `-`. The test works in `sandbox/<id>` and uses the GitHub
  repository `lab-<id>`.
- `profile`: a directory in `lab/profiles/`; [design.md](design.md#profiles)
  lists their plugins.
  - `clean`: no plugins, the baseline.
  - `superpowers`: the `superpowers` skill plugin.
  - `dev-stack`: a typical developer setup: four plugins, two with MCP servers.
  - `dev-messy`: twelve plugins and nine more MCP servers, heavily overlapping.
- `prompt`: a file path relative to the lab root (the directory with
  `lab.py`). Without the column, or with `-`, the test uses
  `prompt-simple.md`.
- `model`: a model ID. Without the column, or with `-`, the test uses
  `LAB_MODEL` from `.env`, else `claude-haiku-5-5`.
- `effort`: `low`, `medium`, `high`, `xhigh` or `max`, passed to Claude as
  `--effort`. Without it the lab passes no `--effort`.

Use `-` to skip a column and still fill a later one. An error in the file
names the file and line.

Run one test with `python3 lab.py run <id>`, all tests with
`python3 lab.py run all`. `REPEAT=n` in `.env`, or `--repeat n`, runs each
test n times. `python3 lab.py report` summarizes all recorded runs.

Each example below is a complete `lab/matrix.conf`, so `run all` runs
exactly its tests. To keep the existing tests, add the lines instead and
run the new tests by id.

## Example 1: compare effort

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

## Example 2: compare models

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

## Example 3: compare prompts

The short prompt against the detailed one, on the same model and effort:

```
test-prompt-simple        clean  prompt-simple.md        claude-haiku-5-5  low
test-prompt-well-defined  clean  prompt-well-defined.md  claude-haiku-5-5  low
```

```sh
python3 lab.py run all --repeat 3
python3 lab.py report
```
