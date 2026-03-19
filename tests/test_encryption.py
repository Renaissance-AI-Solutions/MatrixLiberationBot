"""
tests/test_encryption.py
========================
Unit tests for the AES-256-GCM encryption/decryption module.
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from security.encryption import (
    encrypt_emergency_data,
    decrypt_emergency_data,
    pack_vault_blob,
    unpack_vault_blob,
    generate_master_key,
)


class TestEncryption:
    """Test suite for the encryption module."""

    def setup_method(self):
        self.master_key = generate_master_key()
        self.plaintext = (
            "Emergency contact: Jane Doe, +1-555-0100. "
            "Investigative lead: Check the encrypted drive at location X. "
            "Last wishes: Donate to EFF."
        )

    def test_generate_master_key_length(self):
        key = generate_master_key()
        assert len(key) == 64, "Master key should be 64 hex characters (256 bits)."

    def test_encrypt_returns_bytes(self):
        ciphertext, iv, salt = encrypt_emergency_data(self.plaintext, self.master_key)
        assert isinstance(ciphertext, bytes)
        assert isinstance(iv, bytes)
        assert isinstance(salt, bytes)

    def test_iv_length(self):
        _, iv, _ = encrypt_emergency_data(self.plaintext, self.master_key)
        assert len(iv) == 12, "IV should be 12 bytes for AES-GCM."

    def test_salt_length(self):
        _, _, salt = encrypt_emergency_data(self.plaintext, self.master_key)
        assert len(salt) == 16, "Salt should be 16 bytes."

    def test_encrypt_decrypt_roundtrip(self):
        ciphertext, iv, salt = encrypt_emergency_data(self.plaintext, self.master_key)
        recovered = decrypt_emergency_data(ciphertext, iv, salt, self.master_key)
        assert recovered == self.plaintext

    def test_different_ivs_each_encryption(self):
        _, iv1, _ = encrypt_emergency_data(self.plaintext, self.master_key)
        _, iv2, _ = encrypt_emergency_data(self.plaintext, self.master_key)
        assert iv1 != iv2, "Each encryption should use a unique IV."

    def test_ciphertext_differs_each_time(self):
        ct1, _, _ = encrypt_emergency_data(self.plaintext, self.master_key)
        ct2, _, _ = encrypt_emergency_data(self.plaintext, self.master_key)
        assert ct1 != ct2, "Ciphertext should differ due to random IV/salt."

    def test_wrong_key_raises(self):
        ciphertext, iv, salt = encrypt_emergency_data(self.plaintext, self.master_key)
        wrong_key = generate_master_key()
        with pytest.raises(Exception):
            decrypt_emergency_data(ciphertext, iv, salt, wrong_key)

    def test_tampered_ciphertext_raises(self):
        ciphertext, iv, salt = encrypt_emergency_data(self.plaintext, self.master_key)
        tampered = bytearray(ciphertext)
        tampered[0] ^= 0xFF  # Flip bits
        with pytest.raises(Exception):
            decrypt_emergency_data(bytes(tampered), iv, salt, self.master_key)

    def test_pack_unpack_vault_blob(self):
        ciphertext, iv, salt = encrypt_emergency_data(self.plaintext, self.master_key)
        blob = pack_vault_blob(ciphertext, salt)
        recovered_ct, recovered_salt = unpack_vault_blob(blob)
        assert recovered_ct == ciphertext
        assert recovered_salt == salt

    def test_full_vault_roundtrip(self):
        """Simulate the full vault store-and-retrieve cycle."""
        ciphertext, iv, salt = encrypt_emergency_data(self.plaintext, self.master_key)
        blob = pack_vault_blob(ciphertext, salt)

        # Simulate retrieval from DB
        recovered_ct, recovered_salt = unpack_vault_blob(blob)
        recovered_text = decrypt_emergency_data(
            recovered_ct, iv, recovered_salt, self.master_key
        )
        assert recovered_text == self.plaintext

    def test_unicode_plaintext(self):
        unicode_text = "Emergency: 緊急連絡先 — Ñoño — Привет — 🔐"
        ciphertext, iv, salt = encrypt_emergency_data(unicode_text, self.master_key)
        recovered = decrypt_emergency_data(ciphertext, iv, salt, self.master_key)
        assert recovered == unicode_text

    def test_large_plaintext(self):
        large_text = "A" * 100_000
        ciphertext, iv, salt = encrypt_emergency_data(large_text, self.master_key)
        recovered = decrypt_emergency_data(ciphertext, iv, salt, self.master_key)
        assert recovered == large_text
