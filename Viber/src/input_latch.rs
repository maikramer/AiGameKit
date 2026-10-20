//! Input latch — robustez de TECLAS SEGURADAS para teclados remotos.
//!
//! Ambientes de remote desktop (RustDesk/AnyDesk) e alguns teclados
//! virtuais entregam um press como par down+up quase instantâneo: o
//! `ButtonInput::pressed` fica true por UM frame e o movimento morre (o
//! atrito/gravidade comem o passo de 1 frame). As interações de TOQUE
//! (`just_pressed` — atacar/interagir) funcionam, o MOVIMENTO não —
//! exatamente o sintoma reportado via RustDesk (2026-10-16).
//!
//! O latch dá a cada tecla pressionada um piso de [`LATCH_SECS`] de "held":
//! enquanto o SO reportar a tecla pressionada o piso é renovado; um press
//! curto fica ativo até o piso expirar. Teclado normal é indistinguível
//! (held = renovado a cada frame); um tap passa a MOVER de verdade.

use bevy::input::keyboard::KeyCode;
use bevy::prelude::*;
use std::collections::HashMap;

/// Piso de hold por press (s) — 3-4 frames a 60 fps; curto o suficiente para
/// não parecer input colado, largo o suficiente para mover de facto.
pub const LATCH_SECS: f64 = 0.22;

/// Pisos de hold por tecla (expiram sozinhos).
#[derive(Debug, Default, Resource)]
pub struct KeyLatches {
    now: f64,
    until: HashMap<KeyCode, f64>,
}

/// Atualiza os pisos: held renova, press novo arma, expirados saem.
pub fn key_latch_system(
    keys: Res<ButtonInput<KeyCode>>,
    time: Res<Time>,
    mut latches: ResMut<KeyLatches>,
) {
    latches.now = time.elapsed_secs_f64();
    let now = latches.now;
    latches.until.retain(|code, until| {
        if keys.pressed(*code) {
            *until = now + LATCH_SECS;
            true
        } else {
            *until > now
        }
    });
    for code in keys.get_just_pressed() {
        latches.until.insert(*code, now + LATCH_SECS);
    }
}

impl KeyLatches {
    /// A tecla está efetivamente pressionada (SO **ou** piso ativo).
    pub fn down(&self, keys: &ButtonInput<KeyCode>, code: KeyCode) -> bool {
        keys.pressed(code) || self.until.get(&code).copied().unwrap_or(0.0) > self.now
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn latch_expires_without_press() {
        let mut latches = KeyLatches {
            now: 10.0,
            until: HashMap::new(),
        };
        // um press aos 10 s arma até 10.22
        latches.until.insert(KeyCode::KeyW, 10.0 + LATCH_SECS);
        // nada de input aqui — só consulta o estado
        let keys = ButtonInput::<KeyCode>::default();
        latches.now = 10.1;
        assert!(latches.down(&keys, KeyCode::KeyW), "latch ativo");
        latches.now = 10.4;
        assert!(!latches.down(&keys, KeyCode::KeyW), "latch expirado");
    }
}
