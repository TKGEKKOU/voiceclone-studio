# VoiceClone Studio

VoiceClone Studio 是一个 Windows 本地语音工作台，用于管理 GPT-SoVITS 推理与训练、音色文件，并向服务器上的 AstrBot `astrbot_plugin_voice_clone_flow` 提供远程语音服务。

它适合这样的部署方式：AstrBot、NapCat 和插件运行在低配服务器，本地电脑负责 GPT-SoVITS 的推理或训练。Studio 通过 FRPC 主动连接服务器，服务器不需要直接访问本机入站端口。

## 当前能力

- GPT-SoVITS v2Pro 运行环境检测、安装、启动和停止
- FFmpeg 与 HT-Demucs 人声分离模型管理
- 视频/音频导入、音频提取、人声分离、VAD 切片和 ASR 标注
- 片段试听、文本审核、训练数据集生成和音色训练
- 音色目录扫描、试听、Provider 配置预览与发送
- GSVI TTS(API) 兼容接口，支持 AstrBot 远程 Provider
- FRPC 配置、启动、停止、连接状态和实例冲突检测
- 实时请求监控：文本接收、翻译、推理、音频生成和下载状态
- 生成音频自动清理，默认保留 10 分钟

## 快速启动

### 1. 安装依赖

建议使用 Python 3.11 或更高版本：

```powershell
cd E:\VCS
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 2. 启动 Studio

```powershell
.\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 9090
```

浏览器打开 `http://127.0.0.1:9090/`。也可以使用项目附带的 `start-studio.bat`。

Studio 启动后不会自动启动 GPT-SoVITS 或 FRPC。需要语音服务时，在页面的“语音服务总开关”中手动开启；关闭时会先停止 GPT-SoVITS 和 Studio 管理的 FRPC。

## 远程连接四步

在 Studio 页面进入“连接 AstrBot”，严格按页面编号操作：

1. 设置 Studio Token，保存并测试。这个 Token 稍后要原样填写到服务器插件。
2. 填写服务器公网地址、FRPS 控制端口和 FRP Token，保存并准备 FRPC。FRP Token 必须与服务器 `frps.toml` 一致。
3. 启动 FRPC 隧道，等待状态变成“已连接”或“远程可用”。
4. 将页面生成的 Studio 地址和同一个 Studio Token 填入服务器 AstrBot 插件的远程模式。

Studio 地址是服务器侧 AstrBot 使用的地址，不是本机浏览器地址。映射端口和 FRPS 控制端口都可以在页面中调整；服务器映射端口建议只允许服务器本机访问，不要开放到公网。

## 服务器插件配置

服务器上的 `astrbot_plugin_voice_clone_flow` 选择“远程模式”：

- **Studio 地址**：复制 Studio 第 4 步生成的地址
- **Studio Token**：填写 Studio 第 1 步设置的 Token
- **GPT-SoVITS 本地地址**：远程模式下留空

连接成功后，插件可以同步 Studio 音色并生成 GSVI TTS(API) Provider。AstrBot 的 Provider 请求会经由 FRP 映射回本机 Studio，再由 GPT-SoVITS 生成音频。

## 本地工作流

1. 在“运行环境”确认 GPT-SoVITS、FFmpeg 和人声分离模型状态。
2. 在“训练音色”上传有合法使用授权的视频或音频素材。
3. 等待音频提取、人声分离、VAD 切片和 ASR 标注完成。
4. 试听片段并修正文本，勾选要使用的片段。
5. 生成训练数据，填写音色名称并开始训练。
6. 在“音色与试听”选择音色进行生成试听，或在连接页生成 Provider 配置并发送给服务器插件。

训练比推理更占用显存、内存和磁盘。实际占用受模型、素材长度及并发量影响。推理时只需启动 GPT-SoVITS 和 FRPC，不需要重新训练。

## API

常用接口：

- `GET /api/health`
- `GET /api/voices`
- `POST /api/tts`
- `POST /infer_single`：AstrBot GSVI TTS(API) 兼容接口
- `GET /api/runtime/status`
- `GET/POST /api/voice-service/status|start|stop`
- `GET/POST /api/frp/status|prepare|start|stop`

除健康检查外，控制接口使用：

```http
Authorization: Bearer <Studio Token>
```

## 安全说明

- Studio Token 和 FRP Token 用途不同，建议分别设置。
- Token 保存在本机 `data/config/studio.json`，不要提交到 Git 或发送到公开渠道。
- 音色、模型、日志和生成音频默认保存在 `data/`，本仓库的 `.gitignore` 会排除这些运行数据。
- 服务器映射端口只供 AstrBot 使用，不建议配置公网访问。
- 仅使用你拥有合法授权的音频和音色素材。

## 常见问题

### 页面能打开，但 FRPC 一直“连接中”

检查服务器公网地址、FRPS 控制端口、FRP Token 是否与服务器配置完全一致，并确认服务器安全组放行 FRPS 控制端口。

### 可以生成语音，但页面状态报错

先按 `Ctrl+F5` 强制刷新页面。若仍有错误，查看 Studio 控制台和 `data/data/logs/` 下的日志。`POST /infer_single` 返回 `200` 表示推理接口已经成功。

### 服务器插件无法连接 Studio

确认服务器插件使用的是 FRP 映射后的 Studio 地址，而不是本机 `127.0.0.1:9090`；Studio 页面必须显示“远程可用”。

### 关闭 Studio 后 FRPC 仍在运行

保持“关闭 Studio 时停止 FRPC”选项开启，并通过页面的“语音服务总开关”关闭。该操作会先停止 GPT-SoVITS，再停止 Studio 管理的 FRPC。

## 许可证与第三方组件

项目许可证见 [LICENSE](LICENSE)，第三方组件与模型说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
