"""
BackendConnection: WebSocket client bridging the PyQt GUI to the FastAPI backend.

Protocol (client → server):
    {"type": "audio", "data": "<base64 16kHz PCM>"}
    {"type": "video", "data": "<base64 JPEG>"}
    {"type": "text",  "text": "..."}

Protocol (server → client):
    {"type": "user",         "text": "..."}        – user speech transcription
    {"type": "gemini",       "text": "..."}        – model speech transcription
    {"type": "audio",        "data": "<base64 24kHz PCM>"}
    {"type": "action",       "action": {...}}      – UI action (has action_type key)
    {"type": "status",       "message": "..."}
    {"type": "turn_complete"}
    {"type": "interrupted"}
    {"type": "error",        "error": "..."}
"""

import asyncio
import base64
import io
import json
import logging
import os
import threading

import websockets
import pyautogui
from dotenv import load_dotenv
from screen_capture import screenshot_with_cursor

load_dotenv()
logger = logging.getLogger(__name__)


class BackendConnection:
    """
    Manages the WebSocket connection to the FastAPI backend.

    Callbacks:
        action_callback(dict)          – UI action to execute; returns (bool, str)
        status_callback(str)           – status message for the UI bar
        error_callback(str)            – error message
        audio_callback(bytes)          – raw 24 kHz PCM to play
        chat_callback(speaker, text)   – add a chat bubble
        turn_complete_callback()       – model turn finished (trigger transcription)
        interrupt_callback()           – model was interrupted (clear audio buffer)
    """

    def __init__(
        self,
        action_callback,
        status_callback,
        error_callback,
        audio_callback=None,
        chat_callback=None,
        turn_complete_callback=None,
        interrupt_callback=None,
    ):
        self.action_callback = action_callback
        self.status_callback = status_callback
        self.error_callback = error_callback
        self.audio_callback = audio_callback
        self.chat_callback = chat_callback
        self.turn_complete_callback = turn_complete_callback
        self.interrupt_callback = interrupt_callback

        self.backend_url = os.getenv("CLOUD_BACKEND_WS_URL", "ws://localhost:8000/ws")
        self.fps = int(os.getenv("SCREEN_CAPTURE_FPS", "2"))

        self.connected = False
        self.loop = asyncio.new_event_loop()
        self._ws = None
        # Track the last transcription speaker for in-place bubble updates
        self._last_speaker: str | None = None

    # ── Public API ─────────────────────────────────────────────────────────

    def start_connection(self):
        if self.connected:
            return
        self.status_callback("Connecting...")
        threading.Thread(target=self._run_loop, daemon=True).start()

    def stop_connection(self):
        self.connected = False
        if self._ws:
            asyncio.run_coroutine_threadsafe(self._ws.close(), self.loop)

    def send_audio_chunk(self, pcm_bytes: bytes):
        """Send raw 16 kHz PCM chunk to backend (thread-safe)."""
        if self.connected and self._ws:
            payload = {"type": "audio", "data": base64.b64encode(pcm_bytes).decode()}
            asyncio.run_coroutine_threadsafe(
                self._ws.send(json.dumps(payload)), self.loop
            )

    def send_text(self, text: str):
        """Send a text message to backend (thread-safe)."""
        if self.connected and self._ws:
            payload = {"type": "text", "text": text}
            asyncio.run_coroutine_threadsafe(
                self._ws.send(json.dumps(payload)), self.loop
            )

    # ── Internal async machinery ───────────────────────────────────────────

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._connect_and_handle())
        except Exception as e:
            self.error_callback(f"Event loop error: {e}")
        finally:
            self.connected = False
            self.status_callback("Disconnected")

    async def _connect_and_handle(self):
        try:
            self._action_queue = asyncio.Queue()
            async with websockets.connect(self.backend_url) as ws:
                self._ws = ws
                self.connected = True
                self.status_callback("Connected. Listening...")

                capture_task = asyncio.create_task(self._capture_and_send())
                receive_task = asyncio.create_task(self._receive_loop())
                action_task = asyncio.create_task(self._action_runner_loop())

                done, pending = await asyncio.wait(
                    [capture_task, receive_task, action_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for t in pending:
                    t.cancel()

        except websockets.exceptions.ConnectionClosedError:
            self.error_callback("Connection closed unexpectedly.")
        except ConnectionRefusedError:
            self.error_callback("Backend refused connection. Is it running?")
        except Exception as e:
            self.error_callback(f"WebSocket error: {e}")
        finally:
            self.connected = False
            self._ws = None

    async def _capture_and_send(self):
        """Capture screen frames and send as JPEG video.
        Uses pyautogui.screenshot() in an executor (it's blocking).
        """
        import functools
        loop = asyncio.get_event_loop()
        sleep_time = 1.0 / self.fps  # env default is 1fps now

        def _grab() -> bytes:
            return screenshot_with_cursor(quality=65)

        while self.connected:
            try:
                jpeg = await loop.run_in_executor(None, _grab)
                payload = {
                    "type": "video",
                    "data": base64.b64encode(jpeg).decode(),
                }
                await self._ws.send(json.dumps(payload))
            except Exception as e:
                self.error_callback(f"Screen capture error: {e}")
                break
            await asyncio.sleep(sleep_time)


    async def _send_verify_screenshot(self, delay: float = 0.4):
        """
        Wait `delay` seconds for the screen to settle after an action,
        then send a high-quality screenshot immediately.

        Uses pyautogui.screenshot() to maintain coordinate consistency.
        """
        await asyncio.sleep(delay)
        if not self.connected or not self._ws:
            return
        try:
            jpeg = screenshot_with_cursor(quality=95)
            payload = {
                "type": "video",
                "data": base64.b64encode(jpeg).decode(),
            }
            await self._ws.send(json.dumps(payload))
            logger.debug("Sent post-action verify screenshot")
        except Exception as e:
            logger.warning("Verify screenshot error: %s", e)


    async def _receive_loop(self):
        """Receive events from backend and dispatch to callbacks."""
        while self.connected:
            try:
                raw = await self._ws.recv()
                event = json.loads(raw)
                await self._dispatch(event)
            except websockets.exceptions.ConnectionClosed:
                break
            except Exception as e:
                self.error_callback(f"Error parsing message: {e}")

    async def _dispatch(self, event: dict):
        msg_type = event.get("type")

        if msg_type == "audio":
            if self.audio_callback:
                pcm = base64.b64decode(event["data"])
                self.audio_callback(pcm)

        elif msg_type == "user":
            text = event.get("text", "").strip()
            if text and self.chat_callback:
                # Update existing bubble if we're still in the same user turn
                update = (self._last_speaker == "You 🎙")
                self._last_speaker = "You 🎙"
                self.chat_callback("You 🎙", text, update)

        elif msg_type == "gemini":
            text = event.get("text", "").strip()
            if text and self.chat_callback:
                # Update existing bubble if we're still in the same agent turn
                update = (self._last_speaker == "Agent 🤖")
                self._last_speaker = "Agent 🤖"
                self.chat_callback("Agent 🤖", text, update)

        elif msg_type == "action":
            action = event.get("action", {})
            action_type = action.get("action_type", "?")
            thought = action.get("thought", "")
            
            # --- DEBUG TRACING ---
            print(f">>> UI CLIENT RECEIVED ACTION: {action}")
            logger.info(">>> UI CLIENT RECEIVED ACTION: %s", action)

            logger.info("Received action: %s — %s", action_type, thought)
            # Show thought in chat instead of status bar
            if thought:
                self._last_speaker = "System"
                if self.chat_callback:
                    self.chat_callback("System", f"▶ {thought}", False)
            
            # Queue the action for sequential execution instead of blocking here
            if hasattr(self, '_action_queue'):
                asyncio.run_coroutine_threadsafe(self._action_queue.put(action), self.loop)
            else:
                self.error_callback("Action queue not initialized!")

        elif msg_type == "speak":
            # Direct speak from backend (if implemented there)
            text = event.get("text", "")
            if text and self.chat_callback:
                self._last_speaker = "Agent 🤖"
                self.chat_callback("Agent 🤖", text, False)

        elif msg_type == "status":
            status_text = event.get("message", "")
            self.status_callback(status_text)
            if status_text and self.chat_callback:
                self._last_speaker = "System"
                self.chat_callback("System", f"ℹ {status_text}", False)

        elif msg_type == "turn_complete":
            self.status_callback("Listening...")
            self._last_speaker = None  # next turn gets a fresh bubble
            if self.turn_complete_callback:
                self.turn_complete_callback()

        elif msg_type == "interrupted":
            self.status_callback("Interrupted.")
            self._last_speaker = None
            if self.interrupt_callback:
                self.interrupt_callback()

        elif msg_type == "error":
            self.error_callback(f"Gemini error: {event.get('error', 'Unknown')}")

    async def _action_runner_loop(self):
        """Consume actions sequentially, adding a human-like delay between them
        to prevent overlapping and allow Windows UI (Start menu, search) to render.
        """
        while self.connected:
            try:
                action = await self._action_queue.get()
                
                # Execute the action
                success, msg = self.action_callback(action)
                print(f">>> ACTION EXECUTOR RESULT: success={success}, msg={msg}")
                
                if not success:
                    self.error_callback(f"Action failed: {msg}")
                else:
                    if action.get("action_type") == "speak":
                        # If the action was 'speak', ensure it appears in chat
                        text = action.get("text", "")
                        if text and self.chat_callback:
                            self._last_speaker = "Agent 🤖"
                            self.chat_callback("Agent 🤖", text, False)
                    
                    # After a successful action, wait for UI to settle, then take a screenshot
                    await asyncio.sleep(0.4) # Wait briefly for UI animations
                    asyncio.create_task(self._send_verify_screenshot(delay=0.1))
                    
                self._action_queue.task_done()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Action runner loop error: %s", e)
