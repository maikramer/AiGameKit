//! Mipmaps + samplers for loaded world textures — THE single writer.
//!
//! Bevy's image loader does NOT generate mipmaps for plain PNG/JPG files
//! (only KTX2/DDS ship them) and the default sampler ships with
//! `anisotropy_clamp: 1`. World-space tiled ground textures (the simple-rpg
//! terrain tiles `vale_grass.png` every 5 m) then alias into crawling pixel
//! speckle — worst when the camera moves. This module generates a
//! box-filter mip chain CPU-side for uncompressed 4-byte-per-pixel images on
//! load, and raises the sampler anisotropy to 8.
//!
//! **Sampler ownership lives HERE and only here.** Two systems used to write
//! the same image's sampler: the mip patch below (ClampToEdge + aniso 8) and
//! `terrain::drop_failed_terrain_textures` (REPEAT). They run unordered in
//! `Update`, the render world extracts whatever state wins the race, and one
//! run in two shipped a ClampToEdge ground texture — with world/meter UVs
//! that stretches the border texel into long streaks ("estrada e relva
//! quebradas"). The fix is one registry ([`WorldTiledTextures`], filled by
//! the terrain bootstrap at `server.load` time, i.e. strictly before the
//! `Added` event can fire) and one writer: [`patch_image`] decides the
//! final sampler in a single pass. Do NOT mutate `image.sampler` elsewhere.

use std::collections::HashSet;

use bevy::asset::{AssetEvent, AssetId, Assets};
use bevy::image::{Image, ImageAddressMode, ImageSampler, ImageSamplerDescriptor};
use bevy::prelude::*;

/// Asset ids whose textures use world-space tiled UVs (terrain chunks, road
/// ribbons, junction discs, ground decals — meter-unit UVs that need REPEAT).
///
/// The terrain bootstrap registers each id in the same statement that calls
/// `AssetServer::load`, so the registration is visible to
/// [`generate_mipmaps_on_load`] before the image's `Added` event is ever
/// read: the first (and only) patch already carries the tiled sampler.
#[derive(Resource, Default)]
pub struct WorldTiledTextures(HashSet<AssetId<Image>>);

impl WorldTiledTextures {
    /// Marks a texture id as world-tiled. Idempotent.
    pub fn register(&mut self, id: AssetId<Image>) {
        self.0.insert(id);
    }

    /// True when `id` was registered as world-tiled.
    pub fn contains(&self, id: AssetId<Image>) -> bool {
        self.0.contains(&id)
    }
}

/// Plugin: registry resource + the one load-patch system.
pub struct TexturesPlugin;

impl Plugin for TexturesPlugin {
    fn build(&self, app: &mut App) {
        app.init_resource::<WorldTiledTextures>()
            .add_systems(bevy::app::Update, generate_mipmaps_on_load);
    }
}

/// Loads a world-tiled texture and registers its id in the same statement —
/// registration has to be visible before the image's `Added` event is ever
/// read, or the first patch would settle a ClampToEdge sampler and the
/// world-space UVs would smear into streaks. Every world-space-UV load site
/// (terrain chunks, road ribbons, junction discs, ground decals, tiled
/// primitives) goes through this.
pub fn load_tiled_image(
    server: &AssetServer,
    tiled: &mut WorldTiledTextures,
    url: &str,
) -> Handle<Image> {
    // strip leading '/' — bevy treats root-absolute asset paths as unapproved
    let handle: Handle<Image> = server.load(url.trim_start_matches('/').to_string());
    tiled.register(handle.id());
    handle
}

/// Patch every freshly-loaded image: add a mip chain when the file has none
/// and settle its sampler (tiled → REPEAT, plain → aniso 8 clamp).
pub fn generate_mipmaps_on_load(
    mut events: MessageReader<AssetEvent<Image>>,
    tiled: Res<WorldTiledTextures>,
    mut images: ResMut<Assets<Image>>,
) {
    for event in events.read() {
        match event {
            AssetEvent::Added { id } | AssetEvent::LoadedWithDependencies { id } => {
                if let Some(image) = images.get_mut(*id) {
                    // into_inner marca o asset como modificado → o render
                    // re-extrai e re-faz o upload agora com a cadeia de mips.
                    patch_image(image.into_inner(), tiled.contains(*id));
                }
            }
            _ => {}
        }
    }
}

/// Sampler for textures with world-space (meter) UVs (`world/tile`, road
/// `arc/scale`): sem REPEAT os UVs > 1.0 clampeiam na borda e a textura
/// estica nos riscos do texel da borda. Linear + anisotropy 8 — wgpu exige
/// filtros lineares com aniso > 1.
pub fn world_tiled_sampler() -> ImageSampler {
    ImageSampler::Descriptor(ImageSamplerDescriptor {
        address_mode_u: ImageAddressMode::Repeat,
        address_mode_v: ImageAddressMode::Repeat,
        anisotropy_clamp: 8,
        ..ImageSamplerDescriptor::linear()
    })
}

