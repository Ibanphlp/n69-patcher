// Minimal LZSS compression for complzss
// Based on standard LZSS with 4KB window, 18-bit offset/length encoding

#include "lzss.h"
#include <string.h>

#define WINDOW_SIZE 4096
#define MAX_MATCH 258
#define THRESHOLD 3

// Adler-32 checksum
uint32_t lzadler32(const uint8_t* data, int32_t len) {
    uint32_t a = 1, b = 0;
    for (int32_t i = 0; i < len; i++) {
        a = (a + data[i]) % 65521;
        b = (b + a) % 65521;
    }
    return (b << 16) | a;
}

// Simple LZSS compression (not optimal but functional)
uint8_t* compress_lzss(uint8_t* dst, uint32_t dst_size, const uint8_t* src, uint32_t src_size) {
    uint8_t* dst_start = dst;
    uint8_t* dst_end = dst + dst_size;
    uint32_t src_pos = 0;
    
    // Output buffer for flags and data
    uint8_t flags = 0;
    int flag_bit = 0;
    uint8_t* flag_pos = dst++;
    
    while (src_pos < src_size && dst < dst_end - 4) {
        // Find best match in window
        int best_len = 0;
        int best_offset = 0;
        int window_start = (src_pos > WINDOW_SIZE) ? src_pos - WINDOW_SIZE : 0;
        
        for (int i = window_start; i < (int)src_pos; i++) {
            int len = 0;
            while (len < MAX_MATCH && src_pos + len < src_size && 
                   src[i + len] == src[src_pos + len]) {
                len++;
            }
            if (len > best_len) {
                best_len = len;
                best_offset = src_pos - i;
                if (best_len >= MAX_MATCH) break;
            }
        }
        
        if (best_len >= THRESHOLD && dst + 2 <= dst_end) {
            // Output match: flag=0, then offset/length
            flags &= ~(1 << flag_bit);
            // Encode: 12 bits offset, 4 bits length-3 (Apple format)
            // Simplified: 2 bytes for offset (12 bits) + length (4 bits)
            *dst++ = (best_offset >> 4) & 0xFF;
            *dst++ = ((best_offset & 0xF) << 4) | ((best_len - 3) & 0xF);
            src_pos += best_len;
        } else {
            // Output literal: flag=1, then byte
            flags |= (1 << flag_bit);
            if (dst >= dst_end) break;
            *dst++ = src[src_pos++];
        }
        
        flag_bit++;
        if (flag_bit == 8) {
            *flag_pos = flags;
            flag_bit = 0;
            flags = 0;
            if (dst >= dst_end) break;
            flag_pos = dst++;
        }
    }
    
    // Flush remaining flags
    if (flag_bit > 0) {
        *flag_pos = flags;
    }
    
    return dst;
}
