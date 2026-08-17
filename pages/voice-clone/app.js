const bridge = window.AstrBotPluginPage;
const $ = (id) => document.getElementById(id);
let modelReady = false;
let currentTask = "";
let timer = null;
let runtimeTimer = null;
let currentVoice = "";
let trainingTimer = null;
const presetValues = { light: [5, 10], quick: [10, 20], standard: [15, 30], enhanced: [20, 50], fine: [30, 100] };
const phaseNames = { preparing: "准备", download: "下载源码", extracting: "解压", python: "准备 Python", dependencies: "安装依赖", models: "下载模型", patching: "应用补丁", cleaning: "清理", verifying: "校验", complete: "完成", error: "失败", cancelling: "正在取消" };

function errorText(error) {
  return error?.message || String(error);
}

function applyRemoteMode() {
  const mode = $("remoteMode").value;
  for (const item of document.querySelectorAll("[data-local-only]")) item.hidden = mode === "remote";
  $("remoteBaseUrl").disabled = mode !== "remote";
  $("remoteToken").disabled = mode !== "remote";
  $("remoteTimeout").disabled = mode !== "remote";
}
function renderRemoteVoices(voices) {
  const box = $("remoteVoices"); box.replaceChildren();
  if (!voices?.length) { box.hidden = true; return; }
  box.hidden = false;
  for (const voice of voices) {
    const row = document.createElement("div"); row.className = "remote-voice-row";
    row.textContent = `${voice.name || voice.remote_voice_id} · ${voice.reference_language || "zh"} · ${voice.status || "ready"} · ${voice.reference_text || "无参考文本"}`;
    box.append(row);
  }
}
async function loadRemoteStatus() {
  const data = await bridge.apiGet("remote/status"); const config = data.config || {};
  $("remoteMode").value = data.mode || "local";
  $("remoteBaseUrl").value = config.base_url || "";
  $("remoteToken").value = config.token || "";
  $("remoteTimeout").value = config.timeout_seconds || 300;
  $("remoteStatus").textContent = data.last_error ? `异常：${data.last_error}` : data.configured ? "已配置" : "未配置";
  renderRemoteVoices(data.voices || []); applyRemoteMode();
}
async function remoteAction(route) {
  const payload = { mode: $("remoteMode").value, base_url: $("remoteBaseUrl").value.trim(), token: $("remoteToken").value, timeout_seconds: Number($("remoteTimeout").value) };
  $("remoteMessage").textContent = "处理中..."; $("remoteTest").disabled = true; $("remoteSync").disabled = true;
  try { const data = await bridge.apiPost(route, payload); $("remoteMessage").textContent = route.endsWith("sync") ? `已同步 ${data.count || 0} 个音色` : "连接成功"; await loadRemoteStatus(); }
  catch (error) { $("remoteMessage").textContent = errorText(error); }
  finally { $("remoteTest").disabled = false; $("remoteSync").disabled = false; }
}

