"""Unit tests for voiceuse.config."""

import tempfile
from pathlib import Path

import pytest
import yaml

from voiceuse.config import Config, STTConfig, LLMConfig


class TestConfigFromYaml:
    """Tests for loading configuration from YAML files."""

    def test_load_default_when_file_missing(self) -> None:
        """from_yaml should return default Config when path does not exist."""
        cfg = Config.from_yaml("/nonexistent/path/config.yaml")
        assert cfg.audio.sample_rate == 16000
        assert cfg.stt.provider == "groq"

    def test_round_trip_yaml(self) -> None:
        """Config written to YAML should be reconstructible."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.yaml"
            original = Config()
            original.audio.sample_rate = 24000
            original.to_yaml(str(path))
            loaded = Config.from_yaml(str(path))
            assert loaded.audio.sample_rate == 24000

    def test_to_yaml_omits_resolved_api_keys(self) -> None:
        """Runtime API keys should not be serialized into config files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.yaml"
            config = Config()
            config.stt.api_key = "groq-secret"
            config.llm.api_key = "llm-secret"
            config.llm.fallback_api_key = "openai-secret"
            config.llm.cerebras_api_key = "cerebras-secret"
            config.computer_use.api_key = "anthropic-secret"
            config.plugins.grok_voice.api_key = "xai-secret"

            config.to_yaml(str(path))

            serialized = path.read_text(encoding="utf-8")
            assert "secret" not in serialized
            data = yaml.safe_load(serialized)
            assert "api_key" not in data["stt"]
            assert "fallback_api_key" not in data["llm"]

    def test_env_var_resolution_stt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """STTConfig should resolve API key from GROQ_API_KEY env var."""
        monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
        stt = STTConfig(api_key=None)
        assert stt.api_key == "test-groq-key"

    def test_explicit_key_takes_precedence(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An explicitly provided API key should override the env var."""
        monkeypatch.setenv("GROQ_API_KEY", "env-key")
        stt = STTConfig(api_key="explicit-key")
        assert stt.api_key == "explicit-key"

    def test_llm_fallback_key_resolution(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """LLMConfig fallback_api_key should resolve from OPENAI_API_KEY."""
        monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
        llm = LLMConfig(fallback_api_key=None)
        assert llm.fallback_api_key == "test-openai-key"

    def test_llm_cerebras_key_resolution(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """LLMConfig cerebras_api_key should resolve from CEREBRAS_API_KEY."""
        monkeypatch.setenv("CEREBRAS_API_KEY", "test-cerebras-key")
        llm = LLMConfig(cerebras_api_key=None)
        assert llm.cerebras_api_key == "test-cerebras-key"

    def test_yaml_override(self) -> None:
        """YAML values should override Pydantic defaults."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.yaml"
            data = {"audio": {"sample_rate": 48000}, "stt": {"model": "whisper-small"}}
            path.write_text(yaml.dump(data), encoding="utf-8")
            cfg = Config.from_yaml(str(path))
            assert cfg.audio.sample_rate == 48000
            assert cfg.stt.model == "whisper-small"

    def test_agent_backend_config(self) -> None:
        """Config should support selecting an external desktop action agent."""
        cfg = Config(agent={"backend": "external_agent", "runner": "codex_cli", "command": "codex"})

        assert cfg.agent.backend == "external_agent"
        assert cfg.agent.runner == "codex_cli"
        assert cfg.agent.command == "codex"
