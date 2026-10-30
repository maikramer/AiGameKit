/* The text encoder and the generator: masked and residual heads on one trunk (MoMask, Guo et al. 2024). */
#include "sm_internal.h"

enum { N_CODES = 512, MASK_ID = 512, RES_N = 513 };   /* RES_N: the codes and the pad code */
enum { TABLE = 0, MATRIX = 1, MATRIX_BIAS = 2 };      /* what lin() binds */
#define PINNED_SCORE 1e5f                              /* never re-masked */

static int has(const sm_model_impl *m, const char *name) {
    uint64_t dims[2] = {0, 0};
    return sm_gguf_tensor(&m->g, name, SM_T_Q8_0, 2, dims) || sm_gguf_tensor(&m->g, name, SM_T_F32, 2, dims);
}

static const float *vec(const sm_model_impl *m, const char *name, uint64_t n, char *err, size_t err_len) {
    uint64_t dims[1] = {n};
    return sm_need(m, name, SM_T_F32, 1, dims, err, err_len);
}

static uint32_t head_count(uint32_t d) { return d / 64 > 4 ? d / 64 : 4; }

/* [n][k] rows, Q8_0 or F32 (k or n 0: any); a MATRIX_BIAS is name.w and name.b */
static int lin(const sm_model_impl *m, sm_lin *l, const char *name, int kind, uint64_t k, uint64_t n, char *err,
               size_t err_len) {
    char weight[64], bias[64];
    uint64_t dims[2] = {k, n};
    snprintf(weight, sizeof weight, kind == MATRIX_BIAS ? "%s.w" : "%s", name);
    snprintf(bias, sizeof bias, "%s.b", name);

    l->type = SM_T_Q8_0;
    l->w = sm_gguf_tensor(&m->g, weight, SM_T_Q8_0, 2, dims);
    if (!l->w) {
        l->type = SM_T_F32;
        l->w = sm_need(m, weight, SM_T_F32, 2, dims, err, err_len);
    }
    if (!l->w) return 0;
    if (dims[0] % 32 || dims[0] > 0xFFFF || dims[1] > 0xFFFF || (kind != TABLE && dims[1] % 16))
        return !sm_fail(err, err_len, SM_E_FORMAT, "bad matrix size");

    l->k = (uint32_t)dims[0];
    l->n = (uint32_t)dims[1];
    l->b = NULL;
    return kind != MATRIX_BIAS || (l->b = vec(m, bias, l->n, err, err_len));
}

static int norm(const sm_model_impl *m, sm_norm *n, const char *name, uint32_t d, char *err, size_t err_len) {
    char weight[64], bias[64];
    snprintf(weight, sizeof weight, "%s.w", name);
    snprintf(bias, sizeof bias, "%s.b", name);
    return (n->w = vec(m, weight, d, err, err_len)) && (n->b = vec(m, bias, d, err, err_len));
}

static int bind_layer(const sm_model_impl *m, sm_layer *l, const char *prefix, uint32_t d, char *err, size_t err_len) {
    static const char *const part[6] = {"attn.qkv", "attn.out", "ff1", "ff2", "ln1", "ln2"};
    char name[6][48];
    for (int i = 0; i < 6; i++) snprintf(name[i], sizeof name[i], "%s.%s", prefix, part[i]);
    return lin(m, &l->qkv, name[0], MATRIX_BIAS, d, 3 * d, err, err_len) &&
           lin(m, &l->out, name[1], MATRIX_BIAS, d, d, err, err_len) &&
           lin(m, &l->ff1, name[2], MATRIX_BIAS, d, 0, err, err_len) &&
           lin(m, &l->ff2, name[3], MATRIX_BIAS, l->ff1.n, d, err, err_len) &&
           norm(m, &l->ln1, name[4], d, err, err_len) && norm(m, &l->ln2, name[5], d, err, err_len);
}

/* layers by probing prefix.L0, prefix.L1, ... */
static int stack(const sm_model_impl *m, sm_layer *layers, const char *prefix, uint32_t d, uint32_t *n, char *err,
                 size_t err_len) {
    for (*n = 0; *n < SM_MAX_LAYERS; ++*n) {
        char layer[40], probe[56];
        snprintf(layer, sizeof layer, "%s.L%u", prefix, *n);
        snprintf(probe, sizeof probe, "%s.attn.qkv.w", layer);
        if (!has(m, probe)) break;
        if (!bind_layer(m, &layers[*n], layer, d, err, err_len)) return 0;
    }
    if (*n == 0 || d % head_count(d)) return !sm_fail(err, err_len, SM_E_FORMAT, "bad layer stack");
    return 1;
}

