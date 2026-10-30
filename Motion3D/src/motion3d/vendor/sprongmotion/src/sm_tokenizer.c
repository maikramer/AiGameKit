/* CLIP's BPE tokenizer (openai/CLIP simple_tokenizer.py) with its ftfy and html.unescape cleanup. */
#include "sm_internal.h"

#define MAX_CP      0x10FFFFu
#define REPLACEMENT 0xFFFD
#define BAD_UTF8    ((size_t)-1)
#define NO_DIGIT    99
#define MAX_ENTITY  32   /* the longest HTML entity name */
#define MAX_PIECE   64   /* code points of a word the vocabulary lists */

enum { OTHER = 0, LETTER = 1, NUMBER = 2 };   /* the classes of tok.uni_class */
enum { SOT_LITERAL = 0, EOT_LITERAL = 1, N_LITERALS = 9 };

static int is_surrogate(uint64_t c) { return c >= 0xD800 && c <= 0xDFFF; }

/* BAD_UTF8 if invalid; out NULL: only checks */
static size_t utf8_decode(const char *s, size_t n, uint32_t *out, size_t cap) {
    static const uint32_t min_cp[5] = {0, 0, 0x80, 0x800, 0x10000};   /* shortest form only */
    size_t count = 0;
    size_t i = 0;
    while (i < n && count < cap) {
        const uint8_t lead = (uint8_t)s[i];
        const size_t len = lead < 0x80 ? 1 : (lead & 0xE0) == 0xC0 ? 2 : (lead & 0xF0) == 0xE0 ? 3
                         : (lead & 0xF8) == 0xF0 ? 4 : 0;
        if (!len || i + len > n) return BAD_UTF8;

        uint32_t cp = len == 1 ? lead : lead & (0x7Fu >> len);
        for (size_t j = 1; j < len; j++) {
            const uint8_t next = (uint8_t)s[i + j];
            if ((next & 0xC0) != 0x80) return BAD_UTF8;
            cp = cp << 6 | (next & 0x3Fu);
        }
        if (cp < min_cp[len] || cp > MAX_CP || is_surrogate(cp)) return BAD_UTF8;

        if (out) out[count] = cp;
        count++;
        i += len;
    }
    return count;
}

int sm_utf8_valid(const char *s, size_t n) { return utf8_decode(s, n, NULL, (size_t)-1) != BAD_UTF8; }

static int utf8_encode(uint32_t cp, uint8_t bytes[4]) {
    if (cp < 0x80) {
        bytes[0] = (uint8_t)cp;
        return 1;
    }
    const int n = cp < 0x800 ? 2 : cp < 0x10000 ? 3 : 4;
    for (int i = n - 1; i > 0; i--) {
        bytes[i] = (uint8_t)(0x80 | (cp & 63));
        cp >>= 6;
    }
    bytes[0] = (uint8_t)((0xFF00u >> n) | cp);   /* lead byte 110xxxxx, 1110xxxx or 11110xxx */
    return n;
}

static int is_space(uint32_t c) {  /* Python str.isspace */
    return (c >= 9 && c <= 13) || (c >= 0x1C && c <= 0x20) || c == 0x85 || c == 0xA0 || c == 0x1680 ||
           (c >= 0x2000 && c <= 0x200A) || c == 0x2028 || c == 0x2029 || c == 0x202F || c == 0x205F || c == 0x3000;
}

/* the first of n sorted rows whose column col is >= key */
static uint32_t search(const int32_t *rows, uint32_t n, uint32_t stride, uint32_t col, uint64_t key) {
    uint32_t lo = 0;
    uint32_t hi = n;
    while (lo < hi) {
        const uint32_t mid = (lo + hi) / 2;
        if ((uint64_t)rows[stride * mid + col] < key) lo = mid + 1;
        else hi = mid;
    }
    return lo;
}

static int uni_class(const sm_tok *t, uint32_t c) {
    const uint32_t i = search(t->uni_class, t->n_class, 3, 1, c);
    if (i < t->n_class && (uint32_t)t->uni_class[3 * i] <= c) return t->uni_class[3 * i + 2];
    return OTHER;
}

