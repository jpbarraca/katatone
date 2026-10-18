"""The web GUI password: factory default, hashing, lockout."""
from __future__ import annotations

import json
import stat

import pytest

from katatone.auth import (DEFAULT_PASSWORD, LOCKOUT_S, MAX_FAILURES, Auth,
                           AuthError, LaunchTokens)


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


@pytest.fixture
def auth(tmp_path):
    return Auth(tmp_path / "auth.json", clock=Clock())


def test_factory_password_until_one_is_set(auth) -> None:
    assert auth.is_default()
    assert auth.check(DEFAULT_PASSWORD) and not auth.check("nope")


def test_set_password_replaces_the_factory_one(auth) -> None:
    auth.set_password("correct horse")
    assert not auth.is_default()
    assert auth.check("correct horse")
    assert not auth.check(DEFAULT_PASSWORD)


def test_password_is_stored_hashed_with_private_permissions(auth) -> None:
    auth.set_password("correct horse")
    assert "correct horse" not in auth.path.read_text()
    assert set(json.loads(auth.path.read_text())) == {"salt", "hash"}
    assert stat.S_IMODE(auth.path.stat().st_mode) == 0o600


def test_weak_passwords_are_refused(auth) -> None:
    for bad in ("short", DEFAULT_PASSWORD):
        with pytest.raises(AuthError):
            auth.set_password(bad)
    assert auth.is_default()


def test_a_damaged_file_falls_back_to_the_factory_password(auth) -> None:
    auth.path.write_text("{not json")
    assert auth.is_default()


def test_repeated_failures_lock_out_even_the_right_password(auth) -> None:
    clock = auth._clock
    for _ in range(MAX_FAILURES):
        assert not auth.check("wrong")
    assert auth.lockout_remaining() > 0
    assert not auth.check(DEFAULT_PASSWORD)
    clock.t += LOCKOUT_S + 1
    assert auth.lockout_remaining() == 0
    assert auth.check(DEFAULT_PASSWORD)


def test_a_success_resets_the_failure_count(auth) -> None:
    for _ in range(MAX_FAILURES - 1):
        auth.check("wrong")
    assert auth.check(DEFAULT_PASSWORD)
    for _ in range(MAX_FAILURES - 1):
        auth.check("wrong")
    assert auth.lockout_remaining() == 0


def test_launch_tokens_are_random_valid_until_revoked_or_expired() -> None:
    clock = Clock()
    tokens = LaunchTokens(ttl=100, clock=clock)
    a, b = tokens.issue(), tokens.issue()
    assert a != b and len(a) >= 30
    assert tokens.valid(a) and tokens.valid(b)
    assert not tokens.valid(None) and not tokens.valid("") and not tokens.valid("guess")
    tokens.revoke(a)
    tokens.revoke("unknown")                       # harmless
    assert not tokens.valid(a) and tokens.valid(b)
    clock.t += 101
    assert not tokens.valid(b)


def test_launch_tokens_are_capped() -> None:
    tokens = LaunchTokens()
    first = tokens.issue()
    for _ in range(LaunchTokens.MAX_TOKENS):
        tokens.issue()
    assert not tokens.valid(first)                 # the oldest made room
