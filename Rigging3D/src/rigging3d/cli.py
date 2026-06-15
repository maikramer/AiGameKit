"""Rigging3D — CLI principal."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path

import yaml
from gamedev_shared.profiler.session import ProfilerSession
from gamedev_shared.progress import STATUS_ERROR, STATUS_OK, TOOL_RIGGING3D, emit_progress, emit_result
from gamedev_shared.quality import VALID_QUALITIES
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
@click.option(
    "--hw-auto/--no-hw-auto",
    "hw_auto",
    default=True,
    show_default=True,
    help=(
        "Auto-detecção de hardware: em rigs multi-GPU pina o UniRig na placa "
        "com mais VRAM livre; avisa em GPUs <6.5GB. --gpu-ids explícito ganha. "
        "Env: RIGGING3D_HW_AUTO=0."
    ),
)
@click.pass_context
def cli(
    ctx: click.Context,
    root: Path | None,
    python_cmd: str | None,
    profiler_flag: bool,
    gpu_ids_str: str | None,
    hw_auto: bool,
) -> None:
    """Rigging3D — auto-rigging 3D (skeleton, skinning, merge)."""
    ctx.ensure_object(dict)
    ctx.obj["PROFILER"] = profiler_flag
    gpu_ids: list[int] | None = None
    if gpu_ids_str:
        gpu_ids = [int(x) for x in gpu_ids_str.split(",") if x.strip()]

    if hw_auto:
        from .hardware import detect_hardware_profile, hw_auto_enabled

        if hw_auto_enabled():
            hwp = detect_hardware_profile()
            if hwp.device == "cuda":
                ctx.obj["HW_LOW_VRAM"] = hwp.low_vram
            if gpu_ids is None and hwp.gpu_ids is not None:
                gpu_ids = hwp.gpu_ids
                click.echo(f"Hardware (auto): {hwp.summary()}", err=True)
            elif hwp.low_vram_warning and hwp.device == "cuda":
                click.echo(f"Hardware (auto): {hwp.summary()}", err=True)
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
@click.option("--smooth-iterations", type=int, default=3, show_default=True, help="Passagens de suavização Laplaciana.")
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


def _rename_generic_bones(glb_path: Path, root: Path) -> int:  # noqa: ARG001
    """Rename ``bone_0..bone_N`` nodes to semantic humanoid names.

    UniRig's autoregressive model sometimes predicts ``cls_none`` instead of the
    expected ``articulationxl`` token, causing ``order.make_names()`` to produce
    generic placeholder names.  This post-merge step analyses the **bone hierarchy
    tree** in the GLB and assigns Mixamo-style semantic names (Hips, Spine,
    LeftArm, RightUpLeg, …) based on each bone's structural role.

    Classification is purely topological (parent-child tree shape), not
    positional, so it works regardless of bone transforms or model predictions
    of ``cls`` tokens.  Bones that cannot be confidently classified keep their
    ``bone_*`` names for downstream tools (e.g. Animator3D spatial rename).

    Args:
        glb_path: Output GLB file to rewrite in-place.
        root: UniRig package root (unused — kept for API compatibility).

    Returns:
        Number of bones renamed (0 if nothing to do).
    """
    import re
    import struct

    # ------------------------------------------------------------------ #
    # Name templates (Mixamo-style, compatible with Animator3D chains)     #
    # ------------------------------------------------------------------ #
    _SPINE_NAMES = ["Hips", "Spine", "Chest", "UpperChest", "UpperChest2", "UpperChest3"]
    _NECK_NAMES = ["Neck", "Head", "Neck1", "Neck2"]
    _ARM_L = ["LeftShoulder", "LeftArm", "LeftForeArm", "LeftHand"]
    _ARM_R = ["RightShoulder", "RightArm", "RightForeArm", "RightHand"]
    _LEG_L = ["LeftUpLeg", "LeftLeg", "LeftFoot", "LeftToeBase"]
    _LEG_R = ["RightUpLeg", "RightLeg", "RightFoot", "RightToeBase"]

    # ------------------------------------------------------------------ #
    # Parse GLB                                                           #
    # ------------------------------------------------------------------ #
    try:
        if glb_path.stat().st_size < 20:
            return 0

        with open(glb_path, "rb") as f:
            header = f.read(12)
            if len(header) < 12:
                return 0
            _magic, _ver, _total = struct.unpack("<III", header)
            if _magic != 0x46546C67:
                return 0
            chunk0_header = f.read(8)
            if len(chunk0_header) < 8:
                return 0
            chunk0_len, _ct = struct.unpack("<II", chunk0_header)
            json_bytes = f.read(chunk0_len)
            remaining = f.read()

        glb_json = json.loads(json_bytes)
    except (struct.error, json.JSONDecodeError, OSError):
        return 0
    nodes = glb_json.get("nodes", [])

    # ------------------------------------------------------------------ #
    # Identify bone nodes and build hierarchy                             #
    # ------------------------------------------------------------------ #
    bone_re = re.compile(r"^bone_(\d+)$")
    bone_nodes: dict[int, int] = {}  # bone_index → node_index
    for ni, node in enumerate(nodes):
        name = node.get("name", "")
        m = bone_re.match(name)
        if m:
            bone_nodes[int(m.group(1))] = ni

    if not bone_nodes:
        return 0

    # Build children map (bone-index space)
    parent_map: dict[int, int | None] = {}  # node_index → parent_node_index
    for ni, node in enumerate(nodes):
        for c in node.get("children", []):
            parent_map[c] = ni

    children_of_bone: dict[int, list[int]] = {}
    for bi, ni in bone_nodes.items():
        parent_ni = parent_map.get(ni)
        if parent_ni is not None:
            parent_name = nodes[parent_ni].get("name", "")
            pm = bone_re.match(parent_name)
            if pm:
                parent_bi = int(pm.group(1))
                children_of_bone.setdefault(parent_bi, []).append(bi)

    def _linear_chain(start_bi: int) -> list[int]:
        """Follow single-child path from *start_bi*."""
        chain = [start_bi]
        cur = start_bi
        while len(children_of_bone.get(cur, [])) == 1:
            cur = children_of_bone[cur][0]
            chain.append(cur)
        return chain

    def _descendants(bi: int) -> int:
        n = 0
        for c in children_of_bone.get(bi, []):
            n += 1 + _descendants(c)
        return n

    # ------------------------------------------------------------------ #
    # Classify the tree                                                   #
    # ------------------------------------------------------------------ *
    # Find root (bone whose parent is not a bone)
    root_bi: int | None = None
    for bi, ni in bone_nodes.items():
        parent_ni = parent_map.get(ni)
        if parent_ni is None:
            root_bi = bi
            break
        parent_name = nodes[parent_ni].get("name", "")
        if not bone_re.match(parent_name):
            root_bi = bi
            break
    if root_bi is None:
        return 0

    # Spine: follow the child with the most descendants
    root_kids = children_of_bone.get(root_bi, [])
    if not root_kids:
        return 0
    spine_start = max(root_kids, key=_descendants)
    spine = [root_bi] + _linear_chain(spine_start)

    # Upper-chest = last bone in spine
    upper_chest = spine[-1]
    uc_kids = children_of_bone.get(upper_chest, [])

    # Legs = root children that are NOT the spine start
    leg_starts = [c for c in root_kids if c != spine_start]

    # Chains from upper-chest, sorted shortest first
    uc_chains = sorted(
        [_linear_chain(c) for c in uc_kids],
        key=len,
    )

    # ------------------------------------------------------------------ #
    # Assign names                                                        #
    # ------------------------------------------------------------------ #
    assignments: dict[int, str] = {}

    # Spine
    for i, bi in enumerate(spine):
        assignments[bi] = _SPINE_NAMES[i] if i < len(_SPINE_NAMES) else f"Spine{i}"

    # From upper-chest: shortest chain(s) = neck/head, rest = arms
    neck_done = False
    arm_idx = 0
    arm_templates = [_ARM_L, _ARM_R]
    for chain in uc_chains:
        if not neck_done and len(chain) <= 3:
            for i, bi in enumerate(chain):
                assignments[bi] = _NECK_NAMES[i] if i < len(_NECK_NAMES) else f"NeckExtra{i}"
            neck_done = True
        else:
            tpl = arm_templates[arm_idx] if arm_idx < len(arm_templates) else None
            side = "Left" if arm_idx == 0 else "Right"
            tpl_len = len(tpl) if tpl else 0
            for i, bi in enumerate(chain):
                if tpl and i < tpl_len:
                    assignments[bi] = tpl[i]
                else:
                    # Finger bones beyond the standard arm template
                    finger_idx = i - tpl_len
                    assignments[bi] = f"{side}HandFinger{finger_idx + 1}"
            arm_idx += 1

    # Legs from root
    leg_templates = [_LEG_L, _LEG_R]
    for idx, start in enumerate(leg_starts):
        chain = _linear_chain(start)
        tpl = leg_templates[idx] if idx < len(leg_templates) else None
        for i, bi in enumerate(chain):
            if tpl and i < len(tpl):
                assignments[bi] = tpl[i]

    # ------------------------------------------------------------------ #
    # Apply renames in GLB node space                                     #
    # ------------------------------------------------------------------ #
    renames: dict[int, str] = {}  # node_index → new_name
    for bi, new_name in assignments.items():
        ni = bone_nodes.get(bi)
        if ni is not None:
            renames[ni] = new_name

    if not renames:
        return 0

    for ni, new_name in renames.items():
        nodes[ni]["name"] = new_name

    # ------------------------------------------------------------------ #
    # Write back                                                          #
    # ------------------------------------------------------------------ #
    new_json_bytes = json.dumps(glb_json, separators=(",", ":")).encode("utf-8")
    pad = (4 - len(new_json_bytes) % 4) % 4
    new_json_bytes += b" " * pad
    new_chunk0_len = len(new_json_bytes)
    new_total = 12 + 8 + new_chunk0_len + len(remaining)

    with open(glb_path, "wb") as f:
        f.write(struct.pack("<III", 0x46546C67, 2, new_total))
        f.write(struct.pack("<II", new_chunk0_len, 0x4E4F534A))
        f.write(new_json_bytes)
        f.write(remaining)

    return len(renames)


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


@cli.command("pipeline")
@click.option("--input", "-i", "mesh", type=click.Path(exists=True, path_type=Path), required=True)
@click.option("--output", "-o", "out", type=click.Path(path_type=Path), required=True)
@click.option("--work-dir", type=click.Path(file_okay=False, path_type=Path), default=None, help="Dir intermédio")
@click.option("--seed", type=int, default=None, show_default=True, help="Seed reprodutível (None = aleatório)")
@click.option("--keep-temp", is_flag=True, help="Não apagar work-dir temporário")
@click.option(
    "--smooth-iterations", type=int, default=3, show_default=True, help="Passadas de suavização Laplaciana no merge."
)
@click.option("--groups-per-vertex", type=int, default=8, show_default=True, help="Influências de osso por vértice.")
@click.option("--low-vram", is_flag=True, help="Modo baixa VRAM: num_train_vertex 256 (padrão: 512).")
@click.option(
    "--draco/--no-draco", default=False, show_default=True, help="Comprimir meshes com Draco no GLB de saída."
)
@click.option(
    "--quality",
    type=click.Choice(list(VALID_QUALITIES)),
    default="medium",
    show_default=True,
    help="Quality tier (fast / low / medium / high / highest).",
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
    low_vram: bool,
    draco: bool,
    quality: str,
) -> None:
    """Encadeia skeleton → skin → merge até um GLB rigado."""
    from gamedev_shared.gpu import warn_if_vram_occupied
    from gamedev_shared.quality import QualityEngine

    from .oneshot import run_skeleton_inprocess, run_skin_inprocess

    root, py = _ctx_root_py(ctx)
    gpu_ids = _ctx_gpu_ids(ctx)
    do_profile = _ctx_profiler(ctx)

    warn_if_vram_occupied()

    # QualityEngine: soft resolution — fills defaults when user didn't specify.
    _src = click.core.ParameterSource
    _user_set_smooth = ctx.get_parameter_source("smooth_iterations") not in (_src.DEFAULT,)
    _user_set_groups = ctx.get_parameter_source("groups_per_vertex") not in (_src.DEFAULT,)
    _user_set_low_vram = ctx.get_parameter_source("low_vram") not in (_src.DEFAULT,)

    _qengine = QualityEngine()
    _qresolved = _qengine.resolve("rigging3d", quality=quality)
    if not _user_set_smooth and "smooth_iterations" in _qresolved.params:
        smooth_iterations = _qresolved.params["smooth_iterations"]
    if not _user_set_groups and "groups_per_vertex" in _qresolved.params:
        groups_per_vertex = _qresolved.params["groups_per_vertex"]
    if not _user_set_low_vram and "low_vram" in _qresolved.params:
        low_vram = bool(_qresolved.params["low_vram"])

    # hw-auto: auto-enable low_vram on small GPUs unless user explicitly passed --low-vram.
    if not _user_set_low_vram and ctx.obj.get("HW_LOW_VRAM", False):
        low_vram = True

    item_id = mesh.stem
    t0 = time.monotonic()

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

        skel = wd / "_skeleton.glb"
        skin = wd / "_skin.glb"
        _low_vram_cleanup: list[Path] = []
        try:
            skel_gpu = gpu_ids[:1] if gpu_ids and len(gpu_ids) >= 2 else gpu_ids
            skin_gpu = gpu_ids[1:2] if gpu_ids and len(gpu_ids) >= 2 else gpu_ids
            if gpu_ids and len(gpu_ids) >= 2:
                console.print(f"[dim]Multi-GPU: skeleton→cuda:{gpu_ids[0]}, skin→cuda:{gpu_ids[1]}[/dim]")

            emit_progress(item_id, TOOL_RIGGING3D, phase="skeleton", percent=0)
            _old_cuda = os.environ.get("CUDA_VISIBLE_DEVICES")
            try:
                if skel_gpu:
                    os.environ["CUDA_VISIBLE_DEVICES"] = ",".join(str(g) for g in skel_gpu)
                elif _old_cuda is not None:
                    os.environ.pop("CUDA_VISIBLE_DEVICES", None)
                run_skeleton_inprocess(
                    root,
                    input_path=_shell_path(actual_mesh),
                    output_path=_shell_path(skel),
                    seed=seed if seed is not None else 123,
                    npz_dir=str(wd / "_npz"),
                )
            except Exception as exc:
                raise click.ClickException(
                    f"skeleton falhou: {exc}. Confirma deps inferência, pesos HF e logs acima."
                ) from exc
            finally:
                if _old_cuda is not None:
                    os.environ["CUDA_VISIBLE_DEVICES"] = _old_cuda
                else:
                    os.environ.pop("CUDA_VISIBLE_DEVICES", None)
            if not skel.is_file() or skel.stat().st_size == 0:
                emit_result(
                    item_id,
                    TOOL_RIGGING3D,
                    STATUS_ERROR,
                    phase="skeleton",
                    error="skeleton falhou (GLB em falta)",
                    seconds=time.monotonic() - t0,
                )
                raise click.ClickException(
                    "skeleton falhou (GLB em falta). Confirma deps inferência, pesos HF e logs acima."
                )
            emit_progress(item_id, TOOL_RIGGING3D, phase="skeleton", percent=100)

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

            emit_progress(item_id, TOOL_RIGGING3D, phase="skin", percent=0)
            _old_cuda_skin = os.environ.get("CUDA_VISIBLE_DEVICES")
            try:
                if skin_gpu:
                    os.environ["CUDA_VISIBLE_DEVICES"] = ",".join(str(g) for g in skin_gpu)
                elif _old_cuda_skin is not None:
                    os.environ.pop("CUDA_VISIBLE_DEVICES", None)
                run_skin_inprocess(
                    root,
                    input_path=_shell_path(skel),
                    output_path=_shell_path(skin),
                    seed=seed if seed is not None else 123,
                    skin_task=skin_task_path,
                    npz_dir=str(wd / "_npz"),
                )
            except Exception as exc:
                raise click.ClickException(f"skin falhou: {exc}. Confirma spconv, VRAM e logs acima.") from exc
            finally:
                if _old_cuda_skin is not None:
                    os.environ["CUDA_VISIBLE_DEVICES"] = _old_cuda_skin
                else:
                    os.environ.pop("CUDA_VISIBLE_DEVICES", None)
            if not skin.is_file() or skin.stat().st_size == 0:
                emit_result(
                    item_id,
                    TOOL_RIGGING3D,
                    STATUS_ERROR,
                    phase="skin",
                    error="skin falhou (GLB em falta)",
                    seconds=time.monotonic() - t0,
                )
                raise click.ClickException("skin falhou (GLB em falta). Confirma spconv, VRAM e logs acima.")
            emit_progress(item_id, TOOL_RIGGING3D, phase="skin", percent=100)

            emit_progress(item_id, TOOL_RIGGING3D, phase="merge", percent=0)
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
                emit_result(
                    item_id,
                    TOOL_RIGGING3D,
                    STATUS_ERROR,
                    phase="merge",
                    error=f"merge falhou (código {rc})",
                    seconds=time.monotonic() - t0,
                )
                raise click.ClickException(f"merge falhou (código {rc} ou GLB vazio). Confirma bpy e caminhos acima.")
            if rc != 0:
                console.print(f"[yellow]merge rc={rc}, output={out.stat().st_size}B- prosseguindo.[/yellow]")
            emit_progress(item_id, TOOL_RIGGING3D, phase="merge", percent=100)
            renamed = _rename_generic_bones(out, root)
            if renamed:
                console.print(f"[green]Renomeados {renamed} ossos para nomes semânticos (humanoid).[/green]")
            _validate_and_fix_origin(out)
        finally:
            if cleanup is not None and not keep_temp:
                shutil.rmtree(cleanup, ignore_errors=True)
            elif low_vram and not keep_temp:
                for p in _low_vram_cleanup:
                    p.unlink(missing_ok=True)

    console.print(f"[green]Pipeline concluído:[/green] {out}")
    emit_result(item_id, TOOL_RIGGING3D, STATUS_OK, output=str(out), seconds=time.monotonic() - t0)


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


@cli.command("transfer-weights")
@click.option(
    "--source",
    "-s",
    "source_glb",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="GLB rigged high-poly (saída de ``rigging3d pipeline`` sobre _clean.glb).",
)
@click.option(
    "--target",
    "-t",
    "targets",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    multiple=True,
    required=True,
    help="GLB(s) target(s) — use múltiplas vezes para LOD0/1/2.",
)
@click.option(
    "--output",
    "-o",
    "outputs",
    type=click.Path(dir_okay=False, path_type=Path),
    multiple=True,
    default=None,
    help=(
        "Caminhos explícitos de output (1:1 com --target). Se omitido, escreve "
        "ao lado de cada target com sufixo ``_rigged``."
    ),
)
@click.option(
    "--output-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Pasta de saída comum quando --output não é especificado.",
)
@click.option(
    "--output-suffix",
    type=str,
    default="_rigged",
    show_default=True,
    help="Sufixo aplicado ao stem do target quando --output não é especificado.",
)
@click.option(
    "--finish/--no-finish",
    default=True,
    show_default=True,
    help="Round 2: aplica gltf_transform_finish (dedup+prune+uastc+meshopt+tangents) aos outputs.",
)
def transfer_weights_cmd(
    source_glb: Path,
    targets: tuple[Path, ...],
    outputs: tuple[Path, ...],
    output_dir: Path | None,
    output_suffix: str,
    finish: bool,
) -> None:
    """Stage 8 — transfere skin weights do source rigged para LOD0/1/2.

    Usa ``bpy.ops.object.data_transfer`` (POLYINTERP_NEAREST) e ata cada
    target ao mesmo armature do source. Ideal para reaproveitar um rig
    high-fidelity (gerado em ``id_clean.glb`` via ``rigging3d pipeline``)
    em meshes decimadas (LOD0/1/2).
    """
    from .transfer_weights import transfer_weights

    out_list: list[Path] | None = list(outputs) if outputs else None
    if out_list is not None and len(out_list) != len(targets):
        raise click.UsageError("Número de --output deve coincidir com o de --target.")

    try:
        results = transfer_weights(
            source_glb,
            list(targets),
            output_dir=output_dir,
            output_suffix=output_suffix,
            targets_out=out_list,
            apply_finish=finish,
        )
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc

    for r in results:
        try:
            sz = r.target_out.stat().st_size
            sz_str = f"{sz / 1024:.0f} KB" if sz < 1024 * 1024 else f"{sz / (1024 * 1024):.2f} MB"
        except OSError:
            sz_str = "?"
        console.print(
            f"[bold green]✓[/bold green] transfer-weights → "
            f"[cyan]{r.target_out}[/cyan] [dim]({sz_str}, "
            f"{r.bones} bones, {r.vertex_groups} vgroups)[/dim]"
        )


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
