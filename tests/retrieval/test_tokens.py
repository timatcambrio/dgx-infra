"""`retrieval.tokens` — the credential store `kb serve --transport http` authenticates
against. `test_auth.py` covers what the middleware does with it; this file covers the
store's own properties, which are where the revocation guarantee actually lives:

- the secret is never stored, so the file is not a credential;
- a change to the file is picked up without a restart, within a bounded delay;
- a *broken* file does not lock everyone out mid-flight, but does refuse to start.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from retrieval import tokens as tokens_module
from retrieval.tokens import TokenFileError, TokenRecord, TokenStore


def _path(tmp_path: Path) -> Path:
    return tmp_path / "tokens.json"


# --------------------------------------------------------------------------------------
# Issuing and the file format.
# --------------------------------------------------------------------------------------


def test_issue_returns_a_secret_that_is_not_in_the_file(tmp_path: Path) -> None:
    """Hashed storage, the second half of the problem: read access to the store must not
    yield anyone's credential, which plaintext `KB_TOKENS` in `.env` did."""
    path = _path(tmp_path)
    secret, record = tokens_module.issue(path, "alice@example.com")
    text = path.read_text()
    assert secret not in text
    assert record.hash == tokens_module.hash_secret(secret)
    assert record.hash in text


def test_issued_secrets_are_distinct(tmp_path: Path) -> None:
    path = _path(tmp_path)
    first, _ = tokens_module.issue(path, "alice@example.com")
    second, _ = tokens_module.issue(path, "alice@example.com")
    assert first != second


def test_the_file_is_owner_readable_only(tmp_path: Path) -> None:
    path = _path(tmp_path)
    tokens_module.issue(path, "alice@example.com")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_the_file_is_versioned_json(tmp_path: Path) -> None:
    path = _path(tmp_path)
    tokens_module.issue(path, "alice@example.com")
    data = json.loads(path.read_text())
    assert data["version"] == tokens_module.FILE_VERSION
    assert [k for k in data["tokens"][0]] == ["id", "user", "hash", "issued"]


def test_issue_records_the_optional_fields(tmp_path: Path) -> None:
    path = _path(tmp_path)
    _, record = tokens_module.issue(
        path, "alice@example.com", expires="2030-01-01T00:00:00+00:00", note="laptop"
    )
    assert record.note == "laptop"
    assert tokens_module.read(path)[0].expires == "2030-01-01T00:00:00+00:00"


def test_issue_refuses_a_token_with_no_owner(tmp_path: Path) -> None:
    """Naming a person is the point — an unattributable token is the thing being replaced."""
    with pytest.raises(TokenFileError, match="name a user"):
        tokens_module.issue(_path(tmp_path), "   ")


def test_issue_appends_rather_than_replacing(tmp_path: Path) -> None:
    """Rotation with an overlap window depends on this: the replacement is added while the
    old one still works, so there is no flag day."""
    path = _path(tmp_path)
    tokens_module.issue(path, "alice@example.com")
    tokens_module.issue(path, "bob@example.com")
    assert [r.user for r in tokens_module.read(path)] == ["alice@example.com", "bob@example.com"]


# --------------------------------------------------------------------------------------
# Reading: the tolerant and the intolerant cases.
# --------------------------------------------------------------------------------------


def test_a_missing_file_is_no_tokens_not_an_error(tmp_path: Path) -> None:
    """The compose stack bind-mounts /dev/null when no store is configured, and a fresh
    deployment has issued nothing yet."""
    assert tokens_module.read(tmp_path / "absent.json") == ()


def test_an_empty_file_is_no_tokens(tmp_path: Path) -> None:
    path = _path(tmp_path)
    path.write_text("")
    assert tokens_module.read(path) == ()


def test_malformed_json_is_an_error_naming_the_file(tmp_path: Path) -> None:
    path = _path(tmp_path)
    path.write_text("{not json")
    with pytest.raises(TokenFileError, match="not valid JSON"):
        tokens_module.read(path)


def test_an_unknown_version_is_refused(tmp_path: Path) -> None:
    """A newer `kb` writing a format this one cannot read must fail loudly, not
    authenticate no one."""
    path = _path(tmp_path)
    path.write_text(json.dumps({"version": 99, "tokens": []}))
    with pytest.raises(TokenFileError, match="version 99"):
        tokens_module.read(path)


