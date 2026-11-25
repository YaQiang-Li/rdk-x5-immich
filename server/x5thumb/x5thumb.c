/* SPDX-License-Identifier: MIT */
/*
 * x5thumb: JPEG/HEIC -> resized RGB on X5 hardware, for immich previews.
 *
 *   JPEG: the JPU decodes the full picture to NV12. HEIC: the tiles of the
 *   primary image grid are decoded one after another by a single HEVC
 *   decoder of libavcodec (the RDK X5 build, i.e. the VPU) and stitched.
 *   The 2D GPU then resizes (NV12 -> NV12, bilinear filter) and the CPU
 *   converts the small picture to RGB.
 *
 *   The 2D GPU goes through RGB internally and always reads YUV as limited
 *   range (measured on a gray ramp; N2D_CSC_SET_FULL_RANGE only changes
 *   the output side). JPEG is full range, so the copy into the GPU buffer
 *   compresses to limited range, the GPU runs BT.601 limited both ways
 *   (identity), and the final conversion expands again.
 *
 * usage: x5thumb <file.jpg|file.heic> <size>
 *   Resizes like sharp resize(size, size, { fit: 'outside',
 *   withoutEnlargement: true }) and writes "<w> <h> <ow> <oh> <rot>\n"
 *   followed by w*h*3 bytes of RGB to stdout: w x h is the picture (even
 *   sizes, NV12), ow x oh the exact sharp target size, which differs by
 *   one pixel when that is odd, rot the clockwise rotation (HEIF irot)
 *   still to apply. EXIF orientation is not applied.
 * exit: 0 ok, 3 not handled here (not a baseline/extended 8-bit JPEG, not
 *   an 8-bit 4:2:0 HEIC tile grid, odd width/height, or no downscale
 *   needed), other = error
 *   (the caller falls back to the software path on any non-zero exit).
 */
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <strings.h>
#include <sys/file.h>
#include <unistd.h>

#include <hb_media_codec.h>
#include <hb_media_error.h>
#include <GC820/nano2D.h>
#include <GC820/nano2D_util.h>
#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>

#define TIMEOUT_MS	3000
#define N2D_LOCK	"/tmp/x5thumb.n2d.lock"

static unsigned char *read_file(const char *path, size_t *len)
{
	FILE *f = fopen(path, "rb");
	unsigned char *buf = NULL;

	if (!f)
		return NULL;
	fseek(f, 0, SEEK_END);
	*len = ftell(f);
	fseek(f, 0, SEEK_SET);
	buf = malloc(*len);
	if (buf && fread(buf, 1, *len, f) != *len) {
		free(buf);
		buf = NULL;
	}
	fclose(f);
	return buf;
}

/* 0 if the first SOF is baseline/extended sequential with 8-bit samples */
static int jpu_supported(const unsigned char *d, size_t len)
{
	size_t i = 2;

	while (i + 9 < len) {
		unsigned m;

		if (d[i] != 0xff) {
			i++;
			continue;
		}
		m = d[i + 1];
		if (m == 0xff || m == 0x01 || (m >= 0xd0 && m <= 0xd8)) {
			i += m == 0xff ? 1 : 2;
			continue;
		}
		if (m == 0xc0 || m == 0xc1)
			return d[i + 4] == 8 ? 0 : -1;
		if (m >= 0xc2 && m <= 0xcf && m != 0xc4 && m != 0xc8 &&
		    m != 0xcc)
			return -1;
		i += 2 + (d[i + 2] << 8 | d[i + 3]);
	}
	return -1;
}

/* round half up like sharp/libvips */
static int rnd(double v)
{
	return (int)(v + 0.5);
}

static inline unsigned char clip(int v)
{
	return v < 0 ? 0 : v > 255 ? 255 : v;
}

/* full size NV12 picture to be scaled */
struct canvas {
	unsigned char *y, *uv;	/* uv = y + w * h, stride w */
	int rot;		/* clockwise rotation still to apply */
	int w, h;
	int full;		/* full range (JPEG) or limited */
	int bt709;		/* matrix: BT.709, else BT.601 */
};

