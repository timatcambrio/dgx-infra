# Working in this repository

Read `.agent/CONTINUITY.md` first: it is this repo's briefing, and the only record of
why things are the way they are. Project-level decisions live in
`../.agent/CONTINUITY.md`, in the parent `dgx-deployment` repo. **Keep both updated as
you work** — facts only, ISO date plus a provenance tag (`[USER]`, `[TOOL]`,
`[CODE]`), and `UNCONFIRMED` where something is not known rather than a guess that
reads like a fact.

## Git

- **Branch names** follow what is already there: `fix/<short-dash-name>` for a change,
  `eval/<name>` for a measurement run, `docs/<name>` for documentation. No tool or
  vendor prefixes.
- **Commit messages carry no attribution trailers.** No `Co-Authored-By:`, no session
  links, no model or tool names. A message says what changed and why it had to change.
- **Author and committer** are `timatcambrio
  <8162519+timatcambrio@users.noreply.github.com>`. Merges made through the GitHub web
  UI get stamped `Tsu-ting Tim Lin` with that same address; that is GitHub using the
  profile name, not a second convention to follow.
- **Pull request titles and descriptions** carry no attribution footer either.
- **Ask before pushing.** The DGX deploys from GitHub, so a push is a real action with
  an effect outside this repo. Never push to `main`.
- Commit in reviewable steps, one concern each. For a behaviour change the order is
  **failing tests first, then the fix, then any golden or fixture regeneration in a
  separate commit that changes nothing else** — a golden edited alongside the code
  hides the regression it exists to catch.

## Verification

`make check` is the verification command: both policy gates, then the whole suite. Run
it after every code change and report the number of tests, not just that it passed.

Two things change what it covers:

- Without Postgres and an embedding service reachable, the Stage 2 tests **skip** —
  about 50 of them, silently. `docker compose -f compose/docker-compose.yml --profile
  dev up -d db ollama` first if you want the real number.
- While the Marker cache sits in `dgx-knowledge/work`, the policy gates need
  `WORK_DIR` pointed at an empty directory (`WORK_DIR=/private/tmp/dgx-empty-work` on
  the dev Mac).

`make check` cannot reach the deployment: no test process has a certificate, a reverse
proxy, or a live token store. `scripts/http_probe.py` checks a **running** stack over
TLS at its public address, including revocation taking effect on both routes. Use it
after anything that touches `compose/`, `retrieval/auth.py` or `retrieval/tokens.py`.

## Writing

- **The README is written for someone using the pipeline, not maintaining it.** It
  explains what to run and what the output means, not how the code is arranged.
- **No milestone vocabulary in user-facing output** — no `S4`, `M2`, or brief section
  numbers in anything a user reads. Those belong in comments and briefings.
- **Public docs never name agent involvement, and never name corpus files.** The
  client's documents are not examples.
- Explain a failure where it happens: this codebase prefers one line naming the fix
  over a traceback, and a comment recording what was *measured* over one recording
  what was assumed. Several modules exist only to pin down a defect that was found the
  hard way; match that style rather than trimming it.

## Tests

- **Fixtures are synthetic.** `tests/make_fixtures.py` generates every one of them, and
  `make fixtures` must be a no-op. They must regenerate byte-identically on any
  platform, not only the machine that made them — the deployment target is Linux.
- A test's docstring should say what failure it prevents, in terms of what was
  observed. Tests here are the record of defects that already happened once.