static int bind_text(const sm_model_impl *m, sm_textenc *t, char *err, size_t err_len) {
    uint64_t dims[2] = {0, 0};
    if (!lin(m, &t->emb, "text.emb", TABLE, 0, 0, err, err_len)) return 0;
    if (!lin(m, &t->emb_up, "text.emb_up", MATRIX, t->emb.k, 0, err, err_len)) return 0;

    dims[0] = t->d = t->emb_up.n;
    if (!(t->pos = sm_need(m, "text.pos", SM_T_F32, 2, dims, err, err_len))) return 0;
    t->max_len = (uint32_t)(dims[1] < 0xFFFF ? dims[1] : 0);
    t->heads = head_count(t->d);
    if (!stack(m, t->L, "text", t->d, &t->layers, err, err_len)) return 0;
    if (!norm(m, &t->ln, "text.ln", t->d, err, err_len)) return 0;
    if (!lin(m, &t->out, "text.out", MATRIX_BIAS, 2 * t->d, 0, err, err_len)) return 0;

    if (m->tok.max_len > t->max_len || t->emb.n <= m->tok.pad)
        return !sm_fail(err, err_len, SM_E_FORMAT, "text encoder smaller than the tokenizer");
    return 1;
}

static int bind_residual_tables(const sm_model_impl *m, sm_gen *g, char *err, size_t err_len) {
    for (uint32_t q = 1; q < SM_VQ_LAYERS; q++) {
        char in[16], out[16];
        snprintf(in, sizeof in, "res.in%u", q);
        snprintf(out, sizeof out, "res.out%u", q);
        if (!lin(m, &g->in[q], in, TABLE, g->d, 0, err, err_len)) return 0;
        if (!lin(m, &g->res_out[q], out, MATRIX_BIAS, g->d, 0, err, err_len)) return 0;
        if (g->in[q].n < RES_N || g->res_out[q].n < RES_N || g->res_out[q].n != g->res_out[1].n)
            return !sm_fail(err, err_len, SM_E_FORMAT, "bad residual tables");
    }
    return 1;
}

static int bind_heads(const sm_model_impl *m, sm_gen *g, uint32_t text_dim, char *err, size_t err_len) {
    const uint32_t d = g->d;
    uint64_t quant_dims[2] = {SM_VQ_LAYERS, d};
    const struct { sm_lin *l; const char *name; int kind; uint32_t k, n; } lins[] = {
        {&g->mask.cond, "mask.cond", MATRIX_BIAS, text_dim, d},
        {&g->mask.word, "mask.word", MATRIX_BIAS, g->word_dim, d},
        {&g->mask.dense, "mask.head.dense", MATRIX_BIAS, d, d},
        {&g->mask.out, "mask.head.out", MATRIX_BIAS, d, N_CODES},
        {&g->tok, "mask.tok", TABLE, d, 0}, {&g->res.cond, "res.cond", MATRIX_BIAS, text_dim, d},
        {&g->res.dense, "res.head.dense", MATRIX_BIAS, d, d},
    };
    for (size_t i = 0; i < sizeof lins / sizeof lins[0]; i++)
        if (!lin(m, lins[i].l, lins[i].name, lins[i].kind, lins[i].k, lins[i].n, err, err_len)) return 0;

    if (!norm(m, &g->mask.norm, "mask.head.norm", d, err, err_len)) return 0;
    if (!norm(m, &g->res.norm, "res.head.norm", d, err, err_len)) return 0;
    if (!(g->word_type = vec(m, "mask.word_type", d, err, err_len))) return 0;
    if (!(g->in_b = vec(m, "res.in_b", d, err, err_len))) return 0;
    if (!(g->quant_b = vec(m, "res.quant.b", d, err, err_len))) return 0;
    if (!(g->quant_w = sm_need(m, "res.quant.w", SM_T_F32, 2, quant_dims, err, err_len))) return 0;
    if (!bind_residual_tables(m, g, err, err_len)) return 0;
    return g->tok.n > MASK_ID ? 1 : !sm_fail(err, err_len, SM_E_FORMAT, "bad mask.tok");
}

