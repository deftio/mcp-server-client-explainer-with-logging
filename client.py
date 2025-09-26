import requests
import json
from jsonl_logger import get_logger

logger = get_logger("mcp-client-simple")

MCP_SERVER_URL = "http://127.0.0.1:5000/rpc"

init_payload = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "ExampleClient", "version": "0.1"}
    }
}
logger.log("client_request", {"method": "initialize", "payload": init_payload})
print(">> Sending initialize")
response = requests.post(MCP_SERVER_URL, json=init_payload).json()
logger.log("client_response", {"method": "initialize", "response": response})
print("<< Received init response:", json.dumps(response, indent=2), "\n")

# List tools
tools_list_payload = {
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tools/list",
    "params": None
}
logger.log("client_request", {"method": "tools/list", "payload": tools_list_payload})
print(">> Requesting tools list")
response = requests.post(MCP_SERVER_URL, json=tools_list_payload).json()
logger.log("client_response", {"method": "tools/list", "response": response})
tools = response.get("result", {}).get("tools", [])
print("<< Available tools:", json.dumps(tools, indent=2), "\n")

# Demonstrate calls
tool_names = {t['name'] for t in tools}
if "write_file" in tool_names and "search_file" in tool_names:
    write_payload = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "write_file",
            "arguments": {"filename": "demo.txt", "text": "Hello\nThis is a TODO line.\nBye"}
        }
    }
    logger.log("client_request", {"method": "tools/call", "payload": write_payload})
    print(">> Calling write_file to create 'demo.txt'")
    response = requests.post(MCP_SERVER_URL, json=write_payload).json()
    logger.log("client_response", {"method": "tools/call", "response": response})
    print("<< write_file result:", json.dumps(response, indent=2), "\n")

    search_payload = {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {
            "name": "search_file",
            "arguments": {"filename": "demo.txt", "keyword": "TODO"}
        }
    }
    logger.log("client_request", {"method": "tools/call", "payload": search_payload})
    print(">> Calling search_file to find 'TODO' in 'demo.txt'")
    response = requests.post(MCP_SERVER_URL, json=search_payload).json()
    logger.log("client_response", {"method": "tools/call", "response": response})
    print("<< search_file result:", json.dumps(response, indent=2), "\n")
else:
    logger.log("client_warning", {"message": "Required tools not available"}, level="WARNING")
    print("Required tools not available.")
