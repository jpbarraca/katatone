"""The password that protects the web GUI.

A single password (no user names: the device has one owner), kept as a salted
scrypt hash in ``auth.json`` in the data directory (mode 0600).  Until one is
set, the factory password applies and the GUI makes the user replace it at the
first login.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path

from . import rofs
from .paths import data_root

#: The password of a device that has none set yet.
DEFAULT_PASSWORD = "KataTone"
MIN_LENGTH = 8
#: This many wrong passwords in a row lock the login for LOCKOUT_S seconds.
MAX_FAILURES = 5
LOCKOUT_S = 30.0

_SCRYPT = dict(n=2 ** 14, r=8, p=1, dklen=32)


class AuthError(ValueError):
    """A password that cannot be accepted."""


def _hash(password: str, salt: bytes) -> bytes:
    """The scrypt hash of ``password``."""
    return hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)


class LaunchTokens:
    """Short-lived tokens that let a signed-in session open the sound-card tools
    (``/analyzer``, ``/tuner``) in a new tab without logging in again.

    A token is minted for each browser session that shows the app, goes into the
    tools' links, and is revoked when that session ends or is replaced.  It opens
    only the tools, never the app.  Kept in memory: a restart revokes them all.
    """

    MAX_TOKENS = 32

    def __init__(self, ttl: float = 8 * 3600.0, clock=time.monotonic) -> None:
        """
        Args:
            ttl: seconds a token stays valid.
            clock: monotonic time source (tests replace it).
        """
        self._ttl, self._clock = ttl, clock
        self._tokens: dict[str, float] = {}         # token -> expiry
        self._lock = threading.Lock()

    def issue(self) -> str:
        """Mint a new token."""
        token = secrets.token_urlsafe(24)
        with self._lock:
            now = self._clock()
            self._tokens = {t: e for t, e in self._tokens.items() if e > now}
            while len(self._tokens) >= self.MAX_TOKENS:
                self._tokens.pop(next(iter(self._tokens)))      # the oldest
            self._tokens[token] = now + self._ttl
        return token

    def valid(self, token: str | None) -> bool:
        """Whether ``token`` was issued, is not revoked and has not expired."""
        if not token:
            return False
        with self._lock:
            expiry = self._tokens.get(token)
            return expiry is not None and expiry > self._clock()

    def revoke(self, token: str | None) -> None:
        """Forget ``token`` (no error if it is unknown)."""
        with self._lock:
            self._tokens.pop(token, None)


class Auth:
    """Checks and changes the GUI password, and throttles wrong guesses."""

    def __init__(self, path: Path | None = None, *, clock=time.monotonic) -> None:
        """
        Args:
            path: the credentials file (default: ``auth.json`` in the data dir).
            clock: monotonic time source (tests replace it).
        """
        self.path = Path(path) if path else data_root() / "auth.json"
        self._clock = clock
        self._lock = threading.Lock()
        self._failures = 0
        self._locked_until = 0.0

    def _load(self) -> dict | None:
        """The stored credentials, or None when the factory password applies."""
        try:
            data = json.loads(self.path.read_text())
            return {"salt": bytes.fromhex(data["salt"]),
                    "hash": bytes.fromhex(data["hash"])}
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def is_default(self) -> bool:
        """True while the factory password is in use (nothing stored)."""
        return self._load() is None

    def lockout_remaining(self) -> int:
        """Seconds until logins are accepted again (0 when not locked)."""
        with self._lock:
            return max(0, int(self._locked_until - self._clock() + 0.999))

    def check(self, password: str) -> bool:
        """Test a login attempt.

        Wrong guesses are counted; after :data:`MAX_FAILURES` in a row every
        attempt is refused (even a right one) for :data:`LOCKOUT_S` seconds.

        Args:
            password: what the user typed.
        Returns:
            True when it is the password.
        """
        with self._lock:
            if self._clock() < self._locked_until:
                return False
        stored = self._load()
        if stored is None:
            ok = hmac.compare_digest(password.encode(), DEFAULT_PASSWORD.encode())
        else:
            ok = hmac.compare_digest(_hash(password, stored["salt"]), stored["hash"])
        with self._lock:
            if ok:
                self._failures = 0
            else:
                self._failures += 1
                if self._failures >= MAX_FAILURES:
                    self._failures = 0
                    self._locked_until = self._clock() + LOCKOUT_S
        return ok

    def set_password(self, password: str) -> None:
        """Replace the password.

        Args:
            password: the new password, at least :data:`MIN_LENGTH` characters
                and not the factory one.
        Raises:
            AuthError: if it is too short or the factory password.
            OSError: if the file cannot be written.
        """
        if len(password) < MIN_LENGTH:
            raise AuthError(f"The password needs at least {MIN_LENGTH} characters.")
        if password == DEFAULT_PASSWORD:
            raise AuthError("Choose a password other than the default one.")
        salt = secrets.token_bytes(16)
        with rofs.writable(self.path.parent):
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump({"salt": salt.hex(), "hash": _hash(password, salt).hex()}, f)
            os.replace(tmp, self.path)
