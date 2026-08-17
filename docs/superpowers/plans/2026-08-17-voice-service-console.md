# Voice Service Console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make VoiceClone Studio a clear, safe local control center with one voice-service switch, live process state, conflict-proof FRPC lifecycle, and a visual request pipeline.

**Architecture:** Keep Studio API and existing workflow endpoints. Add a small lifecycle facade that starts/stops GPT-SoVITS and the Studio-owned FRPC in one guarded operation, while the browser polls a consolidated status object and subscribes to existing request-monitor events. FRPC remains a child process owned by Studio and is never replaced by an unverified process.

**Tech Stack:** FastAPI, Python, vanilla HTML/CSS/JavaScript, existing request monitor and FRPC manager.

## Global Constraints

- Do not modify AstrBot source or the AstrBot plugin.
- GPT-SoVITS must remain manually enabled; Studio startup alone must not launch it.
- Closing the voice-service switch stops GPT-SoVITS and the Studio-owned FRPC, while the Studio web page remains available.
- Tokens are never returned by status endpoints or shown in logs.
- Existing local voice, training, provider, and monitoring workflows remain available.

### Task 1: Guard FRPC ownership and expose lifecycle state

**Files:**
- Modify: `E:/VCS/studio_core/frpc.py`
- Test: `E:/VCS/tests/test_frpc.py`

Add tests for refusing a second Studio-owned launch when the saved PID is alive, reporting `conflict` separately from `connected`, and clearing stale state after a process exit. Implement a single-instance lock/state record and status fields `conflict`, `phase`, and `message`; do not kill processes that cannot be proven to use this Studio config.

### Task 2: Add a guarded voice-service lifecycle API

**Files:**
- Modify: `E:/VCS/app.py`
- Modify: `E:/VCS/voice_clone_flow/services/studio.py`
- Test: `E:/VCS/tests/test_runtime_api.py`

Add `GET /api/voice-service/status`, `POST /api/voice-service/start`, and `POST /api/voice-service/stop`. Start validates local Studio, requires an installed GPT runtime, starts GPT only when explicitly requested by this endpoint, then prepares and starts the owned FRPC. Stop terminates GPT and owned FRPC but leaves port 9090 serving the UI. Return per-stage states for `gpt`, `frp`, and `astrbot` with actionable Chinese messages.

### Task 3: Build the visual request pipeline

**Files:**
- Modify: `E:/VCS/pages/studio-v2.html`
- Modify: `E:/VCS/pages/voice-clone/style.css` if shared styles are used

Add a live pipeline card with five fixed stages: `文本已接收`, `翻译处理中`, `推理处理中`, `音频已生成`, `已发送/可下载`. Each request row shows elapsed time, current stage, voice, language, processed text, byte progress, and audio playback when available. Use semantic colors, a moving active indicator, and clear failure recovery actions. Poll status every 2 seconds and use the existing monitor SSE for immediate updates.

### Task 4: Rework the connection and service controls

**Files:**
- Modify: `E:/VCS/pages/studio-v2.html`

Replace the current multi-button-first layout with one primary voice-service switch and an expandable advanced section. Show the exact AstrBot plugin values: remote mode, Studio URL, Studio Token, and the server-side mapped address. Keep manual FRPC/GPT buttons under advanced controls. Explain that FRPC is outbound, the mapping port should remain server-local, and the two tokens have different purposes.

### Task 5: Verify the desktop workflow

**Files:**
- Test: `E:/VCS/tests/test_frpc.py`, `E:/VCS/tests/test_runtime_api.py`, `E:/VCS/tests/test_tts_api.py`

Run focused Python tests, compile checks, the UI detector, and a live smoke test: open Studio, confirm the switch is off, start voice service, verify exactly one FRPC process and `connected=true`, issue a short TTS request, observe all five pipeline stages, then stop the service and verify GPT/FRPC exit while port 9090 remains available.
