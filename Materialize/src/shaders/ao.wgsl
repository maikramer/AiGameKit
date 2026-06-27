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

fn sample_coord(coords: vec2<i32>, dims: vec2<u32>) -> vec2<i32> {
    let d = vec2<i32>(dims);
    if (params.seamless == 1u) {
        return ((coords % d) + d) % d;
    }
    return clamp(coords, vec2<i32>(0), d - vec2<i32>(1));
}

fn sample_height(coords: vec2<i32>, dims: vec2<u32>) -> f32 {
    let c = sample_coord(coords, dims);
    return textureLoad(height_texture, c, 0).r;
}

@compute @workgroup_size(8, 8, 1)
fn main(@builtin(global_invocation_id) global_id: vec3<u32>) {
    let dims = textureDimensions(height_texture);
    let coords = vec2<i32>(global_id.xy);

    if (coords.x >= i32(dims.x) || coords.y >= i32(dims.y)) {
        return;
    }

    let center_h = sample_height(coords, dims);
    var occlusion = 0.0;

    let h1_0 = sample_height(coords + vec2<i32>(1, 0), dims);
    let h2_0 = sample_height(coords + vec2<i32>(2, 0), dims);
    occlusion += max(0.0, h1_0 - center_h) * 1.0 + max(0.0, h2_0 - center_h) * 0.5;

    let h1_1 = sample_height(coords + vec2<i32>(1, 1), dims);
    let h2_1 = sample_height(coords + vec2<i32>(2, 2), dims);
    occlusion += max(0.0, h1_1 - center_h) * 1.0 + max(0.0, h2_1 - center_h) * 0.5;

    let h1_2 = sample_height(coords + vec2<i32>(0, 1), dims);
    let h2_2 = sample_height(coords + vec2<i32>(0, 2), dims);
    occlusion += max(0.0, h1_2 - center_h) * 1.0 + max(0.0, h2_2 - center_h) * 0.5;

    let h1_3 = sample_height(coords + vec2<i32>(-1, 1), dims);
    let h2_3 = sample_height(coords + vec2<i32>(-2, 2), dims);
    occlusion += max(0.0, h1_3 - center_h) * 1.0 + max(0.0, h2_3 - center_h) * 0.5;

    let h1_4 = sample_height(coords + vec2<i32>(-1, 0), dims);
    let h2_4 = sample_height(coords + vec2<i32>(-2, 0), dims);
    occlusion += max(0.0, h1_4 - center_h) * 1.0 + max(0.0, h2_4 - center_h) * 0.5;

    let h1_5 = sample_height(coords + vec2<i32>(-1, -1), dims);
    let h2_5 = sample_height(coords + vec2<i32>(-2, -2), dims);
    occlusion += max(0.0, h1_5 - center_h) * 1.0 + max(0.0, h2_5 - center_h) * 0.5;

    let h1_6 = sample_height(coords + vec2<i32>(0, -1), dims);
    let h2_6 = sample_height(coords + vec2<i32>(0, -2), dims);
    occlusion += max(0.0, h1_6 - center_h) * 1.0 + max(0.0, h2_6 - center_h) * 0.5;

    let h1_7 = sample_height(coords + vec2<i32>(1, -1), dims);
    let h2_7 = sample_height(coords + vec2<i32>(2, -2), dims);
    occlusion += max(0.0, h1_7 - center_h) * 1.0 + max(0.0, h2_7 - center_h) * 0.5;

    occlusion = occlusion / 12.0;
    occlusion = clamp(occlusion * params.ao_depth_scale, 0.0, 1.0);
    let ao = 1.0 - occlusion;

    textureStore(output_texture, coords, vec4<f32>(ao, ao, ao, 1.0));
}
