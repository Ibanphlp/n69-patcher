// Apple complzss LZSS encoder: 4096-byte ring, absolute ring positions,
// matches of 3..18 bytes, and LSB-first literal flags.
#include "lzss.h"
#include <stdlib.h>
#include <string.h>

#define WINDOW_SIZE 4096
#define MAX_MATCH 18
#define MIN_MATCH 3
#define HASH_SIZE 65536
#define SEARCH_LIMIT 128

uint32_t lzadler32(const uint8_t* data, int32_t len) {
    uint32_t a = 1, b = 0;
    for (int32_t i = 0; i < len; i++) {
        a = (a + data[i]) % 65521;
        b = (b + a) % 65521;
    }
    return (b << 16) | a;
}

static unsigned hash3(const uint8_t *p) {
    return ((unsigned)p[0] * 251U * 251U + (unsigned)p[1] * 251U + p[2]) & (HASH_SIZE - 1);
}

uint8_t* compress_lzss(uint8_t* dst, uint32_t dst_size, const uint8_t* src, uint32_t src_size) {
    int32_t *head = malloc(HASH_SIZE * sizeof(*head));
    int32_t prev[WINDOW_SIZE];
    if (!head) return NULL;
    for (unsigned i = 0; i < HASH_SIZE; ++i) head[i] = -1;
    for (unsigned i = 0; i < WINDOW_SIZE; ++i) prev[i] = -1;

    uint32_t pos = 0, written = 0;
    while (pos < src_size) {
        if (written >= dst_size) { free(head); return NULL; }
        uint32_t flag_pos = written++;
        uint8_t flags = 0;
        for (unsigned bit = 0; bit < 8 && pos < src_size; ++bit) {
            unsigned best_len = 0;
            int32_t best_pos = -1;
            if (src_size - pos >= MIN_MATCH) {
                int32_t candidate = head[hash3(src + pos)];
                unsigned checked = 0;
                while (candidate >= 0 && pos - (uint32_t)candidate <= WINDOW_SIZE && checked++ < SEARCH_LIMIT) {
                    unsigned len = 0;
                    while (len < MAX_MATCH && pos + len < src_size && src[candidate + len] == src[pos + len]) ++len;
                    if (len > best_len) {
                        best_len = len;
                        best_pos = candidate;
                        if (len == MAX_MATCH) break;
                    }
                    candidate = prev[(unsigned)candidate & (WINDOW_SIZE - 1)];
                }
            }
            unsigned count;
            if (best_len >= MIN_MATCH) {
                if (dst_size - written < 2) { free(head); return NULL; }
                unsigned index = ((unsigned)best_pos + WINDOW_SIZE - MAX_MATCH) & (WINDOW_SIZE - 1);
                dst[written++] = (uint8_t)index;
                dst[written++] = (uint8_t)(((index >> 4) & 0xf0) | (best_len - MIN_MATCH));
                count = best_len;
            } else {
                if (written >= dst_size) { free(head); return NULL; }
                flags |= (uint8_t)(1U << bit);
                dst[written++] = src[pos];
                count = 1;
            }
            for (unsigned i = 0; i < count; ++i) {
                uint32_t current = pos + i;
                if (src_size - current >= MIN_MATCH) {
                    unsigned hash = hash3(src + current);
                    prev[current & (WINDOW_SIZE - 1)] = head[hash];
                    head[hash] = (int32_t)current;
                }
            }
            pos += count;
        }
        dst[flag_pos] = flags;
    }
    free(head);
    return dst + written;
}
