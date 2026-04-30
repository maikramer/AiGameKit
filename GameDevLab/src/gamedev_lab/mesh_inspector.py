"""Mesh quality inspector — topology, geometry, artifacts, and visual QA."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from gamedev_shared.bpy_mesh import load_glb

# ---------------------------------------------------------------------------
# Internal mesh-data container (replaces trimesh.Trimesh)
# ---------------------------------------------------------------------------


class _ConvexHullResult:
    """Minimal wrapper exposing ``volume`` for convex-hull queries."""

    def __init__(self, volume: float | None) -> None:
        self.volume = volume


class _MeshData:
    """Pre-computed mesh data loaded via bpy — trimesh-compatible read-only interface.

    All heavy computation is performed once at construction time so that
    inspection methods can read properties cheaply.
    """

    __slots__ = (
        "area",
        "area_faces",
        "bounds",
        "centroid",
        "connected_components",
        "convex_hull",
        "duplicate_vertices",
        "edges_unique",
        "euler_number",
        "extents",
        "face_normals",
        "faces",
        "is_watertight",
        "triangles_center",
        "vertices",
        "volume",
    )

    def __init__(self, vertices: np.ndarray, faces: np.ndarray) -> None:
        self.vertices = vertices  # (N, 3) float64
        self.faces = faces  # (F, 3) int64
        self._precompute()

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _precompute(self) -> None:
        verts = self.vertices
        tris = self.faces
        V = len(verts)
        F = len(tris)

        # --- bounds / extents / centroid ---
        if V > 0:
            self.bounds = np.array([verts.min(axis=0), verts.max(axis=0)])
            self.extents = self.bounds[1] - self.bounds[0]
            self.centroid = self.bounds.mean(axis=0)
        else:
            self.bounds = np.zeros((2, 3))
            self.extents = np.zeros(3)
            self.centroid = np.zeros(3)

        # --- edges + edge-face counts ---
        edge_set: set[tuple[int, int]] = set()
        edge_count: dict[tuple[int, int], int] = {}
        if F > 0:
            for tri in tris:
                for i in range(3):
                    a, b = int(tri[i]), int(tri[(i + 1) % 3])
                    e = (min(a, b), max(a, b))
                    edge_set.add(e)
                    edge_count[e] = edge_count.get(e, 0) + 1
        self.edges_unique = np.array(sorted(edge_set), dtype=np.int64) if edge_set else np.empty((0, 2), dtype=np.int64)

        # --- Euler number ---
        E = len(edge_set)
        self.euler_number = V - E + F

        # --- face geometry ---
        if F > 0:
            v0 = verts[tris[:, 0]]
            v1 = verts[tris[:, 1]]
            v2 = verts[tris[:, 2]]
            cross = np.cross(v1 - v0, v2 - v0)
            self.area_faces = np.linalg.norm(cross, axis=1) / 2.0
            norms = np.linalg.norm(cross, axis=1, keepdims=True)
            norms = np.where(norms < 1e-12, 1.0, norms)
            self.face_normals = cross / norms
            self.triangles_center = (v0 + v1 + v2) / 3.0
            self.area = float(self.area_faces.sum())
        else:
            self.area_faces = np.empty(0, dtype=np.float64)
            self.face_normals = np.empty((0, 3), dtype=np.float64)
            self.triangles_center = np.empty((0, 3), dtype=np.float64)
            self.area = 0.0

        # --- watertight ---
        if F > 0 and edge_count:
            self.is_watertight = all(c == 2 for c in edge_count.values())
        else:
            self.is_watertight = False

        # --- connected components (union-find on face graph) ---
        if F > 0:
            parent = list(range(V))

            def find(x: int) -> int:
                while parent[x] != x:
                    parent[x] = parent[parent[x]]
                    x = parent[x]
                return x

            def union(a: int, b: int) -> None:
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[ra] = rb

            used: set[int] = set()
            for tri in tris:
                for i in range(3):
                    a, b = int(tri[i]), int(tri[(i + 1) % 3])
                    union(a, b)
                    used.add(a)
                    used.add(b)
            self.connected_components = len({find(v) for v in used}) if used else 1
        else:
            self.connected_components = 1

        # --- duplicate vertices ---
        if V > 0:
            rounded = np.round(verts, decimals=6)
            _, unique_idx = np.unique(rounded, axis=0, return_index=True)
            self.duplicate_vertices = V - len(unique_idx)
        else:
            self.duplicate_vertices = 0

        # --- volume (signed-volume method, watertight only) ---
        if self.is_watertight and F > 0:
            v0 = verts[tris[:, 0]]
            v1 = verts[tris[:, 1]]
            v2 = verts[tris[:, 2]]
            sv = np.sum(v0 * np.cross(v1, v2), axis=1)
            self.volume = float(abs(sv.sum()) / 6.0)
        else:
            self.volume = None

        # --- convex hull (optional, needs scipy) ---
        hull_vol: float | None = None
        if V >= 4:
            try:
                from scipy.spatial import ConvexHull

                hull = ConvexHull(verts)
                hull_vol = float(hull.volume)
            except Exception:
                pass
        self.convex_hull = _ConvexHullResult(hull_vol)


# ---------------------------------------------------------------------------
# Report dataclasses
# ---------------------------------------------------------------------------


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

    def _load_mesh(self) -> _MeshData:
        objects = load_glb(self.path)
        if not objects:
            raise ValueError(f"No mesh objects found in {self.path}")

        all_verts: list[np.ndarray] = []
        all_faces: list[list[int]] = []
        offset = 0
        for obj in objects:
            mesh = obj.data
            mesh.calc_loop_triangles()
            verts = np.array([tuple(obj.matrix_world @ v.co) for v in mesh.vertices], dtype=np.float64)
            all_verts.append(verts)
            for tri in mesh.loop_triangles:
                all_faces.append([v + offset for v in tri.vertices])
            offset += len(verts)

        vertices = np.vstack(all_verts) if all_verts else np.empty((0, 3), dtype=np.float64)
        faces = np.array(all_faces, dtype=np.int64) if all_faces else np.empty((0, 3), dtype=np.int64)
        return _MeshData(vertices, faces)

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
    def _classify_boundary_edges(mesh: _MeshData) -> tuple[int, int, dict[tuple[int, ...], int]]:
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

    def _inspect_topology(self, mesh: _MeshData) -> TopologyReport:
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

        r.connected_components = mesh.connected_components

        areas = mesh.area_faces
        r.degenerate_faces = int(np.sum(areas < self._degen_eps))

        r.duplicate_vertices = mesh.duplicate_vertices

        return r

    # ------------------------------------------------------------------
    # Geometry
    # ------------------------------------------------------------------

    def _inspect_geometry(self, mesh: _MeshData) -> GeometryReport:
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
                r.volume = float(mesh.volume) if mesh.volume is not None else None
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

    def _inspect_artifacts(self, mesh: _MeshData) -> ArtifactReport:
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
        views: str = "front,three_quarter,right,back,top,low_front",
        resolution: int = 512,
        engine: str = "workbench",
    ) -> list[str]:
        output_dir.mkdir(parents=True, exist_ok=True)

        from gamedev_lab.renderer import render_screenshots

        try:
            report = render_screenshots(
                self.path,
                output_dir,
                views=views,
                resolution=resolution,
                engine=engine,
            )
            return [s["path"] for s in report.get("screenshots", []) if s.get("path")]
        except Exception:
            return []

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
