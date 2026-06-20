"""Entry point for the VoiceUse browser-agent web server."""

from __future__ import annotations

import argparse
import logging
import os
import sys

import uvicorn

from voiceuse.config import Config


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


def _resolve_display_env() -> None:
    """Ensure Chrome can find the X server when launched from an SSH session."""
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        os.environ["DISPLAY"] = ":0"
    if sys.platform.startswith("linux") and not os.environ.get("XAUTHORITY"):
        import subprocess

        try:
            uid = subprocess.check_output(["id", "-u"], text=True).strip()
            xauth_dir = f"/run/user/{uid}"
            for entry in os.listdir(xauth_dir):
                if entry.startswith("xauth_"):
                    os.environ["XAUTHORITY"] = os.path.join(xauth_dir, entry)
                    break
        except Exception:
            pass


def main() -> None:
    _setup_logging()
    _resolve_display_env()

    parser = argparse.ArgumentParser(description="VoiceUse remote browser agent")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--host", help="Override bind host")
    parser.add_argument("--port", type=int, help="Override bind port")
    args = parser.parse_args()

    config = Config.from_yaml(args.config)
    host = args.host or config.browser_agent.host
    port = args.port or config.browser_agent.port

    # Uvicorn needs the module path as a string for reloading; otherwise import is fine.
    uvicorn.run(
        "voiceuse.browser_agent.web:create_app",
        host=host,
        port=port,
        factory=True,
        log_level="info",
        ws_max_size=10 * 1024 * 1024,  # allow large screenshot frames
    )


if __name__ == "__main__":
    main()
