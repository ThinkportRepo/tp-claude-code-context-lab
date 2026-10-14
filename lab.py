#!/usr/bin/env python3
"""Claude Code Context Lab: run Claude Code against real GitHub repos and measure
context size and token use per profile, prompt, model and effort.

First run, in this order:
  1. Create a new, empty GitHub organization in which you are an admin.
  2. python3 lab.py tokens claude          create the Claude token, store it in .env
  3. Set LAB_GH_ORG=<organization> in .env by hand (see .env.example).
  4. python3 lab.py tokens github          create a fine-grained GitHub token (1 day), store it
  5. python3 lab.py tokens github --check  create, push, publish and delete a test repo
  6. python3 lab.py setup                  check GitHub access and create sandbox dirs (warns about Google Chrome for dev-stack and dev-messy)
  7. python3 lab.py run test1              run one test headless, record it in results.csv
  8. python3 lab.py report                 summarize results.csv
  9. python3 lab.py teardown --secrets     delete lab repos and sandbox, revoke the tokens

All commands:
  python3 lab.py tokens claude [--config-dir DIR]
  python3 lab.py tokens github [--check]
  python3 lab.py setup
  python3 lab.py run [<test>|all] [--repeat N]   tests are listed in lab/matrix.conf
  python3 lab.py session <test> [--no-prompt]    run a test interactively in this terminal
  python3 lab.py report
  python3 lab.py teardown [--secrets] [--yes]
"""

import argparse
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

LAB_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(LAB_ROOT / "lab"))

from labkit import (  # noqa: E402
    config, env, github, limits, preflight, report, results, runner, sandbox, teardown, tokens,
)

ENV_FILE = LAB_ROOT / ".env"
MATRIX_FILE = LAB_ROOT / "lab" / "matrix.conf"
SANDBOX = LAB_ROOT / "sandbox"
RUN_TIMEOUT_S = runner.RUN_TIMEOUT_S  # per-run timeout; a GitHub token must outlive it
NEEDS_GITHUB = {"setup", "run", "session"}
# Profiles with MCP servers that npx starts and that drive Google Chrome.
MCP_PROFILES = {"dev-stack", "dev-messy"}
# teardown has to work with what is left: no Claude token (it may be gone
# already), no npx, and an expired GitHub token only skips the repo part.
TEARDOWN = "teardown"


def results_file() -> Path:
    return LAB_ROOT / "results.csv"


def prepare(command: str, args: argparse.Namespace, *, need_node: bool = False) -> dict[str, str]:
    """Check the setup once before a command runs and return the config.

    Prints warnings; prints errors and exits with status 1 if there are any.
    Called for every command except `tokens`, which creates the credentials
    the checks look for. Makes no network calls.
    """
    cfg = env.load(ENV_FILE, os.environ)
    errors = [f"{ENV_FILE.name}: {problem}" for problem in env.file_problems(ENV_FILE, LAB_ROOT)]
    check_errors, warnings = preflight.check(
        cfg,
        need_github=command in NEEDS_GITHUB,
        need_node=need_node and command != TEARDOWN,
        need_claude_token=command != TEARDOWN,
        timeout_s=RUN_TIMEOUT_S,
        now=datetime.now(timezone.utc),
        sandbox=SANDBOX,
        home=Path.home(),
    )
    records_row = command == "run" or (command == "session" and not getattr(args, "no_prompt", False))
    if records_row:
        # Before any tokens are spent: a finished run must not fail on its last step.
        # `session --no-prompt` records no row, so an old results file is no problem there.
        try:
            results.check_header(results_file())
        except ValueError as exc:
            errors.append(str(exc))
    for message in warnings:
        print(f"warning: {message}", file=sys.stderr)
    for message in errors + check_errors:
        print(f"error: {message}", file=sys.stderr)
    if errors or check_errors:
        sys.exit(1)
    return cfg


def matrix_needs_node() -> bool:
    """True if any test in matrix.conf uses one of MCP_PROFILES (MCP servers via npx)."""
    return any(t.profile in MCP_PROFILES for t in config.load_matrix(MATRIX_FILE, os.environ))


def positive_int(text: str) -> int:
    """Parse a run count; ValueError unless it is an integer of at least 1."""
    try:
        value = int(text)
    except ValueError:
        raise ValueError(f"expected a positive integer, got {text!r}") from None
    if value < 1:
        raise ValueError(f"expected a positive integer, got {text!r}")
    return value


def _repeat_arg(text: str) -> int:
    try:
        return positive_int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def ask_yes_no(question: str) -> bool:
    """Ask on the terminal; only `y` or `yes` counts as yes."""
    try:
        answer = input(f"{question} ")
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")


def _show(value) -> str:
    return "?" if value is None else str(value)