static uint32_t lower(const sm_tok *t, uint32_t c) {
    const uint32_t i = search(t->uni_lower, t->n_lower, 2, 0, c);
    const int32_t *row = t->uni_lower + 2 * i;
    if (i < t->n_lower && (uint32_t)row[0] == c && row[1] >= 0) return (uint32_t)row[1];
    return c;
}

/* ftfy's typed-text fixes: curly quotes, full-width forms, ligatures, control characters */
static size_t fix_text(const uint32_t *src, size_t n, uint32_t *dst) {
    static const char *ligatures[7] = {"ff", "fi", "fl", "ffi", "ffl", "st", "st"};   /* U+FB00..U+FB06 */
    size_t count = 0;
    for (size_t i = 0; i < n && count + 3 < SM_TOK_MAX_CP; i++) {
        uint32_t c = src[i];
        if (c == 0x2018 || c == 0x2019 || c == 0x201A || c == 0x201B || c == 0x2032) c = '\'';
        else if (c == 0x201C || c == 0x201D || c == 0x201E || c == 0x201F || c == 0x2033) c = '"';
        else if (c >= 0xFF01 && c <= 0xFF5E) c -= 0xFEE0;
        else if ((c < 32 || (c >= 127 && c < 160)) && !is_space(c)) continue;

        if (c < 0xFB00 || c > 0xFB06) {
            dst[count++] = c;
            continue;
        }
        for (const char *p = ligatures[c - 0xFB00]; *p; p++) dst[count++] = (uint8_t)*p;
    }
    return count;
}

/* the entity named exactly s[0..n), or -1 */
static int32_t entity(const sm_tok *t, const uint32_t *s, size_t n) {
    uint32_t lo = 0;
    uint32_t hi = t->n_ent;
    while (lo < hi) {
        const uint32_t mid = (lo + hi) / 2;
        const int32_t *row = t->ent_index + 4 * mid, *name = t->ent_names + row[0];
        const size_t len = (size_t)row[1];
        int cmp = 0;
        for (size_t i = 0; !cmp && i < n && i < len; i++) cmp = (int)s[i] - name[i];
        if (!cmp) cmp = n < len ? -1 : n > len;
        if (!cmp) return (int32_t)mid;
        if (cmp < 0) hi = mid;
        else lo = mid + 1;
    }
    return -1;
}

static int ends_name(uint32_t c) {
    return c == '\t' || c == '\n' || c == '\f' || c == ' ' || c == '<' || c == '&' || c == '#' || c == ';';
}

static uint32_t digit(uint32_t c, int hex) {
    if (c >= '0' && c <= '9') return c - '0';
    const uint32_t low = c | 32;
    return hex && low >= 'a' && low <= 'f' ? low - 'a' + 10 : NO_DIGIT;
}

/* Python's html._replace_charref: its table of fixes, U+FFFD past Unicode and for surrogates; -1 drops it */
static int64_t ref_codepoint(const sm_tok *t, uint64_t v) {
    const uint32_t f = search(t->num_fix, t->n_num_fix, 2, 0, v);
    if (f < t->n_num_fix && (uint64_t)t->num_fix[2 * f] == v) return t->num_fix[2 * f + 1];
    if (is_surrogate(v) || v > MAX_CP) return REPLACEMENT;
    return (int64_t)v;
}

/* &#N; or &#xH; at s[i], ';' optional; *last: the last character it used */
static int char_ref(const sm_tok *t, const uint32_t *s, size_t n, size_t i, uint32_t *dst, size_t *count,
                    size_t *last) {
    const int hex = i + 2 < n && (s[i + 2] | 32) == 'x';
    const size_t first = i + 2 + (size_t)hex;
    size_t j = first;
    uint64_t v = 0;
    for (uint32_t d; j < n && (d = digit(s[j], hex)) != NO_DIGIT; j++)
        v = v > MAX_CP ? v : v * (hex ? 16u : 10u) + d;   /* saturates: above MAX_CP gives U+FFFD */
    if (j == first) return 0;

    const int64_t cp = ref_codepoint(t, v);
    if (cp >= 0) dst[(*count)++] = (uint32_t)cp;
    *last = j < n && s[j] == ';' ? j : j - 1;
    return 1;
}

