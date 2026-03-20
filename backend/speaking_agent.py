import asyncio
import io
import logging
import os
import re

import pyautogui
from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

# V7.1: Restoring Native Tooling with Refined Gating.
LIVE_MODEL = "gemini-2.5-flash-native-audio-latest"
INPUT_SAMPLE_RATE = 16000   # 16 kHz PCM from the microphone
OUTPUT_SAMPLE_RATE = 24000  # 24 kHz PCM output from Gemini

def _get_system_instruction() -> str:
    """Build the system instruction with native tool calling rules (V10.3 state)."""
    screen_w, screen_h = pyautogui.size()
    return f"""
You are an advanced AI Orchestrator. You engage in natural conversation and observe the user's screen.

YOUR ACTIONS: You delegate multi-step tasks to your specialized autonomous agent by calling the `perform_screen_actions` tool.

SCREEN RESOLUTION: {screen_w} x {screen_h} pixels.

DELEGATION RULES:
1. When calling `perform_screen_actions`, your response MUST be ONLY the tool call. No audio/text allowed in the same turn.
2. Provide a brief verbal acknowledgement before starting a mission (e.g., "Sure, I'll take a look.").
3. MISSION COMPLETION: If you receive a text message like `[MISSION_COMPLETE: goal]`, it means the autonomous agent has finished the task. You MUST immediately provide a natural verbal summary of what was accomplished and return control to the user.
"""

def _build_ui_action_tool() -> types.Tool:
    """Restores the native tool definition for mission triggering."""
    return types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name="perform_screen_actions",
                description="Delegates a complex goal to the autonomous agent system.",
                parameters={
                    "type": "OBJECT",
                    "properties": {
                        "goal": {"type": "STRING", "description": "The high-level objective."},
                        "thought": {"type": "STRING", "description": "Reasoning for this mission."}
                    },
                    "required": ["goal", "thought"]
                }
            )
        ]
    )

class GeminiLiveAgent:
    def __init__(self):
        api_key = os.getenv("GEMINI_API_KEY")
        self.client = genai.Client(api_key=api_key, http_options={'api_version': 'v1beta'})
        self.model = LIVE_MODEL
        self.input_sample_rate = INPUT_SAMPLE_RATE
        self._tool_call_in_flight = False 

    async def start_session(
        self,
        audio_input_queue: asyncio.Queue,
        video_input_queue: asyncio.Queue,
        text_input_queue: asyncio.Queue,
        tool_response_queue: asyncio.Queue = None,
    ):
        config = types.LiveConnectConfig(
            response_modalities=[types.Modality.AUDIO],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name="Puck"
                    )
                )
            ),
            system_instruction=types.Content(
                parts=[types.Part(text=_get_system_instruction())]
            ),
            tools=[_build_ui_action_tool()],
            input_audio_transcription=types.AudioTranscriptionConfig(),
        )

        async with self.client.aio.live.connect(model=self.model, config=config) as session:

            async def send_audio():
                logger.info("SpeakingAgent: send_audio task started.")
                try:
                    while True:
                        chunk = await audio_input_queue.get()
                        if not chunk: continue
                        if self._tool_call_in_flight:
                            continue 
                        await session.send_realtime_input(
                            audio=types.Blob(
                                data=chunk,
                                mime_type=f"audio/pcm;rate={self.input_sample_rate}",
                            )
                        )
                except asyncio.CancelledError: pass
                except Exception as e:
                    logger.error("SpeakingAgent: send_audio error: %s", e)

            async def send_video():
                logger.info("SpeakingAgent: send_video task started.")
                try:
                    while True:
                        chunk = await video_input_queue.get()
                        if not chunk: continue
                        if self._tool_call_in_flight:
                            continue 
                        await session.send_realtime_input(
                            video=types.Blob(data=chunk, mime_type="image/jpeg")
                        )
                except asyncio.CancelledError: pass
                except Exception as e:
                    logger.error("SpeakingAgent: send_video error: %s", e)

            async def send_text():
                try:
                    while True:
                        text = await text_input_queue.get()
                        if not text: continue
                        if self._tool_call_in_flight:
                            # V10.3: Don't drop mission completion signals!
                            if "[MISSION_COMPLETE" in text:
                                await session.send_realtime_input(text=text)
                                continue
                            continue
                        await session.send_realtime_input(text=text)
                except asyncio.CancelledError: pass

            async def send_tool_responses():
                """Polls for mission completion signals and sends native tool responses."""
                logger.info("SpeakingAgent: send_tool_responses task started.")
                try:
                    while True:
                        if tool_response_queue:
                            resp = await tool_response_queue.get()
                            logger.info("SpeakingAgent: Sending native tool response: %s", resp)
                            await session.send_tool_response(types.LiveClientToolResponse(
                                function_responses=[types.FunctionResponse(
                                    name="perform_screen_actions",
                                    response={"result": resp.get("status", "mission_complete")}
                                )]
                            ))
                            self._tool_call_in_flight = False 
                        await asyncio.sleep(0.01)
                except asyncio.CancelledError: pass
                except Exception as e:
                    logger.error("SpeakingAgent: send_tool_responses error: %s", e)

            event_queue: asyncio.Queue = asyncio.Queue()

            async def receive_loop():
                logger.info("SpeakingAgent: receive_loop started.")
                _user_text = ""
                _gemini_text = ""
                try:
                    while True:
                        async for response in session.receive():
                            server_content = response.server_content
                            tool_call = response.tool_call

                            if tool_call and tool_call.function_calls:
                                self._tool_call_in_flight = True
                                logger.info("SpeakingAgent: Native Tool Call received: %s", tool_call.function_calls[0].name)
                                await event_queue.put({"type": "tool_call", "data": tool_call})

                            if server_content:
                                if server_content.model_turn:
                                    for part in server_content.model_turn.parts:
                                        if part.inline_data:
                                            await event_queue.put({"type": "audio", "data": part.inline_data.data})
                                        elif part.text:
                                            _gemini_text += part.text
                                            await event_queue.put({"type": "gemini", "text": _gemini_text})

                                if server_content.input_transcription and server_content.input_transcription.text:
                                    t = server_content.input_transcription.text
                                    if len(t) > len(_user_text):
                                        _user_text = t
                                        await event_queue.put({"type": "user", "text": _user_text})

                                if server_content.turn_complete:
                                    _user_text = ""
                                    _gemini_text = ""
                                    await event_queue.put({"type": "turn_complete"})

                                if server_content.interrupted:
                                    _user_text = ""
                                    _gemini_text = ""
                                    await event_queue.put({"type": "interrupted"})
                        
                        await asyncio.sleep(0.01)

                except Exception as e:
                    logger.exception("Error in Gemini receive loop")
                    await event_queue.put({"type": "error", "error": str(e)})
                finally:
                    await event_queue.put(None)

            send_audio_task = asyncio.create_task(send_audio())
            send_video_task = asyncio.create_task(send_video())
            send_text_task = asyncio.create_task(send_text())
            send_tool_task = asyncio.create_task(send_tool_responses())
            receive_task = asyncio.create_task(receive_loop())

            try:
                while True:
                    event = await event_queue.get()
                    if event is None: break
                    yield event
            finally:
                send_audio_task.cancel()
                send_video_task.cancel()
                send_text_task.cancel()
                send_tool_task.cancel()
                receive_task.cancel()
