"""Read Enfusion .edds textures (DayZ GUI atlases) into Pillow images.

An .edds is a DDS header followed by a table of one entry per mipmap, smallest first:
4-byte tag ("COPY" = stored, "LZ4 " = LZ4 block) and the stored size. The mip data follows in
the same order. We rebuild the largest mip as a plain DDS and let Pillow decode the BC format.

    python pipeline/edds.py <file.edds> <out.png>
    python pipeline/edds.py <atlas.edds> <atlas.imageset> <out folder>    # crop every icon
"""
import io, os, re, struct, sys

from PIL import Image


def lz4_block(src, out_size, out=None):
    """LZ4 block decompression (pure Python). Appends to `out` so later chunks can match into earlier ones."""
    out = bytearray() if out is None else out
    base = len(out)
    i, n = 0, len(src)
    while i < n:
        token = src[i]; i += 1
        lit = token >> 4
        if lit == 15:
            while True:
                b = src[i]; i += 1
                lit += b
                if b != 255:
                    break
        out += src[i:i + lit]; i += lit
        if i >= n:
            break
        off = src[i] | (src[i + 1] << 8); i += 2
        ml = token & 15
        if ml == 15:
            while True:
                b = src[i]; i += 1
                ml += b
                if b != 255:
                    break
        ml += 4
        start = len(out) - off
        if off >= ml:
            out += out[start:start + ml]
        else:
            for k in range(ml):
                out.append(out[start + k])
    if len(out) - base != out_size:
        raise ValueError(f"lz4: expected {out_size} bytes, got {len(out) - base}")
    return out


def read_edds(path):
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] != b"DDS ":
        raise ValueError("not a DDS")
    height, width = struct.unpack("<II", data[12:20])
    mips = struct.unpack("<I", data[28:32])[0] or 1
    fourcc = data[84:88]
    p = 128
    if fourcc == b"DX10":
        p += 20
    table = []
    for _ in range(mips):
        tag, size = data[p:p + 4], struct.unpack("<I", data[p + 4:p + 8])[0]
        table.append((tag, size)); p += 8
    # mip data, smallest first; we want the last (largest)
    blocks = []
    for tag, size in table:
        blocks.append((tag, data[p:p + size])); p += size
    tag, blob = blocks[-1]
    bpp = {b"DXT1": 8, b"DXT5": 16, b"DXT3": 16, b"ATI2": 16, b"BC5U": 16}.get(fourcc)
    if bpp is None:
        raise ValueError(f"unsupported format {fourcc}")
    full = (max(1, width // 4) * max(1, height // 4)) * bpp
    if tag == b"LZ4 ":
        # u32 uncompressed size, then chunks that each inflate to 64 KB (the last one less):
        # u32 compressed size + data. Later chunks may match into earlier output.
        usize = struct.unpack("<I", blob[:4])[0]
        out, p = bytearray(), 4
        while len(out) < usize and p + 4 <= len(blob):
            csize = struct.unpack("<I", blob[p:p + 4])[0] & 0x7FFFFFFF; p += 4
            lz4_block(blob[p:p + csize], min(65536, usize - len(out)), out); p += csize
        blob = bytes(out)
    elif tag != b"COPY":
        raise ValueError(f"unknown block tag {tag}")
    header = bytearray(data[:128])
    struct.pack_into("<I", header, 28, 1)                     # one mip
    struct.pack_into("<I", header, 20, full)                  # linear size
    return Image.open(io.BytesIO(bytes(header) + blob)).convert("RGBA")


def read_imageset(path):
    """{name: (x, y, w, h)} from an Enfusion .imageset."""
    icons = {}
    with open(path, encoding="utf-8", errors="replace") as f:
        txt = f.read()
    for m in re.finditer(r'ImageSetDefClass\s+\S+\s*\{\s*Name\s+"([^"]+)"\s*Pos\s+(\d+)\s+(\d+)\s*Size\s+(\d+)\s+(\d+)', txt):
        icons[m.group(1)] = tuple(int(m.group(i)) for i in range(2, 6))
    return icons


def main():
    if len(sys.argv) == 3:
        read_edds(sys.argv[1]).save(sys.argv[2])
        return
    atlas, imageset, out = sys.argv[1:4]
    img = read_edds(atlas)
    os.makedirs(out, exist_ok=True)
    icons = read_imageset(imageset)
    for name, (x, y, w, h) in icons.items():
        img.crop((x, y, x + w, y + h)).save(os.path.join(out, f"{name}.png"))
    print(f"{len(icons)} icons -> {out}")


if __name__ == "__main__":
    main()
