"""First-run onboarding wizard for VoiceUse.

A tkinter-based GUI that walks new users through:
1. Welcome
2. API key setup (with test buttons)
3. Microphone permission check
4. Hotkey selection
5. Voice test (record + playback)
6. License activation (trial or key)
7. Done / launch
"""

import asyncio
import logging
import os
import sys
import tempfile
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any, Callable, Dict, Optional

from voiceuse.config import Config
from voiceuse.licensing import LicenseClient, LicenseInfo

logger = logging.getLogger("voiceuse.gui.wizard")

# Try to import keyring for API key storage
try:
    from voiceuse.licensing import load_api_keys, store_api_keys
except Exception:
    load_api_keys = lambda: {}  # type: ignore
    store_api_keys = lambda _: None  # type: ignore


class OnboardingWizard:
    """Tkinter wizard for first-run setup."""

    WIDTH = 640
    HEIGHT = 520

    def __init__(self, config: Config, on_finish: Callable[[Config], None]) -> None:
        self.config = config
        self.on_finish = on_finish
        self.license_client = LicenseClient()
        self.license_info = self.license_client.get_local_license()

        self.root = tk.Tk()
        self.root.title("VoiceUse Setup")
        self.root.geometry(f"{self.WIDTH}x{self.HEIGHT}")
        self.root.resizable(False, False)

        # Center window
        self.root.update_idletasks()
        x = (self.root.winfo_screenwidth() // 2) - (self.WIDTH // 2)
        y = (self.root.winfo_screenheight() // 2) - (self.HEIGHT // 2)
        self.root.geometry(f"+{x}+{y}")

        self._build_ui()
        self._show_step(0)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        """Build the wizard chrome (header, content area, nav buttons)."""
        self.root.configure(bg="#1e1e2e")

        # Header
        self.header = tk.Label(
            self.root,
            text="VoiceUse Setup",
            font=("Segoe UI", 20, "bold"),
            bg="#1e1e2e",
            fg="#cdd6f4",
        )
        self.header.pack(pady=(20, 10))

        self.subheader = tk.Label(
            self.root,
            text="Configure your desktop voice assistant",
            font=("Segoe UI", 11),
            bg="#1e1e2e",
            fg="#a6adc8",
        )
        self.subheader.pack(pady=(0, 10))

        # Progress bar
        self.progress = ttk.Progressbar(
            self.root, orient="horizontal", length=400, mode="determinate", maximum=7
        )
        self.progress.pack(pady=(0, 20))

        # Content frame (swappable)
        self.content = tk.Frame(self.root, bg="#1e1e2e", width=560, height=320)
        self.content.pack(pady=10)
        self.content.pack_propagate(False)

        # Navigation buttons
        self.nav = tk.Frame(self.root, bg="#1e1e2e")
        self.nav.pack(pady=(10, 20))

        self.btn_back = tk.Button(
            self.nav,
            text="Back",
            command=self._prev_step,
            bg="#313244",
            fg="#cdd6f4",
            activebackground="#45475a",
            font=("Segoe UI", 10),
            width=10,
            relief="flat",
        )
        self.btn_back.pack(side="left", padx=5)

        self.btn_next = tk.Button(
            self.nav,
            text="Next",
            command=self._next_step,
            bg="#89b4fa",
            fg="#1e1e2e",
            activebackground="#b4befe",
            font=("Segoe UI", 10, "bold"),
            width=10,
            relief="flat",
        )
        self.btn_next.pack(side="left", padx=5)

        # Step frames
        self.steps: list[tk.Frame] = [
            self._step_welcome(),
            self._step_license(),
            self._step_api_keys(),
            self._step_permissions(),
            self._step_hotkey(),
            self._step_voice_test(),
            self._step_done(),
        ]

        for step in self.steps:
            step.place(in_=self.content, x=0, y=0, relwidth=1, relheight=1)

    # ------------------------------------------------------------------
    # Step: Welcome
    # ------------------------------------------------------------------

    def _step_welcome(self) -> tk.Frame:
        frame = tk.Frame(self.content, bg="#1e1e2e")
        tk.Label(
            frame,
            text="Welcome to VoiceUse",
            font=("Segoe UI", 16, "bold"),
            bg="#1e1e2e",
            fg="#cdd6f4",
        ).pack(pady=(40, 10))
        tk.Label(
            frame,
            text="Control your computer hands-free with your voice.\n\n"
                 "This wizard will help you set up in about 60 seconds.",
            font=("Segoe UI", 11),
            bg="#1e1e2e",
            fg="#a6adc8",
            justify="center",
        ).pack(pady=10)

        # Trial badge
        status_text = f"Trial: {self.license_client.days_remaining(self.license_info)} days remaining"
        if self.license_info.status == "active":
            status_text = "License: Active"
        tk.Label(
            frame,
            text=status_text,
            font=("Segoe UI", 10),
            bg="#1e1e2e",
            fg="#a6e3a1",
        ).pack(pady=(20, 0))
        return frame

    # ------------------------------------------------------------------
    # Step: License
    # ------------------------------------------------------------------

    def _step_license(self) -> tk.Frame:
        frame = tk.Frame(self.content, bg="#1e1e2e")

        tk.Label(
            frame,
            text="License",
            font=("Segoe UI", 14, "bold"),
            bg="#1e1e2e",
            fg="#cdd6f4",
        ).pack(pady=(10, 10))

        if self.license_info.status in ("trial", "active"):
            tk.Label(
                frame,
                text="You're all set! Your license is active.\n"
                     "You can enter a new key below if you have one.",
                font=("Segoe UI", 10),
                bg="#1e1e2e",
                fg="#a6adc8",
                justify="center",
            ).pack(pady=5)
        else:
            tk.Label(
                frame,
                text="Your trial has expired.\n"
                     "Enter your license key below to activate VoiceUse.",
                font=("Segoe UI", 10),
                bg="#1e1e2e",
                fg="#f38ba8",
                justify="center",
            ).pack(pady=5)

        key_frame = tk.Frame(frame, bg="#1e1e2e")
        key_frame.pack(pady=15)

        tk.Label(key_frame, text="License Key:", bg="#1e1e2e", fg="#cdd6f4").pack(side="left", padx=5)
        self.entry_license = tk.Entry(key_frame, width=30, font=("Segoe UI", 10))
        self.entry_license.pack(side="left", padx=5)

        self.btn_activate = tk.Button(
            key_frame,
            text="Activate",
            command=self._activate_license,
            bg="#89b4fa",
            fg="#1e1e2e",
            relief="flat",
        )
        self.btn_activate.pack(side="left", padx=5)

        self.lbl_license_status = tk.Label(
            frame, text="", bg="#1e1e2e", fg="#a6e3a1", font=("Segoe UI", 9)
        )
        self.lbl_license_status.pack(pady=5)
        return frame

    def _activate_license(self) -> None:
        key = self.entry_license.get().strip()
        if not key:
            messagebox.showwarning("Missing Key", "Please enter a license key.")
            return
        try:
            self.license_info = self.license_client.activate(key)
            self.lbl_license_status.config(text="Activated successfully!")
            messagebox.showinfo("Success", "VoiceUse Pro activated!")
        except Exception as exc:
            self.lbl_license_status.config(text=f"Error: {exc}", fg="#f38ba8")
            messagebox.showerror("Activation Failed", str(exc))

    # ------------------------------------------------------------------
    # Step: API Keys
    # ------------------------------------------------------------------

    def _step_api_keys(self) -> tk.Frame:
        frame = tk.Frame(self.content, bg="#1e1e2e")

        tk.Label(
            frame,
            text="API Keys",
            font=("Segoe UI", 14, "bold"),
            bg="#1e1e2e",
            fg="#cdd6f4",
        ).pack(pady=(10, 5))

        tk.Label(
            frame,
            text="VoiceUse needs API keys for cloud AI services.\n"
                 "Keys are stored securely in your OS keychain.",
            font=("Segoe UI", 9),
            bg="#1e1e2e",
            fg="#a6adc8",
            justify="center",
        ).pack(pady=(0, 10))

        # Load existing keys
        existing = load_api_keys()

        fields = [
            ("Groq API Key", "groq", "Required for speech-to-text and LLM"),
            ("Cerebras API Key", "cerebras", "Required for LLM (if using Cerebras)"),
            ("OpenAI API Key", "openai", "Optional fallback LLM"),
            ("Anthropic API Key", "anthropic", "Optional for vision (clicking UI elements)"),
        ]

        self.api_entries: Dict[str, tk.Entry] = {}
        for label, key, tooltip in fields:
            row = tk.Frame(frame, bg="#1e1e2e")
            row.pack(fill="x", padx=20, pady=3)
            tk.Label(row, text=label + ":", bg="#1e1e2e", fg="#cdd6f4", width=18, anchor="e").pack(side="left")
            ent = tk.Entry(row, width=35, font=("Segoe UI", 9), show="*")
            ent.insert(0, existing.get(key, ""))
            ent.pack(side="left", padx=5)
            self.api_entries[key] = ent
            tk.Label(row, text=tooltip, bg="#1e1e2e", fg="#6c7086", font=("Segoe UI", 8)).pack(side="left")

        # Show/hide toggle
        toggle = tk.Button(
            frame,
            text="Show Keys",
            command=lambda: [e.config(show="" if e.cget("show") == "*" else "*") for e in self.api_entries.values()],
            bg="#313244",
            fg="#cdd6f4",
            relief="flat",
        )
        toggle.pack(pady=10)
        return frame

    def _save_api_keys(self) -> None:
        keys = {k: v.get().strip() for k, v in self.api_entries.items()}
        store_api_keys(keys)
        # Also update config for this session
        self.config.stt.api_key = keys.get("groq") or self.config.stt.api_key
        self.config.llm.api_key = keys.get("groq") or self.config.llm.api_key
        self.config.llm.cerebras_api_key = keys.get("cerebras") or self.config.llm.cerebras_api_key
        self.config.llm.fallback_api_key = keys.get("openai") or self.config.llm.fallback_api_key
        self.config.computer_use.api_key = keys.get("anthropic") or self.config.computer_use.api_key

    # ------------------------------------------------------------------
    # Step: Permissions
    # ------------------------------------------------------------------

    def _step_permissions(self) -> tk.Frame:
        frame = tk.Frame(self.content, bg="#1e1e2e")

        tk.Label(
            frame,
            text="Microphone Permission",
            font=("Segoe UI", 14, "bold"),
            bg="#1e1e2e",
            fg="#cdd6f4",
        ).pack(pady=(10, 5))

        tk.Label(
            frame,
            text="VoiceUse needs access to your microphone.\n\n"
                 "Windows: Settings → Privacy → Microphone → Allow apps\n"
                 "macOS: System Preferences → Security → Microphone\n"
                 "Linux: Ensure your user is in the 'audio' group.",
            font=("Segoe UI", 10),
            bg="#1e1e2e",
            fg="#a6adc8",
            justify="left",
        ).pack(pady=10)

        self.btn_test_mic = tk.Button(
            frame,
            text="Test Microphone",
            command=self._test_microphone,
            bg="#89b4fa",
            fg="#1e1e2e",
            relief="flat",
            font=("Segoe UI", 10, "bold"),
        )
        self.btn_test_mic.pack(pady=10)

        self.lbl_mic_status = tk.Label(frame, text="", bg="#1e1e2e", fg="#a6adc8")
        self.lbl_mic_status.pack()
        return frame

    def _test_microphone(self) -> None:
        try:
            import pyaudio
            pa = pyaudio.PyAudio()
            device_count = pa.get_device_count()
            pa.terminate()
            if device_count > 0:
                self.lbl_mic_status.config(text=f"Found {device_count} audio device(s).", fg="#a6e3a1")
            else:
                self.lbl_mic_status.config(text="No audio devices found.", fg="#f38ba8")
        except Exception as exc:
            self.lbl_mic_status.config(text=f"Error: {exc}", fg="#f38ba8")

    # ------------------------------------------------------------------
    # Step: Hotkey
    # ------------------------------------------------------------------

    def _step_hotkey(self) -> tk.Frame:
        frame = tk.Frame(self.content, bg="#1e1e2e")

        tk.Label(
            frame,
            text="Hotkey",
            font=("Segoe UI", 14, "bold"),
            bg="#1e1e2e",
            fg="#cdd6f4",
        ).pack(pady=(10, 5))

        tk.Label(
            frame,
            text="Choose how you want to trigger VoiceUse.\n"
                 "Press and hold the hotkey to speak, release to submit.",
            font=("Segoe UI", 10),
            bg="#1e1e2e",
            fg="#a6adc8",
            justify="center",
        ).pack(pady=5)

        self.lbl_hotkey = tk.Label(
            frame,
            text=f"Current: {self.config.audio.hotkey}",
            font=("Segoe UI", 12, "bold"),
            bg="#1e1e2e",
            fg="#89b4fa",
        )
        self.lbl_hotkey.pack(pady=15)

        btn = tk.Button(
            frame,
            text="Capture New Hotkey",
            command=self._capture_hotkey,
            bg="#313244",
            fg="#cdd6f4",
            relief="flat",
            font=("Segoe UI", 10),
        )
        btn.pack(pady=5)

        self.lbl_hotkey_hint = tk.Label(
            frame,
            text="Click above and press your preferred key combination.",
            bg="#1e1e2e",
            fg="#6c7086",
            font=("Segoe UI", 9),
        )
        self.lbl_hotkey_hint.pack(pady=5)
        return frame

    def _capture_hotkey(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("Press Hotkey")
        dialog.geometry("300x120")
        dialog.configure(bg="#1e1e2e")
        dialog.transient(self.root)
        dialog.grab_set()

        lbl = tk.Label(
            dialog,
            text="Press your preferred key now...",
            font=("Segoe UI", 11),
            bg="#1e1e2e",
            fg="#cdd6f4",
        )
        lbl.pack(pady=20)

        captured = {"key": None}

        def on_key(event: Any) -> None:
            name = event.keysym.lower()
            if name in ("shift_l", "shift_r"):
                name = "shift"
            elif name in ("control_l", "control_r"):
                name = "ctrl"
            elif name in ("alt_l", "alt_r"):
                name = "alt"
            captured["key"] = name
            self.config.audio.hotkey = name
            self.lbl_hotkey.config(text=f"Current: {name}")
            dialog.destroy()

        dialog.bind("<KeyPress>", on_key)
        dialog.focus_set()

    # ------------------------------------------------------------------
    # Step: Voice Test
    # ------------------------------------------------------------------

    def _step_voice_test(self) -> tk.Frame:
        frame = tk.Frame(self.content, bg="#1e1e2e")

        tk.Label(
            frame,
            text="Voice Test",
            font=("Segoe UI", 14, "bold"),
            bg="#1e1e2e",
            fg="#cdd6f4",
        ).pack(pady=(10, 5))

        tk.Label(
            frame,
            text="Let's verify everything works.\n"
                 "Click Record, speak a few words, then click Play.",
            font=("Segoe UI", 10),
            bg="#1e1e2e",
            fg="#a6adc8",
            justify="center",
        ).pack(pady=5)

        btn_frame = tk.Frame(frame, bg="#1e1e2e")
        btn_frame.pack(pady=15)

        self.btn_record = tk.Button(
            btn_frame,
            text="Record",
            command=self._toggle_record,
            bg="#313244",
            fg="#cdd6f4",
            relief="flat",
            width=10,
        )
        self.btn_record.pack(side="left", padx=5)

        self.btn_play = tk.Button(
            btn_frame,
            text="Play",
            command=self._play_test,
            bg="#313244",
            fg="#cdd6f4",
            relief="flat",
            width=10,
        )
        self.btn_play.pack(side="left", padx=5)

        self.lbl_voice_status = tk.Label(frame, text="", bg="#1e1e2e", fg="#a6adc8")
        self.lbl_voice_status.pack(pady=10)

        self._recording = False
        self._test_audio_path: Optional[Path] = None
        return frame

    def _toggle_record(self) -> None:
        if not self._recording:
            self._recording = True
            self.btn_record.config(text="Stop", bg="#f38ba8")
            self.lbl_voice_status.config(text="Recording...")
            # Simple threaded recording
            threading.Thread(target=self._record_thread, daemon=True).start()
        else:
            self._recording = False
            self.btn_record.config(text="Record", bg="#313244")
            self.lbl_voice_status.config(text="Recording stopped.")

    def _record_thread(self) -> None:
        try:
            import wave
            import pyaudio
            pa = pyaudio.PyAudio()
            stream = pa.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=16000,
                input=True,
                frames_per_buffer=1024,
            )
            frames = []
            while self._recording:
                data = stream.read(1024, exception_on_overflow=False)
                frames.append(data)
            stream.stop_stream()
            stream.close()
            pa.terminate()

            tmp = Path(tempfile.gettempdir()) / "voiceuse_test.wav"
            with wave.open(str(tmp), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(b"".join(frames))
            self._test_audio_path = tmp
            self.lbl_voice_status.config(text=f"Saved {len(frames)} frames.")
        except Exception as exc:
            self.lbl_voice_status.config(text=f"Error: {exc}", fg="#f38ba8")

    def _play_test(self) -> None:
        if self._test_audio_path is None or not self._test_audio_path.exists():
            messagebox.showwarning("No Recording", "Please record something first.")
            return
        try:
            import simpleaudio as sa
            wave_obj = sa.WaveObject.from_wave_file(str(self._test_audio_path))
            wave_obj.play()
            self.lbl_voice_status.config(text="Playing...")
        except Exception as exc:
            self.lbl_voice_status.config(text=f"Playback error: {exc}", fg="#f38ba8")

    # ------------------------------------------------------------------
    # Step: Done
    # ------------------------------------------------------------------

    def _step_done(self) -> tk.Frame:
        frame = tk.Frame(self.content, bg="#1e1e2e")

        tk.Label(
            frame,
            text="You're Ready!",
            font=("Segoe UI", 18, "bold"),
            bg="#1e1e2e",
            fg="#a6e3a1",
        ).pack(pady=(40, 10))

        tk.Label(
            frame,
            text="VoiceUse is configured and ready to go.\n\n"
                 "Hold your hotkey and speak to control your computer.",
            font=("Segoe UI", 11),
            bg="#1e1e2e",
            fg="#cdd6f4",
            justify="center",
        ).pack(pady=10)

        self.btn_launch = tk.Button(
            frame,
            text="Launch VoiceUse",
            command=self._finish,
            bg="#a6e3a1",
            fg="#1e1e2e",
            activebackground="#b4befe",
            font=("Segoe UI", 12, "bold"),
            width=16,
            relief="flat",
        )
        self.btn_launch.pack(pady=20)
        return frame

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _show_step(self, index: int) -> None:
        self._current_step = index
        for i, step in enumerate(self.steps):
            step.lift() if i == index else step.lower()
        self.progress["value"] = index + 1
        self.btn_back.config(state="normal" if index > 0 else "disabled")
        if index == len(self.steps) - 1:
            self.btn_next.config(text="Finish", command=self._finish)
        else:
            self.btn_next.config(text="Next", command=self._next_step)

    def _next_step(self) -> None:
        if self._current_step == 2:
            self._save_api_keys()
        if self._current_step < len(self.steps) - 1:
            self._show_step(self._current_step + 1)

    def _prev_step(self) -> None:
        if self._current_step > 0:
            self._show_step(self._current_step - 1)

    def _finish(self) -> None:
        self._save_api_keys()
        self.root.destroy()
        self.on_finish(self.config)

    def run(self) -> None:
        """Start the wizard (blocks until closed)."""
        self.root.mainloop()


def show_wizard(config: Config, on_finish: Callable[[Config], None]) -> None:
    """Convenience function to create and run the wizard."""
    wizard = OnboardingWizard(config, on_finish)
    wizard.run()
