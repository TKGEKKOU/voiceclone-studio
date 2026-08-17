import tempfile
import unittest
from pathlib import Path

from studio_core.config import StudioConfigStore


class StudioConfigTests(unittest.TestCase):
    def test_public_config_masks_secrets_and_persists_custom_tokens(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as root:
            store = StudioConfigStore(Path(root) / "studio.json")
            config = store.save({
                "api_token": "custom-api-token",
                "frp_token": "custom-frp-token",
                "frp_server_addr": "47.103.73.128",
            })
            self.assertEqual(config["api_token"], "custom-api-token")
            public = store.public()
            self.assertNotIn("custom-api-token", str(public))
            self.assertNotIn("custom-frp-token", str(public))
            self.assertTrue(public["api_token_configured"])
            self.assertTrue(public["frp_token_configured"])

    def test_invalid_ports_are_rejected(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as root:
            store = StudioConfigStore(Path(root) / "studio.json")
            with self.assertRaisesRegex(ValueError, "端口"):
                store.save({"frp_server_port": 70000})


if __name__ == "__main__":
    unittest.main()
