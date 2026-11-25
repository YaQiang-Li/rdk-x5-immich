#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Patch immich-server's dist/repositories/media.repository.js so that
decodeImage() of a downscaled JPEG preview goes through x5thumb (JPU decode
+ 2D GPU resize) and falls back to the original sharp path otherwise.

usage: patch_media_repository.py <media.repository.js>   (edits in place)
"""
import sys

IMPORT_OLD = "import { spawn } from 'node:child_process';\n"
IMPORT_NEW = ("import { execFile, spawn } from 'node:child_process';\n"
              "import { promisify } from 'node:util';\n"
              "import zlib from 'node:zlib';\n")

DECODE_OLD = """    async decodeImage(input, options) {
        const decoded = await this.getImageDecodingPipeline(input, options).raw().toBuffer({ resolveWithObject: true });
"""
DECODE_NEW = """    async decodeImage(input, options) {
        const x5 = await this.x5DecodeImage(input, options);
        if (x5) {
            return x5;
        }
        const decoded = await this.getImageDecodingPipeline(input, options).raw().toBuffer({ resolveWithObject: true });
"""

HELPER = """    // RDK X5: JPU (JPEG) or VPU (HEIC) decode + 2D GPU resize for previews.
    // The small RGB result goes back through immich's own decoding pipeline
    // as a PNG carrying the source ICC profile, so colour management and
    // EXIF orientation stay exactly as in the sharp path. Returns null to
    // use the sharp path.
    async x5DecodeImage(input, options) {
        const helper = process.env.IMMICH_X5THUMB;
        if (!helper || typeof input !== 'string' || !options.size ||
            options.edits?.length || !/\\.(jpe?g|heic|heif)$/i.test(input)) {
            return null;
        }
        const meta = await sharp(input).metadata().catch(() => null);
        // nothing to scale down: the sharp path is cheap
        if (!meta || Math.min(meta.width, meta.height) <= options.size) {
            return null;
        }
        let out;
        try {
            ({ stdout: out } = await execFileAsync(helper, [input, String(options.size)],
                { encoding: 'buffer', maxBuffer: 1 << 27 }));
        }
        catch {
            return null;    // exit 3: not handled by the hardware path
        }
        const nl = out.indexOf(10);
        const [width, height, ow, oh, rot] = out.subarray(0, nl).toString().split(' ').map(Number);
        let img = sharp(out.subarray(nl + 1), { raw: { width, height, channels: 3 } });
        if (ow !== width || oh !== height) {
            img = img.resize(ow, oh, { fit: 'fill' });  // odd target size
        }
        let { data, info } = await img.raw().toBuffer({ resolveWithObject: true });
        if (rot) {
            // HEIF irot, applied by libheif in the sharp path
            ({ data, info } = await sharp(data, { raw: info }).rotate(rot).raw().toBuffer({ resolveWithObject: true }));
        }
        let png = await sharp(data, { raw: info }).png({ compressionLevel: 0 }).toBuffer();
        if (meta.icc) {
            png = x5PngWithIcc(png, meta.icc);
        }
        const decodeOptions = { ...options, orientation: options.orientation ?? meta.orientation };
        return await this.getImageDecodingPipeline(png, decodeOptions).raw().toBuffer({ resolveWithObject: true });
    }
"""

PNG_ICC = """// insert an iCCP chunk right after IHDR (8 byte signature + 25 byte IHDR)
const x5PngWithIcc = (png, icc) => {
    const data = Buffer.concat([Buffer.from('icc\\0\\0', 'latin1'), zlib.deflateSync(icc)]);
    const chunk = Buffer.alloc(12 + data.length);
    chunk.writeUInt32BE(data.length, 0);
    chunk.write('iCCP', 4, 'latin1');
    data.copy(chunk, 8);
    chunk.writeUInt32BE(zlib.crc32(chunk.subarray(4, 8 + data.length)), 8 + data.length);
    return Buffer.concat([png.subarray(0, 33), chunk, png.subarray(33)]);
};
"""

CONST_ANCHOR = "const pascalCase = "
CONST_NEW = "const execFileAsync = promisify(execFile);\n"


def main():
    path = sys.argv[1]
    s = open(path).read()
    if "x5DecodeImage" in s:
        sys.exit("already patched")
    for old in (IMPORT_OLD, DECODE_OLD, CONST_ANCHOR):
        if s.count(old) != 1:
            sys.exit("anchor not found once: %r" % old[:60])
    s = s.replace(IMPORT_OLD, IMPORT_NEW)
    s = s.replace(CONST_ANCHOR, CONST_NEW + PNG_ICC + CONST_ANCHOR)
    s = s.replace(DECODE_OLD, HELPER + DECODE_NEW)
    open(path, "w").write(s)


if __name__ == "__main__":
    main()
