# Test results, 2026-10-15

> [!IMPORTANT]
> Every run started from a new, empty repository with a minimal task (a
> static "Hello GitHub Pages" site). The numbers describe that task only.
> On an existing code base, for example when fixing a bug, context use,
> token counts and the effect of profiles, prompts, models and effort can
> be completely different.

Raw data: [results.csv](results.csv), 72 runs; the pilot runs before
them: [results-pilot.csv](results-pilot.csv). The prompts used:
[prompt-simple.md](prompt-simple.md),
[prompt-well-defined.md](prompt-well-defined.md). The goal predates
`prompt-goal.toml` and is described under Setup.

## Setup

- Task: create the site in the `xyz` checkout, publish it with GitHub
  Pages and set the About link. Goal reached = the site shows "Hello
  GitHub Pages" and the About link points to it.
- If the goal is not met after the prompt, the runner resumes the session
  with up to two follow-up prompts (`lab/prompts/followups.txt`). All
  values include these turns. 12 runs used them: the 8 failures, the
  other 2 Sonnet runs with the simple prompt, and 2 Haiku runs.
- Matrix: the four examples in [matrix.md](../../matrix.md), three runs per
  test, all headless.
- Series 1 (ids `test-*`) ran the examples as in matrix.md. Series 2 (ids
  `test2-*`) ran examples 1–3 with `prompt-well-defined.md` instead of
  `prompt-simple.md` (both as copied in this folder); example 4 ran
  unchanged.
- Tests with identical settings in different examples are grouped. The
  baseline (`clean`, Haiku, `low`) has n = 15 per prompt; every other row
  has n = 3.
- Claude Code 2.1.300; it updated itself to 2.1.301 during series 2 (14
  runs on 2.1.301, one run spans both).
- Values are medians; growth also shows min–max in parentheses.
  Context = input + cache-read + cache-write tokens of one API call in the
  main thread: at the first call (start), and last minus first call
  (growth). Output = output tokens of the run. API-equiv = the cost Claude
  Code reports at API list prices; the runs used a subscription.

## Results

| Profile | Model | Effort | Prompt | n | Goal | Start | Growth | Output | API-equiv |
|---|---|---|---|---|---|---|---|---|---|
| clean | Haiku 5.5 | low | simple | 15 | 13 | 18.9k | 7.1k (5.3–13.1) | 4.6k | $0.008 |
| clean | Haiku 5.5 | low | well-defined | 15 | 13 | 19.5k | 8.9k (2.8–20.4) | 5.2k | $0.010 |
| clean | Haiku 5.5 | medium | simple | 3 | 3 | 18.9k | 10.5k (7.5–13.6) | 7.6k | $0.013 |
| clean | Haiku 5.5 | medium | well-defined | 3 | 3 | 19.5k | 15.4k (12.4–17.0) | 7.8k | $0.016 |
| clean | Haiku 5.5 | high | simple | 3 | 3 | 18.9k | 11.4k (11.0–13.3) | 7.7k | $0.013 |
| clean | Haiku 5.5 | high | well-defined | 3 | 3 | 19.4k | 15.3k (13.8–17.6) | 8.2k | $0.015 |
| superpowers | Haiku 5.5 | low | simple | 3 | 2 | 21.1k | 9.1k (8.1–10.2) | 6.8k | $0.011 |
| superpowers | Haiku 5.5 | low | well-defined | 3 | 3 | 21.6k | 10.4k (8.1–13.7) | 7.0k | $0.014 |
| dev-stack | Haiku 5.5 | low | simple | 3 | 3 | 23.0k | 8.3k (7.9–8.7) | 5.5k | $0.009 |
| dev-stack | Haiku 5.5 | low | well-defined | 3 | 2 | 23.5k | 9.5k (3.9–11.4) | 5.2k | $0.012 |
| dev-messy | Haiku 5.5 | low | simple | 3 | 2 | 29.1k | 8.6k (7.6–18.7) | 5.7k | $0.013 |
| dev-messy | Haiku 5.5 | low | well-defined | 3 | 3 | 29.6k | 17.4k (9.6–25.9) | 5.8k | $0.018 |
| clean | Sonnet 5.5 | low | simple | 3 | 2 | 18.2k | 14.9k (3.7–20.3) | 5.0k | $0.228 |
| clean | Sonnet 5.5 | low | well-defined | 3 | 3 | 18.7k | 13.5k (13.2–21.5) | 3.7k | $0.169 |
| clean | Opus 5.5 | low | simple | 3 | 3 | 17.8k | 3.2k (3.2–10.7) | 2.7k | $0.121 |
| clean | Opus 5.5 | low | well-defined | 3 | 3 | 18.3k | 7.7k (3.3–8.8) | 2.9k | $0.193 |

Total: 72 runs, 64 with the goal reached, $2.93 API-equiv.

## Observations

- **Profiles:** start context grows with the plugins and MCP servers a
  profile loads: +2.2k (superpowers), +4.0k (dev-stack), +10.2k
  (dev-messy) over `clean`. Median growth is 7.1–9.1k (simple) and
  8.9–10.4k (well-defined) for all profiles, except dev-messy with the
  well-defined prompt: 17.4k (range 9.6–25.9k, n = 3).
- **Effort:** on Haiku, `medium` and `high` add 48–74 % growth and
  49–68 % output over `low`. Goal reached in 12/12 runs (`low`: 26/30); n
  is too small to attribute this to effort. `medium` and `high` differ
  little.
- **Prompts:** the well-defined prompt adds 0.5k at start. Median growth is
  higher in 7 of 8 configurations: by 14–26 % on Haiku `low` (clean,
  superpowers, dev-stack), 34–48 % on Haiku `medium`/`high`, 2.0× on
  dev-messy and 2.4× on Opus; Sonnet is 9 % lower. The growth ranges of
  the two prompts overlap in 7 of 8 configurations; only Haiku `high`
  (+34 %) separates. These are tendencies, not established effects.
  Goal rate: simple 31/36, well-defined 33/36.
- **Models:** Opus used the least growth and output and reached the goal
  in 6/6 runs. Sonnet used the most growth; with the simple prompt all
  three runs needed follow-up turns. Median cost per run against Haiku:
  Opus 15× (simple) and 19× (well-defined), Sonnet 28× and 17×.
- **Denials:** 71 of 72 runs had at least one `cd <dir> && …` command
  denied by the deny rule `Bash(cd:*)`: all 8 failed runs and 63 of the
  64 successful ones. The successful runs recovered (for example with
  `git -C`); the failed runs did not within three turns. One failed run
  also had `rm` denied (not on the allow list; headless runs cannot ask
  for permission).

## Limitations

- The runs used empty repositories and a minimal task; see the remark at
  the top.
- n = 3 for most rows. Growth varies by up to 7× within a group (2× or
  more in 8 of 16 groups). Differences smaller than the min–max ranges
  shown are not reliable.
- The `cd` deny rule is part of the lab setup and identical in all
  profiles. It was hit in 71 of 72 runs, so growth, output and goal rate
  include its retries.
- dev-messy started with 7–8 of 11 MCP servers connected, because headless
  Claude Code does not wait for MCP servers to start.
