/* The matrix kernel, transformer layers (torch.nn.TransformerEncoderLayer, eval) and the VQ decoder and encoder. */
#include "sm_internal.h"

#define KC 512   /* K slice: a 32 KB panel that stays in L1 */
#define NP 16    /* panel width */
#define TR 4     /* tile rows: 16 accumulators, enough for 4 FMA pipes of latency 4 */
#define INLINE static inline __attribute__((always_inline))

float sm_half(uint16_t h) {
    const uint32_t s = (uint32_t)(h >> 15) << 31, e = (h >> 10) & 31, m = h & 1023;
    if (!e) {
        const float f = (float)m * 0x1p-24f;   /* subnormal, exact in f32 */
        return s ? -f : f;
    }
    const uint32_t b = s | (e == 31 ? 0x7F800000u : (e + 112) << 23) | m << 13;
    float f;
    memcpy(&f, &b, 4);
    return f;
}

typedef int8_t i8x16 __attribute__((vector_size(16), aligned(1)));

/* k-chunk [k, k + 4) of 4 rows -> 4 vectors over the rows: a 4 x 4 transpose */
static void transpose_store(const v4 r[4], float *buf) {
    const v4 t0 = __builtin_shufflevector(r[0], r[1], 0, 4, 1, 5), t1 = __builtin_shufflevector(r[2], r[3], 0, 4, 1, 5);
    const v4 t2 = __builtin_shufflevector(r[0], r[1], 2, 6, 3, 7), t3 = __builtin_shufflevector(r[2], r[3], 2, 6, 3, 7);
    sm_store4(buf, __builtin_shufflevector(t0, t1, 0, 1, 4, 5));
    sm_store4(buf + NP, __builtin_shufflevector(t0, t1, 2, 3, 6, 7));
    sm_store4(buf + 2 * NP, __builtin_shufflevector(t2, t3, 0, 1, 4, 5));
    sm_store4(buf + 3 * NP, __builtin_shufflevector(t2, t3, 2, 3, 6, 7));
}

/* 16 consecutive weights of a row, dequantized: 4 vectors */
static void row16(const sm_lin *l, uint32_t row, uint32_t k, v4 out[4]) {
    if (l->type == SM_T_F32) {
        const float *w = (const float *)l->w + (size_t)row * l->k + k;
        for (int i = 0; i < 4; i++) out[i] = sm_load4(w + 4 * i);
        return;
    }
    const uint8_t *block = (const uint8_t *)l->w + ((size_t)row * (l->k / 32) + k / 32) * SM_Q8_BLOCK;
    const float scale = sm_half((uint16_t)(block[0] | block[1] << 8));
    i8x16 q;
    memcpy(&q, block + 2 + k % 32, 16);
    out[0] = scale * __builtin_convertvector(__builtin_shufflevector(q, q, 0, 1, 2, 3), v4);
    out[1] = scale * __builtin_convertvector(__builtin_shufflevector(q, q, 4, 5, 6, 7), v4);
    out[2] = scale * __builtin_convertvector(__builtin_shufflevector(q, q, 8, 9, 10, 11), v4);
    out[3] = scale * __builtin_convertvector(__builtin_shufflevector(q, q, 12, 13, 14, 15), v4);
}

void sm_row(const sm_lin *table, uint32_t row, float *out) {
    for (uint32_t k = 0; k < table->k; k += 16) {
        v4 chunk[4];
        row16(table, row, k, chunk);
        for (int c = 0; c < 4; c++) sm_store4(out + k + 4 * c, chunk[c]);
    }
}

/* buf[k][j] = W[n0 + j][k0 + k] for a panel of NP columns and kc of K */
static void expand(const sm_lin *l, uint32_t n0, uint32_t k0, uint32_t kc, float *buf) {
    for (uint32_t j = 0; j < NP; j += 4)
        for (uint32_t k = 0; k < kc; k += 16) {
            v4 rows[4][4], chunk[4];
            for (uint32_t i = 0; i < 4; i++) row16(l, n0 + j + i, k0 + k, rows[i]);
            for (int c = 0; c < 4; c++) {
                for (int i = 0; i < 4; i++) chunk[i] = rows[i][c];
                transpose_store(chunk, buf + (k + 4 * (uint32_t)c) * NP + j);
            }
        }
}

