//! Áudio de corrida — loop de motor com pitch por velocidade (só o kart do
//! jogador tem voz, como na referência) + loop de skid em drift. Lá o motor
//! é sintetizado com osciladores; aqui o `engine_rev.ogg` do pool dá a
//! voz, com playback-rate kira a mapear o rpm.

use bevy::asset::{AssetServer, Handle};
use bevy::prelude::*;
use bevy_kira_audio::{AudioChannel, AudioControl, AudioInstance, AudioTween};
use std::time::Duration;

use super::vehicle::Vehicle;
use crate::music::SfxBus;

/// Pitch do loop de motor em parado / ao teto.
const ENGINE_PITCH_IDLE: f64 = 0.55;
const ENGINE_PITCH_MAX: f64 = 1.7;
/// Volume base do motor (linear 0..1, convertido a dB).
const ENGINE_VOLUME: f32 = 0.32;

/// Volume de silêncio em dB (espelha o `DB_SILENCE` do music.rs).
const DB_SILENCE: f32 = -60.0;

/// Handles + instâncias vivas dos loops de corrida.
#[derive(Debug, Resource, Default)]
pub struct EngineAudio {
    engine: Option<Handle<bevy_kira_audio::AudioSource>>,
    skid: Option<Handle<bevy_kira_audio::AudioSource>>,
    engine_instance: Option<Handle<AudioInstance>>,
    skid_instance: Option<Handle<AudioInstance>>,
}

/// Carrega os clipes de veículo do `sfx_dir` do config.
pub fn load_engine_audio(
    mut commands: Commands,
    server: Res<AssetServer>,
    config: Res<crate::config::GameConfig>,
) {
    commands.insert_resource(EngineAudio {
        engine: Some(server.load(config.sfx_path("vehicles/engine_rev.ogg"))),
        skid: Some(server.load(config.sfx_path("vehicles/skid.ogg"))),
        engine_instance: None,
        skid_instance: None,
    });
}

/// Liga/mantém os loops e modula pitch/volume por velocidade e drift.
pub fn engine_audio_system(
    mut state: ResMut<EngineAudio>,
    audio: Option<Res<bevy_kira_audio::Audio>>,
    channel: Option<Res<AudioChannel<SfxBus>>>,
    mixer: Option<Res<crate::music::AudioMixerSettings>>,
    mut instances: ResMut<Assets<AudioInstance>>,
    karts: Query<&Vehicle>,
    mut had_player: Local<bool>,
) {
    let (Some(_audio), Some(channel)) = (audio, channel) else {
        return;
    };
    let channel = channel.into_inner();
    let Some(kart) = karts.iter().find(|v| v.is_player()) else {
        return;
    };
    // Arranca os loops à primeira passagem com kart do jogador (a zeros —
    // o volume entra no fade do frame seguinte).
    if !*had_player {
        *had_player = true;
        if let Some(engine) = state.engine.clone() {
            state.engine_instance = Some(
                channel
                    .play(engine)
                    .looped()
                    .with_volume(DB_SILENCE)
                    .handle(),
            );
        }
        if let Some(skid) = state.skid.clone() {
            state.skid_instance =
                Some(channel.play(skid).looped().with_volume(DB_SILENCE).handle());
        }
    }

    let master = mixer
        .as_deref()
        .map(|m| m.master)
        .unwrap_or(1.0)
        .clamp(0.0, 1.0);
    // rpm: parado → teto mapeia 0.55 → 1.7.
    let ratio = kart.speed_ratio().clamp(0.0, 1.0);
    let pitch = ENGINE_PITCH_IDLE + (ENGINE_PITCH_MAX - ENGINE_PITCH_IDLE) * ratio as f64;
    let engine_vol = ENGINE_VOLUME * (0.55 + 0.45 * ratio) * master;
    if let Some(instance) = &state.engine_instance
        && let Some(mut snd) = instances.get_mut(instance)
    {
        snd.set_playback_rate(pitch, AudioTween::linear(Duration::from_millis(90)));
        snd.set_decibels(
            crate::music::linear_to_db(engine_vol),
            AudioTween::linear(Duration::from_millis(90)),
        );
    }
    // Skid: audível só em drift com carga.
    let skid_vol = if kart.drift_charge > 0.0 && kart.speed.abs() > 10.0 {
        0.22 * master
    } else {
        0.0
    };
    if let Some(instance) = &state.skid_instance
        && let Some(mut snd) = instances.get_mut(instance)
    {
        snd.set_decibels(
            crate::music::linear_to_db(skid_vol),
            AudioTween::linear(Duration::from_millis(120)),
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn engine_audio_defaults_start_silent() {
        let e = EngineAudio::default();
        assert!(e.engine.is_none() && e.engine_instance.is_none());
    }
}
