/* Binding a model file: every tensor checked by name, type and shape, the settings and the metadata. */
#include "sm_internal.h"
#include <stdlib.h>

const void *sm_need(const sm_model_impl *m, const char *name, uint32_t type, uint32_t nd, uint64_t *dims, char *err,
                    size_t err_len) {
    const void *p = sm_gguf_tensor(&m->g, name, type, nd, dims);
    if (!p && err && err_len) snprintf(err, err_len, "tensor %s missing or with the wrong type or shape", name);
    return p;
}

static int u32_of(const sm_gguf *g, const char *key, uint32_t *out) {
    double v;
    if (!sm_gguf_num(g, key, &v) || v < 0 || v > UINT32_MAX || v != (double)(uint32_t)v) return 0;
    *out = (uint32_t)v;
    return 1;
}

static int by_text(const void *a, const void *b) { return strcmp(*(const char *const *)a, *(const char *const *)b); }

static sm_status find_tok_tables(sm_model_impl *m, const int32_t **keep, uint32_t *n_keep, char *err, size_t err_len) {
    sm_tok *t = &m->tok;
    uint32_t n_bytes = 0;
    const struct { const int32_t **p; const char *name; uint32_t nd, d0, *count; } tables[] = {   /* d0 0: any */
        {&t->merges, "tok.merges", 2, 2, &t->n_merges}, {keep, "tok.keep", 1, 0, n_keep},
        {&t->byte_id, "tok.byte_id", 1, SM_TOK_BYTES, &n_bytes}, {&t->uni_class, "tok.uni_class", 2, 3, &t->n_class},
        {&t->uni_lower, "tok.uni_lower", 2, 2, &t->n_lower}, {&t->ent_index, "tok.ent_index", 2, 4, &t->n_ent},
        {&t->ent_names, "tok.ent_names", 1, 0, &t->n_ent_bytes}, {&t->num_fix, "tok.num_fix", 2, 2, &t->n_num_fix},
    };
    for (size_t i = 0; i < sizeof tables / sizeof tables[0]; i++) {
        uint64_t dims[2] = {tables[i].d0, 0};
        *tables[i].p = sm_need(m, tables[i].name, SM_T_I32, tables[i].nd, dims, err, err_len);
        if (!*tables[i].p) return SM_E_FORMAT;
        *tables[i].count = (uint32_t)dims[tables[i].nd - 1];
    }
    return SM_OK;
}

static sm_status check_tok_tables(const sm_tok *t, char *err, size_t err_len) {
    if (t->sot != t->n_vocab - 2 || t->eot != t->n_vocab - 1)
        return sm_fail(err, err_len, SM_E_FORMAT, "bad tokenizer.sot");
    for (uint32_t r = 0; r < 2 * t->n_merges; r++)   /* merge r may only use ids made before it */
        if (t->merges[r] < 0 || (uint32_t)t->merges[r] >= SM_TOK_BASE + r / 2)
            return sm_fail(err, err_len, SM_E_FORMAT, "bad tokenizer merge");
    for (int b = 0; b < SM_TOK_BYTES; b++)
        if (t->byte_id[b] < 0 || t->byte_id[b] >= SM_TOK_BYTES)
            return sm_fail(err, err_len, SM_E_FORMAT, "bad tok.byte_id");
    for (uint32_t i = 0; i < t->n_ent; i++) {
        const int32_t *e = t->ent_index + 4 * i;
        const int bad_name = e[0] < 0 || e[1] < 1 || e[1] > 32 || (uint32_t)e[0] + (uint32_t)e[1] > t->n_ent_bytes;
        const int bad_cp = e[2] < 0 || e[2] > 0x10FFFF || e[3] < -1 || e[3] > 0x10FFFF;
        if (bad_name || bad_cp) return sm_fail(err, err_len, SM_E_FORMAT, "bad HTML entity table");
    }
    return SM_OK;
}

/* CLIP ids outside the kept vocabulary map to UNK */
static sm_status build_remap(sm_model_impl *m, const int32_t *keep, uint32_t n_keep, char *err, size_t err_len) {
    sm_tok *t = &m->tok;
    t->unk = n_keep;
    t->pad = n_keep + 1;
    t->remap = sm_alloc(&m->h, sizeof(int32_t) * t->n_vocab);
    m->tok_table = sm_alloc(&m->h, sm_tok_build(t, NULL));
    if (!t->remap || !m->tok_table) return sm_fail(err, err_len, SM_E_NOMEM, "out of memory");
    for (uint32_t i = 0; i < t->n_vocab; i++) t->remap[i] = (int32_t)t->unk;
    for (uint32_t i = 0; i < n_keep; i++) {
        if (keep[i] < 0 || (uint32_t)keep[i] >= t->n_vocab) return sm_fail(err, err_len, SM_E_FORMAT, "bad tok.keep");
        t->remap[keep[i]] = (int32_t)i;
    }
    sm_tok_build(t, m->tok_table);
    return SM_OK;
}

