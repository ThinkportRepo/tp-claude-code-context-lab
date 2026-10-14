# Security

## What the lab handles

The lab works with two credentials:

- **A Claude setup token** (`CLAUDE_CODE_OAUTH_TOKEN`), created by
  `python3 lab.py tokens claude`. It is valid for one year; the lifetime
  cannot be shortened.
- **A fine-grained GitHub personal access token** (`GH_TOKEN`), created by
  `python3 lab.py tokens github`. It is valid for one day and limited to
  the lab's GitHub organization.

Both are stored only in `.env`, which the lab writes with mode 600 and
which git ignores, and in the environment of the child processes that need
them. They never appear in command-line arguments, and the lab prints them
only masked. Preflight refuses to run if `.env` is tracked by git or has
another mode.

The lab creates public test repositories in your organization and deletes
them again on the next run or on teardown.

## The lab's isolation is not a security boundary

The lab separates runs from each other and from your own Claude config. It
does not protect your machine from the model under test:

- The model runs as your user, with both tokens in its environment and with
  unscoped `Read`, `Edit`, `Write` and `Bash(...)` allow rules. It can read
  and write files your user can, including the `.env` file two directories
  above its working directory.
- Transcripts under `sandbox/*/results/` contain whatever Claude printed or
  read during a run.

Run the lab under a dedicated user account, in a VM or in a devcontainer,
and do not share the `sandbox/` results.

## Recommendations

- **Use a new, dedicated GitHub organization** that holds nothing but the
  lab's repositories. The GitHub token has write access to every repository
  in it, including GitHub Actions workflows.
- **Revoke both tokens after the lab:** `python3 lab.py teardown --secrets`.
  It revokes the GitHub token, guides you through revoking the Claude token
  on claude.ai, and checks both. If it cannot prove a token is revoked, it
  says so and names the page where you can check.
- **Don't share or record your screen** while `python3 lab.py tokens claude`
  runs. Claude Code's login screen shows the new token; the lab masks it
  where it can, but not reliably. The same applies to the GitHub token page
  during `python3 lab.py tokens github`.

## Reporting a vulnerability

Please do not open a public issue for a vulnerability. Report it through
GitHub's private vulnerability reporting on this repository (*Security* →
*Report a vulnerability*), or contact Thinkport GmbH.

Maintainers: enable private vulnerability reporting in the repository
settings. Without it, the *Report a vulnerability* button does not exist.
