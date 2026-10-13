"""An existing deployment must come out of `make compose-up` matching the current file.

The deployment is updated in place: `git pull`, then `make compose-up` (the guide's "Day
to day" table). Compose recreates services whose definition changed, but a service that
was REMOVED from the file is not its business: the container keeps running as an
"orphan", with a warning nobody reads. That happened for the first time on 2026-10-11,
when `kb-static` (the file server behind the old `/kb/*` route) was deleted: every
deployment updated afterwards would have kept serving the converted documents inside its
compose network, from a container no file describes any more.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MAKEFILE = REPO / "Makefile"


def test_compose_up_removes_containers_the_file_no_longer_defines() -> None:
    recipe = MAKEFILE.read_text(encoding="utf-8").split("\ncompose-up:")[1].split("\n\n")[0]
    assert "--remove-orphans" in recipe, recipe
