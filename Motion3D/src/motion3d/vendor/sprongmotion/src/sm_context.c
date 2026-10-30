/* Contexts (scratch memory and one call's state), text encoding, generating and decoding motions. */
#include "sm_internal.h"

#define FADE 8   /* frames over which a prefix clip's end fades into the new one */

sm_status sm_ctx_fail(sm_ctx *c, sm_status s, const char *msg) { return sm_fail(c->err, sizeof c->err, s, msg); }
sm_status sm_ctx_fail_range(sm_ctx *c, const char *name, double v, double lo, double hi) {
    return sm_fail_range(c->err, sizeof c->err, SM_E_ARG, name, v, lo, hi);
}

static sm_status grow(const sm_model_impl *m, void **p, size_t *have, size_t need) {
    if (need <= *have) return SM_OK;
    void *q = sm_alloc(&m->h, need);
    if (!q) return SM_E_NOMEM;
    sm_dealloc(&m->h, *p);
    *p = q;
    *have = need;
    return SM_OK;
}

/* room for generating L tokens and decoding Lw */
sm_status sm_ctx_fit(sm_ctx *c, uint32_t L, uint32_t Lw) {
    const sm_model_impl *m = c->m;
    const size_t T = 2 * (size_t)Lw, gen = m->gen.layers && L ? sm_gen_scratch_floats(&m->gen, m->tok.max_len, L) : 0;
    const size_t text = m->gen.layers ? sm_text_scratch_floats(&m->text) : 0;
    const size_t dec = sizeof(float) * (sm_vq_scratch_floats(Lw) + T * SM_F) + sizeof(double) * SM_POST_TMP(T) +
                       sizeof(int16_t) * Lw * SM_VQ_LAYERS;
    sm_status s = grow(m, &c->scratch, &c->n_scratch, sizeof(float) * (gen > text ? gen : text));
    if (s == SM_OK) s = grow(m, &c->dec, &c->n_dec, dec);
    if (s != SM_OK) return sm_ctx_fail(c, s, "out of memory");
    return SM_OK;
}

sm_status sm_ctx_create(const sm_model_impl *m, sm_ctx **out) {
    sm_ctx *c = sm_alloc(&m->h, sizeof *c);
    if (!c) return SM_E_NOMEM;
    memset(c, 0, offsetof(sm_ctx, req));
    c->m = m;
    c->tw = sm_alloc(&m->h, sizeof *c->tw);
    if (!c->tw || sm_text_create(m, &c->text) != SM_OK) {
        sm_ctx_free(c);
        return SM_E_NOMEM;
    }
    *out = c;
    return SM_OK;
}

void sm_ctx_free(sm_ctx *c) {
    if (!c) return;
    sm_text_free(c->text);
    void *owned[] = {c->scratch, c->dec, c->tw, c};
    for (size_t i = 0; i < sizeof owned / sizeof owned[0]; i++) sm_dealloc(&c->m->h, owned[i]);
}

uint32_t sm_tokens_of(const sm_model_impl *m, double seconds) {
    return (uint32_t)(seconds * m->fps / m->frames_per_token + 0.5);
}

sm_status sm_ctx_encode(sm_ctx *c, const char *utf8, size_t len, sm_text_impl *t) {
    const sm_model_impl *m = c->m;
    int32_t ids[64];
    const int32_t n = sm_tok_encode(&m->tok, utf8, len, c->tw, ids);
    if (n < 0) return sm_ctx_fail(c, SM_E_ARG, "text is not valid UTF-8");
    const sm_status s = sm_ctx_fit(c, 0, 0);
    if (s != SM_OK) return s;
    sm_text_encode(&m->text, ids, (uint32_t)n, t->vec, t->words, c->scratch);
    t->W = (uint32_t)n;
    t->ready = 1;
    sm_tok_unknown(&m->tok, c->tw, ids, n, t->unknown, sizeof t->unknown, &t->n_unknown);
    return SM_OK;
}

/* the decoder's window: K context tokens (a prefix's, or a loop's end wrapped around), the codes, a loop's start */
static void fill_window(const int16_t *codes, uint32_t n, const sm_prefix *pre, uint32_t K, uint32_t Lw,
                        int16_t *window) {
    for (uint32_t i = 0; i < Lw; i++) {
        const uint32_t at = (i + n * SM_WRAP - K) % n;
        const int16_t *src = pre && i < K ? pre->codes + i * SM_VQ_LAYERS : codes + at * SM_VQ_LAYERS;
        memcpy(window + i * SM_VQ_LAYERS, src, sizeof(int16_t) * SM_VQ_LAYERS);
    }
}

