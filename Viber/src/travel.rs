//! Travel, A Nota & wayfinding (loop 6 do port simple-rpg) — o análogo
//! nativo de `nota.ts` + `travel.ts` + wayfinding originais:
//!
//! - **A Nota**: 12 marcos de traçado (3 por bioma, por `name=` de entidade).
//!   [F] perto de um marco não assinado → "Medido e assinado"; os 3 de um
//!   bioma → toast "bioma assinado". Guardado em [`NotaLog`] (persistência
//!   vem no loop 7).
//! - **Viagem rápida**: perto da fogueira (praça), **[G]** abre o painel de
//!   viagem aos marcos assinados; ↑↓ seleciona, [J] teleporta.
//! - **Waypoint**: o último marco assinado vira waypoint — linha no topo do
//!   ecrã com rumo (N/NE/…) e distância.
//! - **EnemyRegistry**: criaturas hostis vivas por banda do mundo
//!   (centro/norte/sul/este/oeste), snapshot para scripts via
//!   `viber.alive_in_region(idx)` — o gating do boss final vive no
//!   `boss.lua` (dorme enquanto o sul não estiver limpo).
//! - QA: **F11** teleporta ao próximo marco por assinar.
//!
//! Aggro-chain: `AttackAlert` (feedback) → `on_player_attack(px, pz)` nos
//! scripts num raio de 15 m do alvo atingido.

use std::collections::HashSet;

use bevy::prelude::*;

use crate::luau::ScriptToast;
use crate::player::Player;
use crate::profiler::{Group, timed};

/// Alcance para assinar um marco (m) — `NOTA_MARK_RADIUS` por bioma.
pub const NOTA_RANGE_DEFAULT_M: f32 = 12.0;
/// Alcance do painel de viagem à fogueira (a praça toda).
pub const TRAVEL_CAMPFIRE_RANGE_M: f32 = 14.0;
/// Raio do alerta a aliados (aggro-chain, m).
pub const ALERT_RADIUS_M: f32 = 15.0;

// ── catálogo da Nota (espelha nota-landmarks.ts) ────────────────────────

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Biome {
    DarkForest,
    Desert,
    Swamp,
    FrozenPeaks,
    /// Bioma-sintético das atalaias de agrimensura (`tower`): os marcos com
    /// este id são TORRES — sempre assináveis/viáveis, nunca trancadas (são
    /// a chave, não a porta; ver [`RevealedBiomes`]).
    Tower,
}

impl Biome {
    pub fn label(self) -> &'static str {
        match self {
            Biome::DarkForest => "Floresta Sombria",
            Biome::Desert => "Deserto",
            Biome::Swamp => "Pântano",
            Biome::FrozenPeaks => "Picos Gelados",
            Biome::Tower => "Atalaias",
        }
    }

    /// Id canónico do bioma (o mesmo vocabulário do attr `biome` da tag
    /// declarativa `<Landmark>`).
    pub fn id(self) -> &'static str {
        match self {
            Biome::DarkForest => "dark_forest",
            Biome::Desert => "desert",
            Biome::Swamp => "swamp",
            Biome::FrozenPeaks => "frozen_peaks",
            Biome::Tower => "tower",
        }
    }

    /// Resolve um id declarado em XML (`dark_forest`, `desert`, `swamp`,
    /// `frozen_peaks`, `tower`); desconhecido → `None` (o marco fica no
    /// catálogo com o id cru, ver [`NotaLandmarkOwned::biome_label`]).
    pub fn from_id(id: &str) -> Option<Biome> {
        match id {
            "dark_forest" => Some(Biome::DarkForest),
            "desert" => Some(Biome::Desert),
            "swamp" => Some(Biome::Swamp),
            "frozen_peaks" => Some(Biome::FrozenPeaks),
            "tower" => Some(Biome::Tower),
            _ => None,
        }
    }

    /// Quest de traçado do bioma (id no JSON). As atalaias não têm quest de
    /// traçado — a recompensa delas é a REVELAÇÃO do bioma.
    pub fn survey_quest(self) -> &'static str {
        match self {
            Biome::DarkForest => "forest_survey",
            Biome::Desert => "desert_survey",
            Biome::Swamp => "swamp_survey",
            Biome::FrozenPeaks => "peaks_survey",
            Biome::Tower => "",
        }
    }

    pub fn mark_radius(self) -> f32 {
        match self {
            Biome::DarkForest => 10.0,
            Biome::Desert => 12.0,
            Biome::Swamp => 11.0,
            Biome::FrozenPeaks => 9.0,
            Biome::Tower => 8.0,
        }
    }
}

/// Marco de traçado: (name da entidade, bioma, rótulo legível).
pub struct NotaLandmark {
    pub name: &'static str,
    pub biome: Biome,
    pub label: &'static str,
}

/// Marco de traçado OWNED (Fase B2) — a versão `String` do [`NotaLandmark`],
/// construída a partir das tags declarativas `<Landmark>` do mundo ou do
/// fallback hardcoded ([`LandmarkCatalog::fallback`]). `survey-quest` e
/// `mark-radius` têm defaults por bioma (`Biome::survey_quest` /
/// `Biome::mark_radius`), sobreponíveis por attr.
#[derive(Debug, Clone, PartialEq)]
pub struct NotaLandmarkOwned {
    /// `name` da entidade no mundo — é por ele que o [F] casa e as quests
    /// `visit` apontam.
    pub name: String,
    /// Id do bioma tal como declarado (`dark_forest`, `desert`, `swamp`,
    /// `frozen_peaks`) — o agrupamento "3 por bioma" compara ESTE id.
    pub biome_id: String,
    pub label: String,
    /// Quest de traçado associada (dados de paridade; o consumo live das
    /// quests `*_survey` continua a vir dos JSON).
    pub survey_quest: String,
    /// Alcance de marcação [F] (m).
    pub mark_radius: f32,
    /// `Some(bioma)` numa ATALAIA de agrimensura: ativar a torre revela este
    /// bioma (os marcos dele entram na bússola/minimapa/travel rápido e
    /// passam a ser assináveis). `None` num marco comum — disponível desde
    /// que o bioma não esteja trancado por uma atalaia.
    pub tower_reveals: Option<String>,
}

impl NotaLandmarkOwned {
    /// Bioma resolvido (`Biome::from_id`); `None` = id não reconhecido.
    pub fn biome(&self) -> Option<Biome> {
        Biome::from_id(&self.biome_id)
    }

    /// Rótulo de exposição do bioma; id desconhecido → o id cru (o marco
    /// continua assinável/viável, só o toast mostra o nome menos amigável).
    pub fn biome_label(&self) -> String {
        self.biome()
            .map(|b| b.label().to_string())
            .unwrap_or_else(|| self.biome_id.clone())
    }
}

