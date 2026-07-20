/* stub_libgf.c -- LINK-TIME stub for libgf.so.1 (QNX Graphics Framework).
 * Same pattern as stub_libc.c: provides UND symbols + soname so the main
 * program gets a NEEDED libgf.so.1 + PLT slots; the REAL libgf on the car
 * (used by gdcServerCarmine) resolves them at load. Bodies never ship.
 */
int gf_dev_attach(void *dev, int idx, void *info){return -1;}
int gf_dev_detach(void *dev){return -1;}
int gf_display_attach(void *disp, void *dev, int idx, void *info){return -1;}
int gf_display_detach(void *disp){return -1;}
int gf_layer_attach(void *layer, void *disp, int idx, int flags){return -1;}
int gf_layer_detach(void *layer){return -1;}
int gf_layer_enable(void *layer){return -1;}
int gf_layer_update(void *layer, int flags){return -1;}
int gf_layer_set_surfaces(void *layer, void *surfs, int n, int flags){return -1;}
int gf_layer_set_src_viewport(void *layer, int x1, int y1, int x2, int y2){return -1;}
int gf_layer_set_dst_viewport(void *layer, int x1, int y1, int x2, int y2){return -1;}
int gf_surface_attach_by_sid(void *surf, void *dev, unsigned sid){return -1;}
int gf_surface_get_info(void *surf, void *info){return -1;}
int gf_surface_create(void *surf, void *dev, int w, int h, int fmt, void *pal, int flags){return -1;}
int gf_surface_create_layer(void *surf, void *layer, int nlayer, int flags, int w, int h, int fmt, void *pal, int cflags){return -1;}
int gf_surface_free(void *surf){return -1;}
int gf_layer_query(void *layer, int fmtidx, void *info){return -1;}
int gf_layer_choose_format(void *layer, void *fmts, unsigned nfmt, void *crit, void *chosen){return -1;}
int gf_layer_set_blending(void *layer, void *alpha){return -1;}
int gf_display_snapshot(void *d, int o, int x1, int y1, int x2, int y2, void *s){return -1;}
int gf_display_set_layer_order(void *disp, const void *order, int flags){return -1;}
int gf_layer_disable(void *layer){return -1;}
int gf_context_create(void *ctx){return -1;}
int gf_context_free(void *ctx){return -1;}
int gf_context_set_surface(void *ctx, void *surf){return -1;}
int gf_context_set_fgcolor(void *ctx, unsigned color){return -1;}
int gf_draw_begin(void *ctx){return -1;}
int gf_draw_rect(void *ctx, int x1, int y1, int x2, int y2){return -1;}
int gf_draw_end(void *ctx){return -1;}
int gf_draw_finish(void *ctx){return -1;}
int gf_draw_flush(void *ctx){return -1;}
