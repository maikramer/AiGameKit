//! Alcance e ARBITRAGEM das interações ([E] falar/usar, [J] colher).
//!
//! Duas regras, uma fonte:
//!
//! 1. **Alcance** — tudo o que é interagível mede-se com o mesmo metro.
//!    O histórico era 3,5 m repetido em seis sítios (script, diálogo,
//!    colheita, prompt, balão); a 3,5 m os alvos de uma praça sobrepõem-se e
//!    o jogador nunca sabe com quem vai falar. O alcance passa a
//!    [`BASE_RANGE_M`] × [`range_scale`] (default 0,5 → **1,75 m**), e os
//!    alcances AUTORADOS (`viber.set_interaction`, `destructible range`)
//!    levam a mesma escala.
//!
//! 2. **O mais próximo ganha** — um só vencedor POR TECLA, arbitrado aqui e
//!    obedecido por todos os consumidores. Antes cada script decidia sozinho
//!    ("estou em alcance? então disparo"), portanto dois NPC sobrepostos
//!    reagiam ambos ao mesmo [E]; o prompt já mostrava só o mais próximo, o
//!    que tornava o resultado imprevisível para quem joga.
//!
//! O prompt do HUD (`ui::collect::collect_ui_prompt`) continua a ser quem
//! DESENHA a legenda; este módulo é quem decide quem AGE.

use std::collections::HashMap;

use bevy::prelude::*;

use crate::luau::ScriptInteraction;
use crate::player::Player;
use crate::recipes::spawn::DialogueNpc;

/// Alcance histórico de interação (m) — a unidade em que o conteúdo foi
/// autorado. O valor EFETIVO é este × [`range_scale`].
pub const BASE_RANGE_M: f32 = 3.5;

/// Escala global dos alcances de interação.
///
/// 0,5 = metade do histórico (pedido do utilizador a 2026-09-12: "diminua o
/// raio de ação para qualquer ação, para reduzir overlaps").
/// `VIBER_INTERACT_RANGE_SCALE=1` devolve o comportamento antigo (A/B).
pub const RANGE_SCALE: f32 = 0.5;

/// Escala efetiva (env `VIBER_INTERACT_RANGE_SCALE`, senão [`RANGE_SCALE`]).
pub fn range_scale() -> f32 {
    std::env::var("VIBER_INTERACT_RANGE_SCALE")
        .ok()
        .and_then(|raw| raw.parse::<f32>().ok())
        .filter(|v| v.is_finite() && *v > 0.0)
        .unwrap_or(RANGE_SCALE)
}

/// Alcance efetivo de um valor autorado (`set_interaction`, `destructible`).
pub fn scaled_range(authored: f32) -> f32 {
    authored * range_scale()
}

/// Alcance efetivo por omissão (diálogo, prompt, balão, colheita nativa).
pub fn default_range() -> f32 {
    scaled_range(BASE_RANGE_M)
}

/// Vencedor da interação POR TECLA neste frame: a entidade interagível mais
/// próxima do herói dentro do seu alcance.
///
/// Uma tecla, um alvo: é o que faz `[E]` ser previsível numa praça cheia.
#[derive(Resource, Default, Debug, Clone)]
pub struct InteractionFocus {
    by_key: HashMap<KeyCode, (Entity, f32)>,
}

impl InteractionFocus {
    /// Propõe um candidato; fica se for mais perto do que o atual.
    pub fn consider(&mut self, key: KeyCode, entity: Entity, distance: f32) {
        match self.by_key.get(&key) {
            Some((_, best)) if *best <= distance => {}
            _ => {
                self.by_key.insert(key, (entity, distance));
            }
        }
    }

    /// Entidade que ganha esta tecla (se alguma).
    pub fn winner(&self, key: KeyCode) -> Option<Entity> {
        self.by_key.get(&key).map(|(entity, _)| *entity)
    }

    /// `entity` é o alvo desta tecla?
    pub fn wins(&self, key: KeyCode, entity: Entity) -> bool {
        self.winner(key) == Some(entity)
    }

