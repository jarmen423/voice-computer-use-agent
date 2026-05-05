"""License client for VoiceUse commercial builds.

Manages trial periods, license key activation, and validation against
the license server.  All API keys and license tokens are stored in the
OS keychain (Windows Credential Manager / macOS Keychain / Linux Secret
Service) when the ``keyring`` package is available; otherwise they fall
back to an encrypted local file.
"""

import base64
import hashlib
import json
import logging
import os
import platform
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("voiceuse.licensing")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LICENSE_SERVER_URL = os.environ.get(
    "VOICEUSE_LICENSE_URL", "https://license.voiceuse.ai/v1"
)
TRIAL_DAYS = 7
LICENSE_FILE_NAME = ".voiceuse_license"


# ---------------------------------------------------------------------------
# License data structures
# ---------------------------------------------------------------------------

@dataclass
class LicenseInfo:
    """Parsed license state."""

    status: str  # "trial" | "active" | "expired" | "revoked"
    license_key: Optional[str] = None
    expires_at: Optional[float] = None  # Unix timestamp
    tier: str = "free"  # "trial" | "pro"
    machine_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Machine fingerprint
# ---------------------------------------------------------------------------

def get_machine_id() -> str:
    """Generate a stable machine identifier.

    Uses a combination of OS-level identifiers so the same physical
    machine produces the same fingerprint across reinstalls.
    """
    components: list[str] = []

    # Platform info
    components.append(platform.node())
    components.append(platform.machine())
    components.append(platform.processor())

    # OS-specific stable identifiers
    if platform.system() == "Windows":
        try:
            import winreg  # type: ignore[import]
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Windows NT\CurrentVersion",
            ) as key:
                product_id, _ = winreg.QueryValueEx(key, "ProductId")
                components.append(product_id)
        except Exception:
            pass
    elif platform.system() == "Linux":
        try:
            with open("/etc/machine-id", "r") as f:
                components.append(f.read().strip())
        except Exception:
            pass
    elif platform.system() == "Darwin":
        try:
            import subprocess
            out = subprocess.check_output(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                text=True,
            )
            for line in out.splitlines():
                if "IOPlatformUUID" in line:
                    components.append(line.split('"')[-2])
                    break
        except Exception:
            pass

    # MAC addresses (stable but may change with hardware)
    try:
        import uuid
        for iface in [uuid.getnode()]:
            components.append(str(iface))
    except Exception:
        pass

    raw = "|".join(components).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Keychain / secure storage
# ---------------------------------------------------------------------------

try:
    import keyring  # type: ignore[import]
except ImportError:  # pragma: no cover
    keyring = None  # type: ignore[assignment]


_KEYRING_SERVICE = "voiceuse"
_KEYRING_USERNAME_LICENSE = "license_token"
_KEYRING_USERNAME_API_KEYS = "api_keys"


def _license_file_path() -> Path:
    """Return the path to the encrypted fallback license file."""
    if platform.system() == "Windows":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    elif platform.system() == "Darwin":
        base = Path.home() / "Library/Application Support"
    else:
        base = Path.home() / ".local/share"
    dir_path = base / "VoiceUse"
    dir_path.mkdir(parents=True, exist_ok=True)
    return dir_path / LICENSE_FILE_NAME


def _encrypt(data: str, password: str) -> str:
    """Simple XOR-based obfuscation (not true encryption, but sufficient
    to stop casual inspection).  In production consider Fernet.
    """
    key = hashlib.sha256(password.encode()).digest()
    out = bytearray()
    for i, ch in enumerate(data.encode("utf-8")):
        out.append(ch ^ key[i % len(key)])
    return base64.urlsafe_b64encode(bytes(out)).decode("ascii")


def _decrypt(token: str, password: str) -> str:
    key = hashlib.sha256(password.encode()).digest()
    raw = base64.urlsafe_b64decode(token.encode("ascii"))
    out = bytearray()
    for i, ch in enumerate(raw):
        out.append(ch ^ key[i % len(key)])
    return bytes(out).decode("utf-8")


def _storage_password() -> str:
    """Derive a local password from the machine fingerprint."""
    return get_machine_id()


def store_license_token(token: str) -> None:
    """Persist a license token securely."""
    if keyring is not None:
        try:
            keyring.set_password(_KEYRING_SERVICE, _KEYRING_USERNAME_LICENSE, token)
            logger.info("License token stored in OS keychain.")
            return
        except Exception as exc:
            logger.warning("Keychain storage failed (%s), falling back to file.", exc)

    # Fallback: encrypted file
    path = _license_file_path()
    encrypted = _encrypt(token, _storage_password())
    path.write_text(encrypted, encoding="utf-8")
    logger.info("License token stored in encrypted file: %s", path)


def load_license_token() -> Optional[str]:
    """Retrieve the stored license token, or None."""
    if keyring is not None:
        try:
            token = keyring.get_password(_KEYRING_SERVICE, _KEYRING_USERNAME_LICENSE)
            if token:
                return token
        except Exception as exc:
            logger.debug("Keychain read failed: %s", exc)

    # Fallback: encrypted file
    path = _license_file_path()
    if path.exists():
        try:
            encrypted = path.read_text(encoding="utf-8")
            return _decrypt(encrypted, _storage_password())
        except Exception as exc:
            logger.warning("Failed to decrypt license file: %s", exc)
    return None


