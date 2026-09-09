//! Auditoria de assets referenciados pelo XML — corre headless no
//! `viber analyze`: ficheiros ausentes, GLBs com compressão não suportada
//! (Draco/Basis; meshopt é expandido pelo reader da engine), texturas com
//! formato desconhecido ou magia inválida, colliders ausentes em modelos
//! glTF, refs a scripts/estilos/BGM que não existem em disco, e conflitos
//! geométricos entre features de terreno (estradas que entram na lâmina de
//! lagos/rios ou atravessam a banda sólida de cliffs).
//!
//! Não abre a engine: lê bytes de cabeçalho (GLB magic + chunk JSON, PNG/JPEG
//! magic) — barato e suficiente para apanhar os casos reais (GLB truncado,
//! HTML guardado como .png, export Draco do DCC, typo no caminho).

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use bevy::math::Vec2;

use crate::recipes::{EntityKind, EntitySpec, ParsedWorld};
use crate::terrain::cliffs::CliffSpec;
use crate::terrain::paths::{nearest_on_path, resample};
use crate::terrain::roads::{RoadProfile, RoadSpec};
use crate::terrain::water::{LakeShape, LakeSpec, RiverSpec, CONTOUR_PEAK, river_cliff_crossings};

/// Severidade de um achado: `Missing` vira ERRO com `analyze --strict`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Severity {
    Missing,
    Warning,
    Info,
}

#[derive(Debug, Clone)]
pub struct AuditIssue {
    pub severity: Severity,
    pub message: String,
}

#[derive(Debug, Default)]
pub struct AuditReport {
    /// Referências a ficheiros recolhidas (incl. repetidas).
    pub references: usize,
    pub issues: Vec<AuditIssue>,
    /// Modelos glTF SEM collider (passam através) — nome ou url.
    pub colliderless: Vec<String>,
}

impl AuditReport {
    pub fn missing_count(&self) -> usize {
        self.issues
            .iter()
            .filter(|issue| issue.severity == Severity::Missing)
            .count()
    }
}

/// Tipo de ficheiro referenciado (dita o sniffing).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum RefKind {
    Glb,
    Texture,
    Heightmap,
    Audio,
    Script,
    Stylesheet,
}

struct AssetRef {
    kind: RefKind,
    /// Caminho absoluto no disco.
    path: PathBuf,
    /// Como foi referenciado (para a mensagem): `<GltfScene name="x">`.
    context: String,
}

/// Audita o mundo: recolhe refs e verifica cada uma. `asset_roots` está em
/// ordem de precedência (docs/ASSETS.md): a pasta do mundo primeiro, as
/// roots extra do config.yaml do jogo a seguir — [`resolve_asset`] decide;
/// `world_dir` resolve scripts/estilos relativos; `config` fornece os dirs
/// de áudio e texturas do jogo.
/// Paths de áudio carregados por HARDCODE na engine: os clips do registry
/// Lua (`luau::SFX_NAME_REGISTRY` — uma linha por variante, aliases caem no
/// dedup) mais os loops de ambiente (`ambient::AMBIENT_LOOP_FILES`),
/// resolvidos contra o `sfx_dir` do config. O audit valida-os a todos
/// porque nunca aparecem no XML — sem isto o `analyze` dizia "ok" com o jogo
/// mudo (foi o caso dos loops de água).
pub(crate) fn engine_audio_files(config: &crate::config::GameConfig) -> Vec<String> {
    let mut files: Vec<String> = crate::luau::SFX_NAME_REGISTRY
        .iter()
        .map(|(_, clip)| config.sfx_path(clip.file()))
        .collect();
    files.extend(
        crate::ambient::AMBIENT_LOOP_FILES
            .iter()
            .map(|rel| config.sfx_path(rel)),
    );
    files.sort_unstable();
    files.dedup();
    files
}

/// Resolve um caminho de asset contra as roots por ordem: a primeira que o
/// tem ganha (override por-mundo → pool partilhado). Quando nenhuma o tem,
/// devolve o join com a 1.ª root — a mensagem de "ausente" aponta ao sítio
/// onde o autor esperava o ficheiro.
fn resolve_asset(roots: &[PathBuf], rel: &str) -> PathBuf {
    let rel = rel.trim_start_matches('/');
    for root in roots {
        let candidate = root.join(rel);
        if candidate.exists() {
            return candidate;
        }
    }
    roots
        .first()
        .map(|root| root.join(rel))
        .unwrap_or_else(|| PathBuf::from(rel))
}

pub fn audit(
    world: &ParsedWorld,
    world_dir: &Path,
    asset_roots: &[PathBuf],
    config: &crate::config::GameConfig,
) -> AuditReport {
    let mut report = AuditReport::default();
    let mut refs: Vec<AssetRef> = Vec::new();
    collect_entities(
        &world.entities,
        world_dir,
        asset_roots,
        config,
        false,
        &mut refs,
        &mut report,
    );

    // Conflitos entre features de terreno — ver [`FeatureIndex`].
    let mut features = FeatureIndex::default();
    collect_features(&world.entities, [0.0, 0.0], &mut features);
    report.issues.extend(audit_feature_conflicts(&features));

    // SFX carregados por path HARDCODED na engine — ver
    // [`engine_audio_files`]. Os paths vêm do `sfx_dir` do config do jogo.
    for file in engine_audio_files(config) {
        refs.push(AssetRef {
            kind: RefKind::Audio,
            path: resolve_asset(asset_roots, &file),
            context: "sfx engine".to_string(),
        });
    }

    report.colliderless.sort();
    report.colliderless.dedup();

    // Dedup por (kind, path) mantendo o 1.º contexto + contagem.
    report.references = refs.len();
    let mut unique: Vec<(RefKind, PathBuf, String, usize)> = Vec::new();
    for r in refs {
        if let Some(entry) = unique
            .iter_mut()
            .find(|(kind, path, ..)| *kind == r.kind && *path == r.path)
        {
            entry.3 += 1;
        } else {
            unique.push((r.kind, r.path, r.context, 1));
        }
    }

    for (kind, path, context, count) in unique {
        let uses = if count > 1 {
            format!(" (+{count} refs iguais)")
        } else {
            String::new()
        };
        if !path.is_file() {
            report.issues.push(AuditIssue {
                severity: Severity::Missing,
                message: format!("ausente: {} (usado por {context}{uses})", path.display()),
            });
            continue;
        }
        match kind {
            RefKind::Glb => audit_glb(&path, &context, &mut report.issues),
            RefKind::Texture => audit_texture(&path, &context, &mut report.issues),
            RefKind::Heightmap => {
                if path
                    .extension()
                    .is_some_and(|e| e.eq_ignore_ascii_case("png"))
                {
                    check_magic(
                        &path,
                        &context,
                        b"\x89PNG\r\n\x1a\n",
                        "PNG",
                        &mut report.issues,
                    );
                }
            }
            RefKind::Audio => {
                if !path
                    .extension()
                    .is_some_and(|e| e.eq_ignore_ascii_case("ogg"))
                {
                    report.issues.push(AuditIssue {
                        severity: Severity::Warning,
                        message: format!(
                            "formato de áudio pouco usual: {} (a engine usa .ogg) — {context}",
                            path.display()
                        ),
                    });
                }
            }
            RefKind::Script | RefKind::Stylesheet => {}
        }
    }
    report
}

