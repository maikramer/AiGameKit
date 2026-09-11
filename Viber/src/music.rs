//! BGM driver: loops every `<MusicLayer>` at volume 0 and crossfades the
//! layer matching the player's zone (ported from the example's `bgmZone`).
//!
//! **Backend kira** ([`bevy_kira_audio`]): dois buses tipados
//! ([`MusicBus`]/[`SfxBus`]) recebem os volumes do [`AudioMixerSettings`] via
//! [`mixer_sync`] — um som NOVO não precisa de saber do mixer, o bus aplica-o
//! a tudo o que está a tocar (incl. one-shots a meio). O crossfade
//! ([`fade_step`]) continua linear em espaço LINEAR (puro + testado); a
//! conversão para dB acontece só na fronteira ([`linear_to_db`]).

use std::time::Duration;

use bevy::prelude::*;
use bevy_kira_audio::{AudioChannel, AudioControl, AudioInstance, AudioTween};

/// Piso de silêncio em dB — kira trata ≤ −60 dB como inaudível.
const DB_SILENCE: f32 = -60.0;

/// Linear 0..1 → decibéis (kira trabalha em dB; −60 = silêncio).
pub fn linear_to_db(v: f32) -> f32 {
    if !v.is_finite() || v <= 0.000_1 {
        DB_SILENCE
    } else {
        (20.0 * v.log10()).max(DB_SILENCE)
    }
}

/// Bus de música (`<AudioMixer music>` × master) — BGM e loops de ambiente
/// musicais? NÃO: só BGM ([`MusicLayerTag`]). Ambiente de água/chuva vai no
/// bus SFX, como os efeitos.
#[derive(Resource)]
pub struct MusicBus;

/// Bus de efeitos (`<AudioMixer sfx>` × master) — SFX one-shots + loops de
/// água/chuva.
#[derive(Resource)]
pub struct SfxBus;

/// Bus volumes from `<AudioMixer master music sfx>`.
///
/// The resource always exists (worlds without an `<AudioMixer>` get this
/// default): a derived `Default` would zero every bus — i.e. a silent game —
/// and any system taking `ResMut<AudioMixerSettings>` used to panic outright
/// on a world that never declared the tag.
#[derive(Debug, Clone, Resource)]
pub struct AudioMixerSettings {
    pub master: f32,
    pub music: f32,
    pub sfx: f32,
}

impl Default for AudioMixerSettings {
    fn default() -> Self {
        Self {
            master: 1.0,
            music: 1.0,
            sfx: 1.0,
        }
    }
}

/// One playing BGM layer.
#[derive(Debug, Component)]
pub struct MusicLayerTag {
    pub layer: String,
    pub base_volume: f32,
}

/// Loop à espera que o bus kira arranque o playback (BGM OU ambiente). O
/// [`audio_loop_starter`], consome-o, toca o asset em LOOP e insere
/// [`LoopInstance`]. O asset a carregar não é problema — o bevy_kira_audio
/// começa a tocar quando o `.ogg` chega.
#[derive(Debug, Component)]
pub struct AudioLoopPending {
    /// URL do asset de áudio (convenções `assets/audio/bgm/*.ogg` /
    /// `assets/audio/sfx/**`).
    pub url: String,
    /// `true` = bus de música; `false` = bus de SFX.
    pub music: bool,
}

/// Instância kira de um loop em curso (handle para o `Assets<AudioInstance>`).
#[derive(Debug, Component)]
pub struct LoopInstance(pub Handle<AudioInstance>);

/// Volume linear corrente de um loop — cache para o [`fade_step`] (o kira
/// não lê o volume de volta).
#[derive(Debug, Component, Default)]
pub struct LoopVolume(pub f32);

