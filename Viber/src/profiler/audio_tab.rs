//! Snapshot "Áudio" do profiler — o equivalente compacto do `AudioDebug`
//! original: buses do mixer (`AudioMixerSettings`), layers de BGM
//! ([`crate::music::MusicLayerTag`]) e loops activos
//! ([`crate::music::LoopInstance`] + [`crate::music::LoopVolume`]), com
//! estado das instâncias kira e volume linear corrente.

use serde::Serialize;

use bevy::prelude::*;
use bevy_kira_audio::PlaybackState;

use crate::music::{AudioMixerSettings, LoopInstance, LoopVolume, MusicLayerTag};

#[derive(Debug, Clone, Serialize)]
pub struct BusSnapshot {
    pub master: f32,
    pub music: f32,
    pub sfx: f32,
}

#[derive(Debug, Clone, Serialize)]
pub struct LayerSnapshot {
    pub layer: String,
    pub base_volume: f32,
    /// Volume linear CORRENTE da layer (o crossfade do `music_driver`) —
    /// é o que prova a troca por zona (vila → `village`, Picos →
    /// `mountain`…): a layer activa lê perto do `base_volume`, as outras a
    /// caminhar para 0.
    pub volume: f32,
    pub paused: bool,
    pub muted: bool,
}

#[derive(Debug, Clone, Serialize)]
pub struct SinkSnapshot {
    pub entity: u64,
    pub name: String,
    pub paused: bool,
    pub muted: bool,
    pub spatial: bool,
    pub looping: bool,
    pub volume: f32,
    pub layer: Option<String>,
}

/// Snapshot do tab Áudio.
#[derive(Debug, Clone, Serialize)]
pub struct AudioSnapshot {
    pub buses: BusSnapshot,
    pub layers: Vec<LayerSnapshot>,
    pub sinks: Vec<SinkSnapshot>,
    pub total: usize,
    pub playing: usize,
    pub paused: usize,
    pub muted: usize,
    pub spatial: usize,
    pub looping: usize,
}

/// Recolhe o snapshot de áudio (5 Hz — iterar loops é barato).
pub fn snapshot(world: &mut World) -> AudioSnapshot {
    let buses = world
        .get_resource::<AudioMixerSettings>()
        .map(|m| BusSnapshot {
            master: m.master,
            music: m.music,
            sfx: m.sfx,
        })
        .unwrap_or(BusSnapshot {
            master: 1.0,
            music: 1.0,
            sfx: 1.0,
        });

    let mut layers = Vec::new();
    let mut sinks = Vec::new();
    let (mut playing, mut paused, muted, spatial, mut looping) =
        (0usize, 0usize, 0usize, 0usize, 0usize);

    // `world.query` precisa de &mut — criar ANTES de pegar no resource de
    // instâncias (depois ambos são só leitura e coexistem). Sem o resource
    // (apps mínimas sem kira), tudo lê como "a tocar" — os loops SÃO loops;
    // o que interessa aqui é o volume.
    let mut q = world.query::<(
        Entity,
        &LoopInstance,
        &LoopVolume,
        Option<&MusicLayerTag>,
        Option<&Name>,
    )>();
    let instances = world.get_resource::<Assets<bevy_kira_audio::AudioInstance>>();
    for (entity, instance, volume, layer, name) in q.iter(world) {
        let is_paused = instances
            .and_then(|a| a.get(&instance.0))
            .is_some_and(|i| matches!(i.state(), PlaybackState::Paused { .. }));
        let is_muted = false; // mute vive no BUS agora (kira), não por-som
        let vol = volume.0;

        if let Some(layer) = layer {
            layers.push(LayerSnapshot {
                layer: layer.layer.clone(),
                base_volume: layer.base_volume,
                volume: vol,
                paused: is_paused,
                muted: is_muted,
            });
        }

        if !is_paused {
            playing += 1;
        } else {
            paused += 1;
        }
        looping += 1; // tudo o que passa por audio_loop_starter é loop

        sinks.push(SinkSnapshot {
            entity: entity.to_bits(),
            name: name
                .map(|n| n.as_str().to_string())
                .unwrap_or_else(|| format!("#{}", entity.index())),
            paused: is_paused,
            muted: is_muted,
            spatial: false, // áudio espacial ainda não ligado no bus kira
            looping: true,
            volume: vol,
            layer: layer.map(|l| l.layer.clone()),
        });
    }
    sinks.sort_by(|a, b| a.name.cmp(&b.name));

    AudioSnapshot {
        buses,
        layers,
        total: sinks.len(),
        playing,
        paused,
        muted,
        spatial,
        looping,
        sinks,
    }
}

