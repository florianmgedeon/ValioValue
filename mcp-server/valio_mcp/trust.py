"""Canonical hashing and signatures.

* Canonical JSON: RFC 8785 (JCS), so the same content always gives the same bytes.
* Hash: SHA-256 over those bytes.
* Signatures: Ed25519 (RFC 8032), base64url without padding.

Demo keys live in data/keys. Production: buyer keys in a wallet or passkey, Valio key in an HSM.
"""
from __future__ import annotations

import base64
import hashlib
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

import rfc8785
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from . import config

log = logging.getLogger("valio_mcp.trust")


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64u(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def canonical(obj: Any) -> bytes:
    return rfc8785.dumps(obj)


def digest(obj: Any) -> str:
    """'sha256:<hex>' over the JCS form."""
    return "sha256:" + hashlib.sha256(canonical(obj)).hexdigest()


def fingerprint(h: str) -> str:
    """Short display form, e.g. 7F3A-91C2."""
    hexpart = h.split(":", 1)[-1].upper()
    return f"{hexpart[:4]}-{hexpart[4:8]}"


# --------------------------------------------------------------------------- keys

def _load_or_create(path: Path) -> Ed25519PrivateKey:
    if path.exists():
        return serialization.load_pem_private_key(path.read_bytes(), password=None)  # type: ignore[return-value]
    path.parent.mkdir(parents=True, exist_ok=True)
    key = Ed25519PrivateKey.generate()
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
    path.chmod(0o600)
    log.info("generated demo key %s", path)
    return key


def public_b64(key: Ed25519PublicKey) -> str:
    return b64u(key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))


def public_from_b64(text: str) -> Ed25519PublicKey:
    return Ed25519PublicKey.from_public_bytes(unb64u(text))


def key_id(pub: Ed25519PublicKey) -> str:
    return "ed25519:" + hashlib.sha256(unb64u(public_b64(pub))).hexdigest()[:16]


@lru_cache(maxsize=1)
def valio_key() -> Ed25519PrivateKey:
    return _load_or_create(config.KEYS_DIR / "valio_aimo_ed25519.pem")


def valio_public() -> dict:
    pub = valio_key().public_key()
    return {"key_id": key_id(pub), "public_key": public_b64(pub), "alg": "Ed25519"}


def buyer_key(buyer_id: str) -> Ed25519PrivateKey:
    """Demo only: the buyer's key. In this repo both services share data/keys, so the buyer backend
    creates it and Valio reads the public half. Production: registered at onboarding, private key never leaves
    the buyer's device."""
    return _load_or_create(config.KEYS_DIR / "buyers" / f"{buyer_id}_ed25519.pem")


def buyer_public(buyer_id: str) -> Ed25519PublicKey:
    return buyer_key(buyer_id).public_key()


# --------------------------------------------------------------------------- sign / verify

def sign(key: Ed25519PrivateKey, message: str) -> str:
    return b64u(key.sign(message.encode()))


def verify(pub: Ed25519PublicKey, message: str, signature: str | None) -> bool:
    if not signature:
        return False
    try:
        pub.verify(unb64u(signature), message.encode())
        return True
    except (InvalidSignature, ValueError):
        return False


def valio_sign_obj(obj: Any) -> dict:
    h = digest(obj)
    return {"hash": h, "signature": sign(valio_key(), h), "key_id": valio_public()["key_id"]}