/* the whole words, sorted, in one block with their pointers */
static sm_status build_vocab(sm_model_impl *m, char *err, size_t err_len) {
    uint32_t n;
    const size_t bytes = sm_tok_words(&m->tok, NULL, &n);
    const char **words = sm_alloc(&m->h, sizeof(char *) * n + bytes);
    if (!(m->vocab = words)) return sm_fail(err, err_len, SM_E_NOMEM, "out of memory");
    char *p = (char *)(words + n);
    sm_tok_words(&m->tok, p, &n);
    for (uint32_t i = 0; i < n; i++) {
        words[i] = p;
        p += strlen(p) + 1;
    }
    qsort(words, n, sizeof *words, by_text);
    m->n_vocab = n;
    return SM_OK;
}

static sm_status bind_tok(sm_model_impl *m, char *err, size_t err_len) {
    const int32_t *keep = NULL;
    uint32_t n_keep = 0;
    sm_status s = find_tok_tables(m, &keep, &n_keep, err, err_len);
    if (s != SM_OK) return s;
    m->tok.n_vocab = SM_TOK_BASE + m->tok.n_merges + 2;
    s = check_tok_tables(&m->tok, err, err_len);
    if (s == SM_OK) s = build_remap(m, keep, n_keep, err, err_len);
    if (s == SM_OK) s = build_vocab(m, err, err_len);
    return s;
}

#define FORMAT_VERSION 2
#define MIN_BONE 1e-4   /* m */
#define MAX_BONE 10

typedef struct { sm_conv *c; const char *name; uint64_t in, out, k; } conv_def;

static int bind_convs(sm_model_impl *m, const conv_def *defs, size_t n, char *err, size_t err_len) {
    for (size_t i = 0; i < n; i++) {
        const conv_def *d = &defs[i];
        char weight[32], bias[32];
        uint64_t weight_dims[3] = {d->out, d->in, d->k}, bias_dims[1] = {d->out};
        snprintf(weight, sizeof weight, "vq.%s.w", d->name);
        snprintf(bias, sizeof bias, "vq.%s.b", d->name);
        if (!(d->c->w = sm_need(m, weight, SM_T_F32, 3, weight_dims, err, err_len))) return 0;
        if (!(d->c->b = sm_need(m, bias, SM_T_F32, 1, bias_dims, err, err_len))) return 0;
    }
    return 1;
}

/* the decoder, and the encoder when sprongmotion.vq.encoder = 1 */
static sm_status bind_vq(sm_model_impl *m, char *err, size_t err_len) {
    sm_vq *q = &m->vq;
    sm_vq_enc *e = &q->enc;
    const uint32_t W = SM_VQ_W;
    const conv_def dec[] = {
        {&q->in, "in", W, W, 3}, {&q->up, "up", W, W, 3}, {&q->mid, "mid", W, W, 3}, {&q->out, "out", W, SM_F, 3},
        {&q->res1[0], "res0.a", W, W, 3}, {&q->res2[0], "res0.b", W, W, 1}, {&q->res1[1], "res1.a", W, W, 3},
        {&q->res2[1], "res1.b", W, W, 1}, {&q->res1[2], "res2.a", W, W, 3}, {&q->res2[2], "res2.b", W, W, 1},
    };
    const conv_def enc[] = {
        {&e->in, "enc.in", SM_F, W, 3}, {&e->down, "enc.down", W, W, 4}, {&e->out, "enc.out", W, W, 3},
        {&e->res1[0], "enc.res0.a", W, W, 3}, {&e->res2[0], "enc.res0.b", W, W, 1},
        {&e->res1[1], "enc.res1.a", W, W, 3}, {&e->res2[1], "enc.res1.b", W, W, 1},
        {&e->res1[2], "enc.res2.a", W, W, 3}, {&e->res2[2], "enc.res2.b", W, W, 1},
    };
    uint32_t flag = 0;
    q->par = &m->par;
    q->has_enc = u32_of(&m->g, "sprongmotion.vq.encoder", &flag) && flag == 1;
    if (!bind_convs(m, dec, sizeof dec / sizeof dec[0], err, err_len)) return SM_E_FORMAT;
    if (q->has_enc && !bind_convs(m, enc, sizeof enc / sizeof enc[0], err, err_len)) return SM_E_FORMAT;

    uint64_t codebook_dims[3] = {SM_VQ_W, SM_VQ_N, SM_VQ_LAYERS}, mean_dims[1] = {SM_F}, std_dims[1] = {SM_F};
    if (!(q->codebook = sm_need(m, "vq.codebook", SM_T_F32, 3, codebook_dims, err, err_len))) return SM_E_FORMAT;
    if (!(q->mean = sm_need(m, "vq.mean", SM_T_F32, 1, mean_dims, err, err_len))) return SM_E_FORMAT;
    if (!(q->std = sm_need(m, "vq.std", SM_T_F32, 1, std_dims, err, err_len))) return SM_E_FORMAT;
    return SM_OK;
}

