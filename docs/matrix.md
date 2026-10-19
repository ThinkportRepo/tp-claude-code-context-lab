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
  servers) and Google Chrome (the playwright MCP server drives it). Without
  them a run does not fail but measures a smaller context, so the lab
  stops with an error if the matrix uses one of these profiles and either
  is missing. A new profile with MCP servers belongs in `MCP_PROFILES` in
  `lab.py`.
- `prompt`: a file path relative to the lab root (the directory with
  `lab.py`). Without the column, or with `-`, the test uses
  `prompt-simple.md`. A prompt file can ask for any app in the `xyz`
  directory, as long as it asks for what `prompt-goal.toml` checks (see
  below).
- `model`: a model ID. Without the column, or with `-`, the test uses
  `LAB_MODEL` from `.env`, else `claude-haiku-5-5`.
- `effort`: `low`, `medium`, `high`, `xhigh` or `max`, passed to Claude as
  `--effort`. Without it the lab passes no `--effort`.

Use `-` to skip a column and still fill a later one. An error in the file
names the file and line.

## The goal

`prompt-goal.toml` in the lab root holds the goal of every test, the same
for every profile, model and effort. After each turn the runner checks it;
while it is not reached, the runner sends the next follow-up from
`lab/prompts/followups.txt`. Each `[[check]]` names one type, and every
check must pass:

- `pages_live`: the repository's GitHub Pages site answers. `wait_s` is
  how long to wait for the Pages build before deciding.
- `homepage_is_pages`: the repository's About -> Website field points to
  the Pages site.

```toml
[[check]]
type   = "pages_live"
wait_s = 180

[[check]]
type = "homepage_is_pages"
```

Whether the app works is not checked: such a check would change when
follow-ups are sent, and with them the token counts. `python3 lab.py
setup` shows the goal; a missing or invalid file stops `setup`, `run` and
`session`.

## Running tests

Run one test with `python3 lab.py run <id>`, all tests with
`python3 lab.py run all`. `REPEAT=n` in `.env`, or `--repeat n`, runs each
test n times. `python3 lab.py report` summarizes all recorded runs.

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
