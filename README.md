# APNG 真图解析 · Angel 兼容版 v1.1.0

让 AstrBot 及 Angel 插件读取 APNG 封面下的动画帧，而不是只看静态封面。不主动发送群消息，不额外自动调用视觉模型，不改变群聊介入判定。

## 升级与使用

1. 用此 ZIP 更新原来的 `astrbot_plugin_apng_reader`。不要保留两个不同目录的同名插件。
2. **重启 AstrBot**，使运行时适配覆盖已经加载的 Angel 类和工具实例。
3. **重新发送一张完整 APNG**，再按平时方式 @机器人或触发天使心。之前已经压成静态 WebP 的缓存、已经生成的封面转述不会自动修复。
4. 保持 `early_message_decode=true`、`angel_compatibility=true`；这些选项默认开启。
5. 使用支持图片的主模型；若主模型不支持视觉，在天使心中配置可用的 `image_caption_provider_id`，让 `angel_describe_image` 读取静态图片路径。插件只拆帧，不提供视觉模型。

若主要是“封面 + 底图”的包装图，可将 `mode` 改为 `last`，只取最后一个动画帧；默认 `sample` 更适合一般动画。目标内容不一定总在末帧，不能仅靠格式确定哪一帧是“真图”。

## 修复了什么

旧版只修改 `on_llm_request` 的 `req.image_urls`，存在三个绕过路径：

- 原优先级 `100` 在天使心的 `50` 之前执行，结果可能被其上下文重写覆盖。AstrBot 按优先级从高到低执行。
- 天使心提前将图片转为静态 WebP：直接 `Image.open(...).convert(...)` 会读取封面，后续才拆帧已经来不及。
- 天使心直接 `provider.text_chat(...)` 的按需看图不经过旧钩子；天使之眼历史返回只有“[图片]”，图片引用已在格式化时丢失。

新版处理路径：

1. 消息入口，优先级 `10000`：在 AngelHeart 缓存之前将图片组件的 `file/url/path` 同步替换为静态预览路径。已加载的引用消息图片链也会处理，不重新请求引用消息。
2. Angel 适配：只包装已经加载模块的特定方法，不改动四个插件磁盘源码、不全局修改 Pillow、不劫持所有模型请求。
3. 请求收尾，优先级 `-10000`：在 Heart(50)/Memory(40) 重写后检查 `image_urls`、`contexts`、`extra_user_content_parts`，保留其他字段和历史原对象。
4. 手动/工具补救：新增 `apng_extract_frames(path)`，返回静态预览和单帧路径，便于 `angel_describe_image` 或表情工具使用。不直接向聊天发送图片。

## 四个插件的兼容范围

| 插件 | 实现 | 边界 |
|---|---|---|
| AngelHeart 天使心 | 包装 `FrontDesk._read_image_component_bytes` 和 `ConversationLedger._load_image_bytes`，在缓存、转述、按需图片理解压缩之前拆帧；入口也提前替换图片 | 旧版接口若改名，须更新适配；已压成封面的历史缓存无法恢复 |
| AngelMemory 天使记忆 | 保留其原有记忆检索/注入逻辑；入口图片先归一化，请求收尾在其注入之后运行 | 记忆插件本身没有新增视觉能力，记住的是实际收到的文本/识别结果 |
| AngelEye 天使之眼 | 捕获本次查询真正选中、同顺序的原始消息；在返回文本中附 APNG 静态路径，适用于服务器历史及命中本地缓存的查询；不修改历史数据库 | 历史工具仍返回文字，不会自动变成视觉回答。需要调用 `angel_describe_image` 读取路径；历史 URL 必须仍有效。每次只检查有限图片 |
| AngelSmile 天使之笑 | 原始 APNG 入库前提取末帧，避免只用封面判重/收录；可直接收录拆出的单帧 PNG | 原始动画会被收录为静态末帧，可关闭 `smile_extract_last_frame` 保留其原行为；传入多帧预览路径则收录预览，收单帧请用单帧路径 |

适配以 2026-11-06 读取的四个仓库默认分支为准：

- https://github.com/kawayiYokami/astrbot_plugin_angel_heart
- https://github.com/kawayiYokami/astrbot_plugin_angel_memory
- https://github.com/kawayiYokami/astrbot_plugin_angel_eye
- https://github.com/kawayiYokami/astrbot_plugin_angel_smile

适配在初始化、消息入口、模型请求和提帧工具调用时重新发现已加载模块，支持不同加载顺序和被适配插件重载。卸载本插件会还原由本实例安装且尚未被其他代码替换的方法。配置关闭时保留原调用结果。

