"""Animator3D — CLI principal."""

from __future__ import annotations

import dataclasses
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

from aigamekit_shared.anim_packs import AnimPackError, expand_anim_packs
from aigamekit_shared.progress import STATUS_ERROR, STATUS_OK, TOOL_ANIMATOR3D, emit_progress, emit_result
from rich.console import Console
from rich.json import JSON
from rich.table import Table

from . import __version__
from .cli_rich import click

console = Console()


def _draco_option(func):
    return click.option(
        "--draco/--no-draco",
        default=False,
        show_default=True,
        help="Comprimir meshes do GLB com Draco (menor tamanho, compatibilidade limitada).",
    )(func)


def _clip_name_or_default(clip_name: str | None, default: str) -> str:
    """Nome do clip no glTF; caracteres seguros para motores de jogo."""
    if clip_name is None or not str(clip_name).strip():
        return default
    s = str(clip_name).strip()
    if len(s) > 64:
        raise click.ClickException("--clip-name: usa no máximo 64 caracteres")
    return s


def _require_bpy() -> None:
    try:
        import bpy  # noqa: F401
    except ImportError as e:
        raise click.ClickException(
            "O módulo `bpy` não está disponível. Instala: `pip install -e .` dentro de Animator3D "
            "(requer wheel `bpy` compatível com o teu Python; ver README)."
        ) from e


def _validate_anim_pack_opt(_ctx: click.Context, _param: click.Parameter, value: str) -> str:
    """Valida a gramática composta de ``--anim-pack`` cedo (erro de uso, não de execução)."""
    try:
        expand_anim_packs(value)
    except AnimPackError as e:
        raise click.BadParameter(str(e)) from e
    return str(value).strip().lower()


@click.group()
@click.version_option(version=__version__, prog_name="animator3d")
def main() -> None:
    """Ferramentas de animação 3D com Blender (bpy) — complementa Rigging3D."""


@main.command("check")
def cmd_check() -> None:
    """Mostra versão do Blender/bpy e confirma que o runtime está funcional."""
    _require_bpy()
    from . import bpy_ops

    info = bpy_ops.inspect_scene()
    console.print("[green]bpy OK[/green]")
    console.print(f"  Blender: {info['blender_version']}")
    console.print(f"  FPS cena: {info['fps']}")


@main.command("inspect")
@click.argument("input_path", type=click.Path(path_type=Path, exists=True))
@click.option("--json-out", "json_out", is_flag=True, help="Saída em JSON (stdout).")
def cmd_inspect(input_path: Path, json_out: bool) -> None:
    """Importa um GLB/GLTF/FBX e lista armatures, ossos (amostra) e acções."""
    _require_bpy()
    from . import bpy_ops

    bpy_ops.clear_scene()
    bpy_ops.import_asset(input_path)
    data = bpy_ops.inspect_scene()
    if json_out:
        sys.stdout.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    else:
        console.print(JSON.from_data(data))


@main.command("export")
@_draco_option
@click.argument("input_path", type=click.Path(path_type=Path, exists=True))
@click.argument("output_path", type=click.Path(path_type=Path))
def cmd_export(input_path: Path, output_path: Path, draco: bool) -> None:
    """Importa o ficheiro e exporta de novo (útil para validar roundtrip GLB/FBX)."""
    _require_bpy()
    from . import bpy_ops

    bpy_ops.clear_scene()
    bpy_ops.import_asset(input_path)
    bpy_ops.export_auto(output_path, draco=draco)
    console.print(f"[green]Exportado:[/green] {output_path.resolve()}")


@main.command("wave-idle")
@_draco_option
@click.argument("input_path", type=click.Path(path_type=Path, exists=True))
@click.argument("output_path", type=click.Path(path_type=Path))
@click.option("--frames", default=60, show_default=True, type=int, help="Número de frames da animação.")
@click.option("--bone", default=None, type=str, help="Nome do osso (omite para heurística).")
@click.option(
    "--append/--no-append",
    "append_mode",
    default=True,
    show_default=True,
    help="Com --append (defeito), mantém animações já no GLB e acrescenta este clip. "
    "--no-append apaga todas as animações antes de gravar só esta.",
)
@click.option(
    "--clip-name",
    "clip_name",
    default=None,
    type=str,
    help="Nome da animação no glTF (defeito: Animator3D_WaveIdle).",
)
def cmd_wave_idle(
    input_path: Path,
    output_path: Path,
    frames: int,
    bone: str | None,
    append_mode: bool,
    clip_name: str | None,
    draco: bool,
) -> None:
    """Cria uma animação de teste (oscilação) no primeiro armature e exporta."""
    _require_bpy()
    from . import bpy_ops

    if frames < 2:
        raise click.ClickException("--frames deve ser >= 2")

    bpy_ops.clear_scene()
    bpy_ops.import_asset(input_path)
    arms = bpy_ops.list_armatures()
    if not arms:
        raise click.ClickException("Nenhum armature encontrado no ficheiro.")
    arm_name = arms[0].name
    bpy_ops.rename_bones_from_chains(arm_name)
    if not append_mode:
        bpy_ops.clear_armature_animations(arm_name)
    bone_name = bone or bpy_ops.pick_demo_bone(arm_name)
    if not bone_name:
        raise click.ClickException("Não foi possível escolher um osso para animar.")

    bpy_ops.wave_idle_keyframes(
        arm_name,
        bone_name,
        frame_start=1,
        frame_end=frames,
        action_name=_clip_name_or_default(clip_name, "Animator3D_WaveIdle"),
    )
    nclips = bpy_ops.count_nla_tracks(arm_name)
    bpy_ops.export_auto(output_path, draco=draco)
    console.print(
        f"[green]Animado[/green] armature={arm_name!r} osso={bone_name!r} "
        f"· {nclips} clip(s) no GLB → {output_path.resolve()}"
    )


@main.command("breathe-idle")
@_draco_option
@click.argument("input_path", type=click.Path(path_type=Path, exists=True))
@click.argument("output_path", type=click.Path(path_type=Path))
@click.option("--frames", default=120, show_default=True, type=int, help="Número de frames da animação.")
@click.option("--cycles", default=2.0, show_default=True, type=float, help="Ciclos de respiração no intervalo.")
@click.option("--wing-amp", "wing_amp", default=0.25, show_default=True, type=float, help="Amplitude asas (rad).")
@click.option("--tail-amp", "tail_amp", default=0.15, show_default=True, type=float, help="Amplitude cauda (rad).")
@click.option("--neck-amp", "neck_amp", default=0.10, show_default=True, type=float, help="Amplitude pescoço (rad).")
@click.option(
    "--append/--no-append",
    "append_mode",
    default=True,
    show_default=True,
    help="Mantém clips existentes no GLB e acrescenta Animator3D_BreatheIdle (defeito). "
    "--no-append remove todas as animações antes.",
)
@click.option(
    "--clip-name",
    "clip_name",
    default=None,
    type=str,
    help="Nome da animação no glTF (defeito: Animator3D_BreatheIdle).",
)
def cmd_breathe_idle(
    input_path: Path,
    output_path: Path,
    frames: int,
    cycles: float,
    wing_amp: float,
    tail_amp: float,
    neck_amp: float,
    append_mode: bool,
    clip_name: str | None,
    draco: bool,
) -> None:
    """Animação idle multi-osso: respiração, asas, cauda, pescoço — classifica ossos automaticamente."""
    _require_bpy()
    from . import bpy_ops

    if frames < 2:
        raise click.ClickException("--frames deve ser >= 2")

    bpy_ops.clear_scene()
    bpy_ops.import_asset(input_path)
    arms = bpy_ops.list_armatures()
    if not arms:
        raise click.ClickException("Nenhum armature encontrado no ficheiro.")
    arm_name = arms[0].name
    bpy_ops.rename_bones_from_chains(arm_name)
    if not append_mode:
        bpy_ops.clear_armature_animations(arm_name)

    chains = bpy_ops.breathe_idle_keyframes(
        arm_name,
        frame_start=1,
        frame_end=frames,
        cycles=cycles,
        wing_amp=wing_amp,
        tail_amp=tail_amp,
        neck_amp=neck_amp,
        action_name=_clip_name_or_default(clip_name, "Animator3D_BreatheIdle"),
    )
    nclips = bpy_ops.count_nla_tracks(arm_name)
    bpy_ops.export_auto(output_path, draco=draco)

    chain_info = ", ".join(f"{k}({len(v)})" for k, v in chains.items())
    console.print(
        f"[green]Animado[/green] armature={arm_name!r} cadeias=\\[{chain_info}] "
        f"· {nclips} clip(s) no GLB → {output_path.resolve()}"
    )