/// Recursão pela árvore de entidades recolhendo refs + GLBs sem collider.
fn collect_entities(
    entities: &[EntitySpec],
    world_dir: &Path,
    asset_roots: &[PathBuf],
    config: &crate::config::GameConfig,
    inherited_collider: bool,
    refs: &mut Vec<AssetRef>,
    report: &mut AuditReport,
) {
    for entity in entities {
        let label = entity
            .name
            .clone()
            .or_else(|| entity.tag.clone())
            .unwrap_or_else(|| "sem nome".into());

        let collider_covered = inherited_collider || !entity.physics.is_empty();
        if let Some(script) = &entity.script {
            refs.push(AssetRef {
                kind: RefKind::Script,
                path: world_dir.join("scripts").join(script),
                context: format!("<script=\"{script}\"> {label}"),
            });
        }
        match &entity.kind {
            EntityKind::GltfScene { url } | EntityKind::PlayerGltf { url } => {
                // O herói tem character controller próprio (colisão via
                // Rapier, não via attr); o resto herda collider do ancestral.
                if !collider_covered && matches!(entity.kind, EntityKind::GltfScene { .. }) {
                    report.colliderless.push(url.clone());
                }
                refs.push(AssetRef {
                    kind: RefKind::Glb,
                    path: resolve_asset(asset_roots, url),
                    context: format!("<{url}> {label}"),
                });
            }
            EntityKind::Primitive { material, .. } => {
                if let Some(texture) = &material.texture {
                    refs.push(AssetRef {
                        kind: RefKind::Texture,
                        path: resolve_asset(asset_roots, texture),
                        context: format!("<texture=\"{texture}\"> {label}"),
                    });
                }
            }
            EntityKind::Terrain { spec } => {
                if let Some(heightmap) = &spec.heightmap {
                    refs.push(AssetRef {
                        kind: RefKind::Heightmap,
                        path: resolve_asset(asset_roots, heightmap),
                        context: format!("<heightmap=\"{heightmap}\">"),
                    });
                }
                if let Some(texture) = &spec.texture {
                    refs.push(AssetRef {
                        kind: RefKind::Texture,
                        path: resolve_asset(asset_roots, texture),
                        context: format!("<terrain texture=\"{texture}\">"),
                    });
                }
                for layer in &spec.layers {
                    // Lista canónica com buracos (""): slot sem alias
                    // autoral — nada a auditar.
                    if layer.is_empty() {
                        continue;
                    }
                    // Alias do pool (`grass`) ou caminho cru de textura — a
                    // mesma resolução do bootstrap (config.terrain_albedo).
                    let path = config
                        .terrain_albedo(layer)
                        .unwrap_or_else(|| layer.clone());
                    refs.push(AssetRef {
                        kind: RefKind::Texture,
                        path: resolve_asset(asset_roots, &path),
                        context: format!("<terrain layers=\"…{layer}…\">"),
                    });
                }
            }
            EntityKind::Road { spec } => {
                push_ribbon_texture(asset_roots, &spec.texture, &label, refs)
            }
            EntityKind::RoadNetwork { spec } => {
                push_ribbon_texture(asset_roots, &spec.texture, &label, refs)
            }
            EntityKind::GroundDecal { spec } => {
                if let Some(texture) = &spec.texture {
                    refs.push(AssetRef {
                        kind: RefKind::Texture,
                        path: resolve_asset(asset_roots, texture),
                        context: format!("<decal texture=\"{texture}\"> {label}"),
                    });
                }
            }
            EntityKind::Vegetation { spec } => {
                for mesh in &spec.meshes {
                    refs.push(AssetRef {
                        kind: RefKind::Glb,
                        path: resolve_asset(asset_roots, mesh),
                        context: format!("<vegetation mesh=\"{mesh}\"> {label}"),
                    });
                }
            }
            EntityKind::MusicLayer { layer, .. } => {
                // Convenção do runtime (spawn.rs): {bgm_dir}/{layer}.ogg,
                // com o dir do config do jogo.
                let path = resolve_asset(asset_roots, &config.bgm_path(layer));
                refs.push(AssetRef {
                    kind: RefKind::Audio,
                    path,
                    context: format!("<MusicLayer layer=\"{layer}\">"),
                });
            }
            EntityKind::UiStyle { source } => {
                // O parser antecede '@' ao `src` (`@ui/hud.css`); o caminho
                // é relativo à pasta do jogo (o prefixo `ui/` é do autor —
                // contrato do XML, não um dir do config).
                let file = source.trim_start_matches('@');
                refs.push(AssetRef {
                    kind: RefKind::Stylesheet,
                    path: world_dir.join(file),
                    context: format!("<UiStyle src=\"{file}\">"),
                });
            }
            _ => {}
        }
        collect_entities(
            &entity.children,
            world_dir,
            asset_roots,
            config,
            collider_covered,
            refs,
            report,
        );
    }
}

// ─────────────────────────────────────────────────────────────────────────
// Conflitos entre features de terreno — estradas × lagos/rios/cliffs.
//
// Estradas e corpos de água escavam o mesmo campo; o guard do carve já evita
// a escavação dentro de zona de água, mas o RIBBON visual é desenhado na
// mesma — e um trilho decal (`flatten="0"`) não carve nada. Uma estrada cujo
// traçado entra na lâmina fica submersa e rasgada (o caso real: a ponte da
// Lagoa Grande do simple-rpg, afogada pelo contorno orgânico ±28% — pontas
// autoradas à distância nominal do raio, a água chega a `CONTOUR_PEAK` dele).
// Tudo aqui é geométrico e estático — exatamente o que o `analyze` vê sem
// abrir a engine.
//
// Translações XZ dos ancestrais são acumuladas (as features leem
// WorldTransform); escala/rotação de ancestrais é ignorada — nenhum mundo do
// repo põe features sob um grupo assim.

/// Passo de amostragem do traçado das estradas (m).
const ROAD_SAMPLE_STEP: f32 = 2.0;
/// Folga (m) extra no teste da banda de um cliff.
const CLIFF_TIP_MARGIN: f32 = 1.0;

/// Features recolhidas da árvore de entidades, prontas para os testes
/// cruzados. As specs são clonadas — o audit corre uma vez, headless.
#[derive(Default)]
struct FeatureIndex {
    /// `<Road>`: (rótulo, spec).
    roads: Vec<(String, RoadSpec)>,
    /// `<RoadNetwork>` expandido por segmento: (rótulo, polyline, largura,
    /// perfil).
    segments: Vec<(String, Vec<Vec2>, f32, RoadProfile)>,
    lakes: Vec<(String, LakeSpec)>,
    rivers: Vec<(String, RiverSpec)>,
    cliffs: Vec<(String, CliffSpec)>,
}

