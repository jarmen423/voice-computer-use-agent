"""FastAPI web UI for the remote browser agent."""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Dict

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from voiceuse.browser_agent.agent import AgentEvent, BrowserAgent
from voiceuse.browser_agent.controller import BrowserController
from voiceuse.config import Config

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


class BrowserAgentState:
    """Shared server state holding the controller and agent."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.controller = BrowserController(config.browser_agent)
        self.agent = BrowserAgent(config.browser_agent, self.controller)


state: BrowserAgentState | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load configuration and create shared state."""
    global state
    config_path = "config.yaml"
    config = Config.from_yaml(config_path)
    state = BrowserAgentState(config)
    logger.info("Browser agent server starting on %s:%s", config.browser_agent.host, config.browser_agent.port)
    yield
    if state:
        await state.controller.close()
        state = None


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(title="VoiceUse Browser Agent", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        html_path = STATIC_DIR / "index.html"
        if html_path.exists():
            return html_path.read_text(encoding="utf-8")
        return "<h1>Browser Agent</h1><p>UI not found.</p>"

    @app.get("/health")
    async def health() -> Dict[str, Any]:
        connected = state.controller.is_connected() if state else False
        return {"status": "ok", "browser_connected": connected}

    @app.post("/api/browser/launch")
    async def launch_browser() -> Dict[str, Any]:
        if state is None:
            return {"success": False, "message": "Server state not initialized."}
        try:
            await state.controller.launch()
            return {"success": True, "message": "Browser launched."}
        except Exception as exc:
            logger.exception("Launch failed")
            return {"success": False, "message": str(exc)}

    @app.post("/api/browser/attach")
    async def attach_browser() -> Dict[str, Any]:
        if state is None:
            return {"success": False, "message": "Server state not initialized."}
        try:
            await state.controller.attach()
            return {"success": True, "message": "Browser attached."}
        except Exception as exc:
            logger.exception("Attach failed")
            return {"success": False, "message": str(exc)}

    @app.post("/api/browser/close")
    async def close_browser() -> Dict[str, Any]:
        if state is None:
            return {"success": False, "message": "Server state not initialized."}
        try:
            await state.controller.close()
            return {"success": True, "message": "Browser closed."}
        except Exception as exc:
            logger.exception("Close failed")
            return {"success": False, "message": str(exc)}

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    await _send_event(websocket, AgentEvent("error", message="Invalid JSON."))
                    continue

                msg_type = payload.get("type")
                if msg_type == "command":
                    goal = payload.get("text", "")
                    if not goal.strip():
                        await _send_event(websocket, AgentEvent("error", message="Empty command."))
                        continue

                    if not state.controller.is_connected():
                        await _send_event(
                            websocket,
                            AgentEvent("error", message="Browser not connected. Use Launch or Attach first."),
                        )
                        continue

                    await _send_event(websocket, AgentEvent("text", message=f"Goal: {goal}"))
                    async for event in state.agent.stream_task(goal):
                        await _send_event(websocket, event)
                else:
                    await _send_event(websocket, AgentEvent("error", message=f"Unknown message type: {msg_type}"))
        except WebSocketDisconnect:
            logger.info("WebSocket disconnected")
        except Exception as exc:
            logger.exception("WebSocket error")
            await _send_event(websocket, AgentEvent("error", message=f"Server error: {exc}"))

    return app


async def _send_event(websocket: WebSocket, event: AgentEvent) -> None:
    await websocket.send_text(json.dumps({"type": event.type, "data": event.data, "message": event.message}))
