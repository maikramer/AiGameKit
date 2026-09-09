//! Save/load & opções (loop 7 do port simple-rpg) — o análogo nativo do
//! `SaveLoadPlugin` (localStorage + msgpackr) do VibeGame:
//!
//! - **[`SaveGame`]**: XP, posição, vault (recursos + itens), estados das
//!   quests e marcos da Nota — serializado em JSON.
//! - **Disco**: `~/.local/share/viber/<world>.save.json` (fallback
//!   `./<world>.save.json` sem HOME).
//! - **UI**: no modal [Q], tab **Opções** — ↑↓ escolhe a linha (volumes
//!   master/música/sfx e Save/Load), ←/→ ajusta o volume ±10 %, [J] grava
//!   e [L] carrega.
//!
//! Os `defs` das quests são estáticos (embutidos) — só os ESTADOS viajam no
//! ficheiro, como os serializers por módulo do VibeGame.

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use bevy::prelude::*;
use serde::{Deserialize, Serialize};

use crate::economy::Vault;
use crate::luau::ScriptToast;
use crate::menus::MenusOpen;
use crate::music::AudioMixerSettings;
use crate::player::Player;
use crate::quests::QuestLog;
use crate::skills::{LevelProgress, PlayerStatsResource, SkillTree};
use crate::travel::NotaLog;
use crate::vitals::{Health, Xp};

/// Pasta do world.xml, capturada no startup — o save é prefixado por ela.
/// NÃO usar `PendingTerrain` para isto: o bootstrap do terreno remove-o no
/// Startup e a leitura em runtime devolvia sempre None (save global outra
/// vez). Inserido por `recipes::spawn::startup`.
#[derive(Debug, Clone, Resource, Default)]
pub struct WorldBaseDir(pub Option<std::path::PathBuf>);

/// Diretório de saves do `config.yaml` do jogo (`save.dir`); `None` = o
/// histórico `~/.local/share/viber` (apps mínimas de teste).
#[derive(Debug, Clone, Resource, Default)]
pub struct SaveDir(pub Option<PathBuf>);

/// Nome do ficheiro de save fallback (quando o mundo é desconhecido).
pub const SAVE_FILENAME: &str = "simple-rpg.save.json";

/// Caminho do save: `save.dir` do config do jogo (fallback histórico
/// `~/.local/share/viber` via [`dirs::home_dir`] — semântica `$HOME` exata,
/// NÃO `XDG_DATA_HOME`, os saves históricos vivem aí; último fallback: cwd).
pub fn save_path() -> PathBuf {
    save_path_for(None, None)
}

/// Caminho do save prefixado pelo mundo (`base_dir` = pasta do world.xml):
/// gravar no mundo A e carregar no B nunca mais restaura o estado errado.
/// Sem base_dir conhecida, cai no nome global histórico.
pub fn save_path_for(base_dir: Option<&std::path::Path>, save_dir: Option<&Path>) -> PathBuf {
    let name = base_dir
        .and_then(|dir| dir.file_name())
        .map(|world| format!("{}.save.json", world.to_string_lossy()))
        .unwrap_or_else(|| SAVE_FILENAME.to_string());
    let dir = match save_dir.map(Path::to_path_buf) {
        Some(dir) => {
            let _ = std::fs::create_dir_all(&dir);
            dir
        }
        None => dirs::home_dir()
            .map(|home| {
                let dir = home.join(".local/share/viber");
                let _ = std::fs::create_dir_all(&dir);
                dir
            })
            .unwrap_or_default(),
    };
    if dir.as_os_str().is_empty() {
        PathBuf::from(name)
    } else {
        dir.join(name)
    }
}

