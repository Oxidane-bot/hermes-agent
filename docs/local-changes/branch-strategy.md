# Fork Branch Strategy

This repository is maintained as a personal operational fork of upstream
`NousResearch/hermes-agent`.

## Remote roles

- `origin` should point at the personal fork (`Oxidane-bot/hermes-agent`) and is
  the only remote intended for normal pushes.
- `upstream` should point at `NousResearch/hermes-agent` and is kept so official
  releases, tags, and selected upstream commits can be fetched later.

## Branch roles

- `origin/main` is the canonical fork line for local operational changes and
  deployed profile updates.
- `origin/upstream-main` mirrors upstream `main` for GitHub comparisons and
  future forward-port reviews. Do not land local fork commits there.
- A second local-maintained branch may exist only when it has an active
  maintenance purpose documented in this directory. The current checkpoint
  worktree is `impl/v2-compaction-checkpoint`; keep it local or remote only
  while that worktree remains useful.
- Backup branches, temporary PR branches, and historical topic branches imported
  from upstream or prior PR workflows should not remain in the normal fork
  branch list.
- Deleting local branch refs does not break future upstream release tracking as
  long as `upstream` and tags remain fetchable.

After branch cleanup, the fork's normal remote branch list should be small:
`main`, `upstream-main`, and any explicitly documented active local-maintained
branch. Redundant backup branches are not part of the intended steady state.

## Release-following policy

When upstream publishes a useful release:

```bash
git fetch upstream --tags
git checkout main
git merge <upstream-release-tag>
```

Refresh the comparison branch when needed:

```bash
git fetch upstream main
git push --force-with-lease origin upstream/main:refs/heads/upstream-main
```

If the release needs review before adoption, create a temporary upgrade branch
from the upstream tag, forward-port local changes there, verify, and then merge
back into `main`.

## Communication rule

Docs and commit messages should describe this repository as a personal fork with
local operational needs, not as a staging area for upstream pull requests.