/// Player context marker for the driver (reuses the `Player` component).
pub fn bgm_zone(x: f32, z: f32, scene: Option<&crate::worldsys::InteriorSceneConfig>) -> &'static str {
    // Bolsa de interior DECLARADA (fora da área do mapa). A zona de masmorra
    // seguia uma caixa copiada à mão (770..950 × 205..355) que ficava para
    // trás de cada vez que a grelha de interiores se movia — e a grelha mudou
    // de sítio: agora segue o `<InteriorScene>`, que é a fonte de verdade.
    if scene.is_some_and(|s| s.contains(x, z)) {
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

/// Segundos que a layer de combate sobrevive ao último evento de combate —
/// tiros trocados mantêm o timer vivo; afastar-se sem luta deixa voltar a
/// música da zona.
pub const COMBAT_MUSIC_HOLD: f32 = 8.0;

/// Estado da música de combate: enquanto ativo, a layer `battle` (ou `boss`
/// quando o alvo o é) ganha à zona do player. Escrito pelos gatilhos de
/// combate (aggro da IA, golpes); expira sozinho após
/// [`COMBAT_MUSIC_HOLD`] s sem eventos.
///
/// O recurso é inicializado no [`crate::ai::AiPlugin`] (que também o
/// escreve) — worlds sem IA ficam com o crossfade puro por zona.
#[derive(Debug, Clone, Resource, Default)]
pub struct CombatMusicState {
    /// Instante (`Time.elapsed_secs_f64()`) até ao qual o combate "soa".
    until: f64,
    /// Alvo atual é um boss → layer `boss` em vez de `battle`.
    boss: bool,
}

impl CombatMusicState {
    /// (Re)acende a música de combate; `boss` promove para a layer de boss
    /// (nunca desce de boss para battle enquanto o timer viver).
    pub fn engage(&mut self, now: f64, boss: bool) {
        self.until = (now + COMBAT_MUSIC_HOLD as f64).max(self.until);
        if boss {
            self.boss = true;
        }
    }

    /// Layer de combate ativa agora, expirando o timer (`boss` > `battle`).
    pub fn active_layer(&mut self, now: f64) -> Option<&'static str> {
        if now >= self.until {
            self.boss = false;
            None
        } else if self.boss {
            Some("boss")
        } else {
            Some("battle")
        }
    }
}

/// Target linear volume of one layer given the active zone — SEM os buses
/// (music×master agora vivem no [`MusicBus`], aplicados pelo kira a tudo o
/// que está a tocar).
pub fn layer_target(layer: &str, zone: &str, base_volume: f32) -> f32 {
    if layer == zone {
        base_volume
    } else {
        0.0
    }
}

/// Crossfade one step: move `current` toward `target` by `speed` per second.
pub fn fade_step(current: f32, target: f32, dt: f32, speed: f32) -> f32 {
    // NaN/inf não morrem no clamp: devolver o `current` evita que
    // `next != current` seja sempre verdadeiro (reescrita de Volume todos
    // os frames para sempre).
    if !current.is_finite() || !target.is_finite() {
        return current;
    }
    let delta = target - current;
    let max_step = speed * dt;
    if delta.abs() <= max_step {
        target
    } else {
        current + delta.signum() * max_step
    }
}

/// Arranca os loops pendentes ([`AudioLoopPending`]) no bus certo e insere
/// [`LoopInstance`]/[`LoopVolume`]. Sem kira vivo (apps de teste headless)
/// os pendentes ficam — os drivers ignoram-nos.
pub fn audio_loop_starter(
    mut commands: Commands,
    pending: Query<(Entity, &AudioLoopPending), Added<AudioLoopPending>>,
    server: Res<AssetServer>,
    music: Option<Res<AudioChannel<MusicBus>>>,
    sfx: Option<Res<AudioChannel<SfxBus>>>,
) {
    for (entity, pend) in &pending {
        // Os buses são tipos distintos e o trait de playback não é
        // dyn-compatible — o branch repete 2 linhas, mas mantém o resto único.
        let handle = if pend.music {
            let Some(channel) = music.as_deref() else {
                continue;
            };
            channel
                .play(server.load(&pend.url))
                .looped()
                .with_volume(DB_SILENCE)
                .handle()
        } else {
            let Some(channel) = sfx.as_deref() else {
                continue;
            };
            channel
                .play(server.load(&pend.url))
                .looped()
                .with_volume(DB_SILENCE)
                .handle()
        };
        commands
            .entity(entity)
            .insert((LoopInstance(handle), LoopVolume(0.0)))
            .remove::<AudioLoopPending>();
    }
}

