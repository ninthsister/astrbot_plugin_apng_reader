import asyncio
import copy
import json
from pathlib import Path
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, StarTools, register
from .engine import FrameEngine, frame_note, number
from .compat import AngelCompatibility


@register('astrbot_plugin_apng_reader', 'wtx', 'APNG 真图解析与 Angel 插件兼容', '1.1.0')
class APNGReader(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        root = Path(StarTools.get_data_dir('astrbot_plugin_apng_reader')) / 'frames'
        async def resolve(source):
            return await Image(file=source).convert_to_file_path()
        self.engine = FrameEngine(config, root, resolve)
        self.compat = AngelCompatibility(self.engine, logger)

    async def initialize(self):
        self.compat.install()
        logger.info('[APNG Reader] v1.1.0 已启用：入口优先级10000，请求收尾优先级-10000')

    async def terminate(self):
        self.compat.close()

    @filter.event_message_type(filter.EventMessageType.ALL, priority=10000)
    async def prepare_images(self, event: AstrMessageEvent):
        """Normalize BEFORE AngelHeart snapshots/caches the incoming image."""
        self.compat.install()
        if not self.engine.enabled() or not self.config.get('early_message_decode', True):
            return
        remaining = [number(self.config, 'max_apng_per_request', 3, 1, 10)]
        visited = set()
        async def walk(chain, depth=0):
            if not isinstance(chain, list) or depth > 4 or id(chain) in visited:
                return
            visited.add(id(chain))
            for component in chain:
                if isinstance(component, Image) and remaining[0] > 0:
                    # Prefer the same URL as AstrBot's resolver; keep all fields aligned.
                    source = getattr(component, 'url', '') or getattr(component, 'file', '') or getattr(component, 'path', '')
                    try:
                        result = await self.engine.resolve(source)
                        if result:
                            path = result['sheet']
                            component.file = path
                            component.url = path
                            component.path = path
                            remaining[0] -= 1
                            logger.info('[APNG Reader] 消息入口: %s', frame_note(result))
                    except Exception as exc:
                        logger.warning('[APNG Reader] 消息入口保留原图 (%s)', type(exc).__name__)
                # Also handle image components already present in a quoted message.
                await walk(getattr(component, 'chain', None), depth+1)
        await walk(event.get_messages())

    @filter.on_llm_request(priority=-10000)
    async def read_apng(self, event: AstrMessageEvent, req: ProviderRequest):
        """Run AFTER Heart(50)/Memory(40), including history and extra multimodal parts."""
        self.compat.install()
        if not self.engine.enabled():
            return
        remaining = [number(self.config, 'max_apng_per_request', 3, 1, 10)]
        notes = []
        async def convert(source):
            if remaining[0] <= 0 or not isinstance(source, str):
                return None
            try:
                result = await self.engine.resolve(source)
                if result:
                    remaining[0] -= 1
                    notes.append(frame_note(result))
                    logger.info('[APNG Reader] 请求收尾: %s', frame_note(result))
                    return result
            except Exception as exc:
                logger.warning('[APNG Reader] 请求图片保留原图 (%s)', type(exc).__name__)
            return None
        urls = getattr(req, 'image_urls', None)
        if isinstance(urls, list):
            replacements = []
            for source in urls:
                result = await convert(source)
                replacements.extend(result['paths'] if result else [source])
            req.image_urls = replacements

        async def walk(value, depth=0):
            if depth > 12:
                return value
            if isinstance(value, list):
                return [await walk(v, depth+1) for v in value]
            if isinstance(value, dict):
                if value.get('type') == 'image_url':
                    ref = value.get('image_url')
                    source = ref.get('url') if isinstance(ref, dict) else ref
                    result = await convert(source)
                    if result:
                        out = dict(value)
                        out['image_url'] = {**ref, 'url':result['sheet']} if isinstance(ref, dict) else result['sheet']
                        return out
                    return value
                # Copy changed context structures; never mutate shared ledger history.
                return {k:await walk(v, depth+1) if isinstance(v, (list,dict)) else v for k,v in value.items()}
            kind = getattr(value, 'type', None)
            if kind == 'image_url' and hasattr(value, 'image_url'):
                ref = value.image_url
                source = ref.get('url') if isinstance(ref, dict) else getattr(ref, 'url', None)
                result = await convert(source)
                if result:
                    out = copy.deepcopy(value)
                    if isinstance(out.image_url, dict):
                        out.image_url = {**out.image_url, 'url':result['sheet']}
                    else:
                        out.image_url.url = result['sheet']
                    return out
            return value
        for attr in ('contexts', 'extra_user_content_parts'):
            value = getattr(req, attr, None)
            if isinstance(value, list):
                setattr(req, attr, await walk(value))
        if notes and self.config.get('add_frame_note', True):
            req.prompt = (getattr(req, 'prompt', '') or '')+'\n\n[图片解码信息]\n'+'\n'.join(dict.fromkeys(notes))

    @filter.llm_tool(name='apng_extract_frames')
    async def extract_frames(self, event: AstrMessageEvent, path: str) -> str:
        """提取图片中封面下的 APNG 动画帧，返回本地静态 PNG 路径，不直接发送消息。
        若需要看图，可将返回路径传给 angel_describe_image；普通 PNG 无隐藏帧。

        Args:
            path(string): 图片的本地路径或 HTTP(S) URL，必须来自当前消息或工具结果。
        """
        self.compat.install()
        if not self.engine.enabled():
            return 'APNG 解析已关闭。'
        try:
            result = await self.engine.resolve(path)
            if not result:
                return '未发现 APNG 动画帧；如果原始字节已被转成静态封面，无法恢复隐藏内容。'
            return json.dumps({'note':frame_note(result), 'image_path':result['sheet'],
                               'frame_paths':result['paths'],
                               'next':'可调用 angel_describe_image 识别这些静态路径。'}, ensure_ascii=False)
        except Exception as exc:
            return f'APNG 提取失败 ({type(exc).__name__})；请检查原图是否完整或超过解码限制。'
