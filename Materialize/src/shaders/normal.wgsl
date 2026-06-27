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

fn sobel_gradient(center: vec2<i32>, dims: vec2<u32>) -> vec2<f32> {
    let h_m1_m1 = sample_height(center + vec2<i32>(-1, -1), dims);
    let h_0_m1 = sample_height(center + vec2<i32>(0, -1), dims);
    let h_1_m1 = sample_height(center + vec2<i32>(1, -1), dims);
    let h_m1_0 = sample_height(center + vec2<i32>(-1, 0), dims);
    let h_0_0 = sample_height(center + vec2<i32>(0, 0), dims);
    let h_1_0 = sample_height(center + vec2<i32>(1, 0), dims);
    let h_m1_1 = sample_height(center + vec2<i32>(-1, 1), dims);
    let h_0_1 = sample_height(center + vec2<i32>(0, 1), dims);
    let h_1_1 = sample_height(center + vec2<i32>(1, 1), dims);

    let gx = -h_m1_m1 + h_1_m1 - 2.0 * h_m1_0 + 2.0 * h_1_0 - h_m1_1 + h_1_1;
    let gy = -h_m1_m1 - 2.0 * h_0_m1 - h_1_m1 + h_m1_1 + 2.0 * h_0_1 + h_1_1;

    return vec2<f32>(gx, gy);
}

@compute @workgroup_size(8, 8, 1)
fn main(@builtin(global_invocation_id) global_id: vec3<u32>) {
    let dims = textureDimensions(height_texture);
    let coords = vec2<i32>(global_id.xy);

    if (coords.x >= i32(dims.x) || coords.y >= i32(dims.y)) {
        return;
    }

    let gradient = sobel_gradient(coords, dims);

    let scale = params.normal_strength;
    var gx = gradient.x * scale;
    var gy = gradient.y * scale;

    // 0 = OpenGL (Y up), 1 = DirectX (Y down).
    if (params.normal_flip_y == 1u) {
        gy = -gy;
    }

    var normal = vec3<f32>(-gx, -gy, 1.0);
    normal = normalize(normal);

    let encoded = normal * 0.5 + 0.5;

    textureStore(output_texture, coords, vec4<f32>(encoded, 1.0));
}
