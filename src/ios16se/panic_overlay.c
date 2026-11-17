/* Draws a panic onto the live PE_Video framebuffer. No locks and no allocator.
 * The iOS 16 PE_Video layout is base, row bytes, width, height, depth,
 * each an unsigned long, which is what getConsoleInfo copies.
 */
typedef unsigned char u8;
typedef unsigned int u32;
typedef unsigned long long u64;

#define PD_MAGIC 0x314b50414e494343ULL
#define MESSAGE_CAP 512

struct pe_video {
    u64 base;
    u64 stride;
    u64 width;
    u64 height;
    u64 depth;
};
struct pd_state {
    u64 magic;
    u32 busy, stage, length, render_count;
    u64 caller, slide, options;
    u32 frame_count, reserved;
    u64 frames[12];
    char message[MESSAGE_CAP];
};
_Static_assert(sizeof(struct pd_state) <= 1024, "panic state does not fit its page");

extern struct pd_state *pd_context(void);
extern struct pe_video *pd_vinfo(void);
extern u64 pd_slide(void);
extern void pd_doprnt(const char *, const u64 *, void (*)(int), int);

static const char glyph_names[] = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ-_:./()[]<>+=!?%#,@\\'\"*|&";
static const u8 glyphs[][5] = {
 {62,81,73,69,62},{0,66,127,64,0},{66,97,81,73,70},{33,65,69,75,49},
 {24,20,18,127,16},{39,69,69,69,57},{60,74,73,73,48},{1,113,9,5,3},
 {54,73,73,73,54},{6,73,73,41,30},
 {126,17,17,17,126},{127,73,73,73,54},{62,65,65,65,34},{127,65,65,34,28},
 {127,73,73,73,65},{127,9,9,9,1},{62,65,73,73,122},{127,8,8,8,127},
 {0,65,127,65,0},{32,64,65,63,1},{127,8,20,34,65},{127,64,64,64,64},
 {127,2,12,2,127},{127,4,8,16,127},{62,65,65,65,62},{127,9,9,9,6},
 {62,65,81,33,94},{127,9,25,41,70},{70,73,73,73,49},{1,1,127,1,1},
 {63,64,64,64,63},{31,32,64,32,31},{63,64,56,64,63},{99,20,8,20,99},
 {7,8,112,8,7},{97,81,73,69,67},
 {8,8,8,8,8},{64,64,64,64,64},{0,54,54,0,0},{0,96,96,0,0},
 {32,16,8,4,2},{0,28,34,65,0},{0,65,34,28,0},{0,127,65,65,0},
 {0,65,65,127,0},{8,20,34,65,0},{0,65,34,20,8},{8,8,62,8,8},
 {20,20,20,20,20},{0,0,95,0,0},{2,1,81,9,6},{35,19,8,100,98},
 {20,127,20,127,20},{0,80,48,0,0},{62,65,93,85,30},{2,4,8,16,32},
 {0,5,3,0,0},{0,7,0,7,0},{20,8,62,8,20},{0,0,127,0,0},
 {54,73,85,34,80}
};
_Static_assert(sizeof(glyphs) / 5 == sizeof(glyph_names) - 1, "glyph table mismatch");

struct canvas {
    volatile u32 *base;
    u32 width, height, pitch, x, y, scale, color;
};

static void put(struct canvas *c, int ch) {
    if (ch == '\r') return;
    if (ch == '\n') { c->x = 8; c->y += 9 * c->scale; return; }
    if (c->x + 6 * c->scale >= c->width) { c->x = 8; c->y += 9 * c->scale; }
    if (c->y + 7 * c->scale >= c->height) return;
    if (ch >= 'a' && ch <= 'z') ch -= 32;
    if (ch != ' ') {
        unsigned index = 0;
        while (glyph_names[index] && glyph_names[index] != ch) ++index;
        for (u32 x = 0; x < 5; ++x) {
            u8 bits = glyph_names[index] ? glyphs[index][x] : 127;
            for (u32 y = 0; y < 7; ++y) if (bits & (1U << y))
                for (u32 dy = 0; dy < c->scale; ++dy)
                    for (u32 dx = 0; dx < c->scale; ++dx)
                        c->base[(c->y + y * c->scale + dy) * c->pitch + c->x + x * c->scale + dx] = c->color;
        }
    }
    c->x += 6 * c->scale;
}