static size_t add_layers(sm_lin **all, size_t n, sm_layer *layers, uint32_t count) {
    for (uint32_t l = 0; l < count; l++) {
        all[n++] = &layers[l].qkv;
        all[n++] = &layers[l].out;
        all[n++] = &layers[l].ff1;
        all[n++] = &layers[l].ff2;
    }
    return n;
}

/* every matrix the kernel multiplies, dequantised once into panels */
static sm_status pack_weights(sm_model_impl *m, char *err, size_t err_len) {
    sm_textenc *t = &m->text;
    sm_gen *g = &m->gen;
    sm_lin *all[2 * 4 * SM_MAX_LAYERS + 16];
    size_t n = add_layers(all, 0, t->L, t->layers);
    n = add_layers(all, n, g->mask.L, g->layers);
    sm_lin *heads[] = {&t->emb_up, &t->out, &g->mask.cond, &g->mask.word, &g->mask.dense, &g->mask.out, &g->res.cond,
                       &g->res.dense};
    for (size_t i = 0; i < sizeof heads / sizeof heads[0]; i++) all[n++] = heads[i];
    for (uint32_t q = 1; q < SM_VQ_LAYERS; q++) all[n++] = &g->res_out[q];

    size_t floats = 0;
    for (size_t i = 0; i < n; i++) floats += (size_t)all[i]->k * all[i]->n;
    if (!(m->packed = sm_alloc(&m->h, sizeof(float) * floats)))
        return sm_fail(err, err_len, SM_E_NOMEM, "out of memory");
    float *at = m->packed;
    for (size_t i = 0; i < n; i++) {
        sm_pack(all[i], at);
        at += (size_t)all[i]->k * all[i]->n;
    }
    return SM_OK;
}

sm_status sm_bind_gen(sm_model_impl *m, char *err, size_t err_len) {
    if (!has(m, "text.emb")) return SM_OK;
    sm_textenc *t = &m->text;
    sm_gen *g = &m->gen;
    uint64_t pe_dims[2] = {0, 0};
    if (!bind_text(m, t, err, err_len)) return SM_E_FORMAT;
    if (!(g->pe = sm_need(m, "pe", SM_T_F32, 2, pe_dims, err, err_len))) return SM_E_FORMAT;
    if (pe_dims[0] % 32 || pe_dims[0] > 0xFFFF || !pe_dims[1] || pe_dims[1] > 0xFFFF)
        return sm_fail(err, err_len, SM_E_FORMAT, "bad pe");

    g->d = (uint32_t)pe_dims[0];
    g->max_tokens = (uint32_t)pe_dims[1];
    g->heads = head_count(g->d);
    g->word_dim = t->d;
    if (!stack(m, g->mask.L, "trunk", g->d, &g->layers, err, err_len)) return SM_E_FORMAT;
    if (!bind_heads(m, g, t->out.n, err, err_len)) return SM_E_FORMAT;

    t->par = g->par = &m->par;
    const sm_status s = pack_weights(m, err, err_len);
    memcpy(g->res.L, g->mask.L, sizeof g->res.L);
    return s;
}

/* pre-norm layers, final LN, vec = out(concat(x[W-1], mean(x))) */
size_t sm_text_scratch_floats(const sm_textenc *t) {
    const size_t M = t->max_len;
    return M * t->emb.k + M * t->d + 2 * (size_t)t->d +
           sm_layer_scratch_floats(t->max_len, t->d, t->L[0].ff1.n, t->heads);
}

void sm_text_encode(const sm_textenc *t, const int32_t *ids, uint32_t W, float *vec, float *words, float *scratch) {
    const uint32_t d = t->d, M = t->max_len;
    float *emb = scratch, *x = emb + (size_t)M * t->emb.k, *pooled = x + (size_t)M * d, *layer = pooled + 2 * d;
    for (uint32_t i = 0; i < W; i++) sm_row(&t->emb, (uint32_t)ids[i], emb + (size_t)i * t->emb.k);
    sm_linear(t->par, &t->emb_up, emb, W, x, SM_EPI_ADD, t->pos);

    const sm_seg seg = {0, W};
    const sm_batch batch = {&seg, 1, W};
    for (uint32_t l = 0; l < t->layers; l++) sm_layer_pre(t->par, &t->L[l], &batch, t->heads, x, layer);
    sm_layernorm(t->par, x, W, d, t->ln, 1e-5, x);

    memcpy(pooled, x + (size_t)(W - 1) * d, sizeof(float) * d);
    for (uint32_t i = 0; i < d; i++) {
        float sum = 0;
        for (uint32_t j = 0; j < W; j++) sum += x[(size_t)j * d + i];
        pooled[d + i] = sum / (float)W;
    }
    sm_linear(t->par, &t->out, pooled, 1, vec, SM_EPI_NONE, NULL);
    for (size_t i = 0; i < (size_t)W * d; i++) words[i] = sm_round_half(x[i]);   /* the generator expects fp16 words */
}

