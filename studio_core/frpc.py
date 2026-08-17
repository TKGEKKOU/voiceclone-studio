from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import urllib.request
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator


FRP_VERSION = "0.69.1"
FRP_URLS = (
    f"https://github.com/fatedier/frp/releases/download/v{FRP_VERSION}/frp_{FRP_VERSION}_windows_amd64.zip",
    f"https://ghproxy.net/https://github.com/fatedier/frp/releases/download/v{FRP_VERSION}/frp_{FRP_VERSION}_windows_amd64.zip",
)


def _toml_string(value: object) -> str:
    return json.dumps(str(value), ensure_ascii=False)


class FrpcManager:
    def __init__(self, data_dir: Path) -> None:
        self.root = Path(data_dir) / "frp"
        self.binary = self.root / "frpc.exe"
        self.config_path = self.root / "frpc.toml"
        self.archive = self.root / f"frp-{FRP_VERSION}.zip"
        self.part = self.archive.with_suffix(".zip.part")
        self.log_path = self.root / "frpc.log"
        self.state_path = self.root / "state.json"
        self._process: subprocess.Popen | None = None
        self._installing = False
        self._downloaded_bytes = 0
        self._total_bytes = 0
        self._error = ""
        self._lock = threading.Lock()

    def build_config(self, config: dict) -> str:
        return "\n".join(
            [
                f"serverAddr = {_toml_string(config['frp_server_addr'])}",
                f"serverPort = {int(config['frp_server_port'])}",
                'auth.method = "token"',
                f"auth.token = {_toml_string(config['frp_token'])}",
                "loginFailExit = false",
                "",
                "[[proxies]]",
                f"name = {_toml_string(config['frp_proxy_name'])}",
                'type = "tcp"',
                'localIP = "127.0.0.1"',
                f"localPort = {int(config['frp_local_port'])}",
                f"remotePort = {int(config['frp_remote_port'])}",
                "",
            ]
        )

    def prepare(self, config: dict) -> dict:
        if not str(config.get("frp_token", "")).strip():
            raise ValueError("请填写与服务器一致的 FRP Token")
        self.root.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(self.build_config(config), encoding="utf-8")
        if not self.binary.is_file() and not self._installing:
            self._installing = True
            threading.Thread(target=self._download, daemon=True, name="voiceclone-frpc-download").start()
        return self.status()

    def _download(self) -> None:
        last_error: Exception | None = None
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            for url in FRP_URLS:
                try:
                    request = urllib.request.Request(url, headers={"User-Agent": "VoiceCloneStudio/0.2"})
                    with urllib.request.urlopen(request, timeout=120) as response:
                        self._total_bytes = int(response.headers.get("Content-Length", 0))
                        self._downloaded_bytes = 0
                        with self.part.open("wb") as target:
                            while block := response.read(1024 * 1024):
                                target.write(block)
                                self._downloaded_bytes += len(block)
                    os.replace(self.part, self.archive)
                    self._extract()
                    self._error = ""
                    return
                except (OSError, ValueError) as exc:
                    last_error = exc
            raise RuntimeError(f"FRPC 下载失败：{last_error}")
        except Exception as exc:
            self._error = str(exc)
        finally:
            self._installing = False

    def _extract(self) -> None:
        with zipfile.ZipFile(self.archive) as package:
            member = next(
                (name for name in package.namelist() if Path(name).name.lower() == "frpc.exe"),
                None,
            )
            if not member:
                raise RuntimeError("FRP 压缩包中缺少 frpc.exe")
            with package.open(member) as source, self.binary.open("wb") as target:
                while block := source.read(1024 * 1024):
                    target.write(block)

    def start(self) -> dict:
        with self._lock:
            if self.is_running():
                return self.status()
            if not self.binary.is_file():
                raise RuntimeError("FRPC 尚未下载完成")
            if not self.config_path.is_file():
                raise RuntimeError("FRPC 尚未配置")
            self.root.mkdir(parents=True, exist_ok=True)
            log = self.log_path.open("a", encoding="utf-8", errors="replace")
            startup = getattr(subprocess, "STARTUPINFO", None)
            startupinfo = startup() if startup else None
            if startupinfo is not None:
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            self._process = subprocess.Popen(
                [str(self.binary), "-c", str(self.config_path)],
                cwd=str(self.root),
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                startupinfo=startupinfo,
            )
            self._write_state(self._process.pid)
        return self.status()

    def stop(self) -> dict:
        pid = self._owned_pid()
        if pid:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                timeout=15,
                check=False,
            )
        self._process = None
        try:
            self.state_path.unlink(missing_ok=True)
        except OSError as exc:
            # A stale or temporarily locked state file must not turn a normal
            # Studio shutdown into an application failure.
            self._error = f"FRPC 已停止，但状态文件暂时无法清理：{exc}"
        return self.status()

    def _write_state(self, pid: int) -> None:
        self.state_path.write_text(json.dumps({"pid": int(pid)}), encoding="utf-8")

    def _saved_pid(self) -> int | None:
        try:
            return int(json.loads(self.state_path.read_text(encoding="utf-8"))["pid"])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _owned_pid(self) -> int | None:
        pid = self._process.pid if self._process and self._process.poll() is None else self._saved_pid()
        if not pid:
            return None
        command = [
            "powershell.exe", "-NoProfile", "-Command",
            f"(Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\").CommandLine",
        ]
        try:
            line = subprocess.run(command, capture_output=True, text=True, timeout=5).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None
        expected_binary = str(self.binary).lower()
        expected_config = str(self.config_path).lower()
        return pid if expected_binary in line.lower() and expected_config in line.lower() else None

    def is_running(self) -> bool:
        return self._owned_pid() is not None

    @staticmethod
    def _local_port_open(port: int) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            return False

    def status(self) -> dict:
        log_tail = ""
        try:
            log_tail = "\n".join(self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-30:])
        except OSError:
            pass
        remote_port = 0
        local_port = 0
        if self.config_path.is_file():
            text = self.config_path.read_text(encoding="utf-8", errors="replace")
            for line in text.splitlines():
                key, _, value = line.partition("=")
                if key.strip() == "remotePort":
                    remote_port = int(value.strip())
                elif key.strip() == "localPort":
                    local_port = int(value.strip())
        running = self.is_running()
        normalized_log = log_tail.lower()
        last_success = normalized_log.rfind("start proxy success")
        last_conflict = normalized_log.rfind("proxy [") if "already exists" in normalized_log else -1
        conflict = running and last_conflict > last_success
        connected = running and last_success >= 0 and not conflict
        if conflict:
            phase = "conflict"
            message = "服务器已有同名 FRPC 隧道，请先停止旧实例后再启动。"
        elif connected:
            phase = "connected"
            message = "FRPC 已连接，服务器映射可用。"
        elif running:
            phase = "connecting"
            message = "FRPC 已启动，正在等待服务器确认。"
        else:
            phase = "stopped"
            message = "FRPC 未启动。"
        return {
            "version": FRP_VERSION,
            "installed": self.binary.is_file(),
            "installing": self._installing,
            "configured": self.config_path.is_file(),
            "running": running,
            "connected": connected,
            "conflict": conflict,
            "phase": phase,
            "message": message,
            "pid": self._owned_pid(),
            "local_port": local_port,
            "local_service_ready": self._local_port_open(local_port) if local_port else False,
            "remote_port": remote_port,
            "downloaded_bytes": self._downloaded_bytes,
            "total_bytes": self._total_bytes,
            "error": self._error,
            "binary_path": str(self.binary),
            "config_path": str(self.config_path),
            "log_path": str(self.log_path),
            "log_tail": log_tail,
        }


@asynccontextmanager
async def managed_frpc_lifespan(
    manager: FrpcManager,
    *,
    stop_on_exit: bool = True,
) -> AsyncIterator[None]:
    """Stop the Studio-owned FRPC process during graceful app shutdown."""

    try:
        yield
    finally:
        if stop_on_exit:
            manager.stop()
