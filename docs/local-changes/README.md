# Local Fork Change Log

This directory records changes that exist in this personal Hermes fork but are not yet part of the upstream baseline we track. It is the maintained starting point for understanding what this fork changes and why those changes remain local.

Why this exists:

- Local operational fixes can stay deployed for weeks before upstream catches up.
- Commit messages explain one decision, but they are hard to scan when debugging live profiles.
- User-facing behavior, restart requirements, and rollback cautions need one stable place.
- The top-level README should link here so readers can distinguish upstream Hermes from this personal fork.

Maintenance rule:

1. Every local fork commit that changes behavior should add or update an entry here.
2. Each entry should state: intent, user-visible impact, touched areas, verification, and rollback/forward-port notes.
3. Internal bug fixes may be grouped, but the commit hash must still be listed.
4. If a local change is upstreamed, replaced, or dropped, update the entry rather than deleting history silently.

Current index:

- [May 2026 local fork changes](./local-branch-2026-05.md) — behavior, user-visible impact, verification, and maintenance notes for the active local deltas.
- [Fork Branch Strategy](./branch-strategy.md) — remotes, branch cleanup policy, and how this fork keeps following upstream releases/tags.