typedef struct {
    float *words, *cond_mask, *cond_res, *seq, *layer, *h, *h2, *raw, *logits, *hist, *row, *score, *prob;
    int32_t *ids, *pick;
    uint8_t *mask;
} work;

/* one layout for the size and the pointers; s NULL: only the size */
static size_t carve(const sm_gen *g, uint32_t W, uint32_t L, float *s, work *w) {
    const size_t d = g->d, n = g->res_out[1].n, M = 2 * (size_t)L + (W + 2 > 4 ? W + 2 : 4);
    const size_t len[] = {W * d, d, d, M * d, sm_layer_scratch_floats((uint32_t)M, g->d, g->mask.L[0].ff1.n, g->heads),
                          2 * L * d, 2 * L * d, 2 * L * n, L * n, L * d, d, L, L, L, L, (L + 3) / 4};
    float *p[sizeof len / sizeof len[0]];
    size_t at = 0;
    for (size_t i = 0; i < sizeof len / sizeof len[0]; i++) {
        p[i] = s ? s + at : NULL;
        at += len[i];
    }
    *w = (work){p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7], p[8], p[9], p[10], p[11], p[12],
                (int32_t *)p[13], (int32_t *)p[14], (uint8_t *)p[15]};
    return at;
}

size_t sm_gen_scratch_floats(const sm_gen *g, uint32_t W, uint32_t L) { return carve(g, W, L, NULL, &(work){0}); }

void sm_gen_prepare(const sm_gen *g, const float *vec, const float *words, uint32_t W, uint32_t L, float *scratch) {
    work w;
    carve(g, W, L, scratch, &w);
    sm_linear(g->par, &g->mask.cond, vec, 1, w.cond_mask, SM_EPI_NONE, NULL);
    sm_linear(g->par, &g->res.cond, vec, 1, w.cond_res, SM_EPI_NONE, NULL);
    sm_linear(g->par, &g->mask.word, words, W, w.words, SM_EPI_NONE, NULL);
    for (uint32_t j = 0; j < W; j++)
        for (uint32_t i = 0; i < g->d; i++) w.words[(size_t)j * g->d + i] += g->word_type[i];
}

/* the trunk on both sequences, then dense, GELU, LN (eps 1e-12) and out on the L motion rows of each: raw [2L][n] */
static void trunk_head(const sm_gen *g, const sm_head *head, const sm_lin *out, const work *w, const sm_seg seg[2],
                       uint32_t L) {
    const size_t d = g->d;
    const sm_batch batch = {seg, 2, seg[1].start + seg[1].len};
    for (uint32_t l = 0; l < g->layers; l++) sm_layer_post(g->par, &head->L[l], &batch, g->heads, w->seq, w->layer);
    for (size_t s = 0; s < 2; s++)
        memcpy(w->h + s * L * d, w->seq + (seg[s].start + seg[s].len - L) * d, sizeof(float) * L * d);
    sm_linear(g->par, &head->dense, w->h, 2 * L, w->h2, SM_EPI_GELU, NULL);
    sm_layernorm(g->par, w->h2, 2 * L, g->d, head->norm, 1e-12, w->h2);
    sm_linear(g->par, out, w->h2, 2 * L, w->raw, SM_EPI_NONE, NULL);
}

