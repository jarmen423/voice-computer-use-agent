"""Configuration management for VoiceUse."""
import os
from pathlib import Path
from typing import Optional, Literal

import yaml
from pydantic import BaseModel, Field, field_validator


class AudioConfig(BaseModel):
    sample_rate: int = 16000
    chunk_duration_ms: int = 30
    frame_duration_ms: int = 30
    silence_timeout_ms: int = 1500
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
    provider: Literal["groq", "openai"] = "groq"
    model: str = "llama-3.3-70b-versatile"
    api_key: Optional[str] = None
    fallback_provider: Literal["groq", "openai"] = "openai"
    fallback_model: str = "gpt-4o-mini"
    fallback_api_key: Optional[str] = None
    temperature: float = 0.1
    max_tokens: int = 1024

    @field_validator("api_key", mode="before")
    def resolve_api_key(cls, v):
        return v or os.environ.get("GROQ_API_KEY")

    @field_validator("fallback_api_key", mode="before")
    def resolve_fallback_api_key(cls, v):
        return v or os.environ.get("OPENAI_API_KEY")


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


class SafetyConfig(BaseModel):
    confirm_destructive: bool = True
    destructive_keywords: list[str] = Field(default_factory=lambda: [
        "close", "quit", "delete", "remove", "kill", "terminate", "shutdown", "reboot",
        "format", "rm -rf", "type password", "enter password", "input password"
    ])
    confirmation_timeout_seconds: int = 10


class AppConfig(BaseModel):
    preferred_browser: str = "chrome"
    preferred_terminal: str = "cmd" if os.name == "nt" else "gnome-terminal"
    codex_app_name: str = "Codex"  # Window title substring for Codex app


class Config(BaseModel):
    audio: AudioConfig = Field(default_factory=AudioConfig)
    stt: STTConfig = Field(default_factory=STTConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)
    computer_use: ComputerUseConfig = Field(default_factory=ComputerUseConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    app: AppConfig = Field(default_factory=AppConfig)

    @classmethod
    def from_yaml(cls, path: str = "config.yaml") -> "Config":
        path = Path(path)
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return cls(**data)
        return cls()

    def to_yaml(self, path: str = "config.yaml") -> None:
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(self.model_dump(), f, default_flow_style=False, sort_keys=False)