/* y[R][16] (+)= x[R][kc] buf; R is a constant at each call, so the accumulators stay in registers. Each output sums
   in k order, as the naive loop. */
INLINE void tile(int R, const float *x, uint32_t K, const float *buf, uint32_t kc, float *y, uint32_t N, int more,
                 const float *b) {
    v4 a[TR][4];
    for (int r = 0; r < R; r++)
        for (int c = 0; c < 4; c++) a[r][c] = more ? sm_load4(y + (size_t)r * N + 4 * c) : (v4){0};
    for (uint32_t k = 0; k < kc; k++) {
        const float *wk = buf + k * NP;
        const v4 w[4] = {sm_load4(wk), sm_load4(wk + 4), sm_load4(wk + 8), sm_load4(wk + 12)};
        for (int r = 0; r < R; r++)
            for (int c = 0; c < 4; c++) a[r][c] += x[(size_t)r * K + k] * w[c];
    }
    for (int r = 0; r < R; r++)
        for (int c = 0; c < 4; c++) sm_store4(y + (size_t)r * N + 4 * c, b ? a[r][c] + sm_load4(b + 4 * c) : a[r][c]);
}

static void epilogue(const sm_lin *l, uint32_t M, uint32_t n0, float *Y, sm_epi epi, const float *add) {
    for (uint32_t m = 0; m < M; m++) {
        float *y = Y + (size_t)m * l->n + n0;
        if (epi == SM_EPI_GELU) {
            for (uint32_t j = 0; j < NP; j++) y[j] = sm_gelu(y[j]);
            continue;
        }
        const float *residual = add + (size_t)m * l->n + n0;
        for (uint32_t j = 0; j < NP; j++) y[j] += residual[j];
    }
}

void sm_pack(sm_lin *l, float *panels) {
    for (uint32_t n0 = 0; n0 < l->n; n0 += NP) expand(l, n0, 0, l->k, panels + (size_t)n0 * l->k);
    l->p = panels;
}

typedef struct { const sm_lin *l; const float *X; uint32_t M; float *Y; sm_epi epi; const float *add; } linear_job;

/* one task: output columns [16 i, 16 i + 16) of every row, all of K */
static void panel(void *arg, uint32_t i) {
    const linear_job *job = arg;
    const sm_lin *l = job->l;
    const uint32_t K = l->k, N = l->n, M = job->M, n0 = i * NP;
    float tmp[KC * NP];
    for (uint32_t k0 = 0; k0 < K; k0 += KC) {
        const uint32_t kc = K - k0 < KC ? K - k0 : KC;
        const float *bias = l->b && k0 + kc == K ? l->b + n0 : NULL, *x = job->X + k0;
        const float *buf = l->p ? l->p + (size_t)n0 * K + (size_t)k0 * NP : tmp;
        float *y = job->Y + n0;
        if (!l->p) expand(l, n0, k0, kc, tmp);
        uint32_t m = 0;
        for (; m + TR <= M; m += TR) tile(TR, x + (size_t)m * K, K, buf, kc, y + (size_t)m * N, N, k0 > 0, bias);
        for (; m < M; m++) tile(1, x + (size_t)m * K, K, buf, kc, y + (size_t)m * N, N, k0 > 0, bias);
    }
    if (job->epi != SM_EPI_NONE) epilogue(l, M, n0, job->Y, job->epi, job->add);
}

void sm_linear(const sm_par *par, const sm_lin *l, const float *X, uint32_t M, float *Y, sm_epi epi, const float *add) {
    linear_job job = {l, X, M, Y, epi, add};
    sm_par_for(par, l->n / NP, panel, &job);
}

#define LN_ROWS 16   /* rows per task */

typedef struct { const float *x; uint32_t M, d; sm_norm norm; double eps; float *y; } norm_job;

/* biased variance; statistics in double, summed in 4 interleaved partial sums */
static void normalize_row(const norm_job *job, const float *x, float *y) {
    const uint32_t d = job->d;
    double sum[4] = {0}, var[4] = {0};
    for (uint32_t i = 0; i < d; i++) sum[i % 4] += x[i];
    const double mean = ((sum[0] + sum[1]) + (sum[2] + sum[3])) / d;
    for (uint32_t i = 0; i < d; i++) var[i % 4] += (x[i] - mean) * (x[i] - mean);
    const double inv = 1.0 / sqrt(((var[0] + var[1]) + (var[2] + var[3])) / d + job->eps);
    for (uint32_t i = 0; i < d; i++) y[i] = (float)((x[i] - mean) * inv) * job->norm.w[i] + job->norm.b[i];
}

