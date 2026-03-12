# comfyui-mcp

MCP server for ComfyUI – trigger image generation workflows from Claude Code.

## Install

### Claude Code (via uvx from GitHub)

Add to `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "comfyui": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/lukaskellerstein/comfyui-mcp", "comfyui-mcp"],
      "env": {
        "COMFYUI_URL": "https://your-runpod-url:8188"
      }
    }
  }
}
```

### Claude Code (via uvx from PyPI)

Once published to PyPI:

```json
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
```

## Configuration

Set the `COMFYUI_URL` environment variable to point at your ComfyUI instance (e.g. RunPod).

## Tools

### `text_to_image`

Generate an image from a text prompt.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `prompt`  | str  | required | Text description of the image |
| `width`   | int  | 1024 | Image width in pixels |
| `height`  | int  | 1024 | Image height in pixels |

Generated images are saved to `~/.comfyui-mcp/output/`.
