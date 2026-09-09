//! day_tint para os materiais standard dos GltfScene — copas, casas, props
//! e personagens que os sistemas da relva/splat não alcançam (peça
//! exploração, r10 do gauntlet BOTW; mecanismo reportado e autorizado pela
//! lead).
//!
//! **Porquê**: `grass_daynight_tint` e `terrain_daynight_tint` escurecem os
//! MATERIAIS partilhados da relva e do splat ao anoitecer; os materiais dos
//! glTFs carregados por `<GltfScene>` não recebem passada nenhuma — ficam
//! com o albedo de dia sob o luar azul e leem-se como "recortes colados"
//! contra a relva em silhueta.
//!
//! **Como**: mesma curva `day_tint` da relva multiplicada no `base_color`
//! ORIGINAL de cada `StandardMaterial` (capturado à primeira vista e guardado,
//! para o tint nunca compor). Ficam de fora materiais `unlit` (marcadores) e
//! `emissive` ≠ preto (vidros de janela/lanterna têm de continuar acesos —
//! as pools quentes são as luzes de ponto, não o albedo).
//!
//! Seguro para materiais partilhados: a chave é o `AssetId` do material
//! (por ficheiro GLB — todas as instâncias da mesma copa partilham o tint,
//! como no VibeGame).

use std::collections::HashMap;

use bevy::asset::AssetId;
use bevy::color::LinearRgba;
use bevy::pbr::StandardMaterial;
use bevy::prelude::*;

use crate::grass::day_tint;
use crate::profiler::{Group, timed};

/// Originais de albedo capturados por material + último tint aplicado.
#[derive(Resource, Default)]
struct PropTintState {
    originals: HashMap<AssetId<StandardMaterial>, LinearRgba>,
    last_tint: Option<[f32; 3]>,
    /// Nº de materiais na última passada: um roster maior (glTF a carregar
    /// depois de o tint estabilizar) força uma passada fora do early-out de
    /// drift — sem isto, o prop novo ficava ao albedo de dia sob luar até o
    /// relógio mexer 1e-3.
    last_len: usize,
    throttle: f32,
}

pub struct PropTintPlugin;

impl Plugin for PropTintPlugin {
    fn build(&self, app: &mut App) {
        app.init_resource::<PropTintState>()
            .add_systems(Update, (timed(Group::Fx, prop_daynight_tint), patch_transmissive_gltf));
    }
}

/// Marcador inserido pelo spawn quando a URL do glTF contém "crystal"/"glass"
/// — os materiais da cena ganham transmissão especular quando o glTF carrega
/// (cristais que refratam o mundo + refletem o IBL do céu).
#[derive(Component)]
pub struct TransmissiveGltf;

/// Patch UMA VEZ por material dos glTFs marcados: `specular_transmission`
/// com IOR de quartzo e espessura de seixo — o
/// `ScreenSpaceTransmissionPlugin` (default na câmara 3D) refrata a cena por
/// trás e o env map do IBL preenche o resto. Sem o patch os cristais são
/// pedras azuis opacas.
fn patch_transmissive_gltf(
    mut commands: Commands,
    mut materials: ResMut<Assets<StandardMaterial>>,
    children: Query<&Children>,
    roots: Query<(Entity, &bevy::world_serialization::WorldAssetRoot, &TransmissiveGltf)>,
    mesh_materials: Query<&MeshMaterial3d<StandardMaterial>>,
    mut patched: Local<std::collections::HashSet<bevy::asset::AssetId<StandardMaterial>>>,
) {
    // O `WorldAssetRoot` só existe depois do gltf carregar e a cena spawnar
    // — a primeira passada vê a hierarquia completa; no fim o marcador sai
    // para não re-percorrer a árvore todos os frames.
    for (entity, _root, _marker) in roots.iter() {
        let mut stack = vec![entity];
        while let Some(current) = stack.pop() {
            let Ok(kids) = children.get(current) else {
                continue;
            };
            for &kid in kids {
                stack.push(kid);
                let Ok(handle) = mesh_materials.get(kid) else {
                    continue;
                };
                if !patched.insert(handle.id()) {
                    continue;
                }
                if let Some(mut material) = materials.get_mut(handle.id()) {
                    material.specular_transmission = 0.85;
                    material.ior = 1.45;
                    material.thickness = 0.6;
                    material.perceptual_roughness = 0.05;
                }
            }
        }
        commands.entity(entity).remove::<TransmissiveGltf>();
    }
}

