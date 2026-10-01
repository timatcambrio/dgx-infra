"""The bearer-token store: hashed, individually revocable credentials in a JSON file.

WHY THIS EXISTS. Until this module, `kb serve --transport http` took its tokens from
`KB_TOKENS` — read once at process start (`config.py`) and handed to `BearerMiddleware` as
a fixed tuple. Three consequences, all bad: withdrawing one person's access meant editing
`.env` and restarting, which drops *every* user; there was no record of who a token
belongs to, so a per-user audit was impossible; and `.env` held the secrets in plaintext,
so read access to it yielded every user's credential at once. With one token per user
(the access decision recorded 2026-09-29) the first of those makes revocation cost an
outage, which in practice means it never happens.

WHAT REPLACES IT. A JSON file, `KB_TOKENS_FILE`, holding one record per token: a *hash* of
the secret, who it identifies, when it was issued, and when it expires or was revoked.
`TokenStore` reads that file on demand, re-reading it when its mtime/size change, so
issuing and revoking are file writes that take effect within `KB_TOKEN_CACHE_SECONDS`
(default 5) with no restart and nothing required of any other user.

WHY A FILE AND NOT A TABLE. Postgres is already in the stack and the roadmap per-team ACL
work will live there, but auth is the one thing that must keep working — and stay
repairable — when the database does not. A file also needs no migration, is trivial to
back up, and can be inspected with `cat` during an incident.

WHY sha256 AND NOT bcrypt/argon2. These are not passwords. `issue()` generates 32 bytes
from `secrets.token_urlsafe`, so there is no dictionary to attack and no work factor to
buy; a single sha256 is the right trade for a hash computed on every request. Hashing here
buys exactly one thing: a stolen *file* does not yield usable credentials.

NOT IMPLEMENTED HERE: the client-side half of the rotation problem (the secret still lives
in a config file on each laptop). That is a deployment question — rotate on incident, push
the credential as a managed setting, or OAuth — not something the server can fix.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import stat
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

#: Schema version of the file `read`/`write` handle. Bumped only on a breaking change; an
#: unknown version is refused rather than guessed at, so a newer `kb` writing a format an
#: older server cannot read fails loudly instead of silently authenticating no one.
FILE_VERSION = 1

#: Hashes are stored prefixed so the algorithm is visible in the file and a future one can
#: be added without ambiguity.
HASH_ALGORITHM = "sha256"

#: How long `TokenStore` may serve its cached copy before it stats the file again. The
#: upper bound on how long a revoked token keeps working.
DEFAULT_CACHE_SECONDS = 5.0

#: Bytes of entropy in an issued secret. 32 bytes -> a 43-character urlsafe string.
_SECRET_BYTES = 32


class TokenFileError(RuntimeError):
    """The token file exists but cannot be used. The message says why."""


def hash_secret(secret: str) -> str:
    """`sha256:<hex>` for one secret. The stored form; never reversible to the secret."""
    digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    return f"{HASH_ALGORITHM}:{digest}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _parse_iso(value: str, *, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise TokenFileError(f"{field} is not an ISO-8601 timestamp: {value!r}") from exc
    # A bare timestamp with no offset is read as UTC: every timestamp this module writes
    # carries one, so this only forgives a hand-edited file.
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class TokenRecord:
    """One issued credential. Holds no secret — `hash` is all that is stored."""

    id: str
    user: str
    hash: str
    issued: str
    expires: str | None = None
    revoked: str | None = None
    note: str | None = None

    @property
    def status(self) -> str:
        """`active`, `revoked`, or `expired` — what `kb token list` prints."""
        if self.revoked is not None:
            return "revoked"
        if self.expires is not None and _parse_iso(self.expires, field="expires") <= _utcnow():
            return "expired"
        return "active"

    def is_active(self, *, now: datetime | None = None) -> bool:
        now = now or _utcnow()
        if self.revoked is not None:
            return False
        if self.expires is not None and _parse_iso(self.expires, field="expires") <= now:
            return False
        return True

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "user": self.user,
            "hash": self.hash,
            "issued": self.issued,
        }
        # Optional fields are omitted rather than written as null, so a file an operator
        # reads during an incident shows only what is actually set.
        for field in ("expires", "revoked", "note"):
            value = getattr(self, field)
            if value is not None:
                out[field] = value
        return out


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Identity:
    """Who a request authenticated as. `source` is `file` for a stored record or `env` for
    a legacy `KB_TOKENS` value, which has no owner to name."""

    id: str
    user: str
    source: str


#: The identity of a request let through by `--allow-anonymous`. Distinguishable in the
#: audit log from any real credential.
ANONYMOUS = Identity(id="-", user="anonymous", source="allow-anonymous")


# --------------------------------------------------------------------------------------
# Reading and writing the file.
# --------------------------------------------------------------------------------------


def _record_from_json(raw: Any, *, index: int) -> TokenRecord:
    if not isinstance(raw, dict):
        raise TokenFileError(f"tokens[{index}] is not an object")
    missing = [k for k in ("id", "user", "hash", "issued") if not raw.get(k)]
    if missing:
        raise TokenFileError(f"tokens[{index}] is missing {', '.join(missing)}")
    algorithm = str(raw["hash"]).split(":", 1)[0]
    if algorithm != HASH_ALGORITHM:
        raise TokenFileError(
            f"tokens[{index}] ({raw['id']}) uses unsupported hash {algorithm!r}; "
            f"this build understands {HASH_ALGORITHM!r} only"
        )
    return TokenRecord(
        id=str(raw["id"]),
        user=str(raw["user"]),
        hash=str(raw["hash"]),
        issued=str(raw["issued"]),
        expires=str(raw["expires"]) if raw.get("expires") else None,
        revoked=str(raw["revoked"]) if raw.get("revoked") else None,
        note=str(raw["note"]) if raw.get("note") else None,
    )


def read(path: Path) -> tuple[TokenRecord, ...]:
    """Every record in the file. A missing or empty file is no records, not an error — the
    compose stack bind-mounts `/dev/null` when no token file is configured, and a fresh
    deployment has not issued anything yet."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ()
    except OSError as exc:
        raise TokenFileError(f"cannot read {path}: {exc}") from exc

    if not text.strip():
        return ()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise TokenFileError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise TokenFileError(f"{path} must hold a JSON object, found {type(data).__name__}")

    version = data.get("version")
    if version != FILE_VERSION:
        raise TokenFileError(
            f"{path} has version {version!r}; this build reads version {FILE_VERSION} only"
        )

    entries = data.get("tokens", [])
    if not isinstance(entries, list):
        raise TokenFileError(f"{path}: `tokens` must be a list")

    records = tuple(_record_from_json(e, index=i) for i, e in enumerate(entries))
    seen: set[str] = set()
    for record in records:
        if record.id in seen:
            raise TokenFileError(f"{path}: duplicate token id {record.id!r}")
        seen.add(record.id)
    return records