## 输出规则与资源限制

- 按文件内容判断 APNG，`.png`、`.dat` 也可识别。
- 独立 `default_image` 封面跳过；若首帧属于动画本身则保留。
- 使用 Pillow 顺序解码，遵守 dispose/blend，输出完整画布而非差分块。
- 默认最多均匀抽取 6 帧，含首尾，相同抽中帧去重；可能遗漏未抽中的短暂内容。
- 请求 `image_urls` 直接拆为单帧列表；缓存和嵌入上下文保持一个图片槽：单帧直接使用，多帧合成标有 `APNG frame i/n` 的时间序列预览，不是多个人物场景。
- 单帧最长边 1536；多帧预览总尺寸最多约 2048，长文本应改用单帧路径识别。
- 静态 PNG 保留透明通道；多格预览使用白底。Heart 自己后续仍会压缩为 WebP，透明/颜色处理遵循其逻辑。
- 解码在线程运行，串行限制；每个原始字节内容和配置使用不同 SHA-256 缓存键，封面相同而隐藏帧不同不会共用同一导出文件。
- 静态文件持久存放在 AstrBot 的 `data/plugin_data/astrbot_plugin_apng_reader/frames/`（以 StarTools 实际数据目录为准）。不会自动清理仍可能被引用的缓存。需要清理时先停止 AstrBot，注意历史工具结果中的路径会失效。
- 默认最多 25 MB、总帧数 200、单帧 2000 万像素、累计 3 亿像素。大小检查在下载完成后进行，不是网络流量限制。
- 网络获取沿用 AstrBot 图片解析器并设置 30 秒等待超时；每次历史查询最多检查 6 张图，包括检查后发现并非 APNG 的图。

## 配置

沿用 `enabled`、`mode`、`max_frames`、`max_apng_per_request`、`max_file_mb`、`max_decode_frames`、`add_frame_note`。

新增：

| 参数 | 默认 | 用途 |
|---|---|---|
| early_message_decode | true | 消息进入插件链时先拆帧 |
| angel_compatibility | true | 启用 Angel 可撤销适配 |
| smile_extract_last_frame | true | 天使之笑直接收录 APNG 时使用末帧 |
| eye_history_images | true | 历史查询补出 APNG 静态路径 |
| max_history_images | 6 | 每次历史查询最多检查多少张图片，1–20 |

## 仍看不到隐藏内容时

先看日志：

- `消息入口: APNG 已解码`：收到完整原始动画并已替换图片。
- `兼容接口已接入: ...FrontDesk...` / `...ConversationLedger...` / `...MemeManager...` / `...QQChatHistoryService...`：对应运行时适配已安装。只有安装并加载的插件才会出现。
- `请求收尾: APNG 已解码`：请求末尾发现另一路传入的 APNG 并兜底拆帧。
- `保留原图 (...)`：解码失败、超限或图片下载失败，括号中是异常类别。

如果 NapCat/QQ 提供的已经是静态缩略图、JPEG 或只有封面的 PNG，任何拆帧器都无法从这些字节恢复原动画。需要获取真正的 APNG 原文件后重新发送。合并转发内部未展开的附件、纯文件附件不会自动展开；可以在工具已经取得原始图片路径后调用 `apng_extract_frames`。

## 验证情况

- 6 项解码测试：普通 PNG、独立封面、末帧、抽帧/超限、透明叠加、损坏 PNG。
- 13 项钩子/适配测试：入口及引用链、请求收尾多模态字段、幂等、禁用/失败降级、同封面不同隐藏帧、看图路径、Smile 路径边界、Eye 并发查询隔离与检查数量限制、重载与卸载等。
- 另将读取的上游实际方法按原函数体执行，验证 FrontDesk→WebP、ConversationLedger.describe_image→视觉输入、Smile 路径解析和 Eye 格式化的实际连接。测试用视觉 Provider 只捕获输入像素，不声称测过模型识别准确率。
- **尚未连接用户服务器或真实 QQ/NapCat 实测**，因此不承诺所有安装版本、平台转码形式都可识别。

从包含插件目录的父目录运行：

```bash
PYTHONPATH=. python -m unittest discover -s astrbot_plugin_apng_reader/tests -v
python -m unittest discover -s astrbot_plugin_apng_reader -p test_decoder.py -v
```

依赖：Pillow>=10.3.0，装在运行 AstrBot 的 Python 环境中。
