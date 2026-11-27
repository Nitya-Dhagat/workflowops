"""Salted scrypt password hashing (stdlib only).

Format: ``scrypt$<n>$<salt hex>$<digest hex>``. The cost ``n`` is stored in each hash, so it can
be raised later without invalidating existing passwords. scrypt is deliberately CPU- and
memory-hard: call the ``*_async`` variants from async code so the event loop is never blocked.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets

DEFAULT_N = 2**14


def hash_password(password: str, *, n: int = DEFAULT_N, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=n, r=8, p=1, dklen=32, maxmem=64 * 1024 * 1024
    )
    return f"scrypt${n}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored or not stored.startswith("scrypt$"):
        return False
    try:
        _, n_text, salt_hex, digest_hex = stored.split("$")
        n = int(n_text)
    except ValueError:
        return False
    candidate = hash_password(password, n=n, salt=bytes.fromhex(salt_hex)).split("$")[3]
    return hmac.compare_digest(candidate, digest_hex)


async def hash_password_async(password: str, *, n: int = DEFAULT_N) -> str:
    return await asyncio.to_thread(hash_password, password, n=n)


async def verify_password_async(password: str, stored: str | None) -> bool:
    return await asyncio.to_thread(verify_password, password, stored)