/* [cond, words, T_mask[ids] + pe] and [cond_emb(0), T_mask[ids] + pe]: the unconditional pass has no words */
const float *sm_gen_base(const sm_gen *g, uint32_t W, uint32_t L, const int32_t *ids, float *scratch) {
    work w;
    carve(g, W, L, scratch, &w);
    const size_t d = g->d, motion = 1 + W, uncond = 1 + W + L;
    memcpy(w.seq, w.cond_mask, sizeof(float) * d);
    memcpy(w.seq + d, w.words, sizeof(float) * W * d);
    memcpy(w.seq + uncond * d, g->mask.cond.b, sizeof(float) * d);
    for (uint32_t j = 0; j < L; j++) {
        float *x = w.seq + (motion + j) * d;
        sm_row(&g->tok, (uint32_t)ids[j], x);
        for (size_t i = 0; i < d; i++) x[i] += g->pe[j * d + i];
        memcpy(w.seq + (uncond + 1 + j) * d, x, sizeof(float) * d);
    }
    const sm_seg seg[2] = {{0, 1 + W + L}, {(uint32_t)uncond, 1 + L}};
    trunk_head(g, &g->mask, &g->mask.out, &w, seg, L);
    return w.raw;
}

/* [cond, quant_emb(onehot(q)), hist + b_in + pe], conditional and with cond_emb(0) */
const float *sm_gen_res(const sm_gen *g, uint32_t W, uint32_t L, uint32_t q, const float *hist, float *scratch) {
    work w;
    carve(g, W, L, scratch, &w);
    const size_t d = g->d, uncond = 2 + L;
    memcpy(w.seq, w.cond_res, sizeof(float) * d);
    memcpy(w.seq + uncond * d, g->res.cond.b, sizeof(float) * d);
    for (size_t i = 0; i < d; i++) {
        const float layer_emb = g->quant_w[i * SM_VQ_LAYERS + q] + g->quant_b[i];
        w.seq[d + i] = layer_emb;
        w.seq[(uncond + 1) * d + i] = layer_emb;
    }
    for (size_t j = 0; j < L; j++) {
        float *x = w.seq + (2 + j) * d;
        for (size_t i = 0; i < d; i++) x[i] = hist[j * d + i] + g->in_b[i] + g->pe[j * d + i];
        memcpy(w.seq + (uncond + 2 + j) * d, x, sizeof(float) * d);
    }
    const sm_seg seg[2] = {{0, 2 + L}, {(uint32_t)uncond, 2 + L}};
    trunk_head(g, &g->res, &g->res_out[q], &w, seg, L);
    return w.raw;
}

/* logits [L][used] = u + (c - u) * cfg, from raw [2L][n] */
static sm_status guide(const float *raw, uint32_t L, uint32_t n, uint32_t used, float cfg, float *logits) {
    for (size_t j = 0; j < L; j++)
        for (size_t c = 0; c < used; c++) {
            const float cond = raw[j * n + c], uncond = raw[(L + j) * n + c], v = uncond + (cond - uncond) * cfg;
            if (!isfinite(v)) return SM_E_NUMERIC;
            logits[j * used + c] = v;
        }
    return SM_OK;
}

typedef struct { uint64_t seed; uint32_t stream; } noise_key;

static float code_noise(void *user, uint32_t step, uint32_t pos, uint32_t code) {
    const noise_key *k = user;
    return sm_hash_gumbel(k->seed, k->stream, step, pos, code);
}

/* the wrap's tail copies the head */
static void tie(const sm_run *r, int32_t *v, uint32_t layer) {
    for (uint32_t k = 0, t = r->L - r->tie; k < r->tie; k++, t++) {
        v[t] = v[k];
        r->codes[t * SM_VQ_LAYERS + layer] = r->codes[k * SM_VQ_LAYERS + layer];
        if (r->conf) r->conf[t] = r->conf[k];
    }
}

/* code layer 0: each step re-masks the least confident free positions (cosine schedule) and samples them again */
static sm_status base_step(const sm_gen *g, const work *w, sm_run *r, float *scratch) {
    const uint32_t L = r->L, step = r->pass;
    noise_key key = {r->seed, 0};
    sm_lowest(w->score, L, sm_nmask(step, r->s.steps, L - r->fixed), w->mask);
    for (uint32_t j = 0; j < L; j++)
        if (w->mask[j]) w->ids[j] = MASK_ID;
    for (uint32_t j = 0; j < r->tie; j++) w->ids[L - r->tie + j] = w->ids[j];

    const float *raw = sm_gen_base(g, r->W, L, w->ids, scratch);
    const sm_status s = guide(raw, L, N_CODES, N_CODES, r->s.cfg, w->logits);
    if (s != SM_OK) return s;
    const sm_picking picking = {w->logits, L, N_CODES, w->mask, r->s.temp, r->s.top_k, code_noise, &key, step,
                                w->pick, w->prob};
    sm_pick_rows(g->par, &picking);

    for (uint32_t j = 0; j < L; j++) {
        w->score[j] = w->mask[j] ? w->prob[j] : PINNED_SCORE;
        if (w->mask[j]) w->ids[j] = w->pick[j];
        if (w->mask[j] && r->conf) r->conf[j] = w->prob[j];
        r->codes[j * SM_VQ_LAYERS] = (int16_t)w->ids[j];
    }
    tie(r, w->ids, 0);
    return SM_OK;
}

