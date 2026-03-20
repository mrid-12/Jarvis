from typing import Optional, List, Dict, Any, Union
from pydantic import BaseModel, Field


# --- Action & Grounding Models ---

class UIAction(BaseModel):
    """Represents a discrete physical UI action to execute."""
    action_type: str = Field(..., description="The type of action: 'click', 'type', 'key', 'scroll', 'status'")
    target: Optional[str] = Field(None, description="The textual description of the target (e.g., 'Start button', 'Search bar')")
    text: Optional[str] = Field(None, description="The text to type if action_type is 'type' or 'key'")
    key: Optional[str] = Field(None, description="A single key or combo like 'ctrl+c'")
    amount: Optional[int] = Field(None, description="Scroll amount")
    x: Optional[int] = Field(None, description="The resolved X coordinate")
    y: Optional[int] = Field(None, description="The resolved Y coordinate")
    thought: Optional[str] = Field(None, description="The agent's reasoning behind the action, to be shown in UI")
    plan: Optional[List[str]] = Field(None, description="The step-by-step strategy to achieve the goal")
    status_code: Optional[str] = Field(None, description="Status code indicating progress like CONTINUE or GOAL_REACHED")

class GroundingResult(BaseModel):
    """The outcome of a visual grounding request."""
    x: int
    y: int
    confidence: float
    description: str


# --- WebSocket Client <-> Backend Payloads ---

class ClientPayload(BaseModel):
    """Base class for all payloads sent over the WebSocket."""
    type: str

class AudioPayload(ClientPayload):
    type: str = "audio"
    data: str  # Base64 encoded PCM

class ActionPayload(ClientPayload):
    type: str = "action"
    action: UIAction

class StatusPayload(ClientPayload):
    type: str = "status"
    message: str

class ErrorPayload(ClientPayload):
    type: str = "error"
    error: str

class ChatPayload(ClientPayload):
    type: str = "user"  # or "gemini" or "system"
    text: str
