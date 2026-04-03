"""Animator3D — CLI principal."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from rich.console import Console
from rich.json import JSON

from . import __version__
from .cli_rich import click  # noqa: F401 — rich-click

console = Console()


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
@click.argument("input_path", type=click.Path(path_type=Path, exists=True))
@click.argument("output_path", type=click.Path(path_type=Path))
def cmd_export(input_path: Path, output_path: Path) -> None:
    """Importa o ficheiro e exporta de novo (útil para validar roundtrip GLB/FBX)."""
    _require_bpy()
    from . import bpy_ops

    bpy_ops.clear_scene()
    bpy_ops.import_asset(input_path)
    bpy_ops.export_auto(output_path)
    console.print(f"[green]Exportado:[/green] {output_path.resolve()}")


@main.command("wave-idle")
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
    bpy_ops.export_auto(output_path)
    console.print(
        f"[green]Animado[/green] armature={arm_name!r} osso={bone_name!r} "
        f"· {nclips} clip(s) no GLB → {output_path.resolve()}"
    )


@main.command("breathe-idle")
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
    bpy_ops.export_auto(output_path)

    chain_info = ", ".join(f"{k}({len(v)})" for k, v in chains.items())
    console.print(
        f"[green]Animado[/green] armature={arm_name!r} cadeias=\\[{chain_info}] "
        f"· {nclips} clip(s) no GLB → {output_path.resolve()}"
    )


@main.command("attack")
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
    bpy_ops.export_auto(output_path)

    chain_info = ", ".join(f"{k}({len(v)})" for k, v in chains.items())
    console.print(
        f"[green]Ataque[/green] armature={arm_name!r} cadeias=\\[{chain_info}] "
        f"· {nclips} clip(s) no GLB → {output_path.resolve()}"
    )


@main.command("walk")
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
    bpy_ops.export_auto(output_path)
    chain_info = ", ".join(f"{k}({len(v)})" for k, v in chains.items())
    console.print(
        f"[green]Walk[/green] armature={arm_name!r} cadeias=\\[{chain_info}] "
        f"· {nclips} clip(s) no GLB → {output_path.resolve()}"
    )


@main.command("hover")
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
    bpy_ops.export_auto(output_path)
    chain_info = ", ".join(f"{k}({len(v)})" for k, v in chains.items())
    console.print(
        f"[green]Hover[/green] armature={arm_name!r} cadeias=\\[{chain_info}] "
        f"· {nclips} clip(s) no GLB → {output_path.resolve()}"
    )


@main.command("soar")
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
    bpy_ops.export_auto(output_path)
    chain_info = ", ".join(f"{k}({len(v)})" for k, v in chains.items())
    console.print(
        f"[green]Soar[/green] armature={arm_name!r} cadeias=\\[{chain_info}] "
        f"· {nclips} clip(s) no GLB → {output_path.resolve()}"
    )


@main.command("dive")
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
    if not append_mode:
        bpy_ops.clear_armature_animations(arm_name)

    chains = bpy_ops.dive_attack_keyframes(
        arm_name,
        frame_start=1,
        frame_end=frames,
        action_name=_clip_name_or_default(clip_name, "Animator3D_DiveAttack"),
    )
    nclips = bpy_ops.count_nla_tracks(arm_name)
    bpy_ops.export_auto(output_path)
    chain_info = ", ".join(f"{k}({len(v)})" for k, v in chains.items())
    console.print(
        f"[green]Dive[/green] armature={arm_name!r} cadeias=\\[{chain_info}] "
        f"· {nclips} clip(s) no GLB → {output_path.resolve()}"
    )


@main.command("fire")
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
    bpy_ops.export_auto(output_path)
    chain_info = ", ".join(f"{k}({len(v)})" for k, v in chains.items())
    console.print(
        f"[green]Fire[/green] armature={arm_name!r} cadeias=\\[{chain_info}] "
        f"· {nclips} clip(s) no GLB → {output_path.resolve()}"
    )


@main.command("land")
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
    if not append_mode:
        bpy_ops.clear_armature_animations(arm_name)

    chains = bpy_ops.land_keyframes(
        arm_name,
        frame_start=1,
        frame_end=frames,
        action_name=_clip_name_or_default(clip_name, "Animator3D_Land"),
    )
    nclips = bpy_ops.count_nla_tracks(arm_name)
    bpy_ops.export_auto(output_path)
    chain_info = ", ".join(f"{k}({len(v)})" for k, v in chains.items())
    console.print(
        f"[green]Land[/green] armature={arm_name!r} cadeias=\\[{chain_info}] "
        f"· {nclips} clip(s) no GLB → {output_path.resolve()}"
    )


@main.command("roar")
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
    if not append_mode:
        bpy_ops.clear_armature_animations(arm_name)

    chains = bpy_ops.victory_roar_keyframes(
        arm_name,
        frame_start=1,
        frame_end=frames,
        action_name=_clip_name_or_default(clip_name, "Animator3D_VictoryRoar"),
    )
    nclips = bpy_ops.count_nla_tracks(arm_name)
    bpy_ops.export_auto(output_path)
    chain_info = ", ".join(f"{k}({len(v)})" for k, v in chains.items())
    console.print(
        f"[green]Roar[/green] armature={arm_name!r} cadeias=\\[{chain_info}] "
        f"· {nclips} clip(s) no GLB → {output_path.resolve()}"
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


if __name__ == "__main__":
    main()
