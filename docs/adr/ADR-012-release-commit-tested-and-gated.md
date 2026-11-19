# ADR-012: The release job tests its commit before pushing it; chore / ci / docs / test pull requests release nothing

2026-11-19. The ecosystem rule is Velisa ADR-023 (decision D37).

## Context

The release commit is pushed with the default token, so no workflow runs on it on main. On 2026-11-19 two skills' mains broke this way (sound-design-skill, hyperframes-skill). Also, every merged pull request released a patch, `ci:` changes included.

## Decision

- The release job commits locally, runs `tests/test_contract.py`, `tests/test_contract_file.py`, `tests/test_release_labels.py`, `tests/test_version_source.py` and `tests/test_release_gate.py` on that commit, and only then pushes. A failure pushes nothing.
- `.github/scripts/release_gate.py` lets a push release only when a pull request merged since the latest tag carries a release-level label or no label at all. The release-level labels are the ones `.github/release-drafter.yml`'s `version-resolver` maps to a bump. A pull request labelled only chore, ci, docs, test or similar releases nothing: "Decide target version" picks the tagged version again, and the guard skips the release. This is imagemagick-skill's rule. release-drafter's `default` accepts only major, minor or patch, so the gate runs first, and `chore` no longer maps to a patch.

## Consequences

A version bumped by hand in a pull request is released whatever its labels. The contract is unchanged.
