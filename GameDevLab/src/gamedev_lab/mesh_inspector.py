"""Mesh quality inspector — topology, geometry, artifacts, and visual QA."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import trimesh


@dataclass
class TopologyReport:
    vertices: int = 0
    faces: int = 0
    edges: int = 0
    watertight: bool = False
    euler_number: int = 0
    boundary_edges: int = 0
    real_holes: int = 0
    uv_seam_edges: int = 0
    connected_components: int = 1
    degenerate_faces: int = 0
    duplicate_vertices: int = 0


@dataclass
class GeometryReport:
    bounds_min: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    bounds_max: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    extents: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    centroid: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    volume: float | None = None
    area: float = 0.0
    convex_hull_volume: float | None = None
    bbox_volume: float = 0.0
    volume_efficiency: float = 0.0
    flatness_ratio: float = 0.0
    thickness_ratio: float = 0.0
    aspect_ratio: float = 0.0


@dataclass
class ArtifactReport:
    backing_plates: list[dict[str, Any]] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    passed: bool = True


@dataclass
class QualityScore:
    overall: float = 0.0
    topology_score: float = 0.0
    geometry_score: float = 0.0
    artifact_score: float = 0.0
    grade: str = "F"
    summary: str = ""


@dataclass
class MeshQAReport:
    path: str = ""
    topology: TopologyReport = field(default_factory=TopologyReport)
    geometry: GeometryReport = field(default_factory=GeometryReport)
    artifacts: ArtifactReport = field(default_factory=ArtifactReport)
    score: QualityScore = field(default_factory=QualityScore)
    reference_comparison: list[dict[str, Any]] = field(default_factory=list)
    view_images: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def save_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def passed(self) -> bool:
        return self.artifacts.passed and self.score.overall >= 0.4


class MeshInspector:
    """Analyze a mesh GLB/OBJ for quality, topology, geometry, and artifacts."""

    def __init__(
        self,
        path: str | Path,
        *,
        plate_coverage_threshold: float = 0.7,
        flatness_threshold: float = 0.12,
        volume_efficiency_threshold: float = 0.15,
        thickness_ratio_threshold: float = 0.10,
        band_frac: float = 0.10,
        normal_align: float = 0.7,
        degenerate_area_eps: float = 1e-8,
    ) -> None:
        self.path = Path(path)
        self._plate_thr = plate_coverage_threshold
        self._flat_thr = flatness_threshold
        self._vol_thr = volume_efficiency_threshold
        self._thick_thr = thickness_ratio_threshold
        self._band_frac = band_frac
        self._normal_align = normal_align
        self._degen_eps = degenerate_area_eps

    def _load_mesh(self) -> trimesh.Trimesh:
        mesh = trimesh.load(str(self.path), force="mesh")
        if not isinstance(mesh, trimesh.Trimesh):
            raise ValueError(f"Expected a single Trimesh, got {type(mesh).__name__}")
        return mesh

    def inspect(self) -> MeshQAReport:
        mesh = self._load_mesh()
        topo = self._inspect_topology(mesh)
        geom = self._inspect_geometry(mesh)
        arti = self._inspect_artifacts(mesh)
        score = self._compute_score(topo, geom, arti)
        return MeshQAReport(
            path=str(self.path.resolve()),
            topology=topo,
            geometry=geom,
            artifacts=arti,
            score=score,
        )

    def inspect_with_views(
        self,
        output_dir: Path,
        *,
        animator3d_bin: str | None = None,
        views: str = "front,three_quarter,right,back,top,low_front",
        resolution: int = 512,
        reference_image: Path | None = None,
        engine: str = "workbench",
    ) -> MeshQAReport:
        report = self.inspect()
        output_dir.mkdir(parents=True, exist_ok=True)

        views_dir = output_dir / "views"
        rendered = self._render_views(
            views_dir,
            animator3d_bin=animator3d_bin,
            views=views,
            resolution=resolution,
            engine=engine,
        )
        report.view_images = rendered
        if reference_image is not None and reference_image.is_file() and rendered:
            report.reference_comparison = self._compare_with_reference(reference_image, rendered, output_dir)
        report.save_json(output_dir / "qa_report.json")
        return report

    # ------------------------------------------------------------------
    # Topology
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_boundary_edges(mesh: trimesh.Trimesh) -> tuple[int, int, dict[tuple[int, ...], int]]:
        """Classify boundary edges into real holes vs UV seam edges.

        A UV seam edge has both vertices duplicated at the same rounded position
        (i.e. multiple vertex indices share the same spatial location due to UV
        splits). A real hole has at least one vertex that is *not* duplicated —
        the edge truly lies on an open boundary of the mesh surface.

        Returns:
            (real_holes, uv_seam_edges, edge_count) where edge_count maps each
            sorted edge tuple to the number of adjacent faces.
        """
        edge_count: dict[tuple[int, ...], int] = {}
        for face in mesh.faces:
            for i in range(3):
                e = tuple(sorted((face[i], face[(i + 1) % 3])))
                edge_count[e] = edge_count.get(e, 0) + 1

        boundary = [e for e, c in edge_count.items() if c == 1]
        if not boundary:
            return 0, 0, edge_count

        rounded = np.round(mesh.vertices, decimals=6)
        pos_to_indices: dict[tuple[float, ...], list[int]] = {}
        for idx in range(len(mesh.vertices)):
            key = tuple(rounded[idx].tolist())
            pos_to_indices.setdefault(key, []).append(idx)

        real_holes = 0
        uv_seam_edges = 0
        for va, vb in boundary:
            ka = tuple(rounded[va].tolist())
            kb = tuple(rounded[vb].tolist())
            a_dup = len(pos_to_indices.get(ka, [])) > 1
            b_dup = len(pos_to_indices.get(kb, [])) > 1
            if a_dup and b_dup:
                uv_seam_edges += 1
            else:
                real_holes += 1

        return real_holes, uv_seam_edges, edge_count

    def _inspect_topology(self, mesh: trimesh.Trimesh) -> TopologyReport:
        r = TopologyReport()
        r.vertices = len(mesh.vertices)
        r.faces = len(mesh.faces)
        r.edges = len(mesh.edges_unique)
        r.watertight = bool(mesh.is_watertight)
        try:
            r.euler_number = int(mesh.euler_number)
        except Exception:
            r.euler_number = 0

        real_holes, uv_seam_edges, edge_count = self._classify_boundary_edges(mesh)
        r.boundary_edges = sum(1 for c in edge_count.values() if c == 1)
        r.real_holes = real_holes
        r.uv_seam_edges = uv_seam_edges

        try:
            components = mesh.split(only_watertight=False)
            r.connected_components = len(components) if components else 1
        except Exception:
            r.connected_components = 1

        areas = mesh.area_faces
        r.degenerate_faces = int(np.sum(areas < self._degen_eps))

        n_before = len(mesh.vertices)
        mesh.merge_vertices()
        r.duplicate_vertices = max(0, n_before - len(mesh.vertices))

        return r

    # ------------------------------------------------------------------
    # Geometry
    # ------------------------------------------------------------------

    def _inspect_geometry(self, mesh: trimesh.Trimesh) -> GeometryReport:
        r = GeometryReport()
        if len(mesh.faces) == 0:
            return r

        bounds = mesh.bounds
        r.bounds_min = [float(x) for x in bounds[0]]
        r.bounds_max = [float(x) for x in bounds[1]]
        r.extents = [float(x) for x in mesh.extents]
        r.centroid = [float(x) for x in mesh.centroid]
        r.area = float(mesh.area)

        e_sorted = sorted(r.extents)
        r.bbox_volume = float(e_sorted[0] * e_sorted[1] * e_sorted[2])
        r.flatness_ratio = round(e_sorted[0] / e_sorted[2], 4) if e_sorted[2] > 1e-9 else 0
        r.aspect_ratio = round(e_sorted[2] / e_sorted[0], 2) if e_sorted[0] > 1e-9 else 0

        if r.bbox_volume > 1e-12:
            try:
                r.convex_hull_volume = float(mesh.convex_hull.volume)
            except Exception:
                r.convex_hull_volume = None
            r.volume_efficiency = round((r.convex_hull_volume / r.bbox_volume) if r.convex_hull_volume else 0, 4)

        if mesh.is_watertight:
            try:
                r.volume = float(mesh.volume)
            except Exception:
                r.volume = None

        min_ax = int(np.argmin(mesh.extents))
        coords = mesh.vertices[:, min_ax]
        half_range = (float(coords.max()) - float(coords.min())) / 2
        if half_range > 1e-9:
            median_c = float(np.median(coords))
            dists = np.abs(coords - median_c)
            r.thickness_ratio = round(float(np.median(dists) / half_range), 4)

        return r

    # ------------------------------------------------------------------
    # Artifacts (from Text3D mesh_quality_check logic)
    # ------------------------------------------------------------------

    def _inspect_artifacts(self, mesh: trimesh.Trimesh) -> ArtifactReport:
        r = ArtifactReport()
        if len(mesh.faces) == 0:
            r.passed = False
            r.issues.append("empty mesh")
            return r

        e_sorted = sorted(float(x) for x in mesh.extents)
        flatness = e_sorted[0] / e_sorted[2] if e_sorted[2] > 1e-9 else 0

        if flatness < self._flat_thr:
            r.passed = False
            r.issues.append(f"flat cutout bbox (ratio={flatness:.3f})")

        bbox_vol = float(e_sorted[0] * e_sorted[1] * e_sorted[2])
        if bbox_vol > 1e-12:
            try:
                ch_vol = float(mesh.convex_hull.volume)
            except Exception:
                ch_vol = 0.0
            vol_eff = ch_vol / bbox_vol
        else:
            vol_eff = 0.0

        if vol_eff < self._vol_thr:
            r.passed = False
            r.issues.append(f"flat cutout volume (efficiency={vol_eff:.3f})")

        min_ax = int(np.argmin(mesh.extents))
        coords = mesh.vertices[:, min_ax]
        half_range = (float(coords.max()) - float(coords.min())) / 2
        if half_range > 1e-9:
            median_c = float(np.median(coords))
            dists = np.abs(coords - median_c)
            thickness = float(np.median(dists) / half_range)
        else:
            thickness = 0.0

        if thickness < self._thick_thr:
            r.passed = False
            r.issues.append(f"flat-backed (thickness={thickness:.3f})")

        normals = mesh.face_normals
        areas = mesh.area_faces
        centers = mesh.triangles_center
        bounds = mesh.bounds
        extents = mesh.extents

        ax_names = ["X", "Y", "Z"]
        for ax in range(3):
            lo = float(bounds[0, ax])
            hi = float(bounds[1, ax])
            h = hi - lo
            if h < 1e-8:
                continue

            other = [i for i in range(3) if i != ax]
            cross_area = float(extents[other[0]] * extents[other[1]])
            if cross_area < 1e-12:
                continue

            for side_label, band_lo, band_hi in [
                ("min", lo, lo + self._band_frac * h),
                ("max", hi - self._band_frac * h, hi),
            ]:
                in_band = (centers[:, ax] >= band_lo) & (centers[:, ax] <= band_hi)
                aligned = np.abs(normals[:, ax]) >= self._normal_align
                flat_in_band = in_band & aligned
                flat_area = float(areas[flat_in_band].sum())
                coverage = flat_area / cross_area

                if coverage > self._plate_thr:
                    r.passed = False
                    plate_info = {
                        "axis": ax_names[ax],
                        "side": side_label,
                        "coverage": round(coverage, 3),
                    }
                    r.backing_plates.append(plate_info)
                    r.issues.append(f"backing plate {ax_names[ax]}-{side_label} (coverage={coverage:.2f})")

        return r

    # ------------------------------------------------------------------
    # Score
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_score(topo: TopologyReport, geom: GeometryReport, arti: ArtifactReport) -> QualityScore:
        s = QualityScore()

        topo_pts = 1.0
        if topo.watertight:
            topo_pts += 0.3
        if topo.degenerate_faces == 0:
            topo_pts += 0.2
        if topo.connected_components == 1:
            topo_pts += 0.2
        if topo.duplicate_vertices == 0:
            topo_pts += 0.1
        s.topology_score = round(min(topo_pts / 1.8, 1.0), 3)

        geom_pts = 1.0
        if geom.volume_efficiency >= 0.4:
            geom_pts += 0.3
        elif geom.volume_efficiency >= 0.2:
            geom_pts += 0.15
        if geom.flatness_ratio >= 0.2:
            geom_pts += 0.2
        if geom.thickness_ratio >= 0.15:
            geom_pts += 0.2
        s.geometry_score = round(min(geom_pts / 1.7, 1.0), 3)

        s.artifact_score = 1.0 if arti.passed else max(0.0, 1.0 - 0.25 * len(arti.issues))
        s.artifact_score = round(s.artifact_score, 3)

        s.overall = round(0.3 * s.topology_score + 0.3 * s.geometry_score + 0.4 * s.artifact_score, 3)

        if s.overall >= 0.9:
            s.grade = "A"
        elif s.overall >= 0.75:
            s.grade = "B"
        elif s.overall >= 0.6:
            s.grade = "C"
        elif s.overall >= 0.4:
            s.grade = "D"
        else:
            s.grade = "F"

        parts = []
        parts.append(f"Grade {s.grade} ({s.overall:.0%})")
        if not arti.passed:
            parts.append(f"{len(arti.issues)} artifact(s)")
        if not topo.watertight:
            parts.append("non-watertight")
        if topo.degenerate_faces > 0:
            parts.append(f"{topo.degenerate_faces} degenerate faces")
        if geom.flatness_ratio < 0.12:
            parts.append("flat cutout risk")
        s.summary = "; ".join(parts)

        return s

    # ------------------------------------------------------------------
    # Render views via Animator3D
    # ------------------------------------------------------------------

    def _render_views(
        self,
        output_dir: Path,
        *,
        animator3d_bin: str | None = None,
        views: str = "front,three_quarter,right,back,top,low_front",
        resolution: int = 512,
        engine: str = "workbench",
    ) -> list[str]:
        output_dir.mkdir(parents=True, exist_ok=True)

        abin = animator3d_bin
        if abin is None:
            try:
                from gamedev_lab.debug_tools import resolve_animator3d_bin

                abin = resolve_animator3d_bin()
            except Exception:
                abin = None

        if not abin:
            return []

        from gamedev_lab.debug_tools import extract_json_from_output, run_cmd

        argv = [
            abin,
            "screenshot",
            str(self.path),
            "--output-dir",
            str(output_dir),
            "--views",
            views,
            "--resolution",
            str(resolution),
            "--engine",
            engine,
        ]
        r = run_cmd(argv)
        if r.returncode != 0:
            return []

        report = extract_json_from_output(r.stdout)
        screenshots = report.get("screenshots", [])
        result = []
        for shot in screenshots:
            p = shot.get("path", "")
            if p:
                result.append(p)
        return result

    # ------------------------------------------------------------------
    # Compare with reference image
    # ------------------------------------------------------------------

    @staticmethod
    def _compare_with_reference(
        reference_image: Path,
        view_paths: list[str],
        output_dir: Path,
    ) -> list[dict[str, Any]]:
        from gamedev_lab.compare_images import compare_view_pair

        comparisons = []
        for vp in view_paths:
            view_path = Path(vp)
            if not view_path.is_file():
                continue
            try:
                metrics = compare_view_pair(reference_image, view_path)
                metrics["view_path"] = vp
                comparisons.append(metrics)
            except Exception:
                comparisons.append({"view_path": vp, "error": "comparison failed"})
        return comparisons


def print_qa_report(report: MeshQAReport) -> None:
    from rich.console import Console
    from rich.table import Table

    c = Console()
    c.print(f"\n[bold]Mesh QA Report: {report.path}[/bold]\n")

    t = Table(title="Topology", show_header=False)
    t.add_column("Key", style="cyan")
    t.add_column("Value")
    t.add_row("Vertices", f"{report.topology.vertices:,}")
    t.add_row("Faces", f"{report.topology.faces:,}")
    t.add_row("Edges", f"{report.topology.edges:,}")
    t.add_row("Watertight", "Yes" if report.topology.watertight else "No")
    t.add_row("Euler Number", str(report.topology.euler_number))
    t.add_row("Boundary Edges", str(report.topology.boundary_edges))
    if report.topology.boundary_edges > 0:
        t.add_row("  Real Holes", str(report.topology.real_holes))
        t.add_row("  UV Seam Edges", str(report.topology.uv_seam_edges))
    t.add_row("Connected Components", str(report.topology.connected_components))
    t.add_row("Degenerate Faces", str(report.topology.degenerate_faces))
    t.add_row("Duplicate Vertices", str(report.topology.duplicate_vertices))
    c.print(t)

    t2 = Table(title="Geometry", show_header=False)
    t2.add_column("Key", style="cyan")
    t2.add_column("Value")
    t2.add_row("Volume Efficiency", f"{report.geometry.volume_efficiency:.4f}")
    t2.add_row("Flatness Ratio", f"{report.geometry.flatness_ratio:.4f}")
    t2.add_row("Thickness Ratio", f"{report.geometry.thickness_ratio:.4f}")
    t2.add_row("Aspect Ratio", f"{report.geometry.aspect_ratio:.2f}")
    t2.add_row("Surface Area", f"{report.geometry.area:.4f}")
    if report.geometry.volume is not None:
        t2.add_row("Volume", f"{report.geometry.volume:.4f}")
    c.print(t2)

    if report.artifacts.backing_plates:
        c.print("[bold red]Backing Plates:[/bold red]")
        for bp in report.artifacts.backing_plates:
            c.print(f"  {bp['axis']}-{bp['side']}: coverage={bp['coverage']:.2f}")

    if report.artifacts.issues:
        c.print("[bold red]Issues:[/bold red]")
        for issue in report.artifacts.issues:
            c.print(f"  [red]- {issue}[/red]")
    else:
        c.print("[green]No artifacts detected.[/green]")

    grade_color = "green" if report.score.grade in ("A", "B") else ("yellow" if report.score.grade == "C" else "red")
    c.print(f"\n[bold]Score:[/bold] [{grade_color}]{report.score.grade}[/{grade_color}] ({report.score.overall:.0%})")
    c.print(f"[dim]{report.score.summary}[/dim]")

    if report.reference_comparison:
        c.print(f"\n[bold]Reference Comparison ({len(report.reference_comparison)} views):[/bold]")
        for comp in report.reference_comparison:
            vp = comp.get("view_path", "?")
            ssim = comp.get("ssim")
            mae = comp.get("mae")
            if ssim is not None:
                sc = "green" if ssim >= 0.8 else ("yellow" if ssim >= 0.5 else "red")
                c.print(f"  {Path(vp).stem}: SSIM=[{sc}]{ssim:.4f}[/{sc}] MAE={mae:.4f}")