static void normalize_rows(void *arg, uint32_t task) {
    const norm_job *job = arg;
    const uint32_t end = (task + 1) * LN_ROWS < job->M ? (task + 1) * LN_ROWS : job->M;
    for (uint32_t m = task * LN_ROWS; m < end; m++)
        normalize_row(job, job->x + (size_t)m * job->d, job->y + (size_t)m * job->d);
}

void sm_layernorm(const sm_par *par, const float *x, uint32_t M, uint32_t d, sm_norm norm, double eps, float *y) {
    norm_job job = {x, M, d, norm, eps, y};
    sm_par_for(par, (M + LN_ROWS - 1) / LN_ROWS, normalize_rows, &job);
}

#define QB 4   /* query rows per block */

typedef struct { const float *q, *k, *v; uint32_t S, w, hd; } head;

/* p[r][j] = q_r . k_j * scale for R query rows and J keys (constants at each call). Each dot sums 4 lanes over the
   head dimension, then the lanes in a fixed order. */
INLINE void qk(int R, int J, const head *h, uint32_t i, uint32_t j, float scale, float *p, uint32_t ps) {
    v4 a[QB][4] = {{{0}}};
    for (uint32_t c = 0; c < h->hd; c += 4) {
        v4 kv[4];
        for (int t = 0; t < J; t++) kv[t] = sm_load4(h->k + (size_t)(j + (uint32_t)t) * h->w + c);
        for (int r = 0; r < R; r++) {
            const v4 qv = sm_load4(h->q + (size_t)(i + (uint32_t)r) * h->w + c);
            for (int t = 0; t < J; t++) a[r][t] += qv * kv[t];
        }
    }
    for (int r = 0; r < R; r++)
        for (int t = 0; t < J; t++) p[(size_t)r * ps + j + (uint32_t)t] = sm_sum4(a[r][t]) * scale;
}

/* in place: p = exp(p - max) zero-padded to a multiple of 4; returns 1 / sum */
static float softmax_row(float *p, uint32_t S) {
    float max = -INFINITY;
    for (uint32_t j = 0; j < S; j++) max = p[j] > max ? p[j] : max;
    for (uint32_t j = 0; j < S; j++) p[j] = sm_expf(p[j] - max);
    for (uint32_t j = S; j % 4; j++) p[j] = 0;
    v4 sum = {0};
    for (uint32_t j = 0; j < S; j += 4) sum += sm_load4(p + j);
    return 1 / sm_sum4(sum);
}

/* o_r = inv_r sum_j p[r][j] v_j for R query rows, 8 columns at a time, j in order */
INLINE void pv(int R, const head *h, const float *p, uint32_t ps, const float *inv, float *o, uint32_t od) {
    for (uint32_t c = 0; c < h->hd; c += 8) {
        v4 a[QB][2] = {{{0}}};
        for (uint32_t j = 0; j < h->S; j++) {
            const v4 v0 = sm_load4(h->v + (size_t)j * h->w + c), v1 = sm_load4(h->v + (size_t)j * h->w + c + 4);
            for (int r = 0; r < R; r++) {
                a[r][0] += p[(size_t)r * ps + j] * v0;
                a[r][1] += p[(size_t)r * ps + j] * v1;
            }
        }
        for (int r = 0; r < R; r++) {
            sm_store4(o + (size_t)r * od + c, a[r][0] * inv[r]);
            sm_store4(o + (size_t)r * od + c + 4, a[r][1] * inv[r]);
        }
    }
}

/* softmax(q k^T / sqrt(hd)) v for query rows [i, i + R) */
INLINE void rows(int R, const head *h, uint32_t i, float *p, uint32_t ps, float *o, uint32_t od) {
    const float scale = 1.0f / sqrtf((float)h->hd);
    float inv[QB];
    uint32_t j = 0;
    for (; j + 4 <= h->S; j += 4) qk(R, 4, h, i, j, scale, p, ps);
    for (; j < h->S; j++) qk(R, 1, h, i, j, scale, p, ps);
    for (int r = 0; r < R; r++) inv[r] = softmax_row(p + (size_t)r * ps, h->S);
    pv(R, h, p, ps, inv, o, od);
}

