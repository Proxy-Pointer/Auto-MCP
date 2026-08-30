import asyncio
import os
import json
import requests
from typing import Dict, Any, List
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from dotenv import load_dotenv

from config import DOTENV_PATH, LLM_MODEL, GEMINI_API_URL_TEMPLATE

load_dotenv(DOTENV_PATH)
API_KEY = os.environ.get("GOOGLE_API_KEY")

def llm_select_tool(task: str, tools_schema: List[Dict[str, Any]], exclude_tools: List[str] = None, last_error: str = None) -> Dict[str, Any]:
    url = GEMINI_API_URL_TEMPLATE.format(model=LLM_MODEL, api_key=API_KEY)
    headers = {"Content-Type": "application/json"}
    
    available_tools = tools_schema
    if exclude_tools:
        available_tools = [t for t in tools_schema if t["name"] not in exclude_tools]
        if not available_tools:
            available_tools = tools_schema  # fallback if all excluded
    
    error_context = ""
    if last_error:
        error_context = f"\nPREVIOUS ATTEMPT FAILED with this error: {last_error}\nYou MUST choose a different tool or different arguments this time to fix this error.\n"
    import datetime
    current_date = datetime.datetime.now().strftime("%Y-%m-%d")

    prompt = f"""
You are a tool execution agent. Your ONLY job is to execute the EXACT step described in the task using the most appropriate tool.
CURRENT DATE: {current_date}

CRITICAL RULES:
- Execute ONLY what the current step says. Do NOT proactively set up prerequisites (e.g. do NOT create a table if asked to insert data — just attempt the insert).
- If the step says "insert", use an insert tool. If the step says "create table", use a create-table tool. Match the tool to the step description precisely.
- If the step says "insert data", pick the insert/write tool and attempt it immediately. The orchestrator will handle failures and set up any prerequisites in separate recovery steps.
- If a "Past Execution Context" is provided, use it to inform your arguments (e.g. use exact column names from a previously created table, or use data retrieved in previous steps).
- IMPORTANT: All text you generate for the arguments (e.g., file contents, summaries, messages) MUST be written strictly in English. If you receive non-English data in the context or from the task, you MUST translate it to English before including it in the arguments.
- STRICT SCHEMA COMPLIANCE: You MUST restrict your arguments ONLY to the exact parameters defined in the tool's inputSchema. Do NOT hallucinate, invent, or add extra parameters (like 'perPage', 'limit', etc.) if they do not exist in the schema.
- GITHUB SEARCH RULE: When asked to search GitHub for a specific year, ALWAYS use bounded date ranges (e.g., `created:2020-01-01..2023-12-31`) rather than open-ended inequalities (like `created:>2024`).
{error_context}
Output ONLY a JSON object with two keys:
1. "tool_name": the string name of the tool to call.
2. "arguments": a JSON object matching the required schema strictly.

Task: {task}
Available Tools:
{json.dumps(available_tools, indent=2)}
"""
    data = {"contents": [{"parts": [{"text": prompt}]}]}
    response = requests.post(url, headers=headers, json=data)
    
    if response.status_code != 200:
        raise Exception(f"LLM API Error: {response.text}")
        
    text = response.json()['candidates'][0]['content']['parts'][0]['text']
    # Clean up markdown code blocks if present
    text = text.strip()
    if text.startswith("```json"):
        text = text[7:]
    if text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
        
    return json.loads(text.strip())

async def dynamic_mcp_execute(command: str, args: List[str], task: str):
    """Returns a tuple of (result_text: str, tools_schema: List[Dict])."""
    print(f"      [mcp_client] Starting dynamic execution via {command} {' '.join(args)}")
    server_params = StdioServerParameters(
        command=command,
        args=args,
        env=os.environ.copy(),  # Pass full env so API keys (.env) reach the child process
    )
    
    error_to_raise = None
    text_output = ""
    tools_schema = []

    # Initialize the stdio transport
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            
            # List tools dynamically
            tools = await session.list_tools()
            for t in tools.tools:
                tools_schema.append({
                    "name": t.name,
                    "description": t.description,
                    "inputSchema": t.input_schema
                })
            
            print(f"      [mcp_client] Found {len(tools_schema)} available tools. Asking LLM to pick...")

            MAX_TOOL_RETRIES = 3
            tried_tools = []
            last_tool_error = None
            
            for attempt in range(MAX_TOOL_RETRIES):
                # Use LLM to pick tool and formulate arguments (excluding previously failed tools)
                # On retry, pass the last error as context so the LLM can pick a better tool/args
                decision = llm_select_tool(task, tools_schema, exclude_tools=tried_tools, last_error=last_tool_error)
                tool_name = decision.get("tool_name")
                tool_args = decision.get("arguments", {})
                tried_tools.append(tool_name)
                
                print(f"      [mcp_client] LLM selected tool '{tool_name}' with args {json.dumps(tool_args)}")
                
                # Call the tool with a timeout to prevent deadlocks if the server crashes or outputs garbage
                result = await asyncio.wait_for(
                    session.call_tool(
                        name=tool_name, 
                        arguments=tool_args
                    ),
                    timeout=120.0
                )
                
                # Parse output
                text_output = ""
                for content in result.content:
                    if content.type == "text":
                        text_output += content.text + "\n"
                        
                # Check for tool-level errors (including validation errors from FastMCP)
                is_error_flag = getattr(result, "isError", False)
                tool_error = (
                    is_error_flag
                    or "Database error:" in text_output
                    or "Error:" in text_output
                    or "validation error" in text_output.lower()
                    or "INVALID_INPUT:" in text_output
                    or "Failed:" in text_output
                )
                
                if not tool_error:
                    break  # Success — exit retry loop
                
                last_tool_error = text_output.strip()  # Feed error back to LLM on next attempt
                if attempt < MAX_TOOL_RETRIES - 1:
                    print(f"      [mcp_client] Tool '{tool_name}' returned an error, retrying with error context...")
                else:
                    error_to_raise = f"Tool execution failed after {MAX_TOOL_RETRIES} attempts: {text_output.strip()}"
                
    if error_to_raise:
        raise Exception(error_to_raise)
        
    return text_output, tools_schema

def sync_dynamic_mcp_execute(command: str, args: List[str], task: str):
    """Synchronous wrapper to integrate into LangGraph node. Returns (result_text, tools_schema)."""
    return asyncio.run(dynamic_mcp_execute(command, args, task))

if __name__ == "__main__":
    import sys
    from config import VENV_UVX_PATH
    # Test script locally with uvx duckduckgo-mcp-server
    res, tools = sync_dynamic_mcp_execute(VENV_UVX_PATH, ["duckduckgo-mcp-server"], "What is the weather in new york?")
    print("Result snippet:", res[:500])
    print("Tools:", [t["name"] for t in tools])
