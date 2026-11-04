# APNG 真图解析 · AstrBot 插件

在 AstrBot 默认 LLM 请求流程的 on_llm_request 钩子中检查本轮 image_urls，按真实文件格式识别 APNG，将其替换成普通 PNG 动画帧供视觉模型读取。不主动发送群消息，不另行调用模型，不改变群聊唤醒规则。

## 安装

1. 在 AstrBot 管理面板的插件管理中选择从本地 ZIP 安装，上传 astrbot_plugin_apng_reader.zip（不同版本入口名称可能不同）。
2. 依赖安装完成后启用插件；如未生效，重启 AstrBot。
3. 选择支持图片输入的模型，发送 APNG 并按原有规则 @机器人或触发回复。

也可手工解压，把完整 astrbot_plugin_apng_reader 文件夹放入 AstrBot 的 data/plugins/，确保 main.py 直接位于该文件夹。依赖 Pillow>=10.3.0，应安装在运行 AstrBot 的同一个 Python 环境，通常管理面板会自动安装 requirements.txt。

## 默认行为

- 检测文件内容而非扩展名，扩展名为 .png、.dat 的 APNG 也能识别。
- 如果存在独立 default_image 封面，跳过该封面。
- 动画不超过 6 帧时提供全部帧；更多帧时均匀抽取最多 6 帧，包含首尾动画帧。相同的抽中帧去重。
- 首帧如果本来就是动画的一部分，保留它；不会凭图像内容猜测哪张是“真图”。
- 使用 Pillow 顺序解码，处理 blend/dispose，得到完整画布而非局部差分块。
- 最长边缩到 1536，保留透明通道，按时间顺序以静态 PNG 传入模型。
- 每次最多转换 3 张 APNG；原有普通图片保持不变。解码失败、超限时保留原图，并写日志。
- 帧编号说明只附加到本轮用户提示，不修改人格提示。

## 设置

在插件配置页面设置：

| 参数 | 默认 | 含义 |
|---|---|---|
| enabled | true | 启用插件 |
| mode | sample | sample：抽帧；last：只读取最后一个动画帧 |
| max_frames | 6 | sample 模式最多提供 1–12 帧；设为 1 时提供末帧 |
| max_apng_per_request | 3 | 每次请求最多转换多少张 APNG |
| max_file_mb | 25 | 下载完成后检查的文件大小限制，不是网络下载流量上限 |
| max_decode_frames | 200 | 总帧数上限（包含独立封面），可调 1–500 |
| add_frame_note | true | 本轮提示增加帧数、抽帧编号说明 |

解码还限制单帧最多 2000 万像素、累计最多 3 亿像素。解码在线程中运行并串行控制，避免阻塞事件循环。静态帧使用内嵌 PNG 数据，无需额外 HTTP 服务或缓存清理。

如果主要处理“封面 + 真图”的包装图，可设 mode=last，只让模型读取末帧。但不是所有包装图都把目标内容放在末帧，sample 更通用。抽帧可能漏掉短暂出现的内容，不能保证读到所有帧。

## 适用范围与边界

- 对照 AstrBot v4.29.3 和当前上游源码核对了接口；尚未在用户服务器或真实 QQ/NapCat 环境中运行。
- 只处理传入默认 LLM 请求的图片列表。插件自建模型请求、提前进行的图片转文字、群聊主动参与判定插件，如果绕过该钩子，不受本插件控制。
- 需要完整 APNG 原始字节。如果 QQ/NapCat 传来的是已转码的静态 PNG/JPEG、缩略图或只有封面，无法恢复丢失的帧。可尝试以原图发送，并检查下载文件是否仍为 APNG。
- 当前不解析以“文件附件”形式到达且未进入 image_urls 的图片，也不处理 GIF/WebP/合并转发内部附件。
- 需要视觉模型；插件负责解码，不提供 OCR 或图像识别模型。多张帧图可能增加视觉 token 消耗。

日志成功示例：`[APNG Reader] 图片1: 2帧 -> 2张静态图`。

## 测试

6 项解码测试覆盖：静态 PNG、独立封面与单个真实动画帧、普通 APNG 与末帧模式、抽帧与超限、透明 OVER 合成、损坏 PNG。
另外使用 AstrBot 接口替身完成了 3 项钩子契约测试：混合图片与重复处理、关闭插件、失败保留原图。契约测试不能替代真实平台集成测试。

运行解码测试：`python -m unittest discover -s . -p test_decoder.py -v`

## 参考

- https://docs.astrbot.app/dev/star/guides/listen-message-event.html
- https://github.com/AstrBotDevs/AstrBot/tree/v4.29.3
- https://pillow.readthedocs.io/en/stable/handbook/image-file-formats.html#apng-sequences
