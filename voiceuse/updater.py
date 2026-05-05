"""Auto-updater client for VoiceUse.

Checks a remote JSON manifest for newer versions, downloads the update,
and applies it on next restart.
"""

import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

import urllib.request

logger = logging.getLogger("voiceuse.updater")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

UPDATE_MANIFEST_URL = os.environ.get(
    "VOICEUSE_UPDATE_URL",
    "https://raw.githubusercontent.com/jarmen423/voice-computer-use-agent/main/updates.json",
)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

class UpdateInfo:
    def __init__(self, data: Dict[str, Any]) -> None:
        self.version: str = data.get("version", "")
        self.url: str = data.get("url", "")
        self.signature: str = data.get("signature", "")
        self.changelog: str = data.get("changelog", "")
        self.mandatory: bool = data.get("mandatory", False)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class Updater:
    """Checks for and downloads VoiceUse updates."""

    def __init__(self, current_version: str) -> None:
        self.current_version = current_version.lstrip("v")
        self._manifest: Optional[UpdateInfo] = None

    def check(self) -> Optional[UpdateInfo]:
        """Check if a newer version is available. Returns UpdateInfo or None."""
        try:
            with urllib.request.urlopen(UPDATE_MANIFEST_URL, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            logger.warning("Update check failed: %s", exc)
            return None

        latest = data.get("version", "").lstrip("v")
        if not latest:
            return None

        if self._is_newer(latest, self.current_version):
            self._manifest = UpdateInfo(data)
            logger.info("Update available: %s → %s", self.current_version, latest)
            return self._manifest

        logger.debug("No update available (current %s, latest %s).", self.current_version, latest)
        return None

    @staticmethod
    def _is_newer(a: str, b: str) -> bool:
        """Compare semantic version strings."""
        def parse(v: str):
            return tuple(int(x) for x in v.split(".") if x.isdigit())
        try:
            return parse(a) > parse(b)
        except ValueError:
            return a != b

    def download(self, info: UpdateInfo, dest_dir: Optional[Path] = None) -> Optional[Path]:
        """Download the update to a temporary location."""
        if dest_dir is None:
            dest_dir = Path(tempfile.gettempdir())

        ext = ".exe" if sys.platform.startswith("win") else ""
        dest = dest_dir / f"voiceuse_update_{info.version}{ext}"

        logger.info("Downloading update from %s → %s", info.url, dest)
        try:
            urllib.request.urlretrieve(info.url, str(dest))
            # TODO: verify signature
            return dest
        except Exception as exc:
            logger.error("Download failed: %s", exc)
            return None

    def apply(self, update_path: Path) -> None:
        """Apply the update and restart.

        On Windows: spawn the updater script and exit.
        On macOS/Linux: replace the binary and restart.
        """
        logger.info("Applying update from %s", update_path)

        if sys.platform.startswith("win"):
            self._apply_windows(update_path)
        else:
            self._apply_unix(update_path)

    def _apply_windows(self, update_path: Path) -> None:
        """Windows: write a batch script that waits for process exit, replaces exe, restarts."""
        current_exe = Path(sys.executable)
        batch = update_path.with_suffix(".bat")
        batch_content = f"""@echo off
timeout /t 2 /nobreak >nul
copy /Y "{update_path}" "{current_exe}"
start "" "{current_exe}"
del "{update_path}"
del "%~f0"
"""
        batch.write_text(batch_content, encoding="utf-8")
        os.startfile(str(batch))  # type: ignore[attr-defined]
        sys.exit(0)

    def _apply_unix(self, update_path: Path) -> None:
        """macOS/Linux: replace binary and exec."""
        current_exe = Path(sys.executable)
        import shutil
        shutil.copy2(str(update_path), str(current_exe))
        update_path.unlink()
        os.execv(str(current_exe), [str(current_exe)] + sys.argv[1:])