static void text(struct canvas *c, const char *s, u32 max) {
    for (u32 i = 0; i < max && s[i]; ++i) put(c, (u8)s[i]);
}

static void hex(struct canvas *c, u64 value) {
    text(c, "0X", 2);
    for (int bit = 60; bit >= 0; bit -= 4) {
        unsigned d = (unsigned)(value >> bit) & 15;
        put(c, d < 10 ? '0' + d : 'A' + d - 10);
    }
}

__attribute__((noinline)) void pd_render(void) {
    struct pd_state *s = pd_context();
    if (s->magic != PD_MAGIC) return;
    if (s->render_count >= 4) return;
    struct pe_video *v = pd_vinfo();
    if (!v) return;
    u64 base = v->base;
    u64 width = v->width, height = v->height, stride = v->stride, depth = v->depth;
    if (!base || !width || !height || !stride || (base >> 48) != 0xffff || (base & 3) || width < 80 || width > 2048 ||
        height < 80 || height > 2048 || stride < width * 4 || stride > 16384 || (stride & 3) ||
        (depth != 32 && depth != 0)) return;
    struct canvas c = {(volatile u32 *)base, (u32)width, (u32)height, (u32)(stride / 4),
                       8, 8, width >= 480 ? 2 : 1, 0x00ffff60};
    for (u32 y = 0; y < c.height; ++y)
        for (u32 x = 0; x < c.width; ++x) c.base[y * c.pitch + x] = 0x00200000;
    text(&c, "N69 PANIC\n", 16);
    text(&c, s->stage < 3 ? "ENTRY\n" : "REASON\n", 16);
    c.color = 0x00ffffff;
    text(&c, s->message, MESSAGE_CAP);
    text(&c, "\nCALLER ", 9); hex(&c, s->caller);
    text(&c, "\nUNSLID ", 9); hex(&c, s->caller - s->slide);
    for (u32 i = 0; i < s->frame_count && i < 6; ++i) {
        text(&c, "\nFRAME  ", 9); hex(&c, s->frames[i] - s->slide);
    }
    ++s->render_count;
    __asm__ volatile("dsb sy" ::: "memory");
}

static void capture_character(int ch) {
    struct pd_state *s = pd_context();
    u32 n = s->length;
    if (n + 1 < MESSAGE_CAP) {
        s->message[n] = (char)ch;
        s->length = n + 1;
        s->message[n + 1] = 0;
    }
}

__attribute__((noinline)) void pd_capture(u64 options, u64 caller, const char *fmt,
                                          const u64 *args, u64 fp, u64 stack_low) {
    struct pd_state *s = pd_context();
    if (s->busy) return;
    s->busy = 1;
    s->magic = PD_MAGIC;
    s->stage = 1;
    s->reserved = 0x4e363901U; /* N69 panic marker: entry reached, framebuffer may be unavailable. */
    s->slide = pd_slide();
    s->caller = caller;
    s->options = options;
    s->frame_count = 0;
    s->length = 0;
    if (fmt) for (u32 i = 0; i + 1 < MESSAGE_CAP && fmt[i]; ++i) capture_character((u8)fmt[i]);
    pd_render();
    s->stage = 2;
    s->reserved = 0x4e363902U; /* Panic formatter reached. */
    if (fmt && args) {
        u64 copy = *args;
        s->length = 0;
        s->message[0] = 0;
        pd_doprnt(fmt, &copy, capture_character, 0);
    }
    u64 stack_end = (stack_low & ~(u64)0x3fff) + 0x4000;
    for (u32 i = 0; i < 12 && !(fp & 15) && fp >= stack_low && fp <= stack_end - 16; ++i) {
        const u64 *frame = (const u64 *)fp;
        u64 next = frame[0], lr = frame[1];
        s->frames[s->frame_count++] = lr;
        if (next <= fp) break;
        fp = next;
    }
    s->stage = 3;
    s->reserved = 0x4e363903U; /* Stack capture reached. Read in RAM/UART diagnostics. */
    __asm__ volatile("dsb sy" ::: "memory");
    pd_render();
}