def clear_license_token() -> None:
    """Remove any stored license token."""
    if keyring is not None:
        try:
            keyring.delete_password(_KEYRING_SERVICE, _KEYRING_USERNAME_LICENSE)
        except Exception:
            pass
    path = _license_file_path()
    if path.exists():
        path.unlink()


def store_api_keys(keys: Dict[str, str]) -> None:
    """Persist API keys securely."""
    payload = json.dumps(keys)
    if keyring is not None:
        try:
            keyring.set_password(_KEYRING_SERVICE, _KEYRING_USERNAME_API_KEYS, payload)
            return
        except Exception as exc:
            logger.warning("Keychain API key storage failed (%s), falling back to file.", exc)

    path = _license_file_path().with_suffix(".keys")
    encrypted = _encrypt(payload, _storage_password())
    path.write_text(encrypted, encoding="utf-8")


def load_api_keys() -> Dict[str, str]:
    """Retrieve stored API keys."""
    payload: Optional[str] = None
    if keyring is not None:
        try:
            payload = keyring.get_password(_KEYRING_SERVICE, _KEYRING_USERNAME_API_KEYS)
        except Exception:
            pass

    if payload is None:
        path = _license_file_path().with_suffix(".keys")
        if path.exists():
            try:
                encrypted = path.read_text(encoding="utf-8")
                payload = _decrypt(encrypted, _storage_password())
            except Exception:
                pass

    if payload:
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            pass
    return {}


# ---------------------------------------------------------------------------
# License validation (server + local)
# ---------------------------------------------------------------------------

class LicenseClient:
    """Client for the VoiceUse license server."""

    def __init__(self, base_url: str = LICENSE_SERVER_URL) -> None:
        self.base_url = base_url.rstrip("/")
        self._machine_id = get_machine_id()

    # ------------------------------------------------------------------
    # Local trial management
    # ------------------------------------------------------------------

    def get_local_license(self) -> LicenseInfo:
        """Determine the current license state from local storage."""
        # Check stored license token first
        token = load_license_token()
        if token:
            # TODO: validate token against server in production
            # For now, parse a simple local representation
            return LicenseInfo(
                status="active",
                license_key=token,
                tier="pro",
                machine_id=self._machine_id,
            )

        # Check trial
        trial_start = self._get_trial_start()
        if trial_start is None:
            # First ever run — start trial
            self._start_trial()
            return LicenseInfo(
                status="trial",
                tier="pro",
                expires_at=time.time() + TRIAL_DAYS * 86400,
                machine_id=self._machine_id,
            )

        expires_at = trial_start + TRIAL_DAYS * 86400
        if time.time() > expires_at:
            return LicenseInfo(
                status="expired",
                tier="free",
                machine_id=self._machine_id,
            )

        return LicenseInfo(
            status="trial",
            tier="pro",
            expires_at=expires_at,
            machine_id=self._machine_id,
        )

    def _get_trial_start(self) -> Optional[float]:
        """Return the trial start timestamp, or None if never started."""
        path = _license_file_path().with_suffix(".trial")
        if path.exists():
            try:
                return float(path.read_text(encoding="utf-8").strip())
            except ValueError:
                pass
        return None

    def _start_trial(self) -> None:
        """Record the trial start time."""
        path = _license_file_path().with_suffix(".trial")
        path.write_text(str(time.time()), encoding="utf-8")
        logger.info("Trial started (%d days).", TRIAL_DAYS)

    # ------------------------------------------------------------------
    # Activation
    # ------------------------------------------------------------------

    def activate(self, license_key: str) -> LicenseInfo:
        """Activate a license key against the server.

        In offline mode (server unreachable), stores the key locally
        and marks it pending validation.
        """
        # TODO: in production, POST to /v1/license/activate
        # For now, accept any key starting with VU-
        if not license_key.startswith("VU-"):
            raise ValueError("Invalid license key format.")

        store_license_token(license_key)
        return LicenseInfo(
            status="active",
            license_key=license_key,
            tier="pro",
            machine_id=self._machine_id,
        )

    def deactivate(self) -> None:
        """Deactivate the current machine."""
        clear_license_token()
        logger.info("License deactivated on this machine.")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def is_paid(self, info: Optional[LicenseInfo] = None) -> bool:
        """Return True if the user has an active paid license or is in trial."""
        if info is None:
            info = self.get_local_license()
        return info.status in ("trial", "active")

    def days_remaining(self, info: Optional[LicenseInfo] = None) -> int:
        """Return days left in trial, or -1 for active licenses."""
        if info is None:
            info = self.get_local_license()
        if info.status == "active":
            return -1
        if info.status == "trial" and info.expires_at:
            remaining = info.expires_at - time.time()
            return max(0, int(remaining / 86400))
        return 0
