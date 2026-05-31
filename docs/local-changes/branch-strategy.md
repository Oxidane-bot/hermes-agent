# Fork Branch Strategy

This repository is maintained as a personal operational fork of upstream
`NousResearch/hermes-agent`.

## Remote roles

- `origin` should point at the personal fork (`Oxidane-bot/hermes-agent`) and is
  the only remote intended for normal pushes.
- `upstream` should point at `NousResearch/hermes-agent` and is kept so official
  releases, tags, and selected upstream commits can be fetched later.

## Branch roles

- `main` is the canonical fork line for local operational changes.
- Historical topic branches imported from upstream or prior PR workflows should
  not remain in the normal branch list unless they are actively maintained.
- Deleting local branch refs does not break future upstream release tracking as
  long as `upstream` and tags remain fetchable.

## Release-following policy

When upstream publishes a useful release:

```bash
git fetch upstream --tags
git checkout main
git merge <upstream-release-tag>
```

If the release needs review before adoption, create a temporary upgrade branch
from the upstream tag, forward-port local changes there, verify, and then merge
back into `main`.

## Communication rule

Docs and commit messages should describe this repository as a personal fork with
local operational needs, not as a staging area for upstream pull requests.
