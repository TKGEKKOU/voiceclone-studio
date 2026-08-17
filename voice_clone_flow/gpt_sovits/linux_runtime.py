from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
from os import environ
from pathlib import Path
from typing import Callable

from ..linux_tools import LinuxToolInstaller, uv_download_url
from ..platform_runtime import detect_platform_profile


class LinuxPythonEnvironment:
    def __init__(
        self,
        install_dir: Path,
        *,
        current_python: str | None = None,
        uv_path: str | None = None,
        runner: Callable = subprocess.run,
        progress: Callable[[str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> None:
        self.install_dir = Path(install_dir)
        self.venv_dir = self.install_dir / ".venv"
        self.python_path = self.venv_dir / "bin" / "python"
        self.current_python = current_python or sys.executable
        self.uv_path = uv_path if uv_path is not None else self._find_uv()
        self.runner = runner
        self.progress = progress
        self.cancelled = cancelled or (lambda: False)

    @staticmethod
    def _find_uv() -> str | None:
        discovered = shutil.which("uv")
        if discovered:
            return discovered
        home = Path(environ.get("HOME", "")).expanduser()
        candidates = (home / ".local" / "bin" / "uv", Path("/root/.local/bin/uv"))
        return next((str(path) for path in candidates if path.is_file()), None)

    def ensure(self) -> Path:
        if self.python_path.is_file():
            self._ensure_pip()
            return self.python_path
        self.install_dir.mkdir(parents=True, exist_ok=True)
        command = [self.current_python, "-m", "venv", str(self.venv_dir)]
        try:
            self.runner(command, check=True, capture_output=True, text=True)
        except (OSError, subprocess.CalledProcessError):
            self.uv_path = self.uv_path or self._install_managed_uv()
            self._notify("下载 Python 3.11")
            self._run_checked([self.uv_path, "python", "install", "3.11"])
            self._notify("创建 GPT-SoVITS Python 环境")
            self._run_checked(
                [
                    self.uv_path,
                    "venv",
                    "--clear",
                    "--seed",
                    "--python",
                    "3.11",
                    str(self.venv_dir),
                ]
            )
        if not self.python_path.is_file():
            raise RuntimeError(f"Python 环境创建完成但未找到：{self.python_path}")
        self._ensure_pip()
        return self.python_path

    def _ensure_pip(self) -> None:
        check = [str(self.python_path), "-m", "pip", "--version"]
        try:
            self.runner(check, check=True, capture_output=True, text=True)
            return
        except (OSError, subprocess.CalledProcessError):
            self.uv_path = self.uv_path or self._install_managed_uv()
            self._notify("虚拟环境缺少 pip，正在自动补全")
            self._run_checked(
                [
                    self.uv_path,
                    "pip",
                    "install",
                    "--python",
                    str(self.python_path),
                    "pip",
                ]
            )
        try:
            self.runner(check, check=True, capture_output=True, text=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            detail = getattr(exc, "stderr", "") or str(exc)
            raise RuntimeError(f"pip 自动补全后仍不可用：{detail}") from exc

    def _install_managed_uv(self) -> str:
        profile = detect_platform_profile()
        tools_dir = self.install_dir.parent / "tools" / "uv"
        archive = self.install_dir.parent / "downloads" / "uv-linux.tar.gz"
        self._notify("下载 uv 运行工具")
        installer = LinuxToolInstaller(tools_dir)
        installer.download(uv_download_url(profile.architecture), archive)
        uv, _ = installer.install_uv_archive(archive)
        archive.unlink(missing_ok=True)
        return str(uv)

    def _run_checked(self, command: list[str]) -> None:
        try:
            self.runner(command, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or str(exc)).strip()
            raise RuntimeError(f"命令执行失败：{' '.join(command)}\n{detail}") from exc

    def _notify(self, message: str) -> None:
        if self.progress:
            self.progress(message)

    def install_requirements(self, requirements: Path) -> None:
        python = self.ensure()
        if not requirements.is_file():
            raise RuntimeError(f"GPT-SoVITS 源码缺少依赖文件：{requirements}")
        self.run_streamed(
            [str(python), "-m", "pip", "install", "-r", str(requirements)],
            cwd=str(self.install_dir),
        )

    def run_streamed(self, command: list[str], cwd: Path | str | None = None) -> None:
        """Run a long command while exposing its latest output and supporting cancel."""

        options = {"start_new_session": True} if sys.platform != "win32" else {}
        process = subprocess.Popen(
            command,
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            **options,
        )
        output: list[str] = []
        assert process.stdout is not None
        try:
            for raw_line in process.stdout:
                line = raw_line.strip()
                if line:
                    output.append(line)
                    output = output[-20:]
                    self._notify(line)
                if self.cancelled():
                    self._terminate_process(process)
                    raise RuntimeError("安装已取消")
            returncode = process.wait()
        finally:
            if process.poll() is None:
                self._terminate_process(process)
        if returncode:
            detail = "\n".join(output[-12:]) or f"退出码 {returncode}"
            raise RuntimeError(f"命令执行失败：{' '.join(command)}\n{detail}")

    @staticmethod
    def _terminate_process(process: subprocess.Popen) -> None:
        try:
            if sys.platform != "win32":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
            process.wait(timeout=8)
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
