"""
FastAPI WebSocket backend for the Gemini Live Agent.
Restoration V9.4: Connection Stability & Immediate Tool Satisfaction.
"""

import asyncio
import base64
import json
import logging
import os
import re
from typing import Optional, List

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from speaking_agent import GeminiLiveAgent
from memory_manager import MemoryManager
from planner_agent import PlannerAgent
from models import UIAction, ActionPayload

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize MCP
    logger.info("Initializing MCP sessions...")
    await planner.initialize_mcp()
    yield
    # Shutdown: Close MCP
    logger.info("Closing MCP sessions...")
    await planner.close()

app = FastAPI(title="Gemini Live Agent Backend", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

memory_manager = MemoryManager()
planner = PlannerAgent()
logger.info("PlannerAgent ready.")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    logger.info("Client connected.")

    agent = GeminiLiveAgent()
    audio_input_queue: asyncio.Queue = asyncio.Queue()
    video_input_queue: asyncio.Queue = asyncio.Queue()
    text_input_queue: asyncio.Queue = asyncio.Queue()
    tool_response_queue: asyncio.Queue = asyncio.Queue()
    ws_lock = asyncio.Lock()
    mission_task: Optional[asyncio.Task] = None
    
    # State for the planner to access the latest screen context and cancellation flags
    state = {
        "latest_frame": None,
        "active_mission_cancelled": False 
    }

    async def receive_from_client():
        """Reads messages from the WebSocket and routes them to the correct queue."""
        try:
            while True:
                raw = await websocket.receive_text()
                payload = json.loads(raw)
                msg_type = payload.get("type")

                if msg_type == "audio":
                    pcm_bytes = base64.b64decode(payload["data"])
                    await audio_input_queue.put(pcm_bytes)

                elif msg_type == "video":
                    jpeg_bytes = base64.b64decode(payload["data"])
                    state["latest_frame"] = jpeg_bytes
                    await video_input_queue.put(jpeg_bytes)

                elif msg_type == "text":
                    text = payload.get("text", "")
                    context = memory_manager.get_relevant_instructions(text)
                    enriched = f"User: {text}" + (f"\nContext: {context}" if context else "")
                    await text_input_queue.put(enriched)

        except WebSocketDisconnect:
            logger.info("Client disconnected.")
        except Exception as e:
            logger.error("receive_from_client error: %s", e)
        finally:
            await audio_input_queue.put(b"")
            await video_input_queue.put(b"")
            await text_input_queue.put("")

    async def forward_events_to_client():
        """Consumes events from the Gemini async generator and sends to the client."""
        nonlocal mission_task
        try:
            async for event in agent.start_session(
                audio_input_queue, video_input_queue, text_input_queue, tool_response_queue
            ):
                msg_type = event.get("type")

                if msg_type == "audio":
                    async with ws_lock:
                        await websocket.send_json(
                            {"type": "audio", "data": base64.b64encode(event["data"]).decode()}
                        )

                elif msg_type == "tool_call":
                    # Stable V7.1 Native Tool Handling
                    tool_call_data = event.get("data")
                    if tool_call_data and tool_call_data.function_calls:
                        fc = tool_call_data.function_calls[0]
                        if fc.name == "perform_screen_actions":
                            goal = fc.args.get("goal", "Generic Mission")
                            thought = fc.args.get("thought", "")
                            logger.info("Main: Starting Native Mission: %s (%s)", goal, thought)
                            
                            # Notify UI
                            async with ws_lock:
                                await websocket.send_json({
                                    "type": "status",
                                    "message": f"🤖 MISSION: {goal}"
                                })
                            
                            # V9.4: Satisfy the tool call IMMEDIATELY to prevent 1011 deadline errors
                            await tool_response_queue.put({"status": "mission_started"})
                            
                            state["active_mission_cancelled"] = False
                            if mission_task and not mission_task.done():
                                mission_task.cancel()
                            
                            async def run_mission_task():
                                try:
                                    async for action in planner.execute_goal(goal, state):
                                        try:
                                            async with ws_lock:
                                                await websocket.send_json({
                                                    "type": "action",
                                                    "action": action.dict()
                                                })
                                        except Exception as e:
                                            logger.error("Failed to send action: %s", e)
                                            break
                                        await asyncio.sleep(0.05)
                                except asyncio.CancelledError:
                                    logger.info("Mission task killed.")
                                finally:
                                    logger.info("Mission task complete for: %s", goal)
                                    # V10.2: Tell the SpeakingAgent the mission is done so it resumes listening
                                    await text_input_queue.put(f"[MISSION_COMPLETE: {goal}]")

                            mission_task = asyncio.create_task(run_mission_task())

                elif msg_type == "interrupted":
                    logger.warning("Human interrupted! Stopping mission.")
                    state["active_mission_cancelled"] = True
                    if mission_task and not mission_task.done():
                        mission_task.cancel()
                    async with ws_lock:
                        await websocket.send_json(event)

                else:
                    async with ws_lock:
                        await websocket.send_json(event)

        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error("forward_events_to_client error: %s", e)
            try:
                async with ws_lock:
                    await websocket.send_json({"type": "error", "error": str(e)})
            except Exception:
                pass

    receive_task = asyncio.create_task(receive_from_client())
    forward_task = asyncio.create_task(forward_events_to_client())

    done, pending = await asyncio.wait(
        [receive_task, forward_task],
        return_when=asyncio.FIRST_COMPLETED,
    )
    
    if mission_task and not mission_task.done():
        mission_task.cancel()

    for task in pending:
        task.cancel()

    logger.info("WebSocket session closed.")

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
