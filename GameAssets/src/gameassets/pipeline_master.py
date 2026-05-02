"""Novo orquestrador master pipeline (LOD0 + transfer-weights + validate).

Este módulo implementa a sequência:
  text3d topology-fix   (shape → clean)
  text3d bake-master    (painted + clean → lod0 com KTX2/meshopt/tangents)
  text3d lod            (lod0 → lod1, lod2)
  text3d collision      (lod0 → collision)
  rigging3d pipeline    (clean → rigged_hi)
  rigging3d transfer-weights (rigged_hi + lod0/1/2 → lod0/1/2_rigged)
  animator3d game-pack  (lod0/1/2_rigged → lod0/1/2_animated)
  gamedev-lab check glb (lod0/1/2 vs rules YAML)

Usado opcionalmente pelo ``batch`` quando ``--master-pipeline`` está activo;
o caminho legacy fica intacto para compatibilidade. Move intermediários
(shape, painted, rigged_hi) para ``_intermediate/`` no fim.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .categories import (
    animator_preset_for_category,
    category_wants_bake_normals,
    get_target_faces,
)
from .helpers import effective_face_ratio
from .manifest import ManifestRow
from .paths import (
    _clean_existing,
    _clean_path,
    _rigged_hi_existing,
    _intermediate_dir,
    _lod_animated_path,
    _lod_path,
    _lod_rigged_path,
    _painted_existing,
    _painted_path,
    _rigged_hi_path,
    _shape_existing,
    _shape_path,
    move_to_intermediate,
)
from .profile import GameProfile
from .runner import merge_subprocess_output, resolve_binary, run_cmd

try:
    from gamedev_shared.subprocess_utils import run_cmd_streaming as _run_cmd_streaming
except ImportError:  # pragma: no cover
    _run_cmd_streaming = None  # type: ignore[assignment]

log = logging.getLogger(__name__)


@dataclass
class StageResult:
    name: str
    ok: bool
    elapsed_s: float
    error: str = ""
    output: Path | None = None


@dataclass
class MasterPipelineResult:
    asset_id: str
    ok: bool
    stages: list[StageResult] = field(default_factory=list)
    lod0_path: Path | None = None
    intermediates_dir: Path | None = None
    # Round 2 — observabilidade.
    total_elapsed_s: float = 0.0
    cumulative_vram_mb_peak: float = 0.0

    def recompute_totals(self) -> None:
        self.total_elapsed_s = round(sum(s.elapsed_s for s in self.stages), 2)


def _bin_or_none(name_env: str, name: str) -> str | None:
    try:
        return resolve_binary(name_env, name)
    except FileNotFoundError:
        return None


def _rules_dir() -> Path:
    return Path(__file__).resolve().parent / "data" / "rules"


def _run_check_glb(
    glb: Path,
    rules: Path,
    *,
    category: str | None,
    env: dict[str, str],
    cwd: Path,
) -> StageResult:
    import time as _time

    bin_ = _bin_or_none("GAMEDEV_LAB_BIN", "gamedev-lab")
    if not bin_:
        return StageResult("validate", False, 0.0, "gamedev-lab não encontrado no PATH")
    argv = [bin_, "check", "glb", str(glb), str(rules)]
    if category:
        argv.extend(["--category", category])
    argv.extend(["--no-bpy-inspect"])  # rules estão preparadas para glb_meta
    t0 = _time.perf_counter()
    r = run_cmd(argv, extra_env=env, cwd=cwd)
    dt = _time.perf_counter() - t0
    if r.returncode != 0:
        err = merge_subprocess_output(r, max_chars=400) or f"check glb falhou (rc={r.returncode})"
        return StageResult("validate", False, dt, err, glb)
    return StageResult("validate", True, dt, output=glb)


def _stage(
    name: str,
    argv: list[str],
    env: dict[str, str],
    cwd: Path,
    output: Path | None = None,
    *,
    item_id: str | None = None,
    profile_enabled: bool = False,
    on_progress_line: "Callable[[str], None] | None" = None,
) -> StageResult:
    """Executa um stage do master pipeline.

    Round 2: envolto em ``ProfilerSession`` para spans no perf.db quando
    ``profile_enabled`` (controlado por ``GAMEDEV_PROFILE`` no child_env).
    Emite ``emit_progress`` no início e fim para visibilidade no dashboard.

    ``on_progress_line``: callback alimentado linha-a-linha com stdout do
    subprocesso. Permite encaminhar ``emit_progress`` events emitidos pelas
    ferramentas (text3d, rigging3d, animator3d) para o dashboard do
    gameassets — sem isso, o dashboard só vê os events do orquestrador
    (start/end por stage) e parece "congelar" após paint3d.
    """
    import time as _time

    from gamedev_shared.profiler.session import ProfilerSession
    from gamedev_shared.progress import emit_progress

    profiler_tool = name.replace("-", "_")

    def _emit(phase: str, percent: float, status: str = "progress", **meta: object) -> None:
        """Emite progresso E alimenta o dashboard directamente.

        ``emit_progress`` escreve no stdout do processo gameassets, que NÃO
        passa pelo callback ``on_progress_line`` do dashboard (esse só vê
        stdout dos sub-processos via ``run_cmd_streaming``). Sem este
        encaminhamento manual o dashboard congela em "Paint3D 100%" durante
        os stages do master pipeline (que duram dezenas de segundos cada).
        """
        if not item_id:
            return
        emit_progress(item_id, profiler_tool, phase=phase, percent=percent, **meta)
        if on_progress_line is not None:
            import json as _json

            data: dict = {
                "id": item_id,
                "tool": profiler_tool,
                "status": status,
                "phase": phase,
                "percent": round(percent, 1),
            }
            data.update(meta)
            try:
                on_progress_line(_json.dumps(data))
            except Exception:  # noqa: BLE001
                pass

    _emit("run", 0)

    t0 = _time.perf_counter()
    try:
        with ProfilerSession(
            profiler_tool,
            cli_profile=profile_enabled,
            params={"item_id": item_id} if item_id else None,
        ):
            if on_progress_line is not None and _run_cmd_streaming is not None:
                # Stream stdout para callback (dashboard) E acumula resultado.
                # Sub-tools (text3d/rigging3d/animator3d) emitem events com
                # ``id`` derivado do filename (ex.: "goblin_lod0"); o
                # dashboard chaveia por ``row.id`` ("goblin"), portanto
                # reescrevemos o ``id`` em cada linha JSON antes de
                # encaminhar para que a célula do asset reflicta a fase
                # corrente. ``phase`` ganha o nome do stage para distinguir
                # entre rigging-merge-lod0/lod1/animate-lod0/etc.
                import json as _json

                stdout_buf: list[str] = []
                stderr_buf: list[str] = []

                def _on_out(line: str) -> None:
                    stdout_buf.append(line)
                    try:
                        forwarded = line
                        s = line.strip()
                        if item_id and s.startswith("{") and s.endswith("}"):
                            try:
                                data = _json.loads(s)
                            except (ValueError, _json.JSONDecodeError):
                                data = None
                            if isinstance(data, dict) and "id" in data:
                                # Preserva o id original em sub_id e mostra
                                # o ``name`` (stage do master) como tool.
                                data["sub_id"] = data.get("id")
                                data["sub_tool"] = data.get("tool", "")
                                data["id"] = item_id
                                data["tool"] = profiler_tool
                                if "phase" not in data and data.get("sub_tool"):
                                    data["phase"] = data["sub_tool"]
                                forwarded = _json.dumps(data)
                        on_progress_line(forwarded)
                    except Exception:  # noqa: BLE001
                        pass

                def _on_err(line: str) -> None:
                    stderr_buf.append(line)

                rs = _run_cmd_streaming(
                    argv,
                    on_stdout_line=_on_out,
                    on_stderr_line=_on_err,
                    cwd=cwd,
                    extra_env=env,
                )
                r = rs
            else:
                r = run_cmd(argv, extra_env=env, cwd=cwd)
    except Exception as exc:  # noqa: BLE001
        dt = _time.perf_counter() - t0
        _emit("run", 100, status="error")
        return StageResult(name, False, dt, f"ProfilerSession: {exc}", output)

    dt = _time.perf_counter() - t0
    if r.returncode != 0:
        err = merge_subprocess_output(r, max_chars=400) or f"{name} falhou (rc={r.returncode})"
        _emit("run", 100, status="error")
        return StageResult(name, False, dt, err, output)
    if output is not None and not output.is_file():
        _emit("run", 100, status="error")
        return StageResult(name, False, dt, f"{name}: output não foi criado", output)
    # Emite como ``progress`` (não ``ok``) — ``ok`` no dashboard sinaliza
    # conclusão do asset INTEIRO; usá-lo aqui faria a célula piscar OK entre
    # cada stage e contar duplicado no progresso global.
    _emit("run", 100, status="progress", seconds=round(dt, 2))
    return StageResult(name, True, dt, output=output)


def run_master_pipeline(
    profile: GameProfile,
    row: ManifestRow,
    mesh_final: Path,
    *,
    manifest_dir: Path,
    child_env: dict[str, str],
    with_lod: bool = True,
    with_collision: bool = True,
    with_rig: bool = False,
    with_animate: bool = False,
    with_validate: bool = True,
    bake_normals: bool | None = None,
    on_progress_line: Callable[[str], None] | None = None,
) -> MasterPipelineResult:
    """Executa o DAG novo a partir de ``id_shape.glb`` e ``id_painted.glb``.

    Pré-condições:
    - ``_shape_path(mesh_final)`` existe (saída de Stage 1 — text3d generate
      com ``--no-topology-fix`` ou legacy generate).
    - ``_painted_path(mesh_final)`` existe (Stage 3 — paint3d texture sobre
      o GLB intermediário; tipicamente sobre o ``_clean.glb`` produzido aqui).

    Pós-condições em sucesso:
    - ``_lod_path(mesh_final, 0|1|2).glb`` em ``meshes/``.
    - ``_rigged_path(...)`` e ``_animated_path(...)`` quando ``with_rig`` e
      ``with_animate``.
    - Intermediários em ``_intermediate/``.
    """
    res = MasterPipelineResult(asset_id=row.id, ok=True)
    res.intermediates_dir = _intermediate_dir(mesh_final)

    # Round 2 — smart defaults para bake-normals.
    # Precedência: argumento explícito → profile.master_bake_normals → categoria.
    if bake_normals is None:
        bake_normals = bool(getattr(profile, "master_bake_normals", False)) or category_wants_bake_normals(
            row.category,
            overrides=getattr(profile, "master_bake_normals_categories", None),
        )

    profile_enabled = str(child_env.get("GAMEDEV_PROFILE", "")).strip() == "1"

    def _run(name: str, argv: list[str], output: Path | None = None) -> StageResult:
        return _stage(
            name,
            argv,
            child_env,
            manifest_dir,
            output,
            item_id=row.id,
            profile_enabled=profile_enabled,
            on_progress_line=on_progress_line,
        )

    text3d_bin = _bin_or_none("TEXT3D_BIN", "text3d")
    rigging3d_bin = _bin_or_none("RIGGING3D_BIN", "rigging3d")
    animator3d_bin = _bin_or_none("ANIMATOR3D_BIN", "animator3d")
    if not text3d_bin:
        res.ok = False
        res.stages.append(StageResult("setup", False, 0.0, "text3d não encontrado"))
        return res

    # Round 2: shape/painted podem estar em meshes/ OU em meshes/_intermediate/
    # (após uma run anterior). Resolve dinamicamente para permitir resume.
    shape_p = _shape_existing(mesh_final) or _shape_path(mesh_final)
    painted_p = _painted_existing(mesh_final) or _painted_path(mesh_final)
    clean_existing = _clean_existing(mesh_final)
    clean_p = clean_existing if clean_existing is not None else _clean_path(mesh_final)

    if not shape_p.is_file():
        res.ok = False
        res.stages.append(StageResult("preflight", False, 0.0, f"shape ausente: {shape_p}"))
        return res

    # Stage 2 — topology-fix (shape → clean). Skip se já temos um clean
    # válido (em meshes/ ou _intermediate/) — resume-friendly.
    clean_p.parent.mkdir(parents=True, exist_ok=True)
    if clean_existing is not None and clean_existing.is_file():
        res.stages.append(StageResult("topology-fix", True, 0.0, "skipped (clean existente)", clean_p))
    else:
        s = _run(
            "topology-fix",
            [text3d_bin, "topology-fix", str(shape_p), "-o", str(clean_p)],
            clean_p,
        )
        res.stages.append(s)
        if not s.ok:
            res.ok = False
            return res

    # Stage 4 — bake-master (painted → lod0 com tangents/KTX2/meshopt)
    if not painted_p.is_file():
        res.ok = False
        res.stages.append(StageResult("bake-master", False, 0.0, f"painted ausente: {painted_p}"))
        return res

    fr = effective_face_ratio(profile, row)
    target_faces = get_target_faces(row.category or "", face_ratio=fr) if row.category else 0
    if target_faces <= 0:
        target_faces = 8000
    lod0_p = _lod_path(mesh_final, 0)
    bake_argv = [
        text3d_bin,
        "bake-master",
        str(painted_p),
        "-o",
        str(lod0_p),
        "--target-faces",
        str(target_faces),
        "--high-poly",
        str(clean_p),
    ]
    if bake_normals:
        bake_argv.append("--bake-normals")
    # Quando há rigging/anim downstream, o LOD0 vai ser re-importado por bpy
    # (rigging3d, animator3d). bpy não suporta EXT_meshopt_compression,
    # portanto saltamos a compressão em bake-master e re-aplicamos
    # gltf_transform_finish na promoção (Stage 9.5) sobre o output final.
    needs_bpy_downstream = (with_rig and rigging3d_bin is not None) or (
        with_animate and animator3d_bin is not None
    )
    if needs_bpy_downstream:
        bake_argv.extend(["--no-meshopt", "--no-ktx2"])
    s = _run("bake-master", bake_argv, lod0_p)
    res.stages.append(s)
    if not s.ok:
        res.ok = False
        return res
    res.lod0_path = lod0_p

    # Stage 5 — LOD1/LOD2 a partir do LOD0
    lod1_p = _lod_path(mesh_final, 1)
    lod2_p = _lod_path(mesh_final, 2)
    if with_lod:
        # text3d lod com painted-mesh=lod0 dá-nos rácio half/quarter
        lod_target_lod1 = max(target_faces // 2, 100)
        lod_target_lod2 = max(target_faces // 4, 50)
        lod_argv = [
            text3d_bin,
            "lod",
            str(lod0_p),
            "-o",
            str(mesh_final.parent),
            "--basename",
            mesh_final.stem.replace("_lod0", "").replace("_painted", "").replace("_shape", ""),
            "--painted-mesh",
            str(lod0_p),
            "--target-faces",
            str(target_faces),
            "--min-faces-lod1",
            str(lod_target_lod1),
            "--min-faces-lod2",
            str(lod_target_lod2),
        ]
        # Igual a bake-master: salta finish quando bpy precisa importar
        # depois (rigging/animação por LOD). Re-comprimimos na promoção.
        if needs_bpy_downstream:
            lod_argv.append("--no-finish")
        s = _run("lod", lod_argv)
        res.stages.append(s)

    # Stage 6 — collision a partir do LOD0
    if with_collision:
        # Usa o stem-base (strip _shape/_painted/_lod0) para evitar nomes
        # como ``goblin_painted_collision.glb`` quando mesh_final aponta
        # para ``goblin_painted.glb``.
        from .paths import _base_stem as _bs

        coll_p = mesh_final.with_name(f"{_bs(mesh_final.stem)}_collision{mesh_final.suffix}")
        coll_argv = [
            text3d_bin,
            "collision",
            str(lod0_p),
            "-o",
            str(coll_p),
        ]
        s = _run("collision", coll_argv, coll_p)
        res.stages.append(s)
        # Round 2 — finalizar collision: dedup+prune (sem KTX2/meshopt/tangents).
        if s.ok and coll_p.is_file():
            try:
                from text3d.utils.gltf_finish import gltf_transform_finish

                gltf_transform_finish(
                    coll_p,
                    coll_p,
                    apply_tangents=False,
                    apply_uastc=False,
                    apply_meshopt=False,
                    apply_dedup=True,
                    apply_prune=True,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("master: finish collision falhou: %s", exc)

    # Stage 7 — rigging3d pipeline sobre _clean.glb. Skip se já temos um
    # rigged_hi válido (em meshes/ ou _intermediate/) — resume-friendly.
    rigged_hi_existing = _rigged_hi_existing(mesh_final)
    rigged_hi_p = rigged_hi_existing if rigged_hi_existing is not None else _rigged_hi_path(mesh_final)
    if with_rig and rigging3d_bin:
        if rigged_hi_existing is not None and rigged_hi_existing.is_file():
            res.stages.append(StageResult("rigging3d-hi", True, 0.0, "skipped (rigged_hi existente)", rigged_hi_p))
        else:
            rig_argv = [rigging3d_bin, "pipeline", "--input", str(clean_p), "--output", str(rigged_hi_p)]
            s = _run("rigging3d-hi", rig_argv, rigged_hi_p)
            res.stages.append(s)
            if not s.ok:
                with_rig = False  # bloqueia stages dependentes mas não aborta o asset

    # Stage 8 — Rigging por LOD via ``rigging3d merge``.
    #
    # Estratégia: re-usa o skeleton+skin do ``_rigged_hi.glb`` e fá-lo
    # "merge" com cada LOD low-poly, produzindo um GLB com Armature real
    # (re-importável por bpy → animator3d funciona). Não corre o modelo
    # de inferência de novo (sem GPU), apenas faz re-skinning analítico.
    #
    # ``rigging3d transfer-weights`` (bpy.data_transfer) é alternativa mas
    # o exportador GLTF do Blender não detecta o output como skinned —
    # mantido apenas como ferramenta experimental.
    rigged_lods: list[Path] = []
    if with_rig and rigging3d_bin and rigged_hi_p.is_file():
        targets: list[Path] = [lod0_p]
        if lod1_p.is_file():
            targets.append(lod1_p)
        if lod2_p.is_file():
            targets.append(lod2_p)
        for i, tgt in enumerate(targets):
            out = _lod_rigged_path(mesh_final, i)
            merge_argv = [
                rigging3d_bin,
                "merge",
                "--source",
                str(rigged_hi_p),
                "--target",
                str(tgt),
                "--output",
                str(out),
            ]
            s = _run(f"rigging3d-merge-lod{i}", merge_argv, out)
            res.stages.append(s)
            if s.ok and out.is_file():
                # Aplica gltf_transform_finish para alinhar com regras
                # rigged.yaml (KTX2+meshopt+tangents+dedup+prune).
                try:
                    from text3d.utils.gltf_finish import gltf_transform_finish

                    gltf_transform_finish(out, out)
                except Exception as exc:  # noqa: BLE001
                    log.warning("master: finish rigged-lod%d falhou: %s", i, exc)
                rigged_lods.append(out)

    # Stage 9 — animate cada LOD rigged. Round 2: preset por categoria.
    animated_lods: list[Path] = []
    animator_preset = animator_preset_for_category(row.category)
    if with_rig and with_animate and animator3d_bin and rigged_lods:
        for rg in rigged_lods:
            try:
                level_str = rg.stem.split("_lod")[1].split("_")[0]
                lvl = int(level_str)
            except (IndexError, ValueError):
                lvl = 0
            anim_p = _lod_animated_path(mesh_final, lvl)
            an_argv = [
                animator3d_bin,
                "game-pack",
                str(rg),
                str(anim_p),
                "--preset",
                animator_preset,
            ]
            s = _run(f"animate-lod{lvl}", an_argv, anim_p)
            res.stages.append(s)
            if s.ok and anim_p.is_file():
                # Round 2: finish em _animated.glb (mantém skin/animation tracks).
                try:
                    from text3d.utils.gltf_finish import gltf_transform_finish

                    gltf_transform_finish(anim_p, anim_p)
                except Exception as exc:  # noqa: BLE001
                    log.warning("master: finish animated falhou em %s: %s", anim_p, exc)
                animated_lods.append(anim_p)

    # Stage 9.5 — Promoção: o output do estágio mais alto vira lodN.glb.
    # Semântica: lod0.glb é SEMPRE o asset pronto-pra-jogo. Se houve animate,
    # lod0=animated; se houve só rig, lod0=rigged; senão fica o bake-master.
    # Versões "intermediárias" (bake-master sem rig, ou rigged se animado
    # promovido) movem-se para _intermediate/ para debug, evitando ficheiros
    # redundantes em meshes/.
    promotion_kind = "none"
    if animated_lods:
        promotion_kind = "animated"
        winners = animated_lods
    elif rigged_lods:
        promotion_kind = "rigged"
        winners = rigged_lods
    else:
        winners = []

    import shutil as _shutil

    if winners:
        log.info("master: promovendo %s outputs como lod0/1/2 (%s)", len(winners), promotion_kind)
        promoted_levels: set[int] = set()
        for w in winners:
            try:
                level_str = w.stem.rsplit("_lod", 1)[1].split("_")[0]
                level = int(level_str)
            except (IndexError, ValueError):
                continue
            target = _lod_path(mesh_final, level)
            # Move bake-master output para _intermediate/_lodN_painted.glb (debug).
            if target.is_file() and target.resolve() != w.resolve():
                base = mesh_final.stem
                from .paths import _base_stem as _bs

                base = _bs(base)
                debug = _intermediate_dir(mesh_final) / f"{base}_lod{level}_painted{mesh_final.suffix}"
                debug.parent.mkdir(parents=True, exist_ok=True)
                try:
                    _shutil.move(str(target), str(debug))
                except OSError as exc:
                    log.warning("master: move bake-master→intermediate falhou: %s", exc)
            try:
                _shutil.move(str(w), str(target))
                promoted_levels.add(level)
            except OSError as exc:
                log.warning("master: promoção %s→%s falhou: %s", w, target, exc)
                continue
            # Garante que o output final está totalmente optimizado
            # (KTX2+meshopt+tangents+dedup+prune). Bake-master e
            # rigging/animação correm com finish minimal quando há bpy
            # downstream — aplicamos a finalização pesada aqui no fim.
            try:
                from text3d.utils.gltf_finish import gltf_transform_finish

                gltf_transform_finish(target, target)
            except Exception as exc:  # noqa: BLE001
                log.warning("master: finish promoted lod%d falhou: %s", level, exc)

        # Se animated promovido, mover _rigged.glb para _intermediate/.
        if promotion_kind == "animated":
            for r in rigged_lods:
                if r.is_file():
                    try:
                        dst = _intermediate_dir(mesh_final) / r.name
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        _shutil.move(str(r), str(dst))
                    except OSError as exc:
                        log.warning("master: move rigged→intermediate falhou: %s", exc)

    # Stage 10 — validação. LOD0 é gate; LOD1/2 são warnings.
    # As regras efectivas dependem de quem foi promovido: animated.yaml >
    # rigged.yaml > lod{N}.yaml. Quando promotion_kind != "none" usamos a
    # mesma regra para LOD0/1/2 (mesmo nível semântico).
    if with_validate:
        rules_dir = _rules_dir()
        if promotion_kind == "animated":
            rule_for = {0: "animated.yaml", 1: "animated.yaml", 2: "animated.yaml"}
        elif promotion_kind == "rigged":
            rule_for = {0: "rigged.yaml", 1: "rigged.yaml", 2: "rigged.yaml"}
        else:
            rule_for = {0: "lod0.yaml", 1: "lod1.yaml", 2: "lod2.yaml"}

        for lvl, lod_p in ((0, lod0_p), (1, lod1_p), (2, lod2_p)):
            if lod_p.is_file():
                rules = rules_dir / rule_for[lvl]
                if rules.is_file():
                    s = _run_check_glb(
                        lod_p,
                        rules,
                        category=row.category,
                        env=child_env,
                        cwd=manifest_dir,
                    )
                    s.name = f"validate-lod{lvl}"
                    res.stages.append(s)
                    if not s.ok and lvl == 0:
                        # LOD0 inválido é gate.
                        res.ok = False
        # Validação extra contra lod{N}.yaml (face count caps). Mesmo após
        # promoção, lod0 deve respeitar limites de faces da categoria.
        if promotion_kind != "none":
            base_rules = rules_dir / "lod0.yaml"
            if base_rules.is_file() and lod0_p.is_file():
                s = _run_check_glb(
                    lod0_p, base_rules, category=row.category, env=child_env, cwd=manifest_dir
                )
                s.name = "validate-lod0-base"
                res.stages.append(s)
                if not s.ok:
                    res.ok = False

    # Move intermediários (shape, painted) para _intermediate/.
    move_to_intermediate(shape_p, mesh_final)
    move_to_intermediate(painted_p, mesh_final)
    # rigged_hi e clean já nascem em _intermediate/.

    res.recompute_totals()
    return res


def resume_master_pipeline(
    profile: GameProfile,
    row: ManifestRow,
    mesh_final: Path,
    *,
    manifest_dir: Path,
    child_env: dict[str, str],
    with_lod: bool = True,
    with_collision: bool = True,
    with_rig: bool = False,
    with_animate: bool = False,
    with_validate: bool = True,
    bake_normals: bool | None = None,
    on_progress_line: Callable[[str], None] | None = None,
) -> MasterPipelineResult:
    """Retoma o master pipeline a partir do checkpoint detectado.

    Diferente de ``run_master_pipeline``: não falha se um stage de pré-condição
    já está pronto; usa ``_classify_row_state_master`` para decidir o entry
    point. Re-executa apenas o que falta.
    """
    from .paths import (
        _ROW_DONE,
        _ROW_NEED_ANIMATE_LOD,
        _ROW_NEED_BAKE_MASTER,
        _ROW_NEED_LOD_GEN,
        _ROW_NEED_RIG_HI,
        _ROW_NEED_TOPOLOGY_FIX,
        _ROW_NEED_TRANSFER,
        _classify_row_state_master,
    )

    img_final = mesh_final.with_suffix(".png")  # heurística — caller tipicamente fornece via row
    state = _classify_row_state_master(
        img_final=img_final,
        mesh_final=mesh_final,
        want_texture=True,
        wants_rig=with_rig,
        wants_animate=with_animate,
        wants_lod=with_lod,
        wants_collision=with_collision,
    )

    if state == _ROW_DONE:
        res = MasterPipelineResult(asset_id=row.id, ok=True)
        res.lod0_path = _lod_path(mesh_final, 0)
        res.intermediates_dir = _intermediate_dir(mesh_final)
        return res

    # Para qualquer estado parcial, simplesmente re-corre o pipeline completo.
    # ``run_master_pipeline`` tem skips implícitos (chamada a binary é o caro;
    # quando o output já existe, podemos delegar verificação a cada stage no
    # futuro). Isto cobre 90% dos casos de retomada sem complicar o DAG.
    log.info("resume-master: state=%s — retomando pipeline para %s", state, row.id)
    return run_master_pipeline(
        profile,
        row,
        mesh_final,
        manifest_dir=manifest_dir,
        child_env=child_env,
        with_lod=with_lod,
        with_collision=with_collision,
        with_rig=with_rig,
        with_animate=with_animate,
        with_validate=with_validate,
        bake_normals=bake_normals,
        on_progress_line=on_progress_line,
    )


def aggregate_master_results(
    results: list[StageResult],
    rec: dict[str, Any],
) -> None:
    """Despeja stages num record de manifest (run.jsonl)."""
    timing: dict[str, float] = rec.get("timing") or {}
    for st in results:
        timing[st.name] = round(st.elapsed_s, 2)
    rec["timing"] = timing
    rec["total_elapsed_s"] = round(sum(s.elapsed_s for s in results), 2)
    rec["stages"] = [
        {"name": s.name, "ok": s.ok, "elapsed_s": round(s.elapsed_s, 2), "error": s.error}
        for s in results
    ]