/* the rest pose: the 22-joint skeleton, every bone longer than 0.1 mm */
static sm_status bind_skeleton(sm_model_impl *m, char *err, size_t err_len) {
    uint64_t parent_dims[1] = {SM_J}, offset_dims[2] = {3, SM_J};
    const int32_t *parents = sm_need(m, "skeleton.parents", SM_T_I32, 1, parent_dims, err, err_len);
    if (!parents) return SM_E_FORMAT;
    if (!(m->rest = sm_need(m, "skeleton.offsets", SM_T_F32, 2, offset_dims, err, err_len))) return SM_E_FORMAT;
    for (int j = 0; j < SM_J; j++) {
        const float *o = m->rest + 3 * j;
        const double len = sqrt((double)o[0] * o[0] + (double)o[1] * o[1] + (double)o[2] * o[2]);
        const int fits = j ? len > MIN_BONE && len < MAX_BONE : len == 0;
        if (parents[j] != sm_parent[j] || !fits)
            return sm_fail(err, err_len, SM_E_FORMAT, "the skeleton is not sprong-22");
    }
    return SM_OK;
}

static sm_status check_file(sm_model_impl *m, uint32_t flags, char *err, size_t err_len) {
    const sm_gguf *g = &m->g;
    uint32_t version = 0;
    if (!u32_of(g, "sprongmotion.format_version", &version))
        return sm_fail(err, err_len, SM_E_FORMAT, "no sprongmotion.format_version");
    if (version != FORMAT_VERSION)
        return sm_fail(err, err_len, SM_E_VERSION, "model format version is newer than this runtime");
    if (!u32_of(g, "sprongmotion.data_crc32", &m->crc))
        return sm_fail(err, err_len, SM_E_FORMAT, "no sprongmotion.data_crc32");
    if (!(flags & SM_LOAD_NOCHECK) && sm_crc32(g->data, g->data_size) != m->crc)
        return sm_fail(err, err_len, SM_E_CHECKSUM, "model data checksum mismatch");
    return SM_OK;
}

static sm_status read_settings(sm_model_impl *m, char *err, size_t err_len) {
    const struct { const char *key; uint32_t *v; } settings[] = {
        {"sprongmotion.fps", &m->fps}, {"sprongmotion.frames_per_token", &m->frames_per_token},
        {"sprongmotion.max_frames", &m->max_frames}, {"sprongmotion.tokenizer.max_len", &m->tok.max_len},
        {"sprongmotion.tokenizer.sot", &m->tok.sot}, {"sprongmotion.tokenizer.eot", &m->tok.eot},
    };
    for (size_t i = 0; i < sizeof settings / sizeof settings[0]; i++)
        if (!u32_of(&m->g, settings[i].key, settings[i].v))
            return sm_fail(err, err_len, SM_E_FORMAT, "missing setting");
    if (m->tok.max_len < 3 || m->tok.max_len > 64) return sm_fail(err, err_len, SM_E_FORMAT, "bad tokenizer.max_len");
    if (!m->fps || !m->frames_per_token || m->max_frames < m->frames_per_token)
        return sm_fail(err, err_len, SM_E_FORMAT, "bad frame settings");
    return SM_OK;
}

/* the string metadata and the id */
static sm_status describe(sm_model_impl *m, char *err, size_t err_len) {
    const sm_gguf *g = &m->g;
    if (!(m->meta_mem = sm_alloc(&m->h, sm_gguf_strings(g, NULL, NULL, 0, &m->n_meta) + 1)))
        return sm_fail(err, err_len, SM_E_NOMEM, "out of memory");
    sm_gguf_strings(g, m->meta_mem, m->meta, SM_MAX_META, &m->n_meta);
    const char *name = sm_meta(m, "general.name");
    snprintf(m->id, sizeof m->id, "%.64s@%08x", name ? name : "unnamed", m->crc);
    return SM_OK;
}

sm_status sm_bind_all(sm_model_impl *m, uint32_t flags, char *err, size_t err_len) {
    sm_status s = check_file(m, flags, err, err_len);
    if (s == SM_OK) s = read_settings(m, err, err_len);
    if (s == SM_OK) s = bind_tok(m, err, err_len);
    if (s == SM_OK) s = bind_vq(m, err, err_len);
    if (s == SM_OK) s = sm_bind_gen(m, err, err_len);
    if (s == SM_OK) s = bind_skeleton(m, err, err_len);
    if (s == SM_OK) s = sm_bind_params(m, err, err_len);
    if (s == SM_OK) s = describe(m, err, err_len);
    return s;
}