/* fade out prev's own pose offset from its last frame as this decode sees it */
static void fade_from_prefix(sm_motion_impl *mo, const sm_prefix *pre, const float *seen) {
    for (uint32_t t = 0; t < FADE && t < mo->frames; t++) {
        const float w = 1 - (float)(t + 1) / (FADE + 1);
        for (size_t i = 3; i < SM_F; i++) mo->feat[t * SM_F + i] += (pre->last[i] - seen[i]) * w;
    }
}

/* a prefix clip's last tokens give the decoder its left context and the root its start; a loop's window wraps */
sm_status sm_ctx_decode(sm_ctx *c, const int16_t *codes, uint32_t n, const sm_prefix *pre, const double *v,
                        const sm_path *path, sm_motion_impl *mo) {
    if (pre && !pre->n) pre = NULL;
    const uint32_t K = pre ? pre->n : v[SM_P_LOOP] != 0 ? SM_WRAP : 0, Lw = K + n + (pre ? 0 : K);
    sm_status s = sm_ctx_fit(c, 0, Lw);
    if (s != SM_OK) return s;
    s = sm_motion_fit(mo, n);
    if (s != SM_OK) return sm_ctx_fail(c, s, "out of memory");

    float *vq_scratch = c->dec, *feat = vq_scratch + sm_vq_scratch_floats(Lw);
    double *tmp = (double *)(void *)(feat + 2 * (size_t)Lw * SM_F);
    int16_t *window = (int16_t *)(void *)(tmp + SM_POST_TMP(2 * Lw));
    fill_window(codes, n, pre, K, Lw, window);
    sm_vq_decode(&c->m->vq, window, Lw, feat, vq_scratch);

    if (codes != mo->codes) memcpy(mo->codes, codes, sizeof(int16_t) * n * SM_VQ_LAYERS);
    memcpy(mo->feat, feat + 2 * (size_t)K * SM_F, sizeof(float) * 2 * n * SM_F);
    mo->tokens = n;
    mo->frames = 2 * n;
    sm_track track = {0};
    if (pre) {
        const float *seen = feat + (2 * (size_t)K - 1) * SM_F;
        fade_from_prefix(mo, pre, seen);
        track = pre->track;
        memcpy(track.vel, seen, sizeof track.vel);   /* into the first frame: prev's own end velocity is poor */
    }
    sm_motion_post(mo, &track, v, path, tmp);
    return SM_OK;
}

/* codes not generated in this call: certain, and no seed */
static sm_status decode_plain(sm_ctx *c, const int16_t *codes, uint32_t n, sm_motion_impl *out) {
    const sm_request *r = &c->req;
    const sm_status s = sm_ctx_decode(c, codes, n, NULL, r->v, &r->path, out);
    if (s != SM_OK) return s;
    for (uint32_t i = 0; i < n; i++) out->conf[i] = 1;
    out->seed = -1;
    return SM_OK;
}

static sm_status decode_codes(sm_ctx *c, const int16_t *codes, int n_tokens, sm_motion_impl *out) {
    if (!codes) return sm_ctx_fail(c, SM_E_ARG, "codes is NULL");
    if (n_tokens < 1 || n_tokens > SM_MAX_CODES) return sm_ctx_fail_range(c, "token_count", n_tokens, 1, SM_MAX_CODES);
    const uint32_t n = (uint32_t)n_tokens;
    for (size_t i = 0; i < (size_t)n * SM_VQ_LAYERS; i++) {
        const int lo = i % SM_VQ_LAYERS ? -1 : 0;
        if (codes[i] < lo || codes[i] >= SM_VQ_N)
            return sm_ctx_fail_range(c, lo ? "a residual code" : "a base code", codes[i], lo, SM_VQ_N - 1);
    }
    if (sm_path_check(c->m, &c->req.path, n, c->err, sizeof c->err) != SM_OK) return SM_E_ARG;
    return decode_plain(c, codes, n, out);
}

