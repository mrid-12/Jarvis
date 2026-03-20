import sys
import logging
from PyQt5.QtWidgets import QApplication

from ui_manager import SidebarUI
from action_executor import ActionExecutor
from backend_connection import BackendConnection
from audio_manager import AudioManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class GeminiLiveApp:
    def __init__(self):
        self.app = QApplication(sys.argv)
        self.action_executor = ActionExecutor()

        # UI Setup
        self.ui = SidebarUI(
            on_connect_callback=self.toggle_connection,
            on_mic_callback=self.toggle_mic,
        )

        # Audio manager: mic → backend, model audio → speakers, model audio → transcription
        self.audio = AudioManager(
            on_audio_chunk_callback=self._send_audio_chunk,
            on_error_callback=self.ui.show_error,
            on_transcription_callback=self._on_agent_transcription,
        )
        self.audio.start_playback_thread()

        # Backend WebSocket — note: audio_callback and turn_complete_callback
        # are wired through audio_manager so they stay in sync
        self.backend = BackendConnection(
            action_callback=self.handle_agent_action,
            status_callback=self.ui.update_status,
            error_callback=self.ui.show_error,
            audio_callback=self.audio.play_audio,
            chat_callback=self._on_chat_message,
            turn_complete_callback=self.audio.on_turn_complete,
            interrupt_callback=self.audio.interrupt_playback,
        )

    # ── Connection ──────────────────────────────────────────────────────────

    def toggle_connection(self):
        if self.backend.connected:
            self.backend.stop_connection()
            self.ui.connect_btn.setText("Connect")
            self.ui.update_status("Disconnected")
            self.ui.add_chat_message("System", "Disconnected.")
        else:
            self.backend.start_connection()
            self.ui.connect_btn.setText("Disconnect")
            self.ui.add_chat_message("System", "Connecting to Gemini Live…")

    # ── Microphone ──────────────────────────────────────────────────────────

    def toggle_mic(self):
        is_on = self.audio.toggle_mic()
        if is_on:
            self.ui.add_chat_message("System", "🎙 Microphone ON — speak now.")
        else:
            self.ui.add_chat_message("System", "🎙 Microphone OFF.")
        return is_on

    # ── Audio streaming ─────────────────────────────────────────────────────

    def _send_audio_chunk(self, pcm_bytes: bytes):
        """Called by AudioManager for each mic chunk → send to backend."""
        self.backend.send_audio_chunk(pcm_bytes)

    # ── Transcription callbacks ─────────────────────────────────────────────

    def _on_agent_transcription(self, text: str):
        """Called by AudioManager after it STT-transcribes model turn audio."""
        self.ui.add_chat_message("Agent 🤖", text)

    def _on_chat_message(self, speaker: str, text: str, update: bool = False):
        """Called for user/gemini transcription events from the backend."""
        self.ui.add_chat_message(speaker, text, update)

    # ── Agent actions ───────────────────────────────────────────────────────

    def handle_agent_action(self, action_dict):
        """Execute a UI action received from the Gemini model."""
        thought = action_dict.get("thought", "Acting…")
        action_type = action_dict.get("action_type", "")
        logger.info("Executing action: %s — %s", action_type, thought)
        return self.action_executor.execute_action(action_dict)

    # ── Run ─────────────────────────────────────────────────────────────────

    def run(self):
        result = 0
        try:
            self.ui.show()
            result = self.app.exec_()
        finally:
            self.audio.stop()
            self.backend.stop_connection()
            sys.exit(result)


if __name__ == "__main__":
    client_app = GeminiLiveApp()
    client_app.run()
