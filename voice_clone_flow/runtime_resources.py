from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import string
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .platform_runtime import PlatformProfile, detect_platform_profile
from .linux_tools import LinuxToolInstaller

# 下载源使用 BilibiliDown 类似的模板：{os}、{arch}、{exeSuffix}。
# 当前默认 Windows 使用 Gyan Essentials；Linux 使用 John Van Sickle 静态构建。
FFMPEG_WINDOWS_URLS = (
    "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
    "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-win64-gpl.zip",
)
FFMPEG_LINUX_URL_TEMPLATES = (
    "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-{arch}-static.tar.xz",
)
FFMPEG_URL = FFMPEG_WINDOWS_URLS[0]
# 国内可访问的 GitHub Releases 加速镜像，仅用于 GitHub 候选源。
GITHUB_MIRROR_PREFIXES = (
    "https://ghfast.top/",
    "https://gh-proxy.com/",
)
FFMPEG_DOWNLOAD_TIMEOUT_SECONDS = 60
FFMPEG_PROBE_BYTES = 512 * 1024
FFMPEG_PROBE_TIMEOUT_SECONDS = 10
FFMPEG_PROBE_CACHE_TTL_SECONDS = 300
SHA256_RE = re.compile(r"\b[a-fA-F0-9]{64}\b")


class FFmpegSourceError(RuntimeError):
    """A candidate archive failed integrity or format validation."""