/// Reaplica o tint de dia sobre o albedo ORIGINAL preservando o alpha ATUAL
/// do material (não o do original): o fade de cadáveres
/// (`combat::corpse_fade_tick`) desce `base_color.alpha` e um tint com alpha
/// 1 fazia o corpo morto voltar a opaco no tick seguinte do day/night.
/// Puro para testes.
fn tinted_base_color(original: LinearRgba, tint: [f32; 3], current_alpha: f32) -> Color {
    Color::linear_rgb(
        original.red * tint[0],
        original.green * tint[1],
        original.blue * tint[2],
    )
    .with_alpha(current_alpha)
}

/// Aplica a curva de noite da relva a todos os `StandardMaterial` (glTFs e
/// primitivas) que não sejam `unlit`/emissivos. Throttle 0,25 s + early-out
/// quando o factor de dia não mudou — o custo parado é uma subtração.
fn prop_daynight_tint(
    mut state: ResMut<PropTintState>,
    time: Res<Time>,
    clock: Option<Res<crate::worldsys::DayCycleState>>,
    mut materials: ResMut<Assets<StandardMaterial>>,
) {
    state.throttle -= time.delta_secs();
    if state.throttle > 0.0 {
        return;
    }
    state.throttle = 0.25;

    // ids são Copy — o Vec termina o borrow imutável antes do get_mut.
    let ids: Vec<_> = materials.ids().collect();
    let roster_changed = ids.len() != state.last_len;
    state.last_len = ids.len();

    let day = clock
        .as_deref()
        .map(|clock| {
            crate::worldsys::daylight_factor(
                clock.minute_of_day,
                clock.dawn_minute,
                clock.dusk_minute,
            )
        })
        .unwrap_or(1.0);
    let tint = day_tint(day);
    if let Some(last) = state.last_tint {
        if !roster_changed
            && (tint[0] - last[0]).abs() < 1e-3
            && (tint[1] - last[1]).abs() < 1e-3
            && (tint[2] - last[2]).abs() < 1e-3
        {
            return;
        }
    }

    for id in ids {
        let Some(mut material) = materials.get_mut(id) else {
            continue;
        };
        // Auto-iluminados ficam de fora: marcadores e vidros acesos.
        if material.unlit || material.emissive != LinearRgba::BLACK {
            continue;
        }
        let original = *state
            .originals
            .entry(id)
            .or_insert_with(|| material.base_color.to_linear());
        let current_alpha = material.base_color.to_linear().alpha;
        material.base_color = tinted_base_color(original, tint, current_alpha);
    }
    state.last_tint = Some(tint);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tint_at_noon_is_identity() {
        let noon = day_tint(1.0);
        assert!(noon.iter().all(|c| (c - 1.0).abs() < 1e-4));
    }

    #[test]
    fn tint_at_night_darkens_below_half() {
        let night = day_tint(0.0);
        assert!(night.iter().all(|c| *c < 0.5), "{night:?}");
    }

    /// R2-G8: o tint preserva o alpha ATUAL do material — o fade de
    /// cadáveres desce o alpha e a reescrita com alpha 1 o desfazia (o
    /// corpo "acendia" de novo a cada tick do day/night, throttle 0,25 s).
    #[test]
    fn tinted_base_color_preserves_current_alpha() {
        let original = LinearRgba::rgb(0.8, 0.4, 0.2);
        let night = day_tint(0.0);
        // Cadáver a meio do fade (alpha 0.35): o tint de noite mantém o 0.35.
        let tinted = tinted_base_color(original, night, 0.35);
        let linear = tinted.to_linear();
        assert!(
            (linear.alpha - 0.35).abs() < 1e-5,
            "alpha {:#?}",
            linear.alpha
        );
        // RGB multiplicado pelo tint sobre o ORIGINAL (não compõe).
        assert!((linear.red - original.red * night[0]).abs() < 1e-5);
        assert!((linear.green - original.green * night[1]).abs() < 1e-5);
        assert!((linear.blue - original.blue * night[2]).abs() < 1e-5);
        // Dia (tint identidade): cor volta ao original, alpha corrente fica.
        let noon = day_tint(1.0);
        let back = tinted_base_color(original, noon, 0.9).to_linear();
        assert!((back.red - original.red).abs() < 1e-5);
        assert!((back.alpha - 0.9).abs() < 1e-5);
    }
}