def _load_tests(cfg: dict[str, str], selection: str) -> list[config.Test] | None:
    """Tests from matrix.conf matching `selection` (an id or `all`); None after an error."""
    try:
        # The merged config, so LAB_MODEL from .env counts as well.
        matrix = config.load_matrix(MATRIX_FILE, cfg)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return None
    tests = matrix if selection == "all" else [t for t in matrix if t.id == selection]
    if not tests:
        known = ", ".join(t.id for t in matrix)
        print(f"error: unknown test {selection!r} (known: {known}, all)", file=sys.stderr)
        return None
    return tests


def _gate(cfg: dict[str, str]) -> tuple[bool, dict | None]:
    """Probe the usage limit and ask the gate; (may run, probe result)."""
    probe = limits.probe(LAB_ROOT, cfg)
    return limits.gate(probe, ask=ask_yes_no, is_tty=sys.stdin.isatty()), probe


def _summary(row: dict) -> str:
    return (
        f"{row['test']}: {row['status']} · ctx_start {_show(row['ctx_start'])}"
        f" · ctx_growth {_show(row['ctx_growth'])} · tokens_out {_show(row['tokens_out'])}"
        f" · 5-hour limit used {_show(row['limit_5h_start_pct'])} %"
        f" → {_show(row['limit_5h_end_pct'])} %"
    )


def run_tests(cfg: dict[str, str], selection: str, repeat: int | None) -> int:
    """`lab.py run`: for each selected test and repetition, gate, run, summarize."""
    if repeat is None:
        try:
            repeat = positive_int(cfg.get("REPEAT") or "1")
        except ValueError as exc:
            print(f"error: REPEAT: {exc}", file=sys.stderr)
            return 1
    tests = _load_tests(cfg, selection)
    if tests is None:
        return 1

    gh = github.GitHub(cfg["LAB_GH_ORG"], cfg["GH_TOKEN"], SANDBOX / ".gh")
    skipped = 0
    try:
        for test in tests:
            for n in range(1, repeat + 1):
                print(f"== {test.id}: run {n} of {repeat}")
                allowed, probe = _gate(cfg)
                if not allowed:
                    print(f"skipping {test.id}: the usage-limit gate said no")
                    skipped += 1
                    break
                row = runner.run_headless(
                    LAB_ROOT, test, cfg, gh,
                    limit_start=probe["five_hour_pct"] if probe else None,
                    timeout_s=RUN_TIMEOUT_S,
                )  # fmt: skip
                print(_summary(row))
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except (RuntimeError, OSError, subprocess.SubprocessError, ValueError) as exc:
        # RuntimeError covers GitHubError and sandbox.PluginInstallError.
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 1 if skipped else 0