/// Os 12 marcos do fallback — espelham os `objective.target` das quests
/// `*_survey`.
pub const LANDMARKS: [NotaLandmark; 12] = [
    NotaLandmark {
        name: "forest-outpost-tower",
        biome: Biome::DarkForest,
        label: "Torre do Posto Avançado",
    },
    NotaLandmark {
        name: "forest-crossroads-well",
        biome: Biome::DarkForest,
        label: "Poço da Encruzilhada",
    },
    NotaLandmark {
        name: "forest-stone-circle",
        biome: Biome::DarkForest,
        label: "Círculo de Menires",
    },
    NotaLandmark {
        name: "desert-arch",
        biome: Biome::Desert,
        label: "Arco do Deserto",
    },
    NotaLandmark {
        name: "desert-caravan-wreck",
        biome: Biome::Desert,
        label: "Caravana Encalhada",
    },
    NotaLandmark {
        name: "desert-sun-obelisk",
        biome: Biome::Desert,
        label: "Obelisco do Sol",
    },
    NotaLandmark {
        name: "swamp-wrecked-boat",
        biome: Biome::Swamp,
        label: "Barco Naufragado",
    },
    NotaLandmark {
        name: "swamp-sunken-graves",
        biome: Biome::Swamp,
        label: "Covas Submersas",
    },
    NotaLandmark {
        name: "swamp-bone-altar",
        biome: Biome::Swamp,
        label: "Altar de Ossos",
    },
    NotaLandmark {
        name: "peaks-cairn-1",
        biome: Biome::FrozenPeaks,
        label: "Primeiro Mojão",
    },
    NotaLandmark {
        name: "peaks-cairn-2",
        biome: Biome::FrozenPeaks,
        label: "Segundo Mojão",
    },
    NotaLandmark {
        name: "peaks-cairn-3",
        biome: Biome::FrozenPeaks,
        label: "Terceiro Mojão",
    },
];

/// Catálogo dos marcos da Nota (Fase B2): preenchido no arranque a partir das
/// tags declarativas `<Landmark>` do mundo (via `recipes::spawn::startup`);
/// quando o mundo não declara NENHUM, [`Default`] entrega o fallback
/// hardcoded ([`fallback`]) — política aditiva: o simple-rpg sem declarações
/// comporta-se exatamente como antes.
#[derive(Debug, Clone, Resource)]
pub struct LandmarkCatalog(pub Vec<NotaLandmarkOwned>);

impl Default for LandmarkCatalog {
    fn default() -> Self {
        Self::fallback()
    }
}

impl LandmarkCatalog {
    /// As 12 entradas hardcoded ([`LANDMARKS`]) no formato owned — o
    /// comportamento de sempre quando o mundo não declara `<Landmark>`.
    pub fn fallback() -> Self {
        Self(
            LANDMARKS
                .iter()
                .map(|l| NotaLandmarkOwned {
                    name: l.name.to_string(),
                    biome_id: l.biome.id().to_string(),
                    label: l.label.to_string(),
                    survey_quest: l.biome.survey_quest().to_string(),
                    mark_radius: l.biome.mark_radius(),
                    tower_reveals: None,
                })
                .collect(),
        )
    }
}

/// Lookup puro: marco por `name` de entidade DENTRO de um catálogo.
pub fn landmark_by_name<'a>(
    catalog: &'a LandmarkCatalog,
    name: &str,
) -> Option<&'a NotaLandmarkOwned> {
    catalog.0.iter().find(|l| l.name == name)
}

/// Quantos marcos do MESMO bioma (mesmo `biome_id`) faltam assinar — puro,
/// para os testes do toast "faltam N em bioma".
pub fn remaining_in_biome(
    catalog: &LandmarkCatalog,
    marked: &HashSet<String>,
    biome_id: &str,
) -> usize {
    catalog
        .0
        .iter()
        .filter(|l| l.biome_id == biome_id && !marked.contains(&l.name))
        .count()
}

// ── atalaias de agrimensura (reveal BotW) ───────────────────────────────

/// Biomas revelados por atalaias ativadas (`viber.reveal_biome` nos scripts
/// das torres). Persistido no save (`revealed_biomes`); os marcos de um
/// bioma NÃO revelado não se assinam [F], não viajam [G] nem aparecem na
/// bússola/minimapa — o mundo fica escuro até alguém subir à torre.
#[derive(Debug, Clone, Resource, Default)]
pub struct RevealedBiomes(pub HashSet<String>);

impl RevealedBiomes {
    /// Biomas que TÊM atalaia no catálogo (a chave existe para estes).
    pub fn keyed_biomes(catalog: &LandmarkCatalog) -> HashSet<&str> {
        catalog
            .0
            .iter()
            .filter_map(|l| l.tower_reveals.as_deref())
            .collect()
    }
}

/// Um marco está disponível (assinável/viável/visível)? Atalaias são sempre
/// disponíveis; um marco comum precisa do bioma revelado — MAS mundos sem
/// NENHUMA atalaia nunca trancam (back-compat: o simple-rpg sem torres
/// comporta-se como antes, e biomas sem chave ficam livres).
pub fn landmark_available(
    catalog: &LandmarkCatalog,
    revealed: &RevealedBiomes,
    name: &str,
) -> bool {
    let Some(landmark) = landmark_by_name(catalog, name) else {
        return false;
    };
    if landmark.tower_reveals.is_some() {
        return true;
    }
    let keyed = RevealedBiomes::keyed_biomes(catalog);
    !keyed.contains(landmark.biome_id.as_str()) || revealed.0.contains(&landmark.biome_id)
}

/// Blip de marcodisponível para o HUD (bússola/minimapa) — snapshot a 2 Hz
/// ([`landmark_blip_system`]); as atalaias levam `tower: true` para o HUD
/// as desenhar com silhueta própria.
#[derive(Debug, Clone, PartialEq)]
pub struct LandmarkBlip {
    pub name: String,
    pub label: String,
    pub tower: bool,
    pub position: Vec3,
}

/// Snapshot dos marcos DISPONÍVEIS com posição de mundo (atalaias + biomas
/// revelados). Consumido pelo minimapa/compasso; recalculado a 2 Hz —
/// varrer ~79k nomes por FRAME era o bug de perf que o menu de viagem já
/// pagou uma vez.
#[derive(Debug, Clone, Resource, Default)]
pub struct LandmarkBlips(pub Vec<LandmarkBlip>);

/// Passo puro do snapshot de blips (testável sem mundo): filtra o catálogo
/// pelos disponíveis e casa posições pelos nomes de entidade.
pub fn collect_landmark_blips(
    catalog: &LandmarkCatalog,
    revealed: &RevealedBiomes,
    named: &[(String, Vec3)],
) -> Vec<LandmarkBlip> {
    catalog
        .0
        .iter()
        .filter(|l| landmark_available(catalog, revealed, &l.name))
        .filter_map(|l| {
            named
                .iter()
                .find(|(name, _)| name == &l.name)
                .map(|(_, pos)| LandmarkBlip {
                    name: l.name.clone(),
                    label: l.label.clone(),
                    tower: l.tower_reveals.is_some(),
                    position: *pos,
                })
        })
        .collect()
}

