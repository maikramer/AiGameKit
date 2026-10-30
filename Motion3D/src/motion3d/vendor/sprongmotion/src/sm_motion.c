/* Motions and BVH text. */
#include "sm_internal.h"

const char *const sm_joint_names[SM_J] = {
    "pelvis", "l_hip", "r_hip", "spine1", "l_knee", "r_knee", "spine2", "l_ankle", "r_ankle", "spine3", "l_foot",
    "r_foot", "neck", "l_collar", "r_collar", "head", "l_shoulder", "r_shoulder", "l_elbow", "r_elbow", "l_wrist",
    "r_wrist",
};

#define MAX_TOKENS 6000   /* 10 minutes */
#ifdef __wasm32__
_Static_assert(sizeof(sm_model) == 52 && sizeof(sm_motion) == 68, "examples/05_wasm.mjs reads these sizes");
#endif

sm_status sm_motion_create(const sm_model_impl *m, sm_motion_impl **out) {
    sm_motion_impl *mo = sm_alloc(&m->h, sizeof *mo);
    if (!mo) return SM_E_NOMEM;
    memset(mo, 0, sizeof *mo);
    mo->m = m;
    mo->seed = -1;
    *out = mo;
    return SM_OK;
}

void sm_motion_free(sm_motion_impl *mo) {
    if (!mo) return;
    sm_dealloc(&mo->m->h, mo->block_starts);
    sm_dealloc(&mo->m->h, mo->mem);
    sm_dealloc(&mo->m->h, mo);
}

/* one block per capacity; the floats first, then codes, then contacts */
sm_status sm_motion_fit(sm_motion_impl *mo, uint32_t tokens) {
    if (tokens <= mo->cap) return SM_OK;
    if (tokens > MAX_TOKENS) return SM_E_ARG;
    const size_t T = 2 * (size_t)tokens, floats = T * (SM_F + SM_J * 3 + SM_J * 4 + 4) + tokens;
    float *f = sm_alloc(&mo->m->h, sizeof(float) * floats + sizeof(int16_t) * tokens * SM_VQ_LAYERS + T * 4);
    if (!f) return SM_E_NOMEM;
    sm_dealloc(&mo->m->h, mo->mem);
    mo->mem = mo->feat = f;
    mo->joints = mo->feat + T * SM_F;
    mo->rot = mo->joints + T * SM_J * 3;
    mo->root = mo->rot + T * SM_J * 4;
    mo->speed = mo->root + T * 3;
    mo->conf = mo->speed + T;
    mo->codes = (int16_t *)(mo->conf + tokens);
    mo->contacts = (uint8_t *)(mo->codes + (size_t)tokens * SM_VQ_LAYERS);
    mo->cap = tokens;
    mo->tokens = mo->frames = 0;
    return SM_OK;
}

sm_motion sm_wrap_motion(sm_motion_impl *mo) {
    if (!mo) return (sm_motion){0};
    return (sm_motion){
        .frame_count = (int)mo->frames, .fps = (int)mo->m->fps, .seed = mo->seed, .joints = mo->joints,
        .rotations = mo->rot, .offsets = mo->off, .root = mo->root, .speed = mo->speed, .contacts = mo->contacts,
        .features = mo->feat, .codes = mo->codes, .confidence = mo->conf, .token_count = (int)mo->tokens,
        .path_error = mo->path_err, .block_start_frames = mo->block_starts, .block_count = (int)mo->n_blocks,
        .internal = mo,
    };
}

bool sm_is_motion_valid(sm_motion motion) { return motion.internal != NULL; }
void sm_unload_motion(sm_motion motion) { sm_motion_free(motion.internal); }

static void remove_travel(sm_motion_impl *mo) {
    for (size_t i = 0; i < (size_t)mo->frames * SM_J; i++) {
        mo->joints[3 * i] -= mo->root[3 * (i / SM_J)];
        mo->joints[3 * i + 2] -= mo->root[3 * (i / SM_J) + 2];
    }
}

