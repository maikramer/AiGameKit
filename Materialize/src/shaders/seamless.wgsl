// Make-seamless pre-passes (F4), operating on the ROLLED diffuse before the
// height chain. Kwatra et al. 2003 (minimum-error boundary); linear tier =
// mirror-pair cross-fade; high tier = Poisson blend (Pérez et al. 2003)
// solved with Jacobi iterations on the band.

@group(0) @binding(0)
var src_tex: texture_2d<f32>;

@group(0) @binding(1)
var target_tex: texture_2d<f32>;

@group(0) @binding(2)
var source_tex: texture_2d<f32>;

@group(0) @binding(3)
var dst_tex: texture_storage_2d<rgba8unorm, write>;

@group(0) @binding(4)
var dst_f16_tex: texture_storage_2d<rgba16float, write>;

@group(1) @binding(0)
var<uniform> params: Params;

fn band_px(dims: vec2<u32>) -> i32 {
    let b = i32(params.seam_band);
    let cap = i32(min(dims.x, dims.y) / 4u);
    return clamp(b, 2, max(cap, 2));
}

// Fast tier: mirror-pair cross-fade. Pixel x in the left band blends with its
// mirror W−1−x across the seam, with weight u=0.5 at the border (both seam
// ends become the same value — exact C0 continuity) decaying to 0 at the band
// edge. X and Y applied in one pass; corners share the weights.
@compute @workgroup_size(8, 8, 1)
fn main_blend(@builtin(global_invocation_id) global_id: vec3<u32>) {
    let dims = textureDimensions(src_tex);
    let coords = vec2<i32>(global_id.xy);
    if (coords.x >= i32(dims.x) || coords.y >= i32(dims.y)) {
        return;
    }

    let b = band_px(dims);
    let d = vec2<i32>(dims);
    let w = d.x;
    let h = d.y;

    let c = textureLoad(src_tex, coords, 0).rgb;

    var ux = 0.0;
    var px = coords;
    if (coords.x < b) {
        px = vec2<i32>(w - 1 - coords.x, coords.y);
        ux = 0.5 * (1.0 - f32(coords.x) / f32(b - 1));
    } else if (coords.x >= w - b) {
        px = vec2<i32>(w - 1 - coords.x, coords.y);
        ux = 0.5 * (1.0 - f32(w - 1 - coords.x) / f32(b - 1));
    }

    var uy = 0.0;
    var py = coords;
    if (coords.y < b) {
        py = vec2<i32>(coords.x, h - 1 - coords.y);
        uy = 0.5 * (1.0 - f32(coords.y) / f32(b - 1));
    } else if (coords.y >= h - b) {
        py = vec2<i32>(coords.x, h - 1 - coords.y);
        uy = 0.5 * (1.0 - f32(h - 1 - coords.y) / f32(b - 1));
    }

    let cx = textureLoad(src_tex, px, 0).rgb;
    let cy = textureLoad(src_tex, py, 0).rgb;

    let own = max(1.0 - ux - uy, 0.0);
    let blended = clamp(own * c + ux * cx + uy * cy, vec3<f32>(0.0), vec3<f32>(1.0));

    textureStore(dst_tex, coords, vec4<f32>(blended, 1.0));
}

// rgba8 target → rgba16 iterate seed (band solver works in half-float).
@compute @workgroup_size(8, 8, 1)
fn main_upconvert(@builtin(global_invocation_id) global_id: vec3<u32>) {
    let dims = textureDimensions(src_tex);
    let coords = vec2<i32>(global_id.xy);
    if (coords.x >= i32(dims.x) || coords.y >= i32(dims.y)) {
        return;
    }
    let c = textureLoad(src_tex, coords, 0);
    textureStore(dst_f16_tex, coords, c);
}

fn wrap_c(c: vec2<i32>, dims: vec2<u32>) -> vec2<i32> {
    let d = vec2<i32>(dims);
    return ((c % d) + d) % d;
}

// High tier: one Jacobi step of Δf = ΔS inside the band, f = T outside
// (T = fast-blend target, S = rolled source). 48 steps converge on the band.
@compute @workgroup_size(8, 8, 1)
fn main_poisson(@builtin(global_invocation_id) global_id: vec3<u32>) {
    let dims = textureDimensions(src_tex);
    let coords = vec2<i32>(global_id.xy);
    if (coords.x >= i32(dims.x) || coords.y >= i32(dims.y)) {
        return;
    }

    let b = band_px(dims);
    let d = vec2<i32>(dims);
    let in_band = coords.x < b || coords.x >= d.x - b || coords.y < b || coords.y >= d.y - b;

    if (!in_band) {
        // Pinned to the target outside the band.
        let t = textureLoad(target_tex, coords, 0);
        textureStore(dst_f16_tex, coords, t);
        return;
    }

    let s_c = textureLoad(source_tex, coords, 0);
    let s_l = textureLoad(source_tex, wrap_c(coords + vec2<i32>(-1, 0), dims), 0);
    let s_r = textureLoad(source_tex, wrap_c(coords + vec2<i32>(1, 0), dims), 0);
    let s_t = textureLoad(source_tex, wrap_c(coords + vec2<i32>(0, -1), dims), 0);
    let s_b = textureLoad(source_tex, wrap_c(coords + vec2<i32>(0, 1), dims), 0);

    let f_l = textureLoad(src_tex, wrap_c(coords + vec2<i32>(-1, 0), dims), 0);
    let f_r = textureLoad(src_tex, wrap_c(coords + vec2<i32>(1, 0), dims), 0);
    let f_t = textureLoad(src_tex, wrap_c(coords + vec2<i32>(0, -1), dims), 0);
    let f_b = textureLoad(src_tex, wrap_c(coords + vec2<i32>(0, 1), dims), 0);

    // 4f = Σf_n + 4S_c − ΣS_n
    let sum_f = f_l + f_r + f_t + f_b;
    let sum_s = s_l + s_r + s_t + s_b;
    let next = (sum_f + 4.0 * s_c - sum_s) / 4.0;

    textureStore(dst_f16_tex, coords, vec4<f32>(next.rgb, 1.0));
}

// Final rgba16 iterate → rgba8 prepared diffuse for the downstream passes.
@compute @workgroup_size(8, 8, 1)
fn main_downconvert(@builtin(global_invocation_id) global_id: vec3<u32>) {
    let dims = textureDimensions(src_tex);
    let coords = vec2<i32>(global_id.xy);
    if (coords.x >= i32(dims.x) || coords.y >= i32(dims.y)) {
        return;
    }
    let c = textureLoad(src_tex, coords, 0);
    textureStore(dst_tex, coords, vec4<f32>(clamp(c.rgb, vec3<f32>(0.0), vec3<f32>(1.0)), 1.0));
}