/// Bandas do mundo para o registry de hostis (índice = `alive_in_region`).
pub const REGIONS: [(&str, f32, f32, f32, f32); 5] = [
    // (rótulo, min_x, min_z, max_x, max_z)
    ("centro", -120.0, -120.0, 120.0, 120.0),
    ("norte", -2000.0, 120.0, 2000.0, 2000.0),
    ("sul", -2000.0, -2000.0, 2000.0, -120.0),
    ("este", 120.0, -2000.0, 2000.0, 2000.0),
    ("oeste", -2000.0, -2000.0, -120.0, 2000.0),
];

/// Índice da região que contém o ponto.
pub fn region_of(x: f32, z: f32) -> usize {
    REGIONS
        .iter()
        .position(|(_, x0, z0, x1, z1)| x >= *x0 && x <= *x1 && z >= *z0 && z <= *z1)
        .unwrap_or(0)
}

// ── estado ──────────────────────────────────────────────────────────────

/// A Nota: marcos assinados (persistência no loop 7).
#[derive(Debug, Clone, Resource, Default)]
pub struct NotaLog {
    pub marked: HashSet<String>,
}

/// Waypoint atual (último marco assinado). O rótulo é `String` desde a Fase
/// B2: com o catálogo declarável (`<Landmark>`) os labels já não são
/// `'static` — vivem no [`LandmarkCatalog`].
#[derive(Debug, Clone, Resource, Default)]
pub struct Waypoint {
    pub label: Option<String>,
    pub position: Option<Vec3>,
}

/// Hostis vivos por banda do mundo (índice = [`REGIONS`]).
#[derive(Debug, Clone, Resource, Default)]
pub struct EnemyRegistry {
    pub counts: [u32; 5],
}

impl EnemyRegistry {
    /// Puro: conta quantas posições caem em cada banda.
    pub fn count_positions(positions: &[(f32, f32)]) -> [u32; 5] {
        let mut counts = [0_u32; 5];
        for &(x, z) in positions {
            counts[region_of(x, z)] += 1;
        }
        counts
    }
}

// ── plugin ──────────────────────────────────────────────────────────────

pub struct TravelPlugin;

impl Plugin for TravelPlugin {
    fn build(&self, app: &mut App) {
        app.init_resource::<LandmarkCatalog>()
            .init_resource::<NotaLog>()
            .init_resource::<Waypoint>()
            .init_resource::<EnemyRegistry>()
            .init_resource::<RevealedBiomes>()
            .init_resource::<LandmarkBlips>()
            .init_resource::<TravelMenuState>()
            .init_resource::<TravelFade>()
            // O painel de viagem espelha-se em `MenusOpen.travel` (R2-G2);
            // nasce aqui também para apps mínimas (idempotente com o
            // MenusPlugin — mesmo padrão de Skills/Combat).
            .init_resource::<crate::menus::MenusOpen>()
            // Idempotente com o Ambient/Combat (apps mínimas auto-suficientes).
            .add_message::<crate::ambient::SfxEvent>()
            .add_message::<TravelPing>()
            .add_systems(
                Startup,
                (
                    spawn_travel_menu,
                    spawn_waypoint_hud,
                    spawn_travel_fade_overlay,
                ),
            )
            .add_systems(
                Update,
                (
                    timed(Group::World, nota_measure_system),
                    timed(Group::World, travel_menu_system),
                    travel_fade_system,
                    timed(Group::Hud, landmark_blip_system),
                    timed(Group::Hud, waypoint_hud_system),
                    enemy_registry_system,
                    quest_debug_landmark.run_if(crate::quests::debug_keys_enabled),
                ),
            );
    }
}

/// Pedido de viagem (puro p/ testes do menu).
#[derive(Debug, Clone, bevy::ecs::message::Message)]
pub struct TravelPing {
    pub label: &'static str,
}

// ── fade da viagem rápida (passe de juice r1) ───────────────────────────

/// Duração do fade a PRETO antes do teleport (s).
pub const TRAVEL_FADE_OUT: f32 = 0.4;
/// Duração do fade de volta do preto depois do teleport (s).
pub const TRAVEL_FADE_IN: f32 = 0.4;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TravelFadePhase {
    /// Sem viagem em curso (overlay escondido).
    Idle,
    /// A escurecer (0 → 1 em [`TRAVEL_FADE_OUT`]).
    Out,
    /// Preto cheio atingido — teleport feito, a clarear (1 → 0).
    In,
}

/// Estado do fade da viagem rápida. O pedido ([J] no menu) só ARMA o estado
/// (`Out` + destino); [`travel_fade_system`] aplica o teleport no preto
/// cheio — máquina pura em [`travel_fade_step`] para os testes.
#[derive(Debug, Clone, Copy, Resource)]
pub struct TravelFade {
    pub phase: TravelFadePhase,
    /// Segundos restantes da fase atual.
    pub timer: f32,
    /// Destino a aplicar no meio do fade (já com Y amostrado).
    pub target: Option<Vec3>,
}

impl Default for TravelFade {
    fn default() -> Self {
        Self {
            phase: TravelFadePhase::Idle,
            timer: 0.0,
            target: None,
        }
    }
}

/// Avança a máquina um passo de `dt`; devolve `(alpha do overlay, chegou a
/// meio?)`. O teleport dispara EXATAMENTE uma vez, na transição Out→In.
pub fn travel_fade_step(state: &mut TravelFade, dt: f32) -> (f32, bool) {
    match state.phase {
        TravelFadePhase::Idle => (0.0, false),
        TravelFadePhase::Out => {
            state.timer -= dt;
            if state.timer <= 0.0 {
                state.phase = TravelFadePhase::In;
                state.timer = TRAVEL_FADE_IN;
                (1.0, true)
            } else {
                (1.0 - state.timer / TRAVEL_FADE_OUT, false)
            }
        }
        TravelFadePhase::In => {
            state.timer -= dt;
            if state.timer <= 0.0 {
                *state = TravelFade::default();
                (0.0, false)
            } else {
                (state.timer / TRAVEL_FADE_IN, false)
            }
        }
    }
}

// ── A Nota: medir e assinar [F] ─────────────────────────────────────────

