from flask import Flask, request, jsonify
import os
from jsonl_logger import get_logger

app = Flask(__name__)
BASE_DIR = "./mcp_files"
os.makedirs(BASE_DIR, exist_ok=True)

logger = get_logger("mcp-server")

TOOLS = {
    "list_files": {
        "description": "List all files in the directory",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
        "outputSchema": {"type": "object", "properties": {
            "files": {"type": "array", "items": {"type": "string"}}
        }, "required": ["files"]}
    },
    "read_file": {
        "description": "Read the contents of a file",
        "inputSchema": {"type": "object", "properties": {
            "filename": {"type": "string"}
        }, "required": ["filename"]},
        "outputSchema": {"type": "object", "properties": {
            "content": {"type": "string"}
        }, "required": ["content"]}
    },
    "write_file": {
        "description": "Write text to a file (creates or overwrites)",
        "inputSchema": {"type": "object", "properties": {
            "filename": {"type": "string"},
            "text": {"type": "string"}
        }, "required": ["filename", "text"]},
        "outputSchema": {"type": "object", "properties": {
            "message": {"type": "string"}
        }, "required": ["message"]}
    },
    "delete_file": {
        "description": "Delete a file by name",
        "inputSchema": {"type": "object", "properties": {
            "filename": {"type": "string"}
        }, "required": ["filename"]},
        "outputSchema": {"type": "object", "properties": {
            "deleted": {"type": "boolean"}
        }, "required": ["deleted"]}
    },
    "search_file": {
        "description": "Search for a keyword in a file and count occurrences",
        "inputSchema": {"type": "object", "properties": {
            "filename": {"type": "string"},
            "keyword": {"type": "string"}
        }, "required": ["filename", "keyword"]},
        "outputSchema": {"type": "object", "properties": {
            "count": {"type": "integer"}
        }, "required": ["count"]}
    }
}

def list_files():
    files = os.listdir(BASE_DIR)
    return {"files": files}

def read_file(filename: str):
    filepath = os.path.join(BASE_DIR, filename)
    if not os.path.isfile(filepath):
        raise FileNotFoundError(f"{filename} not found")
    with open(filepath, 'r') as f:
        content = f.read()
    return {"content": content}

def write_file(filename: str, text: str):
    filepath = os.path.join(BASE_DIR, filename)
    with open(filepath, 'w') as f:
        f.write(text)
    return {"message": f"Wrote {len(text)} bytes to {filename}"}

def delete_file(filename: str):
    filepath = os.path.join(BASE_DIR, filename)
    if os.path.isfile(filepath):
        os.remove(filepath)
        deleted = True
    else:
        deleted = False
    return {"deleted": deleted}

def search_file(filename: str, keyword: str):
    filepath = os.path.join(BASE_DIR, filename)
    if not os.path.isfile(filepath):
        raise FileNotFoundError(f"{filename} not found")
    count = 0
    with open(filepath, 'r') as f:
        for line in f:
            if keyword in line:
                count += 1
    return {"count": count}

TOOL_FUNCTIONS = {
    "list_files": lambda params: list_files(),
    "read_file":  lambda params: read_file(params.get("filename")),
    "write_file": lambda params: write_file(params.get("filename"), params.get("text")),
    "delete_file": lambda params: delete_file(params.get("filename")),
    "search_file": lambda params: search_file(params.get("filename"), params.get("keyword"))
}

@app.route("/rpc", methods=["POST"])
def rpc():
    req = request.get_json(force=True)
    if req is None:
        logger.log("rpc_parse_error", {"remote": request.remote_addr})
        return jsonify({"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}})
    jsonrpc = req.get("jsonrpc", "2.0")
    method = req.get("method")
    req_id = req.get("id")
    logger.log("rpc_request", {"method": method, "id": req_id, "payload": req}, remote=request.remote_addr)
    if method == "initialize":
        response = {
            "protocolVersion": "2025-06-18",
            "capabilities": {
                "tools": {"listChanged": False},
                "resources": {"subscribe": False, "listChanged": False},
                "prompts": {"listChanged": False}
            },
            "serverInfo": {"name": "FileServer", "version": "1.0"}
        }
        logger.log("rpc_response", {"method": method, "id": req_id, "result": response})
        return jsonify({"jsonrpc": jsonrpc, "id": req_id, "result": response})
    if method == "tools/list":
        tools_list = []
        for name, meta in TOOLS.items():
            tools_list.append({
                "name": name,
                "description": meta["description"],
                "inputSchema": meta["inputSchema"],
                "outputSchema": meta["outputSchema"]
            })
        logger.log("rpc_response", {"method": method, "id": req_id, "count": len(tools_list)})
        return jsonify({"jsonrpc": jsonrpc, "id": req_id, "result": {"tools": tools_list}})
    if method == "tools/call":
        params = req.get("params", {})
        tool_name = params.get("name")
        args = params.get("arguments", {})
        logger.log("tool_call_request", {"name": tool_name, "arguments": args, "id": req_id})
        if tool_name in TOOL_FUNCTIONS:
            try:
                result_data = TOOL_FUNCTIONS[tool_name](args)
                content_item = {"type": "text", "text": str(result_data)}
                response = {
                    "content": [content_item],
                    "structuredContent": result_data,
                    "isError": False
                }
                logger.log("tool_call_success", {"name": tool_name, "result": result_data, "id": req_id})
                return jsonify({"jsonrpc": jsonrpc, "id": req_id, "result": response})
            except Exception as e:
                error_msg = str(e)
                logger.log("tool_call_error", {"name": tool_name, "error": error_msg, "id": req_id}, level="ERROR")
                return jsonify({"jsonrpc": jsonrpc, "id": req_id,
                                "error": {"code": 1, "message": error_msg}})
        else:
            logger.log("tool_not_found", {"name": tool_name, "id": req_id}, level="ERROR")
            return jsonify({"jsonrpc": jsonrpc, "id": req_id,
                            "error": {"code": 404, "message": f"Tool '{tool_name}' not found"}})
    logger.log("rpc_method_not_found", {"method": method, "id": req_id}, level="ERROR")
    return jsonify({"jsonrpc": jsonrpc, "id": req_id,
                    "error": {"code": -32601, "message": "Method not found"}})

@app.route("/events")
def events():
    logger.log("sse_heartbeat", {"remote": request.remote_addr})
    return app.response_class(
        "data: {\"event\": \"heartbeat\"}\n\n", mimetype="text/event-stream"
    )

if __name__ == "__main__":
    logger.log("server_start", {"base_dir": BASE_DIR})
    app.run(host="127.0.0.1", port=5000)
