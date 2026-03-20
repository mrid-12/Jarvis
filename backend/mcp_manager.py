import asyncio
import logging
import os
from typing import List, Dict, Any, Optional
from contextlib import AsyncExitStack
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger(__name__)

class MCPManager:
    """
    Manages connections to multiple MCP servers and provides a unified interface
     for the agents to discover and call tools.
    """
    def __init__(self):
        self.sessions: Dict[str, ClientSession] = {}
        self.exit_stack = AsyncExitStack()
        self.tools: List[Dict[str, Any]] = []

    async def connect_to_server(self, name: str, command: str, args: List[str] = None):
        """Connects to an MCP server via stdio."""
        logger.info(f"Connecting to MCP server '{name}' via: {command} {' '.join(args or [])}")
        server_params = StdioServerParameters(command=command, args=args or [])
        
        try:
            # Note: stdio_client returns an async context manager
            transport_cm = stdio_client(server_params)
            read, write = await self.exit_stack.enter_async_context(transport_cm)
            session = await self.exit_stack.enter_async_context(ClientSession(read, write))
            
            await session.initialize()
            self.sessions[name] = session
            logger.info(f"Successfully initialized MCP server '{name}'")
            
            # Fetch available tools
            response = await session.list_tools()
            for tool in response.tools:
                self.tools.append({
                    "server": name,
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.inputSchema
                })
                
        except Exception as e:
            logger.error(f"Failed to connect to MCP server '{name}': {e}")

    async def call_tool(self, server_name: str, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """Calls a tool on a specific MCP server."""
        if server_name not in self.sessions:
            raise ValueError(f"Server '{server_name}' not found.")
        
        session = self.sessions[server_name]
        try:
            result = await session.call_tool(tool_name, arguments)
            # Extract text content from MCP result
            if hasattr(result, "content") and result.content:
                return "\n".join([c.text for c in result.content if hasattr(c, "text")])
            return str(result)
        except Exception as e:
            logger.error(f"Error calling tool '{tool_name}' on '{server_name}': {e}")
            raise

    async def close_all(self):
        """Closes all active MCP sessions."""
        await self.exit_stack.aclose()
        self.sessions.clear()
        self.tools.clear()

    def get_tools_for_llm(self) -> List[Dict[str, Any]]:
        """Returns the list of available tools in a format suitable for the LLM."""
        return self.tools
