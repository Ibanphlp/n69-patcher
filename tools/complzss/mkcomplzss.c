// mkcomplzss <in-raw-kernel> <out-complzss>
// 用 img4lib 的 compress_lzss 生成 Apple complzss 载荷 (供 img4 -R 打包成 rkrn)。
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include "lzss.h"

#define PUT_BE(b,o,v) do{ (b)[(o)]=(uint8_t)((v)>>24); (b)[(o)+1]=(uint8_t)((v)>>16); \
                          (b)[(o)+2]=(uint8_t)((v)>>8); (b)[(o)+3]=(uint8_t)(v); }while(0)

int main(int argc, char **argv) {
    if (argc != 3) { fprintf(stderr, "usage: %s <in> <out>\n", argv[0]); return 2; }
    FILE *f = fopen(argv[1], "rb");
    if (!f) { perror("open in"); return 1; }
    fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
    if (n <= 0 || (uint64_t)n > INT32_MAX) { fprintf(stderr, "invalid input size\n"); fclose(f); return 1; }
    uint8_t *src = malloc((size_t)n);
    if (!src) { fprintf(stderr, "out of memory\n"); fclose(f); return 1; }
    if (fread(src, 1, n, f) != (size_t)n) { perror("read"); free(src); fclose(f); return 1; }
    fclose(f);

    uint64_t capacity = (uint64_t)n + ((uint64_t)n + 7) / 8 + 1;
    if (capacity > UINT32_MAX) { fprintf(stderr, "compressed buffer is too large\n"); return 1; }
    uint8_t *buf = malloc(0x180 + (size_t)capacity);
    if (!src || !buf) { fprintf(stderr, "out of memory\n"); return 1; }
    uint8_t *end = compress_lzss(buf + 0x180, (uint32_t)capacity, src, (uint32_t)n);
    if (!end) { fprintf(stderr, "compression failed\n"); free(buf); free(src); return 1; }
    uint32_t csize = (uint32_t)(end - (buf + 0x180));
    uint32_t adler = lzadler32(src, (int32_t)n);

    PUT_BE(buf, 0, 0x636f6d70);  // "comp"
    PUT_BE(buf, 4, 0x6c7a7373);  // "lzss"
    PUT_BE(buf, 8, adler);
    PUT_BE(buf, 12, (uint32_t)n); // uncompressed size
    PUT_BE(buf, 16, csize);       // compressed size
    PUT_BE(buf, 20, 1);           // version
    memset(buf + 24, 0, 0x180 - 24);

    FILE *o = fopen(argv[2], "wb");
    if (!o) { perror("open out"); return 1; }
    fwrite(buf, 1, 0x180 + csize, o);
    fclose(o);
    fprintf(stderr, "complzss: usize=%u csize=%u adler=%08x -> %s\n", (uint32_t)n, csize, adler, argv[2]);
    return 0;
}