/// Empurra [`AudioMixerSettings`] para os buses kira — sliders do menu e
/// volumes do save afetam INSTANTANEamente o que está a tocar (o modelo
/// antigo multiplicava no momento do spawn; um one-shot a meio não ouvia a
/// mudança). Tween curto = transições sem clique.
pub fn mixer_sync(
    mixer: Option<Res<AudioMixerSettings>>,
    music: Option<Res<AudioChannel<MusicBus>>>,
    sfx: Option<Res<AudioChannel<SfxBus>>>,
    mut seeded: Local<bool>,
    mut last: Local<[f32; 3]>,
) {
    let Some(mixer) = mixer else {
        return;
    };
    // 1.º frame aplica SEMPRE (o `last` a zeros é lixo — um mixer autoral
    // 0/0/0 no arranque teria de empurrar −60 dB para os buses).
    let values = [mixer.master, mixer.music, mixer.sfx];
    if *seeded && *last == values {
        return;
    }
    *seeded = true;
    *last = values;
    let master = mixer.master.clamp(0.0, 1.0);
    let tween_ms = || Duration::from_millis(120);
    if let Some(channel) = music {
        channel
            .set_volume(linear_to_db(mixer.music.clamp(0.0, 1.0) * master))
            .linear_fade_in(tween_ms());
    }
    if let Some(channel) = sfx {
        channel
            .set_volume(linear_to_db(mixer.sfx.clamp(0.0, 1.0) * master))
            .linear_fade_in(tween_ms());
    }
}