    /// Cópia `tecla → entidade` para o snapshot dos scripts.
    pub fn snapshot(&self) -> HashMap<KeyCode, Entity> {
        self.by_key
            .iter()
            .map(|(key, (entity, _))| (*key, *entity))
            .collect()
    }

    pub fn clear(&mut self) {
        self.by_key.clear();
    }

    pub fn is_empty(&self) -> bool {
        self.by_key.is_empty()
    }
}

/// Recalcula o vencedor por tecla a cada frame (PreUpdate, antes dos scripts
/// e dos sistemas nativos que leem o foco).
///
/// Fontes: entidades com [`ScriptInteraction`] (NPCs/props scriptados; o
/// alcance já vem escalado do `set_interaction`) e os `<DialogueNPC>`
/// nativos, que jogam no [E] com o alcance por omissão.
#[allow(clippy::type_complexity)]
pub fn focus_interactions(
    mut focus: ResMut<InteractionFocus>,
    players: Query<&GlobalTransform, With<Player>>,
    targets: Query<(Entity, &GlobalTransform, &ScriptInteraction), Without<Player>>,
    npcs: Query<(Entity, &GlobalTransform), (With<DialogueNpc>, Without<Player>)>,
) {
    focus.clear();
    // `iter().next()` e não `single()`: ≥2 players não pode matar a interação.
    let Some(player) = players.iter().next() else {
        return;
    };
    let origin = player.translation();
    for (entity, transform, interaction) in &targets {
        let distance = transform.translation().distance(origin);
        if distance <= interaction.range {
            focus.consider(interaction.key, entity, distance);
        }
    }
    let dialogue_range = default_range();
    for (entity, transform) in &npcs {
        let distance = transform.translation().distance(origin);
        if distance <= dialogue_range {
            focus.consider(KeyCode::KeyE, entity, distance);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// O alcance efetivo é METADE do autorado (a menos que o env mande).
    #[test]
    fn ranges_are_halved_by_default() {
        if std::env::var_os("VIBER_INTERACT_RANGE_SCALE").is_none() {
            assert!((scaled_range(BASE_RANGE_M) - 1.75).abs() < 1e-5);
            assert!((default_range() - 1.75).abs() < 1e-5);
            assert!((scaled_range(6.0) - 3.0).abs() < 1e-5);
        }
    }

    /// Uma tecla, um vencedor: o MAIS PRÓXIMO, independentemente da ordem por
    /// que os candidatos aparecem (a ordem de query do Bevy não é estável).
    #[test]
    fn nearest_candidate_wins_the_key() {
        let mut focus = InteractionFocus::default();
        let far = Entity::from_raw_u32(7).expect("id de teste");
        let near = Entity::from_raw_u32(9).expect("id de teste");
        focus.consider(KeyCode::KeyE, far, 3.0);
        focus.consider(KeyCode::KeyE, near, 1.0);
        assert_eq!(focus.winner(KeyCode::KeyE), Some(near));
        // A ordem inversa dá o mesmo vencedor.
        let mut focus = InteractionFocus::default();
        focus.consider(KeyCode::KeyE, near, 1.0);
        focus.consider(KeyCode::KeyE, far, 3.0);
        assert!(focus.wins(KeyCode::KeyE, near));
        assert!(!focus.wins(KeyCode::KeyE, far));
    }

    /// Teclas diferentes não competem: um prop de [J] colado ao herói não
    /// pode roubar o [E] ao NPC ao lado.
    #[test]
    fn keys_do_not_steal_each_other() {
        let mut focus = InteractionFocus::default();
        let prop = Entity::from_raw_u32(3).expect("id de teste");
        let npc = Entity::from_raw_u32(4).expect("id de teste");
        focus.consider(KeyCode::KeyJ, prop, 0.5);
        focus.consider(KeyCode::KeyE, npc, 2.0);
        assert!(focus.wins(KeyCode::KeyJ, prop));
        assert!(focus.wins(KeyCode::KeyE, npc));
    }
}