static sm_status check_joints(sm_ctx *c, const float *joints, int n_frames) {
    if (!joints) return sm_ctx_fail(c, SM_E_ARG, "joints is NULL");
    if (!c->m->vq.has_enc) return sm_ctx_fail(c, SM_E_UNSUPPORTED, "this model file has no motion encoder");
    if (n_frames < 3 || (n_frames - 1) / 2 > SM_MAX_CODES)
        return sm_ctx_fail_range(c, "frame_count", n_frames, 3, 2 * SM_MAX_CODES + 1);
    for (size_t i = 0; i < (size_t)n_frames * SM_J * 3; i++)
        if (!isfinite(joints[i])) return sm_ctx_fail(c, SM_E_ARG, "joints hold NaN or Inf");
    return SM_OK;
}

static sm_status encode_motion(sm_ctx *c, const float *joints, int frame_count, sm_motion_impl *out) {
    sm_status s = check_joints(c, joints, frame_count);
    if (s != SM_OK) return s;
    const uint32_t n_frames = (uint32_t)frame_count, T = n_frames - 1, L = T / 2;   /* 2 feature frames per token */
    if (sm_path_check(c->m, &c->req.path, L, c->err, sizeof c->err) != SM_OK) return SM_E_ARG;
    const size_t tmp = sizeof(double) * sm_feat_tmp_doubles(n_frames);
    const size_t floats = (size_t)T * SM_F + sm_vq_enc_scratch_floats(T);
    s = grow(c->m, &c->dec, &c->n_dec, tmp + sizeof(float) * floats);
    if (s == SM_OK) s = sm_motion_fit(out, L);
    if (s != SM_OK) return sm_ctx_fail(c, s, "out of memory");

    float *feat = (float *)(void *)((char *)c->dec + tmp);
    sm_joints_to_features(joints, n_frames, c->dec, feat);
    for (size_t i = 0; i < (size_t)T * SM_F; i++)
        if (!isfinite(feat[i]))
            return sm_ctx_fail(c, SM_E_NUMERIC, "the joints have no pose to read (zero-length bones?)");
    sm_vq_encode(&c->m->vq, feat, T, out->codes, feat + (size_t)T * SM_F);
    return decode_plain(c, out->codes, L, out);
}

sm_status sm_ctx_report(sm_ctx *c, uint32_t done, uint32_t total) {
    const sm_options *o = c->options;
    if (!o->progress || o->progress(o->user, (int)done, (int)total)) return SM_OK;
    return sm_ctx_fail(c, SM_E_CANCELLED, "stopped by the progress callback");
}

sm_status sm_ctx_start_run(sm_ctx *c, const double *v, uint32_t L, uint32_t tie, int32_t seed) {
    const sm_sampling s = {(uint32_t)v[SM_P_STEPS], (uint32_t)v[SM_P_RES_LAYERS], (uint32_t)v[SM_P_TOP_K],
                           (float)v[SM_P_CFG], (float)v[SM_P_RES_CFG], (float)v[SM_P_TEMP]};
    c->run = (sm_run){c->text->W, L, 0, 0, s, (uint64_t)seed, c->codes, c->conf, c->pins, tie};
    const sm_status st = sm_gen_start(&c->m->gen, &c->run, c->text->vec, c->text->words, c->scratch);
    return st == SM_OK ? SM_OK : sm_ctx_fail(c, st, "bad sampling settings");
}

/* the run's lengths: a prefix's overlap, the new tokens, a loop's wrap */
static sm_status check_run(sm_ctx *c, const sm_request *r) {
    if (!c->m->gen.layers) return sm_ctx_fail(c, SM_E_UNSUPPORTED, "this model file has no generator");
    if (!r->has_text) return sm_ctx_fail(c, SM_E_ARG, "no prompt");
    const int loop = r->v[SM_P_LOOP] != 0;
    if (loop && r->pre.n) return sm_ctx_fail(c, SM_E_ARG, "a loop has no start to continue a previous motion from");
    const uint32_t fresh = sm_tokens_of(c->m, r->v[SM_P_SECONDS]), skip = r->pre.n ? SM_OVERLAP : 0;
    const uint32_t tie = loop ? SM_TIE : 0, L = skip + fresh + tie;
    const uint32_t cap = c->m->gen.max_tokens < SM_MAX_RUN ? c->m->gen.max_tokens : SM_MAX_RUN;
    if (L > cap) {
        const double lo = c->m->param[SM_P_SECONDS][0];
        const double hi = (double)(cap - (L - fresh)) * c->m->frames_per_token / c->m->fps;
        const char *name = skip ? "seconds with a previous motion" : loop ? "seconds of a loop" : "seconds";
        return sm_ctx_fail_range(c, name, r->v[SM_P_SECONDS], lo, hi);
    }
    if (r->n_pins > fresh) return sm_ctx_fail_range(c, "pinned tokens", r->n_pins, 0, fresh);
    const sm_status s = sm_path_check(c->m, &r->path, fresh, c->err, sizeof c->err);
    if (s != SM_OK) return s;
    c->fresh = fresh;
    c->L = L;
    c->skip = skip;
    c->tie = tie;
    return SM_OK;
}