typedef struct { const float *qkv; const sm_batch *b; uint32_t d, heads, stride; float *out, *p; } attention_job;

/* one task: one head of one sequence; qkv rows hold q, k, v of all heads */
static void attend(void *arg, uint32_t task) {
    const attention_job *job = arg;
    const sm_seg seg = job->b->seg[task / job->heads];
    const uint32_t d = job->d, hd = d / job->heads, width = 3 * d, ps = job->stride;
    const float *q = job->qkv + (size_t)seg.start * width + task % job->heads * hd;
    const head h = {q, q + d, q + 2 * d, seg.len, width, hd};
    float *p = job->p + (size_t)task * QB * ps, *o = job->out + (size_t)seg.start * d + task % job->heads * hd;
    uint32_t i = 0;
    for (; i + QB <= seg.len; i += QB) rows(QB, &h, i, p, ps, o + (size_t)i * d, d);
    for (; i < seg.len; i++) rows(1, &h, i, p, ps, o + (size_t)i * d, d);
}

static void attention(const sm_par *par, const float *qkv, const sm_batch *b, uint32_t d, uint32_t heads, float *out,
                      float *p) {
    attention_job job = {qkv, b, d, heads, b->rows + 4, out, p};
    sm_par_for(par, b->n_seg * heads, attend, &job);
}

static uint32_t widest(uint32_t d, uint32_t ff) { return 3 * d > ff ? 3 * d : ff; }

size_t sm_layer_scratch_floats(uint32_t M, uint32_t d, uint32_t ff, uint32_t heads) {
    return (size_t)M * widest(d, ff) + (size_t)2 * M * d + (size_t)SM_MAX_SEGS * heads * QB * (M + 4);
}

/* scratch: big [M][max(3d, ff)] (qkv, then ff1), a and h [M][d], then the attention rows */
typedef struct { float *big, *a, *h, *p; } bufs;
static bufs carve(const sm_layer *l, uint32_t M, float *scratch) {
    const uint32_t d = l->out.n;
    float *a = scratch + (size_t)M * widest(d, l->ff1.n);
    return (bufs){scratch, a, a + (size_t)M * d, a + (size_t)2 * M * d};
}

void sm_layer_post(const sm_par *par, const sm_layer *l, const sm_batch *b, uint32_t heads, float *x, float *scratch) {
    const uint32_t M = b->rows, d = l->out.n;
    const bufs s = carve(l, M, scratch);
    sm_linear(par, &l->qkv, x, M, s.big, SM_EPI_NONE, NULL);
    attention(par, s.big, b, d, heads, s.a, s.p);
    sm_linear(par, &l->out, s.a, M, s.h, SM_EPI_ADD, x);
    sm_layernorm(par, s.h, M, d, l->ln1, 1e-5, x);
    sm_linear(par, &l->ff1, x, M, s.big, SM_EPI_GELU, NULL);
    sm_linear(par, &l->ff2, s.big, M, s.h, SM_EPI_ADD, x);
    sm_layernorm(par, s.h, M, d, l->ln2, 1e-5, x);
}

void sm_layer_pre(const sm_par *par, const sm_layer *l, const sm_batch *b, uint32_t heads, float *x, float *scratch) {
    const uint32_t M = b->rows, d = l->out.n;
    const bufs s = carve(l, M, scratch);
    sm_layernorm(par, x, M, d, l->ln1, 1e-5, s.a);
    sm_linear(par, &l->qkv, s.a, M, s.big, SM_EPI_NONE, NULL);
    attention(par, s.big, b, d, heads, s.a, s.p);
    sm_linear(par, &l->out, s.a, M, s.h, SM_EPI_ADD, x);
    sm_layernorm(par, s.h, M, d, l->ln2, 1e-5, s.a);
    sm_linear(par, &l->ff1, s.a, M, s.big, SM_EPI_GELU, NULL);
    sm_linear(par, &l->ff2, s.big, M, x, SM_EPI_ADD, s.h);
}

#define CONV_ROWS 8   /* output frames per task */

