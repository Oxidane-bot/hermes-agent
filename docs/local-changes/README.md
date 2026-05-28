# Local Branch Change Log

This directory records changes that exist on the local Hermes branch but are not yet part of the upstream baseline we track.

Why this exists:

- Local operational fixes can stay deployed for weeks before upstream catches up.
- Commit messages explain one decision, but they are hard to scan when debugging live profiles.
- User-facing behavior, restart requirements, and rollback cautions need one stable place.

Maintenance rule:

1. Every local branch commit that changes behavior should add or update an entry here.
2. Each entry should state: intent, user-visible impact, touched areas, verification, and rollback/forward-port notes.
3. Internal bug fixes may be grouped, but the commit hash must still be listed.
4. If a local change is upstreamed, replaced, or dropped, update the entry rather than deleting history silently.

Current index:

- [May 2026 local branch changes](./local-branch-2026-05.md)