fn collect_features(entities: &[EntitySpec], offset: [f32; 2], out: &mut FeatureIndex) {
    for entity in entities {
        let named = entity.name.is_some() || entity.tag.is_some();
        let label = entity
            .name
            .clone()
            .or_else(|| entity.tag.clone())
            .unwrap_or_else(|| "sem nome".into());
        let off = [
            offset[0] + entity.transform.translation[0],
            offset[1] + entity.transform.translation[2],
        ];
        let shift = |points: &[Vec2]| -> Vec<Vec2> {
            points
                .iter()
                .map(|p| p + Vec2::new(off[0], off[1]))
                .collect()
        };
        // Fallback de rótulo com coordenadas — "sem nome" não diz NADA ao
        // autor a procurar o sítio no XML.
        let where_at = |p: Vec2| format!("({:.0}, {:.0})", p.x, p.y);
        match &entity.kind {
            EntityKind::Road { spec } => {
                let mut spec = spec.clone();
                spec.path = shift(&spec.path);
                let label = spec.name.clone().unwrap_or_else(|| {
                    if named {
                        label
                    } else {
                        format!("estrada {}", where_at(spec.path[0]))
                    }
                });
                out.roads.push((label, spec));
            }
            EntityKind::RoadNetwork { spec } => {
                let ways: HashMap<&str, (Vec2, Option<f32>)> = spec
                    .ways
                    .iter()
                    .map(|w| (w.id.as_str(), (w.at + Vec2::new(off[0], off[1]), w.width)))
                    .collect();
                let net_label = spec.name.clone().unwrap_or(if named {
                    label
                } else {
                    String::new()
                });
                for seg in &spec.segments {
                    let (Some(&(a_at, a_w)), Some(&(b_at, _))) =
                        (ways.get(seg.a.as_str()), ways.get(seg.b.as_str()))
                    else {
                        continue; // ways em falta são erro do parse, não daqui
                    };
                    let mut pts = vec![a_at];
                    pts.extend(shift(&seg.via));
                    pts.push(b_at);
                    let width = seg.width.or(a_w).unwrap_or(spec.default_width);
                    let profile = seg.profile.unwrap_or(spec.default_profile);
                    let seg_label = if net_label.is_empty() {
                        format!("{}→{}", seg.a, seg.b)
                    } else {
                        format!("{net_label}: {}→{}", seg.a, seg.b)
                    };
                    out.segments.push((seg_label, pts, width, profile));
                }
            }
            EntityKind::Lake { spec } => {
                let mut spec = spec.clone();
                spec.at += Vec2::new(off[0], off[1]);
                let label = if named {
                    label
                } else {
                    format!("lago {}", where_at(spec.at))
                };
                out.lakes.push((label, spec));
            }
            EntityKind::River { spec } => {
                let mut spec = spec.clone();
                spec.path = shift(&spec.path);
                let label = if named {
                    label
                } else if let Some(p) = spec.path.first() {
                    format!("rio {}", where_at(*p))
                } else {
                    label
                };
                out.rivers.push((label, spec));
            }
            EntityKind::Cliff { spec } => {
                let mut spec = spec.clone();
                spec.path = shift(&spec.path);
                let label = if named {
                    label
                } else if let Some(p) = spec.path.first() {
                    format!("cliff {}", where_at(*p))
                } else {
                    label
                };
                out.cliffs.push((label, spec));
            }
            _ => {}
        }
        collect_features(&entity.children, off, out);
    }
}

fn audit_feature_conflicts(idx: &FeatureIndex) -> Vec<AuditIssue> {
    let mut issues = Vec::new();
    for (label, spec) in &idx.roads {
        audit_road_line(label, &spec.path, spec.width, spec.profile, idx, &mut issues);
    }
    for (label, pts, width, profile) in &idx.segments {
        audit_road_line(label, pts, *width, *profile, idx, &mut issues);
    }
    // Rio × cliff — já não é um conflito: é uma CACHOEIRA automática. O
    // runtime conduz o perfil do rio pelo cruzamento (hold a montante +
    // queda garantida) e a parede voxel corta a face. Informa o autor com
    // a queda prometida — cruzar sem querer é exatamente o erro que esta
    // linha torna visível.
    for (rlabel, river) in &idx.rivers {
        for (clabel, cliff) in &idx.cliffs {
            for fall in river_cliff_crossings(river, std::slice::from_ref(cliff)) {
                issues.push(AuditIssue {
                    severity: Severity::Info,
                    message: format!(
                        "rio `{rlabel}` cruza o cliff `{clabel}` perto de ({:.0}, {:.0}) — \
                         cachoeira automática (queda ≥ {:.1} m)",
                        fall.at.x, fall.at.y, fall.min_drop
                    ),
                });
            }
        }
    }
    issues.sort_by(|a, b| a.message.cmp(&b.message));
    issues
}

