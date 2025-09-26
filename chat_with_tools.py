import json
import os
import sys
import time
from typing import Any, Dict, List

import requests
from openai import OpenAI
from jsonl_logger import get_logger
from dotenv import load_dotenv

MCP_SERVER_URL = "http://127.0.0.1:5000/rpc"
logger = get_logger("mcp-client-chat")

load_dotenv()

PROVIDER = os.getenv("PROVIDER", "ollama").lower()  # ollama | openai | anthropic

# Ollama defaults
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "granite3.3")

# OpenAI settings
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL")  # optional; leave empty for official
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# Anthropic settings
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")


def mcp_initialize() -> Dict[str, Any]:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "ToolChatDemo", "version": "0.1"},
        },
    }
    logger.log("client_request", {"method": "initialize", "payload": payload})
    resp = requests.post(MCP_SERVER_URL, json=payload).json()
    logger.log("client_response", {"method": "initialize", "response": resp})
    return resp


def mcp_list_tools() -> List[Dict[str, Any]]:
    payload = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": None}
    logger.log("client_request", {"method": "tools/list", "payload": payload})
    resp = requests.post(MCP_SERVER_URL, json=payload).json()
    logger.log("client_response", {"method": "tools/list", "response": resp})
    return resp.get("result", {}).get("tools", [])


def mcp_call_tool(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    payload = {
        "jsonrpc": "2.0",
        "id": int(time.time()),
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
    logger.log("client_request", {"method": "tools/call", "payload": payload})
    resp = requests.post(MCP_SERVER_URL, json=payload).json()
    logger.log("client_response", {"method": "tools/call", "response": resp})
    if "result" in resp:
        return resp["result"]
    raise RuntimeError(resp.get("error", {"message": "Unknown MCP error"}))


def format_tools_for_openai(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    formatted = []
    for t in tools:
        name = t.get("name")
        desc = t.get("description", "")
        schema = t.get("inputSchema", {"type": "object", "properties": {}})
        formatted.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": desc,
                    "parameters": schema,
                },
            }
        )
    logger.log("tools_mapped_for_llm", {"count": len(formatted), "provider": PROVIDER})
    return formatted


def format_tools_for_anthropic(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    # Anthropic expects: [{ name, description, input_schema }]
    formatted = []
    for t in tools:
        formatted.append(
            {
                "name": t.get("name"),
                "description": t.get("description", ""),
                "input_schema": t.get("inputSchema", {"type": "object", "properties": {}}),
            }
        )
    logger.log("tools_mapped_for_llm", {"count": len(formatted), "provider": "anthropic"})
    return formatted


def run_chat_openai_like(base_url: str, api_key: str, model_name: str, tools: List[Dict[str, Any]]) -> None:
    client = OpenAI(base_url=base_url, api_key=api_key)

    system_prompt = (
        "You are a helpful assistant with access to tools. "
        "When appropriate, call a tool using function calling. "
        "Prefer using available tools to read/write/search files."
    )

    available_tools = format_tools_for_openai(tools)

    print("Loaded tools:")
    print(json.dumps(tools, indent=2))

    print("\nType your question (Ctrl+C to exit):")
    while True:
        try:
            user_msg = input("> ")
        except KeyboardInterrupt:
            print("\nBye!")
            break

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ]

        logger.log("chat_request", {"messages": messages, "provider": PROVIDER})
        resp = client.chat.completions.create(
            model=model_name,
            messages=messages,
            tools=available_tools,
            tool_choice="auto",
        )
        logger.log("chat_response", {"raw": resp.model_dump()})

        choice = resp.choices[0]
        message = choice.message

        if message.tool_calls:
            tool_results: List[Dict[str, Any]] = []
            for tc in message.tool_calls:
                fn_name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                logger.log("llm_tool_call", {"name": fn_name, "arguments": args, "tool_call_id": tc.id})
                try:
                    mcp_result = mcp_call_tool(fn_name, args)
                    tool_results.append(
                        {
                            "tool_call_id": tc.id,
                            "role": "tool",
                            "name": fn_name,
                            "content": json.dumps(mcp_result),
                        }
                    )
                except Exception as e:
                    tool_results.append(
                        {
                            "tool_call_id": tc.id,
                            "role": "tool",
                            "name": fn_name,
                            "content": json.dumps({"isError": True, "message": str(e)}),
                        }
                    )
                    logger.log("llm_tool_error", {"name": fn_name, "error": str(e)}, level="ERROR")

            followup_messages = messages + [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": tr["tool_call_id"],
                            "type": "function",
                            "function": {"name": tr["name"], "arguments": "{}"},
                        }
                        for tr in tool_results
                    ],
                }
            ] + tool_results

            logger.log("chat_followup_request", {"messages": followup_messages})
            final = client.chat.completions.create(
                model=model_name,
                messages=followup_messages,
            )
            logger.log("chat_followup_response", {"raw": final.model_dump()})
            print(final.choices[0].message.content or "")
        else:
            print(message.content or "")