/* the prefix's base codes, then the request's pinned tokens */
static void pin_run(sm_ctx *c, const sm_request *r) {
    for (uint32_t j = 0; j < c->L; j++) {
        int16_t *pin = c->pins + j * SM_VQ_LAYERS;
        const uint32_t fresh = j - c->skip;
        pin[0] = j < c->skip ? r->pre.codes[(r->pre.n - SM_OVERLAP + j) * SM_VQ_LAYERS] : -1;
        for (int q = 1; q < SM_VQ_LAYERS; q++) pin[q] = SM_FREE;
        if (j >= c->skip && fresh < r->n_pins && r->pins[fresh * SM_VQ_LAYERS] >= 0)
            memcpy(pin, r->pins + fresh * SM_VQ_LAYERS, sizeof(int16_t) * SM_VQ_LAYERS);
    }
}

static sm_status run_passes(sm_ctx *c) {
    const uint32_t total = c->run.s.steps + c->run.s.res_layers;
    sm_status s = SM_OK;
    while (s == SM_OK) {
        s = sm_gen_pass(&c->m->gen, &c->run, c->scratch);
        if (s >= 0 && sm_ctx_report(c, c->run.pass, total) != SM_OK) s = SM_E_CANCELLED;
    }
    return s;
}

static sm_status generate(sm_ctx *c, sm_motion_impl *out) {
    const sm_request *r = &c->req;
    sm_status s = check_run(c, r);
    if (s == SM_OK) s = sm_ctx_fit(c, c->L, c->L + r->pre.n);
    if (s == SM_OK && (s = sm_motion_fit(out, c->fresh)) != SM_OK) sm_ctx_fail(c, s, "out of memory");
    if (s == SM_OK && r->has_text == 2) sm_text_copy(c->text, r->enc);
    else if (s == SM_OK) s = sm_ctx_encode(c, r->text, r->text_len, c->text);
    if (s != SM_OK) return s;

    pin_run(c, r);
    s = sm_ctx_start_run(c, r->v, c->L, c->tie, (int32_t)r->v[SM_P_SEED]);
    if (s == SM_OK) s = run_passes(c);
    if (s != SM_DONE) return s;

    memcpy(out->codes, c->codes + c->skip * SM_VQ_LAYERS, sizeof(int16_t) * c->fresh * SM_VQ_LAYERS);
    memcpy(out->conf, c->conf + c->skip, sizeof(float) * c->fresh);
    out->seed = (int32_t)r->v[SM_P_SEED];
    return sm_ctx_decode(c, out->codes, c->fresh, &r->pre, r->v, &r->path, out);
}

/* the frame every motion call shares: a context, the request from the options, a motion, the error */
sm_motion sm_make_motion(sm_model model, const char *prompt, const sm_options *o, sm_maker make, const void *args) {
    const sm_model_impl *m = model.internal;
    if (!sm_check_input(m, "the model is not loaded")) return sm_wrap_motion(NULL);
    sm_ctx *c = sm_borrow_ctx(m);
    if (!c) return sm_wrap_motion(NULL);

    c->options = o;
    sm_motion_impl *mo = NULL;
    sm_status s = sm_fill_request(&c->req, m, prompt, o);
    if (s != SM_OK) sm_ctx_fail(c, s, c->req.err);
    if (s == SM_OK && (s = sm_motion_create(m, &mo)) != SM_OK) sm_ctx_fail(c, s, "out of memory");
    if (s == SM_OK) s = make(c, args, mo);
    if (s != SM_OK) {
        sm_set_error(c->err[0] ? c->err : sm_status_message(s));
        sm_motion_free(mo);
        mo = NULL;
    }
    sm_return_ctx(c);
    return sm_wrap_motion(mo);
}

