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
- `upstream/main` is fetched from `NousResearch/hermes-agent` only when comparing
  or forward-porting upstream changes. Do not mirror upstream branches into
  `origin` unless there is a temporary, documented review need.
- Additional local-maintained branches should stay local by default. Keep a
  non-`main` branch on `origin` only when it has an active maintenance purpose
  documented in this directory and its changes are not already represented on
  `main`.
- Backup branches, temporary PR branches, and historical topic branches imported
  from upstream or prior PR workflows should not remain in the normal fork
  branch list.
- Deleting local branch refs does not break future upstream release tracking as
  long as `upstream` and tags remain fetchable.

After branch cleanup, the fork's normal `origin` branch list should contain only
`main`. Redundant backup branches, upstream mirror branches, and already-ported
local checkpoints are not part of the intended steady state.

## Release-following policy

When upstream publishes a useful release:

```bash
git fetch upstream --tags
git checkout main
git merge <upstream-release-tag>
```

If the release needs review before adoption, create a temporary upgrade branch
from the upstream tag, forward-port local changes there, verify, and then merge
back into `main`. Delete that temporary branch after the review is merged or
abandoned.

## Communication rule

Docs and commit messages should describe this repository as a personal fork with
local operational needs, not as a staging area for upstream pull requests.
