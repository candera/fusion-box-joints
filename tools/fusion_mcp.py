#!/usr/bin/env python3
"""Run a Python file in Autodesk Fusion through Fusion's local MCP server.

usage: fusion_mcp.py script.py [--read-only]
       fusion_mcp.py --tool TOOL_NAME 'JSON_ARGUMENTS' [--save-images DIR]
The script must define run(_context). Printed output is returned.
"""
import base64
import os
import json
import sys
import urllib.request

URL = "http://127.0.0.1:27182/mcp"


def call(payload, session=None, timeout=600):
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if session:
        headers["MCP-Session-Id"] = session
    request = urllib.request.Request(URL, json.dumps(payload).encode(), headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode()
        return response.headers.get("MCP-Session-Id"), (json.loads(body) if body.strip() else None)


def main():
    session, _ = call({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "fusion-box-joints", "version": "0"}}})
    call({"jsonrpc": "2.0", "method": "notifications/initialized"}, session)
    if sys.argv[1] == "--tool":
        name, arguments = sys.argv[2], json.loads(sys.argv[3])
    else:
        obj = {"script": open(sys.argv[1], encoding="utf-8").read()}
        if "--read-only" in sys.argv:
            obj["readOnly"] = True
        name, arguments = "fusion_mcp_execute", {"featureType": "script", "object": obj}
    _, result = call({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
        "name": name, "arguments": arguments}}, session)
    if "error" in result:
        print(json.dumps(result["error"], indent=1))
        sys.exit(1)
    res = result["result"]
    image_dir = sys.argv[sys.argv.index("--save-images") + 1] if "--save-images" in sys.argv else "."
    for index, item in enumerate(res.get("content", [])):
        if item.get("type") == "image":
            path = os.path.join(image_dir, f"fusion-{os.getpid()}-{index}.png")
            with open(path, "wb") as stream:
                stream.write(base64.b64decode(item["data"]))
            print("image:", path)
        else:
            print(item.get("text", item))
    sys.exit(1 if res.get("isError") else 0)


if __name__ == "__main__":
    main()
