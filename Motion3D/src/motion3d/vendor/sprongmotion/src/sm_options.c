/* The parameter table, the default options, and options checked into a request. */
#include "sm_internal.h"

/* the runtime's ranges; a model file may narrow them and set its own defaults under sprongmotion.defaults.<key> */
typedef struct { const char *key, *name; uint8_t is_int; double min, max, dflt; } param;
static const param P[SM_N_PARAMS] = {
    [SM_P_SECONDS] = {"seconds", "seconds", 0, 0.5, 10, 4},
    [SM_P_SEED] = {"seed", "seed", 1, 0, 2147483647, 0},
    [SM_P_STEPS] = {"steps", "steps", 1, 1, 64, 10},
    [SM_P_RES_LAYERS] = {"res_layers", "residual_layers", 1, 0, 5, 5},
    [SM_P_CFG] = {"cfg", "text_guidance", 0, 0, 20, 3},
    [SM_P_RES_CFG] = {"res_cfg", "residual_guidance", 0, 0, 20, 3},
    [SM_P_TEMP] = {"temp", "temperature", 0, 0.01, 10, 1},
    [SM_P_TOP_K] = {"top_k", "top_k", 1, 0, 512, 52},
    [SM_P_FOOT_LOCK] = {"foot_lock", "foot_lock", 1, 0, 1, 1},
    [SM_P_IN_PLACE] = {"in_place", "in_place", 1, 0, 1, 0},
    [SM_P_LOOP] = {"loop", "loop", 1, 0, 1, 0},
    [SM_P_PATH_WARP] = {"path_warp", "path_warp", 1, 0, 1, 1},
};

sm_status sm_bind_params(sm_model_impl *m, char *err, size_t err_len) {
    static const char *const part[3] = {".min", ".max", ""};
    for (int p = 1; p < SM_N_PARAMS; p++) {
        if (!P[p].key) continue;
        double *v = m->param[p], x;   /* min, max, default */
        char key[64] = "";
        v[0] = P[p].min;
        v[1] = p == SM_P_SECONDS ? (double)m->max_frames / m->fps : P[p].max;
        v[2] = P[p].dflt;
        for (int i = 0; i < 3; i++) {
            snprintf(key, sizeof key, "sprongmotion.defaults.%s%s", P[p].key, part[i]);
            if (sm_gguf_num(&m->g, key, &x)) v[i] = i == 0 ? fmax(v[0], x) : i == 1 ? fmin(v[1], x) : x;
        }
        if (!(v[0] <= v[2] && v[2] <= v[1]) || (P[p].is_int && v[2] != floor(v[2])))
            return sm_fail_range(err, err_len, SM_E_FORMAT, key, v[2], v[0], v[1]);
    }
    return SM_OK;
}

void sm_param_defaults(const sm_model_impl *m, double *v) {
    for (int p = 0; p < SM_N_PARAMS; p++) v[p] = m->param[p][2];
}

sm_options sm_default_options(sm_model model) {
    const sm_model_impl *m = model.internal;
    if (!m) return (sm_options){0};
    double v[SM_N_PARAMS];
    sm_param_defaults(m, v);
    return (sm_options){
        .seconds = (float)v[SM_P_SECONDS], .seed = (int)v[SM_P_SEED], .steps = (int)v[SM_P_STEPS],
        .residual_layers = (int)v[SM_P_RES_LAYERS], .text_guidance = (float)v[SM_P_CFG],
        .residual_guidance = (float)v[SM_P_RES_CFG], .temperature = (float)v[SM_P_TEMP], .top_k = (int)v[SM_P_TOP_K],
        .foot_lock = v[SM_P_FOOT_LOCK] != 0, .in_place = v[SM_P_IN_PLACE] != 0, .loop = v[SM_P_LOOP] != 0,
        .path_warp = v[SM_P_PATH_WARP] != 0,
    };
}

static sm_status rfail(sm_request *r, sm_status s, const char *msg) { return sm_fail(r->err, sizeof r->err, s, msg); }

/* float options are checked in float, so 0.01f passes a minimum of 0.01 */
static sm_status set_value(sm_request *r, int p, double v) {
    const double *b = r->m->param[p];
    const double lo = P[p].is_int ? b[0] : (float)b[0], hi = P[p].is_int ? b[1] : (float)b[1];
    if (!(v >= lo && v <= hi)) return sm_fail_range(r->err, sizeof r->err, SM_E_ARG, P[p].name, v, b[0], b[1]);
    r->v[p] = v;
    return SM_OK;
}

static sm_status set_values(sm_request *r, const sm_options *o) {
    const double v[SM_N_PARAMS] = {
        [SM_P_SECONDS] = o->seconds, [SM_P_SEED] = o->seed, [SM_P_STEPS] = o->steps,
        [SM_P_RES_LAYERS] = o->residual_layers, [SM_P_CFG] = o->text_guidance, [SM_P_RES_CFG] = o->residual_guidance,
        [SM_P_TEMP] = o->temperature, [SM_P_TOP_K] = o->top_k, [SM_P_FOOT_LOCK] = o->foot_lock,
        [SM_P_IN_PLACE] = o->in_place, [SM_P_LOOP] = o->loop, [SM_P_PATH_WARP] = o->path_warp,
    };
    for (int p = 1; p < SM_N_PARAMS; p++) {
        if (!P[p].key) continue;
        const sm_status s = set_value(r, p, v[p]);
        if (s != SM_OK) return s;
    }
    return SM_OK;
}