void sm_motion_post(sm_motion_impl *mo, const sm_track *tr, const double *v, const sm_path *path, double *tmp) {
    const uint32_t T = mo->frames;
    const double fps = mo->m->fps;
    double *yaw_added = tmp + 3 * (size_t)T, *smooth_root = yaw_added + T;
    sm_track track = *tr;
    sm_features_to_joints(mo->feat, T, &track, mo->joints);
    mo->end = track;

    for (size_t t = 0; t < T; t++)
        for (size_t k = 0; k < 4; k++) mo->contacts[4 * t + k] = mo->feat[t * SM_F + SM_F_CONTACT + k] > 0.5f;
    sm_fill_contacts(mo->joints, mo->contacts, T, fps);
    sm_root_smooth(mo->joints, T, fps, smooth_root, mo->speed);

    const int has_path = path && path->n, warp = has_path && v[SM_P_PATH_WARP] != 0;
    mo->path_err = -1;
    if (has_path)
        mo->path_err = (float)sm_path_warp(path, mo->joints, T, fps, smooth_root, warp, yaw_added,
                                           smooth_root + 2 * (size_t)T);
    for (size_t t = 0; t < T; t++) memcpy(mo->root + 3 * t, mo->joints + t * SM_J * 3, sizeof(float) * 3);

    if (v[SM_P_FOOT_LOCK] != 0) sm_foot_lock(mo->joints, mo->contacts, T, tmp, warp ? yaw_added : NULL);
    sm_rotations(mo->joints, T, mo->m->rest, mo->rot);
    sm_clip_offsets(mo->joints, mo->rot, T, tmp, mo->off);
    if (v[SM_P_IN_PLACE] != 0) remove_travel(mo);
}

void sm_puts(sm_buf *b, const char *s) {
    for (; *s; s++, b->n++)
        if (b->p && b->n < b->cap) b->p[b->n] = *s;
}

void sm_putn(sm_buf *b, double v) {   /* at most 6 decimals, no trailing zeros */
    const double a = fabs(v) * 1e6 + 0.5;
    const uint64_t u = a < 9e18 ? (uint64_t)a : 0;
    uint64_t f = u % 1000000, i = u / 1000000;
    char s[40], *p = s + sizeof s;
    *--p = 0;
    int digits = 6;
    while (digits && f % 10 == 0) {
        f /= 10;
        digits--;
    }
    for (; digits; digits--, f /= 10) *--p = (char)('0' + f % 10);
    if (*p) *--p = '.';
    do *--p = (char)('0' + i % 10);
    while (i /= 10);
    if (v < 0 && u) *--p = '-';
    sm_puts(b, p);
}

#define DEGREES 57.29577951308232   /* per radian */

static const uint8_t dfs[SM_J] = {0, 1, 4, 7, 10, 2, 5, 8, 11, 3, 6, 9, 12, 15, 13, 16, 18, 20, 14, 17, 19, 21};

static void line(sm_buf *b, int depth, const char *s) {
    for (int i = 0; i < depth; i++) sm_puts(b, "  ");
    sm_puts(b, s);
}

static void hierarchy(const sm_motion_impl *mo, double scale, sm_buf *b) {
    int depth[SM_J], open = 0;
    sm_puts(b, "HIERARCHY\n");
    for (int i = 0; i < SM_J; i++) {
        const int j = dfs[i], leaf = i + 1 == SM_J || sm_parent[dfs[i + 1]] != j;
        depth[j] = j ? depth[sm_parent[j]] + 1 : 0;
        for (; open > depth[j]; open--) line(b, open - 1, "}\n");
        line(b, depth[j], j ? "JOINT " : "ROOT ");
        sm_puts(b, sm_joint_names[j]);
        sm_puts(b, "\n");
        line(b, depth[j], "{\n");
        line(b, depth[j] + 1, "OFFSET");
        for (int k = 0; k < 3; k++) {
            sm_puts(b, " ");
            sm_putn(b, mo->off[3 * j + k] * scale);
        }
        sm_puts(b, j ? "\n" : "\n  CHANNELS 6 Xposition Yposition Zposition Zrotation Yrotation Xrotation\n");
        if (j) line(b, depth[j] + 1, "CHANNELS 3 Zrotation Yrotation Xrotation\n");
        const char *end[4] = {"End Site\n", "{\n", "  OFFSET 0 0 0\n", "}\n"};
        for (int k = 0; leaf && k < 4; k++) line(b, depth[j] + 1, end[k]);
        open = depth[j] + 1;
    }
    for (; open > 0; open--) line(b, open - 1, "}\n");
}

