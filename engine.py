"""Bounded, content-addressed static frames shared by the ingress/adapters/hooks."""
import asyncio
import base64
import hashlib
import io
import json
import math
import os
from pathlib import Path
from PIL import Image, ImageDraw
from .decoder import decode_apng


def number(config, key, default, lo, hi):
    try:
        return max(lo, min(hi, int(config.get(key, default))))
    except (ValueError, TypeError):
        return default


class FrameEngine:
    def __init__(self, config, root, resolver):
        self.config = config
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.resolver = resolver
        self.lock = asyncio.Lock()
        self._cache = {}

    def enabled(self):
        return self.config.get('enabled', True)

    def options(self, mode=None):
        return dict(max_frames=number(self.config, 'max_frames', 6, 1, 12),
                    mode=mode or self.config.get('mode', 'sample'),
                    max_file_mb=number(self.config, 'max_file_mb', 25, 1, 100),
                    max_decode_frames=number(self.config, 'max_decode_frames', 200, 1, 500))

    async def resolve(self, source, mode=None):
        if not self.enabled() or not isinstance(source, str) or not source:
            return None
        # Already exported files are static; do not ask a network resolver again.
        try:
            if Path(source).is_file() and Path(source).parent == self.root:
                return None
        except (OSError, ValueError):
            pass
        path = await asyncio.wait_for(self.resolver(source), timeout=30)
        async with self.lock:
            return await asyncio.to_thread(self._decode_path, path, mode)

    async def bytes(self, data, mode=None):
        if not self.enabled() or not data.startswith(b'\x89PNG\r\n\x1a\n'):
            return None
        async with self.lock:
            return await asyncio.to_thread(self._export, data, mode)

    def _decode_path(self, path, mode=None):
        p = Path(path)
        limit = number(self.config, 'max_file_mb', 25, 1, 100)*1024*1024
        if p.stat().st_size > limit:
            from .decoder import DecodeLimit
            raise DecodeLimit('图片文件超过大小上限')
        with p.open('rb') as f:
            if f.read(8) != b'\x89PNG\r\n\x1a\n':
                return None
            f.seek(0)
            data = f.read(limit+1)
        return self._export(data, mode)

    def _export(self, data, mode=None):
        key = hashlib.sha256(data+json.dumps(self.options(mode), sort_keys=True).encode()).hexdigest()
        cached = self._cache.get(key)
        if cached and all(Path(p).exists() for p in cached['paths']+[cached['sheet']]):
            return cached
        result = decode_apng(data, **self.options(mode))
        if not result:
            return None
        paths = []
        for i, url in enumerate(result['frames']):
            path = self.root / f'{key}_{result["indices"][i]:04d}.png'
            self._write(path, base64.b64decode(url.split(',', 1)[1]))
            paths.append(str(path))
        sheet = self.root / f'{key}_sheet.png'
        if len(paths) == 1:
            sheet = Path(paths[0])
        elif not sheet.exists():
            # Keep a single image slot for plugins which assume one component -> one image.
            # The labels make clear that this is a time sequence, not multiple subjects.
            count = len(paths)
            cols = min(3, math.ceil(math.sqrt(count)))
            rows = math.ceil(count/cols)
            cell = min(768, 2048//cols, (2048-28*rows)//rows)
            thumbs = []
            for path in paths:
                with Image.open(path) as img:
                    frame = img.convert('RGBA')
                frame.thumbnail((cell, cell), Image.Resampling.LANCZOS)
                thumbs.append(frame)
            cw = max(x.width for x in thumbs)
            ch = max(x.height for x in thumbs)
            canvas = Image.new('RGBA', (cols*cw, rows*(ch+28)), 'white')
            draw = ImageDraw.Draw(canvas)
            for i, frame in enumerate(thumbs):
                x, y = i%cols*cw, i//cols*(ch+28)
                draw.text((x+6,y+6), f'APNG frame {result["indices"][i]}/{result["total"]}', fill='black')
                canvas.alpha_composite(frame, (x+(cw-frame.width)//2, y+28))
            out = io.BytesIO()
            canvas.save(out, format='PNG')
            self._write(sheet, out.getvalue())
        result.pop('frames')
        exported = {**result, 'paths':paths, 'sheet':str(sheet)}
        if len(self._cache) >= 32:
            self._cache.pop(next(iter(self._cache)))
        self._cache[key] = exported
        return exported

    @staticmethod
    def _write(path, data):
        if path.exists():
            return
        # Serial engine lock + atomic replacement prevents partial cached frames.
        temp = path.with_suffix('.tmp')
        temp.write_bytes(data)
        os.replace(temp, path)


def frame_note(result):
    return ('APNG 已解码：共 '+str(result['total'])+' 个动画帧；静态帧编号 '+
            ','.join(map(str, result['indices']))+'。'+
            ('独立默认封面已跳过。' if result['poster_skipped'] else '')+
            ('这是抽帧结果，未提供帧内容未知。' if result['sampled'] else '')+
            '多格图是同一动画的时间序列。')
