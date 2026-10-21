# Test results, 2026-10-21: Tic Tac Toe

> [!IMPORTANT]
> Every run started from a new, empty repository with a small task: a
> browser Tic Tac Toe game, published with GitHub Pages. The numbers
> describe that task only. On an existing code base, for example when
> fixing a bug, context use, token counts and the effect of profiles,
> prompts, models and effort can be completely different.

Raw data: [results.csv](results.csv), 216 runs. As used in the series:
the prompts [prompt-simple.md](prompt-simple.md) and
[prompt-well-defined.md](prompt-well-defined.md), the follow-ups
[followups.txt](followups.txt), the goal
[prompt-goal.toml](prompt-goal.toml), the assessment
[prompt-assessment.toml](prompt-assessment.toml) with
[prompt-assessment.js](prompt-assessment.js), and the test list
[matrix.conf](matrix.conf).

## Setup

- Task: build the game in the `xyz` checkout, publish it with GitHub Pages
  and set the About link. Goal reached = the Pages site answers and the
  About link points to it.
- Assessment, after the goal is reached: the page loads without errors and
  shows content (`page_ok`), clicks change it (`reacts_to_clicks`), and a
  game script plays one game: board found, a click marks a cell, X and O
  alternate, the end is reported, no move after the end
  (`browser_script`).
- If the goal is not met after the prompt, the runner resumes the session
  with up to two follow-ups. All values include these turns.
- Matrix: Haiku, Sonnet and Opus 5.5, each at effort `low`, `medium` and
  `high`, with both prompts on all four profiles: 72 tests, three runs
  each, all headless. Up to five runs ran at the same time.
- The series ran from 2026-10-20 19:00 to 2026-10-21 14:26 (local time),
  with breaks for the usage limits. Seven runs were repeated because the
  GitHub token expired while they ran.
- Claude Code 2.1.304 to 2.1.306 (it updated itself during the series);
  `superpowers` 7.0.0.
- Values are medians; growth also shows min–max in parentheses.
  Context = input + cache-read + cache-write tokens of one API call in the
  main thread: at the first call (start), and last minus first call
  (growth). Output = output tokens of the run. API-equiv = the cost Claude
  Code reports at API list prices; the runs used a subscription.

## Results

By model and effort (each row: 4 profiles × 2 prompts × 3 runs):

| Model | Effort | n | Goal | Follow-ups | Assessment | Start | Growth | Output | Duration | API-equiv |
|---|---|---|---|---|---|---|---|---|---|---|
| Haiku 5.5 | low | 24 | 20 | 4 | 20/20 | 23.0k | 13.8k (4.9–24.4) | 10.0k | 144 s | $0.016 |
| Haiku 5.5 | medium | 24 | 23 | 1 | 23/23 | 23.0k | 23.2k (14.7–33.7) | 16.7k | 232 s | $0.026 |
| Haiku 5.5 | high | 24 | 24 | 3 | 23/24 | 23.0k | 34.8k (19.1–62.3) | 24.5k | 247 s | $0.038 |
| Sonnet 5.5 | low | 24 | 21 | 11 | 21/21 | 22.2k | 10.6k (4.1–27.4) | 6.1k | 112 s | $0.190 |
| Sonnet 5.5 | medium | 24 | 22 | 7 | 21/22 | 22.2k | 11.8k (4.3–24.0) | 7.3k | 99 s | $0.214 |
| Sonnet 5.5 | high | 24 | 24 | 9 | 24/24 | 22.2k | 21.2k (10.7–39.3) | 12.3k | 143 s | $0.353 |
| Opus 5.5 | low | 24 | 24 | 0 | 24/24 | 21.8k | 6.5k (2.9–21.6) | 4.9k | 106 s | $0.315 |
| Opus 5.5 | medium | 24 | 24 | 0 | 24/24 | 21.8k | 17.0k (6.8–32.0) | 9.4k | 139 s | $0.578 |
| Opus 5.5 | high | 24 | 24 | 1 | 24/24 | 21.8k | 22.2k (12.6–41.0) | 13.4k | 194 s | $0.757 |

Total: 216 runs, 206 with the goal reached, 204 of them with the
assessment passed, $61.35 API-equiv.

## Overall comparison

Each table combines all other parameters: a model row covers both
prompts, all profiles and all efforts; an effort row all models, and so
on.

### Models

| Model | n | Goal | Follow-ups | Assessment | Start | Growth | Output | Duration | API-equiv |
|---|---|---|---|---|---|---|---|---|---|
| Haiku 5.5 | 72 | 67 | 8 | 66/67 | 23.0k | 22.5k (4.9–62.3) | 16.1k | 205 s | $0.024 |
| Sonnet 5.5 | 72 | 67 | 27 | 66/67 | 22.2k | 13.0k (4.1–39.3) | 8.1k | 121 s | $0.246 |
| Opus 5.5 | 72 | 72 | 1 | 72/72 | 21.8k | 16.9k (2.9–41.0) | 9.4k | 145 s | $0.578 |

Opus is the most reliable: it reached the goal in all 72 runs, needed a
follow-up once, and every game passed the assessment; Haiku and Sonnet
reached it in 67 of 72. Haiku uses the most context and output (growth
1.3×, output 1.7× Opus) and takes longest, but costs about 4 % of Opus per
run ($2.01 for its 72 runs, against $40.12 for Opus). Sonnet has the
least context growth and output and is the fastest, but needed follow-ups
in 27 of 72 runs, and costs about 43 % of Opus per run.

