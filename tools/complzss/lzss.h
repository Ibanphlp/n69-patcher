// LZSS compression for complzss - minimal implementation
// Based on Apple's LZSS used in kernelcache compression

#ifndef LZSS_H
#define LZSS_H

#include <stdint.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

// Compress using LZSS
// Returns pointer to end of compressed data
uint8_t* compress_lzss(uint8_t* dst, uint32_t dst_size, const uint8_t* src, uint32_t src_size);

// Adler-32 checksum (used in complzss header)
uint32_t lzadler32(const uint8_t* data, int32_t len);

#ifdef __cplusplus
}
#endif

#endif