/// [F] perto de um marco não assinado → assina; 3 do bioma → bioma assinado.
#[allow(clippy::too_many_arguments)]
fn nota_measure_system(
    keys: Res<ButtonInput<KeyCode>>,
    players: Query<&GlobalTransform, With<Player>>,
    named: Query<(&Name, &GlobalTransform)>,
    catalog: Res<LandmarkCatalog>,
    revealed: Res<RevealedBiomes>,
    mut nota: ResMut<NotaLog>,
    mut waypoint: ResMut<Waypoint>,
    mut toasts: MessageWriter<ScriptToast>,
) {
    // just_pressed (não pressed): o tap sintético da bridge faz press+release
    // no mesmo lote, e `pressed` já voltou a false quando o Update corre.
    // SEM throttle a montante: just_pressed só é verdadeiro 1 frame e um
    // gate de 0,3 s descartava a maioria das pressões de [F].
    if !keys.just_pressed(KeyCode::KeyF) {
        return;
    }
    let Some(player) = players.iter().next() else {
        return;
    };
    let player_pos = player.translation();
    // Visitar um sítio é 2D: a distância de marcação ignora Y — diferenças
    // residuais de cota (marco num outeiro, herói na base) não deviam
    // impedir o [F] nem completar as quests *_survey.
    let Some((name, _)) = named.iter().find(|(name, t)| {
        landmark_by_name(&catalog, name)
            .filter(|l| !nota.marked.contains(&l.name))
            // Atalaia de agrimensura: marcos de bioma não revelado ficam
            // "escuros" — o [F] só assina com o bioma revelado pela torre.
            .filter(|l| landmark_available(&catalog, &revealed, &l.name))
            .filter(|l| t.translation().xz().distance(player_pos.xz()) <= l.mark_radius)
            .is_some()
    }) else {
        // Em alcance de um marco trancado? Diz ao jogador O QUE falta —
        // o silêncio lia-se como tecla avariada.
        if let Some(locked) = named.iter().find_map(|(name, t)| {
            landmark_by_name(&catalog, name)
                .filter(|l| !nota.marked.contains(&l.name))
                .filter(|l| !landmark_available(&catalog, &revealed, &l.name))
                .filter(|l| t.translation().xz().distance(player_pos.xz()) <= l.mark_radius)
        }) {
            toasts.write(ScriptToast(format!(
                "A terra aqui ainda não foi cartografada — procura a atalaia de {}.",
                locked.biome_label()
            )));
        }
        return;
    };
    let name = name.to_string();
    let Some(landmark) = landmark_by_name(&catalog, &name) else {
        return;
    };
    nota.marked.insert(name.clone());
    waypoint.label = Some(landmark.label.clone());
    waypoint.position = nearest_named(&named, &name, player_pos);
    let biome_label = landmark.biome_label();
    let remaining = remaining_in_biome(&catalog, &nota.marked, &landmark.biome_id);
    if remaining == 0 {
        toasts.write(ScriptToast(format!(
            "Medido e assinado: {} — {} ASSINADO!",
            landmark.label,
            biome_label.to_uppercase()
        )));
    } else {
        toasts.write(ScriptToast(format!(
            "Medido e assinado: {} (faltam {remaining} em {biome_label})",
            landmark.label
        )));
    }
    info!(target: "viber::nota", "marco '{name}' assinado");
}

/// A entidade `name` MAIS PRÓXIMA de `from` (não a primeira da query): com
/// nomes duplicados (includes repetidos) a primeira podia estar do outro
/// lado do mapa — o waypoint e o fast-travel apontavam para lá.
fn nearest_named(named: &Query<(&Name, &GlobalTransform)>, name: &str, from: Vec3) -> Option<Vec3> {
    named
        .iter()
        .filter(|(entity_name, _)| entity_name.as_str() == name)
        .map(|(_, t)| t.translation())
        .min_by(|a, b| {
            a.distance_squared(from)
                .total_cmp(&b.distance_squared(from))
        })
}

// ── blips de marcos para o HUD (bússola/minimapa) ───────────────────────

/// Cadência do snapshot de blips (s) — varrer todas as entidades nomeadas
/// custa ~1 ms; a 2 Hz o custo amortizado é irrelevante e os blips ainda
/// acompanham a revelação no frame seguinte a subir a torre.
pub const LANDMARK_BLIP_PERIOD: f32 = 0.5;

/// Snapshot a 2 Hz dos marcos disponíveis (atalaias + biomas revelados) com
/// a posição de mundo — o minimapa/compasso leem daqui e NUNCA varrem o
/// mundo eles próprios. SEM coletar os ~79k nomes num Vec: uma passagem com
/// lookup num mapa do catálogo — o vício de alloc que o menu de viagem
/// pagou em 2026-09-09 não volta aqui.
#[allow(clippy::type_complexity)]
fn landmark_blip_system(
    mut throttle: Local<f32>,
    time: Res<Time>,
    named: Query<(&Name, &GlobalTransform)>,
    catalog: Res<LandmarkCatalog>,
    revealed: Res<RevealedBiomes>,
    mut blips: ResMut<LandmarkBlips>,
) {
    // Recalcular quando a revelação MUDA (ativar a torre tem de acender os
    // blips já, não só daqui a meio segundo) ou no batimento do throttle.
    let due = *throttle <= 0.0 || revealed.is_changed() || catalog.is_changed();
    if !due {
        *throttle -= time.delta_secs();
        return;
    }
    *throttle = LANDMARK_BLIP_PERIOD;
    let index: std::collections::HashMap<&str, usize> = catalog
        .0
        .iter()
        .enumerate()
        .map(|(i, l)| (l.name.as_str(), i))
        .collect();
    let mut found = Vec::new();
    for (name, transform) in &named {
        let Some(&i) = index.get(name.as_str()) else {
            continue;
        };
        let landmark = &catalog.0[i];
        if landmark_available(&catalog, &revealed, &landmark.name) {
            found.push(LandmarkBlip {
                name: landmark.name.clone(),
                label: landmark.label.clone(),
                tower: landmark.tower_reveals.is_some(),
                position: transform.translation(),
            });
        }
    }
    blips.0 = found;
}

// ── viagem rápida [G] na fogueira ───────────────────────────────────────

#[derive(Component)]
struct TravelMenu;

#[derive(Component)]
struct TravelContent;

fn spawn_travel_menu(mut commands: Commands) {
    commands
        .spawn((
            Node {
                position_type: PositionType::Absolute,
                top: Val::Px(0.0),
                left: Val::Px(0.0),
                right: Val::Px(0.0),
                bottom: Val::Px(0.0),
                justify_content: JustifyContent::Center,
                align_items: AlignItems::Center,
                ..Default::default()
            },
            BackgroundColor(Color::srgba(0.0, 0.0, 0.0, 0.45)),
            Visibility::Hidden,
            Name::new("ui:travel"),
            TravelMenu,
        ))
        .with_children(|root| {
            root.spawn((
                Node {
                    width: Val::Px(480.0),
                    height: Val::Px(280.0),
                    padding: UiRect::all(Val::Px(20.0)),
                    flex_direction: FlexDirection::Column,
                    row_gap: Val::Px(8.0),
                    border_radius: BorderRadius::all(Val::Px(14.0)),
                    ..Default::default()
                },
                BackgroundColor(Color::srgba(0.08, 0.06, 0.04, 0.96)),
                BorderColor::all(Color::srgb(0.95, 0.65, 0.3)),
            ))
            .with_children(|panel| {
                panel.spawn((
                    Text::new("VIAJAR (fogueira da praça)"),
                    TextColor(Color::srgb(0.98, 0.8, 0.5)),
                    TextFont::from_font_size(20.0),
                ));
                panel.spawn((
                    Text::new(""),
                    TextColor(Color::srgb(0.9, 0.88, 0.8)),
                    TextFont::from_font_size(15.0),
                    TravelContent,
                ));
            });
        });
}

/// Estado de abertura do menu de viagem (lido pelo QA F11 e pelo HUD).
#[derive(Debug, Clone, Resource, Default)]
pub struct TravelMenuState {
    pub open: bool,
    pub selection: usize,
}

