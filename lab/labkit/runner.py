"""One recorded run of a test: reset, `claude` turns, goal check, collect.

The goal comes from the goal file (`config.load_goal`, `prompt-goal.toml`)
and is checked by `GitHub.goal_ok`. Whether the app works is not part of
the goal: once the goal is reached, the optional assessment
(`prompt-assessment.toml`, `assess.run`) checks the app, outside the
measured time and without follow-ups.

`run_headless` drives `claude -p`; `run_interactive` hands the terminal to
a human. Every run appends exactly one row to `<lab_root>/results.csv`, also
when it times out, fails, or is interrupted with Ctrl+C (an interactive
session without a prompt records nothing). A run's output lands in
`sandbox/<test>/results/<timestamp>/`: one `stream-<n>.jsonl` (stdout) and
`stderr-<n>.txt` per `claude` process and a copy of the transcripts
(`projects/`).
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from . import assess, config, metrics, preflight, results, sandbox
from .github import GitHub

RUN_TIMEOUT_S = 1200
KILL_GRACE_S = 5  # after SIGTERM, before SIGKILL
FOLLOWUPS_FILE = Path("lab") / "prompts" / "followups.txt"
STATUS_FILE = "statusline.json"
RESULTS_FILE = "results.csv"


class _RunTimeout(Exception):
    """The run's time budget ran out while (or before) `claude` ran."""


def _model_flags(model: str, effort: str | None) -> list[str]:
    """`--model M`, plus `--effort E` only when the test sets an effort."""
    return ["--model", model, *(["--effort", effort] if effort else [])]


def _model_label(test: config.Test) -> str:
    """The model for progress lines, with the effort if the test sets one."""
    return f"{test.model}, effort {test.effort}" if test.effort else test.model


def headless_argv(prompt: str, model: str, resume: str | None = None, *, effort: str | None = None) -> list[str]:
    """argv of a headless `claude` call with the lab's pinned flags.

    `--effort` is passed only when `effort` is set. The prompt is not a
    secret, so it travels in argv; credentials only ever reach `claude`
    through its environment.
    """
    argv = ["claude", "-p", prompt]
    if resume:
        argv += ["--resume", resume]
    return argv + [
        *_model_flags(model, effort), "--permission-mode", "default",
        "--output-format", "stream-json", "--verbose",
    ]  # fmt: skip


def _stop_group(proc: subprocess.Popen) -> None:
    """End proc and everything it started: SIGTERM to its process group, SIGKILL after a grace."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            break  # the whole group is gone
        if sig == signal.SIGTERM:
            try:
                proc.wait(timeout=KILL_GRACE_S)
            except subprocess.TimeoutExpired:
                pass
    proc.wait()


def _run_claude(argv: list[str], *, cwd: Path, env: dict[str, str], stdout, stderr, timeout: float) -> int:
    """Run one headless `claude` in a session of its own; return its exit code.

    Tool processes it starts share its process group, so on timeout or
    interrupt the whole group is stopped and nothing outlives the run.
    Raises subprocess.TimeoutExpired.
    """
    proc = subprocess.Popen(
        argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
        start_new_session=True,
    )  # fmt: skip
    try:
        return proc.wait(timeout=timeout)
    except BaseException:
        _stop_group(proc)
        raise


def _totals_mismatch(row: dict, result_totals: dict | None) -> str:
    """'key a vs b, …' for each token total where transcripts and stream results differ."""
    if not result_totals:
        return ""
    return ", ".join(
        f"{key} {row.get(key)} vs {value}" for key, value in result_totals.items() if row.get(key) != value
    )


def fresh_config(
    lab_root: Path, test: config.Test, p: SimpleNamespace, out_dir: Path, env: dict[str, str]
) -> dict[str, str]:
    """Lifecycle step 3, config part: the results dir, a fresh config dir, its plugins.

    Returns the installed plugin versions. `env` is the run's child_env.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    sandbox.build_config(lab_root, test, p, out_dir / STATUS_FILE)
    versions = sandbox.install_plugins(p.config, sandbox.profile_plugins(lab_root, test.profile), env)
    for key, version in versions.items():
        print(f"{test.id}: installed {key} {version}")
    return versions


def reset_repo(lab_root: Path, test: config.Test, gh: GitHub, p: SimpleNamespace) -> None:
    """Lifecycle step 3, repo part: an empty GitHub repo and local checkout."""
    print(f"{test.id}: resetting repo lab-{test.id}")
    sandbox.reset_repo(gh, test.id, p.repo, lab_root=lab_root)


