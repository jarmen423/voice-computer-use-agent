"""Tests for updater integrity checks."""

from __future__ import annotations

import hashlib

from voiceuse.updater import UpdateInfo, Updater


def test_supported_signature_requires_sha256_hex() -> None:
    """The updater should fail closed for missing or placeholder signatures."""
    assert Updater._has_supported_signature("") is False
    assert Updater._has_supported_signature("sha256:placeholder") is False
    assert Updater._has_supported_signature("ed25519:abc") is False
    assert Updater._has_supported_signature("sha256:" + "a" * 64) is True


def test_verify_sha256_matches_file_contents(tmp_path) -> None:
    """A downloaded file is valid only when its digest matches the manifest."""
    payload = b"voiceuse update binary"
    update_path = tmp_path / "VoiceUse.exe"
    update_path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()

    assert Updater._verify_sha256(update_path, f"sha256:{digest}") is True
    assert Updater._verify_sha256(update_path, "sha256:" + "0" * 64) is False


def test_update_info_reads_signature() -> None:
    """Manifest parsing should preserve the signature for verification."""
    info = UpdateInfo({"version": "1.2.3", "url": "https://example.test/app", "signature": "sha256:" + "a" * 64})
    assert info.signature == "sha256:" + "a" * 64
