# Working in this repository

Git identity, branch names, attribution and pushing follow the global agent guidance.
This file covers only what is specific to this repo.

## Continuity

Read `.agent/CONTINUITY.md` first: it is this repo's briefing, and the only record of
why things are the way they are. Project-level decisions live in
`../.agent/CONTINUITY.md`, in the parent `dgx-deployment` repo. Keep both updated.

## Commits

Commit in reviewable steps, one concern each. For a behaviour change the order is
**failing tests first, then the fix, then any golden or fixture regeneration in a
separate commit that changes nothing else**. A golden edited alongside the code hides
the regression it exists to catch.

## Verification

`make check` is the verification command: both policy gates, then the whole suite. Run
it after every code change and report the number of tests, not just that it passed.

- Without Postgres and an embedding service reachable, about 50 Stage 2 tests **skip
  silently**. Run `docker compose -f compose/docker-compose.yml --profile dev up -d db
  ollama` first to get the real number.
- While the Marker cache sits in `dgx-knowledge/work`, the policy gates need `WORK_DIR`
  pointed at an empty directory (`WORK_DIR=/private/tmp/dgx-empty-work` on the dev Mac).
- `make check` cannot reach the deployment. After anything that touches `compose/`,
  `retrieval/auth.py` or `retrieval/tokens.py`, run `scripts/http_probe.py` against a
  running stack; it checks TLS, both auth routes and revocation.

## Writing

- The README is a short description of the repo that links to `docs/`. The docs are
  written for someone using or running the service, not maintaining it: what to run and
  what the output means, not how the code is arranged.
- No milestone vocabulary (`S4`, `M2`) or brief section numbers in anything a user reads.
  Those belong in comments and briefings.
- Public docs never name corpus files or describe the test documents. The client's
  documents are not examples.
- Explain a failure where it happens: one line naming the fix rather than a traceback,
  and comments that record what was measured rather than what was assumed. Several
  modules exist only to pin down a defect found the hard way; match that style.

## Tests

- Fixtures are synthetic. `tests/make_fixtures.py` generates every one, and `make
  fixtures` must be a no-op. They must regenerate byte-identically on any platform, not
  only the machine that made them; the deployment target is Linux.
- A test's docstring says what failure it prevents, in terms of what was observed. Tests
  here are the record of defects that already happened once.
