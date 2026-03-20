import logging
import base64
import asyncio
import os
import json
from typing import List, AsyncGenerator, Dict, Any

from google import genai
from google.genai import types

from models import UIAction
from system_prompts import PLANNER_SYSTEM_PROMPT
from screen_utils import capture_screen_with_grid
from mcp_manager import MCPManager
from playbook_manager import PlaybookManager
import pyautogui

logger = logging.getLogger(__name__)

PLANNER_MODEL = "gemini-3-pro-preview"

class PlannerAgent:
    def __init__(self):
        self.history: List[str] = []
        self.current_plan: List[str] = []
        self.screen_w, self.screen_h = pyautogui.size()
        
        # Initialize MCP Manager & Playbook Manager
        self.mcp = MCPManager()
        self.playbook = PlaybookManager()
        
        # Initialize Native Client
        api_key = os.getenv("GEMINI_API_KEY")
        self.client = genai.Client(api_key=api_key, http_options={'api_version': 'v1beta'})
        self.model_id = PLANNER_MODEL

    async def initialize_mcp(self):
        """Initializes default MCP servers (e.g., search)."""
        await self.mcp.connect_to_server(
            "search", 
            "npx", 
            ["-y", "@modelcontextprotocol/server-duckduckgo"]
        )

    async def close(self):
        """Cleans up resources."""
        await self.mcp.close_all()

    def reset_history(self):
        self.history = []

    def _get_tools_config(self) -> List[types.Tool]:
        """Defines the native tools for the planner brain."""
        ui_action_tool = types.Tool(
            function_declarations=[
                types.FunctionDeclaration(
                    name="UIAction",
                    description="Emits a structured UI action (click, type, status, etc.)",
                    parameters=UIAction.schema()
                )
            ]
        )
        
        read_file_tool = types.Tool(
            function_declarations=[
                types.FunctionDeclaration(
                    name="read_file_tool",
                    description="Reads the content of a local text-based file.",
                    parameters={
                        "type": "OBJECT",
                        "properties": {
                            "path": {"type": "STRING", "description": "Absolute path to the file."}
                        },
                        "required": ["path"]
                    }
                )
            ]
        )
        
        mcp_tool = types.Tool(
            function_declarations=[
                types.FunctionDeclaration(
                    name="use_mcp_tool",
                    description="Calls an external MCP tool (searching, data extraction, etc.)",
                    parameters={
                        "type": "OBJECT",
                        "properties": {
                            "server": {"type": "STRING", "description": "The name of the MCP server (e.g., 'search')"},
                            "tool_name": {"type": "STRING", "description": "The name of the tool to call"},
                            "arguments": {"type": "OBJECT", "description": "A dictionary of arguments for the tool"}
                        },
                        "required": ["server", "tool_name", "arguments"]
                    }
                )
            ]
        )
        
        record_tool = types.Tool(
            function_declarations=[
                types.FunctionDeclaration(
                    name="record_successful_procedure",
                    description="Saves the current successful procedure as a Standard Operating Procedure (SOP) for future missions.",
                    parameters={
                        "type": "OBJECT",
                        "properties": {
                            "goal_context": {"type": "STRING", "description": "A concise title or goal for this SOP."},
                            "steps": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "The list of successful steps taken."},
                            "insight": {"type": "STRING", "description": "Any key tips or insights for future missions."}
                        },
                        "required": ["goal_context", "steps", "insight"]
                    }
                )
            ]
        )
        
        return [ui_action_tool, read_file_tool, mcp_tool, record_tool]

    async def execute_goal(self, goal: str, state_dict: dict) -> AsyncGenerator[UIAction, None]:
        """
        Continuous loop that yields UIActions until the goal is met.
        V8.2: Isolated history & Increased Step Limit.
        """
        self.reset_history() # Clear history for each new mission
        self.history.append(f"--- NEW MISSION: {goal} ---")
        max_steps = 30
        step = 0
        
        initial_history_str = "\n".join(f"- {h}" for h in self.history) if self.history else "None yet."
        playbook_context = self.playbook.format_sops_for_llm(goal)
        
        # Internal context messages (Native Format)
        messages: List[types.Content] = []
        
        while step < max_steps:
            if state_dict.get("active_mission_cancelled", False):
                logger.warning("Planner: Active mission cancelled.")
                yield UIAction(action_type="status", thought="🛑 Mission cancelled by user.")
                break
                
            step += 1
            
            # V9.2: Synchronization Delay
            # Wait for previous action (typing, clicking, loading) to stabilize
            logger.info("Planner Step %d: Waiting for UI to stabilize (1.5s)...", step)
            await asyncio.sleep(1.5)
            
            # Fresh screenshot
            current_img = capture_screen_with_grid()
            if not current_img:
                logger.warning("Planner Step %d: Screenshot failed.", step)
                await asyncio.sleep(0.5)
                continue

            logger.info("Planner Step %d: Starting native reasoning cycle...", step)

            # Context assembly
            history_str = "\n".join(f"- {h}" for h in self.history) if self.history else "None yet."
            mcp_tools = self.mcp.get_tools_for_llm()
            mcp_context = f"\n\nAVAILABLE MCP TOOLS:\n{mcp_tools}" if mcp_tools else ""
            
            system_instruction = PLANNER_SYSTEM_PROMPT.format(goal=goal, history=history_str) + mcp_context + playbook_context
            b64_img = base64.b64encode(current_img).decode("utf-8")
            
            # Core turn content
            messages = [
                types.Content(role="user", parts=[
                    types.Part(text=f"System Instruction: {system_instruction}\n\nWhat is the next action?"),
                    types.Part(inline_data=types.Blob(data=b64_img, mime_type="image/jpeg"))
                ])
            ]

            try:
                while True:
                    if state_dict.get("active_mission_cancelled", False):
                        return

                    # Native generation call
                    response = await self.client.aio.models.generate_content(
                        model=self.model_id,
                        contents=messages,
                        config=types.GenerateContentConfig(
                            tools=self._get_tools_config(),
                            temperature=0.0
                        )
                    )
                    
                    if not response.candidates or not response.candidates[0].content or not response.candidates[0].content.parts:
                         logger.warning("Planner Step %d: Empty response.", step)
                         break

                    parts = response.candidates[0].content.parts
                    tool_call = None
                    for p in parts:
                        if p.function_call:
                            tool_call = p.function_call
                            break
                    
                    if not tool_call:
                        # If no tool call, maybe it's just text. We need a tool call to proceed.
                        text_resp = "".join(p.text for p in parts if p.text)
                        logger.warning("Planner Step %d: No tool calls. Text: %s", step, text_resp)
                        yield UIAction(action_type="status", thought="Brain is thinking but didn't act. Retrying...")
                        await asyncio.sleep(1.0)
                        break

                    name = tool_call.name
                    args = tool_call.args
                    
                    # Tool Handling
                    if name == "read_file_tool":
                        logger.info("Planner: Calling native read_file_tool")
                        path = args.get("path")
                        try:
                            def read_sync():
                                with open(path, "r", encoding="utf-8") as f:
                                    return f.read(5000)
                            result = await asyncio.to_thread(read_sync)
                        except Exception as e:
                            result = f"Error: {e}"
                        
                        # Add call and response to history
                        messages.append(response.candidates[0].content)
                        messages.append(types.Content(role="user", parts=[
                            types.Part(function_response=types.FunctionResponse(name=name, response={"result": result}))
                        ]))
                        continue
                        
                    elif name == "use_mcp_tool":
                        logger.info("Planner: Calling native use_mcp_tool")
                        server = args.get("server")
                        tool_name = args.get("tool_name")
                        mcp_args = args.get("arguments", {})
                        
                        yield UIAction(action_type="status", thought=f"🔍 Searching: {tool_name}...")
                        try:
                            result = await self.mcp.call_tool(server, tool_name, mcp_args)
                        except Exception as e:
                            result = f"Error: {e}"
                        
                        messages.append(response.candidates[0].content)
                        messages.append(types.Content(role="user", parts=[
                            types.Part(function_response=types.FunctionResponse(name=name, response={"result": result}))
                        ]))
                        continue
                        
                    elif name == "record_successful_procedure":
                        logger.info("Planner: Calling native record_successful_procedure")
                        goal_ctx = args.get("goal_context")
                        steps_list = args.get("steps", [])
                        insight = args.get("insight", "")
                        
                        self.playbook.record_procedure(goal_ctx, steps_list, insight)
                        
                        messages.append(response.candidates[0].content)
                        messages.append(types.Content(role="user", parts=[
                            types.Part(function_response=types.FunctionResponse(name=name, response={"result": "Procedure recorded successfully."}))
                        ]))
                        continue
                        
                    elif name == "UIAction":
                        logger.info("Planner: Action decided: %s", args.get("action_type"))
                        action = UIAction(**args)
                        
                        if hasattr(action, "plan") and action.plan:
                            self.current_plan = action.plan

                        # Achievement check
                        is_finished = (
                            (action.status_code and "GOAL_REACHED" in action.status_code.upper()) or 
                            (action.thought and "GOAL_REACHED" in action.thought.upper()) or
                            (action.action_type == "status" and "GOAL" in (action.thought or "").upper() and "ACHIEVED" in (action.thought or "").upper())
                        )

                        if is_finished:
                            logger.info("Planner: Goal achieved detection triggered.")
                            action.thought = f"✅ Goal Achieved: {action.thought}"
                            self.history.append(f"MISSION COMPLETE: {action.thought}")
                            yield action
                            return
                            
                        if action.action_type == "ask":
                            self.history.append(f"STEP {step}: ASK: {action.text}")
                            yield action
                            return
                            
                        # Grounding mapping
                        x_norm = getattr(action, "x", -1)
                        y_norm = getattr(action, "y", -1)
                        if x_norm is not None and y_norm is not None and x_norm != -1 and y_norm != -1:
                            action.x = int((float(x_norm) / 1000.0) * self.screen_w)
                            action.y = int((float(y_norm) / 1000.0) * self.screen_h)

                        yield action
                        self.history.append(f"STEP {step}: {action.action_type} (thought: {action.thought})")
                        await asyncio.sleep(0.1)
                        break 

            except Exception as e:
                logger.exception("Planner step failed")
                yield UIAction(action_type="status", thought=f"Brain Error: {e}")
                break

        if step >= max_steps:
             yield UIAction(action_type="status", thought="Reached maximum step limit.")
