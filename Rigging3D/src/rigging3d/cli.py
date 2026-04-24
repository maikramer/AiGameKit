"""Rigging3D — CLI principal."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

import yaml
from gamedev_shared.profiler.session import ProfilerSession
from rich.console import Console

from . import __version__
from .cli_rich import click

console = Console()

DEFAULT_SKELETON_TASK = "configs/task/quick_inference_skeleton_articulationxl_ar_256.yaml"
DEFAULT_SKIN_TASK = "configs/task/quick_inference_unirig_skin.yaml"

_WIN32 = sys.platform == "win32"

# ---------------------------------------------------------------------------
# Resolução de caminhos
# ---------------------------------------------------------------------------


def _package_root() -> Path:
    """Raiz da árvore UniRig empacotada (configs/, src/, launch/)."""
    return Path(__file__).resolve().parent / "unirig"


def _resolve_root(explicit: Path | None) -> Path:
    if explicit is not None:
        root = explicit.expanduser().resolve()
    else:
        env = os.environ.get("RIGGING3D_ROOT", "").strip()
        root = Path(env).expanduser().resolve() if env else _package_root()

    if not (root / "configs").is_dir() or not (root / "src").is_dir():
        raise FileNotFoundError(
            f"Árvore de inferência não encontrada em {root} (falta configs/ ou src/). "
            "Define RIGGING3D_ROOT ou usa --root."
        )
    return root


def _resolve_python(explicit: str | None) -> str:
    if explicit:
        return explicit
    env = os.environ.get("RIGGING3D_PYTHON", "").strip()
    return env if env else sys.executable


def _shell_path(path: Path) -> str:
    s = str(path.expanduser().resolve())
    if _WIN32:
        s = s.replace("\\", "/")
    return s


# ---------------------------------------------------------------------------
# Bash
# ---------------------------------------------------------------------------


def _find_bash() -> str | None:
    if _WIN32:
        for c in (
            Path(r"C:\Program Files\Git\bin\bash.exe"),
            Path(r"C:\Program Files\Git\usr\bin\bash.exe"),
            Path(r"C:\Program Files (x86)\Git\bin\bash.exe"),
            Path(r"C:\msys64\usr\bin\bash.exe"),
        ):
            if c.is_file():
                return str(c)
        w = shutil.which("bash")
        if w:
            low = w.lower().replace("/", "\\")
            if "\\system32\\bash.exe" in low or "\\windowsapps\\" in low:
                return None
            return w
        return None
    return shutil.which("bash")


def _require_bash() -> None:
    if not _find_bash():
        raise click.ClickException(
            "bash não encontrado. No Windows: Git Bash ou MSYS2; noutros sistemas: bash no PATH."
        )


# ---------------------------------------------------------------------------
# Subprocess
# ---------------------------------------------------------------------------


def _make_env(
    root: Path,
    extra: dict[str, str] | None = None,
    *,
    python_bin: str | None = None,
    propagate_profile: bool = False,
    gpu_ids: list[int] | None = None,
) -> dict[str, str]:
    merged = {**os.environ, **(extra or {})}
    root_s = str(root)
    pp = merged.get("PYTHONPATH", "")
    merged["PYTHONPATH"] = root_s if not pp else root_s + os.pathsep + pp
    if python_bin:
        abspath = os.path.abspath(os.path.expanduser(python_bin))
        bindir = os.path.dirname(abspath)
        merged["PATH"] = bindir + os.pathsep + merged.get("PATH", "")
        merged["PYTHON"] = abspath
    if not _WIN32:
        merged.setdefault("PYOPENGL_PLATFORM", "egl")
        merged.setdefault("__NV_PRIME_RENDER_OFFLOAD", "1")
        merged.setdefault("__GLX_VENDOR_LIBRARY_NAME", "nvidia")
    if propagate_profile:
        merged["GAMEDEV_PROFILE"] = "1"
    if gpu_ids:
        merged["CUDA_VISIBLE_DEVICES"] = ",".join(str(g) for g in gpu_ids)
    return merged


def _run(
    cmd: list[str],
    *,
    root: Path,
    env: dict[str, str] | None = None,
    python_bin: str | None = None,
    propagate_profile: bool = False,
    gpu_ids: list[int] | None = None,
) -> int:
    return subprocess.run(
        cmd,
        cwd=str(root),
        env=_make_env(root, env, python_bin=python_bin, propagate_profile=propagate_profile, gpu_ids=gpu_ids),
    ).returncode


def _run_bash(
    root: Path,
    script: str,
    args: Sequence[str],
    *,
    python_bin: str | None = None,
    propagate_profile: bool = False,
    gpu_ids: list[int] | None = None,
) -> int:
    bash = _find_bash()
    if not bash:
        raise RuntimeError("bash não encontrado")
    full = root / script
    if not full.is_file():
        raise FileNotFoundError(f"Script em falta: {full}")
    return _run(
        [bash, _shell_path(full), *args],
        root=root,
        python_bin=python_bin,
        propagate_profile=propagate_profile,
        gpu_ids=gpu_ids,
    )


def _run_module(
    root: Path,
    py: str,
    module: str,
    args: Sequence[str],
    *,
    env: dict[str, str] | None = None,
    gpu_ids: list[int] | None = None,
) -> int:
    return _run([py, "-m", module, *args], root=root, env=env, python_bin=py, gpu_ids=gpu_ids)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@click.group()
@click.version_option(version=__version__, prog_name="rigging3d")
@click.option(
    "--root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    envvar="RIGGING3D_ROOT",
    help="Raiz da árvore de inferência (configs/ + src/). Por defeito: pacote ou RIGGING3D_ROOT",
)
@click.option(
    "--python",
    "python_cmd",
    default=None,
    envvar="RIGGING3D_PYTHON",
    help="Interpretador Python (conda/venv).",
)
@click.option(
    "--profiler",
    "profiler_flag",
    is_flag=True,
    help="Gravar métricas de performance (perf DB).",
)
@click.option(
    "--gpu-ids",
    "gpu_ids_str",
    default=None,
    help='IDs de GPU visíveis aos subprocessos (ex: "0,1"). Propaga CUDA_VISIBLE_DEVICES.',
)
@click.pass_context
def cli(
    ctx: click.Context,
    root: Path | None,
    python_cmd: str | None,
    profiler_flag: bool,
    gpu_ids_str: str | None,
) -> None:
    """Rigging3D — auto-rigging 3D (skeleton, skinning, merge)."""
    ctx.ensure_object(dict)
    ctx.obj["PROFILER"] = profiler_flag
    gpu_ids: list[int] | None = None
    if gpu_ids_str:
        gpu_ids = [int(x) for x in gpu_ids_str.split(",") if x.strip()]
    ctx.obj["GPU_IDS"] = gpu_ids
    if profiler_flag:
        os.environ["GAMEDEV_PROFILE"] = "1"


def _ctx_root_py(ctx: click.Context) -> tuple[Path, str]:
    p = ctx.parent.params if ctx.parent is not None else ctx.params
    try:
        root = _resolve_root(p.get("root"))
    except FileNotFoundError as e:
        raise click.ClickException(str(e)) from e
    return root, _resolve_python(p.get("python_cmd"))


def _ctx_profiler(ctx: click.Context) -> bool:
    parent = ctx.parent
    if parent is None:
        return False
    return bool(parent.obj.get("PROFILER"))


def _ctx_gpu_ids(ctx: click.Context) -> list[int] | None:
    parent = ctx.parent
    if parent is None:
        return None
    return parent.obj.get("GPU_IDS")


# --- skeleton ---


@cli.command("skeleton")
@click.option("--input", "-i", "input_path", type=click.Path(path_type=Path), default=None)
@click.option("--output", "-o", "output_path", type=click.Path(path_type=Path), default=None)
@click.option("--seed", type=int, default=None, show_default=True, help="Seed reprodutível (None = aleatório)")
@click.option("--skeleton-task", default=DEFAULT_SKELETON_TASK, show_default=True, help="YAML de task")
@click.option("--input-dir", type=click.Path(file_okay=False, path_type=Path), default=None)
@click.option("--output-dir", type=click.Path(file_okay=False, path_type=Path), default=None)
@click.pass_context
def skeleton_cmd(
    ctx: click.Context,
    input_path: Path | None,
    output_path: Path | None,
    seed: int | None,
    skeleton_task: str,
    input_dir: Path | None,
    output_dir: Path | None,
) -> None:
    """Gera skeleton (GLB por defeito; .fbx ainda suportado) a partir de mesh (.glb/.obj/…)."""
    root, py = _ctx_root_py(ctx)
    gpu_ids = _ctx_gpu_ids(ctx)
    _require_bash()
    _validate_io(input_path, output_path, input_dir, output_dir)
    seed_args: list[str] = []
    if seed is not None:
        seed_args = ["--seed", str(seed)]
    args: list[str] = [*seed_args, "--skeleton_task", skeleton_task]
    args += _io_args(input_path, output_path, input_dir, output_dir)
    rc = _run_bash(root, "launch/inference/generate_skeleton.sh", args, python_bin=py, gpu_ids=gpu_ids)
    if rc != 0:
        raise click.ClickException(f"generate_skeleton.sh terminou com código {rc}")
    console.print("[green]Skeleton concluído.[/green]")


# --- skin ---


@cli.command("skin")
@click.option("--input", "-i", "input_path", type=click.Path(path_type=Path), default=None)
@click.option("--output", "-o", "output_path", type=click.Path(path_type=Path), default=None)
@click.option("--seed", type=int, default=None, show_default=True, help="Seed reprodutível (None = aleatório)")
@click.option("--skin-task", default=DEFAULT_SKIN_TASK, show_default=True, help="YAML de task")
@click.option("--input-dir", type=click.Path(file_okay=False, path_type=Path), default=None)
@click.option("--output-dir", type=click.Path(file_okay=False, path_type=Path), default=None)
@click.option("--data-name", default="raw_data.npz", show_default=True)
@click.pass_context
def skin_cmd(
    ctx: click.Context,
    input_path: Path | None,
    output_path: Path | None,
    seed: int | None,
    skin_task: str,
    input_dir: Path | None,
    output_dir: Path | None,
    data_name: str,
) -> None:
    """Prevê pesos de skinning a partir do GLB/FBX com skeleton."""
    root, py = _ctx_root_py(ctx)
    gpu_ids = _ctx_gpu_ids(ctx)
    _require_bash()
    _validate_io(input_path, output_path, input_dir, output_dir)
    seed_args: list[str] = []
    if seed is not None:
        seed_args = ["--seed", str(seed)]
    args: list[str] = [*seed_args, "--skin_task", skin_task, "--data_name", data_name]
    args += _io_args(input_path, output_path, input_dir, output_dir)
    rc = _run_bash(root, "launch/inference/generate_skin.sh", args, python_bin=py, gpu_ids=gpu_ids)
    if rc != 0:
        raise click.ClickException(f"generate_skin.sh terminou com código {rc}")
    console.print("[green]Skinning concluído.[/green]")


# --- merge ---


@cli.command("merge")
@click.option("--source", "-s", type=click.Path(path_type=Path), required=True, help="GLB/FBX com skin")
@click.option("--target", "-t", type=click.Path(path_type=Path), required=True, help="Mesh original")
@click.option("--output", "-o", type=click.Path(path_type=Path), required=True)
@click.option("--require-suffix", default="obj,fbx,FBX,dae,glb,gltf,vrm", show_default=True)
@click.option("--smooth-iterations", type=int, default=2, show_default=True, help="Passagens de suavização Laplaciana.")
@click.option("--groups-per-vertex", type=int, default=8, show_default=True, help="Influências de osso por vértice.")
@click.option(
    "--draco/--no-draco", default=False, show_default=True, help="Comprimir meshes com Draco no GLB de saída."
)
@click.pass_context
def merge_cmd(
    ctx: click.Context,
    source: Path,
    target: Path,
    output: Path,
    require_suffix: str,
    smooth_iterations: int,
    groups_per_vertex: int,
    draco: bool,
) -> None:
    """Combina resultado da fase skin com o mesh original (GLB rigado)."""
    root, py = _ctx_root_py(ctx)
    gpu_ids = _ctx_gpu_ids(ctx)
    args = [
        f"--require_suffix={require_suffix}",
        "--num_runs=1",
        "--id=0",
        f"--source={_shell_path(source)}",
        f"--target={_shell_path(target)}",
        f"--output={_shell_path(output)}",
    ]
    merge_env = {
        "RIGGING3D_SMOOTH_ITERATIONS": str(smooth_iterations),
        "RIGGING3D_GROUPS_PER_VERTEX": str(groups_per_vertex),
        "RIGGING3D_DRACO": "1" if draco else "0",
    }
    rc = _run_module(root, py, "src.inference.merge", args, env=merge_env, gpu_ids=gpu_ids)
    if rc != 0:
        raise click.ClickException(f"merge terminou com código {rc}")
    console.print("[green]Merge concluído.[/green]")


# --- pipeline ---


def _validate_and_fix_origin(glb_path: Path, tolerance: float = 0.1) -> bool:
    """Valida se a base do modelo está em Y≈0 (convenção feet do Text3D).

    Não aplica correção: reexportar com trimesh removeria armature/skin do GLB rigado.

    Args:
        glb_path: GLB final após merge.
        tolerance: Aceita |min_y| até este valor.

    Returns:
        True se min_y está fora da tolerância (foi emitido aviso); False se OK ou em erro de leitura.
    """
    try:
        import trimesh

        scene = trimesh.load(str(glb_path))
        if isinstance(scene, trimesh.Scene):
            mesh = trimesh.util.concatenate(scene.dump())
        else:
            mesh = scene
        min_y = float(mesh.bounds[0][1])
        if abs(min_y) <= tolerance:
            return False
        click.echo(
            f"  ⚠ Origem: min Y = {min_y:.3f} (esperado ≈0); "
            "GLB rigado não pode ser corrigido aqui — regenerar com origin=feet (Text3D)."
        )
        return True
    except Exception:
        return False


def _prep_mesh_for_rigging(input_path: Path, output_path: Path, python_bin: str) -> bool:
    """Prepara mesh para rigging: merge verts, remove degenerados, remesh, close holes.

    Retorna True se conseguiu preparar (output_path escrito); False se não
    conseguiu (input_path é usado sem modificação).
    """
    import subprocess as _sp

    script = """