@main.command("attack")
@_draco_option
@click.argument("input_path", type=click.Path(path_type=Path, exists=True))
@click.argument("output_path", type=click.Path(path_type=Path))
@click.option("--frames", default=72, show_default=True, type=int, help="Duracao do clip (1 ou mais golpes).")
@click.option(
    "--strikes", default=1, show_default=True, type=int, help="Numero de golpes no mesmo clip (repete o perfil)."
)
@click.option("--wing-amp", "wing_amp", default=0.62, show_default=True, type=float, help="Amplitude asas (rad).")
@click.option(
    "--neck-amp", "neck_amp", default=0.55, show_default=True, type=float, help="Amplitude pescoço / mordida (rad)."
)
@click.option(
    "--tail-amp", "tail_amp", default=0.42, show_default=True, type=float, help="Amplitude cauda contrabalanco (rad)."
)
@click.option(
    "--append/--no-append",
    "append_mode",
    default=True,
    show_default=True,
    help="Mantém clips existentes e acrescenta Animator3D_Attack (defeito). --no-append apaga animações antes.",
)
@click.option(
    "--clip-name",
    "clip_name",
    default=None,
    type=str,
    help="Nome da animação no glTF (defeito: Animator3D_Attack).",
)
def cmd_attack(
    input_path: Path,
    output_path: Path,
    frames: int,
    strikes: int,
    wing_amp: float,
    neck_amp: float,
    tail_amp: float,
    append_mode: bool,
    clip_name: str | None,
    draco: bool,
) -> None:
    """Investida / mordida: tronco e pescoço para a frente, asas à frente, cauda em contrapeso (patas fixas)."""
    _require_bpy()
    from . import bpy_ops

    if frames < 2:
        raise click.ClickException("--frames deve ser >= 2")
    if strikes < 1:
        raise click.ClickException("--strikes deve ser >= 1")

    bpy_ops.clear_scene()
    bpy_ops.import_asset(input_path)
    arms = bpy_ops.list_armatures()
    if not arms:
        raise click.ClickException("Nenhum armature encontrado no ficheiro.")
    arm_name = arms[0].name
    bpy_ops.rename_bones_from_chains(arm_name)
    if not append_mode:
        bpy_ops.clear_armature_animations(arm_name)

    chains = bpy_ops.attack_keyframes(
        arm_name,
        frame_start=1,
        frame_end=frames,
        strikes=strikes,
        wing_amp=wing_amp,
        neck_amp=neck_amp,
        tail_amp=tail_amp,
        action_name=_clip_name_or_default(clip_name, "Animator3D_Attack"),
    )
    nclips = bpy_ops.count_nla_tracks(arm_name)
    bpy_ops.export_auto(output_path, draco=draco)

    chain_info = ", ".join(f"{k}({len(v)})" for k, v in chains.items())
    console.print(
        f"[green]Ataque[/green] armature={arm_name!r} cadeias=\\[{chain_info}] "
        f"· {nclips} clip(s) no GLB → {output_path.resolve()}"
    )


@main.command("walk")
@_draco_option
@click.argument("input_path", type=click.Path(path_type=Path, exists=True))
@click.argument("output_path", type=click.Path(path_type=Path))
@click.option("--frames", default=48, show_default=True, type=int, help="Duração do ciclo de passada.")
@click.option("--cycles", default=2.0, show_default=True, type=float, help="Ciclos de passada no intervalo.")
@click.option("--leg-amp", "leg_amp", default=0.14, show_default=True, type=float, help="Amplitude patas (rad).")
@click.option(
    "--append/--no-append",
    "append_mode",
    default=True,
    show_default=True,
    help="Acrescenta ao GLB sem apagar clips anteriores (defeito).",
)
@click.option(
    "--clip-name",
    "clip_name",
    default=None,
    type=str,
    help="Nome da animação no glTF (defeito: Animator3D_Walk).",
)
def cmd_walk(
    input_path: Path,
    output_path: Path,
    frames: int,
    cycles: float,
    leg_amp: float,
    append_mode: bool,
    clip_name: str | None,
    draco: bool,
) -> None:
    """Ciclo de caminhada: patas alternadas (se o rig tiver), tronco e cauda."""
    _require_bpy()
    from . import bpy_ops

    if frames < 2:
        raise click.ClickException("--frames deve ser >= 2")

    bpy_ops.clear_scene()
    bpy_ops.import_asset(input_path)
    arms = bpy_ops.list_armatures()
    if not arms:
        raise click.ClickException("Nenhum armature encontrado no ficheiro.")
    arm_name = arms[0].name
    bpy_ops.rename_bones_from_chains(arm_name)
    if not append_mode:
        bpy_ops.clear_armature_animations(arm_name)

    chains = bpy_ops.walk_cycle_keyframes(
        arm_name,
        frame_start=1,
        frame_end=frames,
        cycles=cycles,
        leg_amp=leg_amp,
        action_name=_clip_name_or_default(clip_name, "Animator3D_Walk"),
    )
    nclips = bpy_ops.count_nla_tracks(arm_name)
    bpy_ops.export_auto(output_path, draco=draco)
    chain_info = ", ".join(f"{k}({len(v)})" for k, v in chains.items())
    console.print(
        f"[green]Walk[/green] armature={arm_name!r} cadeias=\\[{chain_info}] "
        f"· {nclips} clip(s) no GLB → {output_path.resolve()}"
    )


@main.command("hover")
@_draco_option
@click.argument("input_path", type=click.Path(path_type=Path, exists=True))
@click.argument("output_path", type=click.Path(path_type=Path))
@click.option("--frames", default=60, show_default=True, type=int, help="Duração do clip.")
@click.option("--cycles", default=3.5, show_default=True, type=float, help="Batidas de asa por loop.")
@click.option("--wing-amp", "wing_amp", default=0.38, show_default=True, type=float, help="Amplitude asas (rad).")
@click.option(
    "--append/--no-append",
    "append_mode",
    default=True,
    show_default=True,
    help="Acrescenta ao GLB sem apagar clips anteriores (defeito).",
)
@click.option(
    "--clip-name",
    "clip_name",
    default=None,
    type=str,
    help="Nome da animação no glTF (defeito: Animator3D_Hover).",
)
def cmd_hover(
    input_path: Path,
    output_path: Path,
    frames: int,
    cycles: float,
    wing_amp: float,
    append_mode: bool,
    clip_name: str | None,
    draco: bool,
) -> None:
    """Pairar: batimento de asas rápido, tronco estável."""
    _require_bpy()
    from . import bpy_ops

    if frames < 2:
        raise click.ClickException("--frames deve ser >= 2")

    bpy_ops.clear_scene()
    bpy_ops.import_asset(input_path)
    arms = bpy_ops.list_armatures()
    if not arms:
        raise click.ClickException("Nenhum armature encontrado no ficheiro.")
    arm_name = arms[0].name
    bpy_ops.rename_bones_from_chains(arm_name)
    if not append_mode:
        bpy_ops.clear_armature_animations(arm_name)

    chains = bpy_ops.hover_flap_keyframes(
        arm_name,
        frame_start=1,
        frame_end=frames,
        cycles=cycles,
        wing_amp=wing_amp,
        action_name=_clip_name_or_default(clip_name, "Animator3D_Hover"),
    )
    nclips = bpy_ops.count_nla_tracks(arm_name)
    bpy_ops.export_auto(output_path, draco=draco)
    chain_info = ", ".join(f"{k}({len(v)})" for k, v in chains.items())
    console.print(
        f"[green]Hover[/green] armature={arm_name!r} cadeias=\\[{chain_info}] "
        f"· {nclips} clip(s) no GLB → {output_path.resolve()}"
    )


@main.command("soar")
@_draco_option
@click.argument("input_path", type=click.Path(path_type=Path, exists=True))
@click.argument("output_path", type=click.Path(path_type=Path))
@click.option("--frames", default=90, show_default=True, type=int, help="Duração do clip de plano.")
@click.option("--cycles", default=1.5, show_default=True, type=float, help="Ciclos de batida lenta.")
@click.option(
    "--append/--no-append",
    "append_mode",
    default=True,
    show_default=True,
    help="Acrescenta ao GLB sem apagar clips anteriores.",
)
@click.option(
    "--clip-name",
    "clip_name",
    default=None,
    type=str,
    help="Nome da animação no glTF (defeito: Animator3D_Soar).",
)
def cmd_soar(
    input_path: Path,
    output_path: Path,
    frames: int,
    cycles: float,
    append_mode: bool,
    clip_name: str | None,
    draco: bool,
) -> None:
    """Planar majestoso: batidas de asa largas e lentas, cauda como leme."""
    _require_bpy()
    from . import bpy_ops

    if frames < 2:
        raise click.ClickException("--frames deve ser >= 2")

    bpy_ops.clear_scene()
    bpy_ops.import_asset(input_path)
    arms = bpy_ops.list_armatures()
    if not arms:
        raise click.ClickException("Nenhum armature encontrado no ficheiro.")
    arm_name = arms[0].name
    bpy_ops.rename_bones_from_chains(arm_name)
    if not append_mode:
        bpy_ops.clear_armature_animations(arm_name)

    chains = bpy_ops.soar_keyframes(
        arm_name,
        frame_start=1,
        frame_end=frames,
        cycles=cycles,
        action_name=_clip_name_or_default(clip_name, "Animator3D_Soar"),
    )
    nclips = bpy_ops.count_nla_tracks(arm_name)
    bpy_ops.export_auto(output_path, draco=draco)
    chain_info = ", ".join(f"{k}({len(v)})" for k, v in chains.items())
    console.print(
        f"[green]Soar[/green] armature={arm_name!r} cadeias=\\[{chain_info}] "
        f"· {nclips} clip(s) no GLB → {output_path.resolve()}"
    )


