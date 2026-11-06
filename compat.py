"""Reversible adapters for the inspected Angel plugin interfaces.

Never imports another plugin just to patch it. Discovery only sees loaded modules;
re-run at event/request boundaries to support load order and plugin reloads.
"""
import asyncio
import contextvars
import functools
import sys
from pathlib import Path
from .engine import frame_note, number


class AngelCompatibility:
    def __init__(self, engine, logger):
        self.engine = engine
        self.logger = logger
        self.patches = []
        self._eye_rows = contextvars.ContextVar('apng_eye_rows', default=None)
        self._reported = set()

    def _patch(self, cls, name, factory):
        if not hasattr(cls, name):
            return
        current = getattr(cls, name)
        if getattr(current, '_apng_adapter', False):
            return
        wrapped = factory(current)
        wrapped._apng_adapter = True
        setattr(cls, name, wrapped)
        self.patches.append((cls, name, current, wrapped))
        label = f'{cls.__module__}.{name}'
        if label not in self._reported:
            self.logger.info('[APNG Reader] 兼容接口已接入: %s', label)
            self._reported.add(label)

    def install(self):
        if not self.engine.enabled() or not self.engine.config.get('angel_compatibility', True):
            return
        for name, mod in list(sys.modules.items()):
            if mod is None:
                continue
            if name.endswith('astrbot_plugin_angel_heart.roles.front_desk'):
                cls = getattr(mod, 'FrontDesk', None)
                if cls:
                    self._patch(cls, '_read_image_component_bytes', self._heart_read)
            elif name.endswith('astrbot_plugin_angel_heart.core.conversation_ledger'):
                cls = getattr(mod, 'ConversationLedger', None)
                if cls:
                    self._patch(cls, '_load_image_bytes', self._ledger_read)
            elif name.endswith('astrbot_plugin_angel_smile.services.meme_manager'):
                cls = getattr(mod, 'MemeManager', None)
                if cls and self.engine.config.get('smile_extract_last_frame', True):
                    self._patch(cls, '_resolve_image_ref', self._smile_resolve)
            elif name.endswith('astrbot_plugin_angel_eye.services.qq_history_service'):
                cls = getattr(mod, 'QQChatHistoryService', None)
                if cls and self.engine.config.get('eye_history_images', True):
                    self._patch(cls, '_format_messages', self._eye_format)
                    self._patch(cls, 'get_messages', self._eye_get)

    def _active(self):
        return self.engine.enabled() and self.engine.config.get('angel_compatibility', True)

    async def _normalize_bytes(self, data):
        if not self._active():
            return data
        try:
            result = await self.engine.bytes(data)
            if result:
                return await asyncio.to_thread(Path(result['sheet']).read_bytes)
        except Exception as exc:
            self.logger.warning('[APNG Reader] Angel 图片转换未完成，保留原字节 (%s)', type(exc).__name__)
        return data

    def _heart_read(self, original):
        @functools.wraps(original)
        async def wrapped(obj, *args, **kwargs):
            data, source = await original(obj, *args, **kwargs)
            return await self._normalize_bytes(data), source
        return wrapped

    def _ledger_read(self, original):
        @functools.wraps(original)
        async def wrapped(obj, *args, **kwargs):
            return await self._normalize_bytes(await original(obj, *args, **kwargs))
        return wrapped

    def _smile_resolve(self, original):
        @functools.wraps(original)
        async def wrapped(obj, *args, **kwargs):
            path, downloaded = await original(obj, *args, **kwargs)
            if not self._active() or not self.engine.config.get('smile_extract_last_frame', True) or path is None:
                return path, downloaded
            # Retain Smile's original path boundary BEFORE copying to our allowed cache.
            roots = getattr(obj, 'allowed_image_roots', ())
            try:
                resolved = Path(path).resolve()
                if not any(resolved == Path(r).resolve() or Path(r).resolve() in resolved.parents for r in roots):
                    return path, downloaded
                result = await self.engine.resolve(str(path), mode='last')
                if result:
                    return Path(result['paths'][-1]), downloaded
            except Exception as exc:
                self.logger.warning('[APNG Reader] Smile 拆帧失败，保留原图 (%s)', type(exc).__name__)
            return path, downloaded
        return wrapped

    def _eye_format(self, original):
        @functools.wraps(original)
        def wrapped(obj, messages, *args, **kwargs):
            # The query has already selected and ordered these exact rows. Do not query
            # again or rewrite the history database / shared service instance.
            rows = self._eye_rows.get()
            if rows is not None:
                rows.extend(messages)
            return original(obj, messages, *args, **kwargs)
        return wrapped

    def _eye_get(self, original):
        @functools.wraps(original)
        async def wrapped(obj, *args, **kwargs):
            if not self._active() or not self.engine.config.get('eye_history_images', True):
                return await original(obj, *args, **kwargs)
            rows = []
            token = self._eye_rows.set(rows)
            try:
                result = await original(obj, *args, **kwargs)
            finally:
                self._eye_rows.reset(token)
            remaining = number(self.engine.config, 'max_history_images', 6, 1, 20)
            output = list(result.formatted_messages)
            for i, msg in enumerate(rows[:len(output)]):
                chain = msg.get('message', []) if isinstance(msg, dict) else []
                if not isinstance(chain, list):
                    continue
                for comp in chain:
                    if remaining <= 0:
                        break
                    if not isinstance(comp, dict) or comp.get('type') != 'image':
                        continue
                    data = comp.get('data', {})
                    if not isinstance(data, dict):
                        continue
                    source = data.get('url') or data.get('file')
                    if not isinstance(source, str) or not source:
                        continue
                    remaining -= 1  # bound all downloads, including static/non-APNG files
                    try:
                        decoded = await self.engine.resolve(source)
                        if decoded:
                            output[i] += '\n[APNG历史图片] '+frame_note(decoded)
                            output[i] += '\n可读取的静态图片路径: '+decoded['sheet']
                            output[i] += '\n单帧路径: '+', '.join(decoded['paths'])
                            output[i] += '\n需要识别内容时，可用 angel_describe_image 读取以上路径。路径本身不代表已识别画面。'
                    except Exception as exc:
                        self.logger.warning('[APNG Reader] Eye 历史图片无法拆帧 (%s)', type(exc).__name__)
            result.formatted_messages = output
            return result
        return wrapped

    def close(self):
        for cls, name, original, wrapped in reversed(self.patches):
            # Do not overwrite an adapter installed by a later reload/another plugin.
            if getattr(cls, name, None) is wrapped:
                setattr(cls, name, original)
        self.patches.clear()
