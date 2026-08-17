import unittest

from app import _StudioRuntime


class _Adapter:
    def status(self):
        return {"service_running": False}


class _Registry:
    def get(self, _voice_id):
        return type("Voice", (), {"status": "ready"})()


class _Synthesis:
    called = False

    def synthesize(self, *_args, **_kwargs):
        self.called = True
        return b"unexpected"


class ManualGptStartTests(unittest.TestCase):
    def test_synthesis_does_not_auto_start_gpt_service(self):
        runtime = _StudioRuntime.__new__(_StudioRuntime)
        runtime.adapter = _Adapter()
        runtime.registry = _Registry()
        runtime.synthesis = _Synthesis()

        with self.assertRaisesRegex(RuntimeError, "请先手动启动 GPT-SoVITS 推理服务"):
            list(runtime.synthesize_chunks("voice-1", "测试", "zh"))

        self.assertFalse(runtime.synthesis.called)


if __name__ == "__main__":
    unittest.main()