@main.command("dive")
@_draco_option
@click.argument("input_path", type=click.Path(path_type=Path, exists=True))
@click.argument("output_path", type=click.Path(path_type=Path))
@click.option("--frames", default=48, show_default=True, type=int, help="Duração do mergulho.")
@click.option(
    "--append/--no-append",
    "append_mode",
    default=True,
    show_default=True,
    help="Acrescenta ao GLB sem apagar clips anteriores.",
)
@click.option(
    "--clip-name",
    "clip_name",
    default=None,
    type=str,
    help="Nome da animação no glTF (defeito: Animator3D_DiveAttack).",
)
def cmd_dive(
    input_path: Path,
    output_path: Path,
    frames: int,
    append_mode: bool,
    clip_name: str | None,
    draco: bool,
) -> None:
    """Ataque em picada: asas recolhidas, mergulho e impacto brusco."""
    _require_bpy()
    from . import bpy_ops

    if frames < 2:
        raise click.ClickException("--frames deve ser >= 2")

    bpy_ops.clear_scene()
    bpy_ops.import_asset(input_path)
    arms = bpy_ops.list_armatures()
    if not arms:
        raise click.ClickException("Nenhum armature encontrado no ficheiro.")
    arm_name = arms[0].name
    bpy_ops.rename_bones_from_chains(arm_name)
    if not append_mode:
        bpy_ops.clear_armature_animations(arm_name)

    chains = bpy_ops.dive_attack_keyframes(
        arm_name,
        frame_start=1,
        frame_end=frames,
        action_name=_clip_name_or_default(clip_name, "Animator3D_DiveAttack"),
    )
    nclips = bpy_ops.count_nla_tracks(arm_name)
    bpy_ops.export_auto(output_path, draco=draco)
    chain_info = ", ".join(f"{k}({len(v)})" for k, v in chains.items())
    console.print(
        f"[green]Dive[/green] armature={arm_name!r} cadeias=\\[{chain_info}] "
        f"· {nclips} clip(s) no GLB → {output_path.resolve()}"
    )


@main.command("fire")
@_draco_option
@click.argument("input_path", type=click.Path(path_type=Path, exists=True))
@click.argument("output_path", type=click.Path(path_type=Path))
@click.option("--frames", default=64, show_default=True, type=int, help="Duração do sopro.")
@click.option("--bursts", default=2, show_default=True, type=int, help="Número de rajadas de fogo.")
@click.option(
    "--append/--no-append",
    "append_mode",
    default=True,
    show_default=True,
    help="Acrescenta ao GLB sem apagar clips anteriores.",
)
@click.option(
    "--clip-name",
    "clip_name",
    default=None,
    type=str,
    help="Nome da animação no glTF (defeito: Animator3D_FireBreath).",
)
def cmd_fire(
    input_path: Path,
    output_path: Path,
    frames: int,
    bursts: int,
    append_mode: bool,
    clip_name: str | None,
    draco: bool,
) -> None:
    """Sopro de fogo: peito expande, pescoço avança, rajadas poderosas."""
    _require_bpy()
    from . import bpy_ops

    if frames < 2:
        raise click.ClickException("--frames deve ser >= 2")
    if bursts < 1:
        raise click.ClickException("--bursts deve ser >= 1")

    bpy_ops.clear_scene()
    bpy_ops.import_asset(input_path)
    arms = bpy_ops.list_armatures()
    if not arms:
        raise click.ClickException("Nenhum armature encontrado no ficheiro.")
    arm_name = arms[0].name
    bpy_ops.rename_bones_from_chains(arm_name)
    if not append_mode:
        bpy_ops.clear_armature_animations(arm_name)

    chains = bpy_ops.fire_breath_keyframes(
        arm_name,
        frame_start=1,
        frame_end=frames,
        bursts=bursts,
        action_name=_clip_name_or_default(clip_name, "Animator3D_FireBreath"),
    )
    nclips = bpy_ops.count_nla_tracks(arm_name)
    bpy_ops.export_auto(output_path, draco=draco)
    chain_info = ", ".join(f"{k}({len(v)})" for k, v in chains.items())
    console.print(
        f"[green]Fire[/green] armature={arm_name!r} cadeias=\\[{chain_info}] "
        f"· {nclips} clip(s) no GLB → {output_path.resolve()}"
    )


@main.command("land")
@_draco_option
@click.argument("input_path", type=click.Path(path_type=Path, exists=True))
@click.argument("output_path", type=click.Path(path_type=Path))
@click.option("--frames", default=80, show_default=True, type=int, help="Duração do pouso.")
@click.option(
    "--append/--no-append",
    "append_mode",
    default=True,
    show_default=True,
    help="Acrescenta ao GLB sem apagar clips anteriores.",
)
@click.option(
    "--clip-name",
    "clip_name",
    default=None,
    type=str,
    help="Nome da animação no glTF (defeito: Animator3D_Land).",
)
def cmd_land(
    input_path: Path,
    output_path: Path,
    frames: int,
    append_mode: bool,
    clip_name: str | None,
    draco: bool,
) -> None:
    """Pouso majestoso: descida controlada, freio aerodinâmico, impacto suave."""
    _require_bpy()
    from . import bpy_ops

    if frames < 2:
        raise click.ClickException("--frames deve ser >= 2")

    bpy_ops.clear_scene()
    bpy_ops.import_asset(input_path)
    arms = bpy_ops.list_armatures()
    if not arms:
        raise click.ClickException("Nenhum armature encontrado no ficheiro.")
    arm_name = arms[0].name
    bpy_ops.rename_bones_from_chains(arm_name)
    if not append_mode:
        bpy_ops.clear_armature_animations(arm_name)

    chains = bpy_ops.land_keyframes(
        arm_name,
        frame_start=1,
        frame_end=frames,
        action_name=_clip_name_or_default(clip_name, "Animator3D_Land"),
    )
    nclips = bpy_ops.count_nla_tracks(arm_name)
    bpy_ops.export_auto(output_path, draco=draco)
    chain_info = ", ".join(f"{k}({len(v)})" for k, v in chains.items())
    console.print(
        f"[green]Land[/green] armature={arm_name!r} cadeias=\\[{chain_info}] "
        f"· {nclips} clip(s) no GLB → {output_path.resolve()}"
    )


@main.command("roar")
@_draco_option
@click.argument("input_path", type=click.Path(path_type=Path, exists=True))
@click.argument("output_path", type=click.Path(path_type=Path))
@click.option("--frames", default=96, show_default=True, type=int, help="Duração do rugido.")
@click.option(
    "--append/--no-append",
    "append_mode",
    default=True,
    show_default=True,
    help="Acrescenta ao GLB sem apagar clips anteriores.",
)
@click.option(
    "--clip-name",
    "clip_name",
    default=None,
    type=str,
    help="Nome da animação no glTF (defeito: Animator3D_VictoryRoar).",
)
def cmd_roar(
    input_path: Path,
    output_path: Path,
    frames: int,
    append_mode: bool,
    clip_name: str | None,
    draco: bool,
) -> None:
    """Rugido de vitória: peito inflado, cabeça erguida, pose majestosa."""
    _require_bpy()
    from . import bpy_ops

    if frames < 2:
        raise click.ClickException("--frames deve ser >= 2")

    bpy_ops.clear_scene()
    bpy_ops.import_asset(input_path)
    arms = bpy_ops.list_armatures()
    if not arms:
        raise click.ClickException("Nenhum armature encontrado no ficheiro.")
    arm_name = arms[0].name
    bpy_ops.rename_bones_from_chains(arm_name)
    if not append_mode:
        bpy_ops.clear_armature_animations(arm_name)

    chains = bpy_ops.victory_roar_keyframes(
        arm_name,
        frame_start=1,
        frame_end=frames,
        action_name=_clip_name_or_default(clip_name, "Animator3D_VictoryRoar"),
    )
    nclips = bpy_ops.count_nla_tracks(arm_name)
    bpy_ops.export_auto(output_path, draco=draco)
    chain_info = ", ".join(f"{k}({len(v)})" for k, v in chains.items())
    console.print(
        f"[green]Roar[/green] armature={arm_name!r} cadeias=\\[{chain_info}] "
        f"· {nclips} clip(s) no GLB → {output_path.resolve()}"
    )


@main.command("run")
@_draco_option
@click.argument("input_path", type=click.Path(path_type=Path, exists=True))
@click.argument("output_path", type=click.Path(path_type=Path))
@click.option("--frames", default=36, show_default=True, type=int, help="Duração do ciclo de corrida.")
@click.option("--cycles", default=2.0, show_default=True, type=float, help="Ciclos de corrida no intervalo.")
@click.option("--leg-amp", "leg_amp", default=0.22, show_default=True, type=float, help="Amplitude pernas (rad).")
@click.option(
    "--append/--no-append",
    "append_mode",
    default=True,
    show_default=True,
    help="Acrescenta ao GLB sem apagar clips anteriores (defeito).",
)
@click.option("--clip-name", "clip_name", default=None, type=str, help="Nome da animação no glTF.")
def cmd_run(
    input_path: Path,
    output_path: Path,
    frames: int,
    cycles: float,
    leg_amp: float,
    append_mode: bool,
    clip_name: str | None,
    draco: bool,
) -> None:
    """Ciclo de corrida: cadência rápida, amplitude alta, balanço de braços."""
    _require_bpy()
    from . import bpy_ops

    if frames < 2:
        raise click.ClickException("--frames deve ser >= 2")

    bpy_ops.clear_scene()
    bpy_ops.import_asset(input_path)
    arms = bpy_ops.list_armatures()
    if not arms:
        raise click.ClickException("Nenhum armature encontrado no ficheiro.")
    arm_name = arms[0].name
    bpy_ops.rename_bones_from_chains(arm_name)
    if not append_mode:
        bpy_ops.clear_armature_animations(arm_name)

    chains = bpy_ops.run_cycle_keyframes(
        arm_name,
        frame_start=1,
        frame_end=frames,
        cycles=cycles,
        leg_amp=leg_amp,
        action_name=_clip_name_or_default(clip_name, "Animator3D_Run"),
    )
    nclips = bpy_ops.count_nla_tracks(arm_name)
    bpy_ops.export_auto(output_path, draco=draco)
    chain_info = ", ".join(f"{k}({len(v)})" for k, v in chains.items())
    console.print(
        f"[green]Run[/green] armature={arm_name!r} cadeias=\\[{chain_info}] "
        f"· {nclips} clip(s) no GLB → {output_path.resolve()}"
    )