def test_a_record_missing_a_field_is_refused(tmp_path: Path) -> None:
    path = _path(tmp_path)
    path.write_text(json.dumps({"version": 1, "tokens": [{"id": "a", "user": "b"}]}))
    with pytest.raises(TokenFileError, match="missing hash, issued"):
        tokens_module.read(path)


def test_an_unsupported_hash_is_refused(tmp_path: Path) -> None:
    path = _path(tmp_path)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "tokens": [
                    {"id": "a", "user": "b", "hash": "md5:deadbeef", "issued": "2026-01-01T00:00:00+00:00"}
                ],
            }
        )
    )
    with pytest.raises(TokenFileError, match="unsupported hash 'md5'"):
        tokens_module.read(path)


def test_duplicate_ids_are_refused(tmp_path: Path) -> None:
    path = _path(tmp_path)
    _, record = tokens_module.issue(path, "alice@example.com")
    tokens_module.write(path, (record, record))
    with pytest.raises(TokenFileError, match="duplicate token id"):
        tokens_module.read(path)


# --------------------------------------------------------------------------------------
# Status: revoked, expired, active.
# --------------------------------------------------------------------------------------


def test_revoke_marks_the_record_and_leaves_the_others(tmp_path: Path) -> None:
    path = _path(tmp_path)
    _, alice = tokens_module.issue(path, "alice@example.com")
    _, bob = tokens_module.issue(path, "bob@example.com")
    tokens_module.revoke(path, alice.id)
    by_id = {r.id: r for r in tokens_module.read(path)}
    assert by_id[alice.id].status == "revoked"
    assert by_id[bob.id].status == "active"


def test_revoke_is_idempotent_and_keeps_the_first_timestamp(tmp_path: Path) -> None:
    """When access was withdrawn is the fact worth preserving."""
    path = _path(tmp_path)
    _, record = tokens_module.issue(path, "alice@example.com")
    first = tokens_module.revoke(path, record.id).revoked
    assert tokens_module.revoke(path, record.id).revoked == first


def test_revoke_of_an_unknown_id_is_an_error(tmp_path: Path) -> None:
    path = _path(tmp_path)
    tokens_module.issue(path, "alice@example.com")
    with pytest.raises(TokenFileError, match="no token with id 'nope'"):
        tokens_module.revoke(path, "nope")


def test_an_expired_token_does_not_resolve(tmp_path: Path) -> None:
    path = _path(tmp_path)
    secret, _ = tokens_module.issue(path, "alice@example.com", expires="2000-01-01T00:00:00+00:00")
    assert tokens_module.read(path)[0].status == "expired"
    assert TokenStore(path=path).resolve(secret) is None


def test_a_future_expiry_still_resolves(tmp_path: Path) -> None:
    path = _path(tmp_path)
    secret, _ = tokens_module.issue(path, "alice@example.com", expires="2099-01-01T00:00:00+00:00")
    identity = TokenStore(path=path).resolve(secret)
    assert identity is not None and identity.user == "alice@example.com"


def test_a_naive_expiry_is_read_as_utc(tmp_path: Path) -> None:
    """Forgives a hand-edited file; everything this module writes carries an offset."""
    record = TokenRecord(
        id="x", user="u", hash=tokens_module.hash_secret("s"),
        issued="2026-01-01T00:00:00", expires="2000-01-01T00:00:00",
    )
    assert record.status == "expired"


def test_a_garbled_expiry_is_an_error_not_silently_active() -> None:
    record = TokenRecord(
        id="x", user="u", hash=tokens_module.hash_secret("s"),
        issued="2026-01-01T00:00:00+00:00", expires="whenever",
    )
    with pytest.raises(TokenFileError, match="ISO-8601"):
        _ = record.status


# --------------------------------------------------------------------------------------
# Resolving, and the cache. This is where "no restart" is actually earned.
# --------------------------------------------------------------------------------------


def test_a_wrong_secret_resolves_to_nothing(tmp_path: Path) -> None:
    path = _path(tmp_path)
    tokens_module.issue(path, "alice@example.com")
    assert TokenStore(path=path).resolve("not-a-token") is None


