"""Unified progress protocol for GameDev CLI tools.

Tools emit JSONL to stdout for machine-readable progress updates.
GameAssets (or any orchestrator) parses these lines to drive dashboards.

Line schema (all fields optional except ``id``, ``tool``, ``status``):

    id       str   Item identifier (matches manifest row id)
    tool     str   Tool name: "text2d", "text3d", "paint3d", "rigging3d",
                   "animator3d", "text2sound"
    status   str   "progress" | "ok" | "skipped" | "error"
    phase    str   Sub-step name (e.g. "loading_model", "inference",
                   "mesh_repair", "export")
    percent  float 0-100 within current phase (status="progress" only)
    output   str   Output file path (status="ok"|"skipped")
    seconds  float Wall-clock seconds for this item/phase
    faces    int   Face count (text3d)
    error    str   Error message (status="error")
    meta     dict  Extra tool-specific data

Usage in sub-tools::

    from gamedev_shared.progress import emit_progress, emit_result

    emit_progress("hero", "text3d", phase="inference", percent=45)
    emit_result("hero", "text3d", "ok", phase="shape", output="shape.glb", seconds=12.3)

Usage in orchestrator::

    from gamedev_shared.progress import parse_progress_line

    for line in subprocess.stdout:
        parsed = parse_progress_line(line)
        if parsed:
            dashboard.update(parsed)
"""

from __future__ import annotations

import json
import sys
from typing import Any

STATUS_PROGRESS = "progress"
STATUS_OK = "ok"
STATUS_SKIPPED = "skipped"
STATUS_ERROR = "error"

TOOL_TEXT2D = "text2d"
TOOL_TEXT3D = "text3d"
TOOL_PAINT3D = "paint3d"
TOOL_RIGGING3D = "rigging3d"
TOOL_ANIMATOR3D = "animator3d"
TOOL_TEXT2SOUND = "text2sound"
TOOL_GAMEASSETS = "gameassets"

PHASE_LOADING_MODEL = "loading_model"
PHASE_INFERENCE = "inference"
PHASE_MESH_REPAIR = "mesh_repair"
PHASE_EXPORT = "export"
PHAGE_MARCHING_CUBES = "marching_cubes"
PHASE_REMESH = "remesh"
PHASE_SKELETON = "skeleton"
PHASE_SKIN = "skin"
PHASE_MERGE = "merge"
PHASE_MULTIVIEW_RENDER = "multiview_render"
PHASE_BAKE = "bake"
PHASE_SAVE = "save"


def emit_progress(
    id: str,
    tool: str,
    *,
    phase: str | None = None,
    percent: float | None = None,
    **meta: Any,
) -> None:
    """Emit a progress line to stdout.

    Args:
        id: Item identifier.
        tool: Tool name (use TOOL_* constants).
        phase: Current sub-step name.
        percent: 0-100 progress within current phase.
        **meta: Extra fields merged into the JSON line.
    """
    line: dict[str, Any] = {
        "id": id,
        "tool": tool,
        "status": STATUS_PROGRESS,
    }
    if phase is not None:
        line["phase"] = phase
    if percent is not None:
        line["percent"] = round(percent, 1)
    if meta:
        line["meta"] = meta
    _write_line(line)


def emit_result(
    id: str,
    tool: str,
    status: str,
    *,
    phase: str | None = None,
    output: str | None = None,
    seconds: float | None = None,
    faces: int | None = None,
    error: str | None = None,
    **meta: Any,
) -> None:
    """Emit a result line to stdout.

    Args:
        id: Item identifier.
        tool: Tool name (use TOOL_* constants).
        status: One of STATUS_OK, STATUS_SKIPPED, STATUS_ERROR.
        phase: Which phase produced this result.
        output: Output file path.
        seconds: Wall-clock time for this item.
        faces: Face count (text3d).
        error: Error message (when status is error).
        **meta: Extra fields merged into the JSON line.
    """
    line: dict[str, Any] = {
        "id": id,
        "tool": tool,
        "status": status,
    }
    if phase is not None:
        line["phase"] = phase
    if output is not None:
        line["output"] = output
    if seconds is not None:
        line["seconds"] = round(seconds, 2)
    if faces is not None:
        line["faces"] = faces
    if error is not None:
        line["error"] = error
    if meta:
        line["meta"] = meta
    _write_line(line)


def parse_progress_line(line: str) -> dict[str, Any] | None:
    """Parse a JSONL progress line.

    Args:
        line: Raw line from sub-tool stdout.

    Returns:
        Parsed dict with at least ``id``, ``tool``, ``status``,
        or ``None`` if the line is not valid JSON or is missing required fields.
    """
    stripped = line.strip()
    if not stripped:
        return None
    try:
        data = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if "id" not in data or "status" not in data:
        return None
    data.setdefault("tool", "unknown")
    return data


def _write_line(data: dict[str, Any]) -> None:
    """Write a JSON line to stdout and flush."""
    sys.stdout.write(json.dumps(data, ensure_ascii=False) + "\n")
    sys.stdout.flush()
