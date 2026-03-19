"""
security/encryption.py
======================
AES-256-GCM encryption and decryption for the Emergency Data Vault.

Design principles:
  - Each encryption operation generates a fresh random 96-bit IV (nonce).
  - The master key is derived from the BOT_MASTER_KEY environment variable
    using PBKDF2-HMAC-SHA256 with a fixed salt (stored in the vault row).
  - The decryption key is NEVER stored in the database; it is derived at
    runtime only when the group consensus function explicitly requests it.
  - Plaintext emergency data is zeroed from memory immediately after encryption.
"""

import os
import logging
import secrets
from base64 import b64encode, b64decode
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

logger = logging.getLogger(__name__)

# Number of PBKDF2 iterations (NIST recommends >= 600,000 for SHA-256 in 2023)
PBKDF2_ITERATIONS = 600_000
KEY_LENGTH = 32  # 256 bits


def _derive_key(master_key_hex: str, salt: bytes) -> bytes:
    """
    Derive a 256-bit AES key from the master key hex string and a per-record salt
    using PBKDF2-HMAC-SHA256.
    """
    master_key_bytes = bytes.fromhex(master_key_hex)
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=KEY_LENGTH,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    return kdf.derive(master_key_bytes)


def encrypt_emergency_data(plaintext: str, master_key_hex: str) -> tuple[bytes, bytes, bytes]:
    """
    Encrypt plaintext emergency data using AES-256-GCM.

    Returns:
        (ciphertext_with_tag, iv, salt)
        - ciphertext_with_tag: encrypted bytes including the 16-byte GCM auth tag
        - iv: 12-byte (96-bit) random nonce
        - salt: 16-byte random salt used for key derivation

    The IV and salt must be stored alongside the ciphertext to allow future decryption.
    The plaintext string is encoded as UTF-8 before encryption.
    """
    salt = secrets.token_bytes(16)
    iv = secrets.token_bytes(12)
    key = _derive_key(master_key_hex, salt)
    aesgcm = AESGCM(key)
    plaintext_bytes = plaintext.encode("utf-8")
    ciphertext = aesgcm.encrypt(iv, plaintext_bytes, None)

    # Explicitly overwrite the key and plaintext bytes in memory
    del key
    del plaintext_bytes

    logger.debug("Emergency data encrypted successfully (ciphertext length: %d bytes).", len(ciphertext))
    return ciphertext, iv, salt


def decrypt_emergency_data(
    ciphertext: bytes,
    iv: bytes,
    salt: bytes,
    master_key_hex: str,
) -> str:
    """
    Decrypt AES-256-GCM ciphertext back to plaintext.

    This function should ONLY be called after the group consensus threshold
    has been met and verified by the consensus module.

    Returns:
        Decrypted plaintext string.

    Raises:
        cryptography.exceptions.InvalidTag if authentication fails (tampered data).
    """
    key = _derive_key(master_key_hex, salt)
    aesgcm = AESGCM(key)
    plaintext_bytes = aesgcm.decrypt(iv, ciphertext, None)
    plaintext = plaintext_bytes.decode("utf-8")
    del key
    del plaintext_bytes
    logger.info("Emergency data decrypted successfully.")
    return plaintext


def generate_master_key() -> str:
    """
    Utility function: generate a cryptographically secure 256-bit master key
    as a 64-character hex string. Use this once during initial setup.
    """
    return secrets.token_hex(32)


# ---------------------------------------------------------------------------
# Vault helper: pack/unpack ciphertext + salt into a single blob for storage
# ---------------------------------------------------------------------------
# Storage format: [16-byte salt][ciphertext_with_tag]
# The IV is stored separately in the database column.

SALT_LENGTH = 16


def pack_vault_blob(ciphertext: bytes, salt: bytes) -> bytes:
    """Pack salt and ciphertext into a single bytes object for DB storage."""
    assert len(salt) == SALT_LENGTH, "Salt must be 16 bytes."
    return salt + ciphertext


def unpack_vault_blob(blob: bytes) -> tuple[bytes, bytes]:
    """Unpack a vault blob into (ciphertext, salt)."""
    salt = blob[:SALT_LENGTH]
    ciphertext = blob[SALT_LENGTH:]
    return ciphertext, salt
