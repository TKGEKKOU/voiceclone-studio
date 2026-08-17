import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app import create_app
from studio_core.config import StudioConfigStore


class FakeRuntime:
    def __init__(self): self.running = False
    def health(self): return {}
    def list_voices(self): return []
    def runtime_status(self):
        return {
            "gpt_sovits": {"installed": True},
            "service": {"service_running": self.running, "ready": self.running},
            "ffmpeg": {"ready": False},
            "separator": {"ready": False},
        }
    def start_gpt(self):
        self.running = True
        return {"service_running": True, "ready": True}
    def stop_gpt(self):
        self.running = False
        return {"service_running": False, "ready": False}
    def install_ffmpeg(self): return {"started": True}
    def install_separator(self): return {"started": True}
    def install_gpt(self, url): return {"started": True, "url": url}


class FakeFrpc:
    def __init__(self):
        self.running = False
        self.connected = False

    def prepare(self, _config):
        return self.status()

    def start(self):
        self.running = True
        self.connected = True
        return self.status()

    def stop(self):
        self.running = False
        self.connected = False
        return self.status()

    def status(self):
        return {
            "running": self.running,
            "connected": self.connected,
            "conflict": False,
            "phase": "connected" if self.connected else "stopped",
            "message": "连接成功" if self.connected else "语音连接已关闭",
            "remote_port": 19090,
        }


class RuntimeApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        root = Path(self.temp.name)
        store = StudioConfigStore(root / "studio.json")
        store.save({"api_token": "test-api-token"})
        self.client = TestClient(create_app(root=root, config_store=store, runtime=FakeRuntime()))
        self.headers = {"Authorization": "Bearer test-api-token"}

    def tearDown(self): self.temp.cleanup()

    def test_install_resource_routes_delegate_to_runtime(self):
        self.assertTrue(self.client.post("/api/runtime/ffmpeg/install", headers=self.headers).json()["started"])
        self.assertTrue(self.client.post("/api/runtime/separator/install", headers=self.headers).json()["started"])
        response = self.client.post("/api/gpt-sovits/install", headers=self.headers, json={"url": "https://example.test/gpt.7z"})
        self.assertEqual(response.json()["url"], "https://example.test/gpt.7z")

    def test_voice_service_switch_controls_gpt_and_frpc_together(self):
        root = Path(self.temp.name)
        store = StudioConfigStore(root / "voice-service.json")
        store.save({"api_token": "test-api-token", "frp_token": "frp-secret"})
        runtime = FakeRuntime()
        frpc = FakeFrpc()
        client = TestClient(
            create_app(root=root, config_store=store, runtime=runtime, frpc_manager=frpc)
        )

        initial = client.get("/api/voice-service/status", headers=self.headers).json()
        self.assertFalse(initial["enabled"])

        started = client.post("/api/voice-service/start", headers=self.headers).json()
        self.assertTrue(started["enabled"])
        self.assertEqual(started["phase"], "ready")

        stopped = client.post("/api/voice-service/stop", headers=self.headers).json()
        self.assertFalse(stopped["enabled"])
        self.assertEqual(stopped["phase"], "stopped")
