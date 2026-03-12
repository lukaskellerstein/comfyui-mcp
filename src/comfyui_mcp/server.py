"""
ComfyUI MCP Server – exposes ComfyUI workflows as MCP tools.

Install & run via uvx:
    uvx comfyui-mcp

Or configure in Claude Code ~/.claude/settings.json:
    {
        "mcpServers": {
            "comfyui": {
                "command": "uvx",
                "args": ["comfyui-mcp"],
                "env": {
                    "COMFYUI_URL": "https://your-runpod-url:8188"
                }
            }
        }
    }
"""

import base64
import importlib.resources
import json
import os
import random
import time
import uuid
import urllib.parse
import urllib.request
from pathlib import Path

import websocket
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("comfyui")

OUTPUT_DIR = Path.home() / ".comfyui-mcp" / "output"
DEFAULT_SERVER = "http://127.0.0.1:8188"
UA = "ComfyUI-MCP-Server/1.0"


# ── Workflow loading ─────────────────────────────────────────────────


def _load_bundled_workflow(name: str) -> dict:
    """Load a workflow JSON bundled with the package."""
    ref = importlib.resources.files("comfyui_mcp") / "workflows" / name
    return json.loads(ref.read_text(encoding="utf-8"))


# ── ComfyUI API helpers ─────────────────────────────────────────────


def _get_server_url() -> str:
    return os.environ.get("COMFYUI_URL", DEFAULT_SERVER).rstrip("/")


def _parse_server_url(server: str) -> tuple[str, str]:
    """Return (http_base, ws_base) from a server URL."""
    if server.startswith("https://"):
        return server, "wss://" + server[len("https://"):]
    elif server.startswith("http://"):
        return server, "ws://" + server[len("http://"):]
    else:
        return f"http://{server}", f"ws://{server}"


def _http_request(url: str, data: bytes | None = None, content_type: str | None = None) -> bytes:
    headers = {"User-Agent": UA}
    if content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req) as resp:
        return resp.read()


def _queue_prompt(http_base: str, prompt: dict, client_id: str, prompt_id: str) -> dict:
    payload = json.dumps(
        {"prompt": prompt, "client_id": client_id, "prompt_id": prompt_id}
    ).encode()
    return json.loads(_http_request(f"{http_base}/prompt", payload, "application/json"))


def _get_history(http_base: str, prompt_id: str) -> dict:
    return json.loads(_http_request(f"{http_base}/history/{prompt_id}"))


def _get_image(http_base: str, filename: str, subfolder: str, folder_type: str) -> bytes:
    params = urllib.parse.urlencode(
        {"filename": filename, "subfolder": subfolder, "type": folder_type}
    )
    return _http_request(f"{http_base}/view?{params}")


def _wait_for_completion(ws: websocket.WebSocket, prompt_id: str):
    """Block until ComfyUI signals the prompt has finished executing."""
    while True:
        msg = ws.recv()
        if isinstance(msg, str):
            data = json.loads(msg)
            if data["type"] == "executing":
                d = data["data"]
                if d["prompt_id"] == prompt_id and d["node"] is None:
                    return
            elif data["type"] == "execution_error":
                d = data["data"]
                raise RuntimeError(
                    f"Execution error on node {d.get('node_id')}: "
                    f"{d.get('exception_message', 'unknown error')}"
                )


def _find_positive_prompt_node(workflow: dict) -> str:
    """Find the CLIPTextEncode node used as the positive prompt."""
    for node_id, node in workflow.items():
        if node.get("class_type") != "CLIPTextEncode":
            continue
        meta_title = (node.get("_meta") or {}).get("title", "")
        if "negative" in meta_title.lower():
            continue
        if "text" in node.get("inputs", {}):
            return node_id
    raise ValueError("No CLIPTextEncode (positive prompt) node found in workflow")


def _run_workflow(workflow: dict) -> list[Path]:
    """Submit a workflow to ComfyUI, wait for completion, download images."""
    server = _get_server_url()
    http_base, ws_base = _parse_server_url(server)
    client_id = str(uuid.uuid4())
    prompt_id = str(uuid.uuid4())

    # Randomize seeds
    for node_id, node in workflow.items():
        if node.get("class_type") == "KSampler" and "seed" in node.get("inputs", {}):
            node["inputs"]["seed"] = random.randint(0, 2**63 - 1)

    # Connect websocket and submit
    ws = websocket.WebSocket()
    ws.connect(f"{ws_base}/ws?clientId={client_id}")

    try:
        _queue_prompt(http_base, workflow, client_id, prompt_id)
        _wait_for_completion(ws, prompt_id)
    finally:
        ws.close()

    # Download images
    history = _get_history(http_base, prompt_id)[prompt_id]
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    saved = []
    for node_id, node_output in history["outputs"].items():
        for image_info in node_output.get("images", []):
            image_data = _get_image(
                http_base,
                image_info["filename"],
                image_info["subfolder"],
                image_info["type"],
            )
            dest = OUTPUT_DIR / image_info["filename"]
            dest.write_bytes(image_data)
            saved.append(dest)

    return saved


# ── MCP Tools ────────────────────────────────────────────────────────


@mcp.tool()
def text_to_image(prompt: str, width: int = 1024, height: int = 1024) -> str:
    """Generate an image from a text prompt using ComfyUI.

    Args:
        prompt: Text description of the image to generate.
        width: Image width in pixels (default 1024).
        height: Image height in pixels (default 1024).

    Returns:
        A message with the path(s) to the saved image(s) and the image data
        encoded as base64 so Claude can see it.
    """
    workflow = _load_bundled_workflow("01_get_started_text_to_image.json")

    # Inject prompt text
    prompt_node_id = _find_positive_prompt_node(workflow)
    workflow[prompt_node_id]["inputs"]["text"] = prompt

    # Inject dimensions
    for node_id, node in workflow.items():
        if node.get("class_type") == "EmptySD3LatentImage":
            node["inputs"]["width"] = width
            node["inputs"]["height"] = height

    t_start = time.monotonic()
    saved = _run_workflow(workflow)
    elapsed = time.monotonic() - t_start

    if not saved:
        return "Workflow completed but no images were returned."

    # Return paths + base64-encoded image data
    parts = [f"Generated {len(saved)} image(s) in {elapsed:.1f}s:\n"]
    for path in saved:
        parts.append(f"- {path}")
        image_bytes = path.read_bytes()
        b64 = base64.b64encode(image_bytes).decode("ascii")
        suffix = path.suffix.lstrip(".").lower()
        mime = {
            "png": "image/png",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "webp": "image/webp",
        }.get(suffix, "image/png")
        parts.append(f"  ![image](data:{mime};base64,{b64})")

    return "\n".join(parts)


# ── Entry point ──────────────────────────────────────────────────────


def main():
    mcp.run()


if __name__ == "__main__":
    main()
