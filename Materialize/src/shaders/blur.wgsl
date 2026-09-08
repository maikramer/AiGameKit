// Separable blur pass (F1): box or gaussian, horizontal (`main_h`) and
// vertical (`main_v`) entry points. Chain one of each per blur; σ² add under
// composition so a level of the pyramid = two σ0·√3 rounds.

@group(0) @binding(0)
var src_tex: texture_2d<f32>;

@group(0) @binding(1)
var dst_tex: texture_storage_2d<r32float, write>;

@group(1) @binding(0)
var<uniform> fparams: FilterParams;

fn sample_r(coords: vec2<i32>) -> f32 {
    let dims = textureDimensions(src_tex);
    let c = wrap_or_clamp(coords, dims, fparams.seamless);
    return textureLoad(src_tex, c, 0).r;
}

fn kernel_weight(d: i32) -> f32 {
    if (fparams.kind == 0u) {
        return 1.0;
    }
    let s = max(fparams.sigma, 0.05);
    let fd = f32(d);
    return exp(-(fd * fd) / (2.0 * s * s));
}

@compute @workgroup_size(8, 8, 1)
fn main_h(@builtin(global_invocation_id) global_id: vec3<u32>) {
    let dims = textureDimensions(src_tex);
    let coords = vec2<i32>(global_id.xy);

    if (coords.x >= i32(dims.x) || coords.y >= i32(dims.y)) {
        return;
    }

    let r = i32(fparams.radius);
    var acc = 0.0;
    var wsum = 0.0;
    for (var d = -r; d <= r; d++) {
        let w = kernel_weight(d);
        acc += w * sample_r(coords + vec2<i32>(d, 0));
        wsum += w;
    }

    textureStore(dst_tex, coords, vec4<f32>(acc / wsum, 0.0, 0.0, 1.0));
}

@compute @workgroup_size(8, 8, 1)
fn main_v(@builtin(global_invocation_id) global_id: vec3<u32>) {
    let dims = textureDimensions(src_tex);
    let coords = vec2<i32>(global_id.xy);

    if (coords.x >= i32(dims.x) || coords.y >= i32(dims.y)) {
        return;
    }

    let r = i32(fparams.radius);
    var acc = 0.0;
    var wsum = 0.0;
    for (var d = -r; d <= r; d++) {
        let w = kernel_weight(d);
        acc += w * sample_r(coords + vec2<i32>(0, d));
        wsum += w;
    }

    textureStore(dst_tex, coords, vec4<f32>(acc / wsum, 0.0, 0.0, 1.0));
}