/// `true` quando o nome identifica uma fogueira (`campfire`, sem distinguir
/// maiúsculas) — sem alocar, porque corre sobre cada `Name` novo do mundo.
fn is_campfire_name(name: &str) -> bool {
    const NEEDLE: &[u8] = b"campfire";
    name.as_bytes()
        .windows(NEEDLE.len())
        .any(|w| w.eq_ignore_ascii_case(NEEDLE))
}

/// [G] perto da fogueira abre; ↑↓ seleciona marcos assinados; [J] viaja.
/// O [J] não teleporta directamente: arma o [`TravelFade`] (o teleport
/// acontece no preto cheio, em [`travel_fade_system`]).
#[allow(clippy::type_complexity, clippy::too_many_arguments)]
fn travel_menu_system(
    keys: Res<ButtonInput<KeyCode>>,
    players: Query<&GlobalTransform, With<Player>>,
    named: Query<(&Name, &GlobalTransform)>,
    catalog: Res<LandmarkCatalog>,
    revealed: Res<RevealedBiomes>,
    nota: Res<NotaLog>,
    mut state: ResMut<TravelMenuState>,
    // Espelho "painel aberto rouba input": sem isto, com o painel de viagem
    // aberto o player ANDAVA (W/S navegavam E moviam) e o [J] que confirma a
    // viagem disparava o melee — `MenusOpen.any()` é a porta do input.
    mut menus: ResMut<crate::menus::MenusOpen>,
    mut fade: ResMut<TravelFade>,
    terrain: Option<Res<crate::terrain::runtime::TerrainRuntime>>,
    mut q_menu: Query<&mut Visibility, With<TravelMenu>>,
    mut q_content: Query<&mut Text, With<TravelContent>>,
    mut toasts: MessageWriter<ScriptToast>,
    mut sfx: MessageWriter<crate::ambient::SfxEvent>,
    // Profiling 2026-09-09: o scan de TODAS as entidades nomeadas custava
    // ~1 ms POR FRAME; o throttle a 4 Hz que se seguiu ainda deixava um pico
    // de ~2,5 ms (p95) a cada 250 ms — `to_ascii_lowercase` alocava uma
    // String por cada uma das ~79k entidades nomeadas. As fogueiras são
    // indexadas UMA vez, quando o `Name` aparece (`Changed`), e o teste de
    // alcance corre todos os frames só sobre elas.
    new_names: Query<(Entity, &Name), Changed<Name>>,
    mut campfires: Local<Vec<Entity>>,
) {
    // Fogueira POR NOME ("campfire" no mundo) — sem o marcador, qualquer
    // entidade a <14 m (árvore, rocha, NPC) abria o fast-travel em todo o
    // lado e o requisito "fogueira da praça" era letra morta.
    for (entity, name) in &new_names {
        if is_campfire_name(name.as_str()) && !campfires.contains(&entity) {
            campfires.push(entity);
        }
    }
    campfires.retain(|&e| named.contains(e));
    let near_campfire = players.iter().next().is_some_and(|player| {
        campfires.iter().any(|&e| {
            named.get(e).is_ok_and(|(_, t)| {
                t.translation().distance(player.translation()) < TRAVEL_CAMPFIRE_RANGE_M
            })
        })
    });

    if keys.just_pressed(KeyCode::KeyG) && (near_campfire || state.open) {
        state.open = !state.open;
        state.selection = 0;
    }
    if state.open && !near_campfire {
        state.open = false;
    }
    // Espelho ao MenusOpen logo após os dois caminhos de fecho acima
    // (toggle [G] e saída por distância) — o [J] de confirmação fecha mais
    // abaixo e re-espelha outra vez.
    menus.travel = state.open;
    for mut visibility in q_menu.iter_mut() {
        let wanted = if state.open {
            Visibility::Visible
        } else {
            Visibility::Hidden
        };
        if *visibility != wanted {
            *visibility = wanted;
        }
    }

    let marked: Vec<&NotaLandmarkOwned> = catalog
        .0
        .iter()
        // Só marcos ASSINADOS e DISPONÍVEIS: um save antigo com marcas de um
        // bioma que passou a ter atalaia não lista destinos no escuro.
        .filter(|l| {
            nota.marked.contains(&l.name) && landmark_available(&catalog, &revealed, &l.name)
        })
        .collect();

    if !state.open {
        return;
    }
    if marked.is_empty() {
        for mut text in q_content.iter_mut() {
            let wanted = "Nenhum marco assinado ainda — [F] junto aos marcos.".into();
            if text.0 != wanted {
                text.0 = wanted;
            }
        }
        return;
    }
    if keys.just_pressed(KeyCode::ArrowDown) || keys.just_pressed(KeyCode::KeyS) {
        state.selection = (state.selection + 1) % marked.len();
    }
    if keys.just_pressed(KeyCode::ArrowUp) || keys.just_pressed(KeyCode::KeyW) {
        state.selection = (state.selection + marked.len() - 1) % marked.len();
    }

    // viajar: fade a preto 0.4 s → teleport no preto cheio → 0.4 s de volta
    if keys.just_pressed(KeyCode::KeyJ)
        && let Some(entry) = marked.get(state.selection)
    {
        let hero = players
            .iter()
            .next()
            .map_or(Vec3::ZERO, GlobalTransform::translation);
        let target = nearest_named(&named, &entry.name, hero);
        if let Some(pos) = target {
            let x = pos.x + 2.0;
            let z = pos.z + 2.0;
            // SUPERFÍCIE RENDERIZADA (paridade com os spawners/knockback):
            // o sample analítico flutua acima das cordas do mesh nas
            // cristas — chegava-se do fast-travel a "pairar".
            let y = terrain
                .as_ref()
                .map(|t| t.sample_mesh_surface(x, z))
                .unwrap_or(pos.y);
            fade.phase = TravelFadePhase::Out;
            fade.timer = TRAVEL_FADE_OUT;
            fade.target = Some(Vec3::new(x, y + 0.1, z));
            sfx.write(crate::ambient::SfxEvent {
                clip: crate::ambient::SfxClip::Travel,
                position: None,
            });
            toasts.write(ScriptToast(format!("A viajar para {}…", entry.label)));
            state.open = false;
            // Confirmação fecha o painel: re-espelha (senão o input
            // ficava roubado para sempre).
            menus.travel = false;
        }
    }

    let mut lines = vec![String::new()];
    for (i, entry) in marked.iter().enumerate() {
        let marker = if i == state.selection { ">" } else { " " };
        lines.push(format!("{marker} {}", entry.label));
    }
    lines.push(String::new());
    lines.push("[J] viajar · ↑↓ escolher · [G] sair".into());
    for mut text in q_content.iter_mut() {
        let wanted = lines.join("\n");
        if text.0 != wanted {
            text.0 = wanted;
        }
    }
}

// ── overlay + sistema do fade da viagem ─────────────────────────────────

/// Nó full-screen preto (uma vez em Startup); o alpha é conduzido por frame.
#[derive(Component)]
struct TravelFadeOverlay;

