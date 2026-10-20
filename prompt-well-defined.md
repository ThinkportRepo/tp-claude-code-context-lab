Create a simple "TIC TAC TOE" online game using the Git repository located in the `xyz` directory. Publish it with GitHub Pages and link the site in the repository's About section.

## 1. Functional requirements

Implement a complete, browser-based Tic Tac Toe game featuring:

- A 3×3 interactive board for two local players (X and O).
- Alternating turns and clear indication of the current player.
- Automatic detection of wins and draws.
- Prevention of invalid moves and moves after game completion.
- A restart button that resets the game.
- A clean, responsive interface supporting desktop and mobile browsers.
- Basic accessibility, including keyboard interaction and accessible status messages.

Online means publicly accessible through GitHub Pages. Network multiplayer, accounts, and back-end services are out of scope.

## 2. Technical constraints

- Prefer vanilla HTML, CSS, and JavaScript.
- Avoid frameworks, external dependencies, build tools, and third-party services unless already required by the repository.
- Keep the implementation small, readable, and maintainable.
- Use relative asset paths compatible with GitHub Pages project URLs.
- Include a concise README with basic instructions and the published URL.

## 3. Execution workflow

1. Inspect the `xyz` repository, including its working tree, relevant files, branch, remote configuration, and GitHub authentication.
2. Reuse existing project conventions where applicable. Preserve unrelated files and existing changes.
3. Implement the complete game.
4. Perform focused functional verification covering turns, wins, draws, invalid moves, and restarting.
5. Commit and push the changes using the repository's existing conventions.
6. Publish the game through GitHub Pages:
  - Prefer branch-based deployment from the repository root for this static site.
  - Configure GitHub Pages using the GitHub CLI/API.
  - If no GitHub remote exists, create a public repository under the authenticated account and configure the remote.
  - Do not overwrite existing remotes, force-push, or change an existing repository's visibility without approval.
7. Verify the deployment using the actual GitHub Pages URL and confirm that the expected game is served successfully.
8. Set the repository's About website URL using `gh repo edit --homepage` and verify the change.

Complete the workflow autonomously unless authentication, permissions, or potentially destructive changes require user intervention.

## 4. Token efficiency and context management

Treat token consumption and context usage as important execution constraints.

- **Minimize exploration:** Inspect only files and GitHub settings relevant to the task. Avoid unnecessary repository-wide scans.
- **Minimize tool output:** Prefer targeted commands and filtered results. Do not load entire logs or large files when only specific information is needed.
- **Minimize dependencies:** Do not install libraries or introduce tooling for functionality readily implemented with standard browser APIs.
- **Avoid unnecessary delegation:** Do not spawn subagents, agent teams, or parallel research tasks for this straightforward implementation.
- **Prefer lightweight tools:** Use Git, the GitHub CLI, and existing local tooling rather than MCP integrations whenever possible.
- **Avoid redundant operations:** Do not repeatedly inspect unchanged files, rerun successful checks, or research established implementation details.
- **Keep context focused:** Retain only relevant requirements, decisions, modified files, outstanding issues, and verification results. If context compaction becomes necessary, prioritize these details.
- **Keep communication concise:** Avoid lengthy plans, speculative analysis, verbose progress reports, and reproduction of code or command output already available in files.
- **Avoid overengineering:** Do not introduce abstractions, configuration systems, documentation, or test infrastructure beyond what this small project reasonably needs.

These efficiency constraints must not compromise functional correctness or deployment verification.

## 5. Definition of done

The task is complete only when:

- The game satisfies all functional requirements.
- Relevant functionality has been verified.
- Changes are committed and pushed successfully.
- GitHub Pages serves the game at a verified public URL.
- The repository's About section links to that URL.

Do not report unverified operations as successful.

## 6. Final response

Provide a concise completion report containing only:

- Repository URL
- Live GitHub Pages URL
- Verification results (pass/fail)
- Any incomplete steps or blockers

Do not include implementation walkthroughs, full source files, or verbose command output.
