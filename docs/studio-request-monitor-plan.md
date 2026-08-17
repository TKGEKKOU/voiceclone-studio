# VoiceClone Studio 实时请求监控实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在正式 Studio 中实时展示 TTS 请求全链路，并使生成 WAV 在 10 分钟后自动删除。

**Architecture:** 新增独立 `RequestMonitorStore` 管理持久化元数据、音频文件、订阅队列和清理。`app.py` 只负责在现有 `/api/tts`、`/infer_single` 和音频下载边界写入阶段事件；页面通过快照接口恢复状态，通过 SSE 接收增量更新。

**Tech Stack:** Python 3.11 标准库、FastAPI、SSE `StreamingResponse`、原生 HTML/CSS/JavaScript、`unittest`。

## Global Constraints

- WAV 从生成完成起严格保留 10 分钟。
- 元数据最多 100 条且最长保留 24 小时。
- 不记录或返回任何 Token、API Key 或鉴权请求头。
- 不修改 AstrBot 或 NapCat 源码。
- GPT-SoVITS 仍只能由用户手动启动。
- 监控失败不得中断语音推理主流程。

---

### Task 1: 请求监控存储与清理

**Files:**
- Create: `studio_core/request_monitor.py`
- Test: `tests/test_request_monitor.py`

**Interfaces:**
- Produces: `RequestMonitorStore(root, audio_ttl_seconds=600, metadata_ttl_seconds=86400, max_records=100)`
- Produces: `create_request(...) -> dict`、`update(request_id, stage, **changes) -> dict`、`save_audio(request_id, bytes) -> dict`、`mark_downloaded(request_id) -> dict`、`snapshot(limit=100) -> list[dict]`、`audio_path(request_id) -> Path | None`、`cleanup() -> dict`、`subscribe() -> asyncio.Queue`

- [ ] 写失败测试：创建请求后持久化；保存 WAV 后记录大小、时长和 `expires_at`；时间推进 601 秒后文件删除且阶段为 `expired`；超过 100 条或 24 小时的记录被清理。
- [ ] 运行 `C:\Python311\python.exe -m unittest tests.test_request_monitor -v`，确认因模块不存在失败。
- [ ] 使用 JSON 临时文件替换、WAV 临时文件原子重命名和标准库 `wave` 实现最小存储；所有公开数据仅包含允许字段。
- [ ] 实现有界订阅队列，更新时发布脱敏事件；队列满时丢弃最旧事件。
- [ ] 重跑测试，预期全部通过。

### Task 2: 推理与下载边界埋点

**Files:**
- Modify: `app.py`
- Test: `tests/test_monitor_api.py`
- Test: `tests/test_tts_api.py`

**Interfaces:**
- Consumes: Task 1 的 `RequestMonitorStore`
- Produces: `GET /api/monitor/requests`、`GET /api/monitor/requests/{request_id}`、`GET /api/monitor/audio/{request_id}`、`GET /api/monitor/events`、`DELETE /api/monitor/requests`

- [ ] 写失败测试：`/infer_single` 依次产生 `received/translating/translated/synthesizing/generated`，音频 GET 后为 `downloaded`；推理异常为 `failed` 且响应保持原状态码。
- [ ] 写失败测试：`/api/tts` 也生成监控记录与可回放 WAV；监控接口要求 Bearer Token；记录中不存在 `api_key`、`token`、`authorization`。
- [ ] 运行两个测试模块并确认新接口 404 或记录缺失。
- [ ] 在 `create_app()` 中创建 store，给翻译和推理阶段增加非阻塞记录；监控写入异常只记录日志，不改变主响应。
- [ ] 将现有一次性票据关联 `request_id`；AstrBot 下载前调用 `mark_downloaded()`，但不提前删除监控 WAV。
- [ ] 添加启动时清理和 30 秒后台清理循环，并在应用关闭时取消任务。
- [ ] 重跑 TTS 与监控 API 测试，预期全部通过。

### Task 3: 实时监控页面

**Files:**
- Modify: `pages/studio-v2.html`
- Test: `tests/test_ui_contract.py`

**Interfaces:**
- Consumes: Task 2 的快照、SSE、监控音频和清空接口
- Produces: `data-tab="monitor"` 页面、`monitorList`、`monitorConnection`、状态/来源筛选和音频播放器

- [ ] 扩展静态契约测试，要求实时监控标签、连接状态、筛选、请求列表、播放器容器和清空按钮存在。
- [ ] 运行 UI 契约测试，确认缺少元素失败。
- [ ] 新增简洁的操作型监控页面：顶部摘要，下方单层时间线列表；桌面双列信息，移动端单列；不嵌套卡片。
- [ ] JavaScript 首次加载快照，然后使用 `EventSource` 携带短期页面票据或带鉴权的 fetch 流读取 SSE；断线指数退避重连。
- [ ] 每条记录展示原文、处理后文本、音色、阶段、耗时、错误、下载状态、剩余时间和有效期内音频播放器。
- [ ] 重跑 UI 契约和 JavaScript 语法检查，预期通过。

### Task 4: 正式目录、真实链路与保留期验收

**Files:**
- Modify: `E:\VCS\app.py`
- Create: `E:\VCS\studio_core\request_monitor.py`
- Modify: `E:\VCS\pages\studio-v2.html`
- Add tests under: `E:\VCS\tests`

**Interfaces:**
- Consumes: Tasks 1-3 的已验证文件
- Produces: 可运行的正式 Studio 与验收报告

- [ ] 将临时开发镜像中通过测试的文件复制到 `E:\VCS`，逐文件校验 SHA-256。
- [ ] 重启 Studio，仅确认 `9090`；验证 `17005` 不会随 Studio 自动启动。
- [ ] 手动启动 GPT-SoVITS，发送中文给日语音色，确认页面依次显示翻译、推理、生成和 AstrBot 下载状态。
- [ ] 验证监控 WAV 可播放，模拟时间或缩短测试 TTL 验证文件删除，生产配置保持 600 秒。
- [ ] 运行 Studio 全测试、Python 语法检查、JavaScript 语法检查和桌面/移动视口溢出检查。
- [ ] 关闭测试启动的 GPT-SoVITS，Studio 保持运行；报告 PID、停止命令、已验证事实和仍需服务器/NapCat 验证的边界。