/* limited range NV12 -> RGB24, BT.601 or BT.709 */
static void nv12_to_rgb(const unsigned char *y, int ys, const unsigned char *uv,
			int uvs, int w, int h, int bt709, unsigned char *rgb)
{
	int i, j;
	/* x1024: Y gain, R/V, G/U, G/V, B/U */
	const int k[2][5] = { { 1192, 1634, 401, 833, 2066 },
			      { 1192, 1836, 218, 546, 2163 } };
	const int *c = k[bt709 != 0];

	for (j = 0; j < h; j++) {
		const unsigned char *yl = y + j * ys;
		const unsigned char *cl = uv + (j / 2) * uvs;
		unsigned char *o = rgb + (size_t)j * w * 3;

		for (i = 0; i < w; i += 2) {
			int u = cl[i] - 128, v = cl[i + 1] - 128;
			int r = c[1] * v + 512, g = -c[2] * u - c[3] * v + 512;
			int b = c[4] * u + 512;
			int y0 = (yl[i] - 16) * c[0], y1 = (yl[i + 1] - 16) * c[0];

			o[0] = clip((y0 + r) >> 10);
			o[1] = clip((y0 + g) >> 10);
			o[2] = clip((y0 + b) >> 10);
			o[3] = clip((y1 + r) >> 10);
			o[4] = clip((y1 + g) >> 10);
			o[5] = clip((y1 + b) >> 10);
			o += 6;
		}
	}
}

/* full -> limited range copy of one plane */
static void copy_limited(unsigned char *d, int ds, const unsigned char *s,
			 int ss, int w, int h, const unsigned char *lut)
{
	int i, j;

	for (j = 0; j < h; j++) {
		const unsigned char *sl = s + (size_t)j * ss;
		unsigned char *dl = d + (size_t)j * ds;

		for (i = 0; i < w; i++)
			dl[i] = lut[sl[i]];
	}
}

/* JPU decode into a malloc'ed canvas; 3 = not handled */
static int decode_jpeg(const char *path, struct canvas *cv)
{
	media_codec_context_t ctx;
	mc_video_codec_dec_params_t *p = &ctx.video_dec_params;
	mc_av_codec_startup_params_t startup = { 0 };
	media_codec_buffer_t in = { 0 }, ob = { 0 };
	media_codec_output_buffer_info_t info = { 0 };
	unsigned char *jpg;
	size_t len;
	int y, ret = 1, mc = 0, stride;

	jpg = read_file(path, &len);
	if (!jpg)
		return 2;
	if (jpu_supported(jpg, len)) {
		free(jpg);
		return 3;
	}
	memset(&ctx, 0, sizeof(ctx));
	ctx.codec_id = MEDIA_CODEC_ID_JPEG;
	p->feed_mode = MC_FEEDING_MODE_FRAME_SIZE;
	p->pix_fmt = MC_PIXEL_FORMAT_NV12;
	p->bitstream_buf_size = (len + 0xfff) & ~0xfffUL;
	p->bitstream_buf_count = 1;
	p->frame_buf_count = 1;
	if (hb_mm_mc_initialize(&ctx) || hb_mm_mc_configure(&ctx) ||
	    hb_mm_mc_start(&ctx, &startup))
		goto out;
	mc = 1;
	in.type = MC_VIDEO_STREAM_BUFFER;
	if (hb_mm_mc_dequeue_input_buffer(&ctx, &in, TIMEOUT_MS))
		goto out;
	memcpy(in.vstream_buf.vir_ptr, jpg, len);
	in.vstream_buf.size = len;
	if (hb_mm_mc_queue_input_buffer(&ctx, &in, TIMEOUT_MS) ||
	    hb_mm_mc_dequeue_output_buffer(&ctx, &ob, &info, TIMEOUT_MS))
		goto out;
	mc = 2;
	cv->w = info.jpeg_frame_info.display_width;
	cv->h = info.jpeg_frame_info.display_height;
	if ((cv->w | cv->h) & 1) {
		ret = 3;
		goto out;
	}
	cv->full = 1;
	cv->bt709 = 0;
	stride = ob.vframe_buf.stride;
	cv->y = malloc((size_t)cv->w * cv->h * 3 / 2);
	cv->uv = cv->y + (size_t)cv->w * cv->h;
	for (y = 0; y < cv->h; y++)
		memcpy(cv->y + (size_t)y * cv->w,
		       ob.vframe_buf.vir_ptr[0] + (size_t)y * stride, cv->w);
	for (y = 0; y < cv->h / 2; y++)
		memcpy(cv->uv + (size_t)y * cv->w,
		       ob.vframe_buf.vir_ptr[1] + (size_t)y * stride, cv->w);
	ret = 0;
out:
	if (mc == 2)
		hb_mm_mc_queue_output_buffer(&ctx, &ob, TIMEOUT_MS);
	if (mc) {
		hb_mm_mc_stop(&ctx);
		hb_mm_mc_release(&ctx);
	}
	free(jpg);
	return ret;
}