### Effort

| Effort | n | Goal | Follow-ups | Assessment | Start | Growth | Output | Duration | API-equiv |
|---|---|---|---|---|---|---|---|---|---|
| low | 72 | 65 | 15 | 65/65 | 22.2k | 10.9k (2.9–27.4) | 7.5k | 116 s | $0.168 |
| medium | 72 | 69 | 8 | 68/69 | 22.2k | 17.6k (4.3–33.7) | 9.8k | 139 s | $0.214 |
| high | 72 | 72 | 13 | 71/72 | 22.2k | 24.1k (10.7–62.3) | 13.9k | 187 s | $0.353 |

More effort makes runs more expensive and tends to make them more
reliable: the goal rate rises from 65 over 69 to 72 of 72, and 7 of the 9
`denied` runs were at `low`; follow-ups do not follow effort (15, 8, 13).
Over `low`, `medium` and `high` add growth 1.6× and 2.2×, output 1.3× and
1.9×, cost per run 1.3× and 2.1×, and duration 20 % and 61 %. Effort does
not change the start context.

### Prompts

| Prompt | n | Goal | Follow-ups | Assessment | Start | Growth | Output | Duration | API-equiv |
|---|---|---|---|---|---|---|---|---|---|
| simple | 108 | 101 | 16 | 99/101 | 21.6k | 13.8k (2.9–41.0) | 8.5k | 118 s | $0.170 |
| well-defined | 108 | 105 | 20 | 105/105 | 23.1k | 19.8k (6.1–62.3) | 12.0k | 165 s | $0.303 |

The well-defined prompt costs more and tends to be more reliable: it
reached the goal in 105 against 101 of 108 runs (2 against 7 `denied`),
and all its games passed the assessment, a difference within the spread
of three runs per configuration. It adds 1.5k at start (the longer
prompt), 44 % growth, 42 % output, 39 % duration and 79 % cost per run;
in total $34.82 against $26.53.

### Profiles

| Profile | n | Goal | Follow-ups | Assessment | Start | Growth | Output | Duration | API-equiv |
|---|---|---|---|---|---|---|---|---|---|
| clean | 54 | 52 | 11 | 51/52 | 19.2k | 14.7k (3.3–51.7) | 9.1k | 128 s | $0.200 |
| superpowers | 54 | 53 | 11 | 52/53 | 21.4k | 16.2k (2.9–42.7) | 9.8k | 149 s | $0.227 |
| dev-stack | 54 | 50 | 5 | 50/50 | 23.3k | 20.9k (4.6–62.3) | 12.7k | 174 s | $0.257 |
| dev-messy | 54 | 51 | 9 | 51/51 | 29.6k | 20.3k (4.0–41.7) | 10.2k | 138 s | $0.311 |

More plugins and MCP servers (0, 1, 4 and 12 plugins) raise the start
context and the cost without making runs more reliable. Start context
grows by 2.2k (superpowers), 4.1k (dev-stack) and 10.4k (dev-messy) over
`clean`, and cost per run by 14 %, 29 % and 56 %. Growth is higher with
dev-stack and dev-messy (about 20.5k) than with clean and superpowers
(14.7k and 16.2k); output and duration do not follow the profile size. The goal rate does not improve: 50 to 53 of 54 runs, with
`superpowers` highest and `dev-stack` lowest; differences of this size
are within the spread of three runs per configuration.

## Observations

- **Goal:** Opus reached it in 72/72 runs, Sonnet in 67/72, Haiku in
  67/72. Of the 10 misses, 9 ended `denied` (the agent gave up after
  denied commands, 7 of them with the simple prompt) and one timed out
  (Haiku `medium`, dev-stack, well-defined).
- **Follow-ups:** Sonnet needed them in 27 of 72 runs, Haiku in 8, Opus
  in 1; 27 of the 36 runs with follow-ups still reached the goal.
- **Assessment:** 2 of 206 reached games failed it. One Sonnet game
  (`medium`, superpowers, simple) never shows the marks: its render
  function looks for cells by a class the cells never get. The other
  failure (Haiku `high`, clean, simple, first run) could not be checked
  afterwards, because the next run of that test replaced the game.
- **Denials:** all 216 runs had at least one command with `cd` denied by
  the deny rule `Bash(cd:*)`; most runs recovered, the 9 `denied` runs
  did not within three turns.

## Limitations

- The runs used empty repositories and a small task; see the remark at
  the top.
- n = 24 per model and effort, but only 3 per single configuration
  (profile × prompt × model × effort); see [results.csv](results.csv).
  Growth varies by up to 7.5× within a row. Differences smaller than the
  min–max ranges shown are not reliable.
- Up to five runs ran at the same time, so durations include waiting for
  GitHub (Pages builds) under load and are not comparable with a series
  run one at a time.
- The `cd` deny rule is part of the lab setup and identical in all
  profiles. It was hit in every run, so growth, output and goal rate
  include its retries.
- dev-messy started with 6–11 of 11 MCP servers connected (median 8),
  because headless Claude Code does not wait for MCP servers to start.
- The assessment checks one game of each run; it does not test draws,
  clicks on occupied cells or keyboard play.