def test_static_tokens_resolve_without_an_owner() -> None:
    identity = TokenStore(static_tokens=("legacy",)).resolve("legacy")
    assert identity is not None
    assert identity.source == "env"
    assert identity.user == "(KB_TOKENS)"


def test_a_new_token_is_picked_up_without_rebuilding_the_store(tmp_path: Path) -> None:
    path = _path(tmp_path)
    store = TokenStore(path=path, cache_seconds=0.0)
    assert store.resolve("anything") is None  # loads an absent file
    secret, _ = tokens_module.issue(path, "alice@example.com")
    assert store.resolve(secret) is not None


def test_the_cache_is_not_consulted_again_within_the_window(tmp_path: Path) -> None:
    """A stale window is the deliberate trade for not stat'ing the file on every request;
    `KB_TOKEN_CACHE_SECONDS` is the bound on how long a revocation can lag."""
    path = _path(tmp_path)
    secret, record = tokens_module.issue(path, "alice@example.com")
    now = [1000.0]
    store = TokenStore(path=path, cache_seconds=5.0, clock=lambda: now[0])
    assert store.resolve(secret) is not None

    tokens_module.revoke(path, record.id)
    now[0] += 4.0
    assert store.resolve(secret) is not None, "within the window, the cached copy is used"
    now[0] += 2.0
    assert store.resolve(secret) is None, "past the window, the revocation is in force"


def test_an_unchanged_file_is_not_re_read(tmp_path: Path) -> None:
    """Past the window the file is stat'd, but only a changed mtime/size costs a read."""
    path = _path(tmp_path)
    secret, _ = tokens_module.issue(path, "alice@example.com")
    store = TokenStore(path=path, cache_seconds=0.0)
    assert store.resolve(secret) is not None

    calls = [0]
    real_read = tokens_module.read

    def counting_read(p: Path):
        calls[0] += 1
        return real_read(p)

    tokens_module.read = counting_read  # noqa: SLF001 - simplest possible spy
    try:
        store.resolve(secret)
        store.resolve(secret)
    finally:
        tokens_module.read = real_read
    assert calls[0] == 0


def test_a_broken_edit_keeps_the_last_good_copy_serving(tmp_path: Path) -> None:
    """Fail safe towards continuity: a typo in the file must not lock out every user at
    once, which is precisely the outage this work removes. `kb serve` refuses to *start*
    on a broken file (see `test_cli_errors.py`); this is about breaking it mid-flight."""
    path = _path(tmp_path)
    secret, _ = tokens_module.issue(path, "alice@example.com")
    store = TokenStore(path=path, cache_seconds=0.0)
    assert store.resolve(secret) is not None

    path.write_text("{ broken")
    assert store.resolve(secret) is not None
    assert store.last_error is not None and "not valid JSON" in store.last_error

    # And it recovers on its own once the file is valid again.
    tokens_module.write(path, ())
    assert store.resolve(secret) is None
    assert store.last_error is None


def test_a_broken_file_with_no_good_copy_raises(tmp_path: Path) -> None:
    """No last good copy means no safe way to continue; the caller (`kb serve`) turns this
    into a refusal to start rather than a server that authenticates nobody."""
    path = _path(tmp_path)
    path.write_text("{ broken")
    with pytest.raises(TokenFileError):
        TokenStore(path=path).resolve("anything")


def test_a_deleted_file_is_no_tokens_rather_than_the_stale_set(tmp_path: Path) -> None:
    """Deleting the store must not leave the credentials it held still working."""
    path = _path(tmp_path)
    secret, _ = tokens_module.issue(path, "alice@example.com")
    store = TokenStore(path=path, cache_seconds=0.0)
    assert store.resolve(secret) is not None
    path.unlink()
    assert store.resolve(secret) is None


# --------------------------------------------------------------------------------------
# `has_any_credential` — what `kb serve --transport http` gates on.
# --------------------------------------------------------------------------------------


def test_no_sources_means_no_credential() -> None:
    assert TokenStore().has_any_credential() is False


def test_static_tokens_count_as_a_credential() -> None:
    assert TokenStore(static_tokens=("legacy",)).has_any_credential() is True


