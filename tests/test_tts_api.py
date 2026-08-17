import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

from fastapi.testclient import TestClient

from app import create_app
from studio_core.config import StudioConfigStore


class FakeRuntime:
    def list_voices(self):
        return [{"id": "voice-1", "name": "Test", "language": "zh", "status": "ready"}]

    def get_voice(self, voice_id):
        if voice_id != "voice-1":
            return None
        return SimpleNamespace(id=voice_id, name="Test", reference_language="zh")

    def synthesize_chunks(self, voice_id, text, text_language):
        if voice_id != "voice-1":
            raise KeyError(voice_id)
        return iter((b"RIFF", b"test-audio"))


class TTSApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        root = Path(self.temp.name)
        store = StudioConfigStore(root / "studio.json")
        store.save({"api_token": "test-api-token"})
        self.client = TestClient(create_app(root=root, config_store=store, runtime=FakeRuntime()))
        self.headers = {"Authorization": "Bearer test-api-token"}

    def tearDown(self):
        self.temp.cleanup()

    def test_voiceclone_tts_streams_wav_bytes(self):
        response = self.client.post("/api/tts", headers=self.headers, json={"voice_id": "voice-1", "text": "你好", "text_language": "zh"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"RIFFtest-audio")
        self.assertEqual(response.headers["content-type"], "audio/wav")

    def test_gsvi_endpoint_uses_dl_url_and_one_time_ticket(self):
        response = self.client.post("/infer_single", headers=self.headers, json={"dl_url": "http://127.0.0.1:19090", "version": "v2Pro", "model_name": "voice-1", "prompt_text_lang": "中文", "emotion": "默认", "text": "你好", "text_lang": "中文"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["msg"], "合成成功")
        self.assertTrue(payload["audio_url"].startswith("http://127.0.0.1:19090/api/audio/"))
        path = urlsplit(payload["audio_url"]).path
        first, second = self.client.get(path), self.client.get(path)
        self.assertEqual(first.content, b"RIFFtest-audio")
        self.assertEqual(second.status_code, 404)

    def test_invalid_token_is_rejected(self):
        self.assertEqual(self.client.get("/api/health", headers={"Authorization": "Bearer wrong"}).status_code, 401)