/* a named reference at s[i] with its ';', else the longest known prefix (legacy names such as "amp" need no ';') */
static int named_ref(const sm_tok *t, const uint32_t *s, size_t n, size_t i, uint32_t *dst, size_t *count,
                     size_t *last) {
    size_t j = i + 1;
    while (j < n && j - i - 1 < MAX_ENTITY && !ends_name(s[j])) j++;
    j += (size_t)(j < n && s[j] == ';');
    size_t len = j - i - 1;
    int32_t e = entity(t, s + i + 1, len);
    while (e < 0 && len > 2) e = entity(t, s + i + 1, --len);
    if (e < 0) return 0;

    const int32_t *row = t->ent_index + 4 * e;
    dst[(*count)++] = (uint32_t)row[2];
    if (row[3] >= 0) dst[(*count)++] = (uint32_t)row[3];
    for (size_t q = i + 1 + len; q < j; q++) dst[(*count)++] = s[q];
    *last = j - 1;
    return 1;
}

/* html.unescape, matched as Python does */
static size_t unescape(const sm_tok *t, const uint32_t *s, size_t n, uint32_t *dst) {
    size_t count = 0;
    for (size_t i = 0; i < n; i++) {
        const int amp = s[i] == '&' && i + 1 < n;
        size_t last = i;
        int replaced = 0;
        if (amp && s[i + 1] == '#') replaced = char_ref(t, s, n, i, dst, &count, &last);
        else if (amp && !ends_name(s[i + 1])) replaced = named_ref(t, s, n, i, dst, &count, &last);
        if (replaced) i = last;
        else dst[count++] = s[i];
    }
    return count;
}

static uint32_t pair_hash(uint64_t key) {
    return ((uint32_t)(key >> 32) * 0x9E3779B1u) ^ ((uint32_t)key * 0x85EBCA77u);
}

static uint64_t pair_key(int32_t a, int32_t b) { return (uint64_t)(uint32_t)a << 32 | (uint32_t)b; }

/* open addressing, at most half full */
size_t sm_tok_build(sm_tok *t, void *table) {
    uint32_t size = 1;
    while (size < 2 * t->n_merges) size <<= 1;
    if (!table) return (size_t)size * (sizeof(uint64_t) + sizeof(int32_t));

    t->pair_key = table;
    t->pair_rank = (int32_t *)(t->pair_key + size);
    t->pair_mask = size - 1;
    memset(t->pair_rank, 0xFF, sizeof(int32_t) * size);   /* -1 = empty */
    for (uint32_t rank = 0; rank < t->n_merges; rank++) {
        const uint64_t key = pair_key(t->merges[2 * rank], t->merges[2 * rank + 1]);
        uint32_t h = pair_hash(key) & t->pair_mask;
        while (t->pair_rank[h] >= 0) h = (h + 1) & t->pair_mask;
        t->pair_key[h] = key;
        t->pair_rank[h] = (int32_t)rank;
    }
    return 0;
}

static int32_t rank_of(const sm_tok *t, int32_t a, int32_t b) {
    const uint64_t key = pair_key(a, b);
    for (uint32_t h = pair_hash(key) & t->pair_mask; t->pair_rank[h] >= 0; h = (h + 1) & t->pair_mask)
        if (t->pair_key[h] == key) return t->pair_rank[h];
    return -1;
}

static int32_t lowest_rank(const sm_tok *t, const int32_t *sym, int32_t n) {
    int32_t best = -1;
    for (int32_t i = 0; i + 1 < n; i++) {
        const int32_t rank = rank_of(t, sym[i], sym[i + 1]);
        if (rank >= 0 && (best < 0 || rank < best)) best = rank;
    }
    return best;
}

/* merge the lowest-ranked pair until none is left; returns the symbol count */
static int32_t bpe(const sm_tok *t, int32_t *sym, int32_t n) {
    for (int32_t rank; (rank = lowest_rank(t, sym, n)) >= 0;) {
        const int32_t a = t->merges[2 * rank], b = t->merges[2 * rank + 1];
        int32_t kept = 0;
        for (int32_t i = 0; i < n; kept++) {
            const int pair = i + 1 < n && sym[i] == a && sym[i + 1] == b;
            sym[kept] = pair ? SM_TOK_BASE + rank : sym[i];
            i += 1 + pair;
        }
        n = kept;
    }
    return n;
}