static uint32_t be32(const unsigned char *p)
{
	return (uint32_t)p[0] << 24 | p[1] << 16 | p[2] << 8 | p[3];
}

/* find child box `type` in [p, end); returns payload start, sets *len */
static const unsigned char *find_box(const unsigned char *p,
				     const unsigned char *end, const char *type,
				     size_t *len)
{
	while (p + 8 <= end) {
		size_t sz = be32(p);

		if (sz < 8 || p + sz > end)
			return NULL;
		if (!memcmp(p + 4, type, 4)) {
			*len = sz - 8;
			return p + 8;
		}
		p += sz;
	}
	return NULL;
}

/*
 * Clockwise rotation (0/90/180/270) of the HEIF primary item from its
 * 'irot' property, -1 if mirrored ('imir') or unparsable. libheif (and so
 * sharp/immich) applies these transforms; the tiles come out unrotated.
 */
static int heif_rotation(const char *path)
{
	unsigned char *d;
	const unsigned char *meta, *pitm, *iprp, *ipco, *ipma, *q, *e;
	size_t len, ml, l, il;
	uint32_t primary, i, n;
	int rot = 0, ret = -1, idx[32], nidx = 0, k;

	d = read_file(path, &len);
	if (!d)
		return -1;
	meta = find_box(d, d + len, "meta", &ml);
	if (!meta)
		goto out;
	meta += 4;	/* FullBox */
	ml -= 4;
	pitm = find_box(meta, meta + ml, "pitm", &l);
	iprp = find_box(meta, meta + ml, "iprp", &il);
	if (!pitm || !iprp)
		goto out;
	primary = pitm[0] ? be32(pitm + 4) : (uint32_t)(pitm[4] << 8 | pitm[5]);
	ipco = find_box(iprp, iprp + il, "ipco", &l);
	ipma = find_box(iprp, iprp + il, "ipma", &il);
	if (!ipco || !ipma)
		goto out;
	/* ipma: version/flags, entry_count, entries */
	q = ipma + 8;
	e = ipma + il;
	n = be32(ipma + 4);
	for (i = 0; i < n && q < e; i++) {
		uint32_t item = ipma[0] < 1 ? (uint32_t)(q[0] << 8 | q[1]) :
			be32(q);
		int cnt, big = ipma[3] & 1;

		q += ipma[0] < 1 ? 2 : 4;
		cnt = *q++;
		for (k = 0; k < cnt; k++) {
			int v = big ? (q[0] << 8 | q[1]) & 0x7fff : q[0] & 0x7f;

			q += big ? 2 : 1;
			if (item == primary && nidx < 32)
				idx[nidx++] = v;
		}
	}
	for (k = 0; k < nidx; k++) {
		/* property index is 1-based in ipco */
		const unsigned char *b = ipco;
		int j;

		for (j = 1; j < idx[k] && b + 8 <= ipco + l; j++)
			b += be32(b);
		if (b + 9 > ipco + l)
			goto out;
		if (!memcmp(b + 4, "imir", 4))
			goto out;
		if (!memcmp(b + 4, "irot", 4))
			rot = (4 - (b[8] & 3)) % 4 * 90;	/* ccw -> cw */
	}
	ret = rot;
out:
	free(d);
	return ret;
}