/// Move each layer's volume toward its zone target every frame.
pub fn music_driver(
    time: Res<Time>,
    players: Query<&GlobalTransform, With<crate::player::Player>>,
    scene: Option<Res<crate::worldsys::InteriorSceneConfig>>,
    mut combat: ResMut<CombatMusicState>,
    mut layers: Query<(&MusicLayerTag, &LoopInstance, &mut LoopVolume)>,
    mut instances: ResMut<Assets<AudioInstance>>,
) {
    // `iter().next()` e não `single()`: mundos com 0 ou ≥2 players (o
    // auto-orbit suporta mundo sem player) ficavam para sempre sem BGM.
    let Some(player) = players.iter().next() else {
        return;
    };
    let pos = player.translation();
    // Combate ativo ganha à zona (boss > battle); sem luta, música do sítio.
    let zone = combat
        .active_layer(time.elapsed_secs_f64())
        .unwrap_or_else(|| bgm_zone(pos.x, pos.z, scene.as_deref()));
    let dt = time.delta_secs().clamp(0.0, 0.2);
    for (tag, instance, mut volume) in &mut layers {
        let target = layer_target(&tag.layer, zone, tag.base_volume);
        let next = fade_step(volume.0, target, dt, 0.6);
        if next != volume.0 {
            let Some(mut snd) = instances.get_mut(&instance.0) else {
                continue;
            };
            snd.set_decibels(
                linear_to_db(next),
                AudioTween::linear(Duration::from_millis(60)),
            );
            volume.0 = next;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_linear_to_db_floor_and_passthrough() {
        assert_eq!(linear_to_db(0.0), DB_SILENCE);
        assert_eq!(linear_to_db(f32::NAN), DB_SILENCE);
        assert_eq!(linear_to_db(-1.0), DB_SILENCE);
        // 1.0 linear = 0 dB.
        assert!((linear_to_db(1.0)).abs() < 1e-4);
        // 0.1 linear ≈ −20 dB.
        assert!((linear_to_db(0.1) + 20.0).abs() < 0.01);
    }

    #[test]
    fn test_bgm_zone_village_at_origin() {
        assert_eq!(bgm_zone(0.0, 0.0, None), "village");
        assert_eq!(bgm_zone(40.0, 30.0, None), "village");
    }

    #[test]
    fn test_bgm_zone_dungeon_box() {
        let scene = crate::worldsys::InteriorSceneConfig {
            min: [2500.0, 2500.0],
            max: [2900.0, 2830.0],
        };
        assert_eq!(bgm_zone(2700.0, 2700.0, Some(&scene)), "dungeon");
        assert_eq!(bgm_zone(2510.0, 2820.0, Some(&scene)), "dungeon");
        // fora da bolsa (e sem bolsa declarada) cai na zona normal
        assert_eq!(bgm_zone(2400.0, 2700.0, Some(&scene)), "explore");
        assert_eq!(bgm_zone(2700.0, 2700.0, None), "explore");
    }

    #[test]
    fn test_bgm_zone_mountain_wedge() {
        assert_eq!(bgm_zone(0.0, -300.0, None), "mountain");
        assert_eq!(bgm_zone(200.0, -400.0, None), "mountain");
        // wedge fecha a sul: em z=-250 só |x| <= 240+10
        assert_eq!(bgm_zone(600.0, -250.0, None), "explore");
    }

    #[test]
    fn test_bgm_zone_explore_default() {
        assert_eq!(bgm_zone(300.0, 300.0, None), "explore");
    }

    #[test]
    fn test_layer_target_crossfades() {
        assert!(
            layer_target("explore", "explore", 0.18) > 0.1,
            "active layer gets volume"
        );
        assert_eq!(
            layer_target("boss", "explore", 0.24),
            0.0,
            "inactive layer mutes"
        );
    }

    #[test]
    fn test_fade_step_converges() {
        assert_eq!(fade_step(0.5, 0.5, 0.1, 0.6), 0.5);
        assert!(fade_step(0.0, 1.0, 0.1, 0.6) > 0.0);
        assert_eq!(fade_step(0.0, 1.0, 5.0, 0.6), 1.0, "reaches target");
        // NaN não se propaga: devolve o current (o driver não reescreve).
        assert!(fade_step(f32::NAN, 1.0, 0.1, 0.6).is_nan());
        assert_eq!(fade_step(0.25, f32::NAN, 0.1, 0.6), 0.25);
    }

    #[test]
    fn test_combat_music_engage_and_expiry() {
        let mut combat = CombatMusicState::default();
        assert_eq!(combat.active_layer(0.0), None, "sem combate não há layer");

        combat.engage(10.0, false);
        assert_eq!(combat.active_layer(10.0), Some("battle"));
        assert_eq!(
            combat.active_layer(10.0 + COMBAT_MUSIC_HOLD as f64 - 0.5),
            Some("battle")
        );
        assert_eq!(
            combat.active_layer(10.0 + COMBAT_MUSIC_HOLD as f64 + 0.5),
            None,
            "expira sozinho após o hold"
        );

        // Refrescos prolongam; boss promove e não desce a battle.
        combat.engage(20.0, false);
        combat.engage(22.0, true);
        assert_eq!(combat.active_layer(27.0), Some("boss"), "boss > battle");
        // 22 + hold = 30; a 31 já ninguém renovou.
        assert_eq!(combat.active_layer(31.0), None);
    }

    #[test]
    fn test_combat_music_beats_zone_in_driver_target() {
        // layer_target recebe a zona resolvida — com combate ativo a layer
        // "battle" tem de ser a ativa mesmo em pé na vila.
        assert!(layer_target("battle", "battle", 0.24) > 0.1);
        assert_eq!(layer_target("village", "battle", 0.2), 0.0);
    }
}
