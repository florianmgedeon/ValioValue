"""The buyer's signing key. Signs exactly the hash the human was shown.

Demo: key file in data/keys/buyers (shared with the local Valio server, which reads the public half).
Production: a passkey or wallet on the buyer's device (WebAuthn), the private key never reaches a server.
"""
from __future__ import annotations

import base64
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from . import config


def _key(buyer_id: str) -> Ed25519PrivateKey:
    path = Path(config.KEYS_DIR) / "buyers" / f"{buyer_id}_ed25519.pem"
    if path.exists():
        return serialization.load_pem_private_key(path.read_bytes(), password=None)  # type: ignore[return-value]
    path.parent.mkdir(parents=True, exist_ok=True)
    key = Ed25519PrivateKey.generate()
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
    path.chmod(0o600)
    return key


def sign(buyer_id: str, message_hash: str) -> str:
    sig = _key(buyer_id).sign(message_hash.encode())
    return base64.urlsafe_b64encode(sig).rstrip(b"=").decode()