function formatBytes(value) {
  const bytes = Number(value || 0);
  if (!bytes) return "--";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** index).toFixed(index > 2 ? 1 : 0)} ${units[index]}`;
}

function update() {
  $("createTask").disabled = !modelReady || !$("providerSelect").value
    || !$("materialInput").files.length || !$("authorized").checked;
}

async function providers() {
  const data = await bridge.apiGet("stt/providers");
  const select = $("providerSelect");
  select.replaceChildren();
  for (const provider of data.providers || []) {
    select.add(new Option(
      `${provider.id}${provider.model ? ` · ${provider.model}` : ""}`,
      provider.id,
      false,
      provider.selected,
    ));
  }
  select.disabled = !select.options.length;
  $("connectionStatus").dataset.state = select.options.length ? "ready" : "error";
  $("connectionStatus").textContent = select.options.length
    ? `${select.options.length} 个 Provider 可用`
    : "没有可用的 STT Provider";
  update();
}

function showProviderError(error) {
  $("providerSelect").disabled = true;
  $("connectionStatus").dataset.state = "error";
  $("connectionStatus").textContent = `Provider 读取失败：${errorText(error)}`;
  update();
}

function showSeparatorError(error) {
  modelReady = false;
  $("taskError").hidden = false;
  $("taskError").textContent = `运行环境不可用：${errorText(error)}。请前往插件配置检查模型和 FFmpeg 路径。`;
  update();
}

async function selectVoice(voiceId) {
  currentVoice = voiceId || "";
  if (!currentVoice) return;
  const data = await bridge.apiGet(`gpt-sovits/voices/${currentVoice}`);
  const provider = data.provider || {};
  $("providerApi").textContent = provider.api_base_url || "";
  $("providerGpt").textContent = provider.gpt_model_path || "";
  $("providerSovits").textContent = provider.sovits_model_path || "";
  $("providerReference").value = provider.reference_audio_path || "";
  $("providerText").value = provider.reference_audio_text || "";
  $("providerLanguage").textContent = provider.reference_language || "zh";
  $("applyProvider").disabled = false;
  $("saveProviderReference").disabled = false;
  $("referenceSaveResult").textContent = "";
}

async function loadVoices(preferred = "") {
  const data = await bridge.apiGet("gpt-sovits/voices");
  const select = $("providerVoice");
  select.replaceChildren();
  for (const voice of (data.voices || []).filter((item) => item.status === "ready")) {
    select.add(new Option(`${voice.name} · ${voice.dir_name || voice.id.slice(0, 8)}`, voice.id));
  }
  if (preferred && [...select.options].some((item) => item.value === preferred)) select.value = preferred;
  if (!select.options.length) select.add(new Option("暂无训练完成的音色", ""));
  const discovery = data.discovery || {};
  if ((discovery.imported || []).length) {
    $("providerActionResult").textContent = `已导入外部音色：${discovery.imported.join("、")}`;
  } else if ((discovery.skipped || []).length) {
    const first = discovery.skipped[0];
    $("providerActionResult").textContent = `${first.dir_name} 未导入：${first.reason}`;
  }
  await selectVoice(select.value);
}

function renderFfmpegManualHelp(resource) {
  const help = $("ffmpegManualHelp");
  if (!help) return;
  help.replaceChildren();
  if (!resource.error) { help.hidden = true; return; }
  help.hidden = false;
  const title = document.createElement("strong");
  title.textContent = "自动下载失败，可手动下载 FFmpeg";
  const text = document.createElement("p");
  text.textContent = `下载 ZIP 后解压，将 ffmpeg.exe 和 ffprobe.exe 放入：${resource.managed_path}`;
  help.append(title, text);
  for (const item of resource.manual_download_urls || []) {
    const link = document.createElement("a");
    link.href = item.url;
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = item.name;
    help.append(document.createTextNode(" "), link);
  }
}

function renderResource(prefix, resource) {
  const ready = Boolean(resource.ready || resource.usable);
  const installing = Boolean(resource.installing);
  const probing = Boolean(resource.probing);
  const label = prefix === "ffmpeg" ? "FFmpeg" : "人声分离模型";
  $(`${prefix}State`).textContent = `${label} ${installing ? (probing ? "正在测速选择最快下载源" : `下载中 ${resource.progress_percent || 0}%`) : ready ? "已就绪" : "未安装"}`;
  $(`${prefix}State`).dataset.state = installing ? "loading" : ready ? "ready" : "error";
  $(`${prefix}Path`).textContent = resource.resolved_path || resource.active_model || resource.resolved_model || "";
  const percent = Number.isFinite(resource.progress_percent) ? resource.progress_percent : (resource.total_bytes ? Math.round(resource.downloaded_bytes * 100 / resource.total_bytes) : 0);
  const verification = resource.verification === "verified" ? " · SHA-256 已校验" : resource.verification === "failed" ? " · SHA-256 校验失败" : resource.verification === "unverified" && ready ? " · 未取得 SHA-256" : "";
  $(`${prefix}Progress`).textContent = resource.error || (installing ? (probing ? "正在并行测速多个下载源，自动选择最快的一个…" : resource.total_bytes ? `${percent}%` : "正在计算进度") : `来源：${resource.source || "unknown"}${verification}`);
  if (prefix === "ffmpeg") renderFfmpegManualHelp(resource);
  $(`${prefix}ProgressBar`).style.transform = `scaleX(${Math.max(0, Math.min(100, percent)) / 100})`;
  const track = $(`${prefix}ProgressTrack`);
  if (track) track.hidden = !installing;
  const install = prefix === "ffmpeg" ? $("installFfmpeg") : $("installSeparator");
  const remove = prefix === "ffmpeg" ? $("deleteFfmpeg") : $("deleteSeparator");
  install.disabled = installing || ready;
  remove.disabled = installing || resource.source !== "managed";
}

async function loadRuntimeStatus() {
  clearTimeout(runtimeTimer);
  try {
    const data = await bridge.apiGet("runtime/status");
    const gpt = await bridge.apiGet("gpt-sovits/status");
    const runtime = gpt.runtime || {}, service = gpt.service || {};
    const ttsText = runtime.installing ? `下载中 ${runtime.progress_percent || 0}%` : service.starting ? "正在启动" : service.service_running ? "服务运行中" : service.start_error ? "启动失败" : runtime.installed ? "已安装，未启动" : "未安装";
    $("gptState").textContent = ttsText;
    $("ttsState").textContent = `GPT-SoVITS TTS ${ttsText}`;
    $("ttsState").dataset.state = service.service_running ? "ready" : service.start_error ? "error" : service.starting || runtime.installing ? "loading" : "idle";
    $("gptPath").textContent = runtime.install_dir || service.install_dir || "";
    const strategy = runtime.install_strategy === "linux_source" ? "源码安装" : "v2Pro 整合包";
    $("gptMeta").textContent = `${runtime.platform || data.platform?.system || "unknown"} ${runtime.architecture || data.platform?.architecture || ""} · ${strategy} · ${String(runtime.runtime_device || "").toUpperCase()} · 可用空间 ${formatBytes(runtime.disk_free_bytes)}`;
    const phase = phaseNames[runtime.phase] || runtime.phase || "";
    const percentText = Number.isFinite(runtime.progress_percent) ? `总进度 ${runtime.progress_percent}%` : "";
    const phasePercent = Number.isFinite(runtime.phase_progress_percent) ? `当前项 ${runtime.phase_progress_percent}%` : "";
    const serviceDetail = service.service_running ? `PID ${service.process_id || "外部进程"} · 日志 ${service.log_path || ""}` : "";
    $("gptProgress").textContent = runtime.error || service.start_error || (service.starting ? `正在加载模型 · 日志 ${service.log_path || ""}` : runtime.installing ? [phase, percentText, phasePercent, runtime.detail || runtime.last_output].filter(Boolean).join(" · ") : serviceDetail);
    $("gptProgressTrack").hidden = !runtime.installing;
    const gptPercent = runtime.installing && Number.isFinite(runtime.progress_percent) ? runtime.progress_percent : 0;
    $("gptProgressBar").style.transform = `scaleX(${Math.max(0, Math.min(100, gptPercent)) / 100})`;
    $("installGpt").hidden = runtime.installing; $("cancelGpt").hidden = !runtime.installing;
    $("installGpt").disabled = runtime.installed; $("startGpt").hidden = Boolean(service.service_running || service.starting); $("stopGpt").hidden = !service.service_running && !service.starting; $("startGpt").disabled = runtime.installing || !runtime.installed; $("deleteGpt").disabled = runtime.installing || service.starting || !runtime.present;
    renderResource("ffmpeg", data.ffmpeg || {});
    renderResource("separator", data.separator || {});
    $("runtimeError").textContent = "";
    modelReady = Boolean(data.separator?.usable);
    update();
    if (data.ffmpeg?.installing || data.separator?.installing || gpt.runtime?.installing || service.starting) runtimeTimer = setTimeout(loadRuntimeStatus, 1200);
  } catch (error) {
    $("runtimeError").textContent = errorText(error);
  }
}

async function runtimeAction(route) {
  $("runtimeError").textContent = "";
  try { await bridge.apiPost(route, {}); await loadRuntimeStatus(); }
  catch (error) { $("runtimeError").textContent = errorText(error); }
}

async function startGpt() {
  $("gptState").textContent = "正在提交启动请求";
  $("gptProgress").textContent = "准备加载 GPT-SoVITS 模型";
  $("startGpt").disabled = true;
  await runtimeAction("gpt-sovits/start");
}

$("toggleRuntime").onclick = () => {
  const details = $("runtimeDetails");
  details.hidden = !details.hidden;
  $("toggleRuntime").setAttribute("aria-expanded", String(!details.hidden));
};
$("remoteMode").onchange = applyRemoteMode;
$("remoteTest").onclick = () => remoteAction("remote/test");
$("remoteSync").onclick = () => remoteAction("remote/sync");
$("refreshRuntime").onclick = loadRuntimeStatus;
$("installFfmpeg").onclick = () => runtimeAction("runtime/ffmpeg/install");
$("deleteFfmpeg").onclick = () => runtimeAction("runtime/ffmpeg/delete");
$("installSeparator").onclick = () => runtimeAction("separator/install");
$("deleteSeparator").onclick = () => runtimeAction("separator/delete");
$("installGpt").onclick = () => runtimeAction("gpt-sovits/install");
$("cancelGpt").onclick = () => runtimeAction("gpt-sovits/install/cancel");
$("startGpt").onclick = startGpt;
$("stopGpt").onclick = () => runtimeAction("gpt-sovits/stop");
$("deleteGpt").onclick = () => runtimeAction("gpt-sovits/delete");
$("providerSelect").onchange = update;
$("materialInput").onchange = update;
$("authorized").onchange = update;

function renderReviewRows(rows) {
  const box = $("segments");
  box.replaceChildren();
  $("exportDataset").disabled = !rows.length;
  if (!rows.length) {
    box.innerHTML = '<div class="empty-state">正在处理，尚未生成片段</div>';
    return;
  }
  for (const row of rows) {
    const card = document.createElement("article");
    card.className = "segment";
    card.dataset.name = row.audio_name;
    card.innerHTML = `<div class="segment-head"><label class="segment-select"><input class="approved" type="checkbox" ${row.approved !== false ? "checked" : ""}><span><strong>片段 ${row.index + 1}</strong><small>${row.start_seconds}s - ${row.end_seconds}s</small></span></label></div><div class="segment-audio"><audio controls preload="metadata"></audio><span class="audio-state">正在准备音频</span></div><label class="transcript-field"><span>识别文本</span><textarea rows="3" aria-label="片段 ${row.index + 1} 的识别文本"></textarea></label>`;
    card.querySelector("textarea").value = row.text;
    const audio = card.querySelector("audio");
    const audioState = card.querySelector(".audio-state");
    (async () => {
      try {
        const data = await bridge.apiGet(`tasks/${currentTask}/audio/${encodeURIComponent(row.audio_name)}`);
        const bytes = Uint8Array.from(atob(data.base64), (char) => char.charCodeAt(0));
        audio.src = URL.createObjectURL(new Blob([bytes], { type: data.content_type }));
        audioState.hidden = true;
      } catch (error) {
        audioState.textContent = `音频加载失败：${errorText(error)}`;
        audioState.dataset.state = "error";
      }
    })();
    box.append(card);
  }
}

function renderLegacy(rows) {
  const box = $("segments");
  box.replaceChildren();
  $("exportDataset").disabled = !rows.length;
  if (!rows.length) {
    box.innerHTML = '<div class="empty-state">正在处理，尚未生成片段</div>';
    return;
  }
  for (const row of rows) {
    const card = document.createElement("article");
    card.className = "segment";
    card.dataset.name = row.audio_name;
    card.innerHTML = `<div><label><input class="approved" type="checkbox" ${row.approved !== false ? "checked" : ""}> <strong>片段 ${row.index + 1}</strong></label><span>${row.start_seconds}s - ${row.end_seconds}s</span></div><button class="play secondary" type="button">加载试听</button><audio controls preload="none"></audio><textarea rows="2"></textarea>`;
    card.querySelector("textarea").value = row.text;
    card.querySelector(".play").onclick = async () => {
      const data = await bridge.apiGet(`tasks/${currentTask}/audio/${encodeURIComponent(row.audio_name)}`);
      const bytes = Uint8Array.from(atob(data.base64), (char) => char.charCodeAt(0));
      const url = URL.createObjectURL(new Blob([bytes], { type: data.content_type }));
      const audio = card.querySelector("audio");
      audio.src = url;
      audio.play();
      card.querySelector(".play").hidden = true;
    };
    box.append(card);
  }
}

const render = renderReviewRows;

async function poll() {
  if (!currentTask) return;
  try {
    const data = await bridge.apiGet(`tasks/${currentTask}`);
    $("taskState").textContent = data.task.state;
    render(data.segments || []);
    if (["review", "failed", "cancelled", "ready"].includes(data.task.state)) {
      clearInterval(timer);
      if (data.task.error) {
        $("taskError").hidden = false;
        $("taskError").textContent = data.task.error;
      }
    }
  } catch (error) {
    $("taskError").hidden = false;
    $("taskError").textContent = errorText(error);
  }
}

$("taskForm").onsubmit = async (event) => {
  event.preventDefault();
  $("createTask").disabled = true;
  $("taskError").hidden = true;
  try {
    const route = `tasks/create/${encodeURIComponent($("providerSelect").value)}/${$("language").value}/authorized`;
    const data = await bridge.upload(route, $("materialInput").files[0]);
    currentTask = data.task.id;
    $("taskState").textContent = "queued";
    await poll();
    timer = setInterval(poll, 1500);
  } catch (error) {
    $("taskError").hidden = false;
    $("taskError").textContent = errorText(error);
    update();
  }
};

$("exportDataset").onclick = async () => {
  const segments = [...document.querySelectorAll(".segment")].map((card) => ({
    audio_name: card.dataset.name,
    text: card.querySelector("textarea").value,
    approved: card.querySelector(".approved").checked,
  }));
  try {
    const data = await bridge.apiPost(`tasks/${currentTask}/dataset`, { segments });
    const skipped = data.skipped_count ? `，自动跳过 ${data.skipped_count} 个低信息短片段` : "";
    $("datasetResult").textContent = `已生成 ${data.count} 个片段${skipped}：${data.dataset_dir}`;
    $("trainingPanel").hidden = false;
  } catch (error) {
    $("datasetResult").textContent = errorText(error);
  }
};

function trainingPercent(stage) {
  const stages = ["准备数据集", "文本预处理", "音频特征提取", "说话人向量", "语义提取", "训练 GPT 模型", "训练 SoVITS 模型", "训练完成"];
  const index = stages.findIndex((item) => stage.includes(item.replace("模型", "")) || item.includes(stage));
  return index < 0 ? 8 : Math.round((index + 1) * 100 / stages.length);
}

function formatEta(seconds) {
  if (!Number.isFinite(seconds)) return "预计剩余 --";
  const minutes = Math.ceil(seconds / 60);
  return minutes < 1 ? "预计不足 1 分钟" : `预计剩余 ${minutes} 分钟`;
}

async function pollTraining() {
  if (!currentVoice) return;
  try {
    const data = await bridge.apiGet(`gpt-sovits/voices/${currentVoice}`);
    const voice = data.voice || {};
    const progress = voice.training_progress || {};
    $("trainingState").textContent = voice.status === "ready" ? "训练完成" : voice.status === "failed" ? "训练失败" : "训练中";
    $("trainingDetail").textContent = voice.error_message || voice.training_stage || "正在准备训练";
    const percent = Number.isFinite(progress.percent) ? progress.percent : trainingPercent(voice.training_stage || "");
    $("trainingProgressBar").style.transform = `scaleX(${percent / 100})`;
    $("epochMetric").textContent = progress.epoch_total ? `Epoch ${progress.epoch}/${progress.epoch_total}` : "Epoch --";
    $("stepMetric").textContent = progress.step_total ? `Step ${progress.step}/${progress.step_total}` : "Step --";
    $("speedMetric").textContent = progress.steps_per_second ? `${progress.steps_per_second.toFixed(2)} step/s` : "-- step/s";
    $("etaMetric").textContent = formatEta(progress.eta_seconds);
    if (voice.status === "ready" || voice.status === "failed") {
      clearInterval(trainingTimer);
      $("startTraining").disabled = false;
    }
    if (voice.status === "ready") {
      const provider = data.provider || {};
      await loadVoices(voice.id);
      $("providerApi").textContent = provider.api_base_url || "";
      $("providerGpt").textContent = provider.gpt_model_path || "";
      $("providerSovits").textContent = provider.sovits_model_path || "";
      $("providerReference").value = provider.reference_audio_path || "";
      $("providerText").value = provider.reference_audio_text || "";
      $("providerLanguage").textContent = provider.reference_language || "zh";
      $("applyProvider").disabled = false;
      $("saveProviderReference").disabled = false;
    }
  } catch (error) {
    $("trainingState").textContent = "训练未开始";
    $("trainingDetail").textContent = errorText(error);
  }
}

$("startTraining").onclick = async () => {
  const name = $("voiceName").value.trim();
  if (!name) { $("trainingDetail").textContent = "请填写音色名称"; return; }
  $("startTraining").disabled = true;
  $("trainingState").textContent = "正在提交训练";
  try {
    const data = await bridge.apiPost(`tasks/${currentTask}/train`, { name, language: $("language").value, preset_id: $("trainingPreset").value, gpt_epochs: Number($("gptEpochs").value), sovits_epochs: Number($("sovitsEpochs").value) });
    currentVoice = data.voice.id;
    await pollTraining();
    trainingTimer = setInterval(pollTraining, 3000);
  } catch (error) {
    $("trainingDetail").textContent = errorText(error);
    $("startTraining").disabled = false;
  }
};

$("trainingPreset").onchange = () => {
  const values = presetValues[$("trainingPreset").value];
  if (values) { $("gptEpochs").value = values[0]; $("sovitsEpochs").value = values[1]; }
};
for (const id of ["gptEpochs", "sovitsEpochs"]) $(id).oninput = () => { $("trainingPreset").value = "custom"; };
$("providerVoice").onchange = (event) => selectVoice(event.target.value).catch((error) => { $("providerActionResult").textContent = errorText(error); });

$("refreshVoices").onclick = async () => {
  const button = $("refreshVoices");
  button.disabled = true;
  $("providerActionResult").textContent = "正在扫描音色目录...";
  try {
    await loadVoices(currentVoice);
    if ($("providerActionResult").textContent === "正在扫描音色目录...") {
      $("providerActionResult").textContent = "音色列表已刷新";
    }
  } catch (error) {
    $("providerActionResult").textContent = `刷新失败：${errorText(error)}`;
  } finally {
    button.disabled = false;
  }
};

$("saveProviderReference").onclick = async () => {
  if (!currentVoice) return;
  const result = $("referenceSaveResult");
  result.textContent = "正在保存...";
  $("saveProviderReference").disabled = true;
  try {
    const data = await bridge.apiPost(`gpt-sovits/voices/${currentVoice}/reference`, {
      reference_audio_path: $("providerReference").value.trim(),
      reference_text: $("providerText").value.trim(),
    });
    const provider = data.provider || {};
    $("providerReference").value = provider.reference_audio_path || "";
    $("providerText").value = provider.reference_audio_text || "";
    result.textContent = "参考信息已保存，请更新 Provider 使其生效";
  } catch (error) {
    result.textContent = `保存失败：${errorText(error)}`;
  } finally {
    $("saveProviderReference").disabled = false;
  }
};

$("applyProvider").onclick = async () => {
  if (!currentVoice) return;
  const result = $("providerActionResult");
  result.textContent = "正在应用 Provider...";
  $("applyProvider").disabled = true;
  try {
    const data = await bridge.apiPost(`gpt-sovits/voices/${currentVoice}/provider`, {});
    result.textContent = `已启用 ${data.provider_id}`;
  } catch (error) {
    result.textContent = `应用失败：${errorText(error)}`;
  } finally {
    $("applyProvider").disabled = false;
  }
};

$("openVoiceFolder").onclick = async () => {
  try { await bridge.apiPost("gpt-sovits/voices/open-folder", {}); }
  catch (error) { $("providerActionResult").textContent = `打开失败：${errorText(error)}`; }
};

$("synthesizeVoice").onclick = async () => {
  const text = $("synthesisText").value.trim();
  if (!text || !currentVoice) return;
  try {
    const data = await bridge.apiPost("gpt-sovits/synthesize", { voice_id: currentVoice, text });
    const bytes = Uint8Array.from(atob(data.base64), (char) => char.charCodeAt(0));
    const audio = $("synthesisAudio");
    audio.src = URL.createObjectURL(new Blob([bytes], { type: data.content_type }));
    audio.hidden = false;
    audio.play();
  } catch (error) { $("trainingDetail").textContent = errorText(error); }
};

async function initialize() {
  try {
    await bridge.ready();
  } catch (error) {
    showProviderError(error);
    showSeparatorError(error);
    return;
  }
  providers().catch(showProviderError);
  loadRemoteStatus().catch((error) => { $("remoteMessage").textContent = `远程状态读取失败：${errorText(error)}`; });
  loadRuntimeStatus();
  loadVoices().catch((error) => { $("providerActionResult").textContent = `音色读取失败：${errorText(error)}`; });
}

initialize();