/* copy a decoded yuv420p tile into the NV12 canvas at (x0, y0), clipped */
static void paste_tile(struct canvas *cv, const AVFrame *f, int x0, int y0)
{
	int x, y, w = f->width, h = f->height;

	if (x0 + w > cv->w)
		w = cv->w - x0;
	if (y0 + h > cv->h)
		h = cv->h - y0;
	if (w <= 0 || h <= 0)
		return;
	for (y = 0; y < h; y++)
		memcpy(cv->y + (size_t)(y0 + y) * cv->w + x0,
		       f->data[0] + (size_t)y * f->linesize[0], w);
	for (y = 0; y < h / 2; y++) {
		unsigned char *d = cv->uv + (size_t)(y0 / 2 + y) * cv->w + x0;
		const unsigned char *u = f->data[1] + (size_t)y * f->linesize[1];
		const unsigned char *v = f->data[2] + (size_t)y * f->linesize[2];

		for (x = 0; x < w / 2; x++) {
			d[2 * x] = u[x];
			d[2 * x + 1] = v[x];
		}
	}
}

/*
 * Range and matrix come from the HEVC VUI. The X5 decoder does not export
 * them, so decode one tile with the native decoder to read them (~10 ms).
 */
static int probe_color(const AVCodecParameters *par, const AVPacket *pkt,
		       struct canvas *cv)
{
	const AVCodec *c = avcodec_find_decoder_by_name("hevc");
	AVCodecContext *d = c ? avcodec_alloc_context3(c) : NULL;
	AVFrame *f = av_frame_alloc();
	int ret = -1;

	if (!d || !f || !pkt || avcodec_parameters_to_context(d, par) < 0)
		goto out;
	d->thread_count = 1;
	if (avcodec_open2(d, c, NULL) < 0 || avcodec_send_packet(d, pkt) < 0 ||
	    avcodec_send_packet(d, NULL) < 0 || avcodec_receive_frame(d, f) < 0)
		goto out;
	cv->full = f->color_range == AVCOL_RANGE_JPEG ||
		   f->format == AV_PIX_FMT_YUVJ420P;
	cv->bt709 = f->colorspace == AVCOL_SPC_BT709;
	ret = 0;
out:
	av_frame_free(&f);
	avcodec_free_context(&d);
	return ret;
}

/*
 * Decode the largest tile grid of a HEIF file with ONE decoder: tiles are
 * fed in order (pts = tile number) and pasted as they come out. Opening a
 * decoder per tile (what the ffmpeg CLI does, ~100 for an iPhone HEIC)
 * exhausts the VPU instances and memory.
 */