/* R = Rz(a) Ry(b) Rx(c) from x y z w, degrees; each angle the nearest turn to the previous frame's */
static void euler_zyx(const float *q, double *e) {
    const double x = q[0], y = q[1], z = q[2], w = q[3], r20 = 2 * (x * z - y * w);
    const double a = atan2(2 * (x * y + z * w), 1 - 2 * (y * y + z * z));
    const double b = asin(r20 > 1 ? -1 : r20 < -1 ? 1 : -r20), c = atan2(2 * (y * z + x * w), 1 - 2 * (x * x + y * y));
    const double next[3] = {a * DEGREES, b * DEGREES, c * DEGREES};
    for (int k = 0; k < 3; k++) e[k] = next[k] + 360 * round((e[k] - next[k]) / 360);
}

static void frames(const sm_motion_impl *mo, double scale, sm_buf *b) {
    double angles[SM_J][3] = {{0}};
    sm_puts(b, "MOTION\nFrames: ");
    sm_putn(b, mo->frames);
    sm_puts(b, "\nFrame Time: ");
    sm_putn(b, 1.0 / mo->m->fps);
    for (size_t t = 0; t < mo->frames; t++) {
        sm_puts(b, "\n");
        for (int k = 0; k < 3; k++) {
            sm_putn(b, mo->joints[t * SM_J * 3 + (size_t)k] * scale);
            sm_puts(b, " ");
        }
        for (int i = 0; i < SM_J; i++) {
            const int j = dfs[i];
            euler_zyx(mo->rot + (t * SM_J + (size_t)j) * 4, angles[j]);
            for (int k = 0; k < 3; k++) {
                sm_putn(b, angles[j][k]);
                sm_puts(b, i + 1 < SM_J || k < 2 ? " " : "");
            }
        }
    }
    sm_puts(b, "\n");
}

static void write_bvh(const sm_motion_impl *mo, double scale, sm_buf *b) {
    hierarchy(mo, scale, b);
    frames(mo, scale, b);
}

char *sm_export_motion_bvh_text(sm_motion motion, float scale) {
    const sm_motion_impl *mo = motion.internal;
    if (!sm_check_input(mo, "the motion is empty")) return NULL;
    if (!(scale > 0 && scale <= 1e4f)) {
        sm_set_error("scale is not in 0 .. 10000");
        return NULL;
    }
    sm_buf b = {NULL, 0, 0};
    write_bvh(mo, scale, &b);
    char *text = sm_alloc_for_caller(&mo->m->h, b.n + 1);
    if (!text) {
        sm_set_error("out of memory");
        return NULL;
    }
    b = (sm_buf){text, b.n + 1, 0};
    write_bvh(mo, scale, &b);
    text[b.n] = 0;
    return text;
}

bool sm_export_motion_bvh(sm_motion motion, const char *file_name) {
    char *text = sm_export_motion_bvh_text(motion, 100);
    if (!text) return false;
    FILE *f = file_name ? sm_open_file(file_name, "wb") : NULL;
    const size_t n = strlen(text);
    const bool ok = f && fwrite(text, 1, n, f) == n;
    if (f && fclose(f)) sm_set_error("cannot write the file");
    if (!ok) sm_set_error("cannot write the file");
    sm_free(text);
    return ok && !sm_get_error()[0];
}
