/* Sampling noise, top-k picks, the mask schedule, and exp, log and float16 rounding with the same bits everywhere. */
#include "sm_internal.h"

#define PICK_ROWS 4      /* rows per task */
#define MAX_CODES 513    /* the widest logit row: the codes and the pad code */

static uint64_t splitmix64(uint64_t x) {
    x += 0x9E3779B97F4A7C15ull;
    x = (x ^ (x >> 30)) * 0xBF58476D1CE4E5B9ull;
    x = (x ^ (x >> 27)) * 0x94D049BB133111EBull;
    return x ^ (x >> 31);
}

/* h = splitmix64(seed ^ splitmix64(stream << 48 | step << 32 | pos << 12 | code)), u = ((h >> 11) + 0.5) 2^-53,
   g = -log(-log(u)); stream 0: base steps, 1: residual layers */
float sm_hash_gumbel(uint64_t seed, uint32_t stream, uint32_t step, uint32_t pos, uint32_t code) {
    const uint64_t key = (uint64_t)stream << 48 | (uint64_t)step << 32 | (uint64_t)pos << 12 | code;
    const double u = ((double)(splitmix64(seed ^ splitmix64(key)) >> 11) + 0.5) * 0x1p-53;
    return (float)-sm_log(-sm_log(u));
}

/* quickselect with a 3-way split: [> pivot | == pivot | < pivot] */
static float kth_largest(float *v, uint32_t n, uint32_t k) {
    uint32_t lo = 0;
    uint32_t hi = n;
    const uint32_t want = k - 1;
    for (;;) {
        const float pivot = v[lo + (hi - lo) / 2];
        uint32_t above = lo, i = lo, below = hi;
        while (i < below) {
            const float x = v[i];
            if (x > pivot) {
                v[i++] = v[above];
                v[above++] = x;
            } else if (x < pivot) {
                v[i] = v[--below];
                v[below] = x;
            } else {
                i++;
            }
        }
        if (want < above) hi = above;
        else if (want < below) return pivot;
        else lo = below;
    }
}

static float softmax_at(const float *x, uint32_t N, int32_t pick) {
    float max = x[0];
    double sum = 0;
    for (uint32_t c = 1; c < N; c++) max = x[c] > max ? x[c] : max;
    for (uint32_t c = 0; c < N; c++) sum += sm_exp(x[c] - max);
    return (float)(sm_exp(x[pick] - max) / sum);
}

static void pick_row(const sm_picking *p, uint32_t j, float *tmp) {
    const uint32_t N = p->N, k = p->top_k;
    const float *x = p->logits + (size_t)j * N;
    memcpy(tmp, x, sizeof(float) * N);
    const float kth = k && k < N ? kth_largest(tmp, N, k) : -INFINITY;
    int32_t best = -1;
    float best_v = 0;
    for (uint32_t c = 0; c < N; c++) {
        if (x[c] < kth) continue;   /* outside the top k (ties are kept) */
        const float v = x[c] / p->temp + p->noise(p->user, p->step, j, c);
        if (best < 0 || v > best_v) {
            best = (int32_t)c;
            best_v = v;
        }
    }
    p->pick[j] = best;
    if (p->prob) p->prob[j] = softmax_at(x, N, best);
}

void sm_pick(const float *lg, uint32_t L, uint32_t N, const uint8_t *rows, float temp, uint32_t k,
             sm_noise_fn noise, void *user, uint32_t step, int32_t *pick, float *prob, float *tmp) {
    const sm_picking p = {lg, L, N, rows, temp, k, noise, user, step, pick, prob};
    for (uint32_t j = 0; j < L; j++)
        if (!rows || rows[j]) pick_row(&p, j, tmp);
}

static void pick_rows(void *arg, uint32_t task) {
    const sm_picking *p = arg;
    float tmp[MAX_CODES];
    for (uint32_t j = task * PICK_ROWS; j < p->L && j < (task + 1) * PICK_ROWS; j++)
        if (!p->rows || p->rows[j]) pick_row(p, j, tmp);
}

void sm_pick_rows(const sm_par *par, const sm_picking *p) {
    sm_par_for(par, (p->L + PICK_ROWS - 1) / PICK_ROWS, pick_rows, (void *)p);
}

#define PI 3.14159265358979323846