static int decode_heic(const char *path, struct canvas *cv)
{
	AVFormatContext *fmt = NULL;
	AVCodecContext *dec = NULL;
	const AVStreamGroupTileGrid *tg = NULL;
	const AVStreamGroup *grp = NULL;
	AVPacket **pkt = NULL, *p;
	AVFrame *f = NULL;
	unsigned g, i, sent = 0, got = 0;
	int ret = 3, x0, y0;
	unsigned char *full = NULL;

	av_log_set_level(AV_LOG_QUIET);
	if (avformat_open_input(&fmt, path, NULL, NULL) < 0)
		return 3;
	for (g = 0; g < fmt->nb_stream_groups; g++) {
		const AVStreamGroup *sg = fmt->stream_groups[g];

		if (sg->type != AV_STREAM_GROUP_PARAMS_TILE_GRID)
			continue;
		if (!tg || (int64_t)sg->params.tile_grid->width *
		    sg->params.tile_grid->height > (int64_t)tg->width * tg->height) {
			tg = sg->params.tile_grid;
			grp = sg;
		}
	}
	if (!tg || !grp->nb_streams)
		goto out;
	{
		const AVCodecParameters *par = grp->streams[0]->codecpar;
		const AVCodec *c = avcodec_find_decoder(par->codec_id);

		/* no avformat_find_stream_info(): format may be unset here,
		 * the decoded frames are checked instead */
		if (par->codec_id != AV_CODEC_ID_HEVC || !c)
			goto out;
		dec = avcodec_alloc_context3(c);
		if (!dec || avcodec_parameters_to_context(dec, par) < 0 ||
		    avcodec_open2(dec, c, NULL) < 0)
			goto out;
	}
	/* one packet per tile stream */
	pkt = av_calloc(fmt->nb_streams, sizeof(*pkt));
	p = av_packet_alloc();
	while (av_read_frame(fmt, p) >= 0) {
		if (!pkt[p->stream_index])
			pkt[p->stream_index] = av_packet_clone(p);
		av_packet_unref(p);
	}
	av_packet_free(&p);

	if (probe_color(grp->streams[0]->codecpar,
			pkt[grp->streams[tg->offsets[0].idx]->index], cv) < 0)
		goto out;
	cv->w = tg->width & ~1;
	cv->h = tg->height & ~1;
	if (cv->w != tg->width || cv->h != tg->height)
		goto out;
	full = malloc((size_t)tg->coded_width * tg->coded_height * 3 / 2);
	cv->y = full;
	f = av_frame_alloc();
	/* decode on a coded_width x coded_height canvas, crop afterwards */
	{
		struct canvas cc = {
			.y = full,
			.uv = full + (size_t)tg->coded_width * tg->coded_height,
			.w = tg->coded_width,
			.h = tg->coded_height,
		};

		for (i = 0; i <= tg->nb_tiles; i++) {
			int r;

			if (i < tg->nb_tiles) {
				const AVStream *st =
					grp->streams[tg->offsets[i].idx];

				p = pkt[st->index];
				if (!p)
					goto out;
				p->pts = i;
				r = avcodec_send_packet(dec, p);
				sent++;
			} else {
				r = avcodec_send_packet(dec, NULL);
			}
			if (r < 0 && r != AVERROR(EAGAIN))
				goto out;
			while (avcodec_receive_frame(dec, f) >= 0) {
				unsigned t = (unsigned)f->pts;

				if (t >= tg->nb_tiles ||
				    (f->format != AV_PIX_FMT_YUV420P &&
				     f->format != AV_PIX_FMT_YUVJ420P))
					goto out;
				paste_tile(&cc, f, tg->offsets[t].horizontal,
					   tg->offsets[t].vertical);
				got++;
				av_frame_unref(f);
			}
		}
		if (got != tg->nb_tiles)
			goto out;
		/* crop into the front of the same buffer, then the chroma */
		x0 = tg->horizontal_offset & ~1;
		y0 = tg->vertical_offset & ~1;
		cv->y = malloc((size_t)cv->w * cv->h * 3 / 2);
		cv->uv = cv->y + (size_t)cv->w * cv->h;
		for (i = 0; i < (unsigned)cv->h; i++)
			memcpy(cv->y + (size_t)i * cv->w,
			       cc.y + (size_t)(y0 + i) * cc.w + x0, cv->w);
		for (i = 0; i < (unsigned)cv->h / 2; i++)
			memcpy(cv->uv + (size_t)i * cv->w,
			       cc.uv + (size_t)(y0 / 2 + i) * cc.w + x0, cv->w);
	}
	ret = 0;
out:
	(void)sent;
	free(full);
	if (ret)
		cv->y = NULL;
	if (pkt) {
		for (i = 0; i < fmt->nb_streams; i++)
			av_packet_free(&pkt[i]);
		av_free(pkt);
	}
	av_frame_free(&f);
	avcodec_free_context(&dec);
	avformat_close_input(&fmt);
	return ret;
}

