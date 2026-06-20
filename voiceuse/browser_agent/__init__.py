"""Remote browser-control agent for VoiceUse.

This package provides a Playwright-based browser controller, a vision-enabled
LLM agent loop, and a FastAPI web UI so the agent can be operated remotely
from another machine over Tailscale.
"""

from voiceuse.browser_agent.agent import BrowserAgent
from voiceuse.browser_agent.controller import BrowserController

__all__ = ["BrowserAgent", "BrowserController"]