static size_t match(const uint32_t *s, size_t n, size_t i, const char *literal) {
    size_t len = 0;
    while (literal[len] && i + len < n && s[i + len] == (uint8_t)literal[len]) len++;
    return literal[len] ? 0 : len;
}

/* CLIP's basic_clean (unescaped twice) and lowercase, into w->cp[1]. Not covered: NFC composition, ftfy's mojibake
   repair, rare entity names */
static size_t clean_text(const sm_tok *t, const char *utf8, size_t len, sm_tok_work *w) {
    uint32_t *a = w->cp[0], *b = w->cp[1];
    size_t n = utf8_decode(utf8, len, a, SM_TOK_MAX_CP);
    if (n == BAD_UTF8) return BAD_UTF8;

    n = fix_text(a, n, b);
    n = unescape(t, b, n, a);
    n = unescape(t, a, n, b);
    for (size_t i = 0; i < n; i++) b[i] = lower(t, b[i]);
    return n;
}

/* the end of the piece of CLIP's pattern at s[i] (not a space), alternatives in its order; *literal: which one */
static size_t piece_end(const sm_tok *t, const uint32_t *s, size_t n, size_t i, int *literal) {
    static const char *literals[N_LITERALS] = {"<|startoftext|>", "<|endoftext|>", "'s", "'t", "'re", "'ve", "'m",
                                               "'ll", "'d"};
    size_t len = 0;
    int k = 0;
    while (k < N_LITERALS && !(len = match(s, n, i, literals[k]))) k++;
    *literal = k;
    if (len) return i + len;

    size_t end = i + 1;
    const int cls = uni_class(t, s[i]);
    if (cls == LETTER)
        while (end < n && uni_class(t, s[end]) == LETTER) end++;
    else if (cls == OTHER)
        while (end < n && !is_space(s[end]) && uni_class(t, s[end]) == OTHER) end++;
    return end;
}

/* the piece's UTF-8 bytes as symbols, the last one marked end of word, then merged */
static int32_t piece_symbols(const sm_tok *t, const uint32_t *s, size_t start, size_t end, int32_t *sym) {
    int32_t n = 0;
    for (size_t c = start; c < end; c++) {
        uint8_t bytes[4];
        const int n_bytes = utf8_encode(s[c], bytes);
        for (int q = 0; q < n_bytes && n < SM_TOK_MAX_CP; q++) sym[n++] = t->byte_id[bytes[q]];
    }
    sym[n - 1] += SM_TOK_BYTES;
    return bpe(t, sym, n);
}

static void add_id(sm_tok_work *w, int32_t *ids, int32_t *n_ids, int32_t id, size_t start, size_t end) {
    w->span[*n_ids][0] = (uint32_t)start;
    w->span[*n_ids][1] = (uint32_t)end;
    ids[(*n_ids)++] = id;
}

int32_t sm_tok_encode(const sm_tok *t, const char *utf8, size_t len, sm_tok_work *w, int32_t *out) {
    const size_t n = clean_text(t, utf8, len, w);
    if (n == BAD_UTF8) return -1;

    const uint32_t *s = w->cp[1];
    const int32_t room = (int32_t)t->max_len - 2;
    int32_t ids[64], n_ids = 0;
    size_t i = 0;
    while (i < n && n_ids < room) {
        if (is_space(s[i])) {
            i++;
            continue;
        }
        int literal;
        const size_t end = piece_end(t, s, n, i, &literal);
        if (literal == SOT_LITERAL || literal == EOT_LITERAL) {
            add_id(w, ids, &n_ids, (int32_t)(literal == EOT_LITERAL ? t->eot : t->sot), i, end);
        } else {
            const int32_t n_sym = piece_symbols(t, s, i, end, w->sym);
            for (int32_t q = 0; q < n_sym && n_ids < room; q++) add_id(w, ids, &n_ids, w->sym[q], i, end);
        }
        i = end;
    }

    out[0] = t->remap[t->sot];
    for (int32_t q = 0; q < n_ids; q++) out[q + 1] = t->remap[ids[q]];
    out[n_ids + 1] = t->remap[t->eot];
    for (uint32_t q = (uint32_t)n_ids + 2; q < t->max_len; q++) out[q] = (int32_t)t->pad;
    return n_ids + 2;
}