/* a 1D convolution over [T][C] frames: kernel K, dilation, stride, optionally a ReLU on its input */
typedef struct { sm_conv w; uint32_t C, O, K, dilation, stride, relu_in; } conv_shape;
typedef struct { const float *x; uint32_t T, To; conv_shape s; float *y; } conv_job;

static void conv_frame(const conv_job *job, uint32_t t, int64_t pad) {
    const conv_shape *s = &job->s;
    float *yt = job->y + (size_t)t * s->O;
    memcpy(yt, s->w.b, sizeof(float) * s->O);
    for (uint32_t k = 0; k < s->K; k++) {
        const int64_t src = (int64_t)t * s->stride + (int64_t)(k * s->dilation) - pad;
        if (src < 0 || src >= (int64_t)job->T) continue;
        const float *xs = job->x + (size_t)src * s->C, *wk = s->w.w + (size_t)k * s->C * s->O;
        for (uint32_t c = 0; c < s->C; c++) {
            const float v = s->relu_in && xs[c] < 0 ? 0 : xs[c];
            if (v == 0) continue;   /* exact, and ReLU outputs are often zero */
            const float *wc = wk + (size_t)c * s->O;
            for (uint32_t o = 0; o < s->O; o++) yt[o] += v * wc[o];
        }
    }
}

static void conv_frames(void *arg, uint32_t task) {
    const conv_job *job = arg;
    const int64_t pad = (int64_t)((job->s.K - 1) * job->s.dilation / 2);
    const uint32_t end = (task + 1) * CONV_ROWS < job->To ? (task + 1) * CONV_ROWS : job->To;
    for (uint32_t t = task * CONV_ROWS; t < end; t++) conv_frame(job, t, pad);
}

/* y[t][o] = b[o] + sum_k sum_c act(x[t stride + k dil - pad][c]) w[k][c][o], zero padding pad = (K - 1) dil / 2
   (PyTorch's padding 1 for the stride-2 K = 4 conv). Returns the output length. */
static uint32_t conv(const sm_par *par, const float *x, uint32_t T, conv_shape s, float *y) {
    const int64_t pad = (int64_t)((s.K - 1) * s.dilation / 2);
    const uint32_t To = (uint32_t)(((int64_t)T + 2 * pad - (int64_t)(s.dilation * (s.K - 1)) - 1) / s.stride + 1);
    conv_job job = {x, T, To, s, y};
    sm_par_for(par, (To + CONV_ROWS - 1) / CONV_ROWS, conv_frames, &job);
    return To;
}

/* stride 1, dilation 1, SM_VQ_W channels in */
static uint32_t conv3(const sm_par *par, const float *x, uint32_t T, sm_conv w, uint32_t out, int relu_in, float *y) {
    return conv(par, x, T, (conv_shape){w, SM_VQ_W, out, 3, 1, 1, (uint32_t)relu_in}, y);
}

static const uint32_t dilations[3] = {9, 3, 1};   /* Resnet1D with reverse_dilation: 3^2, 3^1, 3^0 */

/* x += conv2(relu(conv1(relu(x)))) per block; a, b: scratch of x's size */
static void res_stack(const sm_par *par, const sm_conv *conv1, const sm_conv *conv2, float *x, uint32_t L, float *a,
                      float *b) {
    const size_t n = (size_t)L * SM_VQ_W;
    for (int r = 0; r < 3; r++) {
        conv(par, x, L, (conv_shape){conv1[r], SM_VQ_W, SM_VQ_W, 3, dilations[r], 1, 1}, a);
        conv(par, a, L, (conv_shape){conv2[r], SM_VQ_W, SM_VQ_W, 1, 1, 1, 1}, b);
        for (size_t i = 0; i < n; i++) x[i] += b[i];
    }
}

/* code -1: the layer is left out */
static void sum_codes(const sm_vq *v, const int16_t *codes, uint32_t L, float *x) {
    const uint32_t W = SM_VQ_W;
    memset(x, 0, sizeof(float) * L * W);
    for (uint32_t t = 0; t < L; t++)
        for (uint32_t q = 0; q < SM_VQ_LAYERS; q++) {
            const int16_t id = codes[t * SM_VQ_LAYERS + q];
            if (id < 0) continue;
            const float *e = v->codebook + ((size_t)q * SM_VQ_N + (size_t)(uint16_t)id) * W;
            for (uint32_t i = 0; i < W; i++) x[(size_t)t * W + i] += e[i];
        }
}