@main.command("jump")
@_draco_option
@click.argument("input_path", type=click.Path(path_type=Path, exists=True))
@click.argument("output_path", type=click.Path(path_type=Path))
@click.option("--frames", default=36, show_default=True, type=int, help="Duração do salto.")
@click.option(
    "--append/--no-append",
    "append_mode",
    default=True,
    show_default=True,
    help="Acrescenta ao GLB sem apagar clips anteriores (defeito).",
)
@click.option("--clip-name", "clip_name", default=None, type=str, help="Nome da animação no glTF.")
def cmd_jump(
    input_path: Path,
    output_path: Path,
    frames: int,
    append_mode: bool,
    clip_name: str | None,
    draco: bool,
) -> None:
    """Salto: agachar → estender → aéreo → aterrar. Não-cíclico."""
    _require_bpy()
    from . import bpy_ops

    if frames < 2:
        raise click.ClickException("--frames deve ser >= 2")

    bpy_ops.clear_scene()
    bpy_ops.import_asset(input_path)
    arms = bpy_ops.list_armatures()
    if not arms:
        raise click.ClickException("Nenhum armature encontrado no ficheiro.")
    arm_name = arms[0].name
    bpy_ops.rename_bones_from_chains(arm_name)
    if not append_mode:
        bpy_ops.clear_armature_animations(arm_name)

    chains = bpy_ops.jump_keyframes(
        arm_name,
        frame_start=1,
        frame_end=frames,
        action_name=_clip_name_or_default(clip_name, "Animator3D_Jump"),
    )
    nclips = bpy_ops.count_nla_tracks(arm_name)
    bpy_ops.export_auto(output_path, draco=draco)
    chain_info = ", ".join(f"{k}({len(v)})" for k, v in chains.items())
    console.print(
        f"[green]Jump[/green] armature={arm_name!r} cadeias=\\[{chain_info}] "
        f"· {nclips} clip(s) no GLB → {output_path.resolve()}"
    )


@main.command("fall")
@_draco_option
@click.argument("input_path", type=click.Path(path_type=Path, exists=True))
@click.argument("output_path", type=click.Path(path_type=Path))
@click.option("--frames", default=24, show_default=True, type=int, help="Duração da queda.")
@click.option(
    "--append/--no-append",
    "append_mode",
    default=True,
    show_default=True,
    help="Acrescenta ao GLB sem apagar clips anteriores (defeito).",
)
@click.option("--clip-name", "clip_name", default=None, type=str, help="Nome da animação no glTF.")
def cmd_fall(
    input_path: Path,
    output_path: Path,
    frames: int,
    append_mode: bool,
    clip_name: str | None,
    draco: bool,
) -> None:
    """Pose de queda: braços abertos, balanço de vento. Não-cíclico."""
    _require_bpy()
    from . import bpy_ops

    if frames < 2:
        raise click.ClickException("--frames deve ser >= 2")

    bpy_ops.clear_scene()
    bpy_ops.import_asset(input_path)
    arms = bpy_ops.list_armatures()
    if not arms:
        raise click.ClickException("Nenhum armature encontrado no ficheiro.")
    arm_name = arms[0].name
    bpy_ops.rename_bones_from_chains(arm_name)
    if not append_mode:
        bpy_ops.clear_armature_animations(arm_name)

    chains = bpy_ops.fall_keyframes(
        arm_name,
        frame_start=1,
        frame_end=frames,
        action_name=_clip_name_or_default(clip_name, "Animator3D_Fall"),
    )
    nclips = bpy_ops.count_nla_tracks(arm_name)
    bpy_ops.export_auto(output_path, draco=draco)
    chain_info = ", ".join(f"{k}({len(v)})" for k, v in chains.items())
    console.print(
        f"[green]Fall[/green] armature={arm_name!r} cadeias=\\[{chain_info}] "
        f"· {nclips} clip(s) no GLB → {output_path.resolve()}"
    )


# ---------- game-pack: batch animation preset ----------


def _procedural_action_matches_filter(action_name: str, allowed: set[str]) -> bool:
    """Match ``--clips`` against procedural action names without substring traps.

    ``attack`` must match ``Animator3D_Attack`` but not ``Animator3D_AxeAttack`` /
    ``Animator3D_SwordAttack``. ``idle`` still matches ``BreatheIdle``.
    """
    bare = action_name.strip().lower()
    if bare.startswith("animator3d_"):
        bare = bare[len("animator3d_") :]
    for a in allowed:
        if not a:
            continue
        if bare == a:
            return True
        if a == "idle" and bare.endswith("idle"):
            return True
        # Nomes de clip do perfil quaternius-hero: o procedural tem
        # `SwordAttack`/`AxeAttack`, não `sword`/`axe` — sem estes aliases o
        # fallback "graceful" morria com "Nenhum clip corresponde ao filtro".
        if a in ("sword", "axe") and bare == f"{a}attack":
            return True
        # `swordidle`/`axeidle`/… só têm o BreatheIdle procedural.
        if a.endswith("idle") and bare.endswith("idle"):
            return True
        if a == "roar" and "roar" in bare:
            return True
        if a == "punch" and bare == "attack":
            return True
        if a == "dive" and bare.startswith("dive"):
            return True
        if a == "land" and bare == "land":
            return True
        if a == "hover" and bare == "hover":
            return True
        if a == "soar" and bare == "soar":
            return True
        if a == "death" and bare in {"death", "die", "dead"}:
            return True
        if a == "hit" and bare in {"hit", "hurt", "flinch", "react"}:
            return True
    return False


_PRESETS: dict[str, list[tuple[str, dict[str, object]]]] = {
    "humanoid": [
        ("breathe_idle_keyframes", {"frame_end": 72, "action_name": "Animator3D_BreatheIdle"}),
        ("walk_cycle_keyframes", {"frame_end": 48, "action_name": "Animator3D_Walk"}),
        ("run_cycle_keyframes", {"frame_end": 36, "action_name": "Animator3D_Run"}),
        ("jump_keyframes", {"frame_end": 36, "action_name": "Animator3D_Jump"}),
        ("fall_keyframes", {"frame_end": 24, "action_name": "Animator3D_Fall"}),
        ("attack_keyframes", {"frame_end": 28, "action_name": "Animator3D_Attack"}),
        ("turn_in_place_keyframes", {"frame_end": 32, "direction": 1.0, "action_name": "Animator3D_TurnLeft"}),
        ("turn_in_place_keyframes", {"frame_end": 32, "direction": -1.0, "action_name": "Animator3D_TurnRight"}),
        ("mine_keyframes", {"frame_end": 40, "action_name": "Animator3D_Mine"}),
        ("chop_keyframes", {"frame_end": 40, "action_name": "Animator3D_Chop"}),
        ("spear_keyframes", {"frame_end": 34, "action_name": "Animator3D_Spear"}),
        ("axe_keyframes", {"frame_end": 40, "action_name": "Animator3D_AxeAttack"}),
        ("sword_keyframes", {"frame_end": 32, "action_name": "Animator3D_SwordAttack"}),
        ("gather_keyframes", {"frame_end": 40, "action_name": "Animator3D_Gather"}),
    ],
    "creature": [
        ("breathe_idle_keyframes", {"frame_end": 72, "action_name": "Animator3D_BreatheIdle"}),
        ("walk_cycle_keyframes", {"frame_end": 48, "action_name": "Animator3D_Walk"}),
        ("run_cycle_keyframes", {"frame_end": 36, "action_name": "Animator3D_Run"}),
        ("jump_keyframes", {"frame_end": 36, "action_name": "Animator3D_Jump"}),
        ("attack_keyframes", {"frame_end": 48, "action_name": "Animator3D_Attack"}),
        ("turn_in_place_keyframes", {"frame_end": 32, "direction": 1.0, "action_name": "Animator3D_TurnLeft"}),
        ("turn_in_place_keyframes", {"frame_end": 32, "direction": -1.0, "action_name": "Animator3D_TurnRight"}),
        # Fallback procedural (ativos desde a Fase 2.1): criaturas já não ficam
        # sem estes clips. Variações de attack_keyframes por tipo de gesto.
        ("mine_keyframes", {"frame_end": 40, "action_name": "Animator3D_Mine"}),
        ("chop_keyframes", {"frame_end": 40, "action_name": "Animator3D_Chop"}),
        ("spear_keyframes", {"frame_end": 34, "action_name": "Animator3D_Spear"}),
        ("axe_keyframes", {"frame_end": 40, "action_name": "Animator3D_AxeAttack"}),
        ("sword_keyframes", {"frame_end": 32, "action_name": "Animator3D_SwordAttack"}),
        ("gather_keyframes", {"frame_end": 40, "action_name": "Animator3D_Gather"}),
        ("victory_roar_keyframes", {"frame_end": 60, "action_name": "Animator3D_Roar"}),
        # Explicit hit/death so ``--clips idle,...,hit,death`` fills game scripts
        # (mine/chop alone never matched the hit/death filter).
        ("mine_keyframes", {"frame_end": 24, "action_name": "Animator3D_Hit"}),
        ("chop_keyframes", {"frame_end": 48, "action_name": "Animator3D_Death"}),
    ],
    "flying": [
        ("breathe_idle_keyframes", {"frame_end": 72, "action_name": "Animator3D_BreatheIdle"}),
        ("hover_flap_keyframes", {"frame_end": 60, "action_name": "Animator3D_Hover"}),
        ("soar_keyframes", {"frame_end": 48, "action_name": "Animator3D_Soar"}),
        ("dive_attack_keyframes", {"frame_end": 48, "action_name": "Animator3D_Dive"}),
        ("land_keyframes", {"frame_end": 48, "action_name": "Animator3D_Land"}),
    ],
}