/* symbol id -> its bytes, inv: the byte symbols' inverse; 0 if longer than cap (each pending symbol is >= 1 byte) */
static size_t expand(const sm_tok *t, int32_t id, const uint8_t *inv, uint8_t *out, size_t cap) {
    int32_t stack[64];
    size_t n = 0;
    size_t top = 0;
    stack[top++] = id;
    while (top) {
        const int32_t sym = stack[--top];
        if (sym < SM_TOK_BASE) {
            out[n++] = inv[sym % SM_TOK_BYTES];
            continue;
        }
        if (n + top + 2 > cap || top + 2 > sizeof stack / sizeof stack[0]) return 0;
        stack[top++] = t->merges[2 * (sym - SM_TOK_BASE) + 1];
        stack[top++] = t->merges[2 * (sym - SM_TOK_BASE)];
    }
    return n;
}

static int ends_word(const sm_tok *t, int32_t id) {
    while (id >= SM_TOK_BASE) id = t->merges[2 * (id - SM_TOK_BASE) + 1];
    return id >= SM_TOK_BYTES;
}

/* id's bytes when it is a lowercase word that is this one token when typed alone, else 0 */
static size_t whole_word(const sm_tok *t, int32_t id, const uint8_t *inv, uint8_t *bytes) {
    uint32_t cp[MAX_PIECE];
    int32_t sym[MAX_PIECE];
    if (t->remap[id] == (int32_t)t->unk || !ends_word(t, id)) return 0;

    const size_t n = expand(t, id, inv, bytes, MAX_PIECE);
    const size_t n_cp = n ? utf8_decode((const char *)bytes, n, cp, MAX_PIECE) : 0;
    if (!n_cp || n_cp == BAD_UTF8) return 0;
    for (size_t i = 0; i < n_cp; i++)
        if (uni_class(t, cp[i]) != LETTER || lower(t, cp[i]) != cp[i]) return 0;

    for (size_t i = 0; i < n; i++) sym[i] = t->byte_id[bytes[i]] + (i + 1 == n ? SM_TOK_BYTES : 0);
    return bpe(t, sym, (int32_t)n) == 1 && sym[0] == id ? n : 0;
}

size_t sm_tok_words(const sm_tok *t, char *buf, uint32_t *count) {
    uint8_t inv[SM_TOK_BYTES] = {0}, bytes[MAX_PIECE];
    size_t used = 0;
    for (int b = 0; b < SM_TOK_BYTES; b++) inv[t->byte_id[b]] = (uint8_t)b;
    *count = 0;
    for (int32_t id = SM_TOK_BYTES; id < (int32_t)t->sot; id++) {
        const size_t n = whole_word(t, id, inv, bytes);
        if (!n) continue;
        if (buf) {
            memcpy(buf + used, bytes, n);
            buf[used + n] = 0;
        }
        used += n + 1;
        ++*count;
    }
    return used;
}

size_t sm_tok_unknown(const sm_tok *t, const sm_tok_work *w, const int32_t *ids, int32_t n, char *buf, size_t cap,
                      uint32_t *count) {
    size_t need = 1;
    size_t used = 0;
    *count = 0;
    for (int32_t q = 1, last = -1; q + 1 < n; q++) {   /* ids[q] came from span[q - 1] */
        if (ids[q] != (int32_t)t->unk || (last >= 0 && w->span[last][0] == w->span[q - 1][0])) continue;
        last = q - 1;

        uint8_t word[4 * MAX_PIECE];
        size_t len = 0;
        for (uint32_t c = w->span[last][0]; c < w->span[last][1] && len + 4 <= sizeof word; c++)
            len += (size_t)utf8_encode(w->cp[1][c], word + len);
        need += len + (need > 1);
        if (!buf || used + (used > 0) + len + 1 > cap) continue;

        if (used) buf[used++] = '\n';
        memcpy(buf + used, word, len);
        used += len;
        ++*count;
    }
    if (buf && cap) buf[used] = 0;
    return need;
}
