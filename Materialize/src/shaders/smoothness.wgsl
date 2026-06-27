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
var diffuse_texture: texture_2d<f32>;

@group(0) @binding(1)
var metallic_texture: texture_2d<f32>;

@group(0) @binding(2)
var output_texture: texture_storage_2d<rgba8unorm, write>;

@group(1) @binding(0)
var<uniform> params: Params;

fn sample_coord(coords: vec2<i32>, dims: vec2<u32>) -> vec2<i32> {
    let d = vec2<i32>(dims);
    if (params.seamless == 1u) {
        return ((coords % d) + d) % d;
    }
    return clamp(coords, vec2<i32>(0), d - vec2<i32>(1));
}

fn luma_at(coords: vec2<i32>, dims: vec2<u32>) -> f32 {
    let c = sample_coord(coords, dims);
    let rgb = textureLoad(diffuse_texture, c, 0).rgb;
    return dot(rgb, vec3<f32>(0.2126, 0.7152, 0.0722));
}

// 5×5 luminance variance, scaled to ~[0,1]. Textured regions (rough) → high;
// polished regions (smooth) → low.
fn local_contrast_5x5(center: vec2<i32>, dims: vec2<u32>) -> f32 {
    var sum = 0.0;
    let n = 25.0;
    for (var dy = -2; dy <= 2; dy++) {
        for (var dx = -2; dx <= 2; dx++) {
            sum += luma_at(center + vec2<i32>(dx, dy), dims);
        }
    }
    let mean = sum / n;
    var acc = 0.0;
    for (var dy = -2; dy <= 2; dy++) {
        for (var dx = -2; dx <= 2; dx++) {
            let luma = luma_at(center + vec2<i32>(dx, dy), dims);
            acc += (luma - mean) * (luma - mean);
        }
    }
    return clamp((acc / n) * 8.0, 0.0, 1.0);
}

@compute @workgroup_size(8, 8, 1)
fn main(@builtin(global_invocation_id) global_id: vec3<u32>) {
    let dims = textureDimensions(diffuse_texture);
    let coords = vec2<i32>(global_id.xy);

    if (coords.x >= i32(dims.x) || coords.y >= i32(dims.y)) {
        return;
    }

    let metallic = textureLoad(metallic_texture, coords, 0).r;
    let lc = local_contrast_5x5(coords, dims);

    let smoothness = clamp(
        params.smoothness_base
            + params.smoothness_metallic_boost * metallic
            - params.smoothness_roughness_factor * lc,
        0.0,
        1.0,
    );

    textureStore(output_texture, coords, vec4<f32>(smoothness, smoothness, smoothness, 1.0));
}
