import json
import os
import socket
from typing import Any


def request(payload: str) -> Any:
    """Send a request to the Niri IPC socket and return the unwrapped result.

    The Niri IPC protocol uses a simple request/response pattern over a UNIX domain socket:
    - Connect to the socket at $NIRI_SOCKET
    - Send a JSON-quoted payload followed by a newline
    - Read one line of JSON response
    - Response is {"Ok": <data>} on success or {"Err": <msg>} on failure

    Args:
        payload: The request type (e.g. "Outputs", "Workspaces", "Windows").

    Returns:
        The unwrapped data from the "Ok" response.

    Raises:
        RuntimeError: If NIRI_SOCKET is not set or the request fails.
    """
    socket_path = os.getenv("NIRI_SOCKET")
    if not socket_path:
        raise RuntimeError("NIRI_SOCKET environment variable is not set")

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.connect(socket_path)
        # Wire format: JSON-quoted string + newline (e.g. "Outputs"\n)
        sock.sendall(f'"{payload}"\n'.encode())

        # Read until newline (one response line)
        resp = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            resp += chunk
            if b"\n" in resp:
                break

    resp_str = resp.decode().strip()
    if not resp_str:
        raise RuntimeError(f"Niri IPC returned empty response for {payload}")

    data = json.loads(resp_str)

    if "Err" in data:
        raise RuntimeError(f"Niri IPC error for {payload}: {data['Err']}")

    if "Ok" not in data:
        raise RuntimeError(f"Niri IPC unexpected response for {payload}: {resp_str}")

    return data["Ok"]


def get_outputs() -> dict[str, Any]:
    """Get all Niri outputs, normalised to the contract consumers expect.

    Niri's IPC returns ``modes`` (list of modes), ``current_mode`` (index into
    it, ``None`` when disabled) and ``logical`` (compositor-space rectangle).
    Consumers (record, wallpaper) expect a single active ``mode`` object and a
    ``location`` point, so both are injected per output while every original
    field (including ``logical``) is preserved.

    Returns:
        Dict keyed by output name (e.g., "eDP-1"), each value containing at
        least:
        {
            "mode": {"width": 1920, "height": 1080, "refresh_rate": 60000},
            "location": {"x": 0, "y": 0},
            "logical": {"x": 0, "y": 0, "width": 1920, "height": 1080, ...},
            ...
        }
        where ``refresh_rate`` is in millihertz.
    """
    outputs = request("Outputs")["Outputs"]

    for output in outputs.values():
        modes = output.get("modes") or []
        current = output.get("current_mode")
        if isinstance(current, int) and 0 <= current < len(modes):
            mode = modes[current]
        elif modes:
            mode = next((m for m in modes if m.get("is_preferred")), modes[0])
        else:
            mode = {}

        logical = output.get("logical") or {}
        output["mode"] = {
            "width": mode.get("width", 0),
            "height": mode.get("height", 0),
            "refresh_rate": mode.get("refresh_rate", 0),
        }
        output["location"] = {"x": logical.get("x", 0), "y": logical.get("y", 0)}

    return outputs


def get_focused_output_name() -> str | None:
    """Get the name of the focused output.

    Determines this by finding the workspace with is_focused=True
    and returning its associated output name.

    Returns:
        The output name (e.g. "eDP-1") or None if no focused workspace is found.
    """
    workspaces = request("Workspaces")["Workspaces"]
    for ws in workspaces:
        if ws.get("is_focused"):
            return ws.get("output")
    return None