def write(path: Path, records: Sequence[TokenRecord]) -> None:
    """Replace the file with `records`, atomically and owner-readable only.

    Atomic because the server re-reads this file on an mtime change: a plain truncate-then-
    write leaves a window in which the server sees an empty or half-written file and
    authenticates no one. The temp file is created with mode 0600 *before* any content is
    written, so the secrets' hashes are never briefly world-readable.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {"version": FILE_VERSION, "tokens": [r.to_json() for r in records]},
        indent=2,
    ) + "\n"

    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        _inherit_access(path, tmp)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, path)


def _inherit_access(target: Path, tmp: Path) -> None:
    """Give the replacement the mode and owner that the file it replaces already had.

    `write` installs a NEW inode, so without this every deployment-level fix to the
    store's permissions is undone by the next `issue` or `revoke`. Measured 2026-10-01 on
    the compose stack: `kb-mcp` runs as uid 10001 (compose/Dockerfile) and cannot read a
    root-owned 0600 store, so `kb serve` exits 2 and the container crash-loops. Chowning
    the store to that uid fixes it — and the next `kb token` write took it back, silently,
    which is the same shape as the inode bug that made this function atomic in the first
    place.

    A fresh store keeps the 0600 it was created with: there is nothing to inherit, and a
    new credential store must not start out readable by more than its owner.

    Ownership is best effort. Only root may give a file away, and a deployment where
    `kb token` already runs as the store's owner neither needs to nor can. A refusal there
    is not worth failing the write over: the records are what matter, the mode has already
    been carried over, and `make compose-up`'s pre-flight
    (`scripts/check_token_paths.py`) is what catches a store the server cannot read.
    """
    try:
        previous = target.stat()
    except FileNotFoundError:
        return
    os.chmod(tmp, stat.S_IMODE(previous.st_mode))
    try:
        os.chown(tmp, previous.st_uid, previous.st_gid)
    except (PermissionError, OSError):
        pass


# --------------------------------------------------------------------------------------
# Mutations. Each is read -> change -> write, so `kb token` never holds the file open.
# --------------------------------------------------------------------------------------


def issue(
    path: Path,
    user: str,
    *,
    expires: str | None = None,
    note: str | None = None,
) -> tuple[str, TokenRecord]:
    """Add a record for a freshly generated secret. Returns `(secret, record)`.

    The secret is returned once and never stored; if it is lost, issue another and revoke
    this one. The record id is the first 12 characters of the hash digest — derived, so
    two ids never collide for different secrets, and safe to log or print because it is a
    truncated hash of a 32-byte random value, not a fragment of the secret itself.
    """
    if not user.strip():
        raise TokenFileError("a token must name a user")
    secret = secrets.token_urlsafe(_SECRET_BYTES)
    hashed = hash_secret(secret)
    record = TokenRecord(
        id=hashed.split(":", 1)[1][:12],
        user=user.strip(),
        hash=hashed,
        issued=_now_iso(),
        expires=expires,
        note=note,
    )
    write(path, (*read(path), record))
    return secret, record


def revoke(path: Path, token_id: str) -> TokenRecord:
    """Mark one record revoked, as of now. Idempotent: re-revoking keeps the first
    timestamp, because when access was withdrawn is the fact worth preserving."""
    records = read(path)
    for index, record in enumerate(records):
        if record.id != token_id:
            continue
        if record.revoked is not None:
            return record
        updated = replace(record, revoked=_now_iso())
        write(path, (*records[:index], updated, *records[index + 1 :]))
        return updated
    raise TokenFileError(f"no token with id {token_id!r} in {path}")


# --------------------------------------------------------------------------------------
# The store the middleware holds.
# --------------------------------------------------------------------------------------


class TokenStore:
    """Resolves a supplied secret to an `Identity`, re-reading the file as it changes.

    Held for the life of the process by `BearerMiddleware`. `resolve` is called on every
    request under `/mcp`, so the file is stat'd at most once per `cache_seconds` and read
    only when mtime or size actually changed.

    `static_tokens` carries `KB_TOKENS` through unchanged. It is deliberately still
    supported — the dev path, the test suite and any existing deployment use it — but it
    has none of this module's properties: no owner, no revocation short of a restart,
    plaintext on disk. `kb serve` warns when it is the only source.

    A `TokenFileError` while refreshing is NOT swallowed into "no tokens": that would turn
    a typo in the file into a silent lockout of everyone. The last good copy keeps serving
    and the error is reported through `last_error` (and logged by the middleware), so a
    broken edit fails safe in the direction of continuity rather than outage. The one
    exception is the very first read, where there is no last good copy — that propagates,
    and `kb serve` refuses to start.
    """

    def __init__(
        self,
        *,
        path: Path | None = None,
        static_tokens: Iterable[str] = (),
        allow_anonymous: bool = False,
        cache_seconds: float = DEFAULT_CACHE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.path = Path(path) if path is not None else None
        self.static_tokens = tuple(static_tokens)
        self.allow_anonymous = allow_anonymous
        self.cache_seconds = cache_seconds
        self._clock = clock
        self._static_hashes = tuple(hash_secret(t) for t in self.static_tokens)
        self._records: tuple[TokenRecord, ...] = ()
        self._stamp: tuple[int, int] | None = None
        self._checked_at: float | None = None
        self._loaded = False
        self.last_error: str | None = None

    # -- state ---------------------------------------------------------------------

    def has_any_credential(self) -> bool:
        """Whether anything could authenticate. `kb serve --transport http` refuses to
        start when this is false and `--allow-anonymous` was not passed, for the same
        reason the `KB_TOKENS` check existed: an HTTP server no one can use is a
        misconfiguration, and one that lets everyone in is worse."""
        if self.static_tokens:
            return True
        self._refresh(force=True)
        return any(r.is_active() for r in self._records)

    def records(self) -> tuple[TokenRecord, ...]:
        self._refresh(force=True)
        return self._records

    # -- the hot path --------------------------------------------------------------

    def resolve(self, supplied: str) -> Identity | None:
        """The identity `supplied` authenticates as, or `None`.

        `hmac.compare_digest` over hex digests rather than `==`: the digests make the
        comparison uncorrelated with the secret already, but a non-constant-time compare
        would still leak which *record* was being tested through timing, and the cost of
        avoiding that is nothing (brief §8.4).
        """
        digest = hash_secret(supplied)

        for token_hash, token in zip(self._static_hashes, self.static_tokens):
            if hmac.compare_digest(digest, token_hash):
                # No owner to name: `KB_TOKENS` records none. The id is a truncated hash
                # so the audit log can still distinguish which static token was used.
                return Identity(id=token_hash.split(":", 1)[1][:12], user="(KB_TOKENS)", source="env")

        self._refresh()
        now = _utcnow()
        for record in self._records:
            if not hmac.compare_digest(digest, record.hash):
                continue
            if not record.is_active(now=now):
                return None
            return Identity(id=record.id, user=record.user, source="file")
        return None

    # -- caching ------------------------------------------------------------------

    def _refresh(self, *, force: bool = False) -> None:
        if self.path is None:
            self._loaded = True
            return

        now = self._clock()
        if not force and self._loaded and self._checked_at is not None:
            if now - self._checked_at < self.cache_seconds:
                return
        self._checked_at = now

        try:
            stat = self.path.stat()
            stamp = (stat.st_mtime_ns, stat.st_size)
        except FileNotFoundError:
            stamp = None
        except OSError as exc:
            self._fail(f"cannot stat {self.path}: {exc}")
            return

        if self._loaded and stamp == self._stamp:
            return

        try:
            records = read(self.path)
        except TokenFileError as exc:
            self._fail(str(exc))
            return

        self._records = records
        self._stamp = stamp
        self._loaded = True
        self.last_error = None

    def _fail(self, message: str) -> None:
        """A refresh failed. Keep the last good copy if there is one; otherwise raise, so
        the failure surfaces at startup rather than as a silent deny-all."""
        self.last_error = message
        if not self._loaded:
            raise TokenFileError(message)
