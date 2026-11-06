"""Adapter contracts, concurrency, ingress and late request regression tests.

The upstream-shape stubs here do not constitute a live AstrBot/NapCat test.
Actual upstream methods are additionally exercised by the external QA script.
"""
import asyncio
import base64
import io
import json
import logging
import sys
import tempfile
import types
import unittest
from pathlib import Path
from PIL import Image

# Import hook tests first to install AstrBot-shaped stubs.
import test_apng_hook as hook
from astrbot_plugin_apng_reader.engine import FrameEngine
from astrbot_plugin_apng_reader.compat import AngelCompatibility
from astrbot_plugin_apng_reader.main import APNGReader


class CompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.apng = self.root/'hidden.dat'
        Image.new('RGBA',(20,20),'red').save(self.apng, format='PNG', save_all=True,
            append_images=[Image.new('RGBA',(20,20),'blue')], default_image=True)
        self.raw = self.apng.read_bytes()
        self.static = self.root/'static.png'
        Image.new('RGB',(20,20),'green').save(self.static)
        self.calls = []
        async def resolver(source):
            self.calls.append(source)
            return str(self.apng) if source.startswith('https://') else source
        self.engine = FrameEngine({}, self.root/'frames', resolver)
        self.compat = AngelCompatibility(self.engine, logging.getLogger('compat'))
        self.modules = []

    async def asyncTearDown(self):
        self.compat.close()
        for name in self.modules:
            sys.modules.pop(name, None)
        self.tmp.cleanup()

    def module(self, suffix, **exports):
        name = 'data.plugins.'+suffix
        module = types.ModuleType(name)
        module.__dict__.update(exports)
        sys.modules[name] = module
        self.modules.append(name)
        return module

    def assert_blue(self, data):
        with Image.open(io.BytesIO(data)) as im:
            self.assertEqual(im.convert('RGB').getpixel((0,0)), (0,0,255))

    async def test_heart_ingress_before_static_cache(self):
        raw = self.raw
        class FrontDesk:
            async def _read_image_component_bytes(self, component):
                return raw, 'original'
        self.module('astrbot_plugin_angel_heart.roles.front_desk', FrontDesk=FrontDesk)
        self.compat.install()
        data, ref = await FrontDesk()._read_image_component_bytes(None)
        self.assert_blue(data)
        self.assertEqual(ref, 'original')
        self.compat.close()
        self.assertEqual((await FrontDesk()._read_image_component_bytes(None))[0], raw)

    async def test_direct_caption_reader_and_disabled(self):
        raw = self.raw
        class ConversationLedger:
            async def _load_image_bytes(self, source):
                return raw
        self.module('astrbot_plugin_angel_heart.core.conversation_ledger', ConversationLedger=ConversationLedger)
        self.compat.install()
        self.assert_blue(await ConversationLedger()._load_image_bytes('anything'))
        self.engine.config['enabled'] = False
        self.assertEqual(await ConversationLedger()._load_image_bytes('anything'), raw)

    async def test_smile_retains_path_boundary_and_extracts_last(self):
        root = self.root
        apng = self.apng
        class MemeManager:
            allowed_image_roots = (root,)
            async def _resolve_image_ref(self, path):
                return Path(path), False
        self.module('astrbot_plugin_angel_smile.services.meme_manager', MemeManager=MemeManager)
        self.compat.install()
        result, downloaded = await MemeManager()._resolve_image_ref(str(apng))
        self.assert_blue(result.read_bytes())
        self.assertFalse(downloaded)
        obj = MemeManager()
        obj.allowed_image_roots = (root/'not-allowed',)
        untouched, _ = await obj._resolve_image_ref(str(apng))
        self.assertEqual(untouched, apng)

    async def test_eye_concurrent_queries_preserve_rows_and_do_not_mutate_db(self):
        source = str(self.apng)
        class QQChatHistoryService:
            def _format_messages(self, messages):
                return [m['id']+' [图片]' for m in messages]
            async def get_messages(self, group):
                rows = [{'id':group, 'message':[{'type':'image','data':{'url':source}}]}]
                text = self._format_messages(rows)
                await asyncio.sleep(0)
                return types.SimpleNamespace(formatted_messages=text, raw_rows=rows)
        self.module('astrbot_plugin_angel_eye.services.qq_history_service', QQChatHistoryService=QQChatHistoryService)
        self.compat.install()
        a,b = await asyncio.gather(QQChatHistoryService().get_messages('A'),QQChatHistoryService().get_messages('B'))
        self.assertTrue(a.formatted_messages[0].startswith('A '))
        self.assertTrue(b.formatted_messages[0].startswith('B '))
        self.assertIn('可读取的静态图片路径',a.formatted_messages[0])
        self.assertEqual(a.raw_rows[0]['message'][0]['data']['url'],source)
        self.assertIn('angel_describe_image',b.formatted_messages[0])

    async def test_eye_caps_even_non_apng_downloads(self):
        self.engine.config['max_history_images'] = 2
        static = str(self.static)
        class QQChatHistoryService:
            def _format_messages(self, messages):
                return ['[图片]']
            async def get_messages(self):
                text=self._format_messages([{'message':[{'type':'image','data':{'url':static}}]*20}])
                return types.SimpleNamespace(formatted_messages=text)
        self.module('astrbot_plugin_angel_eye.services.qq_history_service', QQChatHistoryService=QQChatHistoryService)
        self.compat.install()
        await QQChatHistoryService().get_messages()
        self.assertEqual(len(self.calls),2)

    async def test_module_reload_is_discovered_and_unload_restores(self):
        class FrontDesk:
            async def _read_image_component_bytes(self,c):
                return self.raw, ''
        mod = self.module('astrbot_plugin_angel_heart.roles.front_desk', FrontDesk=FrontDesk)
        first=FrontDesk._read_image_component_bytes
        self.compat.install()
        self.compat.install()
        self.assertEqual(len(self.compat.patches),1)
        class NewFrontDesk:
            async def _read_image_component_bytes(self,c):
                return b'', ''
        second=NewFrontDesk._read_image_component_bytes
        mod.FrontDesk=NewFrontDesk
        self.compat.install()
        self.assertEqual(len(self.compat.patches),2)
        self.compat.close()
        self.assertIs(FrontDesk._read_image_component_bytes,first)
        self.assertIs(NewFrontDesk._read_image_component_bytes,second)

    async def test_early_event_mutates_image_fields_and_quoted_images(self):
        plugin=APNGReader(None,{})
        plugin.engine=self.engine
        plugin.compat=self.compat
        current=hook.FakeImage(str(self.apng));current.url=str(self.apng)
        quoted=hook.FakeImage(str(self.apng))
        chain=[current,types.SimpleNamespace(chain=[quoted])]
        await plugin.prepare_images(types.SimpleNamespace(get_messages=lambda:chain))
        self.assertEqual(current.file,current.url)
        self.assertEqual(current.path,current.file)
        self.assert_blue(Path(current.file).read_bytes())
        self.assert_blue(Path(quoted.file).read_bytes())
        snapshot=current.file
        await plugin.prepare_images(types.SimpleNamespace(get_messages=lambda:chain))
        self.assertEqual(current.file,snapshot)

    async def test_request_after_heart_rewrite_contexts_extra_parts(self):
        plugin=APNGReader(None,{})
        plugin.engine=self.engine;plugin.compat=self.compat
        original={'role':'user','content':[{'type':'image_url','image_url':{'url':str(self.apng),'detail':'high'}}]}
        typed=types.SimpleNamespace(type='image_url',image_url={'url':str(self.apng)})
        req=types.SimpleNamespace(prompt='x',image_urls=[],contexts=[original],extra_user_content_parts=[typed])
        await plugin.read_apng(None,req)
        ref=req.contexts[0]['content'][0]['image_url']
        self.assertEqual(ref['detail'],'high')
        self.assert_blue(Path(ref['url']).read_bytes())
        self.assertNotEqual(typed.image_url['url'],req.extra_user_content_parts[0].image_url['url'])
        self.assertEqual(original['content'][0]['image_url']['url'],str(self.apng))
        self.assertIn('独立默认封面已跳过',req.prompt)

    async def test_frame_tool_returns_readable_paths(self):
        plugin=APNGReader(None,{})
        plugin.engine=self.engine;plugin.compat=self.compat
        result=json.loads(await plugin.extract_frames(None,str(self.apng)))
        self.assert_blue(Path(result['image_path']).read_bytes())

    async def test_same_cover_different_hidden_images_different_cache(self):
        other=self.root/'other.png'
        Image.new('RGBA',(20,20),'red').save(other,format='PNG',save_all=True,
            append_images=[Image.new('RGBA',(20,20),'green')],default_image=True)
        a=await self.engine.resolve(str(self.apng))
        b=await self.engine.resolve(str(other))
        self.assertNotEqual(a['sheet'],b['sheet'])
        with Image.open(b['sheet']) as im:
            self.assertEqual(im.getpixel((0,0)),(0,128,0,255))

if __name__=='__main__':
    unittest.main()