/// Testa UMA estrada (de `<Road>` ou expandida de rede) contra lagos, rios e
/// cliffs. `is_bridge` abre a exceção do deck: cruzar água é o propósito de
/// uma ponte — o que se exige é que as PONTAS fiquem fora da lâmina.
fn audit_road_line(
    label: &str,
    path: &[Vec2],
    width: f32,
    profile: RoadProfile,
    idx: &FeatureIndex,
    issues: &mut Vec<AuditIssue>,
) {
    if path.len() < 2 {
        return;
    }
    let samples = resample(path, ROAD_SAMPLE_STEP);
    let is_bridge = profile == RoadProfile::Bridge;

    for (name, lake) in &idx.lakes {
        let shape = LakeShape::new(lake.at);
        // Quanto o traçado (com a MEIA-LARGURA do ribbon) penetra o contorno
        // orgânico REAL: `r(θ) − (|p − at| − half)`. Testar só a linha
        // central deixava passar trilhos cuja BORDA ficava dentro de água —
        // o decal mergulhava na bacia abaixo do plano sem nada no relatório
        // (caso real: o trilho NE da Lagoa Grande do simple-rpg, eixo a
        // 30.0 m do centro com o contorno real a ~29 m naquele rumo).
        let half = width * 0.5;
        let mut depth_in = 0.0_f32;
        let mut worst = path[0];
        for p in &samples {
            let d = p.distance(lake.at);
            let delta = *p - lake.at;
            let theta = delta.y.atan2(delta.x);
            let inside = shape.contour(lake.radius, theta) - (d - half);
            if inside > depth_in {
                depth_in = inside;
                worst = *p;
            }
        }
        if depth_in <= 0.0 {
            continue;
        }
        if is_bridge {
            let reach = lake.radius * CONTOUR_PEAK;
            let tips_inside = path.iter().any(|tip| tip.distance(lake.at) < reach);
            if tips_inside {
                issues.push(AuditIssue {
                    severity: Severity::Warning,
                    message: format!(
                        "ponte \"{label}\": pontas dentro do alcance do contorno orgânico do lago \
                         \"{name}\" (lâmina até r={reach:.0} m, ±{}%) — a água pode cobrir \
                         pontas/deck; afaste as pontas",
                        (CONTOUR_PEAK - 1.0) * 100.0,
                    ),
                });
            } else {
                issues.push(AuditIssue {
                    severity: Severity::Warning,
                    message: format!(
                        "ponte \"{label}\" cruza o lago \"{name}\" perto de ({:.0}, {:.0}) — o \
                         deck é ribbon plana SEM collider no terreno voxel: o herói não tem por \
                         onde atravessar; usa uma <Bridge> volumétrica no vão e garante pontas \
                         fora da lâmina (worst-case r={:.0} m)",
                        worst.x,
                        worst.y,
                        reach,
                    ),
                });
            }
        } else {
            issues.push(AuditIssue {
                severity: Severity::Warning,
                message: format!(
                    "estrada \"{label}\" entra na lâmina do lago \"{name}\" perto de \
                     ({:.0}, {:.0}) (até {depth_in:.1} m para dentro, contando a meia-largura \
                     do ribbon; contorno orgânico ±{}%) — o piso fica submerso",
                    worst.x,
                    worst.y,
                    (CONTOUR_PEAK - 1.0) * 100.0,
                ),
            });
        }
    }

    for (name, river) in &idx.rivers {
        if river.path.len() < 2 {
            continue;
        }
        let half = river.width * 0.5;
        // A borda do ribbon conta: uma estrada cuja margem entra na lâmina
        // rasga o piso na água mesmo com o eixo seco.
        let road_half = width * 0.5;
        let mut dmin = f32::INFINITY;
        let mut worst = path[0];
        for p in &samples {
            if let Some(hit) = nearest_on_path(&river.path, *p) {
                let d = p.distance(hit.point);
                if d < dmin {
                    dmin = d;
                    worst = *p;
                }
            }
        }
        if dmin >= half + road_half {
            continue;
        }
        if is_bridge {
            // Largura máx. da lâmina: com pool-spacing os poços abrem até
            // ×1.2 (`water.rs`, `halfs`). A folga é a banca de margem — não
            // se soma margem genérica por cima (pontas 0.5 m fora da banca
            // são prática normal, o abutment assenta aí).
            let water_half = if river.pool_spacing > 0.0 {
                half * 1.2
            } else {
                half
            };
            let clear = water_half + river.bank_width;
            let tips_clear = path.iter().all(|tip| {
                nearest_on_path(&river.path, *tip)
                    .is_some_and(|h| tip.distance(h.point) >= clear)
            });
            if tips_clear {
                issues.push(AuditIssue {
                    severity: Severity::Warning,
                    message: format!(
                        "ponte \"{label}\" cruza o rio \"{name}\" perto de ({:.0}, {:.0}) — o \
                         deck é ribbon plana SEM collider no terreno voxel: o herói não tem \
                         por onde atravessar; usa uma <Bridge> volumétrica no vão",
                        worst.x, worst.y,
                    ),
                });
            } else {
                issues.push(AuditIssue {
                    severity: Severity::Warning,
                    message: format!(
                        "ponte \"{label}\": pontas a <{clear:.0} m do eixo do rio \"{name}\" — \
                         a lâmina/banco de margem pode cobrir as pontas",
                    ),
                });
            }
        } else {
            issues.push(AuditIssue {
                severity: Severity::Warning,
                message: format!(
                    "estrada \"{label}\" cruza a lâmina do rio \"{name}\" perto de ({:.0}, \
                     {:.0}) (eixo a {dmin:.1} m do centro; lâmina ±{half:.0} m + meia-largura \
                     do ribbon) — o piso fica submerso",
                    worst.x, worst.y,
                ),
            });
        }
    }

    for (name, cliff) in &idx.cliffs {
        if cliff.path.len() < 2 {
            continue;
        }
        let band = cliff.width * 0.5 + width * 0.5 + CLIFF_TIP_MARGIN;
        let mut dmin = f32::INFINITY;
        for p in &samples {
            if let Some(hit) = nearest_on_path(&cliff.path, *p) {
                dmin = dmin.min(p.distance(hit.point));
            }
        }
        if dmin < band {
            issues.push(AuditIssue {
                severity: Severity::Warning,
                message: format!(
                    "estrada \"{label}\" cruza a banda do cliff \"{name}\" (folga \
                     {CLIFF_TIP_MARGIN:.0} m) — o ribbon atravessa a rocha sólida do campo voxel",
                ),
            });
        }
    }
}

fn push_ribbon_texture(
    asset_roots: &[PathBuf],
    texture: &Option<String>,
    label: &str,
    refs: &mut Vec<AssetRef>,
) {
    if let Some(texture) = texture {
        refs.push(AssetRef {
            kind: RefKind::Texture,
            path: resolve_asset(asset_roots, texture),
            context: format!("<ribbon texture=\"{texture}\"> {label}"),
        });
    }
}

// ---------------------------------------------------------------- GLB

/// GLB: magic + chunk JSON → extensões usadas. Draco/Basis não são
/// suportados pela engine; meshopt é expandido pelo asset reader.
fn audit_glb(path: &Path, context: &str, issues: &mut Vec<AuditIssue>) {
    const GLB_MAGIC: &[u8; 4] = b"glTF";
    let Ok(bytes) = std::fs::read(path) else {
        return; // ausência já foi reportada
    };
    if bytes.len() < 12 || &bytes[0..4] != GLB_MAGIC {
        issues.push(AuditIssue {
            severity: Severity::Warning,
            message: format!(
                "glb inválido: {} não começa com a magia \"glTF\" (truncado ou não é GLB?) — {context}",
                path.display()
            ),
        });
        return;
    }
    // Cabeçalho truncado entre a magia e o chunk JSON (12–19 bytes): ler o
    // chunk len ou fatiar [20..end] era index out of bounds — o analyze
    // panicava em vez de falhar limpo.
    if bytes.len() < 20 {
        issues.push(AuditIssue {
            severity: Severity::Warning,
            message: format!(
                "glb inválido: {} truncado antes do chunk JSON — {context}",
                path.display()
            ),
        });
        return;
    }
    let json_len = u32::from_le_bytes([bytes[12], bytes[13], bytes[14], bytes[15]]) as usize;
    let end = (20 + json_len).min(bytes.len());
    let Ok(json) = std::str::from_utf8(&bytes[20..end]) else {
        return;
    };
    // Draco: a engine não lê — falha o load com warn em runtime.
    // `KHR_texture_basisu` NÃO está na lista: a engine lê KTX2 tal-como-
    // autorado (o `MeshoptAssetReader` passa-o e o Bevy transcode UASTC —
    // `meshopt::load_gltf` desliga a validação exactamente por isso). A
    // regra antiga só não disparava porque o extinto espelho vinha
    // decomprimido; servindo o pool direto ela virava falso positivo em
    // massa.
    for (extension, hint) in [(
        "KHR_draco_mesh_compression",
        "a engine (Bevy 0.19) não lê Draco",
    )] {
        if json.contains(extension) {
            issues.push(AuditIssue {
                severity: Severity::Warning,
                message: format!(
                    "compressão não suportada: {extension} em {} ({hint}) — {context}",
                    path.display()
                ),
            });
        }
    }
}

// ---------------------------------------------------------------- texturas

const TEXTURE_EXTENSIONS: &[&str] = &["png", "jpg", "jpeg", "webp", "ktx2", "hdr", "tga"];

