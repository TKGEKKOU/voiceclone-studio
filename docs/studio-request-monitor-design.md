# VoiceClone Studio 实时请求监控设计

## 目标

让用户在 Studio 本机明确看到每次远程 TTS 请求是否到达、如何处理、是否生成音频，以及 AstrBot 是否成功下载音频，从而定位 QQ 无语音发生在哪一层。

## 范围

- 监控 `/infer_single` 和 `/api/tts` 两类推理请求。
- 记录原始文本、目标音色、语言、翻译结果、当前阶段、耗时、错误和音频结果。
- 使用 SSE 实时推送更新，页面断线后自动重连；同时提供普通快照接口恢复状态。
- 生成的 WAV 文件从生成完成起保留 10 分钟，之后自动删除。
- 请求元数据保留最近 100 条且最长 24 小时，二者任一条件先达到即清理。

## 不在范围内

- 不修改 AstrBot 或 NapCat 的消息发送实现。
- 不承诺 Studio 能确认 NapCat 已经把语音发到 QQ；Studio 只能确认 AstrBot 是否下载了 WAV。
- 不记录 Studio Token、FRP Token、LLM/ASR API Key 或未脱敏请求头。

## 数据模型

每次请求创建一个稳定的 `request_id`，记录：

- `source`：`gsvi` 或 `studio_preview`
- `original_text`：Studio 收到的原始文本
- `processed_text`：翻译后或最终送入 GPT-SoVITS 的文本
- `voice_id` 和可读音色名称
- `requested_language` 与 `synthesis_language`
- `stage`：`received`、`translating`、`translated`、`synthesizing`、`generated`、`downloaded`、`failed`、`expired`
- `created_at`、`updated_at`、各阶段耗时和总耗时
- `audio_url`、音频字节数、时长、过期时间和是否仍可播放
- `error`：失败阶段和可操作错误信息

记录持久化到 `data/monitor/requests.json`，使用临时文件替换保证写入完整性。音频保存在 `data/monitor/audio/<request_id>.wav`。

## 请求流程

### GSVI 请求

1. `/infer_single` 收到请求并创建 `received` 记录。
2. 日语音色收到中文时进入 `translating`，成功后写入 `translated` 和处理文本。
3. 调用 GPT-SoVITS 时进入 `synthesizing`。
4. WAV 完成后原子写入文件，记录 `generated`、时长、大小和 10 分钟过期时间。
5. 返回指向 Studio 的一次性下载 URL。
6. AstrBot 请求音频 URL 时，在开始响应前更新为 `downloaded`，记录下载时间；音频文件仍保留到原定过期时间。
7. 到期后删除 WAV，记录改为 `expired`，元数据继续保留至 24 小时或被 100 条上限淘汰。

### Studio 试听

流程与 GSVI 相同，但 `source=studio_preview`。生成后直接流式返回，同时保留监控用 WAV 10 分钟。

## 实时接口

- `GET /api/monitor/requests`：返回最近请求快照，支持数量限制。
- `GET /api/monitor/requests/{request_id}`：返回单次请求详情。
- `GET /api/monitor/events`：SSE 推送创建、阶段变化、失败、下载和过期事件。
- `GET /api/monitor/audio/{request_id}`：在有效期内试听或下载 WAV。
- `DELETE /api/monitor/requests`：由页面“清空记录”显式调用，只删除监控元数据和监控音频，不影响音色或训练成果。

除 AstrBot 使用的现有一次性音频下载 URL 外，监控接口均继续要求 Studio Bearer Token。

## 页面

新增“实时监控”标签页：

- 顶部显示 Studio 请求状态、GPT-SoVITS 状态、最近一次成功和最近一次错误。
- 主区按时间倒序显示请求，每条只展示一层信息，不嵌套卡片。
- 每条展示来源、时间、音色、原文、处理后文本、阶段时间线、总耗时和错误。
- 音频有效时显示播放器、下载按钮和剩余保留时间；到期后显示“音频已自动删除”。
- 提供来源/状态筛选、暂停自动滚动、清空记录。
- SSE 断开时显示“实时连接已断开，正在重连”，但快照仍可手动刷新。

## 清理与稳定性

- 后台每 30 秒检查过期音频；进程启动时立即清理一次遗留文件。
- 每次读取快照和音频时也执行轻量过期检查，避免后台任务异常导致文件长期残留。
- 音频使用临时文件写完后原子重命名，页面不会播放半成品。
- 单个请求失败不能影响其他请求或 Studio 进程。
- SSE 使用有界订阅队列；慢客户端只丢弃旧事件并通过下一次快照恢复，不阻塞推理。

## 验收标准

1. AstrBot 调用 `/infer_single` 后，页面在一秒内出现请求和原始文本。
2. 中文转日文时能看到翻译前后文本与翻译耗时。
3. GPT-SoVITS 成功后可在页面试听 WAV，并显示大小、时长和生成耗时。
4. AstrBot 下载音频后，阶段显示为“已被 AstrBot 下载”。
5. 翻译、推理或下载票据失败时显示准确失败阶段和错误。
6. WAV 在生成后 10 分钟内可播放，到期后文件确实删除。
7. 元数据最多 100 条且不超过 24 小时。
8. 重启 Studio 后仍能看到未过期记录，并自动清理过期音频。
9. 监控中不出现任何 Token 或 API Key。