fn spawn_travel_fade_overlay(mut commands: Commands) {
    commands.spawn((
        Node {
            position_type: PositionType::Absolute,
            top: Val::Px(0.0),
            left: Val::Px(0.0),
            right: Val::Px(0.0),
            bottom: Val::Px(0.0),
            ..Default::default()
        },
        BackgroundColor(Color::srgba(0.0, 0.0, 0.0, 0.0)),
        Visibility::Hidden,
        Name::new("ui:travel-fade"),
        TravelFadeOverlay,
    ));
}

/// Conduz o fade da viagem: alpha do overlay, TELEPORT no preto cheio
/// (transição Out→In, exatamente uma vez) e poeira de aterragem. A máquina
/// em si é pura ([`travel_fade_step`]) — aqui é só ECS.
#[allow(clippy::type_complexity)]
fn travel_fade_system(
    time: Res<Time>,
    mut fade: ResMut<TravelFade>,
    mut overlay: Query<(&mut BackgroundColor, &mut Visibility), With<TravelFadeOverlay>>,
    mut heroes: Query<(Entity, &mut Transform, &mut Player), With<Player>>,
    mut commands: Commands,
) {
    if fade.phase == TravelFadePhase::Idle {
        return;
    }
    let (alpha, arrived) = travel_fade_step(&mut fade, time.delta_secs());
    if arrived && let Some(target) = fade.target {
        if let Ok((entity, mut transform, mut player)) = heroes.single_mut() {
            transform.translation = target;
            // Chegada limpa: sem a inércia do trajeto antigo e com a
            // tutela pós-teleporte (o marco fica longe — a coluna de
            // destino pode ainda estar a assar o collider).
            crate::player::settle_after_teleport(&mut commands, entity, Some(&mut *player));
        }
        {
            // Poeira de aterragem — visível quando o fade abre.
            crate::particles::spawn_burst(
                &mut commands,
                &crate::vitals::juice_spec("ground-dust", (0.3, 0.7), (0.3, 0.6), (1.0, 2.5), None),
                target + Vec3::Y * 0.15,
                14,
            );
        }
    }
    let Ok((mut bg, mut visibility)) = overlay.single_mut() else {
        return;
    };
    let wanted = if alpha > 0.0 {
        Visibility::Visible
    } else {
        Visibility::Hidden
    };
    if *visibility != wanted {
        *visibility = wanted;
    }
    bg.0.set_alpha(alpha);
}

// ── waypoint HUD ────────────────────────────────────────────────────────

fn spawn_waypoint_hud(mut commands: Commands) {
    commands
        .spawn((
            Node {
                position_type: PositionType::Absolute,
                top: Val::Px(64.0),
                left: Val::Px(0.0),
                right: Val::Px(0.0),
                justify_content: JustifyContent::Center,
                ..Default::default()
            },
            Visibility::Hidden,
            Name::new("ui:waypoint"),
            WaypointHud,
        ))
        .with_children(|wrap| {
            wrap.spawn((
                Text::new(""),
                TextColor(Color::srgb(0.98, 0.8, 0.5)),
                TextFont::from_font_size(14.0),
                WaypointText,
            ));
        });
}

#[derive(Component)]
struct WaypointHud;

#[derive(Component)]
struct WaypointText;

/// Rumo cardeal a partir de um delta (dx, dz), norte = -Z.
pub fn bearing_label(dx: f32, dz: f32) -> &'static str {
    let angle = dx.atan2(-dz).to_degrees();
    match angle {
        a if (22.5..67.5).contains(&a) => "NE",
        a if (67.5..112.5).contains(&a) => "E",
        a if (112.5..157.5).contains(&a) => "SE",
        a if (157.5..=180.0).contains(&a) || (-180.0..-157.5).contains(&a) => "S",
        a if (-157.5..-112.5).contains(&a) => "SO",
        a if (-112.5..-67.5).contains(&a) => "O",
        a if (-67.5..-22.5).contains(&a) => "NO",
        _ => "N",
    }
}

/// Linha do waypoint: rumo + distância ao último marco assinado.
fn waypoint_hud_system(
    players: Query<&GlobalTransform, With<Player>>,
    waypoint: Res<Waypoint>,
    mut hud: Query<&mut Visibility, With<WaypointHud>>,
    mut texts: Query<&mut Text, With<WaypointText>>,
) {
    let Some(label) = waypoint.label.as_deref() else {
        return;
    };
    let Some(position) = waypoint.position else {
        return;
    };
    let Some(player) = players.iter().next() else {
        return;
    };
    let delta = position - player.translation();
    // Distância lida no plano XZ: o rumo já é cardeal (2D) e a cota residual
    // do marco não devia inflar os metros mostrados.
    let distance = delta.xz().length();
    for mut visibility in hud.iter_mut() {
        *visibility = Visibility::Visible;
    }
    for mut text in texts.iter_mut() {
        let wanted = format!(
            "{} · {} · {} m",
            label,
            bearing_label(delta.x, delta.z),
            distance.round() as i32
        );
        if text.0 != wanted {
            text.0 = wanted;
        }
    }
}

// ── enemy registry ──────────────────────────────────────────────────────

/// Conta hostis vivos por banda (1 Hz) e espelha no ctx dos scripts.
fn enemy_registry_system(
    mut throttle: Local<f32>,
    time: Res<Time>,
    creatures: Query<(&GlobalTransform, &crate::vitals::Health), With<crate::luau::LuaScriptRef>>,
    mut registry: ResMut<EnemyRegistry>,
    host: ResMut<crate::luau::LuaScriptHost>,
) {
    *throttle -= time.delta_secs();
    if *throttle > 0.0 {
        return;
    }
    *throttle = 1.0;
    let positions: Vec<(f32, f32)> = creatures
        .iter()
        .filter(|(_, hp)| hp.current > 0.0)
        .map(|(t, _)| (t.translation().x, t.translation().z))
        .collect();
    registry.counts = EnemyRegistry::count_positions(&positions);
    if let Some(mut ctx) = host.lua.app_data_mut::<crate::luau::ScriptCtx>() {
        ctx.alive_regions = registry.counts;
    }
}

// ── QA: F11 teleport ao próximo marco por assinar ───────────────────────