fn audit_texture(path: &Path, context: &str, issues: &mut Vec<AuditIssue>) {
    let extension = path
        .extension()
        .and_then(|e| e.to_str())
        .map(|e| e.to_ascii_lowercase());
    let Some(extension) = extension else {
        issues.push(AuditIssue {
            severity: Severity::Warning,
            message: format!(
                "textura sem extensão: {} (esperado png/jpg/webp/ktx2/hdr) — {context}",
                path.display()
            ),
        });
        return;
    };
    if !TEXTURE_EXTENSIONS.contains(&extension.as_str()) {
        issues.push(AuditIssue {
            severity: Severity::Warning,
            message: format!(
                "formato de textura não suportado: .{extension} em {} (esperado png/jpg/webp/ktx2/hdr/tga) — {context}",
                path.display()
            ),
        });
        return;
    }
    match extension.as_str() {
        "png" => check_magic(path, context, b"\x89PNG\r\n\x1a\n", "PNG", issues),
        "jpg" | "jpeg" => check_magic(path, context, b"\xff\xd8\xff", "JPEG", issues),
        "ktx2" => audit_ktx2(path, context, issues),
        "webp" => check_webp(path, context, issues),
        "hdr" => check_magic(path, context, b"#?", "HDR", issues),
        // .tga não tem magia fiável (o campo de ID é opcional e a assinatura
        // opcional vive no FIM do ficheiro) — a extensão basta.
        "tga" => {}
        _ => {}
    }
}

/// Lê só os primeiros `n` bytes (cabeçalho) — o audit não abre texturas
/// inteiras.
fn read_header(path: &Path, n: u64) -> Option<Vec<u8>> {
    use std::io::Read as _;
    let file = std::fs::File::open(path).ok()?;
    let mut buffer = Vec::new();
    file.take(n).read_to_end(&mut buffer).ok()?;
    Some(buffer)
}

/// KTX2: magia de 12 bytes + cabeçalho de 80; `vkFormat` (u32 LE @12) e
/// `supercompressionScheme` (u32 LE @44; 0 none / 1 BasisLZ / 2 Zstandard —
/// discriminantes conferidos na crate `ktx2` 0.5).
const KTX2_MAGIC: [u8; 12] = [
    0xAB, 0x4B, 0x54, 0x58, 0x20, 0x32, 0x30, 0xBB, 0x0D, 0x0A, 0x1A, 0x0A,
];

/// O `.ktx2` merece sniffing próprio: ETC1S/BasisLZ é FATAL na engine (o
/// Bevy 0.19 não descomprime BasisLZ — regra "nunca etc1s") e entrava como
/// falso negativo, ao contrário do análogo GLB (Draco/Basis verificados).
fn audit_ktx2(path: &Path, context: &str, issues: &mut Vec<AuditIssue>) {
    let Some(bytes) = read_header(path, 48) else {
        return;
    };
    if bytes.len() < 12 || bytes[..12] != KTX2_MAGIC {
        issues.push(AuditIssue {
            severity: Severity::Warning,
            message: format!(
                "ktx2 inválido: {} não tem a magia KTX2 (truncado ou não é KTX2?) — {context}",
                path.display()
            ),
        });
        return;
    }
    let vk_format = u32::from_le_bytes([bytes[12], bytes[13], bytes[14], bytes[15]]);
    // O scheme só se lê com os 48 bytes do cabeçalho presentes.
    let scheme = (bytes.len() >= 48)
        .then(|| u32::from_le_bytes([bytes[44], bytes[45], bytes[46], bytes[47]]));
    let problem = if vk_format == 0 {
        // VK_FORMAT_UNDEFINED: Basis Universal — o formato real decide-se na
        // transcodificação; é o caminho do etc1s/BasisLZ.
        Some(
            "Basis Universal (vkFormat 0 — ETC1S/BasisLZ) — a engine (Bevy 0.19) não descomprime BasisLZ; reexporta em UASTC (`text3d finish`)"
                .to_string(),
        )
    } else if (147..=156).contains(&vk_format) {
        // Família ETC2/EAC crua: VK_FORMAT_ETC2_R8G8B8_UNORM_BLOCK
        // (147 = 0x93) até VK_FORMAT_EAC_R11G11_SNORM_BLOCK (156 = 0x9C) —
        // discriminantes conferidos na crate `ktx2` 0.5, enum `Format`.
        // UASTC costuma declarar VK_FORMAT_R8G8B8A8_* (37) e passa.
        Some(format!(
            "ETC2/EAC cru (vkFormat {vk_format}) — desktop não amostra ETC2; reexporta em UASTC (`text3d finish`)"
        ))
    } else if scheme == Some(1) {
        Some(
            "supercompressão BasisLZ (scheme 1) — a engine (Bevy 0.19) não descomprime BasisLZ; reexporta em UASTC (`text3d finish`)"
                .to_string(),
        )
    } else {
        None
    };
    if let Some(problem) = problem {
        issues.push(AuditIssue {
            severity: Severity::Warning,
            message: format!(
                "ktx2 não suportado: {} usa {problem} — {context}",
                path.display()
            ),
        });
    }
}

/// WebP é um contentor RIFF: "RIFF" + tamanho (4 bytes) + "WEBP" @8.
fn check_webp(path: &Path, context: &str, issues: &mut Vec<AuditIssue>) {
    let Some(bytes) = read_header(path, 12) else {
        return;
    };
    if bytes.len() < 12 || !bytes.starts_with(b"RIFF") || !bytes[8..12].starts_with(b"WEBP") {
        issues.push(AuditIssue {
            severity: Severity::Warning,
            message: format!(
                "webp inválido: {} não tem a magia RIFF….WEBP (corrompido ou PNG/JPEG guardado com a extensão errada?) — {context}",
                path.display()
            ),
        });
    }
}