// ── estrutura do save ───────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct SaveGame {
    // Campos nucleares com `#[serde(default)]`: um save antigo/parcial sem
    // `volumes`/`nota_marked`/`items`/… carrega em vez de ser recusado inteiro.
    #[serde(default)]
    pub xp: (u32, u32),
    #[serde(default)]
    pub health: (f32, f32),
    #[serde(default)]
    pub position: [f32; 3],
    #[serde(default)]
    pub gold: u32,
    #[serde(default)]
    pub wood: u32,
    #[serde(default)]
    pub stone: u32,
    #[serde(default)]
    pub items: HashMap<String, u32>,
    /// Estados das quests ativas: id → progresso + marcos visitados.
    #[serde(default)]
    pub quest_states: HashMap<String, (u32, Vec<String>)>,
    #[serde(default)]
    pub quest_done: Vec<String>,
    /// Marcos da Nota assinados.
    #[serde(default)]
    pub nota_marked: Vec<String>,
    /// Skills aprendidas (ids) + pontos disponíveis + nível — sem isto,
    /// carregar apagava a árvore comprada.
    #[serde(default)]
    pub skill_learned: Vec<String>,
    #[serde(default)]
    pub skill_points: u32,
    #[serde(default)]
    pub level: u32,
    /// Volumes (master, music, sfx) 0..=1.
    #[serde(default)]
    pub volumes: (f32, f32, f32),
}

/// Captura o estado do jogo (puro sobre os recursos; xp/posição vêm de fora).
#[allow(clippy::too_many_arguments)]
pub fn capture(
    vault: &Vault,
    quests: &QuestLog,
    nota: &NotaLog,
    tree: &SkillTree,
    level: u32,
    xp: (u32, u32),
    health: (f32, f32),
    position: [f32; 3],
    volumes: (f32, f32, f32),
) -> SaveGame {
    SaveGame {
        xp,
        health,
        position,
        gold: vault.gold,
        wood: vault.wood,
        stone: vault.stone,
        items: vault.items.clone(),
        quest_states: quests
            .states
            .iter()
            .map(|(id, a)| (id.clone(), (a.progress, a.visited.clone())))
            .collect(),
        quest_done: quests.done.clone(),
        nota_marked: nota.marked.iter().cloned().collect(),
        skill_learned: tree.learned.clone(),
        skill_points: tree.points,
        level,
        volumes,
    }
}

/// Escreve o save em disco (JSON pretty) ATOMICAMENTE: escreve num `.tmp` NO
/// MESMO diretório, sincroniza e só então renomea — o rename é atómico no
/// mesmo filesystem, portanto um ENOSPC/kill -9/OOM da GPU a meio nunca
/// destrói o save anterior (é o único save, sem backup).
pub fn save_to_disk(path: &std::path::Path, game: &SaveGame) -> Result<(), String> {
    use std::io::Write;
    let json = serde_json::to_string_pretty(game).map_err(|e| e.to_string())?;
    let tmp = path.with_extension("json.tmp");
    let write_tmp = || -> Result<(), String> {
        let mut f = std::fs::File::create(&tmp).map_err(|e| format!("{}: {e}", tmp.display()))?;
        f.write_all(json.as_bytes())
            .and_then(|()| f.sync_all())
            .map_err(|e| format!("{}: {e}", tmp.display()))
    };
    if let Err(e) = write_tmp() {
        let _ = std::fs::remove_file(&tmp); // não deixar .tmp órfão
        return Err(e);
    }
    std::fs::rename(&tmp, path).map_err(|e| {
        let _ = std::fs::remove_file(&tmp);
        format!("{}: {e}", path.display())
    })
}

/// Lê o save do disco.
pub fn load_from_disk(path: &std::path::Path) -> Result<SaveGame, String> {
    let json = std::fs::read_to_string(path).map_err(|e| format!("{}: {e}", path.display()))?;
    serde_json::from_str(&json).map_err(|e| e.to_string())
}

// ── plugin ──────────────────────────────────────────────────────────────

pub struct SavePlugin;

impl Plugin for SavePlugin {
    fn build(&self, app: &mut App) {
        app.init_resource::<OptionsRows>()
            .init_resource::<WorldBaseDir>()
            .init_resource::<LevelProgress>()
            .add_systems(Update, options_system);
    }
}

/// Linha selecionada na tab Opções (0=master, 1=música, 2=sfx, 3=save,
/// 4=load).
#[derive(Debug, Clone, Resource, Default)]
pub struct OptionsRows {
    pub selected: usize,
}

pub const OPTIONS_ROWS: usize = 5;

