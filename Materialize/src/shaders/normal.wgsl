// Normal from height (F2 final form): Sobel|Scharr operator, optional
// gaussian pre-filter on the height, physically scaled z component.

@group(0) @binding(0)
var height_texture: texture_2d<f32>;

@group(0) @binding(1)
var output_texture: texture_storage_2d<rgba8unorm, write>;

@group(1) @binding(0)
var<uniform> params: Params;

fn sample_height(coords: vec2<i32>) -> f32 {
    let dims = textureDimensions(height_texture);
    let c = wrap_or_clamp(coords, dims, params.seamless);
    return textureLoad(height_texture, c, 0).r;
}

// Separable gaussian pre-filter of the height (inline; σ is small by design).
fn smoothed(coords: vec2<i32>) -> f32 {
    let sigma = params.normal_prefilter_sigma;
    if (sigma < 0.05) {
        return sample_height(coords);
    }
    let r = i32(ceil(sigma * 3.0));

    // Vertical pass over horizontally-smoothed rows (full separable 2D).
    var v_acc = 0.0;
    var v_wsum = 0.0;
    for (var d = -r; d <= r; d++) {
        var row_acc = 0.0;
        var row_wsum = 0.0;
        for (var e = -r; e <= r; e++) {
            let w = exp(-f32(e * e) / (2.0 * sigma * sigma));
            row_acc += w * sample_height(coords + vec2<i32>(e, d));
            row_wsum += w;
        }
        let w = exp(-f32(d * d) / (2.0 * sigma * sigma));
        v_acc += w * (row_acc / row_wsum);
        v_wsum += w;
    }

    return v_acc / v_wsum;
}

// Central-difference gradient with the selected operator.
// Sobel  [[1,2,1],[0,0,0],[-1,-2,-1]] (raw response 8 per unit step)
// Scharr [[3,10,3],[0,0,0],[-3,-10,-3]] (raw 32; ÷4 keeps preset parity).
fn gradient(center: vec2<i32>) -> vec2<f32> {
    let s_m1_m1 = smoothed(center + vec2<i32>(-1, -1));
    let s_0_m1 = smoothed(center + vec2<i32>(0, -1));
    let s_1_m1 = smoothed(center + vec2<i32>(1, -1));
    let s_m1_0 = smoothed(center + vec2<i32>(-1, 0));
    let s_1_0 = smoothed(center + vec2<i32>(1, 0));
    let s_m1_1 = smoothed(center + vec2<i32>(-1, 1));
    let s_0_1 = smoothed(center + vec2<i32>(0, 1));
    let s_1_1 = smoothed(center + vec2<i32>(1, 1));

    if (params.normal_operator == 1u) {
        // Scharr (÷4 → same magnitude scale as Sobel, better rotational
        // invariance — Scharr 2000).
        let gx = (
            -3.0 * s_m1_m1 + 3.0 * s_1_m1
            - 10.0 * s_m1_0 + 10.0 * s_1_0
            - 3.0 * s_m1_1 + 3.0 * s_1_1
        ) / 4.0;
        let gy = (
            -3.0 * s_m1_m1 - 10.0 * s_0_m1 - 3.0 * s_1_m1
            + 3.0 * s_m1_1 + 10.0 * s_0_1 + 3.0 * s_1_1
        ) / 4.0;
        return vec2<f32>(gx, gy);
    }

    // Sobel — identical to the 2.0 kernel.
    let gx = -s_m1_m1 + s_1_m1 - 2.0 * s_m1_0 + 2.0 * s_1_0 - s_m1_1 + s_1_1;
    let gy = -s_m1_m1 - 2.0 * s_0_m1 - s_1_m1 + s_m1_1 + 2.0 * s_0_1 + s_1_1;
    return vec2<f32>(gx, gy);
}

@compute @workgroup_size(8, 8, 1)
fn main(@builtin(global_invocation_id) global_id: vec3<u32>) {
    let dims = textureDimensions(height_texture);
    let coords = vec2<i32>(global_id.xy);

    if (coords.x >= i32(dims.x) || coords.y >= i32(dims.y)) {
        return;
    }

    let g = gradient(coords);

    var gx = g.x * params.normal_strength;
    var gy = g.y * params.normal_strength;

    // 0 = OpenGL (Y up), 1 = DirectX (Y down).
    if (params.normal_flip_y == 1u) {
        gy = -gy;
    }

    var normal = vec3<f32>(-gx, -gy, max(params.normal_slope_z, 1e-4));
    normal = normalize(normal);

    let encoded = normal * 0.5 + 0.5;

    textureStore(output_texture, coords, vec4<f32>(encoded, 1.0));
}
