import json
import sys
import time
from typing import Any, Dict, List

import requests
from openai import OpenAI
from jsonl_logger import get_logger

MCP_SERVER_URL = "http://127.0.0.1:5000/rpc"
logger = get_logger("mcp-client-chat")


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
    logger.log("tools_mapped_for_llm", {"count": len(formatted)})
    return formatted


def run_chat():
    init_info = mcp_initialize()
    tools = mcp_list_tools()

    client = OpenAI(base_url="http://127.0.0.1:11434/v1", api_key="ollama")
    model_name = "granite3.3"

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

        logger.log("chat_request", {"messages": messages})
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


if __name__ == "__main__":
    try:
        logger.log("chat_start", {})
        run_chat()
    except Exception as e:
        logger.log("chat_crash", {"error": str(e)}, level="ERROR")
        print(f"Error: {e}")
        sys.exit(1)
