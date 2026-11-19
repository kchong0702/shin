# Zumi

- Before changing `apps/harness`, read `apps/harness/docs/architecture/` and `apps/harness/docs/development/`, and update them in the same change.
- Before writing code for a feature, look for a maintained open-source library, MCP server or framework feature that already does it, and use it. Write our own code only for what none of them covers, and name the options you checked in the PR.
- Back every pull request with evidence, not claims. Include screenshots, or output from a real run when there is no UI, and say which commit and data they came from. When the change affects how parts fit together, add a small Mermaid architecture diagram. When it changes a flow across parts, such as an A2A call, a tool call or a run's lifecycle, add a Mermaid sequence diagram.
- Never add Claude, Anthropic, or any AI assistant as an author or co-author: no `Co-Authored-By` trailers and no AI attribution in commits, PRs or comments.
- Commit as the configured git user. Never change `user.name` or `user.email`.
- Use Conventional Commits: `type(scope): summary`.
- Never push to `main` or merge into it. Changes reach `main` only through a pull request the user merges.
- Never `git merge main` into a branch. Rebase onto it instead.
- Never force push, except to update a branch after a rebase, with `--force-with-lease`.