/* MoMask's cosine schedule (Guo et al. 2024, arXiv 2312.00063) */
uint32_t sm_nmask(uint32_t i, uint32_t n, uint32_t free) {
    const double t = n > 1 ? (double)i / (double)(n - 1) : 0.0;   /* np.linspace(0, 1, n)[i] */
    const double v = rint(cos(t * PI / 2) * (double)free);         /* rint: half to even, as Python round() */
    return v < 1 ? 1 : (uint32_t)v;
}

void sm_lowest(const float *score, uint32_t L, uint32_t nmask, uint8_t *mask) {
    memset(mask, 0, L);
    for (uint32_t c = 0; c < nmask && c < L; c++) {
        uint32_t best = L;
        for (uint32_t j = 0; j < L; j++) if (!mask[j] && (best == L || score[j] < score[best])) best = j;
        mask[best] = 1;
    }
}

static double pow2i(int64_t n) {   /* 2^n for -1022 <= n <= 1023 */
    const uint64_t b = (uint64_t)(n + 1023) << 52;
    double d;
    memcpy(&d, &b, 8);
    return d;
}

/* + - * / only, so every target gives the same bits: exp(x) = 2^n exp(r), |r| <= ln2/2, Taylor to r^13 */
double sm_exp(double x) {
    if (x != x) return x;
    if (x < -745) return 0;
    if (x > 709) return INFINITY;
    const double ln2_hi = 6.93147180369123816490e-01, ln2_lo = 1.90821492927058770002e-10;
    const double k = x * 1.4426950408889634 + (x < 0 ? -0.5 : 0.5);
    const int64_t n = (int64_t)k;
    const double r = x - (double)n * ln2_hi - (double)n * ln2_lo;
    static const double inv_fact[14] = {1, 1, 1 / 2.0, 1 / 6.0, 1 / 24.0, 1 / 120.0, 1 / 720.0, 1 / 5040.0, 1 / 40320.0,
                                        1 / 362880.0, 1 / 3628800.0, 1 / 39916800.0, 1 / 479001600.0, 1 / 6227020800.0};
    double p = inv_fact[13];
    for (int i = 12; i >= 0; i--) p = inv_fact[i] + r * p;
    return n < -1022 ? p * pow2i(n + 54) * 0x1p-54 : p * pow2i(n);
}

/* x > 0: x = m 2^e, m in [sqrt(1/2), sqrt(2)); log m = 2 atanh(s), s = (m - 1) / (m + 1), |s| < 0.172 */
double sm_log(double x) {
    if (!(x > 0)) return x == 0 ? -INFINITY : NAN;
    if (x == INFINITY) return x;
    uint64_t b;
    memcpy(&b, &x, 8);
    int64_t e = (int64_t)(b >> 52) - 1023;
    if (e == -1023) {   /* subnormal */
        x *= 0x1p54;
        memcpy(&b, &x, 8);
        e = (int64_t)(b >> 52) - 1023 - 54;
    }
    b = (b & 0x000FFFFFFFFFFFFFull) | 0x3FF0000000000000ull;
    double m;
    memcpy(&m, &b, 8);
    if (m > 1.4142135623730951) {
        m *= 0.5;
        e++;
    }
    const double s = (m - 1) / (m + 1), s2 = s * s;
    double p = 0;
    for (int k = 23; k >= 1; k -= 2) p = 1.0 / k + s2 * p;
    return 2 * s * p + (double)e * 6.93147180559945309417e-01;
}

float sm_round_half(float x) {   /* to the nearest float16 and back, ties to even (torch .half().float()) */
    uint32_t b;
    memcpy(&b, &x, 4);
    const uint32_t s = b & 0x80000000u, a = b & 0x7FFFFFFFu;
    if (a >= 0x7F800000u) return x;
    if (a >= 0x477FF000u) b = s | 0x7F800000u;   /* rounds to >= 65536: inf */
    else if (a >= 0x38800000u) b = s | ((a + 0x0FFFu + ((a >> 13) & 1)) & ~0x1FFFu);   /* normal: 10 mantissa bits */
    else {   /* subnormal half: multiples of 2^-24; adding 2^23 rounds to an integer, half to even */
        const float v = ((s ? -x : x) * 0x1p24f + 0x1p23f - 0x1p23f) * 0x1p-24f;
        return s ? -v : v;
    }
    memcpy(&x, &b, 4);
    return x;
}