/// Aplica um save carregado aos recursos (puro; usado pelo sistema e testes).
/// Skills e bónus vivos viajam no save: sem a parte do `tree`/`stats`, as
/// passivas compradas desapareciam ao carregar.
pub fn apply_save(
    game: &SaveGame,
    vault: &mut Vault,
    quests: &mut QuestLog,
    nota: &mut NotaLog,
    mixer: &mut AudioMixerSettings,
    tree: &mut SkillTree,
    stats: &mut PlayerStatsResource,
) {
    vault.gold = game.gold;
    vault.wood = game.wood;
    vault.stone = game.stone;
    // Item de save editado (ex.: 4 000 000 000) não entra sem o cap de stack
    // — rebentava a aritmética a jusante. Ids normalizados como em TODA a
    // escrita do vault (`item_add`): um save com "Potion" ficava visível no
    // inventário mas invisível para hotbar/loja/quests (lookups normalizam).
    vault.items = game
        .items
        .iter()
        .map(|(id, &n)| (crate::economy::normalize_item(id), n.min(99)))
        .collect();
    quests.states.clear();
    for (id, (progress, visited)) in &game.quest_states {
        quests.states.insert(
            id.clone(),
            crate::quests::ActiveQuest {
                progress: *progress,
                visited: visited.clone(),
            },
        );
    }
    quests.done = game.quest_done.clone();
    nota.marked = game.nota_marked.iter().cloned().collect();
    tree.learned = game.skill_learned.clone();
    tree.points = game.skill_points;
    stats.0 = crate::skills::stats_from_learned(&tree.learned);
    // JSON editado/corrompido não pode pochar o mixer nem os vitals.
    mixer.master = game.volumes.0.clamp(0.0, 1.0);
    mixer.music = game.volumes.1.clamp(0.0, 1.0);
    mixer.sfx = game.volumes.2.clamp(0.0, 1.0);
}

/// `XpLevel` que o load deve aplicar ao herói (R2-G4): nível e `last_next`
/// vêm do save, para o `level_up_detector` (que compara `xp.next` ao
/// `last_next`) ler o load como "já visto" em vez de fanfarrar um level-up
/// espúrio com o nível errado. Puro para testes.
pub fn saved_xp_level(game: &SaveGame) -> crate::vitals::XpLevel {
    crate::vitals::XpLevel {
        level: game.level,
        last_next: game.xp.1,
    }
}

