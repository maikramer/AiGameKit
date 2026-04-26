#!/usr/bin/env python3
"""CLI gamedev-lab — debug, bancadas, profiling."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rich.console import Console

from gamedev_lab import __version__
from gamedev_lab.cli_rich import click
from gamedev_lab.compare_inspect import diff_inspect
from gamedev_lab.debug_tools import (
    extract_json_from_output,
    merge_subprocess_output,
    resolve_animator3d_bin,
    run_cmd,
)
from gamedev_lab.validate_rules import evaluate_inspect_rules, load_rules_file

console = Console()
console_err = Console(file=sys.stderr)

EPILOG = """
Exemplos:
  gamedev-lab debug bundle modelo.glb -o ./out_bundle
  gamedev-lab bench part3d --mesh meshes/foo.glb --modo sdnq-uint8 --project-dir .
  gamedev-lab bench sdnq-sweep --mesh modelo.glb --image ref.png --target-vram-mb 5500
  gamedev-lab bench pipeline-opt --mesh input.glb --image ref.png --target-vram-mb 6000
  gamedev-lab profile cprofile -o out.prof meu_script.py -- --arg 1
  gamedev-lab perf list --tool text2d --limit 10
  gamedev-lab perf recommend text2d --vram 8000
  gamedev-lab perf vram --tool text3d
