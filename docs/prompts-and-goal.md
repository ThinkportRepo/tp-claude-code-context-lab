# Prompts and goal

A test scenario consists of three kinds of files: the prompts Claude gets,
the follow-ups the runner sends while the goal is not reached, and the
goal that decides whether a run reached it.

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
the same for every profile, model and effort, and their turns count in
the measured tokens. A `session` sends no follow-ups.

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
