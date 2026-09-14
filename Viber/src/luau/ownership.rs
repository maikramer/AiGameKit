//! Posse de SISTEMAS nativos por scripts (`viber.own_system`).
//!
//! Mesmo padrão do `UiActionOwners`: um script reclama um sistema de domínio
//! e o handler nativo correspondente passa a saltar — a lógica vive no Lua.
//! SEM posse o comportamento nativo é o de sempre (compatível: nada muda até
//! um script pedir).
//!
//! Nomes válidos (`KNOWN_SYSTEMS`): `dialogue` (diálogo [E] dos
//! `<DialogueNPC>`), `abilities` (C/E/R), `bomb` (B), `guard` (L), `hotbar`
//! (1/2) e `harvest` (J nos destrutíveis).

use std::collections::HashSet;

use bevy::prelude::*;

/// Diálogo nativo de `<DialogueNPC>` (`quests::quest_dialogue_system`).
pub const SYSTEM_DIALOGUE: &str = "dialogue";
/// Habilidades [C] dash / [E] cura / [R] golpe radial.
pub const SYSTEM_ABILITIES: &str = "abilities";
/// Bomba [B].
pub const SYSTEM_BOMB: &str = "bomb";
/// Guarda/parry [L].
pub const SYSTEM_GUARD: &str = "guard";
/// Hotbar [1] poção / [2] antídoto.
pub const SYSTEM_HOTBAR: &str = "hotbar";
/// Colheita nativa [J] (destrutíveis).
pub const SYSTEM_HARVEST: &str = "harvest";

/// Sistemas reclamáveis — a validação à fila usa esta lista (typo vira erro
/// de script em vez de no-op silencioso).
pub const KNOWN_SYSTEMS: &[&str] = &[
    SYSTEM_DIALOGUE,
    SYSTEM_ABILITIES,
    SYSTEM_BOMB,
    SYSTEM_GUARD,
    SYSTEM_HOTBAR,
    SYSTEM_HARVEST,
];

/// Sistemas reclamados por scripts neste mundo.
#[derive(Debug, Default, Resource)]
pub struct ScriptSystemOwners(pub HashSet<String>);

impl ScriptSystemOwners {
    /// O sistema `name` está reclamado por um script?
    pub fn owns(&self, name: &str) -> bool {
        self.0.contains(name)
    }
}
