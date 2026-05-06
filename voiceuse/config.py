"""Configuration management for VoiceUse."""
import os
from pathlib import Path
from typing import Dict, Optional, Literal

import yaml
from pydantic import BaseModel, Field, field_validator


class AudioConfig(BaseModel):
    sample_rate: int = 16000
    chunk_duration_ms: int = 30
    frame_duration_ms: int = 30
    silence_timeout_ms: int = 1500
    max_recording_seconds: int = 20
    vad_aggressiveness: int = 2  # 0-3
    hotkey: str = "right ctrl"
    wake_word: str = "hey computer"
    wake_word_model_path: Optional[str] = None  # Path to custom porcupine .ppn file


class STTConfig(BaseModel):
    provider: Literal["groq"] = "groq"
    model: str = "whisper-large-v3"
    api_key: Optional[str] = None
    language: Optional[str] = "en"

    @field_validator("api_key", mode="before")
    def resolve_api_key(cls, v):
        return v or os.environ.get("GROQ_API_KEY")


class LLMConfig(BaseModel):
    provider: Literal["groq", "openai", "cerebras"] = "groq"
    model: str = "llama-3.3-70b-versatile"
    api_key: Optional[str] = None
    fallback_provider: Literal["groq", "openai", "cerebras"] = "openai"
    fallback_model: str = "gpt-4o-mini"
    fallback_api_key: Optional[str] = None
    cerebras_api_key: Optional[str] = None
    temperature: float = 0.1
    max_tokens: int = 1024

    @field_validator("api_key", mode="before")
    def resolve_api_key(cls, v):
        return v or os.environ.get("GROQ_API_KEY")

    @field_validator("fallback_api_key", mode="before")
    def resolve_fallback_api_key(cls, v):
        return v or os.environ.get("OPENAI_API_KEY")

    @field_validator("cerebras_api_key", mode="before")
    def resolve_cerebras_api_key(cls, v):
        return v or os.environ.get("CEREBRAS_API_KEY")


class TTSConfig(BaseModel):
    provider: Literal["edge", "pyttsx3"] = "edge"
    voice: str = "en-US-AriaNeural"  # Edge TTS voice
    enabled: bool = True
    speed: str = "+0%"  # Edge TTS speed


class ComputerUseConfig(BaseModel):
    provider: Literal["codex", "anthropic"] = "codex"
    api_key: Optional[str] = None  # For anthropic
    model: str = "claude-3-5-sonnet-20241022"  # For anthropic
    confidence_threshold: float = 0.8

    @field_validator("api_key", mode="before")
    def resolve_api_key(cls, v):
        return v or os.environ.get("ANTHROPIC_API_KEY")


class AgentConfig(BaseModel):
    """Selects the planner/executor behind the voice interface.

    ``native`` keeps the original in-process Brain pipeline. ``external_agent``
    keeps VoiceUse as the hotkey, microphone, STT, and TTS shell while sending
    the transcribed command to an external desktop action agent. The first
    runner is Codex CLI, but the prompt contract deliberately stays agent-
    generic so another MCP-capable agent can be added later.
    """

    backend: Literal["native", "external_agent"] = "native"
    runner: Literal["codex_cli"] = "codex_cli"
    command: str = "codex"
    working_directory: str = "."
    timeout_seconds: int = 300
    model: Optional[str] = None
    sandbox: Optional[str] = None
    skip_git_repo_check: bool = True


class SafetyConfig(BaseModel):
    confirm_destructive: bool = True
    allowed_tools: list[str] = Field(default_factory=lambda: [
        "open_app",
        "focus_window",
        "split_view_apps",
        "browser_search",
        "click_element",
        "type_text",
        "find_chat",
        "execute_system",
    ])
    destructive_keywords: list[str] = Field(default_factory=lambda: [
        "close", "quit", "delete", "remove", "kill", "terminate", "shutdown", "reboot",
        "format", "rm -rf", "type password", "enter password", "input password"
    ])
    confirmation_timeout_seconds: int = 10
    audit_enabled: bool = True
    audit_log_path: str = "logs/voiceuse_action_audit.jsonl"


class GrokVoiceConfig(BaseModel):
    enabled: bool = False
    api_key: Optional[str] = None
    model: str = "grok-voice-think-fast-1.0"
    voice: str = "Eve"
    instructions: str = (
        "You are a desktop voice assistant. You can open apps, focus windows, "
        "click elements using computer vision, type text, search the browser, "
        "and execute safe system commands."
    )
    sample_rate: int = 24000
    turn_detection_type: str = "server_vad"
    input_audio_transcription_model: str = "grok-2-audio"
    tools: list[str] = Field(default_factory=lambda: [
        "open_app",
        "focus_window",
        "click_element",
        "type_text",
        "browser_search",
        "execute_system",
    ])

    @field_validator("api_key", mode="before")
    def resolve_api_key(cls, v):
        return v or os.environ.get("XAI_API_KEY")


class PluginsConfig(BaseModel):
    grok_voice: GrokVoiceConfig = Field(default_factory=GrokVoiceConfig)


class AppConfig(BaseModel):
    preferred_browser: str = "chrome"
    preferred_terminal: str = "cmd" if os.name == "nt" else "gnome-terminal"
    codex_app_name: str = "Codex"  # Window title substring for Codex app
    dry_run: bool = False  # If True, use mock LLM/STT responses (no API calls)
    aliases: Dict[str, str] = Field(default_factory=lambda: {
        # Map common nicknames to exact Windows app names / Start Menu entries.
        # Keys are what the user (or STT) might say; values are what Windows
        # understands via os.startfile().
        "comet": "Comet Browser",
    })


class Config(BaseModel):
    audio: AudioConfig = Field(default_factory=AudioConfig)
    stt: STTConfig = Field(default_factory=STTConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)
    computer_use: ComputerUseConfig = Field(default_factory=ComputerUseConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    app: AppConfig = Field(default_factory=AppConfig)
    plugins: PluginsConfig = Field(default_factory=PluginsConfig)

    @classmethod
    def from_yaml(cls, path: str = "config.yaml") -> "Config":
        path = Path(path)
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return cls(**data)
        return cls()

    def to_yaml(self, path: str = "config.yaml") -> None:
        """Write non-secret configuration to YAML.

        API keys may be resolved from environment variables or secure storage
        during runtime. Persisting the resolved model directly would leak those
        secrets into ``config.yaml``, so this export deliberately omits every
        API-key field and lets validators resolve them again on load.
        """
        data = self.model_dump(
            exclude={
                "stt": {"api_key"},
                "llm": {"api_key", "fallback_api_key", "cerebras_api_key"},
                "computer_use": {"api_key"},
                "plugins": {"grok_voice": {"api_key"}},
            }
        )
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)
