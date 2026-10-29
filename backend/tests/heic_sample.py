"""Write a tiny HEIC (HEIF + HEVC) still image using only ffmpeg and the standard library.

ffmpeg has no HEIF muxer, so the container is assembled by hand around libx265 intra frames:
the ``hvcC`` parameter-set box and the frames' NAL units are lifted from one-frame MP4s and wrapped in
the minimal HEIF boxes (``ftyp``, ``meta`` with ``hdlr``/``pitm``/``iloc``/``iinf``/``iprp``, ``mdat``).
With ``grid=(cols, rows)`` the picture is stored like a phone does: a ``grid`` item composed of
``cols * rows`` equally sized solid-colour tiles (red, blue, green, yellow, repeating), linked by ``iref``/``dimg``.
Used by the ``make_picture`` fixture and by ``scripts/docker_smoke.sh`` (mounted into the image).
"""

import struct
import subprocess
import tempfile
from pathlib import Path


def _box(kind: bytes, payload: bytes = b"") -> bytes:
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def _full(kind: bytes, payload: bytes = b"", *, version: int = 0) -> bytes:
    return _box(kind, bytes([version, 0, 0, 0]) + payload)


def _lift(data: bytes, kind: bytes) -> bytes:
    """The payload (without the 8-byte header) of the first ``kind`` box found by scanning ``data``."""
    at = data.index(kind) - 4
    size = struct.unpack(">I", data[at : at + 4])[0]
    return data[at + 8 : at + size]


_TILE_COLORS = ("red", "blue", "green", "yellow")
_GRID_DATA_SIZE = 8


def _tile(width: int, height: int, color: str | None) -> tuple[bytes, bytes]:
    """``(hvcC payload, frame NAL units)`` of one intra frame: a solid ``color``, or the test pattern."""
    with tempfile.TemporaryDirectory() as tmp:
        mp4 = Path(tmp) / "frame.mp4"
        src = f"color=c={color}:s={width}x{height}:r=1:d=1" if color else f"testsrc=s={width}x{height}:r=1:d=1"
        cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", src]
        cmd += ["-frames:v", "1", "-c:v", "libx265", "-pix_fmt", "yuvj420p", "-tag:v", "hvc1"]
        cmd += ["-x265-params", "log-level=none", str(mp4)]
        subprocess.run(cmd, check=True, capture_output=True)
        data = mp4.read_bytes()
    return _lift(data, b"hvcC"), _lift(data, b"mdat")


def _meta(sizes: list[int], dims: tuple[int, int], tile_dims: tuple[int, int], hvcc: bytes, base: int) -> bytes:
    """The ``meta`` box for tiles of ``sizes`` bytes (items 1..n) and, for more than one, a grid item n + 1."""
    tiles, grid = len(sizes), len(sizes) > 1
    ids = list(range(1, tiles + 1)) + ([tiles + 1] if grid else [])
    offsets = [base + sum(sizes[:i]) for i in range(tiles)] + ([base + sum(sizes)] if grid else [])
    lengths = [*sizes, _GRID_DATA_SIZE] if grid else sizes
    hdlr = _full(b"hdlr", b"\0\0\0\0pict" + bytes(12) + b"\0")
    pitm = _full(b"pitm", struct.pack(">H", ids[-1]))
    entries = b"".join(struct.pack(">HHHII", i, 0, 1, o, n) for i, o, n in zip(ids, offsets, lengths, strict=True))
    iloc = _full(b"iloc", bytes([0x44, 0x00]) + struct.pack(">H", len(ids)) + entries)
    infes = [_full(b"infe", struct.pack(">HH", i, 0) + b"hvc1\0", version=2) for i in ids[:tiles]]
    if grid:
        infes.append(_full(b"infe", struct.pack(">HH", ids[-1], 0) + b"grid\0", version=2))
    iinf = _full(b"iinf", struct.pack(">H", len(ids)) + b"".join(infes))
    props = [_box(b"hvcC", hvcc), _full(b"ispe", struct.pack(">II", *tile_dims))]
    assoc = [struct.pack(">HB", i, 2) + bytes([0x81, 0x02]) for i in ids[:tiles]]  # hvcC (essential), tile ispe
    if grid:
        props.append(_full(b"ispe", struct.pack(">II", *dims)))
        assoc.append(struct.pack(">HB", ids[-1], 1) + bytes([0x03]))
    ipma = _full(b"ipma", struct.pack(">I", len(assoc)) + b"".join(assoc))
    iprp = _box(b"iprp", _box(b"ipco", b"".join(props)) + ipma)
    iref = b""
    if grid:
        dimg = struct.pack(">HH", ids[-1], tiles) + b"".join(struct.pack(">H", i) for i in ids[:tiles])
        iref = _full(b"iref", _box(b"dimg", dimg))
    return _full(b"meta", hdlr + pitm + iloc + iinf + iref + iprp)


def write_heic(path: Path, width: int = 64, height: int = 48, *, grid: tuple[int, int] = (1, 1)) -> Path:
    """Write a ``width`` x ``height`` HEIC test picture to ``path``.

    ``grid=(cols, rows)`` stores it as a tile grid (``width`` / ``cols`` x ``height`` / ``rows`` tiles, each
    even-sized, in distinct colours); the default is a single test-pattern image.
    """
    cols, rows = grid
    tile_dims = (width // cols, height // rows)
    colors = [_TILE_COLORS[i % len(_TILE_COLORS)] if cols * rows > 1 else None for i in range(cols * rows)]
    frames = [_tile(*tile_dims, color) for color in colors]
    hvcc, nals = frames[0][0], [f[1] for f in frames]
    payload = b"".join(nals)
    if cols * rows > 1:  # version 0, flags 0, rows - 1, cols - 1, output width, output height
        payload += bytes([0, 0, rows - 1, cols - 1]) + struct.pack(">HH", width, height)
    ftyp = _box(b"ftyp", b"heic" + bytes(4) + b"mif1heic")
    sizes = [len(n) for n in nals]
    meta_size = len(_meta(sizes, (width, height), tile_dims, hvcc, 0))
    meta = _meta(sizes, (width, height), tile_dims, hvcc, len(ftyp) + meta_size + 8)
    path.write_bytes(ftyp + meta + _box(b"mdat", payload))
    return path
