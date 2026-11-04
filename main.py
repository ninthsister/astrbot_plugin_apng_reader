import asyncio
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, register
from .decoder import decode_apng, DecodeLimit


@register("astrbot_plugin_apng_reader", "wtx", "将 APNG 动画帧转换为模型可读取的静态图片", "1.0.0")
class APNGReader(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._decode_lock = asyncio.Lock()

    @filter.on_llm_request(priority=100)
    async def read_apng(self, event: AstrMessageEvent, req: ProviderRequest):
        if not self.config.get("enabled", True) or not req.image_urls:
            return
        originals = list(req.image_urls)
        replacements, notes = [], []
        converted = 0
        for number, source in enumerate(originals, 1):
            if converted >= self._number("max_apng_per_request", 3, 1, 10):
                replacements.append(source)
                continue
            try:
                # Use AstrBot's resolver: local files, HTTP(S), file:// and base64.
                path = await asyncio.wait_for(Image(file=source).convert_to_file_path(), timeout=30)
                async with self._decode_lock:
                    result = await asyncio.to_thread(
                        decode_apng, path,
                        max_frames=self._number("max_frames", 6, 1, 12),
                        mode=self.config.get("mode", "sample"),
                        max_file_mb=self._number("max_file_mb", 25, 1, 100),
                        max_decode_frames=self._number("max_decode_frames", 200, 1, 500),
                    )
                if not result or not result["frames"]:
                    replacements.append(source)
                    continue
                converted += 1
                replacements.extend(result["frames"])
                positions = ",".join(map(str, result["indices"]))
                notes.append(
                    f"原第{number}张图片是 APNG，共{result['total']}个动画帧。"
                    f"此处替换为动画帧编号[{positions}]的静态图，按顺序排列。"
                    + ("独立默认封面未传入。" if result["poster_skipped"] else "")
                    + ("这是抽帧结果，未提供的帧内容未知。" if result["sampled"] else "")
                )
                logger.info(f"[APNG Reader] 图片{number}: {result['total']}帧 -> {len(result['frames'])}张静态图")
            except DecodeLimit as exc:
                logger.warning(f"[APNG Reader] 图片{number}跳过：{exc}")
                replacements.append(source)
            except Exception as exc:
                # Do not log signed URLs or base64 payloads.
                logger.warning(f"[APNG Reader] 图片{number}解析失败，保留原图 ({type(exc).__name__})")
                replacements.append(source)
        if notes:
            req.image_urls = replacements
            if self.config.get("add_frame_note", True):
                req.prompt = (req.prompt or "") + "\n\n[图片解码信息]\n" + "\n".join(notes)

    def _number(self, key, default, minimum, maximum):
        try:
            return max(minimum, min(maximum, int(self.config.get(key, default))))
        except (ValueError, TypeError):
            return default