/* at most 4096 bytes of UTF-8 */
sm_status sm_check_text(const char *utf8, size_t *n, char *err, size_t err_len) {
    if (!utf8) return sm_fail(err, err_len, SM_E_ARG, "prompt is NULL");
    *n = strlen(utf8);
    if (*n > SM_MAX_TEXT) return sm_fail_range(err, err_len, SM_E_ARG, "prompt bytes", (double)*n, 0, SM_MAX_TEXT);
    if (!sm_utf8_valid(utf8, *n)) return sm_fail(err, err_len, SM_E_ARG, "prompt is not UTF-8");
    return SM_OK;
}

static sm_status set_text(sm_request *r, const char *prompt, const sm_text *text) {
    if (text) {
        const sm_text_impl *t = text->internal;
        if (!t || t->m != r->m || !t->ready) return rfail(r, SM_E_ARG, "text not encoded or from another model");
        r->enc = t;
        r->has_text = 2;
        return SM_OK;
    }
    size_t n;
    if (sm_check_text(prompt, &n, r->err, sizeof r->err) != SM_OK) return SM_E_ARG;
    memcpy(r->text, prompt, n);
    r->text[n] = 0;
    r->text_len = (uint32_t)n;
    r->has_text = 1;
    return SM_OK;
}

static sm_status set_prefix(sm_request *r, const sm_motion *previous) {
    const sm_motion_impl *prev = previous->internal;
    if (!prev || prev->m != r->m) return rfail(r, SM_E_ARG, "the previous motion is empty or from another model");
    if (prev->tokens < SM_OVERLAP)
        return sm_fail_range(r->err, sizeof r->err, SM_E_ARG, "previous tokens", prev->tokens, SM_OVERLAP,
                             SM_MAX_CODES);
    r->pre.n = prev->tokens < SM_CONTEXT ? prev->tokens : SM_CONTEXT;
    memcpy(r->pre.codes, prev->codes + (prev->tokens - r->pre.n) * SM_VQ_LAYERS,
           sizeof(int16_t) * r->pre.n * SM_VQ_LAYERS);
    r->pre.track = prev->end;
    memcpy(r->pre.last, prev->feat + (size_t)(prev->frames - 1) * SM_F, sizeof r->pre.last);
    return SM_OK;
}

static sm_status set_pins(sm_request *r, const int16_t *codes, int n_tokens) {
    if (n_tokens < 0 || n_tokens > SM_MAX_PINS)
        return sm_fail_range(r->err, sizeof r->err, SM_E_ARG, "pinned_token_count", n_tokens, 0, SM_MAX_PINS);
    if (!codes && n_tokens) return rfail(r, SM_E_ARG, "pinned_codes is NULL");
    for (size_t i = 0; i < (size_t)n_tokens * SM_VQ_LAYERS; i++)
        if (codes[i] < -1 || codes[i] >= SM_VQ_N)
            return sm_fail_range(r->err, sizeof r->err, SM_E_ARG, "a pinned code", codes[i], -1, SM_VQ_N - 1);
    if (n_tokens) memcpy(r->pins, codes, sizeof(int16_t) * (size_t)n_tokens * SM_VQ_LAYERS);
    r->n_pins = (uint32_t)n_tokens;
    return SM_OK;
}

sm_status sm_fill_request(sm_request *r, const sm_model_impl *m, const char *prompt, const sm_options *o) {
    memset(r, 0, offsetof(sm_request, text));
    r->m = m;
    r->err[0] = 0;
    sm_param_defaults(m, r->v);
    sm_status s = set_values(r, o);
    if (s == SM_OK && (prompt || o->text)) s = set_text(r, prompt, o->text);
    if (s == SM_OK && o->previous) s = set_prefix(r, o->previous);
    if (s == SM_OK) s = set_pins(r, o->pinned_codes, o->pinned_token_count);
    if (s == SM_OK) s = sm_set_path(r, (const float *)o->path, o->path_count);
    return s;
}

sm_status sm_text_create(const sm_model_impl *m, sm_text_impl **out) {
    const size_t floats = m->text.out.n + (size_t)m->tok.max_len * m->text.d;
    sm_text_impl *t = sm_alloc(&m->h, sizeof *t + sizeof(float) * floats);
    if (!t) return SM_E_NOMEM;
    memset(t, 0, sizeof *t);
    t->m = m;
    t->vec = (float *)(t + 1);
    t->words = t->vec + m->text.out.n;
    *out = t;
    return SM_OK;
}

void sm_text_free(sm_text_impl *t) {
    if (t) sm_dealloc(&t->m->h, t);
}

void sm_text_copy(sm_text_impl *d, const sm_text_impl *s) {
    memcpy(d->vec, s->vec, sizeof(float) * s->m->text.out.n);
    memcpy(d->words, s->words, sizeof(float) * s->W * s->m->text.d);
    d->W = s->W;
    d->ready = s->ready;
    d->n_unknown = s->n_unknown;
    memcpy(d->unknown, s->unknown, sizeof d->unknown);
}