#[allow(clippy::too_many_arguments)] // sistema Bevy com queries/params explícitos
fn quest_debug_landmark(
    keys: Res<ButtonInput<KeyCode>>,
    mut players: Query<(Entity, &GlobalTransform, &mut Transform, &mut Player), With<Player>>,
    named: Query<(&Name, &GlobalTransform)>,
    catalog: Res<LandmarkCatalog>,
    revealed: Res<RevealedBiomes>,
    nota: Res<NotaLog>,
    terrain: Option<Res<crate::terrain::runtime::TerrainRuntime>>,
    mut toasts: MessageWriter<ScriptToast>,
    mut commands: Commands,
) {
    if !keys.just_pressed(KeyCode::F11) {
        return;
    }
    let Ok((entity, player_global, mut transform, mut player)) = players.single_mut() else {
        return;
    };
    let player_pos = player_global.translation();
    let Some(target) = named
        .iter()
        .filter(|(name, _)| {
            landmark_by_name(&catalog, name)
                .filter(|l| !nota.marked.contains(&l.name))
                // QA segue o JOGO: marcos trancados não são alvo (senão o
                // F11 teleportava para um marco que o [F] recusa assinar).
                .filter(|l| landmark_available(&catalog, &revealed, &l.name))
                .is_some()
        })
        .min_by(|(_, a_t), (_, b_t)| {
            a_t.translation()
                .distance_squared(player_pos)
                .total_cmp(&b_t.translation().distance_squared(player_pos))
        })
        .map(|(name, t)| (name.to_string(), t.translation()))
    else {
        toasts.write(ScriptToast("QA: todos os marcos assinados".into()));
        return;
    };
    let x = target.1.x + 2.0;
    let z = target.1.z + 2.0;
    // Paridade de assentamento: superfície renderizada (ver teleport [J]).
    let y = terrain
        .as_ref()
        .map(|t| t.sample_mesh_surface(x, z))
        .unwrap_or(target.1.y);
    transform.translation = Vec3::new(x, y + 0.1, z);
    crate::player::settle_after_teleport(&mut commands, entity, Some(&mut *player));
    toasts.write(ScriptToast(format!("QA: teleport ao marco {}", target.0)));
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_campfire_name_is_case_insensitive_substring() {
        assert!(is_campfire_name("campfire"));
        assert!(is_campfire_name("Town-Campfire-01"));
        assert!(is_campfire_name("CAMPFIRE"));
        assert!(!is_campfire_name("camp"));
        assert!(!is_campfire_name("fire-pit"));
        assert!(!is_campfire_name(""));
    }

    #[test]
    fn test_catalog_12_landmarks_3_per_biome() {
        assert_eq!(LANDMARKS.len(), 12);
        for biome in [
            Biome::DarkForest,
            Biome::Desert,
            Biome::Swamp,
            Biome::FrozenPeaks,
        ] {
            let count = LANDMARKS.iter().filter(|l| l.biome == biome).count();
            assert_eq!(count, 3, "bioma {:?} com {count}", biome);
        }
        // nomes únicos
        let mut names: Vec<_> = LANDMARKS.iter().map(|l| l.name).collect();
        names.sort_unstable();
        names.dedup();
        assert_eq!(names.len(), 12);
    }

    #[test]
    fn test_survey_quests_exist_in_quest_log() {
        let log = crate::quests::QuestLog::with_dir(&crate::quests::example_quests_dir());
        for biome in [
            Biome::DarkForest,
            Biome::Desert,
            Biome::Swamp,
            Biome::FrozenPeaks,
        ] {
            assert!(
                log.def(biome.survey_quest()).is_some(),
                "quest de traçado {} em falta",
                biome.survey_quest()
            );
        }
    }

    /// Homónimos: ganha o mais próximo, não o primeiro spawnado.
    #[test]
    fn test_nearest_named_picks_the_closest_twin() {
        let mut world = World::new();
        world.spawn((
            Name::new("cairn"),
            GlobalTransform::from_translation(Vec3::new(500.0, 0.0, 0.0)),
        ));
        world.spawn((
            Name::new("cairn"),
            GlobalTransform::from_translation(Vec3::new(5.0, 0.0, 0.0)),
        ));
        world.spawn((Name::new("other"), GlobalTransform::IDENTITY));
        let mut state =
            bevy::ecs::system::SystemState::<Query<(&Name, &GlobalTransform)>>::new(&mut world);
        let named = state.get(&world).expect("query válida");
        assert_eq!(
            nearest_named(&named, "cairn", Vec3::ZERO),
            Some(Vec3::new(5.0, 0.0, 0.0))
        );
        assert_eq!(nearest_named(&named, "missing", Vec3::ZERO), None);
    }

    #[test]
    fn test_region_of_points() {
        assert_eq!(region_of(0.0, 0.0), 0); // centro
        assert_eq!(region_of(0.0, 300.0), 1); // norte
        assert_eq!(region_of(0.0, -300.0), 2); // sul
        assert_eq!(region_of(500.0, 0.0), 3); // este
        assert_eq!(region_of(-500.0, 0.0), 4); // oeste
    }

    #[test]
    fn test_registry_counts_positions() {
        let counts = EnemyRegistry::count_positions(&[
            (0.0, 0.0),
            (0.0, 300.0),
            (0.0, 300.0),
            (-500.0, 0.0),
        ]);
        assert_eq!(counts, [1, 2, 0, 0, 1]);
    }

    #[test]
    fn test_bearing_labels() {
        // norte = -Z
        assert_eq!(bearing_label(0.0, -10.0), "N");
        assert_eq!(bearing_label(10.0, 0.0), "E");
        assert_eq!(bearing_label(0.0, 10.0), "S");
        assert_eq!(bearing_label(-10.0, 0.0), "O");
        assert_eq!(bearing_label(10.0, -10.0), "NE");
    }

    #[test]
    fn test_landmark_lookup_and_radius() {
        // Fase B2: o lookup puro recebe o catálogo — o fallback (default) tem
        // de comportar-se como o const de sempre.
        let catalog = LandmarkCatalog::default();
        let l = landmark_by_name(&catalog, "desert-arch").expect("marco");
        assert_eq!(l.label, "Arco do Deserto");
        assert_eq!(l.biome_id, "desert");
        assert_eq!(l.biome(), Some(Biome::Desert));
        assert_eq!(l.mark_radius, 12.0);
        assert_eq!(l.survey_quest, "desert_survey");
        assert!(landmark_by_name(&catalog, "not-a-landmark").is_none());
    }

    #[test]
    fn test_fallback_catalog_mirrors_const() {
        // O fallback (mundo sem <Landmark>) tem de ser EXATAMENTE o const:
        // 12 marcos, 3 por bioma, nomes únicos, raio/quest por bioma.
        let catalog = LandmarkCatalog::default();
        assert_eq!(catalog.0.len(), 12);
        for biome in [
            Biome::DarkForest,
            Biome::Desert,
            Biome::Swamp,
            Biome::FrozenPeaks,
        ] {
            let count = catalog
                .0
                .iter()
                .filter(|l| l.biome_id == biome.id())
                .count();
            assert_eq!(count, 3, "bioma {} com {count}", biome.id());
        }
        let mut names: Vec<_> = catalog.0.iter().map(|l| l.name.clone()).collect();
        names.sort();
        names.dedup();
        assert_eq!(names.len(), 12);
        // O fallback cobre o const um-a-um (ordem incluída).
        for (owned, constant) in catalog.0.iter().zip(LANDMARKS.iter()) {
            assert_eq!(owned.name, constant.name);
            assert_eq!(owned.label, constant.label);
            assert_eq!(owned.biome(), Some(constant.biome));
            assert_eq!(owned.mark_radius, constant.biome.mark_radius());
            assert_eq!(owned.survey_quest, constant.biome.survey_quest());
        }
    }

    #[test]
    fn test_remaining_in_biome_counts_unmarked() {
        let catalog = LandmarkCatalog::default();
        let mut marked = HashSet::new();
        assert_eq!(remaining_in_biome(&catalog, &marked, "swamp"), 3);
        for name in [
            "swamp-wrecked-boat",
            "swamp-sunken-graves",
            "swamp-bone-altar",
        ] {
            marked.insert(name.to_string());
        }
        assert_eq!(remaining_in_biome(&catalog, &marked, "swamp"), 0);
        // Biomas não afetados pelos swamp marcados.
        assert_eq!(remaining_in_biome(&catalog, &marked, "desert"), 3);
    }

    #[test]
    fn test_biome_from_id_roundtrip() {
        for biome in [
            Biome::DarkForest,
            Biome::Desert,
            Biome::Swamp,
            Biome::FrozenPeaks,
        ] {
            assert_eq!(Biome::from_id(biome.id()), Some(biome));
        }
        assert_eq!(Biome::from_id("vale"), None);
        assert_eq!(Biome::from_id(""), None);
    }

    #[test]
    fn test_unknown_biome_keeps_raw_id_label() {
        // Marco declarado com bioma desconhecido: fica no catálogo, o toast
        // mostra o id cru em vez de partir o parse.
        let l = NotaLandmarkOwned {
            name: "mystery-spire".into(),
            biome_id: "vale".into(),
            label: "Aguja Misteriosa".into(),
            survey_quest: String::new(),
            mark_radius: 8.0,
            tower_reveals: None,
        };
        assert_eq!(l.biome(), None);
        assert_eq!(l.biome_label(), "vale");
    }

    #[test]
    fn test_tower_biome_roundtrip_and_label() {
        assert_eq!(Biome::from_id("tower"), Some(Biome::Tower));
        assert_eq!(Biome::Tower.id(), "tower");
        assert_eq!(Biome::Tower.label(), "Atalaias");
        // Atalaias não têm quest de traçado — a recompensa é a revelação.
        assert_eq!(Biome::Tower.survey_quest(), "");
        assert!((Biome::Tower.mark_radius() - 8.0).abs() < 1e-4);
    }

    fn tower_catalog() -> LandmarkCatalog {
        LandmarkCatalog(vec![
            NotaLandmarkOwned {
                name: "atalaia-do-bosque".into(),
                biome_id: "tower".into(),
                label: "Atalaia do Bosque".into(),
                survey_quest: String::new(),
                mark_radius: 8.0,
                tower_reveals: Some("dark_forest".into()),
            },
            NotaLandmarkOwned {
                name: "forest-outpost-tower".into(),
                biome_id: "dark_forest".into(),
                label: "Torre do Posto Avançado".into(),
                survey_quest: "forest_survey".into(),
                mark_radius: 10.0,
                tower_reveals: None,
            },
        ])
    }

    #[test]
    fn test_landmark_available_gates_by_tower_reveal() {
        let catalog = tower_catalog();
        let mut revealed = RevealedBiomes::default();
        // Atalaia: SEMPRE disponível (é a chave, não a porta).
        assert!(landmark_available(&catalog, &revealed, "atalaia-do-bosque"));
        // Marco do bioma trancaado: indisponível até revelar.
        assert!(!landmark_available(
            &catalog,
            &revealed,
            "forest-outpost-tower"
        ));
        revealed.0.insert("dark_forest".into());
        assert!(landmark_available(
            &catalog,
            &revealed,
            "forest-outpost-tower"
        ));
        // Nome fora do catálogo: nunca disponível.
        assert!(!landmark_available(&catalog, &revealed, "nada"));
    }

    #[test]
    fn test_world_without_towers_never_locks() {
        // Back-compat: o fallback (mundo sem atalaias) não tranca NENHUM
        // bioma — o comportamento de antes fica intacto.
        let catalog = LandmarkCatalog::default();
        let revealed = RevealedBiomes::default();
        assert!(RevealedBiomes::keyed_biomes(&catalog).is_empty());
        for landmark in &catalog.0 {
            assert!(landmark_available(&catalog, &revealed, &landmark.name));
        }
    }

    #[test]
    fn test_collect_landmark_blips_marks_towers_and_filters_locked() {
        let catalog = tower_catalog();
        let revealed = RevealedBiomes::default();
        let named = vec![
            (
                "atalaia-do-bosque".to_string(),
                Vec3::new(30.0, 50.0, 138.0),
            ),
            (
                "forest-outpost-tower".to_string(),
                Vec3::new(-92.0, 44.0, 176.0),
            ),
        ];
        let blips = collect_landmark_blips(&catalog, &revealed, &named);
        assert_eq!(blips.len(), 1, "marco trancado não vira blip");
        assert!(blips[0].tower);
        assert_eq!(blips[0].name, "atalaia-do-bosque");
        // Marco sem entidade no mundo: sem blip (o nome é que tem posição).
        let ghost = LandmarkCatalog(vec![NotaLandmarkOwned {
            name: "fantasma".into(),
            biome_id: "tower".into(),
            label: "Fantasma".into(),
            survey_quest: String::new(),
            mark_radius: 8.0,
            tower_reveals: None,
        }]);
        assert!(collect_landmark_blips(&ghost, &revealed, &named).is_empty());
    }

    #[test]
    fn test_travel_fade_phases_and_midpoint_teleport() {
        // Tempos contratados: 0.4 s out / 0.4 s in.
        assert_eq!(TRAVEL_FADE_OUT, 0.4);
        assert_eq!(TRAVEL_FADE_IN, 0.4);
        let mut fade = TravelFade {
            phase: TravelFadePhase::Out,
            timer: TRAVEL_FADE_OUT,
            target: Some(Vec3::new(5.0, 2.0, -7.0)),
        };
        // A meio do fade-out: a escurecer (alpha 0.5), ainda SEM teleport.
        let (alpha, arrived) = travel_fade_step(&mut fade, TRAVEL_FADE_OUT * 0.5);
        assert!((alpha - 0.5).abs() < 1e-4, "alpha={alpha}");
        assert!(!arrived);
        assert_eq!(fade.phase, TravelFadePhase::Out);
        // Fim do fade-out: preto cheio, teleport EXATAMENTE uma vez.
        let (alpha, arrived) = travel_fade_step(&mut fade, TRAVEL_FADE_OUT);
        assert!((alpha - 1.0).abs() < 1e-4, "preto cheio: {alpha}");
        assert!(arrived, "teleport no meio do fade");
        assert_eq!(fade.phase, TravelFadePhase::In);
        assert_eq!(fade.timer, TRAVEL_FADE_IN);
        // A meio do fade-in: a clarear, sem NOVO teleport.
        let (alpha, arrived) = travel_fade_step(&mut fade, TRAVEL_FADE_IN * 0.5);
        assert!((alpha - 0.5).abs() < 1e-4);
        assert!(!arrived, "teleport só no meio do fade, uma vez");
        assert_eq!(fade.phase, TravelFadePhase::In);
        // Fim: idle e alpha zero.
        let (alpha, arrived) = travel_fade_step(&mut fade, TRAVEL_FADE_IN);
        assert_eq!(alpha, 0.0);
        assert!(!arrived);
        assert_eq!(fade.phase, TravelFadePhase::Idle);
        assert_eq!(fade.target, None);
        // Idle: nada acontece (nem teleport fantasma).
        let (alpha, arrived) = travel_fade_step(&mut fade, 1.0);
        assert_eq!(alpha, 0.0);
        assert!(!arrived);
    }
}
