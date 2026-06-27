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
var input_texture: texture_2d<f32>;

@group(0) @binding(1)
var output_texture: texture_storage_2d<r32float, write>;

@group(1) @binding(0)
var<uniform> params: Params;

const LUM_WEIGHTS: vec3<f32> = vec3<f32>(0.2126, 0.7152, 0.0722);

fn sample_coord(coords: vec2<i32>, dims: vec2<u32>) -> vec2<i32> {
    let d = vec2<i32>(dims);
    if (params.seamless == 1u) {
        return ((coords % d) + d) % d;
    }
    return clamp(coords, vec2<i32>(0), d - vec2<i32>(1));
}

fn simple_blur(coords: vec2<i32>, dims: vec2<u32>, radius: i32) -> f32 {
    var sum = 0.0;
    var count = 0.0;

    for (var x = -radius; x <= radius; x++) {
        for (var y = -radius; y <= radius; y++) {
            let sample_coords = sample_coord(coords + vec2<i32>(x, y), dims);
            let color = textureLoad(input_texture, sample_coords, 0).rgb;
            sum += dot(color, LUM_WEIGHTS);
            count += 1.0;
        }
    }

    return sum / count;
}

@compute @workgroup_size(8, 8, 1)
fn main(@builtin(global_invocation_id) global_id: vec3<u32>) {
    let dims = textureDimensions(input_texture);
    let coords = vec2<i32>(global_id.xy);

    if (coords.x >= i32(dims.x) || coords.y >= i32(dims.y)) {
        return;
    }

    let r0 = i32(params.height_blur_radius_0);
    let r1 = i32(params.height_blur_radius_1);
    let r2 = i32(params.height_blur_radius_2);

    let h0 = simple_blur(coords, dims, r0);
    let h1 = simple_blur(coords, dims, r1);
    let h2 = simple_blur(coords, dims, r2);

    let height = h0 * 0.5 + h1 * 0.3 + h2 * 0.2;

    let contrasted = (height - 0.5) * params.height_contrast + 0.5;
    let final_height = clamp(contrasted, 0.0, 1.0);

    textureStore(output_texture, coords, vec4<f32>(final_height, 0.0, 0.0, 1.0));
}