/// Linhas de texto da janela para o tab Áudio.
pub fn window_lines(snap: &AudioSnapshot) -> Vec<String> {
    let mut lines = Vec::new();
    lines.push(format!(
        "buses  master {:.2}  música {:.2}  sfx {:.2}",
        snap.buses.master, snap.buses.music, snap.buses.sfx
    ));
    lines.push(format!(
        "loops  {} total · {} a tocar · {} pausados",
        snap.total, snap.playing, snap.paused
    ));
    if snap.layers.is_empty() {
        lines.push("música (sem layers activos)".into());
    } else {
        lines.push("música:".into());
        for layer in &snap.layers {
            lines.push(format!(
                "  {} base {:.2} → agora {:.2}{}{}",
                layer.layer,
                layer.base_volume,
                layer.volume,
                if layer.paused { " [pausa]" } else { "" },
                if layer.muted { " [mute]" } else { "" },
            ));
        }
    }
    // Sinks a tocar primeiro, depois pausados — teto de 12 linhas.
    let mut sorted: Vec<&SinkSnapshot> = snap.sinks.iter().collect();
    sorted.sort_by_key(|s| (s.paused, s.name.clone()));
    for sink in sorted.iter().take(12) {
        lines.push(format!(
            "  {} {}{} v={:.2}{}",
            if sink.paused { "·" } else { "▶" },
            sink.name,
            if sink.looping { " ∞" } else { "" },
            sink.volume,
            sink.layer
                .as_ref()
                .map(|l| format!(" [{l}]"))
                .unwrap_or_default(),
        ));
    }
    if sorted.len() > 12 {
        lines.push(format!("  … +{} mais", sorted.len() - 12));
    }
    lines
}

/// Payload JSON do tab.
pub fn json(snap: &AudioSnapshot) -> serde_json::Value {
    serde_json::to_value(snap).unwrap_or(serde_json::Value::Null)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_snapshot_from_world_kira_loops() {
        let mut world = World::new();
        world.init_resource::<AudioMixerSettings>();
        // Layer BGM activa + loop de ambiente sem nome — sem instância kira
        // real (não há backend no teste), o estado default é "a tocar".
        world.spawn((
            MusicLayerTag {
                layer: "explore".to_string(),
                base_volume: 0.18,
            },
            LoopInstance(Handle::default()),
            LoopVolume(0.18),
        ));
        world.spawn((LoopInstance(Handle::default()), LoopVolume(0.3)));

        let snap = snapshot(&mut world);
        assert_eq!(snap.total, 2);
        assert_eq!(snap.playing, 2);
        assert_eq!(snap.looping, 2);
        assert_eq!(snap.layers.len(), 1, "a layer BGM aparece no snapshot");
        assert!(snap.buses.master > 0.0);

        let lines = window_lines(&snap);
        assert!(lines.iter().any(|l| l.contains("2 total")), "{lines:?}");
        assert!(lines.iter().any(|l| l.contains("explore")), "{lines:?}");
        // O crossfade lê-se: o volume CORRENTE da layer aparece ao lado do
        // base (gauntlet p1 r2 — "dumps BGM bit-idênticos sem demonstrar
        // crossfade").
        assert!(
            lines
                .iter()
                .any(|l| l.contains("explore") && l.contains("→ agora 0.18")),
            "{lines:?}"
        );
        assert_eq!(snap.layers[0].volume, 0.18);
        let json = json(&snap);
        assert_eq!(json["total"], 2, "{json}");
        assert_eq!(json["layers"][0]["layer"], "explore", "{json}");
        // 0.18 f32 não é exacto em JSON — comparar com tolerância.
        let json_volume = json["layers"][0]["volume"].as_f64().unwrap_or(0.0);
        assert!((json_volume - 0.18).abs() < 1e-3, "{json}");
    }
}