int main(int argc, char **argv)
{
	struct canvas cv = { 0 };
	n2d_buffer_t src = { 0 }, dst = { 0 };
	n2d_rectangle_t sr, dr;
	unsigned char *rgb, *tmp, ylut[256], clut[256], *cl;
	const char *ext;
	int size, w, h, ow, oh, dw, dh, y, lock = -1, ret;
	double s;

	if (argc != 3)
		return 2;
	size = atoi(argv[2]);
	ext = strrchr(argv[1], '.');
	if (ext && (!strcasecmp(ext, ".heic") || !strcasecmp(ext, ".heif"))) {
		cv.rot = heif_rotation(argv[1]);
		if (cv.rot < 0)
			return 3;
		ret = decode_heic(argv[1], &cv);
	} else
		ret = decode_jpeg(argv[1], &cv);
	if (ret)
		return ret;
	w = cv.w;
	h = cv.h;
	ret = 3;
	s = (double)size / (w < h ? w : h);
	if (s >= 1)
		goto out;
	ret = 1;
	/* NV12 needs even sizes: the caller stretches to ow x oh */
	ow = w < h ? size : rnd(w * s);
	oh = w < h ? rnd(h * s) : size;
	dw = (ow + 1) & ~1;
	dh = (oh + 1) & ~1;
	for (y = 0; y < 256; y++) {
		ylut[y] = cv.full ? (y * 219 + 127) / 255 + 16 : y;
		clut[y] = cv.full ? ((y - 128) * 224 + (y < 128 ? -127 : 127)) /
			  255 + 128 : y;
	}
	cl = clut;

	/* the 2D GPU driver has a single context: serialise processes */
	lock = open(N2D_LOCK, O_CREAT | O_RDWR, 0666);
	if (lock < 0 || flock(lock, LOCK_EX))
		goto out;
	if (N2D_IS_ERROR(n2d_open()))
		goto out;
	n2d_switch_device(N2D_DEVICE_0);
	n2d_switch_core(N2D_CORE_0);
	if (N2D_IS_ERROR(n2d_util_allocate_buffer(w, h, N2D_NV12, N2D_0,
			N2D_LINEAR, N2D_TSC_DISABLE, &src)) ||
	    N2D_IS_ERROR(n2d_util_allocate_buffer(dw, dh, N2D_NV12, N2D_0,
			N2D_LINEAR, N2D_TSC_DISABLE, &dst)))
		goto n2d_out;
	copy_limited(src.memory, src.stride, cv.y, w, w, h, ylut);
	copy_limited(src.uv_memory[0], src.uvstride[0], cv.uv, w, w, h / 2, cl);
	{
		n2d_state_config_t c = { .state = N2D_SET_FILTER_TYPE,
			.config.filterType = N2D_FILTER_BILINEAR };

		n2d_set(&c);
		c = (n2d_state_config_t){ .state = N2D_SET_KERNEL_SIZE,
			.config.kernelSize = { 5, 5 } };
		n2d_set(&c);
		memset(&c, 0, sizeof(c));
		c.state = N2D_SET_CSC;
		c.config.csc.cscMode = cv.bt709 ? N2D_CSC_BT709 : N2D_CSC_BT601;
		c.config.csc.userCSCMode = N2D_CSC_YUV_TO_RGB;
		n2d_set(&c);
		c.config.csc.userCSCMode = N2D_CSC_RGB_TO_YUV;
		n2d_set(&c);
	}
	sr = (n2d_rectangle_t){ 0, 0, w, h };
	dr = (n2d_rectangle_t){ 0, 0, dw, dh };
	if (N2D_IS_ERROR(n2d_filterblit(&dst, &dr, N2D_NULL, &src, &sr,
					N2D_BLEND_NONE)) ||
	    N2D_IS_ERROR(n2d_commit()))
		goto n2d_out;
	/*
	 * n2d buffers are not cached for the CPU: one bulk copy is ~30x
	 * faster than converting straight out of them.
	 */
	tmp = malloc((size_t)dst.stride * dh + (size_t)dst.uvstride[0] * dh / 2);
	memcpy(tmp, dst.memory, (size_t)dst.stride * dh);
	memcpy(tmp + (size_t)dst.stride * dh, dst.uv_memory[0],
	       (size_t)dst.uvstride[0] * dh / 2);
	rgb = malloc((size_t)dw * dh * 3);
	nv12_to_rgb(tmp, dst.stride, tmp + (size_t)dst.stride * dh,
		    dst.uvstride[0], dw, dh, cv.bt709, rgb);
	free(tmp);
	n2d_free(&dst);
	n2d_free(&src);
	n2d_close();
	flock(lock, LOCK_UN);
	printf("%d %d %d %d %d\n", dw, dh, ow, oh, cv.rot);
	fwrite(rgb, 1, (size_t)dw * dh * 3, stdout);
	free(rgb);
	ret = 0;
	goto out;
n2d_out:
	if (dst.memory)
		n2d_free(&dst);
	if (src.memory)
		n2d_free(&src);
	n2d_close();
out:
	if (lock >= 0)
		close(lock);
	free(cv.y);
	return ret;
}