@main.command("game-pack")
@_draco_option
@click.argument("input_path", type=click.Path(path_type=Path, exists=True))
@click.argument("output_path", type=click.Path(path_type=Path))
@click.option(
    "--preset",
    type=click.Choice(list(_PRESETS.keys()), case_sensitive=False),
    default="humanoid",
    show_default=True,
    help="Conjunto de animações a gerar (caminho procedural).",
)
@click.option(
    "--clips",
    "clip_filter",
    default=None,
    type=str,
    help=(
        "Clips separados por vírgulas (ex: idle,walk,run), filtrando o preset/perfil. "
        "Catálogo: animator3d list-animations."
    ),
)
@click.option(
    "--force-preset",
    "force_preset",
    is_flag=True,
    default=False,
    help="Desativa a auto-deteção de tipo de rig (humanoid vs creature).",
)
@click.option(
    "--procedural",
    is_flag=True,
    default=False,
    help="Força clips procedurais mesmo em humanoides (sem retarget Quaternius).",
)
@click.option(
    "--anim-pack",
    "anim_pack",
    default="quaternius",
    show_default=True,
    callback=_validate_anim_pack_opt,
    help=(
        "Pack(s) de retarget: quaternius (UAL1), quaternius2 (UAL2, sem "
        "locomoção base), villager (Kevin Iglesias: farming/pesca/mining — "
        "FBX por clip), both (UAL1+UAL2) ou all (villager + UAL1 + UAL2: a "
        "UAL substitui idle/gather e os trabalhos exclusivos do villager "
        "ficam). Aceita lista por vírgulas — a ordem define quem substitui "
        "quem (ex.: both,villager). Catálogo: animator3d list-animations."
    ),
)
def cmd_game_pack(
    input_path: Path,
    output_path: Path,
    preset: str,
    clip_filter: str | None,
    draco: bool,
    force_preset: bool,
    procedural: bool,
    anim_pack: str,
) -> None:
    """Gera todas as animações de um rig num único comando.

    Humanoides: retarget do pack Quaternius (CC0) por defeito — o naming
    canónico dos rigs do pipeline é o do Quaternius, logo o mapeamento é
    directo. ``--anim-pack both`` combina UAL1 + UAL2 (farming, chopping
    dedicado, combos, zombie, climb...); ``--anim-pack villager`` traz os
    trabalhos do Kevin Iglesias (arado, pesca, mining, martelo — pack
    por-ficheiro, EULA gratuita); ``--anim-pack all`` encadeia os três
    (villager primeiro; a UAL substitui os clips partilhados). Criaturas
    (aranha, mosquito, ...) e rigs sem cobertura caem automaticamente no
    caminho procedural (presets).
    """
    item_id = input_path.stem
    t0 = time.monotonic()

    emit_progress(item_id, TOOL_ANIMATOR3D, phase="loading_bpy", percent=0)

    _require_bpy()
    from . import bpy_ops

    emit_progress(item_id, TOOL_ANIMATOR3D, phase="loading_bpy", percent=50)

    bpy_ops.clear_scene()
    bpy_ops.import_asset(input_path)
    arms = bpy_ops.list_armatures()
    if not arms:
        elapsed = time.monotonic() - t0
        emit_result(
            item_id,
            TOOL_ANIMATOR3D,
            STATUS_ERROR,
            error="Nenhum armature encontrado no ficheiro.",
            seconds=elapsed,
        )
        raise click.ClickException("Nenhum armature encontrado no ficheiro.")
    arm_name = arms[0].name

    emit_progress(item_id, TOOL_ANIMATOR3D, phase="loading_bpy", percent=100)

    # Auto-deteção de tipo de rig: se o preset é humanoid (default) mas o rig
    # não é humanoid (criatura), muda automaticamente para preset creature com
    # aviso visível. --force-preset desativa (preserva comportamento antigo).
    if not force_preset and preset.lower() == "humanoid":
        from .humanoid import HumanoidRig

        detected_chains = bpy_ops._classify_bone_chains(arm_name)
        if not HumanoidRig.is_humanoid(detected_chains):
            console.print(
                "[yellow]Auto-deteção:[/yellow] rig não-humanoidize detectado "
                f"({len(detected_chains.get('leg_r', []))} pernas dir, "
                f"{len(detected_chains.get('arm_r', []))} braços dir) — "
                "mudando para preset [cyan]creature[/cyan]. "
                "Use --force-preset para manter humanoid."
            )
            preset = "creature"

    # Caminho primário (humanoides): retarget do pack Quaternius.
    if preset.lower() == "humanoid" and not procedural:
        done = _game_pack_quaternius_retarget(
            item_id, arm_name, output_path, clip_filter=clip_filter, draco=draco, t0=t0, anim_pack=anim_pack.lower()
        )
        if done:
            return
        console.print("[yellow]Fallback:[/yellow] a gerar clips procedurais.")

    # Caminho procedural (criaturas / fallback): renomear chains e aplicar preset.
    bpy_ops.rename_bones_from_chains(arm_name)

    steps = _PRESETS[preset.lower()]

    if clip_filter:
        allowed = {s.strip().lower() for s in clip_filter.split(",") if s.strip()}
        steps = [(fn, kw) for fn, kw in steps if _procedural_action_matches_filter(str(kw["action_name"]), allowed)]
        if not steps:
            elapsed = time.monotonic() - t0
            emit_result(
                item_id,
                TOOL_ANIMATOR3D,
                STATUS_ERROR,
                error=f"Nenhum clip corresponde ao filtro: {clip_filter}",
                seconds=elapsed,
            )
            raise click.ClickException(f"Nenhum clip corresponde ao filtro: {clip_filter}")

    total = len(steps)
    generated = []
    for i, (fn_name, kwargs) in enumerate(steps):
        pct = round((i / total) * 100) if total > 1 else 0
        emit_progress(item_id, TOOL_ANIMATOR3D, phase="clips", percent=pct, clip=kwargs["action_name"])
        fn = getattr(bpy_ops, fn_name)
        fn(arm_name, frame_start=1, **kwargs)
        generated.append(kwargs["action_name"])
        console.print(f"  [dim]✓[/dim] {kwargs['action_name']}")

    emit_progress(item_id, TOOL_ANIMATOR3D, phase="clips", percent=100)

    nclips = bpy_ops.count_nla_tracks(arm_name)

    emit_progress(item_id, TOOL_ANIMATOR3D, phase="export", percent=0)
    bpy_ops.export_auto(output_path, draco=draco)
    emit_progress(item_id, TOOL_ANIMATOR3D, phase="export", percent=100)

    elapsed = time.monotonic() - t0
    console.print(
        f"[green]game-pack[/green] preset={preset!r} armature={arm_name!r} "
        f"· {nclips} clip(s) no GLB → {output_path.resolve()}"
    )
    emit_result(
        item_id,
        TOOL_ANIMATOR3D,
        STATUS_OK,
        output=str(output_path.resolve()),
        seconds=elapsed,
    )


@main.command("list-clips")
@click.argument("input_path", type=click.Path(path_type=Path, exists=True))
def cmd_list_clips(input_path: Path) -> None:
    """Lista animações (Actions) no ficheiro — JSON em stdout (útil para pipelines)."""
    _require_bpy()
    from . import bpy_ops

    bpy_ops.clear_scene()
    bpy_ops.import_asset(input_path)
    data = bpy_ops.inspect_scene()
    clips: list[dict[str, object]] = []
    for a in data.get("actions", []):
        fr = a.get("frame_range", (0, 0))
        clips.append(
            {
                "name": a.get("name"),
                "frame_range": [int(fr[0]), int(fr[1])],
            }
        )
    out = {
        "input": str(input_path.resolve()),
        "clips": clips,
        "armatures": [
            {
                "name": arm.get("name"),
                "nla_track_count": arm.get("nla_track_count", 0),
                "active_action": arm.get("active_action"),
            }
            for arm in data.get("armatures", [])
        ],
    }
    sys.stdout.write(json.dumps(out, indent=2, ensure_ascii=False) + "\n")


_ANIM_PACK_LABEL: dict[str, str] = {"quaternius": "UAL1", "quaternius2": "UAL2", "villager": "Villager"}


def _short_group_title(group: str) -> str:
    """Título curto de uma secção do catálogo (corta notas entre parênteses/travessões)."""
    return re.split(r"\s\(|\s[—–-]\s|--", group, maxsplit=1)[0].strip() or group


