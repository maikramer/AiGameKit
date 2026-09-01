//! BGM driver: loops every `<MusicLayer>` at volume 0 and crossfades the
//! layer matching the player's zone (ported from the example's `bgmZone`).

use bevy::audio::Volume;
use bevy::math::Vec2;
use bevy::prelude::*;

/// Bus volumes from `<AudioMixer master music sfx>`.
#[derive(Debug, Clone, Resource, Default)]
pub struct AudioMixerSettings {
    pub master: f32,
    pub music: f32,
    pub sfx: f32,
}

/// One playing BGM layer.
#[derive(Debug, Component)]
pub struct MusicLayerTag {
    pub layer: String,
    pub base_volume: f32,
}

/// Player context marker for the driver (reuses the `Player` component).
pub fn bgm_zone(x: f32, z: f32) -> &'static str {
    // Interiores remotos (caixa dos interiores, com margem)
    if (770.0..950.0).contains(&x) && (205.0..355.0).contains(&z) {
        return "dungeon";
    }
    // Cunha dos Picos Gelados: z <= -240, |x| abre 240 → 1040
    if z <= -240.0 && x.abs() <= 240.0 + 0.0f32.max(-z - 240.0) {
        return "mountain";
    }
    // Vila murada na origem (SpawnExclusion r52 + margem)
    if x * x + z * z < 55.0 * 55.0 {
        return "village";
    }
    "explore"
}

/// Target linear volume of one layer given the active zone.
pub fn layer_target(layer: &str, zone: &str, base_volume: f32, music: f32, master: f32) -> f32 {
    if layer == zone {
        base_volume * music * master
    } else {
        0.0
    }
}

/// Crossfade one step: move `current` toward `target` by `speed` per second.
pub fn fade_step(current: f32, target: f32, dt: f32, speed: f32) -> f32 {
    let delta = target - current;
    let max_step = speed * dt;
    if delta.abs() <= max_step {
        target
    } else {
        current + delta.signum() * max_step
    }
}

/// Move each layer's volume toward its zone target every frame.
pub fn music_driver(
    time: Res<Time>,
    players: Query<&GlobalTransform, With<crate::player::Player>>,
    mixer: Option<Res<AudioMixerSettings>>,
    mut layers: Query<(&MusicLayerTag, &mut PlaybackSettings)>,
) {
    let Ok(player) = players.single() else {
        return;
    };
    let pos = player.translation();
    let zone = bgm_zone(pos.x, pos.z);
    let (master, music) = mixer.map(|m| (m.master, m.music)).unwrap_or((1.0, 1.0));
    let dt = time.delta_secs().clamp(0.0, 0.2);
    for (tag, mut settings) in &mut layers {
        let target = layer_target(&tag.layer, zone, tag.base_volume, music, master);
        let current = settings.volume.to_linear();
        let next = fade_step(current, target, dt, 0.6);
        if next != current {
            settings.volume = Volume::Linear(next);
        }
    }
}

/// Unused import guard — `Vec2` kept for future positional (2D) BGM filters.
#[allow(dead_code)]
type _Unused = Vec2;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_bgm_zone_village_at_origin() {
        assert_eq!(bgm_zone(0.0, 0.0), "village");
        assert_eq!(bgm_zone(40.0, 30.0), "village");
    }

    #[test]
    fn test_bgm_zone_dungeon_box() {
        assert_eq!(bgm_zone(850.0, 280.0), "dungeon");
        assert_eq!(bgm_zone(775.0, 210.0), "dungeon");
        // fora da caixa
        assert_eq!(bgm_zone(760.0, 280.0), "explore");
    }

    #[test]
    fn test_bgm_zone_mountain_wedge() {
        assert_eq!(bgm_zone(0.0, -300.0), "mountain");
        assert_eq!(bgm_zone(200.0, -400.0), "mountain");
        // wedge fecha a sul: em z=-250 só |x| <= 240+10
        assert_eq!(bgm_zone(600.0, -250.0), "explore");
    }

    #[test]
    fn test_bgm_zone_explore_default() {
        assert_eq!(bgm_zone(300.0, 300.0), "explore");
    }

    #[test]
    fn test_layer_target_crossfades() {
        assert!(
            layer_target("explore", "explore", 0.18, 0.7, 1.0) > 0.1,
            "active layer gets volume"
        );
        assert_eq!(
            layer_target("boss", "explore", 0.24, 0.7, 1.0),
            0.0,
            "inactive layer mutes"
        );
    }

    #[test]
    fn test_fade_step_converges() {
        assert_eq!(fade_step(0.5, 0.5, 0.1, 0.6), 0.5);
        assert!(fade_step(0.0, 1.0, 0.1, 0.6) > 0.0);
        assert_eq!(fade_step(0.0, 1.0, 5.0, 0.6), 1.0, "reaches target");
    }
}