def run_chat_anthropic(tools: List[Dict[str, Any]]) -> None:
    try:
        from anthropic import Anthropic
    except Exception as e:
        raise RuntimeError("Anthropic SDK not installed or import failed") from e

    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not set in environment")

    client = Anthropic(api_key=ANTHROPIC_API_KEY)

    system_prompt = (
        "You are a helpful assistant with access to tools. "
        "When appropriate, call a tool using tool_use. "
        "Prefer using available tools to read/write/search files."
    )

    anthropic_tools = format_tools_for_anthropic(tools)

    print("Loaded tools:")
    print(json.dumps(tools, indent=2))

    print("\nType your question (Ctrl+C to exit):")
    while True:
        try:
            user_msg = input("> ")
        except KeyboardInterrupt:
            print("\nBye!")
            break

        messages = [
            {"role": "user", "content": user_msg},
        ]

        logger.log("chat_request", {"messages": messages, "provider": PROVIDER})
        resp = client.messages.create(
            model=ANTHROPIC_MODEL,
            system=system_prompt,
            tools=anthropic_tools,
            messages=messages,
        )
        logger.log("chat_response", {"raw": resp.model_dump() if hasattr(resp, 'model_dump') else str(resp)})

        # Collect tool uses
        tool_results_blocks: List[Dict[str, Any]] = []
        tool_use_ids: List[str] = []
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use":
                fn_name = block.name
                tool_use_id = block.id
                tool_use_ids.append(tool_use_id)
                args = block.input if hasattr(block, "input") else {}
                logger.log("llm_tool_call", {"name": fn_name, "arguments": args, "tool_call_id": tool_use_id})
                try:
                    mcp_result = mcp_call_tool(fn_name, args)
                    tool_results_blocks.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_use_id,
                            "content": json.dumps(mcp_result),
                        }
                    )
                except Exception as e:
                    tool_results_blocks.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_use_id,
                            "content": json.dumps({"isError": True, "message": str(e)}),
                        }
                    )
                    logger.log("llm_tool_error", {"name": fn_name, "error": str(e)}, level="ERROR")

        if tool_results_blocks:
            follow_resp = client.messages.create(
                model=ANTHROPIC_MODEL,
                system=system_prompt,
                tools=anthropic_tools,
                messages=[
                    {"role": "user", "content": user_msg},
                    {"role": "assistant", "content": resp.content},
                    {"role": "user", "content": tool_results_blocks},
                ],
            )
            logger.log("chat_followup_response", {"raw": follow_resp.model_dump() if hasattr(follow_resp, 'model_dump') else str(follow_resp)})
            # Print concatenated text parts
            final_text = "".join(
                getattr(p, "text", "") if getattr(p, "type", "") == "text" else ""
                for p in follow_resp.content
            )
            print(final_text)
        else:
            # No tool use; print text from first response
            text_out = "".join(
                getattr(p, "text", "") if getattr(p, "type", "") == "text" else ""
                for p in resp.content
            )
            print(text_out)


def run_chat():
    init_info = mcp_initialize()
    tools = mcp_list_tools()

    if PROVIDER == "openai":
        base_url = OPENAI_BASE_URL if OPENAI_BASE_URL else None
        if not OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY not set in environment")
        run_chat_openai_like(base_url or "https://api.openai.com/v1", OPENAI_API_KEY, OPENAI_MODEL, tools)
    elif PROVIDER == "anthropic":
        run_chat_anthropic(tools)
    else:
        # default to ollama
        run_chat_openai_like(OLLAMA_BASE_URL, os.getenv("OLLAMA_API_KEY", "ollama"), OLLAMA_MODEL, tools)


if __name__ == "__main__":
    try:
        logger.log("chat_start", {"provider": PROVIDER})
        run_chat()
    except Exception as e:
        logger.log("chat_crash", {"error": str(e)}, level="ERROR")
        print(f"Error: {e}")
        sys.exit(1)