def test_an_active_record_counts(tmp_path: Path) -> None:
    path = _path(tmp_path)
    tokens_module.issue(path, "alice@example.com")
    assert TokenStore(path=path).has_any_credential() is True


def test_only_revoked_records_do_not_count(tmp_path: Path) -> None:
    """A store whose every token has been revoked is not a usable server."""
    path = _path(tmp_path)
    _, record = tokens_module.issue(path, "alice@example.com")
    tokens_module.revoke(path, record.id)
    assert TokenStore(path=path).has_any_credential() is False


# --------------------------------------------------------------------------------------
# Writing.
# --------------------------------------------------------------------------------------


def test_write_leaves_no_temp_file_behind(tmp_path: Path) -> None:
    path = _path(tmp_path)
    tokens_module.issue(path, "alice@example.com")
    assert [p.name for p in tmp_path.iterdir()] == ["tokens.json"]


def test_write_creates_the_parent_directory(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "deeper" / "tokens.json"
    tokens_module.issue(path, "alice@example.com")
    assert path.is_file()


# --------------------------------------------------------------------------------------
# Who can still read the store after a write.
#
# Measured 2026-10-01 against the real compose stack, not reasoned about: `kb-mcp` runs as
# a non-root uid (compose/Dockerfile, `--uid 10001`) and could not read a root-owned 0600
# store at all. `kb serve` exited 2 on every start, the container crash-looped, Caddy
# answered 502, and every file-backed token was dead — the same outage the revocation work
# exists to remove, arriving by a different route.
#
# Whatever ownership and mode a deployment sets therefore has to SURVIVE each `kb token`
# write. `write` replaces the file (temp file + `os.replace`), so without this the operator
# chowns the store once and the next `issue` or `revoke` silently undoes it. That is the
# same shape as the inode bug in tests/test_compose_token_mount.py: a correct fix that the
# next write quietly reverts.
# --------------------------------------------------------------------------------------


def test_a_new_file_is_still_owner_readable_only(tmp_path: Path) -> None:
    """Preserving an existing file's mode must not change what a *fresh* store gets."""
    path = _path(tmp_path)
    tokens_module.write(path, ())
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_write_preserves_an_existing_files_mode(tmp_path: Path) -> None:
    path = _path(tmp_path)
    tokens_module.issue(path, "alice@example.com")
    path.chmod(0o640)
    tokens_module.issue(path, "bob@example.com")
    assert stat.S_IMODE(path.stat().st_mode) == 0o640, (
        "a deployment that widened the mode so the server's uid can read it had that "
        "undone by the next `kb token` write"
    )


def test_revoke_preserves_an_existing_files_mode(tmp_path: Path) -> None:
    """Revocation is the write that matters most: it must not be the one that locks the
    server out of the store."""
    path = _path(tmp_path)
    _, record = tokens_module.issue(path, "alice@example.com")
    path.chmod(0o640)
    tokens_module.revoke(path, record.id)
    assert stat.S_IMODE(path.stat().st_mode) == 0o640


@pytest.mark.skipif(os.geteuid() != 0, reason="chowning to another uid needs root")
def test_write_preserves_an_existing_files_owner(tmp_path: Path) -> None:
    """The compose case exactly: the store is chowned to the uid the server container runs
    as, and `kb token` is then run by root on the host."""
    path = _path(tmp_path)
    tokens_module.issue(path, "alice@example.com")
    os.chown(path, 10001, 10001)
    tokens_module.issue(path, "bob@example.com")
    st = path.stat()
    assert (st.st_uid, st.st_gid) == (10001, 10001), (
        "`kb token` gave the store back to root, so the server's uid lost its read"
    )


@pytest.mark.skipif(os.geteuid() != 0, reason="chowning to another uid needs root")
def test_the_records_survive_the_ownership_handover(tmp_path: Path) -> None:
    """Preserving the metadata must not cost the content."""
    path = _path(tmp_path)
    secret, _ = tokens_module.issue(path, "alice@example.com")
    os.chown(path, 10001, 10001)
    tokens_module.issue(path, "bob@example.com")
    users = {r.user for r in tokens_module.read(path)}
    assert users == {"alice@example.com", "bob@example.com"}
    assert TokenStore(path=path).resolve(secret) is not None
