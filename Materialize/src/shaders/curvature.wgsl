struct Params {
    height_blur_radius_0: f32,           // 1
    height_blur_radius_1: f32,           // 2
    height_blur_radius_2: f32,           // 3
    height_contrast: f32,                // 4
    normal_strength: f32,                // 5
    normal_flip_y: u32,                  // 6  (NEW 2.0)
    metallic_scale: f32,                 // 7
    metallic_local_variance_factor: f32, // 8  (NEW 2.0)
    smoothness_base: f32,                // 9
    smoothness_metallic_boost: f32,      // 10
    smoothness_roughness_factor: f32,    // 11 (NEW 2.0)
    edge_contrast: f32,                  // 12
    ao_depth_scale: f32,                 // 13
    seamless: u32,                       // 14 (NEW 2.0)
    _pad0: f32,                          // 15
    _pad1: f32,                          // 16
}

@group(0) @binding(0)
var height_texture: texture_2d<f32>;

@group(0) @binding(1)
var output_texture: texture_storage_2d<rgba8unorm, write>;

@group(1) @binding(0)
var<uniform> params: Params;

// Will be replaced by unified sample_coord helper in F2.4 (Sprint 3).
fn sample_height(coords: vec2<i32>, dims: vec2<u32>) -> f32 {
    let d = vec2<i32>(dims);
    var c = coords;
    if (params.seamless == 1u) {
        c = ((c % d) + d) % d;
    } else {
        c = clamp(c, vec2<i32>(0), d - vec2<i32>(1));
    }
    return textureLoad(height_texture, c, 0).r;
}

// Laplacian of height: convex (peaks) → <0.5, concave (valleys) → >0.5, flat → 0.5.
@compute @workgroup_size(8, 8, 1)
fn main(@builtin(global_invocation_id) global_id: vec3<u32>) {
    let dims = textureDimensions(height_texture);
    let coords = vec2<i32>(global_id.xy);

    if (coords.x >= i32(dims.x) || coords.y >= i32(dims.y)) {
        return;
    }

    let h = sample_height(coords, dims);
    let hl = sample_height(coords + vec2<i32>(-1, 0), dims);
    let hr = sample_height(coords + vec2<i32>(1, 0), dims);
    let ht = sample_height(coords + vec2<i32>(0, -1), dims);
    let hb = sample_height(coords + vec2<i32>(0, 1), dims);

    let laplacian = (hl + hr + ht + hb) - 4.0 * h;
    let curvature = clamp(laplacian * 8.0 + 0.5, 0.0, 1.0);

    textureStore(output_texture, coords, vec4<f32>(curvature, curvature, curvature, 1.0));
}
