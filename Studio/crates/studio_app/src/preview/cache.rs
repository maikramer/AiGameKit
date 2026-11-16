//! Decode dos GLB do pipeline para a cache de assets do viewport.
//!
//! O pool usa `EXT_meshopt_compression`/`KHR_texture_basisu`; o
//! `bevy_ext_gltf::decode_glb` expande/dequantiza e escreve-se uma cópia na
//! cache da app (que é o `AssetPlugin.file_path`).

use std::{
    collections::hash_map::DefaultHasher,
    fs,
    hash::{Hash, Hasher},
    path::Path,
    time::UNIX_EPOCH,
};

use bevy::prelude::*;
use crossbeam_channel::bounded;

use super::{
    DecodedGlb, PendingDecode, PendingScene, PreviewCache, PreviewCommand, PreviewState,
};

pub fn handle_load_request(
    mut commands: ResMut<PreviewCommand>,
    mut bevy_commands: Commands,
    mut state: ResMut<PreviewState>,
    cache: Res<PreviewCache>,
) {
    if commands.clear {
        commands.clear = false;
        if let Some(loaded) = state.loaded.take() {
            bevy_commands.entity(loaded.root).despawn();
        }
        state.pending_decode = None;
        state.pending_scene = None;
        state.ready_scene = None;
        state.bounds = None;
        state.source_artifact = None;
        state.playing = false;
    }

    let Some(request) = commands.load.take() else {
        return;
    };
    if !request.source.is_file() {
        state.error = Some(format!("artefacto ausente: {}", request.source.display()));
        return;
    }
    if state
        .loaded
        .as_ref()
        .is_some_and(|loaded| loaded.source == request.source)
    {
        if let Some(clip) = request.clip {
            commands.set_clip = Some(clip);
        }
        return;
    }
    if let Some(clip) = request.clip.clone() {
        commands.set_clip = Some(clip);
    }

    state.bounds = bevy_ext_gltf::glb_bounds::read_json_chunk(&request.source);
    state.error = None;
    state.source_artifact = Some(request.artifact.clone());

    let (tx, rx) = bounded(1);
    let source = request.source.clone();
    let cache_root = cache.root.clone();
    std::thread::spawn(move || {
        let _ = tx.send(decode_to_cache(&source, &cache_root));
    });

    state.pending_decode = Some(PendingDecode {
        artifact: request.artifact,
        source: request.source,
        rx,
    });
}

pub fn poll_decode(
    mut state: ResMut<PreviewState>,
    asset_server: Res<AssetServer>,
) {
    let Some(pending) = state.pending_decode.as_ref() else {
        return;
    };
    match pending.rx.try_recv() {
        Ok(Ok(decoded)) => {
            let pending = state.pending_decode.take().expect("pending");
            let handle = bevy_ext_gltf::load_gltf(&asset_server, decoded.relative);
            state.pending_scene = Some(PendingScene {
                artifact: pending.artifact,
                source: pending.source,
                handle,
            });
        }
        Ok(Err(err)) => {
            state.error = Some(err);
            state.pending_decode = None;
        }
        Err(crossbeam_channel::TryRecvError::Empty) => {}
        Err(crossbeam_channel::TryRecvError::Disconnected) => {
            state.error = Some("decode interrompido".into());
            state.pending_decode = None;
        }
    }
}

fn cache_key(source: &Path) -> String {
    let mut hasher = DefaultHasher::new();
    source.hash(&mut hasher);
    if let Ok(meta) = fs::metadata(source) {
        meta.len().hash(&mut hasher);
        if let Ok(modified) = meta.modified() {
            if let Ok(duration) = modified.duration_since(UNIX_EPOCH) {
                duration.as_nanos().hash(&mut hasher);
            }
        }
    }
    format!("{:016x}", hasher.finish())
}

fn sanitize_stem(path: &Path) -> String {
    let stem = path
        .file_stem()
        .and_then(|stem| stem.to_str())
        .unwrap_or("model");
    let cleaned: String = stem
        .chars()
        .map(|ch| {
            if ch.is_ascii_alphanumeric() || ch == '-' || ch == '_' {
                ch
            } else {
                '_'
            }
        })
        .collect();
    cleaned.trim_matches('_').to_string()
}

fn decode_to_cache(source: &Path, cache_root: &Path) -> Result<DecodedGlb, String> {
    let bytes = fs::read(source).map_err(|err| format!("ler {}: {err}", source.display()))?;
    let decoded = bevy_ext_gltf::decode_glb(&bytes)
        .map_err(|err| format!("decode {}: {err}", source.display()))?;
    let name = format!("{}-{}.glb", sanitize_stem(source), cache_key(source));
    let dir = cache_root.join("glb");
    fs::create_dir_all(&dir).map_err(|err| format!("criar {}: {err}", dir.display()))?;
    let absolute = dir.join(&name);
    if !absolute.is_file() {
        let tmp = dir.join(format!(".{name}.tmp-{}", std::process::id()));
        fs::write(&tmp, &decoded).map_err(|err| format!("escrever {}: {err}", tmp.display()))?;
        fs::rename(&tmp, &absolute)
            .map_err(|err| format!("mover {}: {err}", absolute.display()))?;
    }
    Ok(DecodedGlb {
        relative: format!("glb/{name}"),
    })
}