import sys, trimesh, numpy as np
mesh = trimesh.load(sys.argv[1], force='mesh')
mesh.merge_vertices(digits_vertex=4)
mask = mesh.area_faces > 1e-7
mesh.update_faces(mask)
mesh.remove_unreferenced_vertices()
try:
    import pymeshlab
    ms = pymeshlab.MeshSet()
    ms.add_mesh(pymeshlab.Mesh(mesh.vertices, mesh.faces))
    ms.meshing_repair_non_manifold_edges()
    ms.meshing_repair_non_manifold_vertices()
    ms.meshing_remove_duplicate_faces()
    ms.meshing_remove_duplicate_vertices()
    ms.meshing_close_holes(maxholesize=300)
    ms.meshing_isotropic_explicit_remeshing(
        targetlen=pymeshlab.PercentageValue(1.0),
        adaptive=True,
        iterations=5,
    )
    ms.apply_coord_taubin_smoothing(stepsmoothnum=3)
    ms.meshing_remove_unreferenced_vertices()
    out = ms.current_mesh()
    mesh = trimesh.Trimesh(vertices=out.vertex_matrix(), faces=out.face_matrix())
except ImportError:
    pass
trimesh.repair.fill_holes(mesh)
trimesh.repair.fix_normals(mesh, multibody=True)
mesh.export(sys.argv[2], file_type='glb')
print(f'prep: {len(mesh.vertices)} verts, {len(mesh.faces)} faces')
"""
    try:
        r = _sp.run(
            [python_bin, "-c", script, str(input_path), str(output_path)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if r.returncode == 0 and output_path.is_file() and output_path.stat().st_size > 0:
            console.print(f"[dim]{r.stdout.strip()}[/dim]")
            return True
        if r.stderr:
            console.print(f"[yellow]prep mesh aviso: {r.stderr[:200]}[/yellow]")
    except Exception as e:
        console.print(f"[yellow]prep mesh falhou ({e}); a usar mesh original.[/yellow]")
    return False


@cli.command("pipeline")
@click.option("--input", "-i", "mesh", type=click.Path(exists=True, path_type=Path), required=True)
@click.option("--output", "-o", "out", type=click.Path(path_type=Path), required=True)
@click.option("--work-dir", type=click.Path(file_okay=False, path_type=Path), default=None, help="Dir intermédio")
@click.option("--seed", type=int, default=None, show_default=True, help="Seed reprodutível (None = aleatório)")
@click.option("--keep-temp", is_flag=True, help="Não apagar work-dir temporário")
@click.option(
    "--smooth-iterations", type=int, default=2, show_default=True, help="Passadas de suavização Laplaciana no merge."
)
@click.option("--groups-per-vertex", type=int, default=8, show_default=True, help="Influências de osso por vértice.")
@click.option("--no-prep", is_flag=True, help="Não preparar mesh (skip remesh/repair).")
@click.option("--low-vram", is_flag=True, help="Modo baixa VRAM: num_train_vertex 256 (padrão: 512).")
@click.option(
    "--draco/--no-draco", default=False, show_default=True, help="Comprimir meshes com Draco no GLB de saída."
)
@click.pass_context
def pipeline_cmd(
    ctx: click.Context,
    mesh: Path,
    out: Path,
    work_dir: Path | None,
    seed: int | None,
    keep_temp: bool,
    smooth_iterations: int,
    groups_per_vertex: int,
    no_prep: bool,
    low_vram: bool,
    draco: bool,
) -> None:
    """Encadeia skeleton → skin → merge até um GLB rigado."""
    from gamedev_shared.gpu import warn_if_vram_occupied

    root, py = _ctx_root_py(ctx)
    gpu_ids = _ctx_gpu_ids(ctx)
    _require_bash()
    do_profile = _ctx_profiler(ctx)

    warn_if_vram_occupied()

    cleanup: Path | None = None
    if work_dir is None:
        cleanup = Path(tempfile.mkdtemp(prefix="rigging3d_"))
        wd = cleanup
    else:
        wd = work_dir
        wd.mkdir(parents=True, exist_ok=True)

    with ProfilerSession(
        "rigging3d",
        cli_profile=do_profile,
        params={"seed": seed, "smooth_iterations": smooth_iterations, "groups_per_vertex": groups_per_vertex},
    ):
        actual_mesh = mesh
        if not no_prep:
            prepped = wd / "_prepped.glb"
            console.print("[dim]Preparando mesh (remesh + repair)...[/dim]")
            if _prep_mesh_for_rigging(mesh, prepped, py):
                actual_mesh = prepped
            else:
                console.print("[yellow]Prep falhou; a usar mesh original.[/yellow]")

        skel = wd / "_skeleton.glb"
        skin = wd / "_skin.glb"
        _low_vram_cleanup: list[Path] = []
        try:
            skel_gpu = gpu_ids[:1] if gpu_ids and len(gpu_ids) >= 2 else gpu_ids
            skin_gpu = gpu_ids[1:2] if gpu_ids and len(gpu_ids) >= 2 else gpu_ids
            if gpu_ids and len(gpu_ids) >= 2:
                console.print(f"[dim]Multi-GPU: skeleton→cuda:{gpu_ids[0]}, skin→cuda:{gpu_ids[1]}[/dim]")

            skel_args = ["--input", _shell_path(actual_mesh), "--output", _shell_path(skel)]
            if seed is not None:
                skel_args += ["--seed", str(seed)]
            rc = _run_bash(
                root,
                "launch/inference/generate_skeleton.sh",
                skel_args,
                python_bin=py,
                propagate_profile=do_profile,
                gpu_ids=skel_gpu,
            )
            if rc != 0 or not skel.is_file() or skel.stat().st_size == 0:
                raise click.ClickException(
                    f"skeleton falhou (código {rc} ou GLB em falta). Confirma deps inferência, pesos HF e logs acima."
                )

            skin_task_path = DEFAULT_SKIN_TASK
            if low_vram:
                model_yaml_path = root / "configs" / "model" / "unirig_skin.yaml"
                with open(model_yaml_path) as f:
                    model_cfg = yaml.safe_load(f)
                model_cfg["num_train_vertex"] = 256

                low_vram_model = wd / "_low_vram_unirig_skin.yaml"
                with open(low_vram_model, "w") as f:
                    yaml.dump(model_cfg, f, default_flow_style=False)

                task_yaml_path = root / "configs" / "task" / "quick_inference_unirig_skin.yaml"
                with open(task_yaml_path) as f:
                    task_cfg = yaml.safe_load(f)
                task_cfg["components"]["model"] = "_low_vram_unirig_skin"

                low_vram_task = wd / "_low_vram_skin_task.yaml"
                with open(low_vram_task, "w") as f:
                    yaml.dump(task_cfg, f, default_flow_style=False)

                skin_task_path = str(low_vram_task)
                _low_vram_cleanup.extend([low_vram_model, low_vram_task])
                console.print("[dim]Low-VRAM: num_train_vertex=256[/dim]")

            skin_args = [
                "--input",
                _shell_path(skel),
                "--output",
                _shell_path(skin),
                "--skin_task",
                skin_task_path,
            ]
            if seed is not None:
                skin_args += ["--seed", str(seed)]
            rc = _run_bash(
                root,
                "launch/inference/generate_skin.sh",
                skin_args,
                python_bin=py,
                propagate_profile=do_profile,
                gpu_ids=skin_gpu,
            )
            if rc != 0 or not skin.is_file() or skin.stat().st_size == 0:
                raise click.ClickException(
                    f"skin falhou (código {rc} ou GLB em falta). Confirma spconv, VRAM e logs acima."
                )

            merge_env = {
                "RIGGING3D_SMOOTH_ITERATIONS": str(smooth_iterations),
                "RIGGING3D_GROUPS_PER_VERTEX": str(groups_per_vertex),
                "RIGGING3D_DRACO": "1" if draco else "0",
            }
            rc = _run_module(
                root,
                py,
                "src.inference.merge",
                [
                    "--require_suffix=obj,fbx,FBX,dae,glb,gltf,vrm",
                    "--num_runs=1",
                    "--id=0",
                    f"--source={_shell_path(skin)}",
                    f"--target={_shell_path(mesh)}",
                    f"--output={_shell_path(out)}",
                ],
                env=merge_env,
                gpu_ids=None,
            )
            if not out.is_file() or out.stat().st_size == 0:
                raise click.ClickException(
                    f"merge falhou (código {rc} ou GLB vazio). Confirma bpy/open3d e caminhos acima."
                )
            if rc != 0:
                console.print(f"[yellow]merge rc={rc}, output={out.stat().st_size}B- prosseguindo.[/yellow]")
            _validate_and_fix_origin(out)
        finally:
            if cleanup is not None and not keep_temp:
                shutil.rmtree(cleanup, ignore_errors=True)
            elif low_vram:
                for p in _low_vram_cleanup:
                    p.unlink(missing_ok=True)

    console.print(f"[green]Pipeline concluído:[/green] {out}")


# ---------------------------------------------------------------------------
# Helpers I/O
# ---------------------------------------------------------------------------


def _validate_io(
    input_path: Path | None,
    output_path: Path | None,
    input_dir: Path | None,
    output_dir: Path | None,
) -> None:
    if input_dir is not None:
        if output_dir is None:
            raise click.ClickException("Com --input-dir indica também --output-dir.")
    elif input_path is None or output_path is None:
        raise click.ClickException("Indica --input e --output, ou --input-dir e --output-dir.")


def _io_args(
    input_path: Path | None,
    output_path: Path | None,
    input_dir: Path | None,
    output_dir: Path | None,
) -> list[str]:
    args: list[str] = []
    if input_dir is not None:
        args += ["--input_dir", _shell_path(input_dir), "--output_dir", _shell_path(output_dir)]
    else:
        assert input_path is not None and output_path is not None
        args += ["--input", _shell_path(input_path), "--output", _shell_path(output_path)]
        if output_dir is not None:
            args += ["--output_dir", _shell_path(output_dir)]
    return args


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------


def main() -> None:
    cli(obj={})


if __name__ == "__main__":
    main()