@main.command("list-animations")
@click.option(
    "--pack",
    "pack",
    default="both",
    show_default=True,
    callback=_validate_anim_pack_opt,
    help=(
        "Catálogo a listar — mesma gramática do game-pack --anim-pack: "
        "quaternius (UAL1), quaternius2 (UAL2), villager (Kevin Iglesias; "
        "variante feminina: --profile villager-f), both (UAL1+UAL2), all "
        "(cadeia completa do game-pack: villager + UAL1 + UAL2) ou lista por "
        "vírgulas (ex.: both,villager). A fusão segue a mesma semântica de "
        "substituições do retarget."
    ),
)
@click.option(
    "--profile",
    "profile_name",
    default=None,
    help="Listar um perfil específico (nome em data/retarget/ ou path YAML) em vez de --pack.",
)
@click.option("--json", "as_json", is_flag=True, help="Saída em JSON (stdout) — útil para agentes/pipelines.")
def cmd_list_animations(pack: str, profile_name: str | None, as_json: bool) -> None:
    """Lista as animações disponíveis nos packs de animação (UAL1/UAL2/villager).

    Leitura leve: só os YAML de retarget — sem bpy, sem GPU, sem download.
    Os nomes limpos são o que se passa a ``game-pack --clips`` /
    ``retarget-batch --clips`` e o que o VibeGame procura nos clips do GLB
    (findClip/findClipFuzzy). Para clips DENTRO de um GLB já gerado, usa
    ``list-clips``.
    """
    from . import retarget as rt

    if profile_name:
        specs: list[tuple[str | None, str]] = [(None, profile_name)]
    else:
        names = expand_anim_packs(pack)
        specs = [(name, _ANIM_PACK_PROFILE[name]) for name in names]

    loaded: list[tuple[str | None, str, Any]] = []
    for label_key, prof_name in specs:
        try:
            prof = rt.load_profile(prof_name)
        except FileNotFoundError as e:
            raise click.ClickException(str(e)) from e
        loaded.append((label_key, prof_name, prof))
    plan = rt.plan_pack_passes([prof for _, _, prof in loaded])

    merged: list[dict[str, Any]] = []
    index: dict[str, dict[str, Any]] = {}
    overrides: dict[str, str] = {}
    profiles: list[dict[str, Any]] = []
    for (label_key, prof_name, prof), (_plan_prof, eff) in zip(loaded, plan):
        groups = rt.profile_clip_groups(prof_name)
        label = _ANIM_PACK_LABEL.get(label_key or "", prof.name)
        profiles.append(
            {
                "profile": prof.name,
                "label": label,
                "pack": prof.extra.get("source_pack"),
                "clips": len(prof.clip_map),
            }
        )
        for clean, src in prof.clip_map.items():
            if clean not in eff:
                # Este pass não retargetiza a key (colisão que o perfil não
                # pode substituir) — o catálogo mostra o vencedor real.
                continue
            prev = index.get(clean)
            if prev is not None:
                overrides[clean] = str(prev["source_track"])
                prev["replaces"] = prev["source_track"]
                prev["source_track"] = src
                prev["profile"] = prof.name
                prev["label"] = label
                if groups.get(clean):
                    prev["group"] = groups[clean]
                continue
            entry: dict[str, Any] = {
                "clip": clean,
                "source_track": src,
                "profile": prof.name,
                "label": label,
                "group": groups.get(clean, ""),
            }
            index[clean] = entry
            merged.append(entry)

    if as_json:
        out = {
            "mode": profile_name if profile_name else pack.lower(),
            "profiles": profiles,
            "total": len(merged),
            "overrides": overrides,
            "clips": merged,
        }
        sys.stdout.write(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
        return

    multi = len(specs) > 1
    title = " + ".join(f"{p['label']} ({p['profile']}, {p['clips']} clips)" for p in profiles)
    console.print(f"[bold]Catálogo de animações[/bold] — {title} · [green]{len(merged)} clips únicos[/green]")
    if overrides:
        console.print(
            f"[yellow]Substituições entre packs[/yellow] ({len(overrides)}): "
            f"{', '.join(f'{k} → {v}' for k, v in overrides.items())}"
        )

    grouped: dict[str, list[dict[str, Any]]] = {}
    for entry in merged:
        grouped.setdefault(entry["group"], []).append(entry)
    for group, entries in grouped.items():
        heading = _short_group_title(group) if group else "Sem secção"
        console.print(f"\n[cyan]── {heading} ({len(entries)})[/cyan]")
        table = Table(box=None, show_header=False, padding=(0, 2))
        table.add_column(style="bold white")
        table.add_column(style="dim")
        if multi:
            table.add_column(style="yellow")
        for e in entries:
            row: list[str] = [str(e["clip"]), f"← {e['source_track']}"]
            if multi:
                row.append(str(e["label"]))
            table.add_row(*row)
        console.print(table)

    console.print(
        "\n[dim]Usa os nomes limpos em:[/dim] animator3d game-pack rig.glb out.glb "
        "--anim-pack both --clips idle,walk,chop"
    )


@main.command("texture-project")
@_draco_option
@click.argument("original_glb", type=click.Path(path_type=Path, exists=True))
@click.argument("parts_glb", type=click.Path(path_type=Path, exists=True))
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path),
    required=True,
    help="Output GLB path (textured parts).",
)
@click.option(
    "--resolution",
    type=int,
    default=1024,
    show_default=True,
    help="Bake texture resolution (square).",
)
@click.option(
    "--margin",
    type=int,
    default=16,
    show_default=True,
    help="Bake margin in pixels (edge bleed).",
)
def cmd_texture_project(
    original_glb: Path,
    parts_glb: Path,
    output: Path,
    resolution: int,
    margin: int,
    draco: bool,
) -> None:
    """Projeta textura do modelo original nas partes."""
    _require_bpy()
    from . import bpy_ops

    bpy_ops.clear_scene()
    bpy_ops.project_texture_to_parts(
        original_glb=original_glb,
        parts_glb=parts_glb,
        output_path=output,
        resolution=resolution,
        margin=margin,
        draco=draco,
    )
    console.print(f"[green]texture-project[/green] {resolution}px → {output.resolve()}")


@main.command("screenshot")
@click.argument("input_path", type=click.Path(path_type=Path, exists=True))
@click.option(
    "--output-dir", "-o", type=click.Path(path_type=Path), default=None, help="Pasta destino (default: <input>_debug/)."
)
@click.option(
    "--views",
    default=",".join(["front", "three_quarter", "right", "back"]),
    show_default=True,
    help="Vistas separadas por virgula.",
)
@click.option("--resolution", "-r", default=512, show_default=True, type=int, help="Resolucao em px.")
@click.option("--show-bones", is_flag=True, help="Mostrar armature wireframe.")
@click.option("--frame", default=None, type=int, help="Um frame para todas as vistas (ficheiros view.png).")
@click.option(
    "--frame-list",
    "frame_list",
    default=None,
    type=str,
    help="Varios frames separados por virgula (ex.: 1,36,72) — gera view_fNNNN.png por vista.",
)
@click.option(
    "--engine",
    type=click.Choice(["workbench", "eevee"]),
    default="workbench",
    show_default=True,
    help="Motor de render: Workbench (rapido) ou EEVEE (materiais).",
)
@click.option("--ortho", is_flag=True, help="Camera ortografica (comparacoes de escala).")
@click.option(
    "--no-transparent-film",
    "no_transparent_film",
    is_flag=True,
    help="Desactivar filme transparente (fundo opaco).",
)
def cmd_screenshot(
    input_path: Path,
    output_dir: Path | None,
    views: str,
    resolution: int,
    show_bones: bool,
    frame: int | None,
    frame_list: str | None,
    engine: str,
    ortho: bool,
    no_transparent_film: bool,
) -> None:
    """Gera screenshots multi-angulo de um modelo 3D (debug para agentes IA)."""
    _require_bpy()
    from .debug_render import render_screenshots

    if output_dir is None:
        output_dir = input_path.parent / f"{input_path.stem}_debug"

    view_list = [v.strip() for v in views.split(",") if v.strip()]
    frames_parsed: list[int] | None = None
    if frame_list:
        frames_parsed = [int(x.strip()) for x in frame_list.split(",") if x.strip()]
    report = render_screenshots(
        input_path,
        output_dir,
        views=view_list,
        resolution=resolution,
        show_bones=show_bones,
        frame=None if frames_parsed else frame,
        frames=frames_parsed,
        engine=engine,
        ortho=ortho,
        film_transparent=not no_transparent_film,
    )

    sys.stdout.write(json.dumps(report, indent=2, ensure_ascii=False) + "\n")


