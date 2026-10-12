import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_devices


class DeviceCheckTests(unittest.TestCase):
    def test_reconnected_serial_port_is_discovered(self):
        with patch('kmbox.glob.glob', return_value=['/dev/cu.usbserial-1120']):
            self.assertEqual(check_devices.KMBox.resolve_port(), '/dev/cu.usbserial-1120')

    def test_ambiguous_serial_ports_require_selection(self):
        with patch('kmbox.glob.glob', return_value=['/dev/cu.usbserial-120', '/dev/cu.usbserial-1120']):
            with self.assertRaises(ValueError):
                check_devices.KMBox.resolve_port()
            self.assertEqual(check_devices.KMBox.resolve_port('/dev/cu.usbserial-1120'),
                             '/dev/cu.usbserial-1120')

    def test_busy_serial_reports_actionable_failure(self):
        with patch.object(check_devices, "KMBox", side_effect=BlockingIOError):
            result = check_devices.check_kmbox("mock-port", 1)
        self.assertFalse(result["ok"])
        self.assertIn("占用", result["error"])

    def test_invalid_button_reply_does_not_report_device_healthy(self):
        with patch.object(check_devices, "KMBox") as mocked:
            box = mocked.return_value.__enter__.return_value
            box.command.return_value = "km.version()\r\nkmbox: 12.0.0\r\n>>> "
            box.physical_left.side_effect = ValueError("bad state")
            result = check_devices.check_kmbox("mock-port", 1)
            box.move.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertIn("bad state", result["error"])

    def test_other_serial_device_is_rejected(self):
        with patch.object(check_devices, "KMBox") as mocked:
            box = mocked.return_value.__enter__.return_value
            box.command.return_value = "version unknown\r\n>>> "
            result = check_devices.check_kmbox("mock-port", 1)
            box.physical_left.assert_not_called()
        self.assertFalse(result["ok"])

    def test_video_startup_timeout_returns_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "aim").mkdir()
            (root / "aim/video_check.swift").write_text("// mock")
            with patch.object(check_devices, "ROOT", root), patch.object(
                    check_devices.subprocess, "run", side_effect=[
                        subprocess.CompletedProcess([], 0, "", ""),
                        subprocess.TimeoutExpired("mock", 16)]):
                result = check_devices.check_video("mock-device", 1)
        self.assertFalse(result["ok"])
        self.assertIn("超时", result["error"])


if __name__ == "__main__":
    unittest.main()
