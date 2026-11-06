"""Hook contract tests using AstrBot-shaped stubs, not a running server."""
import asyncio
import importlib
import logging
import sys
import tempfile
import types
import unittest
from pathlib import Path
from PIL import Image

for name in ['astrbot', 'astrbot.api', 'astrbot.api.event', 'astrbot.api.message_components', 'astrbot.api.provider', 'astrbot.api.star']:
    sys.modules[name] = types.ModuleType(name)
sys.modules['astrbot.api'].AstrBotConfig = dict
sys.modules['astrbot.api'].logger = logging.getLogger('test')
sys.modules['astrbot.api.event'].AstrMessageEvent = object
sys.modules['astrbot.api.event'].filter = types.SimpleNamespace(
    on_llm_request=lambda **kw: lambda f: f, event_message_type=lambda *a, **kw: lambda f: f,
    llm_tool=lambda **kw: lambda f: f, EventMessageType=types.SimpleNamespace(ALL='all'))
class FakeImage:
    def __init__(self, file):
        self.file = file
        self.url = ''
        self.path = ''
    async def convert_to_file_path(self):
        return self.url or self.file
sys.modules['astrbot.api.message_components'].Image = FakeImage
sys.modules['astrbot.api.provider'].ProviderRequest = object
class FakeStar:
    def __init__(self, context):
        pass
_cache_root = tempfile.TemporaryDirectory()
sys.modules['astrbot.api.star'].StarTools = types.SimpleNamespace(get_data_dir=lambda *a: _cache_root.name)
sys.modules['astrbot.api.star'].Star = FakeStar
sys.modules['astrbot.api.star'].Context = object
sys.modules['astrbot.api.star'].register = lambda *args: lambda cls: cls
APNGReader = importlib.import_module('astrbot_plugin_apng_reader.main').APNGReader

class HookTests(unittest.IsolatedAsyncioTestCase):
    async def test_mixed_images_and_idempotence(self):
        with tempfile.TemporaryDirectory() as tmp:
            static = str(Path(tmp) / 'static.png')
            apng = str(Path(tmp) / 'animation.png')
            Image.new('RGB', (8,8), 'red').save(static)
            Image.new('RGB', (8,8), 'red').save(apng, save_all=True,
                append_images=[Image.new('RGB', (8,8), 'blue')], default_image=True)
            req = types.SimpleNamespace(prompt='看看', image_urls=[static,apng])
            plugin = APNGReader(None,{})
            await plugin.read_apng(None,req)
            self.assertEqual(req.image_urls[0],static)
            self.assertEqual(Image.open(req.image_urls[1]).getpixel((0,0)), (0,0,255,255))
            self.assertIn('独立默认封面已跳过',req.prompt)
            snapshot = (req.prompt, list(req.image_urls))
            await plugin.read_apng(None,req)
            self.assertEqual((req.prompt,req.image_urls),snapshot)

    async def test_disabled(self):
        req = types.SimpleNamespace(prompt='x',image_urls=['untouched'])
        await APNGReader(None,{'enabled':False}).read_apng(None,req)
        self.assertEqual(req.image_urls,['untouched'])

    async def test_failure_preserves_original(self):
        req = types.SimpleNamespace(prompt='x',image_urls=['/nonexistent-apng'])
        await APNGReader(None,{}).read_apng(None,req)
        self.assertEqual(req.image_urls,['/nonexistent-apng'])
        self.assertEqual(req.prompt,'x')

if __name__=='__main__':
    unittest.main()
