// Separable blur pass (F1): box or gaussian, horizontal (`main_h`) and
// vertical (`main_v`) entry points. Chain one of each per blur; σ² add under
// composition so a level of the pyramid = two σ0·√3 rounds.

@group(0) @binding(0)
var src_tex: texture_2d<f32>;

@group(0) @binding(1)
var dst_tex: texture_storage_2d<r32float, write>;

@group(1) @binding(0)
var<uniform> fparams: FilterParams;

fn sample_r(coords: vec2<i32>, dims: vec2<u32>) -> f32 {
    let c = wrap_or_clamp(coords, dims, fparams.seamless);
    return textureLoad(src_tex, c, 0).r;
}

fn blur_1d(coords: vec2<i32>, dims: vec2<u32>, axis: vec2<i32>) -> f32 {
    let r = i32(fparams.radius);
    if (r <= 0) {
        return textureLoad(src_tex, coords, 0).r;
    }
    if (fparams.kind == 0u) {
        var acc = textureLoad(src_tex, coords, 0).r;
        for (var d = 1; d <= r; d++) {
            let off = axis * d;
            acc += sample_r(coords + off, dims) + sample_r(coords - off, dims);
        }
        return acc / f32(2 * r + 1);
    }
    let s = max(fparams.sigma, 0.05);
    let inv_two_s2 = -0.5 / (s * s);
    var acc = textureLoad(src_tex, coords, 0).r;
    var wsum = 1.0;
    for (var d = 1; d <= r; d++) {
        let fd = f32(d);
        let w = exp(fd * fd * inv_two_s2);
        let off = axis * d;
        acc += w * (sample_r(coords + off, dims) + sample_r(coords - off, dims));
        wsum += 2.0 * w;
    }
    return acc / wsum;
}

@compute @workgroup_size(8, 8, 1)
fn main_h(@builtin(global_invocation_id) global_id: vec3<u32>) {
    let dims = textureDimensions(src_tex);
    let coords = vec2<i32>(global_id.xy);

    if (coords.x >= i32(dims.x) || coords.y >= i32(dims.y)) {
        return;
    }

    let val = blur_1d(coords, dims, vec2<i32>(1, 0));
    textureStore(dst_tex, coords, vec4<f32>(val, 0.0, 0.0, 1.0));
}

@compute @workgroup_size(8, 8, 1)
fn main_v(@builtin(global_invocation_id) global_id: vec3<u32>) {
    let dims = textureDimensions(src_tex);
    let coords = vec2<i32>(global_id.xy);

    if (coords.x >= i32(dims.x) || coords.y >= i32(dims.y)) {
        return;
    }

    let val = blur_1d(coords, dims, vec2<i32>(0, 1));
    textureStore(dst_tex, coords, vec4<f32>(val, 0.0, 0.0, 1.0));
}