def collect(p: SimpleNamespace, out_dir: Path) -> dict:
    """Metrics from the run's config dir; copies its transcripts to out_dir."""
    row = metrics.compute(p.config)
    row["plugin_versions"] = sandbox.plugin_versions(p.config)
    projects = p.config / "projects"
    if projects.is_dir():
        shutil.copytree(projects, out_dir / "projects", dirs_exist_ok=True)
    return row


def _shown(lab_root: Path, path: Path) -> Path:
    """`path` relative to the lab root if it lies inside it, so it can be opened from there."""
    try:
        return path.relative_to(lab_root.resolve())
    except ValueError:
        return path


def lab_branch(lab_root: Path) -> str | None:
    """The lab repo's current branch, or None if unknown or detached.

    Runs git with the user's own environment on purpose: this asks about
    the lab repo itself, not about anything the sandbox can see.
    """
    try:
        cp = subprocess.run(
            ["git", "-C", str(lab_root), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=30,
        )  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return None
    branch = cp.stdout.strip()
    # A detached lab repo also reports `HEAD`, the sandbox's normal value.
    return branch if cp.returncode == 0 and branch and branch != "HEAD" else None


def warn_if_lab_repo_seen(lab_root: Path, test_id: str, config_dir: Path) -> None:
    """Warn if the run's transcripts carry the lab repo's branch.

    The sandbox hides the lab repo with a `.git` boundary file, which relies
    on undocumented Claude Code behavior; this catches a regression. The
    run's row stays as it is.
    """
    branch = lab_branch(lab_root)
    if branch and branch in metrics.git_branches(config_dir):
        print(
            f"warning: {test_id} saw the lab repo (gitBranch {branch})"
            " — token counts include lab git context",
            file=sys.stderr,
        )


def record(lab_root: Path, row: dict) -> dict:
    results.append(lab_root / RESULTS_FILE, row)
    print(f"{row['test']}: recorded as {row['status']} in {RESULTS_FILE}")
    return row


def _new_row(stamp: str, test: config.Test, mode: str, limit_start: int | None) -> dict:
    row = dict.fromkeys(results.FIELDS)
    row |= {
        "timestamp": stamp, "test": test.id, "mode": mode, "model": test.model,
        "effort": test.effort, "profile": test.profile, "prompt": test.prompt,
        "limit_5h_start_pct": limit_start,
    }  # fmt: skip
    return row


def statusline_limit(path: Path) -> int | None:
    """5-hour limit used, in percent, from a saved status-line JSON; None if missing."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    for key in ("rate_limits", "five_hour", "used_percentage"):
        data = data.get(key) if isinstance(data, dict) else None
    if isinstance(data, bool) or not isinstance(data, (int, float)):
        return None
    return round(data)


def _goal_ok(test: config.Test, gh: GitHub, goal: config.Goal, wait_s: int) -> bool:
    """Check `goal` for the test's repo, waiting up to wait_s for a Pages build."""
    if wait_s:
        print(f"{test.id}: waiting up to {wait_s} s for the GitHub Pages site to go live")
    return gh.goal_ok(f"lab-{test.id}", wait_s, live=goal.pages_live, homepage=goal.homepage_is_pages)


def _build_wait(test: config.Test, gh: GitHub, goal: config.Goal) -> int:
    """The goal's Pages wait if Pages is configured; without Pages there is no build to wait for."""
    if not goal.pages_wait_s or gh.pages_status(f"lab-{test.id}") is None:
        return 0
    return goal.pages_wait_s


def _assess(lab_root: Path, test: config.Test, gh: GitHub, assessment: config.Assessment) -> dict:
    """Run `assessment` against the test's Pages site; the row's assess_ok and assess_failed.

    Never fails the run: an error on the way (GitHub, Chrome, the proxy)
    fails the assessment as "assessment:error".
    """
    print(f"{test.id}: assessing the app ({config.describe_assessment(assessment)})")
    try:
        status = gh.pages_status(f"lab-{test.id}")
        url = status.get("html_url") if status else None
        chrome = preflight.chrome_path(Path.home())
        if not url or not chrome:
            failed, errors = [f"assessment:{'no_site' if not url else 'no_chrome'}"], []
        else:
            failed, errors = assess.run(assessment, url, lab_root, chrome)
    except Exception as exc:  # whatever fails here fails the assessment, never the run
        print(f"{test.id}: assessment error: {exc}")
        failed, errors = ["assessment:error"], []
    for error in errors[:3]:
        print(f"{test.id}: page error: {error}")
    print(f"{test.id}: assessment {'passed' if not failed else 'failed: ' + ', '.join(failed)}")
    return {"assess_ok": not failed, "assess_failed": ";".join(failed)}


def _status(*, failed: bool, timed_out: bool, goal_ok: bool | None, denied: bool | None) -> str:
    if failed:
        return "failed"
    if timed_out:
        return "timeout"
    if goal_ok:
        return "ok"
    if denied:
        return "denied"
    return "failed"


def run_headless(
    lab_root: Path,
    test: config.Test,
    cfg: dict[str, str],
    gh: GitHub,
    *,
    goal: config.Goal,
    assessment: config.Assessment | None = None,
    limit_start: int | None,
    timeout_s: int = RUN_TIMEOUT_S,
) -> dict:
    """Run `test` headless (lifecycle steps 3–7) and return its results row.

    Turn 1 sends the prompt; while `goal` is not met and follow-up lines
    are left, the session is resumed with the next line. Once Pages is
    configured the runner waits up to the goal's `pages_wait_s` for the
    build before it decides, so a deployment in progress never triggers a
    follow-up.
    `timeout_s` is the budget for the run; each `claude` process gets what
    is left of it. The clock (budget and `duration_s`) starts right before
    turn 1, as in an interactive session, so neither plugin installation
    nor the repo reset counts against a profile. Once the goal is reached,
    `assessment` (if any) runs; its time is not part of `duration_s`.
    """
    started = deadline = ended = None
    stamp = datetime.now(timezone.utc).strftime(results.TIMESTAMP_FORMAT)
    p = sandbox.paths(lab_root, test.id)
    out_dir = p.results / stamp  # its name is the row's timestamp
    row = _new_row(stamp, test, "headless", limit_start)
    streams: list[Path] = []
    failed = timed_out = False
    goal_ok = None

    def turn(prompt: str, resume: str | None, env: dict[str, str]) -> str | None:
        """Run one `claude` process; return the session id it reports."""
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _RunTimeout
        n = len(streams) + 1
        stream, stderr = out_dir / f"stream-{n}.jsonl", out_dir / f"stderr-{n}.txt"
        streams.append(stream)
        with stream.open("wb") as out, stderr.open("wb") as err:
            try:
                code = _run_claude(
                    headless_argv(prompt, test.model, resume, effort=test.effort),
                    cwd=p.work, env=env, stdout=out, stderr=err, timeout=remaining,
                )  # fmt: skip
            except subprocess.TimeoutExpired:
                raise _RunTimeout from None
        if code != 0:
            print(f"{test.id}: claude exited {code} (see {_shown(lab_root, stderr)})", file=sys.stderr)
        return metrics.stream_info([stream])["session_id"]

    try:
        env = sandbox.child_env(os.environ, lab_root, p.config, cfg)
        fresh_config(lab_root, test, p, out_dir, env)
        reset_repo(lab_root, test, gh, p)
        followups = config.load_followups(lab_root / FOLLOWUPS_FILE)
        print(f"{test.id}: turn 1 ({_model_label(test)}, profile {test.profile}, prompt {test.prompt})")
        started = time.monotonic()
        deadline = started + timeout_s
        session = turn(config.prompt_text(lab_root, test.prompt), None, env)
        while True:
            wait_s = _build_wait(test, gh, goal)
            goal_ok = _goal_ok(test, gh, goal, wait_s)
            if goal_ok or not followups or not session:
                break
            print(f"{test.id}: follow-up {len(streams)}")
            session = turn(followups.pop(0), session, env)
        if not goal_ok and not wait_s:
            # Final verification: give a site configured in the last turn its
            # build time. Without Pages at all there is nothing to wait for.
            final_wait_s = _build_wait(test, gh, goal)
            if final_wait_s:
                goal_ok = _goal_ok(test, gh, goal, final_wait_s)
        ended = time.monotonic()
        if goal_ok and assessment:
            row.update(_assess(lab_root, test, gh, assessment))
    except _RunTimeout:
        timed_out = True
        print(f"{test.id}: timed out after {timeout_s} s")
    except BaseException:
        failed = True
        raise
    finally:
        info = metrics.stream_info(streams)
        if streams:  # no `claude` call yet: the config dir holds no metrics of this run
            row.update(collect(p, out_dir))
            warn_if_lab_repo_seen(lab_root, test.id, p.config)
            mismatch = _totals_mismatch(row, info["result_totals"])
            if mismatch:
                print(
                    f"warning: {test.id}: token totals from the transcripts differ from the stream's"
                    f" result events ({mismatch}); the row keeps the transcript totals",
                    file=sys.stderr,
                )
        row["goal_ok"] = goal_ok
        row["limit_5h_end_pct"] = info["limit_5h_end_pct"]
        row["mcp_connected"] = info["mcp_connected"]
        row["duration_s"] = None if started is None else round((ended or time.monotonic()) - started)
        row["status"] = _status(
            failed=failed, timed_out=timed_out, goal_ok=goal_ok, denied=info["denied"]
        )
        record(lab_root, row)
    return row


def _run_in_terminal(argv: list[str], cwd: Path, env: dict[str, str]) -> int:
    """Run argv with this terminal; Ctrl+C belongs to the child, not to the lab.

    Ctrl+C in a terminal signals the whole foreground process group, so it
    would also stop the lab and with it the session. A no-op handler (not
    SIG_IGN, which a child would inherit) keeps the lab waiting; the child
    starts with the default handling, as handlers reset on exec.
    """
    previous = signal.signal(signal.SIGINT, lambda signum, frame: None)
    try:
        return subprocess.run(argv, cwd=cwd, env=env).returncode
    finally:
        # None means a handler not set from Python; fall back to the default.
        signal.signal(signal.SIGINT, signal.SIG_DFL if previous is None else previous)


def run_interactive(
    lab_root: Path,
    test: config.Test,
    cfg: dict[str, str],
    gh: GitHub,
    *,
    goal: config.Goal,
    assessment: config.Assessment | None = None,
    limit_start: int | None,
    prompt: bool = True,
) -> dict | None:
    """Run `test` as an interactive session in this terminal; return its row.

    Same reset as headless (fresh config with plugins, empty repo), then
    `claude <prompt> --model M [--effort E] --permission-mode default` until the human
    exits. Afterwards: `goal` check, the `assessment` if the goal is met,
    metrics, row (`mode` interactive).
    `limit_5h_end_pct` comes from the status line the session saved.
    `duration_s` is the session's wall time. Status is `ok` if the goal is met,
    else `failed`: there is no timeout and no denial count interactively.

    With `prompt=False` the session starts empty, to inspect a profile:
    no goal check, no metrics, no row; returns None.
    """
    stamp = datetime.now(timezone.utc).strftime(results.TIMESTAMP_FORMAT)
    p = sandbox.paths(lab_root, test.id)
    out_dir = p.results / stamp
    row = _new_row(stamp, test, "interactive", limit_start)
    ran = failed = False
    goal_ok = None
    try:
        env = sandbox.child_env(os.environ, lab_root, p.config, cfg)
        fresh_config(lab_root, test, p, out_dir, env)
        reset_repo(lab_root, test, gh, p)
        first = [config.prompt_text(lab_root, test.prompt)] if prompt else []
        argv = ["claude", *first, *_model_flags(test.model, test.effort), "--permission-mode", "default"]
        print(f"{test.id}: config dir {_shown(lab_root, p.config)}")
        print(f"{test.id}: starting claude ({_model_label(test)}, profile {test.profile}); /exit ends the session")
        started = time.monotonic()
        ran = True
        code = _run_in_terminal(argv, p.work, env)
        row["duration_s"] = round(time.monotonic() - started)
        if code != 0:
            print(f"{test.id}: claude exited {code}", file=sys.stderr)
        if not prompt:
            return None
        goal_ok = _goal_ok(test, gh, goal, _build_wait(test, gh, goal))
        if goal_ok and assessment:
            row.update(_assess(lab_root, test, gh, assessment))
    except BaseException:
        failed = True
        raise
    finally:
        if prompt:
            if ran:
                row.update(collect(p, out_dir))
                warn_if_lab_repo_seen(lab_root, test.id, p.config)
                row["limit_5h_end_pct"] = statusline_limit(out_dir / STATUS_FILE)
            row["goal_ok"] = goal_ok
            row["mcp_connected"] = ""  # no stream-json output, so no init event
            row["status"] = _status(failed=failed, timed_out=False, goal_ok=goal_ok, denied=None)
            record(lab_root, row)
    return row
