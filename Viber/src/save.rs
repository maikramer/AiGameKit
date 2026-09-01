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
use std::path::PathBuf;

use bevy::prelude::*;
use serde::{Deserialize, Serialize};

use crate::economy::Vault;
use crate::luau::ScriptToast;
use crate::menus::{MenusOpen, ModalTab, TAB_COUNT};
use crate::music::AudioMixerSettings;
use crate::player::Player;
use crate::quests::QuestLog;
use crate::travel::NotaLog;
use crate::vitals::{Health, Xp};

/// Nome do ficheiro de save (prefixado pelo mundo, quando conhecido).
pub const SAVE_FILENAME: &str = "simple-rpg.save.json";

/// Caminho do save: `$HOME/.local/share/viber/<nome>` (fallback: cwd).
pub fn save_path() -> PathBuf {
    let name = SAVE_FILENAME;
    std::env::var_os("HOME")
        .map(|home| {
            let dir = PathBuf::from(home).join(".local/share/viber");
            let _ = std::fs::create_dir_all(&dir);
            dir.join(name)
        })
        .unwrap_or_else(|| PathBuf::from(name))
}

// ── estrutura do save ───────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct SaveGame {
    pub xp: (u32, u32),
    pub health: (f32, f32),
    pub position: [f32; 3],
    pub gold: u32,
    pub wood: u32,
    pub stone: u32,
    pub items: HashMap<String, u32>,
    /// Estados das quests ativas: id → progresso + marcos visitados.
    pub quest_states: HashMap<String, (u32, Vec<String>)>,
    pub quest_done: Vec<String>,
    /// Marcos da Nota assinados.
    pub nota_marked: Vec<String>,
    /// Volumes (master, music, sfx) 0..=1.
    pub volumes: (f32, f32, f32),
}

/// Captura o estado do jogo (puro sobre os recursos; xp/posição vêm de fora).
pub fn capture(
    vault: &Vault,
    quests: &QuestLog,
    nota: &NotaLog,
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
        volumes,
    }
}

/// Escreve o save em disco (JSON pretty).
pub fn save_to_disk(path: &std::path::Path, game: &SaveGame) -> Result<(), String> {
    let json = serde_json::to_string_pretty(game).map_err(|e| e.to_string())?;
    std::fs::write(path, json).map_err(|e| format!("{}: {e}", path.display()))
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
pub fn apply_save(
    game: &SaveGame,
    vault: &mut Vault,
    quests: &mut QuestLog,
    nota: &mut NotaLog,
    mixer: &mut AudioMixerSettings,
) {
    vault.gold = game.gold;
    vault.wood = game.wood;
    vault.stone = game.stone;
    vault.items = game.items.clone();
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
    mixer.master = game.volumes.0;
    mixer.music = game.volumes.1;
    mixer.sfx = game.volumes.2;
}

/// UI + atalhos da tab Opções: linhas ↑↓, volumes ←→, [J] save, [L] load.
#[allow(clippy::type_complexity, clippy::too_many_arguments)]
fn options_system(
    keys: Res<ButtonInput<KeyCode>>,
    open: Res<MenusOpen>,
    tab: Res<ModalTab>,
    mut rows: ResMut<OptionsRows>,
    mut mixer: ResMut<AudioMixerSettings>,
    mut vault: ResMut<Vault>,
    mut quests: ResMut<QuestLog>,
    mut nota: ResMut<NotaLog>,
    mut heroes: Query<(&mut Health, &mut Xp, &mut Transform), With<Player>>,
    mut toasts: MessageWriter<ScriptToast>,
    time: Res<Time>,
    mut throttle: Local<f32>,
) {
    if !open.modal || tab.0 + 1 != TAB_COUNT {
        return; // só na tab Opções (última)
    }
    *throttle -= time.delta_secs();
    if *throttle > 0.0 {
        return;
    }
    *throttle = 0.15;

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
    if keys.just_pressed(KeyCode::KeyJ) {
        let (health, xp, position) = heroes
            .single_mut()
            .map(|(hp, xp, t)| {
                ((hp.current, hp.max), (xp.current, xp.next), [t.translation.x, t.translation.y, t.translation.z])
            })
            .unwrap_or(((100.0, 100.0), (0, 100), [0.0; 3]));
        let game = capture(
            &vault,
            &quests,
            &nota,
            xp,
            health,
            position,
            (mixer.master, mixer.music, mixer.sfx),
        );
        if let Err(e) = save_to_disk(&save_path(), &game) {
            toasts.write(ScriptToast(format!("Falha ao gravar: {e}")));
        } else {
            toasts.write(ScriptToast("Jogo gravado.".into()));
        }
    }
    if keys.just_pressed(KeyCode::KeyL) {
        match load_from_disk(&save_path()) {
            Ok(game) => {
                apply_save(&game, &mut vault, &mut quests, &mut nota, &mut mixer);
                if let Ok((mut hp, mut xp, mut transform)) = heroes.single_mut() {
                    hp.current = game.health.0;
                    hp.max = game.health.1;
                    xp.current = game.xp.0;
                    xp.next = game.xp.1;
                    transform.translation = game.position.into();
                }
                toasts.write(ScriptToast("Jogo carregado.".into()));
            }
            Err(e) => {
                toasts.write(ScriptToast(format!("Falha ao carregar: {e}")));
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
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
        let game = capture(
            &vault,
            &quests,
            &nota,
            (30, 150),
            (77.5, 100.0),
            [12.0, 25.0, -8.0],
            (0.8, 0.5, 0.9),
        );
        assert_eq!(game.gold, 80);
        assert_eq!(game.quest_states.get("city_wolves").unwrap().0, 2);

        // roundtrip por JSON
        let json = serde_json::to_string(&game).unwrap();
        let loaded: SaveGame = serde_json::from_str(&json).unwrap();
        let mut vault2 = Vault::default();
        let mut quests2 = QuestLog::default();
        let mut nota2 = NotaLog::default();
        let mut mixer = AudioMixerSettings::default();
        apply_save(&loaded, &mut vault2, &mut quests2, &mut nota2, &mut mixer);
        assert_eq!(vault2.gold, 80);
        assert_eq!(vault2.wood, 6);
        assert_eq!(vault2.item_count("potion"), 2);
        assert_eq!(quests2.status("city_wolves", Some(&vault2)), crate::quests::QuestStatus::Active);
        assert_eq!(nota2.marked.len(), 1);
        assert!((mixer.master - 0.8).abs() < 1e-6);
        // XP/posição ficam por conta do chamador (teste do JSON):
        assert_eq!(loaded.xp, (30, 150));
        assert_eq!(loaded.position, [12.0, 25.0, -8.0]);
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
        std::fs::remove_file(&path).ok();
    }
}