def run_session(cfg: dict[str, str], test_id: str, prompt: bool) -> int:
    """`lab.py session`: gate, then one interactive session in this terminal."""
    if test_id == "all":
        print("error: session takes one test id, not all", file=sys.stderr)
        return 1
    tests = _load_tests(cfg, test_id)
    if tests is None:
        return 1
    (test,) = tests
    gh = github.GitHub(cfg["LAB_GH_ORG"], cfg["GH_TOKEN"], SANDBOX / ".gh")
    try:
        allowed, probe = _gate(cfg)
        if not allowed:
            print(f"skipping {test.id}: the usage-limit gate said no")
            return 1
        row = runner.run_interactive(
            LAB_ROOT, test, cfg, gh,
            limit_start=probe["five_hour_pct"] if probe else None, prompt=prompt,
        )  # fmt: skip
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except (RuntimeError, OSError, subprocess.SubprocessError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if row is not None:
        print(_summary(row))
    return 0


def run_setup(cfg: dict[str, str]) -> int:
    """`lab.py setup`: check org access (and Chrome for MCP_PROFILES), create sandbox dirs, show the matrix."""
    tests = _load_tests(cfg, "all")
    if tests is None:
        return 1
    gh = github.GitHub(cfg["LAB_GH_ORG"], cfg["GH_TOKEN"], SANDBOX / ".gh")
    try:
        if not gh.org_ok():
            print(f"error: cannot read organization {cfg['LAB_GH_ORG']} with GH_TOKEN", file=sys.stderr)
            return 1
        browser_profiles = sorted({t.profile for t in tests} & MCP_PROFILES)
        if browser_profiles and not preflight.chrome_installed(Path.home()):
            print(
                "warning: Google Chrome is not installed. The MCP server of the playwright plugin"
                " uses the installed Google Chrome (verified 2026-10-14), so runs of the profile(s)"
                f" {', '.join(browser_profiles)} may fail to open a browser. Install Google Chrome"
                " to fix this.",
                file=sys.stderr,
            )
        for test in tests:
            sandbox.paths(LAB_ROOT, test.id).base.mkdir(parents=True, exist_ok=True)
        print(f"Sandbox: {SANDBOX}")
        width = max(len(t.id) for t in tests)
        for t in tests:
            effort = f"  {t.effort}" if t.effort else ""
            print(f"  {t.id:<{width}}  {t.profile:<12}  {t.prompt:<12}  {t.model}{effort}")
        print(limits.limit_line(limits.probe(LAB_ROOT, cfg)))
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def run_report() -> int:
    """`lab.py report`: summarize results.csv, or hint that there is none yet."""
    rows = results.read(results_file())
    if not rows:
        print("no results yet — run python3 lab.py run …")
        return 0
    if "goal_ok" not in rows[0]:
        print(
            f"warning: {results_file().name} was written by an older lab version (no goal_ok column);"
            " the report may be wrong. Move the file aside and run again.",
            file=sys.stderr,
        )
    print(report.render(rows))
    return 0


def run_teardown(cfg: dict[str, str], *, secrets: bool, yes: bool) -> int:
    """`lab.py teardown`: delete the lab's repos and sandbox; --secrets also revokes tokens."""

    def confirm(question: str) -> bool:
        if yes:
            return True
        if not sys.stdin.isatty():
            print("stopping: no terminal to confirm; use --yes to delete without asking")
            return False
        return ask_yes_no(question)

    # teardown.run decides whether the GitHub settings are usable at all.
    gh = github.GitHub(cfg.get("LAB_GH_ORG", ""), cfg.get("GH_TOKEN", ""), SANDBOX / ".gh")
    try:
        return teardown.run(LAB_ROOT, cfg, gh, secrets=secrets, confirm=confirm)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def main() -> int:
    if sys.version_info < (3, 11):
        print("Python 3.11 or newer is required", file=sys.stderr)
        return 1

    parser = argparse.ArgumentParser(
        prog="lab.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    tok = sub.add_parser("tokens", help="create credentials and store them in .env")
    tok_sub = tok.add_subparsers(dest="provider", required=True)
    claude = tok_sub.add_parser("claude", help="run `claude setup-token` and store the token")
    claude.add_argument(
        "--config-dir",
        type=Path,
        help="CLAUDE_CONFIG_DIR to run setup-token in (default: a throwaway dir)",
    )
    github_cmd = tok_sub.add_parser("github", help="create a fine-grained GitHub token (valid 1 day)")
    github_cmd.add_argument(
        "--check", action="store_true", help="create, push, publish and delete a test repo"
    )

    run = sub.add_parser("run", help="run tests headless and record them in results.csv")
    run.add_argument("test", nargs="?", default="all", help="a test id from matrix.conf, or all (default)")
    run.add_argument("--repeat", type=_repeat_arg, help="runs per test (default: REPEAT, else 1)")

    session = sub.add_parser("session", help="run one test interactively in this terminal")
    session.add_argument("test", help="a test id from matrix.conf")
    session.add_argument(
        "--no-prompt", action="store_true",
        help="start without the prompt to inspect the profile; records nothing",
    )  # fmt: skip

    sub.add_parser("setup", help="check GitHub access and create sandbox dirs (warns about Google Chrome for dev-stack and dev-messy)")
    sub.add_parser("report", help="summarize results.csv")
    td = sub.add_parser("teardown", help="delete the lab's repos and sandbox")
    td.add_argument("--secrets", action="store_true", help="also revoke the GitHub and Claude tokens")
    td.add_argument("--yes", action="store_true", help="do not ask before deleting")

    args = parser.parse_args()
    if args.command == "report":
        return run_report()  # reads a local file only: no credentials, no preflight
    cfg: dict[str, str] = {}
    if args.command != "tokens":
        # Only the commands that run tests read the matrix; `tokens` and
        # `teardown` must work without it.
        need_node = False
        if args.command != TEARDOWN:
            try:
                need_node = matrix_needs_node()
            except (OSError, ValueError) as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
        cfg = prepare(args.command, args, need_node=need_node)
    if args.command == "run":
        return run_tests(cfg, args.test, args.repeat)
    if args.command == "session":
        return run_session(cfg, args.test, prompt=not args.no_prompt)
    if args.command == "setup":
        return run_setup(cfg)
    if args.command == "teardown":
        return run_teardown(cfg, secrets=args.secrets, yes=args.yes)
    if args.command == "tokens" and args.provider == "claude":
        return tokens.create_claude_token(ENV_FILE, args.config_dir)
    if args.command == "tokens" and args.provider == "github":
        if args.check:
            return tokens.check_github(ENV_FILE, SANDBOX)
        return tokens.create_github_token(ENV_FILE)
    return 1


if __name__ == "__main__":
    sys.exit(main())