@main.command("inspect-rig")
@click.argument("input_path", type=click.Path(path_type=Path, exists=True))
@click.option("--output-dir", "-o", type=click.Path(path_type=Path), default=None, help="Pasta destino.")
@click.option("--show-weights", default=None, type=str, help="Nome do osso para heatmap de pesos.")
@click.option(
    "--views", default=",".join(["front", "three_quarter", "right", "back"]), show_default=True, help="Vistas."
)
@click.option("--resolution", "-r", default=512, show_default=True, type=int, help="Resolucao em px.")
@click.option(
    "--engine",
    type=click.Choice(["workbench", "eevee"]),
    default="workbench",
    show_default=True,
    help="Motor de render (heatmap usa o mesmo motor).",
)
@click.option("--ortho", is_flag=True, help="Camera ortografica.")
@click.option(
    "--no-transparent-film",
    "no_transparent_film",
    is_flag=True,
    help="Desactivar filme transparente.",
)
def cmd_inspect_rig(
    input_path: Path,
    output_dir: Path | None,
    show_weights: str | None,
    views: str,
    resolution: int,
    engine: str,
    ortho: bool,
    no_transparent_film: bool,
) -> None:
    """Inspeciona rig: screenshots com ossos visiveis e/ou heatmap de pesos (debug IA)."""
    _require_bpy()
    from .debug_render import render_screenshots, render_weight_heatmap

    if output_dir is None:
        output_dir = input_path.parent / f"{input_path.stem}_debug"

    view_list = [v.strip() for v in views.split(",") if v.strip()]
    ft = not no_transparent_film

    report = render_screenshots(
        input_path,
        output_dir,
        views=view_list,
        resolution=resolution,
        show_bones=True,
        engine=engine,
        ortho=ortho,
        film_transparent=ft,
    )

    if show_weights:
        weight_report = render_weight_heatmap(
            input_path,
            output_dir,
            show_weights,
            views=view_list,
            resolution=resolution,
            engine=engine,
            ortho=ortho,
            film_transparent=ft,
        )
        report["weight_heatmap"] = weight_report.get("weight_heatmap")

    sys.stdout.write(json.dumps(report, indent=2, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# retarget — aplicar animações de um rig source (ex.: pack CC0 Quaternius)
#            sobre um rig target (humanoides do simple-rpg). Ver retarget.py.
# ---------------------------------------------------------------------------

# Papéis core do rig TARGET para o retarget fazer sentido (tronco/braços/pernas).
# Os perfis mapeiam sources diferentes (Quaternius ``pelvis`` vs KevDev
# ``B-hips``) mas todos apontam para estes candidatos canónimos — a validação
# é por candidato, não por nome de source.
_CORE_TARGET_BONES = ("pelvis", "upperarm_l", "upperarm_r", "thigh_l", "thigh_r")


def _core_bones_missing(arm_name: str, profile) -> list[str]:
    """Papéis core sem qualquer candidato presente no rig target (ou sem source mapeado)."""
    from . import bpy_ops

    bpy = bpy_ops._bpy()
    bones = {b.name for b in bpy.data.objects[arm_name].data.bones}
    missing = []
    for tgt in _CORE_TARGET_BONES:
        srcs = [src for src, cands in profile.bone_map.items() if tgt in cands]
        if not srcs:
            missing.append(tgt)
            continue
        if not any(c in bones for src in srcs for c in profile.bone_map[src]):
            missing.append(tgt)
    return missing


def _fetch_pack_source(pack: str = "quaternius") -> Path:
    """Garante um pack de ficheiro único em cache e devolve o path do GLB (Quaternius)."""
    from aigamekit_shared.quaternius_fetch import fetch_quaternius_pack

    console.print(f"[cyan]Pack de animação ({pack}):[/cyan] a garantir o cache...")
    pk = fetch_quaternius_pack(pack=pack, on_status=lambda m: console.print(f"  [dim]{m}[/dim]"))
    return Path(pk.glb)


def _fetch_pack_root(pack: str) -> Path:
    """Garante um pack itch.io em cache e devolve a raiz extraída (packs por-ficheiro)."""
    from aigamekit_shared.quaternius_fetch import fetch_itch_pack

    console.print(f"[cyan]Pack de animação ({pack}):[/cyan] a garantir o cache...")
    pk = fetch_itch_pack(pack=pack, on_status=lambda m: console.print(f"  [dim]{m}[/dim]"))
    return Path(pk.root)


def _import_retarget_source(source_path: Path, target_arm_name: str):
    """Importa o pack source na cena e descarta as suas meshes (mannequin)."""
    from . import bpy_ops

    bpy = bpy_ops._bpy()
    existing_arms = {a.name for a in bpy_ops.list_armatures()}
    target_arm = bpy.data.objects[target_arm_name]
    target_mesh_names = {o.name for o in target_arm.children if o.type == "MESH"}

    bpy_ops.import_asset(source_path)
    source_arm = next(
        (a for a in bpy_ops.list_armatures() if a.name not in existing_arms),
        None,
    )
    if source_arm is None:
        raise click.ClickException(
            f"Pack source {source_path.name} não trouxe armature nova (existing: {sorted(existing_arms)})"
        )
    source_arm.name = "Source"
    for obj in list(bpy.data.objects):
        if obj.type == "MESH" and obj.name not in target_mesh_names:
            bpy.data.objects.remove(obj, do_unlink=True)
    return source_arm


def _cleanup_retarget_source(source_arm) -> None:
    """Remove o armature source e actions órfãs antes do export."""
    from . import bpy_ops

    bpy = bpy_ops._bpy()
    bpy.data.objects.remove(source_arm, do_unlink=True)
    for act in list(bpy.data.actions):
        used = any(
            s.action == act
            for arm in bpy_ops.list_armatures()
            if arm.animation_data
            for t in arm.animation_data.nla_tracks
            for s in t.strips
        )
        if not used:
            bpy.data.actions.remove(act)


def _print_retarget_result(res: dict) -> None:
    if "error" in res:
        console.print(f"  [yellow]✗[/yellow] {res['clip']} <- {res.get('source_track', '?')}: {res['error']}")
        return
    console.print(
        f"  [dim]✓[/dim] {res['clip']} <- {res['source_track']} · {res['bones_mapped']} bones · "
        f"frames {res['frames'][0]}-{res['frames'][1]}"
    )
    if res.get("skipped_bones"):
        console.print(f"     [yellow]não mapeados:[/yellow] {', '.join(res['skipped_bones'])}")


# Pack de animação -> perfil de retarget usado no game-pack. ``quaternius-hero``
# é superset da UAL1 (armas/ferramentas); ``quaternius2`` é o catálogo UAL2;
# ``villager`` é o pack por-ficheiro do Kevin Iglesias (FBX por clip).
_ANIM_PACK_PROFILE: dict[str, str] = {
    "quaternius": "quaternius-hero",
    "quaternius2": "quaternius2",
    "villager": "villager",
}


def _game_pack_quaternius_retarget(
    item_id: str,
    arm_name: str,
    output_path: Path,
    *,
    clip_filter: str | None,
    draco: bool,
    t0: float,
    anim_pack: str = "quaternius",
) -> bool:
    """Caminho primário do game-pack: retarget da combinação de packs.

    ``anim_pack`` segue a gramática partilhada
    (:func:`aigamekit_shared.anim_packs.expand_anim_packs`): packs individuais,
    aliases ``both``/``all`` ou lista por vírgulas. A ORDEM define quem
    substitui quem: cada pass pode substituir as colisões autorizadas no seu
    perfil (``replace_keys``; None = substitui tudo) e as restantes keys
    são apenas acrescentadas — nunca há duas tracks com o mesmo nome limpo
    (:func:`animator3d.retarget.plan_pack_passes`).

    Returns:
        True se exportou o GLB (fluxo terminado); False para cair no
        caminho procedural (rig sem cobertura, pack indisponível, etc.).
    """
    from . import bpy_ops
    from . import retarget as rt

    try:
        packs = expand_anim_packs(anim_pack)
    except AnimPackError as e:
        console.print(f"[red]anim-pack inválido:[/red] {e}")
        return False
    profiles = {p: rt.load_profile(_ANIM_PACK_PROFILE[p]) for p in packs}
    plan = rt.plan_pack_passes([profiles[p] for p in packs])

    # bone_map: os candidatos target são canónicos em todos os perfis — valida
    # uma vez com o primeiro perfil da cadeia.
    missing = _core_bones_missing(arm_name, profiles[packs[0]])
    if missing:
        console.print(
            f"[yellow]Retarget de animações indisponível:[/yellow] bones core sem candidato no rig: "
            f"{', '.join(missing)}"
        )
        return False

    # Filtro de clips casado contra os clips EFETIVOS de cada pass (uma key
    # que o pass não vai retargetizar não pode entrar no only_clips dele).
    only_clips: dict[str, list[str]] = {}
    if clip_filter:
        wanted = [s.strip() for s in clip_filter.split(",") if s.strip()]
        matched: set[str] = set()
        for (prof, eff), pack_key in zip(plan, packs):
            only_clips[pack_key] = [c for c in wanted if c in eff]
            matched.update(only_clips[pack_key])
        unmatched = [c for c in wanted if c not in matched]
        if unmatched:
            console.print(
                f"[yellow]Clips do filtro sem correspondência nos perfis ({'/'.join(packs)}):[/yellow] {unmatched} "
                "[dim]— catálogo: animator3d list-animations[/dim]"
            )
        if not matched:
            console.print(
                f"[yellow]Nenhum clip do filtro existe nos perfis ({'/'.join(packs)}):[/yellow] {clip_filter} "
                "[dim]— catálogo: animator3d list-animations[/dim]"
            )
            return False

    results: list[dict] = []
    already: set[str] = set()
    first = True
    for (prof, eff), p in zip(plan, packs):
        only = only_clips.get(p) or None
        if clip_filter and not only:
            continue  # este pack não contribui para o filtro pedido
        if prof.source_files_root:
            # Pack POR-FICHEIRO (ex.: villager): cada clip é um FBX importado,
            # retargetizado e descartado. Não há source único na cena.
            try:
                pack_root = _fetch_pack_root(pack=p)
            except Exception as e:
                console.print(f"[yellow]Pack {p} indisponível:[/yellow] {e}")
                if first:
                    return False
                continue
            emit_progress(item_id, TOOL_ANIMATOR3D, phase="retarget", percent=0)
            if not first:
                # Substituir SÓ as keys que este pass vai retargetizar por cima
                # de clips anteriores — remover mais apagava clips que o
                # efectivo deste pass decidiu PRESERVAR (ex.: idle da UAL).
                rt.remove_clips(arm_name, [k for k in eff if k in already])
            eff_prof = dataclasses.replace(prof, clip_map=eff)
            results.extend(rt.retarget_batch_files(arm_name, eff_prof, pack_root, only_clips=only, replace=first))
            first = False
            already.update(eff)
            continue
        try:
            source_path = _fetch_pack_source(pack=p)
        except Exception as e:
            console.print(f"[yellow]Pack de animação {p} indisponível:[/yellow] {e}")
            if first:
                return False
            continue
        emit_progress(item_id, TOOL_ANIMATOR3D, phase="retarget", percent=0)
        source_arm = _import_retarget_source(source_path, arm_name)
        if not first:
            rt.remove_clips(arm_name, [k for k in eff if k in already])
        eff_prof = dataclasses.replace(prof, clip_map=eff)
        results.extend(rt.retarget_batch(arm_name, source_arm.name, eff_prof, only_clips=only, replace=first))
        _cleanup_retarget_source(source_arm)
        first = False
        already.update(eff)

    total = len(results)
    ok = 0
    for i, res in enumerate(results):
        pct = round(((i + 1) / total) * 100) if total else 100
        emit_progress(item_id, TOOL_ANIMATOR3D, phase="clips", percent=pct, clip=res.get("clip"))
        if "error" not in res:
            ok += 1
        _print_retarget_result(res)
    if ok == 0:
        console.print("[yellow]Retarget não produziu clips.[/yellow]")
        return False

    emit_progress(item_id, TOOL_ANIMATOR3D, phase="export", percent=0)
    bpy_ops.export_auto(output_path, draco=draco)
    emit_progress(item_id, TOOL_ANIMATOR3D, phase="export", percent=100)

    nclips = bpy_ops.count_nla_tracks(arm_name)
    elapsed = time.monotonic() - t0
    console.print(
        f"[green]game-pack[/green] retarget={anim_pack} armature={arm_name!r} "
        f"· {nclips} clip(s) no GLB → {output_path.resolve()}"
    )
    emit_result(item_id, TOOL_ANIMATOR3D, STATUS_OK, output=str(output_path.resolve()), seconds=elapsed)
    return True


@main.command("retarget")
@_draco_option
@click.argument("target_path", type=click.Path(path_type=Path, exists=True))
@click.argument("source_path", type=click.Path(path_type=Path, exists=True))
@click.argument("output_path", type=click.Path(path_type=Path))
@click.option(
    "--profile",
    "profile_name",
    default="quaternius",
    show_default=True,
    help="Perfil de retarget (em data/retarget/ ou path YAML).",
)
@click.option("--source-track", required=True, help="Nome da action/track no source a retargetizar (ex.: Idle_Loop).")
@click.option("--clip-name", required=True, help="Nome limpo do clip de saída (ex.: idle).")
@click.option("--replace", is_flag=True, help="Limpar clips existentes no target antes de retargetizar.")
def cmd_retarget(
    target_path: Path,
    source_path: Path,
    output_path: Path,
    profile_name: str,
    source_track: str,
    clip_name: str,
    replace: bool,
    draco: bool,
) -> None:
    """Retargetiza UMA animação de um rig source para o target.

    Ex.: animator3d retarget hero_rigged.glb UAL1_Standard.glb out.glb \\
        --source-track Walk_Loop --clip-name walk
    """
    _require_bpy()
    from . import bpy_ops
    from . import retarget as rt

    profile = rt.load_profile(profile_name)
    item_id = target_path.stem
    t0 = time.monotonic()
    emit_progress(item_id, TOOL_ANIMATOR3D, phase="retarget", percent=0)

    bpy_ops.clear_scene()
    bpy_ops.import_asset(target_path)
    target_arm = bpy_ops.list_armatures()[0]
    target_arm.name = "Target"

    source_arm = _import_retarget_source(source_path, "Target")
    rt.ensure_feet_root_bone(target_arm)

    if replace:
        rt._clear_nla_tracks("Target")

    res = rt.retarget_animation("Target", source_arm.name, profile.bone_map, source_track, clip_name)
    _print_retarget_result(res)

    _cleanup_retarget_source(source_arm)

    emit_progress(item_id, TOOL_ANIMATOR3D, phase="export", percent=0)
    bpy_ops.export_glb(output_path, draco=draco)
    emit_progress(item_id, TOOL_ANIMATOR3D, phase="export", percent=100)

    elapsed = time.monotonic() - t0
    console.print(f"[green]retarget[/green] {clip_name!r} · {res['bones_mapped']} bones → {output_path.resolve()}")
    emit_result(item_id, TOOL_ANIMATOR3D, STATUS_OK, output=str(output_path.resolve()), seconds=elapsed)


@main.command("retarget-batch")
@_draco_option
@click.argument("target_path", type=click.Path(path_type=Path, exists=True))
@click.argument("output_path", type=click.Path(path_type=Path))
@click.option("--profile", "profile_name", default="quaternius", show_default=True, help="Perfil de retarget.")
@click.option(
    "--source",
    "source_path",
    type=click.Path(path_type=Path),
    default=None,
    help="Ficheiro source (FBX/GLB do pack). Default: pack Quaternius em cache (auto-download).",
)
@click.option(
    "--clips",
    "clip_filter",
    default=None,
    help="Subconjunto de nomes limpos separados por vírgula (ex: idle,walk,run). Catálogo: animator3d list-animations.",
)
@click.option(
    "--replace", is_flag=True, default=True, show_default=True, help="Limpar clips existentes no target (default: on)."
)
@click.option("--no-replace", "replace", flag_value=False, help="Preservar clips existentes (append).")
@click.option("--no-fetch", is_flag=True, help="Não fazer auto-download do pack Quaternius (usa --source).")
def cmd_retarget_batch(
    target_path: Path,
    output_path: Path,
    profile_name: str,
    source_path: Path | None,
    clip_filter: str | None,
    replace: bool,
    no_fetch: bool,
    draco: bool,
) -> None:
    """Retargetiza TODOS os clips de um perfil num único comando.

    Ex.: animator3d retarget-batch hero_rigged.glb out.glb --profile quaternius
    """
    _require_bpy()
    from . import bpy_ops
    from . import retarget as rt

    profile = rt.load_profile(profile_name)
    per_file = profile.source_files_root is not None

    # Resolver source: path explícito > profile.source_path > auto-download do
    # pack indicado pelo perfil (source_pack) ou UAL1 para perfis quaternius*.
    # Packs por-ficheiro (ex.: villager) em vez de source único trazem pack_root.
    pack_root: Path | None = None
    if source_path is None and per_file:
        pack = profile.extra.get("source_pack")
        if pack is None or no_fetch:
            raise click.ClickException(
                f"Perfil por-ficheiro {profile.name!r} precisa do pack em cache: "
                "usa fetch_itch_pack ou remove --no-fetch."
            )
        pack_root = _fetch_pack_root(pack=pack)
    else:
        if source_path is None:
            source_path = profile.source_path
        if source_path is None and not no_fetch:
            pack = profile.extra.get("source_pack") or ("quaternius" if profile.name.startswith("quaternius") else None)
            if pack:
                source_path = _fetch_pack_source(pack=pack)
            else:
                raise click.ClickException(
                    f"Sem --source e o perfil {profile.name!r} não define source_path. Indica --source <pack.fbx/glb>."
                )
        if source_path is None or not Path(source_path).is_file():
            raise click.ClickException(f"Ficheiro source não encontrado: {source_path}")

    only_clips = [s.strip() for s in clip_filter.split(",")] if clip_filter else None
    if only_clips:
        missing = [c for c in only_clips if c not in profile.clip_map]
        if missing:
            raise click.ClickException(f"Clips não definidos no perfil {profile.name!r}: {missing}")

    item_id = target_path.stem
    t0 = time.monotonic()
    emit_progress(item_id, TOOL_ANIMATOR3D, phase="retarget", percent=0)

    bpy_ops.clear_scene()
    bpy_ops.import_asset(target_path)
    target_arm = bpy_ops.list_armatures()[0]
    target_arm.name = "Target"

    total = len(profile.clip_map)
    if per_file:
        results = rt.retarget_batch_files("Target", profile, pack_root, only_clips=only_clips, replace=replace)
    else:
        source_arm = _import_retarget_source(Path(source_path), "Target")
        results = rt.retarget_batch("Target", source_arm.name, profile, only_clips=only_clips, replace=replace)
        _cleanup_retarget_source(source_arm)
    for i, res in enumerate(results):
        pct = round(((i + 1) / total) * 100) if total else 100
        emit_progress(item_id, TOOL_ANIMATOR3D, phase="clips", percent=pct, clip=res.get("clip"))
        _print_retarget_result(res)

    emit_progress(item_id, TOOL_ANIMATOR3D, phase="export", percent=0)
    bpy_ops.export_glb(output_path, draco=draco)
    emit_progress(item_id, TOOL_ANIMATOR3D, phase="export", percent=100)

    nclips = bpy_ops.count_nla_tracks("Target")
    elapsed = time.monotonic() - t0
    console.print(
        f"[green]retarget-batch[/green] profile={profile.name!r} · {nclips} clip(s) → {output_path.resolve()}"
    )
    emit_result(item_id, TOOL_ANIMATOR3D, STATUS_OK, output=str(output_path.resolve()), seconds=elapsed)


@main.command("rename-clips")
@click.argument("input_path", type=click.Path(path_type=Path, exists=True))
@click.argument("output_path", type=click.Path(path_type=Path))
@click.option(
    "--map",
    "map_str",
    required=True,
    help="Mapeamento old:new separado por vírgulas (ex: Animator3D_BreatheIdle:idle,Animator3D_Walk:walk).",
)
@_draco_option
def cmd_rename_clips(input_path: Path, output_path: Path, map_str: str, draco: bool) -> None:
    """Renomeia clips (NLA tracks) já existentes num GLB — sem retarget.

    Útil para alinhar nomes em rigs que mantêm animação procedural (ex.: scorpion,
    mosquito) aos nomes limpos esperados pelo jogo.
    """
    _require_bpy()
    from . import bpy_ops
    from . import retarget as rt

    rename_map: dict[str, str] = {}
    for pair in map_str.split(","):
        pair = pair.strip()
        if not pair or ":" not in pair:
            continue
        old, new = pair.split(":", 1)
        rename_map[old.strip()] = new.strip()
    if not rename_map:
        raise click.ClickException("--map vazio ou inválido. Formato: old1:new1,old2:new2")

    bpy_ops.clear_scene()
    bpy_ops.import_asset(input_path)
    arms = bpy_ops.list_armatures()
    if not arms:
        raise click.ClickException("Nenhum armature encontrado.")
    arm_name = arms[0].name

    done = rt.rename_existing_clips(arm_name, rename_map)
    for d in done:
        console.print(f"  [dim]✓[/dim] {d['old']} -> {d['new']}")
    if not done:
        console.print("[yellow]Nenhum clip correspondeu ao mapeamento fornecido.[/yellow]")

    bpy_ops.export_glb(output_path, draco=draco)
    console.print(f"[green]rename-clips[/green] · {len(done)} renomeado(s) → {output_path.resolve()}")


if __name__ == "__main__":
    main()