static sm_status make_generated(sm_ctx *c, const void *args, sm_motion_impl *out) {
    (void)args;
    return generate(c, out);
}

typedef struct { const void *data; int count; } input;

static sm_status make_decoded(sm_ctx *c, const void *args, sm_motion_impl *out) {
    const input *in = args;
    return decode_codes(c, in->data, in->count, out);
}

static sm_status make_encoded(sm_ctx *c, const void *args, sm_motion_impl *out) {
    const input *in = args;
    return encode_motion(c, in->data, in->count, out);
}

sm_motion sm_generate_motion_ex(sm_model model, const char *prompt, sm_options options) {
    return sm_make_motion(model, prompt, &options, make_generated, NULL);
}

sm_motion sm_generate_motion(sm_model model, const char *prompt, float seconds, int seed) {
    sm_options options = sm_default_options(model);
    options.seconds = seconds;
    options.seed = seed;
    return sm_make_motion(model, prompt, &options, make_generated, NULL);
}

sm_motion sm_decode_codes(sm_model model, const int16_t *codes, int token_count, sm_options options) {
    const input in = {codes, token_count};
    return sm_make_motion(model, NULL, &options, make_decoded, &in);
}

sm_motion sm_encode_motion(sm_model model, const float *joints, int frame_count, sm_options options) {
    const input in = {joints, frame_count};
    return sm_make_motion(model, NULL, &options, make_encoded, &in);
}

static sm_text wrap_text(const sm_text_impl *t) {
    sm_text text = {0};
    if (!t) return text;
    text.vector = t->vec;
    text.vector_size = (int)t->m->text.out.n;
    text.unknown_word_count = (int)t->n_unknown;
    text.unknown_words = t->unknown;
    text.internal = (void *)(uintptr_t)t;
    return text;
}

static sm_status encode_prompt(sm_ctx *c, const char *prompt, sm_text_impl *t) {
    size_t n;
    if (!c->m->gen.layers) return sm_ctx_fail(c, SM_E_UNSUPPORTED, "this model file has no text encoder");
    if (sm_check_text(prompt, &n, c->err, sizeof c->err) != SM_OK) return SM_E_ARG;
    return sm_ctx_encode(c, prompt, n, t);
}

sm_text sm_encode_text(sm_model model, const char *prompt) {
    const sm_model_impl *m = model.internal;
    if (!sm_check_input(m, "the model is not loaded")) return wrap_text(NULL);
    sm_ctx *c = sm_borrow_ctx(m);
    if (!c) return wrap_text(NULL);
    sm_text_impl *t = NULL;
    sm_status s = sm_text_create(m, &t);
    if (s != SM_OK) sm_ctx_fail(c, s, "out of memory");
    if (s == SM_OK) s = encode_prompt(c, prompt, t);
    if (s != SM_OK) {
        sm_set_error(c->err);
        sm_text_free(t);
        t = NULL;
    }
    sm_return_ctx(c);
    return wrap_text(t);
}

sm_text sm_text_from_vector(sm_model model, const float *vector, int vector_size) {
    const sm_model_impl *m = model.internal;
    if (!sm_check_input(m, "the model is not loaded")) return wrap_text(NULL);
    if (!m->gen.layers) {
        sm_set_error("this model file has no text encoder");
        return wrap_text(NULL);
    }
    if (!vector || vector_size != (int)m->text.out.n) {
        sm_set_error("vector is NULL or not the model's text_vector_size");
        return wrap_text(NULL);
    }
    for (int i = 0; i < vector_size; i++)
        if (!isfinite(vector[i])) {
            sm_set_error("vector holds NaN or Inf");
            return wrap_text(NULL);
        }
    sm_text_impl *t = NULL;
    if (sm_text_create(m, &t) != SM_OK) {
        sm_set_error("out of memory");
        return wrap_text(NULL);
    }
    memcpy(t->vec, vector, sizeof(float) * (size_t)vector_size);
    t->ready = 1;
    return wrap_text(t);
}

bool sm_is_text_valid(sm_text text) { return text.internal != NULL; }
void sm_unload_text(sm_text text) { sm_text_free(text.internal); }
