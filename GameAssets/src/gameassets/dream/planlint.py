"""Lint + auto-reparo determinístico de DreamPlan (pós-LLM ou hand-edit).

O LLM devolve JSON; este módulo garante que o plano é executável antes de
queimar GPU: ids únicos/slugificados, placements que referenciam assets 3D
reais, vetores pos/scale parseáveis, rig coerente com kind, terrain com prompt.
Reparos são logged (``plan.repairs``) e o que não é reparável fica como
``PlanIssue`` residual para a CLI/report.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any

from .planner import AssetEntry, DreamPlan

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"

VALID_KINDS = ("prop", "character", "environment")

_KIND_COERCE_MAP = {
    "char": "character",
    "hero": "character",
    "npc": "character",
    "enemy": "character",
    "creature": "character",
    "avatar": "character",
    "env": "environment",
    "nature": "environment",
    "scenery": "environment",
    "terrain": "environment",
    "object": "prop",
    "item": "prop",
    "sfx": "prop",
}

_ID_STRIP_RE = re.compile(r"[^a-z0-9_\-]+")


@dataclass(frozen=True)
class PlanIssue:
    """Problema detectado num DreamPlan (severity error|warning)."""

    severity: str
    code: str
    message: str
    asset_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"severity": self.severity, "code": self.code, "message": self.message, "asset_id": self.asset_id}


def format_issue(issue: PlanIssue) -> str:
    tag = "ERROR" if issue.severity == SEVERITY_ERROR else "WARN"
    asset = f" [{issue.asset_id}]" if issue.asset_id else ""
    return f"{tag} {issue.code}{asset}: {issue.message}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def slugify_id(raw: str, *, fallback: str = "asset") -> str:
    """Normaliza um id de asset para ser seguro como nome de ficheiro."""
    slug = _ID_STRIP_RE.sub("_", (raw or "").strip().lower()).strip("_")
    slug = slug[:48].strip("_")
    return slug or fallback


def parse_vec3(value: str | None) -> tuple[float, float, float] | None:
    """Aceita ``"x y z"`` / ``"x,y,z"``; devolve floats ou None se inválido."""
    if value is None:
        return None
    parts = [p for p in re.split(r"[,\s]+", value.strip()) if p]
    if len(parts) != 3:
        return None
    try:
        x, y, z = (float(p) for p in parts)
    except ValueError:
        return None
    return (x, y, z)


def format_vec3(v: tuple[float, float, float]) -> str:
    def _num(n: float) -> str:
        s = f"{n:.4f}".rstrip("0").rstrip(".")
        return s if s not in ("", "-") else "0"

    return f"{_num(v[0])} {_num(v[1])} {_num(v[2])}"


def asset_stage_chain(a: AssetEntry) -> list[str]:
    """Cadeia de stages do master pipeline para um asset (para previews/reports)."""
    if not a.generate_3d:
        if a.generate_audio:
            return ["audio", "handoff"]
        return []
    chain = ["3d", "paint"]
    if a.generate_rig:
        chain.append("rig")
    if a.generate_animate:
        chain.append("animate")
    chain.extend(["lod0", "collision", "validate"])
    return chain


def asset_artifacts(a: AssetEntry) -> list[str]:
    """Artefactos esperados (paths relativos ao output do batch)."""
    out: list[str] = []
    if a.generate_3d:
        out.append(f"meshes/{a.id}_lod0.glb")
        out.append(f"meshes/{a.id}_collision.glb")
    if a.generate_audio:
        out.append(f"audio/{a.id}.wav")
    return out


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------


def validate_plan(plan: DreamPlan, *, max_assets: int = 8) -> list[PlanIssue]:
    """Validação sem mutação — devolve todos os issues (erros + warnings)."""
    issues: list[PlanIssue] = []

    if not plan.title or not plan.title.strip():
        issues.append(PlanIssue(SEVERITY_ERROR, "DREAM_EMPTY_TITLE", "título vazio"))
    if not plan.assets:
        issues.append(PlanIssue(SEVERITY_ERROR, "DREAM_NO_ASSETS", "plano sem assets"))
        return issues

    seen_ids: set[str] = set()
    three_d_ids: set[str] = set()
    for a in plan.assets:
        if not a.id or not a.id.strip():
            issues.append(PlanIssue(SEVERITY_ERROR, "DREAM_INVALID_ID", "id vazio"))
        elif a.id in seen_ids:
            issues.append(PlanIssue(SEVERITY_ERROR, "DREAM_DUP_ID", f"id duplicado '{a.id}'", asset_id=a.id))
        else:
            seen_ids.add(a.id)
        if not a.idea or not a.idea.strip():
            issues.append(PlanIssue(SEVERITY_ERROR, "DREAM_EMPTY_IDEA", "idea vazia", asset_id=a.id))
        if a.kind not in VALID_KINDS:
            issues.append(PlanIssue(SEVERITY_WARNING, "DREAM_BAD_KIND", f"kind desconhecido '{a.kind}'", asset_id=a.id))
        if a.generate_animate and not a.generate_rig:
            issues.append(
                PlanIssue(
                    SEVERITY_ERROR, "DREAM_ANIMATE_NO_RIG", "animate sem rig (gera animate implica rig)", asset_id=a.id
                )
            )
        if a.generate_rig and a.kind != "character":
            issues.append(
                PlanIssue(
                    SEVERITY_WARNING,
                    "DREAM_RIG_NOT_CHARACTER",
                    f"rig com kind='{a.kind}' (esperado character)",
                    asset_id=a.id,
                )
            )
        if a.generate_3d:
            three_d_ids.add(a.id)
        if not a.generate_3d and not a.generate_audio:
            issues.append(PlanIssue(SEVERITY_WARNING, "DREAM_ASSET_NO_OUTPUT", "asset sem 3d nem audio", asset_id=a.id))

    if not three_d_ids:
        issues.append(PlanIssue(SEVERITY_WARNING, "DREAM_NO_3D", "nenhum asset 3D (cena ficará vazia de modelos)"))

    if len(plan.assets) > max_assets:
        issues.append(
            PlanIssue(SEVERITY_WARNING, "DREAM_MAX_ASSETS", f"{len(plan.assets)} assets > limite {max_assets}")
        )

    for p in plan.scene.placements:
        if p.asset_id not in seen_ids:
            issues.append(
                PlanIssue(
                    SEVERITY_ERROR,
                    "DREAM_PLACEMENT_DANGLING",
                    f"placement para id inexistente '{p.asset_id}'",
                    asset_id=p.asset_id,
                )
            )
        elif p.asset_id not in three_d_ids:
            issues.append(
                PlanIssue(
                    SEVERITY_WARNING,
                    "DREAM_PLACEMENT_AUDIO_ONLY",
                    f"placement de asset sem 3D '{p.asset_id}'",
                    asset_id=p.asset_id,
                )
            )
        if parse_vec3(p.pos) is None:
            issues.append(PlanIssue(SEVERITY_ERROR, "DREAM_BAD_POS", f"pos inválida '{p.pos}'", asset_id=p.asset_id))
        if parse_vec3(p.scale) is None:
            issues.append(
                PlanIssue(SEVERITY_ERROR, "DREAM_BAD_SCALE", f"scale inválida '{p.scale}'", asset_id=p.asset_id)
            )

    if not plan.scene.placements:
        issues.append(PlanIssue(SEVERITY_WARNING, "DREAM_NO_PLACEMENTS", "cena sem placements"))
    if plan.scene.spawn_y is not None and plan.scene.spawn_y < 0.5:
        issues.append(
            PlanIssue(SEVERITY_WARNING, "DREAM_SPAWN_Y_LOW", f"spawn_y={plan.scene.spawn_y} pode clipar no chão")
        )

    if plan.terrain is not None and plan.terrain.enabled and not (plan.terrain.prompt or "").strip():
        issues.append(PlanIssue(SEVERITY_WARNING, "DREAM_TERRAIN_NO_PROMPT", "terrain enabled sem prompt"))
    return issues


# ---------------------------------------------------------------------------
# Autorepair
# ---------------------------------------------------------------------------


def autorepair_plan(plan: DreamPlan, *, max_assets: int = 8) -> tuple[DreamPlan, list[str]]:
    """Devolve cópia reparada do plano + log de repairs (mutações descritivas)."""
    p: DreamPlan = copy.deepcopy(plan)
    fixes: list[str] = []

    if not (p.title or "").strip():
        p.title = "Untitled Dream"
        fixes.append("título vazio → 'Untitled Dream'")

    # --- assets: slug ids, dedupe, drop vazios, coerce kinds, rig/animate ---
    seen: set[str] = set()
    id_remap: dict[str, str] = {}
    kept: list[AssetEntry] = []
    for a in p.assets:
        if not (a.idea or "").strip():
            fixes.append(f"asset '{a.id or '?'}' sem idea → removido")
            continue
        new_id = slugify_id(a.id)
        if new_id != a.id:
            fixes.append(f"id '{a.id}' → '{new_id}'")
            id_remap.setdefault(a.id, new_id)
            a.id = new_id
        base = a.id
        n = 2
        while a.id in seen:
            a.id = f"{base}_{n}"
            n += 1
        if a.id != base:
            fixes.append(f"id duplicado '{base}' → '{a.id}'")
        seen.add(a.id)

        if a.kind not in VALID_KINDS:
            coerced = _KIND_COERCE_MAP.get((a.kind or "").strip().lower(), "prop")
            fixes.append(f"kind '{a.kind}' → '{coerced}' [{a.id}]")
            a.kind = coerced
        if a.generate_animate and not a.generate_rig:
            a.generate_rig = True
            fixes.append(f"animate sem rig → rig=True [{a.id}]")
        if a.generate_rig and a.kind != "character":
            fixes.append(f"rig com kind '{a.kind}' → 'character' [{a.id}]")
            a.kind = "character"
        if not a.generate_3d and not a.generate_audio:
            a.generate_3d = True
            fixes.append(f"asset sem output → generate_3d=True [{a.id}]")
        kept.append(a)

    if len(kept) > max_assets:
        dropped = [a.id for a in kept[max_assets:]]
        fixes.append(f"truncate {len(kept)} → {max_assets} assets (removidos: {', '.join(dropped)})")
        kept = kept[:max_assets]
    p.assets = kept

    # --- remapear placements: dangling/audio-only fora, vecs normalizados ---
    valid_3d = {a.id for a in p.assets if a.generate_3d}
    placements = []
    for pl in p.scene.placements:
        remapped = id_remap.get(pl.asset_id, pl.asset_id)
        if remapped != pl.asset_id:
            pl.asset_id = remapped
        if pl.asset_id not in {a.id for a in p.assets}:
            fixes.append(f"placement dangling '{pl.asset_id}' removido")
            continue
        if pl.asset_id not in valid_3d:
            fixes.append(f"placement audio-only '{pl.asset_id}' removido")
            continue
        pos = parse_vec3(pl.pos)
        if pos is None:
            fixes.append(f"pos '{pl.pos}' → '0 0 0' [{pl.asset_id}]")
            pl.pos = "0 0 0"
        elif pl.pos != format_vec3(pos):
            pl.pos = format_vec3(pos)
        scale = parse_vec3(pl.scale)
        if scale is None:
            fixes.append(f"scale '{pl.scale}' → '1 1 1' [{pl.asset_id}]")
            pl.scale = "1 1 1"
        elif pl.scale != format_vec3(scale):
            pl.scale = format_vec3(scale)
        placements.append(pl)
    p.scene.placements = placements

    if p.scene.spawn_y is not None and p.scene.spawn_y < 0.5:
        fixes.append(f"spawn_y {p.scene.spawn_y} → 1.0")
        p.scene.spawn_y = 1.0

    if p.terrain is not None and p.terrain.enabled and not (p.terrain.prompt or "").strip():
        derived = (p.tone or p.genre or "gentle hills").strip()[:80]
        p.terrain.prompt = derived
        fixes.append(f"terrain sem prompt → '{derived}'")

    return p, fixes


def lint_and_repair(plan: DreamPlan, *, max_assets: int = 8) -> tuple[DreamPlan, list[str], list[PlanIssue]]:
    """Repara + re-valida: devolve (plano, repairs, issues residuais)."""
    repaired, fixes = autorepair_plan(plan, max_assets=max_assets)
    residual = validate_plan(repaired, max_assets=max_assets)
    return repaired, fixes, residual