fn check_magic(
    path: &Path,
    context: &str,
    magic: &[u8],
    format: &str,
    issues: &mut Vec<AuditIssue>,
) {
    let Ok(bytes) = std::fs::read(path) else {
        return;
    };
    if !bytes.starts_with(magic) {
        issues.push(AuditIssue {
            severity: Severity::Warning,
            message: format!(
                "{format} inválido: {} não tem a magia {format} (corrompido ou HTML/JSON guardado com a extensão errada?) — {context}",
                path.display()
            ),
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::physics::{ColliderShape, PhysicsSpec};
    use crate::recipes::{EntitySpec, MaterialSpec, TransformSpec};

    fn entity(name: &str, kind: EntityKind, collider: ColliderShape) -> EntitySpec {
        EntitySpec {
            name: Some(name.into()),
            tag: None,
            script: None,
            destructible: None,
            transform: TransformSpec::default(),
            physics: PhysicsSpec {
                collider,
                body: Default::default(),
                mass: None,
                gravity_scale: None,
            },
            kind,
            children: vec![],
        }
    }

    /// GLB mínimo válido: header + chunk JSON com a extensão dada.
    fn glb_bytes(extension: Option<&str>) -> Vec<u8> {
        let json = match extension {
            Some(ext) => format!(r#"{{"extensionsUsed":["{ext}"]}}"#),
            None => "{}".into(),
        };
        let mut out = Vec::from(&b"glTF"[..]);
        out.extend_from_slice(&2u32.to_le_bytes()); // version
        out.extend_from_slice(&(12 + json.len() as u32).to_le_bytes()); // total
        out.extend_from_slice(&(json.len() as u32).to_le_bytes()); // chunk len
        out.extend_from_slice(b"JSON");
        out.extend_from_slice(json.as_bytes());
        out
    }

    fn setup() -> (tempfile::TempDir, PathBuf, PathBuf) {
        let dir = tempfile::tempdir().expect("tmpdir");
        let world_dir = dir.path().join("mundo");
        let asset_root = world_dir.clone();
        std::fs::create_dir_all(asset_root.join("assets/meshes")).unwrap();
        std::fs::create_dir_all(asset_root.join("assets/textures")).unwrap();
        std::fs::create_dir_all(asset_root.join("scripts")).unwrap();
        // O fixture cria-os vazios: o que se testa aqui é a recolha de refs
        // e o reporte dos assets do MUNDO, não a existência dos clips.
        for rel in engine_audio_files(&crate::config::fixture()) {
            let path = asset_root.join(rel.trim_start_matches('/'));
            std::fs::create_dir_all(path.parent().expect("parent")).unwrap();
            std::fs::File::create(&path).unwrap();
        }
        (dir, world_dir, asset_root)
    }

    #[test]
    fn test_missing_glb_is_reported() {
        let (_dir, world_dir, asset_root) = setup();
        let world = ParsedWorld {
            entities: vec![entity(
                "heroi",
                EntityKind::PlayerGltf {
                    url: "/assets/meshes/fantasma.glb".into(),
                },
                Default::default(),
            )],
            ..Default::default()
        };
        let config = crate::config::fixture();
        let report = audit(&world, &world_dir, &[asset_root.clone()], &config);
        // 1 ref do mundo + os SFX da engine (fixture cria-os, nenhum falha).
        assert_eq!(report.references, 1 + engine_audio_files(&config).len());
        assert_eq!(report.missing_count(), 1);
        assert!(
            report.issues[0].message.contains("ausente")
                && report.issues[0].message.contains("fantasma.glb"),
            "{:?}",
            report.issues
        );
        // PlayerGLTF NUNCA entra na lista de colliderless: o herói tem
        // character controller próprio (colisão via Rapier no spawn).
        assert!(report.colliderless.is_empty());
    }

    #[test]
    fn test_colliderless_and_inherited_collider() {
        let (_dir, world_dir, asset_root) = setup();
        std::fs::write(asset_root.join("assets/meshes/a.glb"), glb_bytes(None)).unwrap();
        std::fs::write(asset_root.join("assets/meshes/b.glb"), glb_bytes(None)).unwrap();
        let glb = |url: &str| EntityKind::GltfScene { url: url.into() };
        // "cobre": Group com collider → filhos GltfScene NÃO são colliderless.
        let mut group = entity("muro", EntityKind::Group, ColliderShape::Auto);
        group.children = vec![entity(
            "painel",
            glb("/assets/meshes/a.glb"),
            Default::default(),
        )];
        let world = ParsedWorld {
            entities: vec![
                group,
                entity("nu", glb("/assets/meshes/b.glb"), Default::default()),
            ],
            ..Default::default()
        };
        let config = crate::config::fixture();
        let report = audit(&world, &world_dir, &[asset_root.clone()], &config);
        assert_eq!(
            report.colliderless,
            vec!["/assets/meshes/b.glb".to_string()]
        );
    }

    #[test]
    fn test_draco_is_flagged_and_meshopt_is_not() {
        let (_dir, world_dir, asset_root) = setup();
        std::fs::write(
            asset_root.join("assets/meshes/draco.glb"),
            glb_bytes(Some("KHR_draco_mesh_compression")),
        )
        .unwrap();
        std::fs::write(
            asset_root.join("assets/meshes/meshopt.glb"),
            glb_bytes(Some("EXT_meshopt_compression")),
        )
        .unwrap();
        let glb_scene = |url: &str, name: &str| {
            entity(
                name,
                EntityKind::GltfScene { url: url.into() },
                ColliderShape::Box {
                    size: Default::default(),
                    offset: Default::default(),
                },
            )
        };
        let world = ParsedWorld {
            entities: vec![
                glb_scene("/assets/meshes/draco.glb", "d"),
                glb_scene("/assets/meshes/meshopt.glb", "m"),
            ],
            ..Default::default()
        };
        let config = crate::config::fixture();
        let report = audit(&world, &world_dir, &[asset_root.clone()], &config);
        assert_eq!(report.missing_count(), 0);
        assert_eq!(report.issues.len(), 1, "só o Draco: {:?}", report.issues);
        assert!(report.issues[0].message.contains("Draco"));
        assert!(report.colliderless.is_empty(), "com collider explícito");
    }

    #[test]
    fn test_invalid_glb_magic_and_texture_checks() {
        let (_dir, world_dir, asset_root) = setup();
        std::fs::write(
            asset_root.join("assets/meshes/falso.glb"),
            b"<html>404</html>",
        )
        .unwrap();
        std::fs::write(
            asset_root.join("assets/textures/ok.png"),
            [0x89, b'P', b'N', b'G', 0x0d, 0x0a, 0x1a, 0x0a, 0, 0],
        )
        .unwrap();
        std::fs::write(
            asset_root.join("assets/textures/falso.png"),
            b"{\"json\": true}",
        )
        .unwrap();
        std::fs::write(asset_root.join("assets/textures/vector.svg"), b"<svg/>").unwrap();
        let with_texture = |name: &str, tex: &str, _file: &str| {
            entity(
                name,
                EntityKind::Primitive {
                    shape: crate::recipes::Shape::Cuboid {
                        half_size: [0.5; 3],
                    },
                    material: MaterialSpec {
                        texture: Some(tex.into()),
                        ..Default::default()
                    },
                },
                ColliderShape::Auto,
            )
        };
        let world = ParsedWorld {
            entities: vec![
                with_texture("a", "/assets/textures/falso.png", "x"),
                with_texture("b", "/assets/textures/vector.svg", "y"),
            ],
            ..Default::default()
        };
        // Referenciar também o glb falso e o png ok (via GltfScene + decal).
        let mut world = world;
        world.entities.push(entity(
            "c",
            EntityKind::GltfScene {
                url: "/assets/meshes/falso.glb".into(),
            },
            ColliderShape::Auto,
        ));
        // O png ok existe mas não é referenciado — acrescentar 2.ª textura ok
        // via 3.ª entidade para provar que NÃO gera issue.
        world
            .entities
            .push(with_texture("d", "/assets/textures/ok.png", "z"));
        let config = crate::config::fixture();
        let report = audit(&world, &world_dir, &[asset_root.clone()], &config);
        assert_eq!(report.missing_count(), 0);
        assert_eq!(report.issues.len(), 3, "{:?}", report.issues);
        assert!(
            report
                .issues
                .iter()
                .any(|i| i.message.contains("glb inválido"))
        );
        assert!(
            report
                .issues
                .iter()
                .any(|i| i.message.contains("PNG inválido"))
        );
        assert!(
            report
                .issues
                .iter()
                .any(|i| i.message.contains("formato de textura não suportado: .svg"))
        );
    }

    #[test]
    fn test_scripts_styles_and_music() {
        let (_dir, world_dir, asset_root) = setup();
        std::fs::write(
            world_dir.join("scripts/lobo.lua"),
            "function on_update(dt) end",
        )
        .unwrap();
        std::fs::create_dir_all(asset_root.join("assets/audio/bgm")).unwrap();
        std::fs::write(asset_root.join("assets/audio/bgm/explore.ogg"), b"ogg").unwrap();
        let mut lobo = entity(
            "lobo",
            EntityKind::GltfScene {
                url: "/assets/meshes/lobo.glb".into(),
            },
            ColliderShape::Auto,
        );
        lobo.script = Some("lobo.lua".into());
        let world = ParsedWorld {
            entities: vec![
                lobo,
                entity(
                    "fantasma",
                    EntityKind::GltfScene {
                        url: "/assets/meshes/fantasma.glb".into(),
                    },
                    ColliderShape::Auto,
                ),
                entity(
                    "musica",
                    EntityKind::MusicLayer {
                        layer: "explore".into(),
                        base_volume: 0.2,
                    },
                    Default::default(),
                ),
                entity(
                    "musica2",
                    EntityKind::MusicLayer {
                        layer: "inexistente".into(),
                        base_volume: 0.2,
                    },
                    Default::default(),
                ),
            ],
            ..Default::default()
        };
        let config = crate::config::fixture();
        let report = audit(&world, &world_dir, &[asset_root.clone()], &config);
        // Ausentes: lobo.glb (não criado), fantasma.glb, bgm/inexistente.ogg.
        assert_eq!(report.missing_count(), 3, "{:?}", report.issues);
        // Script existente não gera issue; audio ok não gera.
        assert!(
            report
                .issues
                .iter()
                .all(|i| i.severity == Severity::Missing)
        );
    }

    fn write_bytes(dir: &tempfile::TempDir, name: &str, bytes: &[u8]) -> PathBuf {
        let path = dir.path().join(name);
        std::fs::write(&path, bytes).expect("escrever fixture");
        path
    }

    #[test]
    fn test_pool_fallback_root_resolves_and_override_wins() {
        // Contrato multi-root (docs/ASSETS.md): a 1.ª root serve primeiro
        // (override por-mundo), o pool preenche o que faltar, e o "ausente"
        // aponta à 1.ª root quando ninguém tem o ficheiro.
        let dir = tempfile::tempdir().expect("tmpdir");
        let world_dir = dir.path().join("mundo");
        let pool = dir.path().join("pool");
        std::fs::create_dir_all(world_dir.join("assets/meshes")).unwrap();
        std::fs::create_dir_all(pool.join("assets/meshes")).unwrap();
        // Só no pool → resolvido pelo fallback.
        std::fs::write(
            pool.join("assets/meshes/do_pool.glb"),
            glb_bytes(None),
        )
        .unwrap();
        // Nas DUAS (override local ganha; basta existir para não faltar).
        std::fs::write(world_dir.join("assets/meshes/override.glb"), glb_bytes(None)).unwrap();
        std::fs::write(pool.join("assets/meshes/override.glb"), glb_bytes(None)).unwrap();
        // Os SFX da engine são auditados por HARDCODE (nunca aparecem no
        // XML) — fixture no POOL prova que o fallback também os resolve.
        for rel in engine_audio_files(&crate::config::fixture()) {
            let path = pool.join(rel.trim_start_matches('/'));
            std::fs::create_dir_all(path.parent().expect("parent")).unwrap();
            std::fs::File::create(&path).unwrap();
        }
        let world = ParsedWorld {
            entities: vec![
                entity(
                    "a",
                    EntityKind::GltfScene {
                        url: "/assets/meshes/do_pool.glb".into(),
                    },
                    ColliderShape::Auto,
                ),
                entity(
                    "b",
                    EntityKind::GltfScene {
                        url: "/assets/meshes/override.glb".into(),
                    },
                    ColliderShape::Auto,
                ),
                entity(
                    "c",
                    EntityKind::GltfScene {
                        url: "/assets/meshes/fantasma.glb".into(),
                    },
                    ColliderShape::Auto,
                ),
            ],
            ..Default::default()
        };
        let roots = vec![world_dir.clone(), pool.clone()];
        let config = crate::config::fixture();
        let report = audit(&world, &world_dir, &roots, &config);
        // Só fantasma falta — e a mensagem aponta à root do MUNDO.
        assert_eq!(report.missing_count(), 1, "{:?}", report.issues);
        assert!(
            report.issues[0]
                .message
                .contains(&world_dir.join("assets/meshes/fantasma.glb").display().to_string()),
            "{:?}",
            report.issues
        );
    }

    /// Cabeçalho KTX2 mínimo (12 magia + 32 campos + scheme @44 = 48 bytes).
    fn ktx2_header(vk_format: u32, scheme: u32) -> Vec<u8> {
        let mut bytes = KTX2_MAGIC.to_vec();
        bytes.extend_from_slice(&vk_format.to_le_bytes()); // vkFormat @12
        bytes.extend_from_slice(&1u32.to_le_bytes()); // typeSize
        bytes.extend_from_slice(&256u32.to_le_bytes()); // pixelWidth
        bytes.extend_from_slice(&256u32.to_le_bytes()); // pixelHeight
        bytes.extend_from_slice(&1u32.to_le_bytes()); // pixelDepth
        bytes.extend_from_slice(&1u32.to_le_bytes()); // layerCount
        bytes.extend_from_slice(&1u32.to_le_bytes()); // faceCount
        bytes.extend_from_slice(&1u32.to_le_bytes()); // levelCount
        bytes.extend_from_slice(&scheme.to_le_bytes()); // supercompressionScheme @44
        bytes
    }

    #[test]
    fn test_ktx2_etc1s_and_etc2_flagged_uastc_passes() {
        let dir = tempfile::tempdir().expect("tmpdir");
        // ETC1S/BasisLZ: vkFormat 0 (UNDEFINED — Basis Universal) → warn.
        let mut issues = Vec::new();
        audit_texture(
            &write_bytes(&dir, "etc1s.ktx2", &ktx2_header(0, 1)),
            "teste",
            &mut issues,
        );
        assert_eq!(issues.len(), 1, "etc1s tem de ser apanhado: {issues:?}");
        // ETC2 cru (147 = VK_FORMAT_ETC2_R8G8B8_UNORM_BLOCK) → warn.
        let mut issues = Vec::new();
        audit_texture(
            &write_bytes(&dir, "etc2.ktx2", &ktx2_header(147, 0)),
            "teste",
            &mut issues,
        );
        assert_eq!(issues.len(), 1, "ETC2 cru tem de ser apanhado: {issues:?}");
        // UASTC (37 = VK_FORMAT_R8G8B8A8_UNORM, scheme Zstandard) → limpo.
        let mut issues = Vec::new();
        audit_texture(
            &write_bytes(&dir, "uastc.ktx2", &ktx2_header(37, 2)),
            "teste",
            &mut issues,
        );
        assert!(issues.is_empty(), "UASTC passa limpo: {issues:?}");
        // Magia errada (PNG guardado como .ktx2) → warn.
        let mut issues = Vec::new();
        audit_texture(
            &write_bytes(&dir, "falso.ktx2", b"\x89PNG\r\n\x1a\n"),
            "teste",
            &mut issues,
        );
        assert_eq!(issues.len(), 1, "magia errada tem de ser apanhada");
    }

    #[test]
    fn test_webp_and_hdr_magic() {
        let dir = tempfile::tempdir().expect("tmpdir");
        let mut webp = b"RIFF\x24\x00\x00\x00".to_vec();
        webp.extend_from_slice(b"WEBP");
        let mut issues = Vec::new();
        audit_texture(&write_bytes(&dir, "ok.webp", &webp), "teste", &mut issues);
        assert!(issues.is_empty(), "webp válido passa: {issues:?}");
        let mut issues = Vec::new();
        audit_texture(
            &write_bytes(&dir, "falso.webp", b"\x89PNG\r\n\x1a\n"),
            "teste",
            &mut issues,
        );
        assert_eq!(issues.len(), 1, "webp sem RIFF/WEBP tem de ser apanhado");
        let mut issues = Vec::new();
        audit_texture(
            &write_bytes(&dir, "ok.hdr", b"#?RADIANCE\n\n-Y 4 3\n"),
            "teste",
            &mut issues,
        );
        assert!(issues.is_empty(), "hdr válido passa: {issues:?}");
    }

    // ── Conflitos estrada × água/cliff ─────────────────────────────────

    /// Parse de um mundo XML pequeno (sem assets) para os testes de features.
    fn feature_index(src: &str) -> FeatureIndex {
        let dir = tempfile::tempdir().expect("tmpdir");
        let path = dir.path().join("world.xml");
        std::fs::write(&path, src).unwrap();
        let loaded = crate::xml::include::load_world(&path).expect("parse xml");
        let world = crate::recipes::parse_world(&loaded.root_attrs, &loaded.nodes)
            .expect("parse ir");
        let mut idx = FeatureIndex::default();
        collect_features(&world.entities, [0.0, 0.0], &mut idx);
        idx
    }

    #[test]
    fn test_road_entering_lake_water_warns() {
        let idx = feature_index(
            r#"<world>
              <Lake at="0 0" radius="10" depth="2" />
              <Road name="trilha" path="-30 0 30 0" width="4" flatten="0" />
            </world>"#,
        );
        let issues = audit_feature_conflicts(&idx);
        let hits: Vec<_> = issues
            .iter()
            .filter(|i| i.message.contains("lâmina do lago"))
            .collect();
        assert_eq!(hits.len(), 1, "{issues:?}");
        assert_eq!(hits[0].severity, Severity::Warning);
        assert!(hits[0].message.contains("\"trilha\""), "{issues:?}");
    }

    #[test]
    fn test_road_far_from_water_is_silent() {
        let idx = feature_index(
            r#"<world>
              <Lake at="0 0" radius="10" depth="2" />
              <River path="-50 200 50 200" width="10" />
              <Cliff path="-40 -200 40 -200" width="6" />
              <Road name="zona" path="-30 100 30 100" width="4" flatten="0" />
            </world>"#,
        );
        let issues = audit_feature_conflicts(&idx);
        assert!(issues.is_empty(), "{issues:?}");
    }

    #[test]
    fn test_bridge_tiers_over_lake() {
        // Pontas a 11 m do centro (11 < 10×1.45 worst-case) → Warning mesmo
        // com o contorno real da fase a poder estar mais pequeno.
        let idx = feature_index(
            r#"<world>
              <Lake at="0 0" radius="10" depth="2" />
              <RoadNetwork default-profile="artery" default-width="4">
                <Way id="e" xz="-11 0" />
                <Way id="w" xz="11 0" />
                <Segment a="e" b="w" profile="bridge" />
              </RoadNetwork>
            </world>"#,
        );
        let issues = audit_feature_conflicts(&idx);
        let warns: Vec<_> = issues
            .iter()
            .filter(|i| i.severity == Severity::Warning)
            .collect();
        assert_eq!(warns.len(), 1, "{issues:?}");
        assert!(warns[0].message.contains("afaste as pontas"), "{issues:?}");

        // Pontas a 15 m (> worst-case 14.5) mas o vão cruza a lâmina →
        // Warning: o deck é ribbon plana sem collider — travessia quebrada
        // no terreno voxel, mesmo com as pontas secas.
        let idx = feature_index(
            r#"<world>
              <Lake at="0 0" radius="10" depth="2" />
              <RoadNetwork default-profile="artery" default-width="4">
                <Way id="e" xz="-15 0" />
                <Way id="w" xz="15 0" />
                <Segment a="e" b="w" profile="bridge" />
              </RoadNetwork>
            </world>"#,
        );
        let issues = audit_feature_conflicts(&idx);
        assert!(
            issues.iter().all(|i| i.severity == Severity::Warning),
            "{issues:?}"
        );
        let cruza = issues
            .iter()
            .find(|i| i.message.contains("cruza o lago"))
            .expect("mensagem de travessia");
        assert!(cruza.message.contains("<Bridge>"), "{issues:?}");
        // O ponto reportado é o de maior penetração (meio do vão), não as
        // pontas — o passo de amostragem (2 m desde o vértice) pega (-1, 0).
        assert!(cruza.message.contains("(-1, 0)"), "{issues:?}");
    }

    #[test]
    fn test_road_crossing_river_warns_bridge_tiers() {
        let idx = feature_index(
            r#"<world>
              <River path="-50 0 50 0" width="10" bank-width="2" />
              <Road name="vau" path="0 -40 0 40" width="4" flatten="0" />
            </world>"#,
        );
        let issues = audit_feature_conflicts(&idx);
        let hits: Vec<_> = issues
            .iter()
            .filter(|i| i.message.contains("lâmina do rio"))
            .collect();
        assert_eq!(hits.len(), 1, "{issues:?}");
        assert_eq!(hits[0].severity, Severity::Warning);

        // Ponte com pontas claras (20 ≥ 5+2+2) → Warning (deck sem collider);
        // pontas curtas → Warning (água cobre as pontas).
        let idx = feature_index(
            r#"<world>
              <River path="-50 0 50 0" width="10" bank-width="2" />
              <RoadNetwork default-profile="artery" default-width="4">
                <Way id="n" xz="0 -20" />
                <Way id="s" xz="0 20" />
                <Segment a="n" b="s" profile="bridge" />
              </RoadNetwork>
            </world>"#,
        );
        let issues = audit_feature_conflicts(&idx);
        assert!(
            issues.iter().all(|i| i.severity == Severity::Warning),
            "{issues:?}"
        );
        assert!(
            issues
                .iter()
                .any(|i| i.message.contains("<Bridge> volumétrica")),
            "{issues:?}"
        );

        let idx = feature_index(
            r#"<world>
              <River path="-50 0 50 0" width="10" bank-width="2" />
              <RoadNetwork default-profile="artery" default-width="4">
                <Way id="n" xz="0 -6" />
                <Way id="s" xz="0 6" />
                <Segment a="n" b="s" profile="bridge" />
              </RoadNetwork>
            </world>"#,
        );
        let issues = audit_feature_conflicts(&idx);
        assert!(
            issues
                .iter()
                .any(|i| i.severity == Severity::Warning && i.message.contains("eixo do rio")),
            "{issues:?}"
        );
    }

    #[test]
    fn test_road_crossing_cliff_band_warns() {
        let idx = feature_index(
            r#"<world>
              <Cliff path="-40 0 40 0" width="6" />
              <Road name="atalho" path="-20 -20 -20 20" width="4" flatten="0" />
            </world>"#,
        );
        let issues = audit_feature_conflicts(&idx);
        let hits: Vec<_> = issues
            .iter()
            .filter(|i| i.message.contains("banda do cliff"))
            .collect();
        assert_eq!(hits.len(), 1, "{issues:?}");
        assert_eq!(hits[0].severity, Severity::Warning);
    }
}