class FFmpegResourceManager:
    def __init__(
        self,
        data_dir: Path,
        configured_path: str = "",
        download_url: str | None = None,
        which=shutil.which,
        common_paths: tuple[Path, ...] | None = None,
        platform_profile: PlatformProfile | None = None,
        *,
        configured_sha256: str = "",
    ) -> None:
        self.platform_profile = platform_profile or detect_platform_profile(which=which)
        self._profile_injected = platform_profile is not None
        self.root = Path(data_dir) / "runtime" / "ffmpeg"
        suffix = ".exe" if self.platform_profile.system == "windows" else ""
        self.managed_ffmpeg = self.root / f"ffmpeg{suffix}"
        self.managed_ffprobe = self.root / f"ffprobe{suffix}"
        self.configured_path = configured_path
        self.download_url = download_url.strip() if download_url else ""
        self._custom_download_url = bool(self.download_url)
        self.configured_sha256 = configured_sha256.strip().lower()
        self.which = which
        self.part_file = self.root.parent / "ffmpeg.archive.part"
        self.part_meta_file = self.root.parent / "ffmpeg.archive.part.meta"
        self.common_paths = common_paths or self._default_common_paths()
        self.installing = False
        self.probing = False
        self.error = ""
        self.verification = "unverified"
        self.downloaded_bytes = 0
        self.total_bytes = 0
        self._lock = threading.Lock()
        self._speed_cache: dict[str, tuple[float, float]] = {}
        self._active_expected_sha256 = ""

    def resolve(self) -> Path | None:
        if self.configured_path and Path(self.configured_path).is_file():
            return Path(self.configured_path).resolve()
        if self.managed_ffmpeg.is_file():
            return self.managed_ffmpeg.resolve()
        found = self.which("ffmpeg")
        if found:
            return Path(found)
        if self.platform_profile.system == "windows":
            found = self._where_windows_ffmpeg()
            if found:
                return found
        common = next(
            (path.resolve() for path in self.common_paths if path.is_file()), None
        )
        return common or (
            self._detect_portable_windows_ffmpeg()
            if self.platform_profile.system == "windows"
            else None
        )

    def _default_common_paths(self) -> tuple[Path, ...]:
        roots = {
            Path.cwd(),
            Path(sys.executable).resolve().parent,
            Path(__file__).resolve().parents[2],
        }
        if self.platform_profile.system == "windows":
            local = Path(os.environ.get("LOCALAPPDATA", ""))
            program_files = Path(os.environ.get("ProgramFiles", ""))
            roots.update(
                {
                    local / "Microsoft" / "WinGet" / "Links",
                    program_files / "ffmpeg" / "bin",
                    Path("C:/ffmpeg/bin"),
                }
            )
        candidates: list[Path] = []
        for root in roots:
            candidates.extend(
                (
                    root / "ffmpeg.exe" if self.platform_profile.system == "windows" else root / "ffmpeg",
                    root / "ffmpeg" / "bin" / ("ffmpeg.exe" if self.platform_profile.system == "windows" else "ffmpeg"),
                    root / "tools" / "ffmpeg" / "bin" / ("ffmpeg.exe" if self.platform_profile.system == "windows" else "ffmpeg"),
                )
            )
        return tuple(dict.fromkeys(candidates))

    def _detect_portable_windows_ffmpeg(self) -> Path | None:
        patterns = (
            "ffmpeg.exe",
            "ffmpeg/bin/ffmpeg.exe",
            "tools/ffmpeg/bin/ffmpeg.exe",
        )
        for letter in string.ascii_uppercase:
            drive = Path(f"{letter}:/")
            if not drive.exists():
                continue
            for pattern in patterns:
                candidate = drive / pattern
                if candidate.is_file():
                    return candidate.resolve()
        return None

    def _where_windows_ffmpeg(self) -> Path | None:
        """Resolve FFmpeg from the Windows system search path as seen by cmd.exe."""
        try:
            result = subprocess.run(
                ["where.exe", "ffmpeg.exe"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        for line in result.stdout.splitlines():
            candidate = Path(line.strip().strip('"'))
            if candidate.is_file():
                return candidate.resolve()
        return None

    def status(self) -> dict[str, object]:
        path = self.resolve()
        source = "missing"
        if path:
            source = (
                "configured"
                if self.configured_path and Path(self.configured_path).is_file()
                else "managed"
                if self.managed_ffmpeg.is_file()
                and path == self.managed_ffmpeg.resolve()
                else "system"
            )
        display_path = path or (
            self.managed_ffmpeg
            if self.platform_profile.system == "windows"
            else "ffmpeg"
        )
        downloaded = self.downloaded_bytes
        total = self.total_bytes
        if (
            self.platform_profile.system == "windows"
            and self.part_file.is_file()
            and not self.installing
        ):
            downloaded = max(downloaded, self.part_file.stat().st_size)
            meta = self._read_part_meta()
            if meta and meta.get("total"):
                total = max(total, int(meta["total"]))
        return {
            "ready": bool(path),
            "installing": self.installing,
            "probing": self.probing,
            "error": self.error,
            "platform": self.platform_profile.system,
            "can_install": self.platform_profile.can_download_managed_ffmpeg,
            "install_hint": self.platform_profile.install_hints.get("ffmpeg", ""),
            "resolved_path": str(display_path),
            "managed_path": str(self.managed_ffmpeg),
            "verification": self.verification,
            "download_templates": list(
                FFMPEG_WINDOWS_URLS
                if self.platform_profile.system == "windows"
                else FFMPEG_LINUX_URL_TEMPLATES
            ),
            "manual_download_urls": [
                {"name": "FFmpeg 下载源", "url": url}
                for url in self._source_candidates()
            ] if self.platform_profile.system in {"windows", "linux"} else [],
            "source": source,
            "downloaded_bytes": downloaded,
            "total_bytes": total,
            "progress_percent": round(downloaded * 100 / total)
            if total
            else 0,
        }

    def start_install(self) -> bool:
        if not self._profile_injected:
            self.platform_profile = detect_platform_profile(which=self.which)
        if not self.platform_profile.can_download_managed_ffmpeg:
            raise RuntimeError(
                f"当前系统或架构不支持自动下载 FFmpeg："
                f"{self.platform_profile.system}/{self.platform_profile.architecture}"
            )
        if self.platform_profile.system == "linux":
            if self.resolve():
                return False
        with self._lock:
            if self.installing:
                return False
            self.installing, self.error = True, ""
        threading.Thread(
            target=self._install, daemon=True, name="voice-ffmpeg-install"
        ).start()
        return True

    def _install(self) -> None:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            try:
                self._download_loop()
            except Exception as exc:
                # 下载中断：保留断点文件，下次安装继续续传
                self.error = str(exc)
                return
            try:
                if self.platform_profile.system == "windows":
                    self._extract_windows()
                else:
                    LinuxToolInstaller(self.root).install_ffmpeg_archive(self.part_file)
            except Exception as exc:
                self.part_file.unlink(missing_ok=True)
                self.part_meta_file.unlink(missing_ok=True)
                self.error = f"下载包损坏，已清除断点缓存：{exc}"
                return
            self.part_file.unlink(missing_ok=True)
            self.part_meta_file.unlink(missing_ok=True)
        finally:
            self.installing = False

    def _download_loop(self) -> None:
        """并行测速后按实测吞吐排序尝试各下载源，第一个成功的源胜出。"""
        defaults = self._source_candidates()
        configured = (self._expand_url(self.download_url),) if self._custom_download_url else ()
        candidates = tuple(dict.fromkeys((*configured, *defaults)))
        if configured:
            ordered_defaults = self._rank_sources(tuple(u for u in candidates if u not in configured))
            ordered = [*configured, *ordered_defaults]
        else:
            ordered = self._rank_sources(candidates)
        last_error: Exception | None = None
        for index, source in enumerate(ordered, start=1):
            try:
                self._download_windows(source)
                self._verify_archive(source)
                last_error = None
                self.error = ""
                break
            except (TimeoutError, OSError, urllib.error.HTTPError, FFmpegSourceError) as exc:
                last_error = exc
                if index < len(ordered):
                    self.error = f"FFmpeg 下载源 {index} 不可用，正在切换下一个源"
        if last_error is not None:
            raise RuntimeError(f"所有 FFmpeg 下载源均不可用：{last_error}") from last_error

    def _source_candidates(self) -> tuple[str, ...]:
        if self.platform_profile.system == "linux":
            templates = FFMPEG_LINUX_URL_TEMPLATES
        else:
            templates = FFMPEG_WINDOWS_URLS
            templates += tuple(
                prefix + FFMPEG_WINDOWS_URLS[1] for prefix in GITHUB_MIRROR_PREFIXES
            )
        return tuple(dict.fromkeys(self._expand_url(url) for url in templates))

    def _expand_url(self, template: str) -> str:
        system = "win" if self.platform_profile.system == "windows" else "linux"
        architecture = "amd64" if self.platform_profile.architecture == "x86_64" else "arm64"
        suffix = self.platform_profile.executable_suffix
        return template.replace("{os}", system).replace("{arch}", architecture).replace(
            "{exeSuffix}", suffix
        )

    def _expected_sha256(self, source: str) -> str:
        configured_source = (
            self._expand_url(self.download_url) if self._custom_download_url else ""
        )
        if (
            self.configured_sha256
            and source == configured_source
            and SHA256_RE.fullmatch(self.configured_sha256)
        ):
            return self.configured_sha256
        meta = self._read_part_meta()
        if meta and meta.get("url") == source and SHA256_RE.fullmatch(str(meta.get("sha256", ""))):
            return str(meta["sha256"]).lower()
        try:
            request = urllib.request.Request(
                source + ".sha256", headers={"User-Agent": "VoiceCloneFlow/0.2"}
            )
            with urllib.request.urlopen(request, timeout=20) as response:
                match = SHA256_RE.search(response.read(4096).decode("ascii", errors="ignore"))
                return match.group(0).lower() if match else ""
        except (OSError, TimeoutError, urllib.error.HTTPError):
            return ""

    def _verify_archive(self, source: str) -> None:
        expected = self._expected_sha256(source)
        self._active_expected_sha256 = expected
        if not expected:
            self.verification = "unverified"
            return
        digest = hashlib.sha256()
        with self.part_file.open("rb") as archive:
            while block := archive.read(1024 * 1024):
                digest.update(block)
        actual = digest.hexdigest()
        if actual != expected:
            self.verification = "failed"
            raise FFmpegSourceError(
                f"FFmpeg 下载包 SHA-256 校验失败：期望 {expected}，实际 {actual}"
            )
        meta = self._read_part_meta() or {}
        self._write_part_meta(
            source,
            str(meta.get("etag", "")),
            self.downloaded_bytes,
            actual,
        )
        self.verification = "verified"

    def _rank_sources(self, urls: tuple[str, ...]) -> list[str]:
        """并行探测各候选源的实测下载吞吐，按速度从快到慢排序。"""
        if len(urls) <= 1:
            return list(urls)
        self.probing = True
        try:
            with ThreadPoolExecutor(max_workers=min(len(urls), 6)) as pool:
                futures = {pool.submit(self._probe_speed, url): url for url in urls}
                results: list[tuple[float, str]] = []
                for future, url in futures.items():
                    try:
                        speed = future.result(timeout=FFMPEG_PROBE_TIMEOUT_SECONDS + 5)
                    except Exception:
                        speed = 0.0
                    results.append((speed, url))
        finally:
            self.probing = False
        results.sort(key=lambda item: item[0], reverse=True)
        return [url for _speed, url in results]

    def _probe_speed(self, url: str) -> float:
        """测量单个候选源下载前 512KB 的实测吞吐（bytes/sec），带进程内缓存。"""
        now = time.monotonic()
        cached = self._speed_cache.get(url)
        if cached and now - cached[1] < FFMPEG_PROBE_CACHE_TTL_SECONDS:
            return cached[0]
        speed = 0.0
        try:
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "VoiceCloneFlow/0.2",
                    "Accept": "*/*",
                    "Range": f"bytes=0-{FFMPEG_PROBE_BYTES - 1}",
                },
            )
            started = time.monotonic()
            received = 0
            with urllib.request.urlopen(
                request, timeout=FFMPEG_PROBE_TIMEOUT_SECONDS
            ) as response:
                while received < FFMPEG_PROBE_BYTES:
                    block = response.read(64 * 1024)
                    if not block:
                        break
                    received += len(block)
            elapsed = time.monotonic() - started
            if received and elapsed > 0:
                speed = received / elapsed
        except Exception:
            speed = 0.0
        self._speed_cache[url] = (speed, time.monotonic())
        return speed

    def _download_windows(self, url: str) -> None:
        """下载单个平台归档，支持基于 Range 的断点续传。

        断点仅在源地址一致时复用（不同源内容不同，不得混用）。
        ETag/Last-Modified 与断点大小记录在 meta 文件中，用于识别上游更新。
        """
        prior = self._read_part_meta()
        existing = self.part_file.stat().st_size if self.part_file.is_file() else 0
        resumable = bool(existing) and prior is not None and prior.get("url") == url
        headers = {"User-Agent": "VoiceCloneFlow/0.2", "Accept": "*/*"}
        if resumable:
            headers["Range"] = f"bytes={existing}-"
            if prior.get("etag"):
                # 上游文件更新（如 BtbN master 滚动构建）时，服务器返回 200 全量，
                # 避免续传产生新旧混合的损坏文件。
                headers["If-Range"] = prior["etag"]
        request = urllib.request.Request(url, headers=headers)
        self.part_file.parent.mkdir(parents=True, exist_ok=True)
        etag = ""
        try:
            with urllib.request.urlopen(
                request, timeout=FFMPEG_DOWNLOAD_TIMEOUT_SECONDS
            ) as response:
                status = getattr(response, "status", 200)
                etag = (
                    response.headers.get("ETag")
                    or response.headers.get("Last-Modified")
                    or ""
                )
                if status == 206 and resumable:
                    total = existing + int(response.headers.get("Content-Length", 0))
                    mode = "ab"
                else:
                    # 服务器不支持 Range 或断点已失效：从头下载
                    existing = 0
                    total = int(response.headers.get("Content-Length", 0))
                    mode = "wb"
                self.total_bytes = total
                self.downloaded_bytes = existing
                with self.part_file.open(mode) as target:
                    while block := response.read(1024 * 1024):
                        target.write(block)
                        self.downloaded_bytes += len(block)
        except urllib.error.HTTPError as exc:
            if exc.code == 416 and existing:
                # 断点超出资源范围：服务器端文件已变化，清除断点后全量重下
                self.part_file.unlink(missing_ok=True)
                self.part_meta_file.unlink(missing_ok=True)
                return self._download_windows(url)
            raise
        if etag:
            self._write_part_meta(url, etag, self.downloaded_bytes)

    def _extract_windows(self) -> None:
        with zipfile.ZipFile(self.part_file) as package:
            for wanted, target in (
                ("ffmpeg.exe", self.managed_ffmpeg),
                ("ffprobe.exe", self.managed_ffprobe),
            ):
                member = next(
                    (
                        n
                        for n in package.namelist()
                        if n.lower().endswith("/bin/" + wanted)
                    ),
                    None,
                )
                if not member:
                    raise RuntimeError(f"压缩包中缺少 {wanted}")
                with package.open(member) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)

    def _read_part_meta(self) -> dict | None:
        try:
            if not self.part_meta_file.is_file():
                return None
            data = json.loads(
                self.part_meta_file.read_text(encoding="utf-8")
            )
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None

    def _write_part_meta(
        self, url: str, etag: str, total: int, sha256: str = ""
    ) -> None:
        try:
            self.part_meta_file.write_text(
                json.dumps(
                    {"url": url, "etag": etag, "total": total, "sha256": sha256}
                ),
                encoding="utf-8",
            )
        except OSError:
            pass

    def _update_download_progress(self, downloaded: int, total: int) -> None:
        self.downloaded_bytes = downloaded
        self.total_bytes = total

    def delete_managed(self) -> bool:
        removed = False
        for path in (self.managed_ffmpeg, self.managed_ffprobe):
            if path.is_file():
                path.unlink()
                removed = True
        return removed