/* activations are [T][C] */
size_t sm_vq_scratch_floats(uint32_t L) { return (size_t)3 * 2 * L * SM_VQ_W; }

void sm_vq_decode(const sm_vq *v, const int16_t *codes, uint32_t L, float *feat, float *scratch) {
    const uint32_t W = SM_VQ_W, T = 2 * L;
    const size_t n = (size_t)L * W;
    float *a = scratch, *b = a + (size_t)T * W, *c = b + (size_t)T * W;

    sum_codes(v, codes, L, a);
    conv3(v->par, a, L, v->in, W, 0, b);
    for (size_t i = 0; i < n; i++) b[i] = b[i] < 0 ? 0 : b[i];   /* the residual stream starts after this ReLU */
    res_stack(v->par, v->res1, v->res2, b, L, a, c);

    for (uint32_t t = 0; t < T; t++)   /* nearest 2x upsampling */
        memcpy(a + (size_t)t * W, b + (size_t)(t / 2) * W, sizeof(float) * W);
    conv3(v->par, a, T, v->up, W, 0, b);
    conv3(v->par, b, T, v->mid, W, 0, a);
    conv3(v->par, a, T, v->out, SM_F, 1, feat);   /* ReLU on its input (after model.3) */

    for (uint32_t t = 0; t < T; t++) {
        float *ft = feat + (size_t)t * SM_F;
        for (uint32_t i = 0; i < SM_F; i++) ft[i] = ft[i] * v->std[i] + v->mean[i];
    }
}

size_t sm_vq_enc_scratch_floats(uint32_t T) { return (size_t)T * (SM_F + SM_VQ_W) + (size_t)2 * (T / 2) * SM_VQ_W; }

/* the nearest code: the squared distance in double, the first of equal ones (torch argmin) */
static uint32_t nearest_code(const float *codebook, const float *x) {
    double best = INFINITY;
    uint32_t pick = 0;
    for (uint32_t k = 0; k < SM_VQ_N; k++) {
        const float *code = codebook + (size_t)k * SM_VQ_W;
        double d2 = 0;
        for (uint32_t i = 0; i < SM_VQ_W; i++) d2 += ((double)x[i] - code[i]) * ((double)x[i] - code[i]);
        if (d2 < best) {
            best = d2;
            pick = k;
        }
    }
    return pick;
}

/* each layer picks the nearest code to what the layers before left, the residual in float as torch subtracts it */
static void quantize(const sm_vq *v, float *residual, int16_t *codes) {
    for (uint32_t q = 0; q < SM_VQ_LAYERS; q++) {
        const float *codebook = v->codebook + (size_t)q * SM_VQ_N * SM_VQ_W;
        const uint32_t pick = nearest_code(codebook, residual);
        for (uint32_t i = 0; i < SM_VQ_W; i++) residual[i] -= codebook[(size_t)pick * SM_VQ_W + i];
        codes[q] = (int16_t)pick;
    }
}

/* out(res(down(relu(in((feat - mean) / std))))), then residual quantization */
void sm_vq_encode(const sm_vq *v, const float *feat, uint32_t T, int16_t *codes, float *scratch) {
    const sm_vq_enc *e = &v->enc;
    const uint32_t W = SM_VQ_W, L = T / 2;
    float *x = scratch, *a = x + (size_t)T * SM_F, *b = a + (size_t)T * W, *c = b + (size_t)L * W;
    for (size_t t = 0; t < T; t++)
        for (size_t i = 0; i < SM_F; i++) x[t * SM_F + i] = (feat[t * SM_F + i] - v->mean[i]) / v->std[i];

    conv(v->par, x, T, (conv_shape){e->in, SM_F, W, 3, 1, 1, 0}, a);
    conv(v->par, a, T, (conv_shape){e->down, W, W, 4, 1, 2, 1}, b);
    res_stack(v->par, e->res1, e->res2, b, L, a, c);
    conv3(v->par, b, L, e->out, W, 0, c);

    for (uint32_t t = 0; t < L; t++) quantize(v, c + (size_t)t * W, codes + t * SM_VQ_LAYERS);
}
