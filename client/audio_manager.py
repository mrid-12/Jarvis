"""
AudioManager: Streams raw 16 kHz PCM microphone audio to the backend
and plays back 24 kHz PCM audio received from the model.

Also transcribes the model's audio output by buffering it during a turn
and running it through SpeechRecognition when turn_complete fires.

Dependencies: pyaudio, SpeechRecognition
    pip install pyaudio SpeechRecognition
"""

import io
import logging
import queue
import struct
import threading
import wave

try:
    import pyaudio
    PYAUDIO_AVAILABLE = True
except ImportError:
    PYAUDIO_AVAILABLE = False

try:
    import speech_recognition as sr
    SR_AVAILABLE = True
except ImportError:
    SR_AVAILABLE = False

logger = logging.getLogger(__name__)

# Microphone input: Gemini Live requires 16 kHz signed 16-bit mono
MIC_SAMPLE_RATE = 16000
MIC_CHANNELS = 1
MIC_CHUNK_SIZE = 1024

# Speaker output: Gemini Live sends 24 kHz signed 16-bit mono
SPK_SAMPLE_RATE = 24000
SPK_CHANNELS = 1
SPK_CHUNK_SIZE = 1024


class AudioManager:
    """
    Manages microphone capture (→ backend) and speaker playback (← backend).

    Also buffers model audio and transcribes it per-turn so the chat UI
    can show the agent's spoken words as text.

    Callbacks:
        on_audio_chunk_callback(bytes)  – mic PCM chunk ready to send
        on_error_callback(str)          – error message
        on_transcription_callback(str)  – called with agent speech text
                                          after each turn completes
    """

    def __init__(self, on_audio_chunk_callback, on_error_callback, on_transcription_callback=None):
        self.on_audio_chunk = on_audio_chunk_callback
        self.on_error = on_error_callback
        self.on_transcription = on_transcription_callback

        self.is_recording = False
        self._mic_thread: threading.Thread | None = None
        self._playback_thread: threading.Thread | None = None
        self._playback_queue: queue.Queue = queue.Queue()

        # Buffer that accumulates model audio for the current turn
        self._turn_audio_buffer: list[bytes] = []
        self._buffer_lock = threading.Lock()

        self._pa = None
        if PYAUDIO_AVAILABLE:
            self._pa = pyaudio.PyAudio()
        else:
            self.on_error("pyaudio not installed — audio streaming unavailable.")

        if not SR_AVAILABLE:
            logger.warning("SpeechRecognition not installed — agent transcription disabled.")

    # ── Microphone ─────────────────────────────────────────────────────────

    def toggle_mic(self) -> bool:
        """Toggle microphone on/off. Returns True if recording started."""
        if not PYAUDIO_AVAILABLE:
            self.on_error("pyaudio not installed.")
            return False

        self.is_recording = not self.is_recording
        if self.is_recording:
            self._mic_thread = threading.Thread(target=self._mic_loop, daemon=True)
            self._mic_thread.start()
        return self.is_recording

    def _mic_loop(self):
        try:
            stream = self._pa.open(
                format=pyaudio.paInt16,
                channels=MIC_CHANNELS,
                rate=MIC_SAMPLE_RATE,
                input=True,
                frames_per_buffer=MIC_CHUNK_SIZE,
            )
            logger.info("Microphone stream opened at %d Hz", MIC_SAMPLE_RATE)
            while self.is_recording:
                try:
                    data = stream.read(MIC_CHUNK_SIZE, exception_on_overflow=False)
                    self.on_audio_chunk(data)
                except Exception as e:
                    logger.warning("Mic read error: %s", e)
                    break
            stream.stop_stream()
            stream.close()
            logger.info("Microphone stream closed.")
        except Exception as e:
            self.is_recording = False
            self.on_error(f"Microphone error: {e}")

    # ── Playback + buffering ───────────────────────────────────────────────

    def start_playback_thread(self):
        """Start the background thread that drains the playback queue."""
        if not PYAUDIO_AVAILABLE:
            return
        self._playback_thread = threading.Thread(target=self._playback_loop, daemon=True)
        self._playback_thread.start()

    def play_audio(self, pcm_bytes: bytes):
        """
        Enqueue raw 24 kHz PCM for playback AND buffer it for transcription.
        Called from any thread when model audio arrives.
        """
        self._playback_queue.put(pcm_bytes)
        # Buffer for transcription
        with self._buffer_lock:
            self._turn_audio_buffer.append(pcm_bytes)

    def interrupt_playback(self):
        """Clear playback queue when model is interrupted. Also clears the buffer."""
        while not self._playback_queue.empty():
            try:
                self._playback_queue.get_nowait()
            except queue.Empty:
                break
        with self._buffer_lock:
            self._turn_audio_buffer.clear()

    def on_turn_complete(self):
        """
        Called when the model's turn is complete. 
        Transcribes the buffered audio and fires on_transcription.
        Runs transcription in a background thread to avoid blocking.
        """
        with self._buffer_lock:
            audio_data = b"".join(self._turn_audio_buffer)
            self._turn_audio_buffer.clear()

        if audio_data and self.on_transcription and SR_AVAILABLE:
            threading.Thread(
                target=self._transcribe_buffer,
                args=(audio_data,),
                daemon=True,
            ).start()

    def _transcribe_buffer(self, pcm_bytes: bytes):
        """Convert buffered 24kHz PCM to WAV and transcribe with SpeechRecognition."""
        try:
            # Wrap raw PCM bytes into a WAV container in-memory
            wav_buf = io.BytesIO()
            with wave.open(wav_buf, 'wb') as wf:
                wf.setnchannels(SPK_CHANNELS)
                wf.setsampwidth(2)            # 16-bit = 2 bytes
                wf.setframerate(SPK_SAMPLE_RATE)
                wf.writeframes(pcm_bytes)
            wav_buf.seek(0)

            recognizer = sr.Recognizer()
            with sr.AudioFile(wav_buf) as source:
                audio = recognizer.record(source)

            text = recognizer.recognize_google(audio)
            if text and self.on_transcription:
                self.on_transcription(text)

        except sr.UnknownValueError:
            pass  # Model made unintelligible sounds, or silence
        except sr.RequestError as e:
            logger.warning("Transcription request error: %s", e)
        except Exception as e:
            logger.warning("Transcription error: %s", e)

    def _playback_loop(self):
        try:
            stream = self._pa.open(
                format=pyaudio.paInt16,
                channels=SPK_CHANNELS,
                rate=SPK_SAMPLE_RATE,
                output=True,
                frames_per_buffer=SPK_CHUNK_SIZE,
            )
            logger.info("Playback stream opened at %d Hz", SPK_SAMPLE_RATE)
            while True:
                chunk = self._playback_queue.get()
                if chunk is None:
                    break
                stream.write(chunk)
            stream.stop_stream()
            stream.close()
        except Exception as e:
            self.on_error(f"Playback error: {e}")

    # ── Cleanup ────────────────────────────────────────────────────────────

    def stop(self):
        self.is_recording = False
        self._playback_queue.put(None)
        if self._pa:
            self._pa.terminate()