/// True when the format is plain uncompressed RGBA/BGRA (1×1 blocks, 4
/// bytes) — the only layouts the CPU box filter here understands.
fn is_plain_rgba(format: bevy::render::render_resource::TextureFormat) -> bool {
    use bevy::render::render_resource::TextureFormat;
    matches!(
        format,
        TextureFormat::Rgba8UnormSrgb
            | TextureFormat::Rgba8Unorm
            | TextureFormat::Bgra8UnormSrgb
            | TextureFormat::Bgra8Unorm
    )
}

/// Patch a freshly-loaded image.
///
/// * `world_tiled` — the image was registered in [`WorldTiledTextures`]: its
///   sampler becomes [`world_tiled_sampler`] unconditionally (even if the
///   file ships its own mips).
/// * Otherwise: append a box-filter mip chain when the file has a single
///   level and set the linear + aniso 8 sampler. Images that already carry
///   mips (KTX2/DDS) and exotic formats pass through untouched.
pub fn patch_image(image: &mut Image, world_tiled: bool) {
    if world_tiled {
        image.sampler = world_tiled_sampler();
    }
    if image.texture_descriptor.mip_level_count > 1 {
        return;
    }
    // Render targets (o viewport do ViberEditor, câmaras render-to-texture)
    // têm de ficar com 1 nível: o wgpu recusa uma view com vários mips como
    // color attachment ("texture view is not renderable").
    if image
        .texture_descriptor
        .usage
        .contains(bevy::render::render_resource::TextureUsages::RENDER_ATTACHMENT)
    {
        return;
    }
    if !is_plain_rgba(image.texture_descriptor.format) {
        return;
    }
    let Some(data) = image.data.as_ref() else {
        return;
    };
    let width = image.width();
    let height = image.height();
    if width == 0 || height == 0 {
        return;
    }
    let mut levels = Vec::new();
    levels.push(data.clone());
    let mut lw = width;
    let mut lh = height;
    while (lw > 1 || lh > 1) && levels.len() < 12 {
        let (nw, nh) = ((lw / 2).max(1), (lh / 2).max(1));
        let prev = levels.last().expect("levels is non-empty");
        let mut next = vec![0u8; (nw * nh * 4) as usize];
        for y in 0..nh {
            for x in 0..nw {
                for c in 0..4 {
                    let sx = (x * 2).min(lw - 1);
                    let sy = (y * 2).min(lh - 1);
                    let sx1 = (sx + 1).min(lw - 1);
                    let sy1 = (sy + 1).min(lh - 1);
                    let px = |xx: u32, yy: u32| ((yy * lw + xx) * 4 + c) as usize;
                    next[((y * nw + x) * 4 + c) as usize] = ((prev[px(sx, sy)] as u32
                        + prev[px(sx1, sy)] as u32
                        + prev[px(sx, sy1)] as u32
                        + prev[px(sx1, sy1)] as u32)
                        / 4) as u8;
                }
            }
        }
        levels.push(next);
        lw = nw;
        lh = nh;
    }
    if levels.len() <= 1 {
        return;
    }
    let total: usize = levels.iter().map(Vec::len).sum();
    let mut chain = Vec::with_capacity(total);
    for level in &levels {
        chain.extend_from_slice(level);
    }
    image.data = Some(chain);
    image.texture_descriptor.mip_level_count = levels.len() as u32;
    // Filtragem anisotrópica nas texturas de chão: em ângulo rasant o mip
    // único de maior compressão é que era amostrado, e é isso que cintila.
    // (Tiled já recebe aniso 8 via `world_tiled_sampler` lá em cima.)
    if !world_tiled {
        image.sampler = ImageSampler::Descriptor(ImageSamplerDescriptor {
            anisotropy_clamp: 8,
            ..ImageSamplerDescriptor::linear()
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use bevy::asset::RenderAssetUsages;
    use bevy::render::render_resource::{
        Extent3d, TextureDescriptor, TextureDimension, TextureFormat, TextureUsages,
    };

    fn rgba_image(width: u32, height: u32, fill: [u8; 4]) -> Image {
        let len = (width * height * 4) as usize;
        Image {
            data: Some(vec_of(fill, len)),
            data_order: Default::default(),
            texture_descriptor: TextureDescriptor {
                label: None,
                size: Extent3d {
                    width,
                    height,
                    depth_or_array_layers: 1,
                },
                mip_level_count: 1,
                sample_count: 1,
                dimension: TextureDimension::D2,
                format: TextureFormat::Rgba8UnormSrgb,
                usage: TextureUsages::TEXTURE_BINDING | TextureUsages::COPY_DST,
                view_formats: &[],
            },
            sampler: ImageSampler::Default,
            texture_view_descriptor: None,
            asset_usage: RenderAssetUsages::default(),
            copy_on_resize: false,
        }
    }

    fn vec_of(fill: [u8; 4], len: usize) -> Vec<u8> {
        let mut v = Vec::with_capacity(len);
        for i in 0..len {
            v.push(fill[i % 4]);
        }
        v
    }

    /// Sampler de uma imagem patched: address mode e anisotropia efectivos.
    fn sampler_of(image: &Image) -> (ImageAddressMode, u16) {
        match &image.sampler {
            ImageSampler::Descriptor(d) => (d.address_mode_u, d.anisotropy_clamp),
            ImageSampler::Default => panic!("sampler should be an explicit descriptor"),
        }
    }

    /// Sólido: a cadeia de mips preserva a cor e produz os níveis esperados.
    #[test]
    fn test_mip_chain_on_solid_color() {
        let mut image = rgba_image(8, 4, [255, 0, 0, 255]);
        patch_image(&mut image, false);
        // 8x4 → 4x2 → 2x1 → 1x1
        assert_eq!(image.texture_descriptor.mip_level_count, 4);
        let data = image.data.as_ref().expect("data present");
        assert_eq!(data.len(), (32 + 8 + 2 + 1) * 4);
        assert!(data.chunks(4).all(|px| px == [255, 0, 0, 255]));
    }

    /// Já tem mips (KTX2/DDS): passa intacto.
    #[test]
    fn test_patch_skips_render_targets() {
        let mut image = Image::new_target_texture(
            64,
            64,
            bevy::render::render_resource::TextureFormat::Bgra8UnormSrgb,
            None,
        );
        patch_image(&mut image, false);
        assert_eq!(image.texture_descriptor.mip_level_count, 1);
    }

    #[test]
    fn test_patch_skips_existing_mips() {
        let mut image = rgba_image(4, 4, [1, 2, 3, 4]);
        image.texture_descriptor.mip_level_count = 3;
        let before = image.data.clone();
        patch_image(&mut image, false);
        assert_eq!(image.data, before);
        assert_eq!(image.texture_descriptor.mip_level_count, 3);
    }

    /// Formatos comprimidos/não-RGBA passam intactos.
    #[test]
    fn test_patch_skips_unsupported_format() {
        use bevy::render::render_resource::TextureFormat;
        let mut image = rgba_image(4, 4, [0; 4]);
        image.texture_descriptor.format = TextureFormat::R16Uint;
        let before = image.data.clone();
        patch_image(&mut image, false);
        assert_eq!(image.data, before);
        assert_eq!(image.texture_descriptor.mip_level_count, 1);
        assert!(is_plain_rgba(TextureFormat::Rgba8UnormSrgb));
        assert!(!is_plain_rgba(TextureFormat::R16Uint));
    }

    /// 1×1: sem cadeia a acrescentar, permanece com um nível.
    #[test]
    fn test_patch_1x1_is_noop() {
        let mut image = rgba_image(1, 1, [9, 9, 9, 255]);
        patch_image(&mut image, false);
        assert_eq!(image.texture_descriptor.mip_level_count, 1);
    }

    /// World-tiled: REPEAT em u/v + aniso 8 + cadeia de mips, tudo no
    /// primeiro (e único) patch — é isto que remove a corrida de samplers.
    #[test]
    fn test_patch_world_tiled_gets_repeat_sampler_and_mips() {
        let mut image = rgba_image(8, 4, [255, 0, 0, 255]);
        patch_image(&mut image, true);
        assert_eq!(image.texture_descriptor.mip_level_count, 4);
        assert_eq!(sampler_of(&image), (ImageAddressMode::Repeat, 8));
        // v acompanha u — sem isto um repeat só num eixo estica a outra dim.
        if let ImageSampler::Descriptor(d) = &image.sampler {
            assert_eq!(d.address_mode_v, ImageAddressMode::Repeat);
        }
    }

    /// World-tiled com mips próprios (hipotético KTX2 de chão): o sampler
    /// tiled aplica-se NA MESMA, e os dados ficam intactos.
    #[test]
    fn test_patch_world_tiled_resamples_even_with_mips() {
        let mut image = rgba_image(4, 4, [1, 2, 3, 4]);
        image.texture_descriptor.mip_level_count = 3;
        let before = image.data.clone();
        patch_image(&mut image, true);
        assert_eq!(image.data, before);
        assert_eq!(image.texture_descriptor.mip_level_count, 3);
        assert_eq!(sampler_of(&image), (ImageAddressMode::Repeat, 8));
    }

    /// Não-tiled mantém ClampToEdge (default de `linear()`): GLBs e afins
    /// não devem ganhar REPEAT só porque o chão precisa.
    #[test]
    fn test_patch_plain_keeps_clamp_address_mode() {
        let mut image = rgba_image(8, 4, [255, 0, 0, 255]);
        patch_image(&mut image, false);
        assert_eq!(sampler_of(&image), (ImageAddressMode::ClampToEdge, 8));
    }
}
