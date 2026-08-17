from __future__ import annotations

import asyncio
import io
import json
import os
import threading
import time
import uuid
import wave
from pathlib import Path


class RequestMonitorStore:
    def __init__(self, root: Path, audio_ttl_seconds=600, metadata_ttl_seconds=86400, max_records=100, clock=time.time):
        self.root = Path(root)
        self.audio_dir = self.root / "audio"
        self.path = self.root / "requests.json"
        self.audio_ttl_seconds = int(audio_ttl_seconds)
        self.metadata_ttl_seconds = int(metadata_ttl_seconds)
        self.max_records = int(max_records)
        self.clock = clock
        self._lock = threading.RLock()
        self._subscribers: set[asyncio.Queue] = set()
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        self.cleanup()

    def _read(self):
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return value if isinstance(value, list) else []
        except (OSError, ValueError):
            return []

    def _write(self, rows):
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)

    def _publish(self, row):
        event = self.public(row)
        for queue in tuple(self._subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def create_request(self, *, source, original_text, voice_id, requested_language, voice_name="", **_ignored):
        now = self.clock()
        row = {
            "id": uuid.uuid4().hex,
            "source": str(source),
            "original_text": str(original_text),
            "processed_text": "",
            "voice_id": str(voice_id),
            "voice_name": str(voice_name or voice_id),
            "requested_language": str(requested_language),
            "synthesis_language": "",
            "stage": "received",
            "created_at": now,
            "updated_at": now,
            "stage_started_at": now,
            "duration_ms": 0,
            "audio_bytes": 0,
            "audio_seconds": 0.0,
            "audio_path": "",
            "expires_at": None,
            "downloaded_at": None,
            "error": "",
        }
        with self._lock:
            rows = self._read()
            rows.append(row)
            self._write(self._prune(rows, now))
        self._publish(row)
        return dict(row)

    def update(self, request_id, stage, **changes):
        allowed = {"processed_text", "synthesis_language", "error", "voice_name"}
        with self._lock:
            rows = self._read()
            row = next((item for item in rows if item.get("id") == request_id), None)
            if row is None:
                raise KeyError(request_id)
            now = self.clock()
            row.update({key: changes[key] for key in allowed if key in changes})
            row["stage"] = str(stage)
            row["updated_at"] = now
            row["duration_ms"] = max(0, round((now - float(row["created_at"])) * 1000))
            self._write(rows)
        self._publish(row)
        return dict(row)

    def save_audio(self, request_id, payload: bytes):
        target = self.audio_dir / f"{request_id}.wav"
        temporary = target.with_suffix(".wav.tmp")
        temporary.write_bytes(payload)
        os.replace(temporary, target)
        try:
            with wave.open(io.BytesIO(payload), "rb") as source:
                seconds = source.getnframes() / source.getframerate()
        except (wave.Error, EOFError, ZeroDivisionError):
            seconds = 0.0
        with self._lock:
            rows = self._read()
            row = next(item for item in rows if item.get("id") == request_id)
            now = self.clock()
            row.update({"stage": "generated", "updated_at": now, "duration_ms": round((now - row["created_at"]) * 1000), "audio_bytes": len(payload), "audio_seconds": seconds, "audio_path": str(target), "expires_at": now + self.audio_ttl_seconds})
            self._write(rows)
        self._publish(row)
        return dict(row)

    def mark_downloaded(self, request_id):
        with self._lock:
            rows = self._read()
            row = next(item for item in rows if item.get("id") == request_id)
            now = self.clock()
            row.update({"stage": "downloaded", "updated_at": now, "downloaded_at": now})
            self._write(rows)
        self._publish(row)
        return dict(row)

    def get(self, request_id):
        self.cleanup()
        row = next((item for item in self._read() if item.get("id") == request_id), None)
        return dict(row) if row else None

    def snapshot(self, limit=100):
        self.cleanup()
        rows = sorted(self._read(), key=lambda item: item.get("created_at", 0), reverse=True)
        return [self.public(row) for row in rows[: max(1, min(int(limit), self.max_records))]]

    def audio_path(self, request_id):
        row = self.get(request_id)
        path = Path(row["audio_path"]) if row and row.get("audio_path") else None
        return path if path and path.is_file() else None

    def public(self, row):
        result = {key: value for key, value in row.items() if key != "audio_path"}
        result["audio_available"] = bool(row.get("audio_path") and Path(row["audio_path"]).is_file())
        return result

    def _prune(self, rows, now):
        fresh = [row for row in rows if now - float(row.get("created_at", now)) <= self.metadata_ttl_seconds]
        return sorted(fresh, key=lambda row: row.get("created_at", 0))[-self.max_records :]

    def cleanup(self):
        with self._lock:
            now = self.clock()
            rows = self._read()
            changed = False
            for row in rows:
                expires = row.get("expires_at")
                if expires and now >= float(expires) and row.get("audio_path"):
                    Path(row["audio_path"]).unlink(missing_ok=True)
                    row["audio_path"] = ""
                    row["stage"] = "expired"
                    row["updated_at"] = now
                    changed = True
            pruned = self._prune(rows, now)
            if changed or len(pruned) != len(rows):
                self._write(pruned)
            return {"records": len(pruned), "changed": changed}

    def subscribe(self):
        queue = asyncio.Queue(maxsize=50)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue):
        self._subscribers.discard(queue)

    def clear(self):
        with self._lock:
            for path in self.audio_dir.glob("*.wav"):
                path.unlink(missing_ok=True)
            self._write([])
