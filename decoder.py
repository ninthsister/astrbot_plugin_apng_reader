"""APNG decoding independent of AstrBot; output contains ordinary PNG data URLs."""
import base64
import hashlib
import io
from pathlib import Path
from PIL import Image, UnidentifiedImageError


class DecodeLimit(ValueError):
    pass


def decode_apng(path, max_frames=6, mode="sample", max_file_mb=25,
                max_decode_frames=200, max_pixels=20_000_000,
                max_total_pixels=300_000_000, max_side=1536):
    raw = path if isinstance(path, bytes) else None
    path = io.BytesIO(raw) if raw is not None else Path(path)
    size = len(raw) if raw is not None else path.stat().st_size
    if size > max_file_mb * 1024 * 1024:
        raise DecodeLimit("图片文件超过大小上限")
    if raw is not None:
        if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
            return None
    else:
        with path.open("rb") as stream:
            if stream.read(8) != b"\x89PNG\r\n\x1a\n":
                return None
    try:
        image = Image.open(path)
    except UnidentifiedImageError:
        return None
    with image:
        # PNG with acTL, including a single animation frame with a separate poster.
        if image.format != "PNG" or image.get_format_mimetype() != "image/apng":
            return None
        start = 1 if image.info.get("default_image", False) else 0
        count = image.n_frames - start
        pixels = image.width * image.height
        if count < 1:
            return None
        if image.n_frames > max_decode_frames or pixels > max_pixels or pixels * image.n_frames > max_total_pixels:
            raise DecodeLimit("APNG 帧数或解码像素量超过上限")
        max_frames = max(1, min(int(max_frames), 12))
        if mode == "last":
            targets = {image.n_frames - 1}
        elif count <= max_frames:
            targets = set(range(start, image.n_frames))
        elif max_frames == 1:
            targets = {image.n_frames - 1}
        else:
            targets = {start + round(i * (count - 1) / (max_frames - 1)) for i in range(max_frames)}
        frames, indices, seen = [], [], set()
        # Sequential seek is necessary for APNG dispose/blend composition.
        for index in range(image.n_frames):
            image.seek(index)
            image.load()
            if index not in targets:
                continue
            rgba = image.convert("RGBA")
            digest = hashlib.sha256(rgba.tobytes()).digest()
            if digest in seen:
                continue
            seen.add(digest)
            rgba.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            # Preserve alpha; the PNG remains a fully composited canvas.
            buf = io.BytesIO()
            rgba.save(buf, format="PNG")
            frames.append("data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii"))
            indices.append(index - start + 1)
        return {"frames": frames, "indices": indices, "total": count,
                "poster_skipped": bool(start), "sampled": len(targets) < count}
