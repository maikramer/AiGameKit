"""GameAssets batch TUI dashboard — Textual app with real-time pipeline status."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Container, Vertical
from textual.reactive import reactive
from textual.widgets import DataTable, Footer, Header, Label, ProgressBar, Static
from textual.worker import get_current_worker

from gamedev_shared.progress import (
    STATUS_ERROR,
    STATUS_OK,
    STATUS_PROGRESS,
    STATUS_SKIPPED,
    parse_progress_line,
)

ICON_PENDING = "·"
ICON_RUNNING = "⠋"
ICON_OK = "✓"
ICON_SKIP = "◌"
ICON_FAIL = "✗"

COLOR_OK = "green"
COLOR_SKIP = "dim cyan"
COLOR_FAIL = "red"
COLOR_RUNNING = "cyan"
COLOR_PENDING = "dim"
COLOR_QUEUED = "dim yellow"

STAGE_COLORS: dict[str, str] = {
    "text2d": "bright_cyan",
    "texture2d": "bright_magenta",
    "skymap2d": "bright_blue",
    "text2sound": "yellow",
    "text3d": "bright_green",
    "paint3d": "green",
    "part3d": "bright_yellow",
    "rigging3d": "cyan",
    "animator3d": "magenta",
    "lod": "dim cyan",
    "collision": "dim magenta",
    "simplify": "dim green",
    "materialize": "bright_red",
}


class AssetStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    OK = "ok"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass
class AssetState:
    id: str
    pipeline_stages: list[str] = field(default_factory=list)
    current_stage: str = ""
    current_tool: str = ""
    current_phase: str = ""
    phase_percent: float = 0.0
    status: AssetStatus = AssetStatus.QUEUED
    timings: dict[str, float] = field(default_factory=dict)
    error: str | None = None
    faces: int | None = None
    _stage_idx: int = -1

    def _advance_stage(self, tool: str) -> None:
        if not self.pipeline_stages:
            return
        for i, stage in enumerate(self.pipeline_stages):
            if stage.lower() == tool.lower() or tool.lower() in stage.lower():
                if i != self._stage_idx:
                    self._stage_idx = i
                    self.current_stage = stage
                return
        if tool != self.current_stage:
            self.current_stage = tool

    def update_from_progress(self, data: dict[str, Any]) -> None:
        tool = data.get("tool", "")
        if tool:
            self._advance_stage(tool)

        if data["status"] == STATUS_PROGRESS:
            self.status = AssetStatus.RUNNING
            self.current_tool = data.get("tool", self.current_tool)
            self.current_phase = data.get("phase", "")
            self.phase_percent = data.get("percent", 0.0)
        elif data["status"] == STATUS_OK:
            self.status = AssetStatus.OK
            self.current_phase = ""
            self.phase_percent = 100.0
            if "seconds" in data:
                phase = data.get("phase", data.get("tool", self.current_stage))
                self.timings[phase] = data["seconds"]
            if "faces" in data:
                self.faces = data["faces"]
        elif data["status"] == STATUS_SKIPPED:
            self.status = AssetStatus.SKIPPED
        elif data["status"] == STATUS_ERROR:
            self.status = AssetStatus.FAILED
            self.error = data.get("error", "erro desconhecido")

    @property
    def icon(self) -> str:
        return {
            AssetStatus.QUEUED: ICON_PENDING,
            AssetStatus.RUNNING: ICON_RUNNING,
            AssetStatus.OK: ICON_OK,
            AssetStatus.SKIPPED: ICON_SKIP,
            AssetStatus.FAILED: ICON_FAIL,
        }[self.status]

    @property
    def phase_label(self) -> str:
        parts: list[str] = []
        if self.current_tool:
            parts.append(self.current_tool)
        if self.current_phase:
            parts.append(self.current_phase)
        label = " > ".join(parts) if parts else "processing..."
        if self.phase_percent > 0 and self.status == AssetStatus.RUNNING:
            label += f" {self.phase_percent:.0f}%"
        return label

    @property
    def stage_display(self) -> str:
        if self.status in (AssetStatus.QUEUED,):
            return "queued"
        if self.current_stage and self._stage_idx >= 0:
            total = len(self.pipeline_stages)
            return f"[{self._stage_idx + 1}/{total}] {self.current_stage}"
        return self.current_tool or "..."

    @property
    def color(self) -> str:
        return {
            AssetStatus.QUEUED: COLOR_PENDING,
            AssetStatus.RUNNING: COLOR_RUNNING,
            AssetStatus.OK: COLOR_OK,
            AssetStatus.SKIPPED: COLOR_SKIP,
            AssetStatus.FAILED: COLOR_FAIL,
        }[self.status]

    @property
    def stage_color(self) -> str:
        tool_lower = self.current_tool.lower()
        for key, color in STAGE_COLORS.items():
            if key in tool_lower:
                return color
        return COLOR_RUNNING


def _fmt_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{m:.0f}m{s:.0f}s"
    h, m = divmod(m, 60)
    return f"{h:.0f}h{m:.0f}m"


class StatsBar(Static):
    ok_count: reactive[int] = reactive(0)
    skip_count: reactive[int] = reactive(0)
    fail_count: reactive[int] = reactive(0)
    total: reactive[int] = reactive(0)
    elapsed: reactive[float] = reactive(0.0)

    def render(self) -> Text:
        t = Text()
        t.append(f"✓ {self.ok_count}", style=COLOR_OK)
        t.append(" │ ")
        t.append(f"◌ {self.skip_count}", style=COLOR_SKIP)
        t.append(" │ ")
        t.append(f"✗ {self.fail_count}", style=COLOR_FAIL)
        t.append(" │ ")
        t.append(f"Σ {self.total}", style="bold dim")
        t.append("   ")
        t.append(f"⏱ {_fmt_duration(self.elapsed)}", style="dim")
        return t


class PhaseLabel(Static):
    phase_name: reactive[str] = reactive("")
    phase_detail: reactive[str] = reactive("")

    def render(self) -> Text:
        t = Text()
        if self.phase_name:
            t.append(f"⏳ {self.phase_name}", style="bold cyan")
        if self.phase_detail:
            t.append(f"  {self.phase_detail}", style="dim")
        return t


class BatchDashboard(App):
    CSS = """
    #main-container {
        layout: vertical;
        padding: 0 1;
        height: 100%;
    }
    #info-panel {
        height: auto;
        margin-bottom: 1;
    }
    #progress-section {
        height: auto;
        margin-bottom: 1;
    }
    #stats-row {
        height: 1;
        margin-bottom: 1;
    }
    #asset-table {
        height: 1fr;
    }
    ProgressBar {
        margin-bottom: 0;
    }
    .phase-label {
        margin-bottom: 0;
        height: 1;
    }
    """

    TITLE = "GameAssets Batch"

    overall_total: reactive[int] = reactive(0)
    overall_done: reactive[int] = reactive(0)

    def __init__(
        self,
        game_title: str = "",
        asset_ids: list[str] | None = None,
        pipeline_desc: str = "",
        *,
        asset_pipelines: dict[str, list[str]] | None = None,
        batch_fn: Any = None,
    ) -> None:
        super().__init__()
        self.game_title = game_title
        self.pipeline_desc = pipeline_desc
        self.asset_pipelines = asset_pipelines or {}
        self.batch_fn = batch_fn
        self._start_time = time.monotonic()
        self._assets: dict[str, AssetState] = {}
        self._col_keys: list[Any] = []
        self._row_keys: dict[str, Any] = {}

        if asset_ids:
            for aid in asset_ids:
                stages = self.asset_pipelines.get(aid, [])
                self._assets[aid] = AssetState(id=aid, pipeline_stages=stages)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="main-container"):
            with Vertical(id="info-panel"):
                yield Label(
                    f"🎮 {self.game_title}" if self.game_title else "🎮 GameAssets Batch",
                    id="game-label",
                )
                if self.pipeline_desc:
                    yield Label(f"Pipeline: {self.pipeline_desc}", id="pipeline-label", classes="dim")
            with Vertical(id="progress-section"):
                yield Label("Overall", classes="phase-label")
                yield ProgressBar(total=100, id="overall-bar")
                yield PhaseLabel(id="phase-label")
                yield ProgressBar(total=100, id="phase-bar")
            yield StatsBar(id="stats-row")
            yield DataTable(id="asset-table")
        yield Footer()

    def on_mount(self) -> None:
        self._setup_table()
        self._setup_assets()
        self._update_timer = self.set_interval(1.0, self._tick_elapsed)
        if self.batch_fn:
            self._run_batch_worker()

    def _setup_table(self) -> None:
        table = self.query_one("#asset-table", DataTable)
        self._col_keys = [
            table.add_column("", width=1),
            table.add_column("Asset", width=20),
            table.add_column("Stage", width=16),
            table.add_column("Progress", width=30),
            table.add_column("Timing", width=16),
        ]
        table.cursor_type = "none"
        table.zebra_stripes = True

    def _setup_assets(self) -> None:
        table = self.query_one("#asset-table", DataTable)
        for aid, state in self._assets.items():
            stages = state.pipeline_stages
            stage_info = f"[{len(stages)} stage{'s' if len(stages) != 1 else ''}]" if stages else "queued"
            rk = table.add_row(
                Text(ICON_PENDING, style=COLOR_PENDING),
                Text(aid, style="bold"),
                Text(stage_info, style=COLOR_PENDING),
                Text("waiting...", style=COLOR_PENDING),
                Text(""),
                key=aid,
            )
            self._row_keys[aid] = rk
        self.overall_total = len(self._assets)

    def _tick_elapsed(self) -> None:
        stats = self.query_one("#stats-row", StatsBar)
        stats.elapsed = time.monotonic() - self._start_time

    @work(thread=True)
    def _run_batch_worker(self) -> None:
        worker = get_current_worker()
        if not worker.is_cancelled and self.batch_fn:
            self.batch_fn(self)

    def feed_line(self, raw_line: str) -> None:
        parsed = parse_progress_line(raw_line)
        if not parsed:
            return
        self.call_from_thread(self._update_from_parsed, parsed)

    def feed_event(self, asset_id: str, tool: str, status: str, **kwargs: Any) -> None:
        data: dict[str, Any] = {"id": asset_id, "tool": tool, "status": status}
        data.update(kwargs)
        self.call_from_thread(self._update_from_parsed, data)

    def _update_from_parsed(self, data: dict[str, Any]) -> None:
        asset_id = data.get("id", "")
        state = self._assets.get(asset_id)
        if not state:
            return

        prev_status = state.status
        prev_stage = state.current_stage
        state.update_from_progress(data)

        table = self.query_one("#asset-table", DataTable)
        row_key = self._row_keys.get(asset_id)
        if not row_key:
            return

        col_icon = self._col_keys[0]
        col_stage = self._col_keys[2]
        col_progress = self._col_keys[3]
        col_timing = self._col_keys[4]

        table.update_cell(row_key, col_icon, Text(state.icon, style=state.color))

        stage_style = state.stage_color if state.status == AssetStatus.RUNNING else "dim"
        table.update_cell(row_key, col_stage, Text(state.stage_display, style=stage_style))

        if state.status == AssetStatus.RUNNING:
            progress_text = Text()
            progress_text.append(state.phase_label, style=state.stage_color)
            table.update_cell(row_key, col_progress, progress_text)
        elif state.status == AssetStatus.OK:
            table.update_cell(row_key, col_progress, Text("✓ done", style=COLOR_OK))
        elif state.status == AssetStatus.SKIPPED:
            table.update_cell(row_key, col_progress, Text("skipped", style=COLOR_SKIP))
        elif state.status == AssetStatus.FAILED:
            err = state.error or "failed"
            if len(err) > 40:
                err = err[:37] + "..."
            table.update_cell(row_key, col_progress, Text(f"✗ {err}", style=COLOR_FAIL))

        if state.status in (AssetStatus.OK, AssetStatus.FAILED):
            parts = [f"{k}({_fmt_duration(v)})" for k, v in state.timings.items()]
            timing_str = " ".join(parts) if parts else "—"
            table.update_cell(row_key, col_timing, Text(timing_str, style="dim"))

        if prev_status != state.status and state.status in (
            AssetStatus.OK,
            AssetStatus.SKIPPED,
            AssetStatus.FAILED,
        ):
            self._increment_done()

        if (
            prev_stage
            and state.current_stage
            and prev_stage != state.current_stage
            and state.status == AssetStatus.RUNNING
        ):
            self.advance_phase()

        self._update_stats()

    def _increment_done(self) -> None:
        self.overall_done = sum(
            1 for s in self._assets.values() if s.status in (AssetStatus.OK, AssetStatus.SKIPPED, AssetStatus.FAILED)
        )
        bar = self.query_one("#overall-bar", ProgressBar)
        if self.overall_total > 0:
            bar.progress = int(100 * self.overall_done / self.overall_total)

    def _update_stats(self) -> None:
        stats = self.query_one("#stats-row", StatsBar)
        stats.ok_count = sum(1 for s in self._assets.values() if s.status == AssetStatus.OK)
        stats.skip_count = sum(1 for s in self._assets.values() if s.status == AssetStatus.SKIPPED)
        stats.fail_count = sum(1 for s in self._assets.values() if s.status == AssetStatus.FAILED)
        stats.total = self.overall_total

    def set_phase(self, name: str, total: int) -> None:
        self.call_from_thread(self._set_phase_ui, name, total)

    def _set_phase_ui(self, name: str, total: int) -> None:
        phase_label = self.query_one("#phase-label", PhaseLabel)
        phase_label.phase_name = name
        phase_bar = self.query_one("#phase-bar", ProgressBar)
        phase_bar.total = max(total, 1)
        phase_bar.progress = 0

    def advance_phase(self, n: int = 1) -> None:
        self.call_from_thread(self._advance_phase_ui, n)

    def _advance_phase_ui(self, n: int = 1) -> None:
        phase_bar = self.query_one("#phase-bar", ProgressBar)
        phase_bar.advance(n)
        completed = int(phase_bar.progress * phase_bar.total / 100) if phase_bar.total else 0
        if phase_bar.total > 0:
            pct = int(100 * min(completed, phase_bar.total) / phase_bar.total)
            phase_label = self.query_one("#phase-label", PhaseLabel)
            phase_label.phase_detail = f"{min(completed, phase_bar.total)}/{phase_bar.total}  ({pct}%)"

    def finish(self) -> None:
        self.call_from_thread(self._finish_ui)

    def _finish_ui(self) -> None:
        stats = self.query_one("#stats-row", StatsBar)
        stats.elapsed = time.monotonic() - self._start_time
        phase_label = self.query_one("#phase-label", PhaseLabel)
        phase_label.phase_name = "Batch concluído"
        phase_label.phase_detail = ""