/* layer q from the summed embeddings of layers 0..q-1; the pad code (512) feeds the history and becomes -1 */
static sm_status res_step(const sm_gen *g, const work *w, sm_run *r, uint32_t q, float *scratch) {
    const size_t d = g->d, n = g->res_out[1].n, L = r->L;
    const int32_t *prev = q == 1 ? w->ids : w->pick;
    noise_key key = {r->seed, 1};
    if (q == 1) memset(w->hist, 0, sizeof(float) * L * d);
    for (size_t j = 0; j < L; j++) {
        sm_row(&g->in[q], (uint32_t)prev[j], w->row);
        for (size_t i = 0; i < d; i++) w->hist[j * d + i] += w->row[i];
    }

    const float *raw = sm_gen_res(g, r->W, r->L, q, w->hist, scratch);
    const sm_status s = guide(raw, r->L, (uint32_t)n, RES_N, r->s.res_cfg, w->logits);
    if (s != SM_OK) return s;
    const sm_picking picking = {w->logits, r->L, RES_N, NULL, r->s.temp, r->s.top_k, code_noise, &key, q - 1, w->pick,
                                NULL};
    sm_pick_rows(g->par, &picking);

    for (size_t j = 0; j < L; j++) {
        const int16_t *pin = r->pins ? r->pins + j * SM_VQ_LAYERS : NULL;
        if (pin && pin[0] >= 0 && pin[q] != SM_FREE) w->pick[j] = pin[q] < 0 ? N_CODES : pin[q];
        r->codes[j * SM_VQ_LAYERS + q] = (int16_t)(w->pick[j] >= N_CODES ? -1 : w->pick[j]);
    }
    tie(r, w->pick, q);
    return SM_OK;
}

static int valid_run(const sm_gen *g, const sm_run *r) {
    if (!r->L || r->L > g->max_tokens || r->tie > r->L) return 0;
    if (!r->s.steps || r->s.res_layers >= SM_VQ_LAYERS || !(r->s.temp > 0)) return 0;
    for (uint32_t j = 0; r->pins && j < r->L; j++)
        if (r->pins[j * SM_VQ_LAYERS] >= N_CODES) return 0;
    return 1;
}

sm_status sm_gen_start(const sm_gen *g, sm_run *r, const float *vec, const float *words, float *scratch) {
    const uint32_t L = r->L;
    if (!valid_run(g, r)) return SM_E_ARG;

    work w;
    carve(g, r->W, L, scratch, &w);
    sm_gen_prepare(g, vec, words, r->W, L, scratch);
    r->pass = 0;
    r->fixed = 0;
    for (uint32_t j = 0; j < L; j++) {
        const int pinned = r->pins && r->pins[j * SM_VQ_LAYERS] >= 0, tail = j >= L - r->tie;
        w.ids[j] = pinned ? r->pins[j * SM_VQ_LAYERS] : MASK_ID;
        w.score[j] = pinned || tail ? PINNED_SCORE : 0;
        if (r->conf) r->conf[j] = 1;
        r->fixed += (uint32_t)(pinned || tail);
    }
    for (size_t j = 0; j < (size_t)L * SM_VQ_LAYERS; j++) r->codes[j] = -1;
    return SM_OK;
}

sm_status sm_gen_pass(const sm_gen *g, sm_run *r, float *scratch) {
    const uint32_t total = r->s.steps + r->s.res_layers;
    if (r->pass >= total) return SM_E_STATE;

    work w;
    carve(g, r->W, r->L, scratch, &w);
    sm_status s;
    if (r->pass < r->s.steps) s = base_step(g, &w, r, scratch);
    else s = res_step(g, &w, r, r->pass - r->s.steps + 1, scratch);
    if (s != SM_OK) return s;
    return ++r->pass == total ? SM_DONE : SM_OK;
}