/// Opções e gravação: linhas ↑↓, volumes ←→, [J] grava, [L] carrega — e os
/// mesmos dois efeitos disparados pelos botões do menu declarativo
/// (`viber.ui.action("save"|"load", "")`).
///
/// Sem throttle a montante do `just_pressed`: a tecla só é verdadeira 1
/// frame e o throttle (0,15 s) descartava ~90 % das pressões de [J]/[L].
#[allow(clippy::type_complexity, clippy::too_many_arguments)]
fn options_system(
    keys: Res<ButtonInput<KeyCode>>,
    open: Res<MenusOpen>,
    ui_tabs: Option<Res<crate::ui::UiTabs>>,
    mut ui_actions: MessageReader<crate::ui::actions::UiAction>,
    mut rows: ResMut<OptionsRows>,
    mut mixer: ResMut<AudioMixerSettings>,
    mut vault: ResMut<Vault>,
    mut quests: ResMut<QuestLog>,
    mut nota: ResMut<NotaLog>,
    mut tree: ResMut<SkillTree>,
    mut stats: ResMut<PlayerStatsResource>,
    mut progress: ResMut<LevelProgress>,
    // Um único param (tuplo) — o sistema já usa os 16 slots do Bevy.
    world_and_save: (Option<Res<WorldBaseDir>>, Option<Res<SaveDir>>),
    mut heroes: Query<
        (
            &mut Health,
            &mut Xp,
            &mut Transform,
            &mut Player,
            Option<&mut crate::skills::LevelState>,
            Option<&mut crate::vitals::XpLevel>,
        ),
        With<Player>,
    >,
    mut toasts: MessageWriter<ScriptToast>,
    mut sfx: MessageWriter<crate::ambient::SfxEvent>,
) {
    // Os botões do menu funcionam sempre que o menu está aberto; as teclas
    // continuam a exigir o mesmo.
    let mut save_requested = false;
    let mut load_requested = false;
    for action in ui_actions.read() {
        match action.name.as_str() {
            "save" => save_requested = true,
            "load" => load_requested = true,
            _ => {}
        }
    }
    if !open.modal && !save_requested && !load_requested {
        return;
    }
    // Contrato: as TECLAS (↑↓ linhas, ←→ volumes, [J]/[L] save/load) só
    // valem na tab Sistema do modal — noutras tabs as setas/[J]/[L]
    // pertencem ao modal (na tab Talentos, ↑↓ mudava as linhas de opções E
    // a navegação em simultâneo). Os BOTÕES do menu declarativo (UiAction)
    // valem sempre — são cliques da própria tab Sistema.
    let on_system_tab = ui_tabs.as_deref().and_then(|tabs| tabs.selected("menu")) == Some("system");
    if on_system_tab {
        if keys.just_pressed(KeyCode::ArrowDown) {
            rows.selected = (rows.selected + 1) % OPTIONS_ROWS;
        }
        if keys.just_pressed(KeyCode::ArrowUp) {
            rows.selected = (rows.selected + OPTIONS_ROWS - 1) % OPTIONS_ROWS;
        }
        let left = keys.just_pressed(KeyCode::ArrowLeft);
        let right = keys.just_pressed(KeyCode::ArrowRight);
        if (left || right) && rows.selected < 3 {
            let delta = if right { 0.1 } else { -0.1 };
            match rows.selected {
                0 => mixer.master = (mixer.master + delta).clamp(0.0, 1.0),
                1 => mixer.music = (mixer.music + delta).clamp(0.0, 1.0),
                _ => mixer.sfx = (mixer.sfx + delta).clamp(0.0, 1.0),
            }
        }
    }
    let base_dir = world_and_save.0.as_deref().and_then(|w| w.0.as_deref());
    let save_dir = world_and_save.1.as_deref().and_then(|d| d.0.as_deref());
    if (on_system_tab && keys.just_pressed(KeyCode::KeyJ)) || save_requested {
        // Sem herói (ausente/Disabled) não há estado real para gravar — cair
        // nos defaults ((100,100),(0,100), origem) SOBRESCREVIA um save bom.
        // Trata como falha: toast + SFX de erro, sem tocar no disco.
        let hero_state = heroes
            .single_mut()
            .ok()
            .map(|(hp, xp, t, _player, level, _xp_level)| {
                (
                    (hp.current, hp.max),
                    (xp.current, xp.next),
                    [t.translation.x, t.translation.y, t.translation.z],
                    level.as_ref().map(|l| l.level).unwrap_or(0),
                )
            });
        if let Some((health, xp, position, level)) = hero_state {
            let game = capture(
                &vault,
                &quests,
                &nota,
                &tree,
                level,
                xp,
                health,
                position,
                (mixer.master, mixer.music, mixer.sfx),
            );
            if let Err(e) = save_to_disk(
                    &save_path_for(base_dir, save_dir),
                    &game,
                ) {
                toasts.write(ScriptToast(format!("Falha ao gravar: {e}")));
                sfx.write(crate::ambient::SfxEvent {
                    clip: crate::ambient::SfxClip::Error,
                    position: None,
                });
            } else {
                toasts.write(ScriptToast("Jogo gravado.".into()));
                sfx.write(crate::ambient::SfxEvent {
                    clip: crate::ambient::SfxClip::Save,
                    position: None,
                });
            }
        } else {
            toasts.write(ScriptToast("Falha ao gravar: sem herói para gravar".into()));
            sfx.write(crate::ambient::SfxEvent {
                clip: crate::ambient::SfxClip::Error,
                position: None,
            });
        }
    }
    if (on_system_tab && keys.just_pressed(KeyCode::KeyL)) || load_requested {
        match load_from_disk(&save_path_for(base_dir, save_dir)) {
            Ok(game) => {
                // Stats PRÉVIO capturado ANTES de apply_save o substituir —
                // o delta tem de ser sessão→save; capturar depois (quando
                // stats.0 já é o do save) multiplicava a speed de novo a
                // cada load.
                let previous = stats.0;
                apply_save(
                    &game,
                    &mut vault,
                    &mut quests,
                    &mut nota,
                    &mut mixer,
                    &mut tree,
                    &mut stats,
                );
                if let Ok((mut hp, mut xp, mut transform, mut player, level, xp_level)) =
                    heroes.single_mut()
                {
                    // Passivas do save aplicadas ao herói (HP máx + speed)
                    // antes de o estado guardado sobrepor tudo.
                    crate::skills::apply_passive_delta(&mut hp, &mut player, &previous, &stats.0);
                    hp.max = game.health.1.max(1.0);
                    hp.current = game.health.0.clamp(0.0, hp.max);
                    xp.current = game.xp.0;
                    xp.next = game.xp.1;
                    // Posição não finita (save editado com `1e40`, etc.) não
                    // entra na transform — virava NaN em cascata no Rapier;
                    // o herói fica onde está.
                    if game.position.iter().all(|v| v.is_finite()) {
                        transform.translation = game.position.into();
                    }
                    if let Some(mut level) = level {
                        level.level = game.level;
                    }
                    // R2-G4: sincronizar o XpLevel com o save — o load muda
                    // `Xp` (Changed<Xp>) e o level_up_detector compara
                    // `xp.next` ao `last_next` visto; dessincronizado,
                    // fanfarra um level-up ESPÚRIO com o nível errado no
                    // load. Escrita DIRETA (não Commands): visível ao
                    // detector no mesmo frame. Sem XpLevel (praticamente
                    // impossível — existe desde o 1.º sighting do herói),
                    // o detector insere baseline silencioso com o next do
                    // save: também não fanfarra.
                    if let Some(mut xp_level) = xp_level {
                        *xp_level = saved_xp_level(&game);
                    }
                }
                // Carregar um save com xp.next maior não pode creditar
                // pontos de nível grátis no level_system.
                progress.previous_next = Some(game.xp.1);
                toasts.write(ScriptToast("Jogo carregado.".into()));
                sfx.write(crate::ambient::SfxEvent {
                    clip: crate::ambient::SfxClip::Load,
                    position: None,
                });
            }
            Err(e) => {
                toasts.write(ScriptToast(format!("Falha ao carregar: {e}")));
                sfx.write(crate::ambient::SfxEvent {
                    clip: crate::ambient::SfxClip::Error,
                    position: None,
                });
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// R2-G4: o XpLevel sincronizado do save mantém o level_up_detector
    /// silencioso — `xp.next` (aplicado pelo load) == `last_next` (do save).
    #[test]
    fn test_saved_xp_level_keeps_detector_silent() {
        let game = SaveGame {
            level: 4,
            xp: (12, 338),
            ..Default::default()
        };
        let synced = saved_xp_level(&game);
        assert_eq!(synced.level, 4);
        assert_eq!(synced.last_next, 338);
        // Detector a seguir ao load: next (338) == last_next (338) → sem
        // cadeia a contar, sem fanfarra.
        assert_eq!(crate::vitals::levels_between(synced.last_next, 338), 0);
        // O estado dessincronizado que fanfarrava: last_next velho (baseline
        // 100) contra o next do save contava 3 níveis inexistentes.
        assert_eq!(crate::vitals::levels_between(100, 338), 3);
    }

    #[test]
    fn test_save_roundtrip_preserves_state() {
        let mut vault = Vault::default();
        vault.add_resource("gold", 80);
        vault.add_resource("wood", 6);
        vault.item_add("potion", 2);
        let mut quests = QuestLog::default();
        quests.accept("city_wolves");
        quests.report_kill("wolf");
        quests.report_kill("wolf");
        let mut nota = NotaLog::default();
        nota.marked.insert("peaks-cairn-1".into());
        let mut tree = SkillTree::default();
        tree.points = 3;
        tree.learned.push("vitality1".into());
        let game = capture(
            &vault,
            &quests,
            &nota,
            &tree,
            2,
            (30, 150),
            (77.5, 100.0),
            [12.0, 25.0, -8.0],
            (0.8, 0.5, 0.9),
        );
        assert_eq!(game.gold, 80);
        assert_eq!(game.quest_states.get("city_wolves").unwrap().0, 2);
        assert_eq!(game.skill_points, 3);
        assert_eq!(game.skill_learned, vec!["vitality1".to_string()]);

        // roundtrip por JSON
        let json = serde_json::to_string(&game).unwrap();
        let loaded: SaveGame = serde_json::from_str(&json).unwrap();
        let mut vault2 = Vault::default();
        let mut quests2 = QuestLog::default();
        let mut nota2 = NotaLog::default();
        let mut mixer = AudioMixerSettings::default();
        let mut tree2 = SkillTree::default();
        let mut stats2 = PlayerStatsResource::default();
        apply_save(
            &loaded,
            &mut vault2,
            &mut quests2,
            &mut nota2,
            &mut mixer,
            &mut tree2,
            &mut stats2,
        );
        assert_eq!(vault2.gold, 80);
        assert_eq!(vault2.wood, 6);
        assert_eq!(vault2.item_count("potion"), 2);
        assert_eq!(
            quests2.status("city_wolves", Some(&vault2)),
            crate::quests::QuestStatus::Active
        );
        assert_eq!(nota2.marked.len(), 1);
        assert!((mixer.master - 0.8).abs() < 1e-6);
        // Skills restauradas + bónus vivos recalculados.
        assert_eq!(tree2.points, 3);
        assert!(tree2.learned.contains(&"vitality1".to_string()));
        assert!(stats2.0.max_hp_bonus > 0.0);
        // XP/posição ficam por conta do chamador (teste do JSON):
        assert_eq!(loaded.xp, (30, 150));
        assert_eq!(loaded.position, [12.0, 25.0, -8.0]);
    }

    /// Um save antigo (sem campos de skills) carrega sem falhar.
    #[test]
    fn test_old_save_without_skills_loads() {
        let json = r#"{ "xp": [10, 100], "health": [100.0, 100.0],
            "position": [0.0, 0.0, 0.0], "gold": 5, "wood": 0, "stone": 0,
            "items": {}, "quest_states": {}, "quest_done": [],
            "nota_marked": [], "volumes": [1.0, 1.0, 1.0] }"#;
        let loaded: SaveGame = serde_json::from_str(json).unwrap();
        assert!(loaded.skill_learned.is_empty());
        assert_eq!(loaded.skill_points, 0);
    }

    #[test]
    fn test_save_to_disk_and_back() {
        let dir = std::env::temp_dir().join(format!("viber-save-test-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("s.json");
        let game = SaveGame {
            gold: 42,
            ..Default::default()
        };
        save_to_disk(&path, &game).unwrap();
        let loaded = load_from_disk(&path).unwrap();
        assert_eq!(loaded.gold, 42);
        assert!(!dir.join("s.json.tmp").exists(), "sem .tmp órfão");
        std::fs::remove_file(&path).ok();
    }

    /// Escrita atómica: quando a escrita do `.tmp` falha (aqui, um diretório
    /// no lugar do tmp), o rename não acontece e o save ANTERIOR fica intacto.
    #[test]
    fn test_save_to_disk_failure_preserves_previous_save() {
        let dir = std::env::temp_dir().join(format!("viber-save-atomic-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("s.json");
        let game = SaveGame {
            gold: 42,
            ..Default::default()
        };
        save_to_disk(&path, &game).unwrap();
        // Sabotar o caminho do tmp com um diretório: File::create falha
        // ANTES do rename.
        let tmp = dir.join("s.json.tmp");
        std::fs::create_dir_all(&tmp).unwrap();
        let bad = SaveGame {
            gold: 999,
            ..Default::default()
        };
        assert!(save_to_disk(&path, &bad).is_err());
        assert_eq!(
            load_from_disk(&path).unwrap().gold,
            42,
            "save anterior sobrevive à falha"
        );
        std::fs::remove_file(&path).ok();
        std::fs::remove_dir(&tmp).ok();
    }
}