"""


@click.group(epilog=EPILOG)
@click.version_option(__version__, prog_name="gamedev-lab")
def main() -> None:
    """Ferramentas de laboratório GameDev."""


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


@main.group("check")
def check_group() -> None:
    """Validação declarativa de GLB (inspect + regras YAML/JSON)."""


@check_group.command("glb")
@click.argument("glb_path", type=click.Path(exists=True, path_type=Path))
@click.argument("rules_file", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--json-out",
    type=click.Path(path_type=Path),
    default=None,
    help="Escrever relatório JSON (stdout se omitido com --quiet).",
)
@click.option("--quiet", "-q", is_flag=True, help="Só código de saída; erros em stderr.")
def check_glb_cmd(glb_path: Path, rules_file: Path, json_out: Path | None, quiet: bool) -> None:
    """Valida um GLB contra regras (via animator3d inspect)."""
    abin = resolve_animator3d_bin()
    if not abin:
        console_err.print("[red]animator3d não encontrado.[/red]")
        sys.exit(2)

    rules = load_rules_file(rules_file)
    r = run_cmd([abin, "inspect", str(glb_path), "--json-out"])
    if r.returncode != 0:
        err = merge_subprocess_output(r, max_chars=2000)
        console_err.print(f"[red]inspect falhou:[/red] {err}")
        sys.exit(2)

    inspect_data = extract_json_from_output(r.stdout)
    ok, failures, details = evaluate_inspect_rules(inspect_data, rules)
    report: dict[str, Any] = {
        "ok": ok,
        "glb": str(glb_path.resolve()),
        "rules_file": str(rules_file.resolve()),
        "failures": failures,
        "details": details,
    }
    line = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if json_out:
        json_out.write_text(line, encoding="utf-8")
    elif not quiet:
        sys.stdout.write(line)

    if not ok and not quiet:
        for f in failures:
            console.print(f"[red]{f}[/red]")
    sys.exit(0 if ok else 1)


# ---------------------------------------------------------------------------
# debug
# ---------------------------------------------------------------------------


@main.group("debug")
def debug_group() -> None:
    """Screenshots, inspect, compare e bundle (Animator3D)."""


@debug_group.command("screenshot")
@click.argument("input_path", type=click.Path(exists=True, path_type=Path))
@click.option("--output-dir", "-o", type=click.Path(path_type=Path), default=None, help="Pasta destino.")
@click.option(
    "--views",
    default="front,three_quarter,right,back",
    show_default=True,
    help="Vistas separadas por vírgula.",
)
@click.option("--resolution", "-r", default=512, show_default=True, type=int, help="Resolução px.")
@click.option("--show-bones", is_flag=True, help="Mostrar armature wireframe.")
@click.option("--frame", default=None, type=int, help="Um frame para todas as vistas.")
@click.option(
    "--frame-list",
    "frame_list",
    default=None,
    type=str,
    help="Vários frames (ex.: 1,36,72) — ficheiros view_fNNNN.png.",
)
@click.option(
    "--engine",
    type=click.Choice(["workbench", "eevee"]),
    default="workbench",
    show_default=True,
)
@click.option("--ortho", is_flag=True)
@click.option("--no-transparent-film", "no_transparent_film", is_flag=True)
def debug_screenshot(
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
    """Gera screenshots multi-ângulo de um GLB (invoca animator3d)."""
    abin = resolve_animator3d_bin()
    if not abin:
        console.print("[red]animator3d não encontrado.[/red] Defina ANIMATOR3D_BIN ou instale Animator3D.")
        sys.exit(1)

    if output_dir is None:
        output_dir = input_path.parent / f"{input_path.stem}_debug"

    argv = [
        abin,
        "screenshot",
        str(input_path),
        "--output-dir",
        str(output_dir),
        "--views",
        views,
        "--resolution",
        str(resolution),
    ]
    if show_bones:
        argv.append("--show-bones")
    if frame_list:
        argv.extend(["--frame-list", frame_list])
    elif frame is not None:
        argv.extend(["--frame", str(frame)])
    argv.extend(["--engine", engine])
    if ortho:
        argv.append("--ortho")
    if no_transparent_film:
        argv.append("--no-transparent-film")

    r = run_cmd(argv)
    if r.returncode != 0:
        err = merge_subprocess_output(r, max_chars=2000) or "animator3d screenshot falhou"
        console.print(f"[red]Erro:[/red] {err}")
        sys.exit(1)

    report = extract_json_from_output(r.stdout)
    report_path = output_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    n = len(report.get("screenshots", []))
    console.print(f"[green]{n} screenshots[/green] em {output_dir}")
    console.print(json.dumps(report, indent=2, ensure_ascii=False))


@debug_group.command("bundle")
@click.argument("input_path", type=click.Path(exists=True, path_type=Path))
@click.option("--output-dir", "-o", type=click.Path(path_type=Path), default=None, help="Pasta destino.")
@click.option(
    "--views",
    default="front,three_quarter,right,back,low_front,worm",
    show_default=True,
    help="Vistas (inclui low_front e worm por defeito).",
)
@click.option("--resolution", "-r", default=512, show_default=True, type=int, help="Resolução px.")
@click.option("--show-bones", is_flag=True, help="Wireframe do armature nos screenshots.")
@click.option("--frame", default=None, type=int, help="Frame único para screenshots.")
@click.option("--frame-list", "frame_list", default=None, type=str, help="Vários frames (animação).")
@click.option(
    "--engine",
    type=click.Choice(["workbench", "eevee"]),
    default="workbench",
    show_default=True,
)
@click.option("--ortho", is_flag=True)
@click.option("--no-transparent-film", "no_transparent_film", is_flag=True)
@click.option(
    "--include-rig",
    "include_rig",
    is_flag=True,
    help="Gera subpasta rig/ com inspect-rig (ossos + heatmap opcional).",
)
@click.option("--rig-weights", "rig_weights", default=None, type=str, help="Com --include-rig: osso para heatmap.")
def debug_bundle(
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
    include_rig: bool,
    rig_weights: str | None,
) -> None:
    """Pacote único para agentes: inspect JSON + screenshots + bundle.json."""
    abin = resolve_animator3d_bin()
    if not abin:
        console.print("[red]animator3d não encontrado.[/red] Defina ANIMATOR3D_BIN ou instale Animator3D.")
        sys.exit(1)

    if output_dir is None:
        output_dir = input_path.parent / f"{input_path.stem}_agent_bundle"
    output_dir.mkdir(parents=True, exist_ok=True)

    inspect_path = output_dir / "inspect.json"
    argv_in = [abin, "inspect", str(input_path), "--json-out"]
    r_in = run_cmd(argv_in)
    if r_in.returncode != 0:
        err = merge_subprocess_output(r_in, max_chars=2000) or "inspect falhou"
        console.print(f"[red]Erro:[/red] {err}")
        sys.exit(1)
    inspect_data = extract_json_from_output(r_in.stdout)
    inspect_path.write_text(json.dumps(inspect_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    shot_dir = output_dir / "screenshots"
    argv_sh = [
        abin,
        "screenshot",
        str(input_path),
        "--output-dir",
        str(shot_dir),
        "--views",
        views,
        "--resolution",
        str(resolution),
    ]
    if show_bones:
        argv_sh.append("--show-bones")
    if frame_list:
        argv_sh.extend(["--frame-list", frame_list])
    elif frame is not None:
        argv_sh.extend(["--frame", str(frame)])
    argv_sh.extend(["--engine", engine])
    if ortho:
        argv_sh.append("--ortho")
    if no_transparent_film:
        argv_sh.append("--no-transparent-film")

    r_sh = run_cmd(argv_sh)
    if r_sh.returncode != 0:
        err = merge_subprocess_output(r_sh, max_chars=2000) or "screenshot falhou"
        console.print(f"[red]Erro:[/red] {err}")
        sys.exit(1)
    shot_report = extract_json_from_output(r_sh.stdout)
    (output_dir / "screenshot_report.json").write_text(
        json.dumps(shot_report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    bundle: dict[str, Any] = {
        "tool": "gamedev_lab.debug.bundle",
        "gamedev_lab_version": __version__,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input": str(input_path.resolve()),
        "input_size_bytes": input_path.stat().st_size if input_path.is_file() else 0,
        "inspect_path": str(inspect_path),
        "screenshot_dir": str(shot_dir),
        "screenshot_report_path": str(output_dir / "screenshot_report.json"),
        "inspect": inspect_data,
        "screenshots": shot_report.get("screenshots", []),
        "world_bounds": shot_report.get("world_bounds"),
        "mesh": shot_report.get("mesh"),
        "animations": shot_report.get("animations"),
    }
    bundle_path = output_dir / "bundle.json"

    if include_rig:
        rig_dir = output_dir / "rig"
        argv_rig = [
            abin,
            "inspect-rig",
            str(input_path),
            "-o",
            str(rig_dir),
            "--views",
            views,
            "--resolution",
            str(resolution),
            "--engine",
            engine,
        ]
        if ortho:
            argv_rig.append("--ortho")
        if no_transparent_film:
            argv_rig.append("--no-transparent-film")
        if rig_weights:
            argv_rig.extend(["--show-weights", rig_weights])
        r_rig = run_cmd(argv_rig)
        if r_rig.returncode != 0:
            err = merge_subprocess_output(r_rig, max_chars=2000) or "inspect-rig falhou"
            console.print(f"[yellow]inspect-rig:[/yellow] {err}")
        else:
            bundle["rig_report"] = extract_json_from_output(r_rig.stdout)
            bundle["rig_dir"] = str(rig_dir)

    bundle_path.write_text(json.dumps(bundle, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    console.print(f"[green]Bundle:[/green] {bundle_path}")
    console.print(f"  inspect → {inspect_path}")
    console.print(f"  screenshots → {shot_dir} ({len(bundle['screenshots'])} imagens)")
    sys.stdout.write(json.dumps(bundle, indent=2, ensure_ascii=False) + "\n")


@debug_group.command("inspect")
@click.argument("input_path", type=click.Path(exists=True, path_type=Path))
@click.option("--output", "-o", type=click.Path(path_type=Path), default=None, help="Guardar JSON em ficheiro.")
def debug_inspect(input_path: Path, output: Path | None) -> None:
    """Metadados de armature/mesh/animação em JSON (via animator3d)."""
    abin = resolve_animator3d_bin()
    if not abin:
        console.print("[red]animator3d não encontrado.[/red]")
        sys.exit(1)

    argv = [abin, "inspect", str(input_path), "--json-out"]
    r = run_cmd(argv)
    if r.returncode != 0:
        err = merge_subprocess_output(r, max_chars=2000) or "animator3d inspect falhou"
        console.print(f"[red]Erro:[/red] {err}")
        sys.exit(1)

    data = extract_json_from_output(r.stdout)
    data["file_size_bytes"] = input_path.stat().st_size if input_path.is_file() else 0
    data["input"] = str(input_path)

    out_str = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(out_str)
        console.print(f"[green]Guardado:[/green] {output}")
    else:
        sys.stdout.write(out_str)


@debug_group.command("inspect-rig")
@click.argument("input_path", type=click.Path(exists=True, path_type=Path))
@click.option("--output-dir", "-o", type=click.Path(path_type=Path), default=None, help="Pasta destino.")
@click.option("--show-weights", default=None, type=str, help="Osso para heatmap de pesos.")
@click.option("--views", default="front,three_quarter,right,back", show_default=True)
@click.option("--resolution", "-r", default=512, show_default=True, type=int)
@click.option("--engine", type=click.Choice(["workbench", "eevee"]), default="workbench", show_default=True)
@click.option("--ortho", is_flag=True)
@click.option("--no-transparent-film", "no_transparent_film", is_flag=True)
def debug_inspect_rig(
    input_path: Path,
    output_dir: Path | None,
    show_weights: str | None,
    views: str,
    resolution: int,
    engine: str,
    ortho: bool,
    no_transparent_film: bool,
) -> None:
    """Rig: vistas com ossos e opcional heatmap (animator3d inspect-rig)."""
    abin = resolve_animator3d_bin()
    if not abin:
        console.print("[red]animator3d não encontrado.[/red]")
        sys.exit(1)

    if output_dir is None:
        output_dir = input_path.parent / f"{input_path.stem}_rig_debug"

    argv = [
        abin,
        "inspect-rig",
        str(input_path),
        "-o",
        str(output_dir),
        "--views",
        views,
        "--resolution",
        str(resolution),
        "--engine",
        engine,
    ]
    if ortho:
        argv.append("--ortho")
    if no_transparent_film:
        argv.append("--no-transparent-film")
    if show_weights:
        argv.extend(["--show-weights", show_weights])

    r = run_cmd(argv)
    if r.returncode != 0:
        err = merge_subprocess_output(r, max_chars=2000) or "inspect-rig falhou"
        console.print(f"[red]Erro:[/red] {err}")
        sys.exit(1)
    sys.stdout.write(r.stdout)


@debug_group.command("compare")
@click.argument("file_a", type=click.Path(exists=True, path_type=Path))
@click.argument("file_b", type=click.Path(exists=True, path_type=Path))
@click.option("--output-dir", "-o", type=click.Path(path_type=Path), default=None, help="Pasta destino.")
@click.option("--views", default="front,three_quarter", show_default=True, help="Vistas para comparar.")
@click.option("--resolution", "-r", default=512, show_default=True, type=int, help="Resolução px.")
@click.option(
    "--with-inspect",
    "with_inspect",
    is_flag=True,
    help="Incluir inspect JSON por modelo no diff_report.",
)
@click.option(
    "--struct-diff/--no-struct-diff",
    "struct_diff",
    default=True,
    help="Calcular inspect_diff (dois inspect; defeito: ligado).",
)
@click.option(
    "--image-metrics",
    "image_metrics",
    is_flag=True,
    help="MAE/RMSE/SSIM por vista (numpy).",
)
@click.option(
    "--fail-below-ssim",
    "fail_below_ssim",
    type=float,
    default=None,
    help="Exit 1 se alguma vista tiver SSIM abaixo deste valor (requer --image-metrics).",
)
@click.option(
    "--engine",
    type=click.Choice(["workbench", "eevee"]),
    default=None,
    help="Motor de render (animator3d screenshot; defeito: workbench no binário).",
)
@click.option("--ortho", is_flag=True, help="Câmara ortográfica (animator3d).")
def debug_compare(
    file_a: Path,
    file_b: Path,
    output_dir: Path | None,
    views: str,
    resolution: int,
    with_inspect: bool,
    struct_diff: bool,
    image_metrics: bool,
    fail_below_ssim: float | None,
    engine: str | None,
    ortho: bool,
) -> None:
    """Compara dois modelos lado a lado (screenshots + report JSON)."""
    abin = resolve_animator3d_bin()
    if not abin:
        console.print("[red]animator3d não encontrado.[/red]")
        sys.exit(1)

    if output_dir is None:
        output_dir = file_a.parent / f"{file_a.stem}_vs_{file_b.stem}"
    output_dir.mkdir(parents=True, exist_ok=True)
    dir_a = output_dir / "a"
    dir_b = output_dir / "b"

    inspect_side: dict[str, Any] = {}
    if with_inspect or struct_diff:
        for label, fpath in [("a", file_a), ("b", file_b)]:
            r_i = run_cmd([abin, "inspect", str(fpath), "--json-out"])
            if r_i.returncode == 0:
                inspect_side[label] = extract_json_from_output(r_i.stdout)
            else:
                inspect_side[label] = {"_error": merge_subprocess_output(r_i, max_chars=500)}

    def _shot_argv(fpath: Path, d: Path) -> list[str]:
        argv = [
            abin,
            "screenshot",
            str(fpath),
            "--output-dir",
            str(d),
            "--views",
            views,
            "--resolution",
            str(resolution),
            "--show-bones",
        ]
        if engine:
            argv.extend(["--engine", engine])
        if ortho:
            argv.append("--ortho")
        return argv

    reports = {}
    for label, fpath, d in [("a", file_a, dir_a), ("b", file_b, dir_b)]:
        r = run_cmd(_shot_argv(fpath, d))
        if r.returncode != 0:
            console.print(
                f"[red]Erro ao gerar screenshots de {label}:[/red] {merge_subprocess_output(r, max_chars=500)}"
            )
            sys.exit(1)
        reports[label] = extract_json_from_output(r.stdout)

    side_by_side_paths = []
    try:
        from PIL import Image

        view_list = [v.strip() for v in views.split(",") if v.strip()]
        for vn in view_list:
            pa = dir_a / f"{vn}.png"
            pb = dir_b / f"{vn}.png"
            if pa.is_file() and pb.is_file():
                img_a = Image.open(pa)
                img_b = Image.open(pb)
                w = img_a.width + img_b.width + 4
                h = max(img_a.height, img_b.height)
                combined = Image.new("RGBA", (w, h), (30, 30, 30, 255))
                combined.paste(img_a, (0, 0))
                combined.paste(img_b, (img_a.width + 4, 0))
                out_path = output_dir / f"compare_{vn}.png"
                combined.save(out_path)
                side_by_side_paths.append({"view": vn, "path": str(out_path)})
    except ImportError:
        console.print("[yellow]Pillow não instalado — side-by-side não gerado.[/yellow]")

    diff_report: dict[str, Any] = {
        "file_a": str(file_a),
        "file_b": str(file_b),
        "report_a": reports.get("a", {}),
        "report_b": reports.get("b", {}),
        "side_by_side": side_by_side_paths,
    }
    if with_inspect:
        diff_report["inspect"] = inspect_side
    if struct_diff and "a" in inspect_side and "b" in inspect_side:
        ia = inspect_side.get("a")
        ib = inspect_side.get("b")
        if isinstance(ia, dict) and isinstance(ib, dict) and "_error" not in ia and "_error" not in ib:
            diff_report["inspect_diff"] = diff_inspect(ia, ib)

    if image_metrics:
        from gamedev_lab.compare_images import compare_view_pair

        metrics_list = []
        view_list = [v.strip() for v in views.split(",") if v.strip()]
        for vn in view_list:
            pa = dir_a / f"{vn}.png"
            pb = dir_b / f"{vn}.png"
            if pa.is_file() and pb.is_file():
                metrics_list.append({"view": vn, **compare_view_pair(pa, pb)})
        diff_report["image_metrics"] = metrics_list
        if fail_below_ssim is not None:
            for m in metrics_list:
                if m.get("ssim", 1.0) < fail_below_ssim:
                    diff_report["ssim_gate_failed"] = True
                    diff_path = output_dir / "diff_report.json"
                    diff_path.write_text(json.dumps(diff_report, indent=2, ensure_ascii=False) + "\n")
                    console.print(f"[red]SSIM abaixo do limiar em vista(s).[/red] {diff_path}")
                    sys.exit(1)

    diff_path = output_dir / "diff_report.json"
    diff_path.write_text(json.dumps(diff_report, indent=2, ensure_ascii=False) + "\n")
    console.print(f"[green]Comparação:[/green] {output_dir}")
    console.print(f"  {len(side_by_side_paths)} imagens side-by-side, report em {diff_path}")


# ---------------------------------------------------------------------------
# bench
# ---------------------------------------------------------------------------


@main.group("bench")
def bench_group() -> None:
    """Bancadas Part3D, Paint/VRAM, pré-quantização SDNQ e batch GameAssets."""


@bench_group.command("part3d")
@click.option(
    "--mesh",
    type=click.Path(path_type=Path, exists=True),
    default=None,
    help="Ficheiro GLB (relativo ao --project-dir se não for absoluto).",
)
@click.option(
    "--modo",
    type=str,
    default="baseline-fp16",
    help="Nome da config ou 'sweep' para todos.",
)
@click.option(
    "--output-dir",
    "-o",
    type=click.Path(path_type=Path),
    default="test_part3d_results",
    help="Diretório de resultados.",
)
@click.option(
    "--project-dir",
    type=click.Path(path_type=Path, file_okay=False),
    default=".",
    help="Diretório base para resolver caminhos relativos.",
)
def bench_part3d_cmd(
    mesh: Path | None,
    modo: str,
    output_dir: Path,
    project_dir: Path,
) -> None:
    """Testes Part3D com VRAM (quantização none / quanto / SDNQ)."""
    pd = project_dir.resolve()
    out = (pd / output_dir).resolve() if not output_dir.is_absolute() else output_dir.resolve()
    m = mesh
    if m is None:
        m = pd / "meshes" / "boa_mesa" / "tigela_ceramica.glb"
    elif not m.is_absolute():
        m = (pd / m).resolve()

    from gamedev_lab.bench_part3d import run_bench_cli

    sys.exit(run_bench_cli(m, modo, out))


@bench_group.command("paint-vram")
@click.option(
    "--image",
    type=click.Path(path_type=Path, exists=True),
    default=None,
    help="Imagem de referência para apply_hunyuan_paint (opcional).",
)
@click.option("--target-vram-mb", type=float, default=5500.0, show_default=True)
@click.option(
    "--output-json",
    type=click.Path(path_type=Path),
    default="quantization_vram_results.json",
    help="Ficheiro de saída.",
)
@click.option(
    "--project-dir",
    type=click.Path(path_type=Path, file_okay=False),
    default=".",
    help="Base para caminhos relativos do --output-json.",
)
def bench_paint_vram_cmd(
    image: Path | None,
    target_vram_mb: float,
    output_json: Path,
    project_dir: Path,
) -> None:
    """Sweet spot de quantização Paint3D com monitorização VRAM."""
    pd = project_dir.resolve()
    out = (pd / output_json).resolve() if not output_json.is_absolute() else output_json.resolve()
    img = image.resolve() if image else None

    from gamedev_lab.bench_paint_vram import run_paint_quantization_bench

    sys.exit(
        run_paint_quantization_bench(
            image_path=img,
            target_vram_mb=target_vram_mb,
            output_json=out,
        )
    )


@bench_group.command("pre-quantize")
@click.option(
    "--modelo",
    type=click.Choice(["part3d", "paint3d", "todos"]),
    default="todos",
)
@click.option("--dry-run", is_flag=True, help="Só verificar SDNQ, sem quantizar.")
def bench_pre_quantize_cmd(modelo: str, dry_run: bool) -> None:
    """Pré-quantização SDNQ (DiT Part3D / UNet Paint3D)."""
    from gamedev_lab.pre_quantize import run_pre_quantize_cli

    sys.exit(run_pre_quantize_cli(modelo, dry_run))


@bench_group.command("sdnq-sweep")
@click.option(
    "--mesh",
    type=click.Path(path_type=Path, exists=True),
    required=True,
    help="Mesh GLB de entrada.",
)
@click.option(
    "--image",
    type=click.Path(path_type=Path, exists=True),
    required=True,
    help="Imagem de referência para texturização.",
)
@click.option(
    "--output-dir",
    "-o",
    type=click.Path(path_type=Path),
    default="sdnq_sweep_results",
    help="Diretório de saída.",
)
@click.option(
    "--target-vram-mb",
    type=float,
    default=5500.0,
    show_default=True,
    help="Meta de VRAM máxima em MB.",
)
@click.option(
    "--project-dir",
    type=click.Path(path_type=Path, file_okay=False),
    default=".",
    help="Diretório base para caminhos relativos.",
)
def bench_sdnq_sweep_cmd(
    mesh: Path,
    image: Path,
    output_dir: Path,
    target_vram_mb: float,
    project_dir: Path,
) -> None:
    """Varre configurações SDNQ otimizadas para Paint3D (TinyVAE, attention slicing, etc)."""
    pd = project_dir.resolve()
    out = (pd / output_dir).resolve() if not output_dir.is_absolute() else output_dir.resolve()
    m = mesh.resolve() if mesh.is_absolute() else (pd / mesh).resolve()
    img = image.resolve() if image.is_absolute() else (pd / image).resolve()

    from gamedev_lab.sdnq_optimizer import run_sdnq_sweep_cli

    sys.exit(
        run_sdnq_sweep_cli(
            mesh=m,
            image=img,
            output_dir=out,
            target_vram_mb=target_vram_mb,
        )
    )


@bench_group.command("pipeline-opt")
@click.option(
    "--mesh",
    type=click.Path(path_type=Path, exists=True),
    required=True,
    help="Mesh GLB de entrada.",
)
@click.option(
    "--image",
    type=click.Path(path_type=Path, exists=True),
    required=True,
    help="Imagem de referência para texturização.",
)
@click.option(
    "--output-dir",
    "-o",
    type=click.Path(path_type=Path),
    default="pipeline_opt_results",
    help="Diretório de saída.",
)
@click.option(
    "--target-vram-mb",
    type=float,
    default=5500.0,
    show_default=True,
    help="Meta de VRAM máxima em MB.",
)
@click.option(
    "--steps",
    type=int,
    default=50,
    show_default=True,
    help="Steps para Part3D decompose.",
)
@click.option(
    "--octree",
    type=int,
    default=256,
    show_default=True,
    help="Resolução octree para Part3D.",
)
@click.option(
    "--project-dir",
    type=click.Path(path_type=Path, file_okay=False),
    default=".",
    help="Diretório base para caminhos relativos.",
)
def bench_pipeline_opt_cmd(
    mesh: Path,
    image: Path,
    output_dir: Path,
    target_vram_mb: float,
    steps: int,
    octree: int,
    project_dir: Path,
) -> None:
    """
    Otimiza pipeline completo Part3D+Paint3D iterando configs SDNQ.

    Encontra a melhor combinação de quantização que funciona sem OOM.
    """
    pd = project_dir.resolve()
    out = (pd / output_dir).resolve() if not output_dir.is_absolute() else output_dir.resolve()
    m = mesh.resolve() if mesh.is_absolute() else (pd / mesh).resolve()
    img = image.resolve() if image.is_absolute() else (pd / image).resolve()

    from gamedev_lab.pipeline_optimizer import run_pipeline_optimization_cli

    sys.exit(
        run_pipeline_optimization_cli(
            mesh=m,
            image=img,
            output_dir=out,
            target_vram_mb=target_vram_mb,
            steps=steps,
            octree=octree,
        )
    )


@bench_group.command("batch")
@click.option("--mode", type=click.Choice(["sweep", "test", "dry-run"]), default="dry-run")
@click.option("--config", "config_name", type=str, default=None, help="Nome da config (modo test).")
@click.option("--output-dir", "-o", type=click.Path(path_type=Path), default="test_results")
@click.option(
    "--project-dir",
    type=click.Path(path_type=Path, file_okay=False),
    default=".",
    help="Diretório do exemplo (cwd dos testes).",
)
@click.option(
    "--manifest",
    type=click.Path(path_type=Path, exists=True),
    default=None,
    help="CSV do manifest (ex.: manifest_3obj.csv). Por defeito: project-dir/manifest_3obj.csv",
)
def bench_batch_cmd(
    mode: str,
    config_name: str | None,
    output_dir: Path,
    project_dir: Path,
    manifest: Path | None,
) -> None:
    """Varre configs de batch (gameassets batch) com perfis de quantização."""
    pd = project_dir.resolve()
    man = manifest or (pd / "manifest_3obj.csv")
    out = (pd / output_dir).resolve() if not output_dir.is_absolute() else output_dir.resolve()

    from gamedev_lab.bench_batch import run_batch_bench_cli

    sys.exit(
        run_batch_bench_cli(
            mode,
            config_name,
            out,
            pd,
            man.resolve(),
        )
    )


# ---------------------------------------------------------------------------
# perf
# ---------------------------------------------------------------------------


@main.group("perf")
def perf_group() -> None:
    """Análise de performance (SQLite perf DB)."""


@perf_group.command("list")
@click.option("--tool", "-t", default=None, help="Filtrar por ferramenta (text2d, text3d, …).")
@click.option("--limit", "-n", default=20, show_default=True, type=int, help="Número de runs.")
@click.option("--db", "db_path", type=click.Path(path_type=Path), default=None, help="Caminho perf.db.")
def perf_list_cmd(tool: str | None, limit: int, db_path: Path | None) -> None:
    """Lista runs recentes do perf DB."""
    from gamedev_lab.perf_analyze import list_runs, print_runs_table

    runs = list_runs(tool=tool, limit=limit, db_path=str(db_path) if db_path else None)
    print_runs_table(runs)


@perf_group.command("show")
@click.argument("run_id", type=int)
@click.option("--db", "db_path", type=click.Path(path_type=Path), default=None, help="Caminho perf.db.")
def perf_show_cmd(run_id: int, db_path: Path | None) -> None:
    """Mostra spans detalhados de um run."""
    from gamedev_lab.perf_analyze import print_spans_detail

    print_spans_detail(run_id, db_path=str(db_path) if db_path else None)


@perf_group.command("summary")
@click.option("--tool", "-t", default=None, help="Filtrar por ferramenta.")
@click.option("--gpu", default=None, help="Filtrar por GPU (substring).")
@click.option("--quant", default=None, help="Filtrar por quantização.")
@click.option("--days", default=30, show_default=True, type=int, help="Janela em dias.")
@click.option("--db", "db_path", type=click.Path(path_type=Path), default=None, help="Caminho perf.db.")
def perf_summary_cmd(tool: str | None, gpu: str | None, quant: str | None, days: int, db_path: Path | None) -> None:
    """Resumo agregado por tool + quantização."""
    from gamedev_lab.perf_analyze import print_summary

    print_summary(
        tool=tool,
        gpu_name=gpu,
        quantization_mode=quant,
        days=days,
        db_path=str(db_path) if db_path else None,
    )


@perf_group.command("vram")
@click.option("--tool", "-t", default=None, help="Filtrar por ferramenta.")
@click.option("--gpu", default=None, help="Filtrar por GPU (substring).")
@click.option("--days", default=30, show_default=True, type=int, help="Janela em dias.")
@click.option("--db", "db_path", type=click.Path(path_type=Path), default=None, help="Caminho perf.db.")
def perf_vram_cmd(tool: str | None, gpu: str | None, days: int, db_path: Path | None) -> None:
    """Análise de VRAM por tool + quantização + span."""
    from gamedev_lab.perf_analyze import print_vram_analysis

    print_vram_analysis(
        tool=tool,
        gpu_name=gpu,
        days=days,
        db_path=str(db_path) if db_path else None,
    )


@perf_group.command("recommend")
@click.argument("tool")
@click.option("--vram", "target_vram_mb", type=float, required=True, help="VRAM disponível em MB.")
@click.option("--gpu", default=None, help="Filtrar por GPU (substring).")
@click.option("--days", default=90, show_default=True, type=int, help="Janela em dias.")
@click.option("--db", "db_path", type=click.Path(path_type=Path), default=None, help="Caminho perf.db.")
def perf_recommend_cmd(
    tool: str,
    target_vram_mb: float,
    gpu: str | None,
    days: int,
    db_path: Path | None,
) -> None:
    """Recomenda melhor config de quantização para a tool e VRAM alvo."""
    from gamedev_lab.perf_analyze import print_recommend

    print_recommend(
        tool=tool,
        target_vram_mb=target_vram_mb,
        gpu_name=gpu,
        days=days,
        db_path=str(db_path) if db_path else None,
    )


@perf_group.command("clean")
@click.option("--days", default=90, show_default=True, type=int, help="Apagar runs mais antigos que N dias.")
@click.option("--db", "db_path", type=click.Path(path_type=Path), default=None, help="Caminho perf.db.")
def perf_clean_cmd(days: int, db_path: Path | None) -> None:
    """Apaga runs antigos do perf DB."""
    from gamedev_shared.perfstore.db import PerfDB

    dp = str(db_path) if db_path else None
    with PerfDB(dp) as db:
        deleted = db.delete_old_runs(days=days)
    console.print(f"[green]{deleted} runs apagados[/green] (mais antigos que {days} dias).")


# ---------------------------------------------------------------------------
# profile
# ---------------------------------------------------------------------------


@main.group("profile")
def profile_group() -> None:
    """Profiling (cProfile)."""


@profile_group.command("cprofile")
@click.argument("script", type=click.Path(exists=True, path_type=Path))
@click.option("-o", "--output", type=click.Path(path_type=Path), default=None, help="Ficheiro .prof.")
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
def profile_cprofile(
    script: Path,
    output: Path | None,
    args: tuple[str, ...],
) -> None:
    """Executa um script Python com cProfile (-m cProfile)."""
    prof = output or (script.parent / f"{script.stem}.prof")
    argv = [sys.executable, "-m", "cProfile", "-o", str(prof), str(script)]
    if args:
        # args may start with -- ; filter empty
        argv.extend(args)
    console.print(f"[dim]Escrevendo perfil em {prof}[/dim]")
    r = subprocess.run(argv)
    sys.exit(r.returncode)


# ---------------------------------------------------------------------------
# mesh
# ---------------------------------------------------------------------------


@main.group("mesh")
def mesh_group() -> None:
    """Inspeção de qualidade de mesh (topologia, geometria, artefactos, views)."""


@mesh_group.command("inspect")
@click.argument("mesh_path", type=click.Path(exists=True, path_type=Path))
@click.option("--json-out", type=click.Path(path_type=Path), default=None, help="Gravar relatório JSON.")
@click.option("--verbose", "-v", is_flag=True, help="Mostrar tabelas Rich detalhadas.")
def mesh_inspect_cmd(mesh_path: Path, json_out: Path | None, verbose: bool) -> None:
    """Analisa qualidade de mesh (topologia, geometria, artefactos)."""
    from gamedev_lab.mesh_inspector import MeshInspector, print_qa_report

    inspector = MeshInspector(mesh_path)
    report = inspector.inspect()

    if verbose:
        print_qa_report(report)
    else:
        console.print(f"[bold]Grade:[/bold] {report.score.grade} ({report.score.overall:.0%})")
        console.print(f"[bold]Passed:[/bold] {'[green]YES[/green]' if report.passed() else '[red]NO[/red]'}")
        console.print(
            f"[bold]Vertices:[/bold] {report.topology.vertices:,}  [bold]Faces:[/bold] {report.topology.faces:,}"
        )
        console.print(f"[bold]Watertight:[/bold] {'Yes' if report.topology.watertight else 'No'}")
        if report.artifacts.issues:
            console.print(f"[bold]Issues ({len(report.artifacts.issues)}):[/bold]")
            for issue in report.artifacts.issues:
                console.print(f"  [red]- {issue}[/red]")
        else:
            console.print("[green]No artifacts detected.[/green]")
        console.print(f"\n[dim]{report.score.summary}[/dim]")

    if json_out:
        report.save_json(json_out)
        console.print(f"[dim]Report: {json_out}[/dim]")

    sys.exit(0 if report.passed() else 1)


@mesh_group.command("qa")
@click.argument("mesh_path", type=click.Path(exists=True, path_type=Path))
@click.option("--output-dir", "-o", type=click.Path(path_type=Path), default=None, help="Diretório de saída.")
@click.option(
    "--reference-image",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Imagem de referência para comparação visual.",
)
@click.option(
    "--views",
    default="front,three_quarter,right,back,top,low_front",
    show_default=True,
    help="Vistas para render (separadas por vírgula).",
)
@click.option("--resolution", "-r", default=512, show_default=True, type=int, help="Resolução px.")
@click.option(
    "--engine",
    type=click.Choice(["workbench", "eevee"]),
    default="workbench",
    show_default=True,
)
def mesh_qa_cmd(
    mesh_path: Path,
    output_dir: Path | None,
    reference_image: Path | None,
    views: str,
    resolution: int,
    engine: str,
) -> None:
    """QA completo: inspect + render views + comparação com referência."""
    from gamedev_lab.mesh_inspector import MeshInspector, print_qa_report

    out = output_dir or mesh_path.parent / f"{mesh_path.stem}_qa"
    inspector = MeshInspector(mesh_path)

    report = inspector.inspect_with_views(
        out,
        animator3d_bin=resolve_animator3d_bin(),
        views=views,
        resolution=resolution,
        reference_image=reference_image,
        engine=engine,
    )

    print_qa_report(report)

    if reference_image and report.reference_comparison:
        console.print(f"\n[bold]Reference comparison:[/bold] {len(report.reference_comparison)} views")
        avg_ssim = sum(c.get("ssim", 0) for c in report.reference_comparison) / max(len(report.reference_comparison), 1)
        ssim_style = "green" if avg_ssim >= 0.8 else ("yellow" if avg_ssim >= 0.5 else "red")
        console.print(f"  Average SSIM: [{ssim_style}]{avg_ssim:.4f}[/{ssim_style}]")

    console.print(f"\n[dim]Report: {out / 'qa_report.json'}[/dim]")
    console.print(f"[dim]Views: {out / 'views'}/[/dim]")

    sys.exit(0 if report.passed() else 1)


@mesh_group.command("render-views")
@click.argument("mesh_path", type=click.Path(exists=True, path_type=Path))
@click.option("--output-dir", "-o", type=click.Path(path_type=Path), default=None, help="Diretório de saída.")
@click.option(
    "--views",
    default="front,three_quarter,right,back,top,low_front",
    show_default=True,
    help="Vistas separadas por vírgula.",
)
@click.option("--resolution", "-r", default=512, show_default=True, type=int, help="Resolução px.")
@click.option(
    "--engine",
    type=click.Choice(["workbench", "eevee"]),
    default="workbench",
    show_default=True,
)
def mesh_render_views_cmd(
    mesh_path: Path,
    output_dir: Path | None,
    views: str,
    resolution: int,
    engine: str,
) -> None:
    """Renderiza vistas do GLB para inspeção visual."""
    from gamedev_lab.mesh_inspector import MeshInspector

    out = output_dir or mesh_path.parent / f"{mesh_path.stem}_views"
    inspector = MeshInspector(mesh_path)

    rendered = inspector._render_views(
        out,
        animator3d_bin=resolve_animator3d_bin(),
        views=views,
        resolution=resolution,
        engine=engine,
    )

    if rendered:
        console.print(f"[green]{len(rendered)} views[/green] em {out}")
        for p in rendered:
            console.print(f"  {p}")
    else:
        console.print("[yellow]animator3d não encontrado — sem renderização.[/yellow]")
        console.print("[dim]Instale Animator3D ou defina ANIMATOR3D_BIN para renderizar vistas.[/dim]")


@mesh_group.command("diff")
@click.argument("mesh_a", type=click.Path(exists=True, path_type=Path))
@click.argument("mesh_b", type=click.Path(exists=True, path_type=Path))
@click.option("--json-out", type=click.Path(path_type=Path), default=None, help="Gravar diff JSON em ficheiro.")
def mesh_diff_cmd(mesh_a: Path, mesh_b: Path, json_out: Path | None) -> None:
    """Compara dois meshes topologicamente (vértices, faces, buracos, UV seams, Euler, volume)."""
    from rich.table import Table

    from gamedev_lab.mesh_inspector import MeshInspector

    inspector_a = MeshInspector(mesh_a)
    inspector_b = MeshInspector(mesh_b)
    report_a = inspector_a.inspect()
    report_b = inspector_b.inspect()

    ta = report_a.topology
    tb = report_b.topology
    ga = report_a.geometry
    gb = report_b.geometry

    def _delta(a: int | float, b: int | float) -> str:
        d = b - a
        if d > 0:
            return f"[green]+{d}[/green]"
        elif d < 0:
            return f"[red]{d}[/red]"
        return "[dim]0[/dim]"

    t = Table(title=f"Mesh Diff: {mesh_a.name} vs {mesh_b.name}")
    t.add_column("Metric", style="cyan")
    t.add_column(mesh_a.name, justify="right")
    t.add_column(mesh_b.name, justify="right")
    t.add_column("Delta", justify="right")

    t.add_row("Vertices", f"{ta.vertices:,}", f"{tb.vertices:,}", _delta(ta.vertices, tb.vertices))
    t.add_row("Faces", f"{ta.faces:,}", f"{tb.faces:,}", _delta(ta.faces, tb.faces))
    t.add_row("Edges", f"{ta.edges:,}", f"{tb.edges:,}", _delta(ta.edges, tb.edges))
    t.add_row(
        "Boundary Edges",
        str(ta.boundary_edges),
        str(tb.boundary_edges),
        _delta(ta.boundary_edges, tb.boundary_edges),
    )
    t.add_row("  Real Holes", str(ta.real_holes), str(tb.real_holes), _delta(ta.real_holes, tb.real_holes))
    t.add_row(
        "  UV Seam Edges",
        str(ta.uv_seam_edges),
        str(tb.uv_seam_edges),
        _delta(ta.uv_seam_edges, tb.uv_seam_edges),
    )
    t.add_row("Euler Number", str(ta.euler_number), str(tb.euler_number), _delta(ta.euler_number, tb.euler_number))
    t.add_row("Watertight", "Yes" if ta.watertight else "No", "Yes" if tb.watertight else "No", "")
    t.add_row(
        "Degenerate Faces",
        str(ta.degenerate_faces),
        str(tb.degenerate_faces),
        _delta(ta.degenerate_faces, tb.degenerate_faces),
    )
    t.add_row(
        "Duplicate Vertices",
        str(ta.duplicate_vertices),
        str(tb.duplicate_vertices),
        _delta(ta.duplicate_vertices, tb.duplicate_vertices),
    )

    vol_a = f"{ga.volume:.4f}" if ga.volume is not None else "N/A"
    vol_b = f"{gb.volume:.4f}" if gb.volume is not None else "N/A"
    t.add_row("Volume", vol_a, vol_b, "")

    console.print(t)

    diff_report: dict[str, Any] = {
        "mesh_a": str(mesh_a.resolve()),
        "mesh_b": str(mesh_b.resolve()),
        "vertices": {"a": ta.vertices, "b": tb.vertices, "delta": tb.vertices - ta.vertices},
        "faces": {"a": ta.faces, "b": tb.faces, "delta": tb.faces - ta.faces},
        "edges": {"a": ta.edges, "b": tb.edges, "delta": tb.edges - ta.edges},
        "boundary_edges": {
            "a": ta.boundary_edges,
            "b": tb.boundary_edges,
            "delta": tb.boundary_edges - ta.boundary_edges,
        },
        "real_holes": {"a": ta.real_holes, "b": tb.real_holes, "delta": tb.real_holes - ta.real_holes},
        "uv_seam_edges": {
            "a": ta.uv_seam_edges,
            "b": tb.uv_seam_edges,
            "delta": tb.uv_seam_edges - ta.uv_seam_edges,
        },
        "euler_number": {"a": ta.euler_number, "b": tb.euler_number, "delta": tb.euler_number - ta.euler_number},
        "watertight": {"a": ta.watertight, "b": tb.watertight},
        "degenerate_faces": {
            "a": ta.degenerate_faces,
            "b": tb.degenerate_faces,
            "delta": tb.degenerate_faces - ta.degenerate_faces,
        },
        "duplicate_vertices": {
            "a": ta.duplicate_vertices,
            "b": tb.duplicate_vertices,
            "delta": tb.duplicate_vertices - ta.duplicate_vertices,
        },
        "volume": {"a": ga.volume, "b": gb.volume},
        "volume_efficiency": {"a": ga.volume_efficiency, "b": gb.volume_efficiency},
        "area": {"a": ga.area, "b": gb.area, "delta": round(gb.area - ga.area, 4)},
    }

    if json_out:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(json.dumps(diff_report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        console.print(f"[dim]Diff report: {json_out}[/dim]")


if __name__ == "__main__":
    main()
