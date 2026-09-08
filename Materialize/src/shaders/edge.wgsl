// Crease/edge map (F0 fix + F2): total variation of the normal map — the
// full gradient magnitude over both normal components (2.0 used only 2 of the
// 4 components and missed diagonal creases).

@group(0) @binding(0)
var normal_texture: texture_2d<f32>;

@group(0) @binding(1)
var output_texture: texture_storage_2d<rgba8unorm, write>;

@group(1) @binding(0)
var<uniform> params: Params;

fn sample_normal_rg(coords: vec2<i32>) -> vec2<f32> {
    let dims = textureDimensions(normal_texture);
    let c = wrap_or_clamp(coords, dims, params.seamless);
    let n = textureLoad(normal_texture, c, 0);
    return vec2<f32>(n.r, n.g);
}

@compute @workgroup_size(8, 8, 1)
fn main(@builtin(global_invocation_id) global_id: vec3<u32>) {
    let dims = textureDimensions(normal_texture);
    let coords = vec2<i32>(global_id.xy);

    if (coords.x >= i32(dims.x) || coords.y >= i32(dims.y)) {
        return;
    }

    // Central difference of the 2D normal field; keep the full vector norm.
    let gx = sample_normal_rg(coords + vec2<i32>(1, 0)) -
             sample_normal_rg(coords + vec2<i32>(-1, 0));
    let gy = sample_normal_rg(coords + vec2<i32>(0, 1)) -
             sample_normal_rg(coords + vec2<i32>(0, -1));

    let mag = sqrt(dot(gx, gx) + dot(gy, gy));
    let edge = smoothstep(0.05, 0.40, mag * params.edge_contrast);

    textureStore(output_texture, coords, vec4<f32>(edge, edge, edge, 1.0));
}
