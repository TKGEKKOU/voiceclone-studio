import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from studio_core.frpc import FrpcManager, managed_frpc_lifespan


class FrpcManagerTests(unittest.TestCase):
    def test_config_uses_dedicated_server_and_mapping_ports(self):
        with tempfile.TemporaryDirectory() as root:
            manager = FrpcManager(Path(root))
            text = manager.build_config({
                "frp_server_addr": "47.103.73.128",
                "frp_server_port": 7001,
                "frp_token": "custom-token",
                "frp_local_port": 9090,
                "frp_remote_port": 19090,
                "frp_proxy_name": "voiceclone-studio",
            })
            self.assertIn('serverAddr = "47.103.73.128"', text)
            self.assertIn("serverPort = 7001", text)
            self.assertIn('auth.token = "custom-token"', text)
            self.assertIn("localPort = 9090", text)
            self.assertIn("remotePort = 19090", text)
            self.assertNotIn("remotePort = 7000", text)

    def test_status_does_not_expose_token(self):
        with tempfile.TemporaryDirectory() as root:
            manager = FrpcManager(Path(root))
            status = manager.status()
            self.assertNotIn("token", status)

    def test_studio_shutdown_stops_owned_frpc(self):
        with tempfile.TemporaryDirectory() as root:
            manager = FrpcManager(Path(root))
            with patch.object(manager, "stop", autospec=True) as stop:

                async def run_lifespan():
                    async with managed_frpc_lifespan(manager):
                        return

                asyncio.run(run_lifespan())
            stop.assert_called_once()

    def test_studio_shutdown_can_leave_frpc_running_when_disabled(self):
        with tempfile.TemporaryDirectory() as root:
            manager = FrpcManager(Path(root))
            with patch.object(manager, "stop", autospec=True) as stop:

                async def run_lifespan():
                    async with managed_frpc_lifespan(manager, stop_on_exit=False):
                        return

                asyncio.run(run_lifespan())
            stop.assert_not_called()

    def test_stop_tolerates_locked_state_file(self):
        with tempfile.TemporaryDirectory() as root:
            manager = FrpcManager(Path(root))
            manager.root.mkdir(parents=True)
            manager.state_path.write_text('{"pid": 0}', encoding="utf-8")
            with patch.object(Path, "unlink", side_effect=PermissionError("locked")):
                status = manager.stop()
            self.assertIn("状态文件暂时无法清理", status["error"])

    def test_status_reports_proxy_conflict_instead_of_endless_connecting(self):
        with tempfile.TemporaryDirectory() as root:
            manager = FrpcManager(Path(root))
            manager.root.mkdir(parents=True)
            manager.config_path.write_text(
                "localPort = 9090\nremotePort = 19090\n",
                encoding="utf-8",
            )
            manager.log_path.write_text(
                "login to server success\n"
                "[voiceclone-studio] start error: proxy [voiceclone-studio] already exists\n",
                encoding="utf-8",
            )
            with patch.object(manager, "is_running", return_value=True), patch.object(
                manager, "_owned_pid", return_value=42
            ):
                status = manager.status()

            self.assertFalse(status["connected"])
            self.assertTrue(status["conflict"])
            self.assertEqual(status["phase"], "conflict")
            self.assertIn("同名 FRPC", status["message"])


if __name__ == "__main__":
    unittest.main()
