# Prompts and goal

A test scenario consists of the prompts Claude gets, the goal that
decides whether a run reached it, and an optional assessment of the
finished app. The follow-ups the runner sends while the goal is not
reached are the same for every scenario.

## Prompts

A prompt file is a text file; `lab/matrix.conf` names it in the `prompt`
column, as a path relative to the lab root (the directory with `lab.py`).
Without the column, or with `-`, a test uses `prompt-simple.md`. The file
is sent to Claude unchanged as the first turn.

A prompt can ask for any app in the `xyz` directory, as long as it asks
for what `prompt-goal.toml` checks.

## Follow-ups

`lab/prompts/followups.txt` holds one follow-up per line; blank lines and
lines starting with `#` are skipped. In a headless run the runner checks
the goal after each turn. While the goal is not reached, it resumes the
session with the next line, until the lines run out. The follow-ups are
the same for every profile, model, effort and scenario, so they must not
refer to a specific task or goal; their turns count in the measured
tokens. A `session` sends no follow-ups.

## Goal

`prompt-goal.toml` in the lab root holds the goal of every test. A
headless run checks it after each turn, a `session` once at the end. Each
`[[check]]` names one type, and every check must pass:

- `pages_live`: the repository's GitHub Pages site answers 200. `wait_s`
  is how long to wait for the Pages build before deciding.
- `homepage_is_pages`: the repository's About -> Website field points to
  the Pages URL. It needs no build, so it takes no wait.

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

## Assessment

`prompt-assessment.toml` in the lab root is optional. When it exists and a
run reached its goal, the lab checks the published app once, after the
last turn: outside `duration_s`, without follow-ups and without changing
the run's status. `results.csv` records the result as `assess_ok` and
`assess_failed` (the failed steps, e.g. `page_ok:no_errors`), and
`python3 lab.py report` shows how many assessed runs passed. Each
`[[check]]` names one type and loads the site fresh in headless Google
Chrome:

- `page_ok`: the page loads without JavaScript errors or failed requests
  and shows content.
- `reacts_to_clicks`: clicking buttons or elements with a click handler
  changes the page, without errors.
- `browser_script`: runs `prompt-assessment.js` from the lab root on the
  loaded page. The script defines
  `window.labAssess = async (document) => ({ step: true, ... })`; every
  step that is not `true` fails.

```toml
[[check]]
type = "page_ok"

[[check]]
type = "reacts_to_clicks"
```

`page_ok` and `reacts_to_clicks` work for any web app; `browser_script` is
for what only the scenario knows, such as the rules of a game. With an
assessment file, `setup`, `run` and `session` need Google Chrome.
