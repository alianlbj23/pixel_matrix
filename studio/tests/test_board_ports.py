import threading
import unittest
from unittest import mock

import serial
from esptool.util import FatalError

from pixelboard.board import AUDIO_OFFSET, GIF_OFFSET, BoardError, PortInfo, find_board_ports
from pixelboard.flasher import MSG_NO_PERMISSION, Flasher, to_board_error


class FindBoardPortsTest(unittest.TestCase):
    def test_picks_only_esp32s3_usb_jtag(self):
        ports = [PortInfo("COM3", 0x10C4, 0xEA60, "CP2102"),
                 PortInfo("COM11", 0x303A, 0x1001, "USB JTAG/serial debug unit"),
                 PortInfo("COM1", None, None, "Communications Port")]
        self.assertEqual(find_board_ports(ports), ["COM11"])

    def test_two_boards_sorted(self):
        ports = [PortInfo("COM12", 0x303A, 0x1001, ""), PortInfo("COM11", 0x303A, 0x1001, "")]
        self.assertEqual(find_board_ports(ports), ["COM11", "COM12"])

    def test_none_found(self):
        self.assertEqual(find_board_ports([]), [])


class ErrorMappingTest(unittest.TestCase):
    def test_port_busy(self):
        err = to_board_error(serial.SerialException(
            "could not open port 'COM11': PermissionError(13, 'Access is denied.', None, 5)"))
        self.assertIsInstance(err, BoardError)
        self.assertIn("被其他程式占用", err.user_message)

    def test_linux_permission_message(self):
        err = to_board_error(serial.SerialException(
            "[Errno 13] could not open port /dev/ttyACM0: [Errno 13] Permission denied: '/dev/ttyACM0'"))
        self.assertEqual(err.user_message, MSG_NO_PERMISSION)

    def test_windows_access_denied_still_busy(self):
        err = to_board_error(serial.SerialException(
            "could not open port 'COM11': PermissionError(13, 'Access is denied.', None, 5)"))
        self.assertIn("被其他程式占用", err.user_message)

    def test_connect_failed_mentions_boot(self):
        err = to_board_error(FatalError("Failed to connect to ESP32-S3: No serial data received."))
        self.assertIn("BOOT", err.user_message)

    def test_write_interrupted(self):
        err = to_board_error(serial.SerialException("write failed: device disconnected"))
        self.assertIn("燒錄中斷", err.user_message)

    def test_write_timeout_interrupted(self):
        err = to_board_error(serial.SerialTimeoutException("Write timeout"))
        self.assertIn("燒錄中斷", err.user_message)

    def test_unexpected_keeps_original_text(self):
        err = to_board_error(ValueError("weird thing"))
        self.assertIn("未預期的錯誤", err.user_message)
        self.assertIn("weird thing", err.user_message)

    def test_board_error_passes_through(self):
        original = BoardError("已經是中文訊息")
        self.assertIs(to_board_error(original), original)


class FakeEsp:
    CHIP_NAME = "ESP32-S3"


class WriteChunkedTest(unittest.TestCase):
    """Flasher.write_chunked against a fake esp: esptool's write_flash is replaced by a recorder."""

    def setUp(self):
        self.stop = threading.Event()
        self.progress = []
        self.flasher = Flasher("COM11", lambda *args: self.progress.append(args))
        self.flasher._esp = FakeEsp()
        self.calls = []
        self.on_write = lambda offset: None

    def fake_write_flash(self, esp, addr_data):
        self.assertIsInstance(esp, FakeEsp)
        self.assertEqual(len(addr_data), 1)
        offset, data = addr_data[0]
        self.calls.append((offset, bytes(data)))
        self.on_write(offset)

    def run_chunked(self, regions):
        with mock.patch("esptool.cmds.write_flash", side_effect=self.fake_write_flash):
            return self.flasher.write_chunked(regions, self.stop)

    def test_write_chunked_all(self):
        gif, audio = bytes(range(256)) * 600, b"\x55" * 70000
        ok = self.run_chunked([(0x10000, b"A" * 200000), (GIF_OFFSET, gif), (AUDIO_OFFSET, audio)])
        self.assertTrue(ok)
        self.assertEqual([(o, len(d)) for o, d in self.calls],
                         [(0x10000, 200000), (GIF_OFFSET, 65536), (GIF_OFFSET + 65536, 65536),
                          (GIF_OFFSET + 131072, 153600 - 131072), (AUDIO_OFFSET, 65536),
                          (AUDIO_OFFSET + 65536, 70000 - 65536)])
        self.assertEqual(b"".join(d for o, d in self.calls if GIF_OFFSET <= o < AUDIO_OFFSET), gif)
        self.assertEqual(self.progress[-1][:2], ("write", 1.0))

    def test_write_chunked_stops_in_gif_region(self):
        def on_write(offset):
            if offset == GIF_OFFSET + 65536:
                self.stop.set()
        self.on_write = on_write
        ok = self.run_chunked([(0x10000, b"A" * 100000), (GIF_OFFSET, b"G" * 300000), (AUDIO_OFFSET, b"M" * 1000)])
        self.assertFalse(ok)
        self.assertEqual([o for o, _ in self.calls], [0x10000, GIF_OFFSET, GIF_OFFSET + 65536])

    def test_stop_deferred_during_firmware(self):
        self.on_write = lambda offset: self.stop.set()  # stop pressed while the bootloader is written
        ok = self.run_chunked([(0x0, b"B" * 20000), (0x8000, b"P" * 3072), (0x10000, b"A" * 300000),
                               (GIF_OFFSET, b"G" * 1000)])
        self.assertFalse(ok)
        self.assertEqual([(o, len(d)) for o, d in self.calls], [(0x0, 20000), (0x8000, 3072), (0x10000, 300000)])
        self.assertTrue(self.progress)
        self.assertTrue(all(p[2] is False for p in self.progress), self.progress)

    def test_progress_marks_content_stoppable(self):
        self.run_chunked([(0x10000, b"A" * 1000), (GIF_OFFSET, b"G" * 1000)])
        self.assertIn(("write", 0.5, True), self.progress)
        self.assertTrue(all(len(p) == 3 for p in self.progress))

    def test_write_error_mapped(self):
        def boom(offset):
            raise serial.SerialException("write failed: device disconnected")
        self.on_write = boom
        with self.assertRaises(BoardError) as ctx, self.assertLogs("pixelboard.flasher", "ERROR"):
            self.run_chunked([(GIF_OFFSET, b"G" * 10)])
        self.assertIn("燒錄中斷", ctx.exception.user_message)


if __name__ == "__main__":
    unittest.main()


class ReconnectTest(unittest.TestCase):
    """Connecting twice in one process (flash, then flash again) must work."""

    def connect(self, progress):
        esp = mock.Mock(CHIP_NAME="ESP32-S3")
        flasher = Flasher("COM11", progress)
        with mock.patch("esptool.cmds.detect_chip", return_value=esp), \
                mock.patch("esptool.cmds.run_stub", return_value=esp), mock.patch("esptool.cmds.attach_flash"):
            flasher.connect()
        return flasher

    def test_second_connect_works_and_gets_progress(self):
        from esptool.logger import log as esptool_log

        first, second = [], []
        self.connect(lambda *a: first.append(a))
        self.connect(lambda *a: second.append(a))
        first.clear()
        esptool_log.progress_bar(cur_iter=1, total_iters=2, prefix="Writing")
        self.assertEqual(first, [])
        self.assertEqual(second[-1], ("Writing", 0.5))


class FlashMd5Test(unittest.TestCase):
    def make(self, **esp_kwargs):
        flasher = Flasher("COM11")
        flasher._esp = mock.Mock(**esp_kwargs)
        return flasher

    def test_md5_lowercased(self):
        flasher = self.make(**{"flash_md5sum.return_value": "0123456789ABCDEF0123456789ABCDEF"})
        self.assertEqual(flasher.flash_md5(0x10000, 4096), "0123456789abcdef0123456789abcdef")
        flasher._esp.flash_md5sum.assert_called_once_with(0x10000, 4096)

    def test_md5_timeout_is_interrupted(self):
        flasher = self.make(**{"flash_md5sum.side_effect": FatalError("Timed out waiting for packet header")})
        with self.assertRaises(BoardError) as cm, self.assertLogs("pixelboard.flasher", "ERROR"):
            flasher.flash_md5(0x10000, 4096)
        self.assertIn("燒錄中斷", cm.exception.user_message)

    def test_md5_garbage_reply_rejected(self):
        for reply in ("xyz", b"\x00" * 16, None, "0" * 31):
            flasher = self.make(**{"flash_md5sum.return_value": reply})
            with self.assertRaises(BoardError, msg=repr(reply)), self.assertLogs("pixelboard.flasher", "ERROR"):
                flasher.flash_md5(0x10000, 4096)

    def test_md5_needs_connection(self):
        with self.assertRaises(BoardError):
            Flasher("COM11").flash_md5(0x10000, 4096)


class CloseResetTest(unittest.TestCase):
    def make(self):
        flasher = Flasher("COM11")
        esp = mock.Mock()
        flasher._esp = esp
        return flasher, esp

    def test_reset_then_close(self):
        flasher, esp = self.make()
        order = mock.Mock()
        order.attach_mock(esp._port.close, "close")
        with mock.patch("esptool.cmds.reset_chip", side_effect=lambda e, mode: order.reset(mode)) as reset:
            flasher.close(reset=True)
        reset.assert_called_once_with(esp, "hard-reset")
        self.assertEqual([c[0] for c in order.mock_calls], ["reset", "close"])
        self.assertIsNone(flasher._esp)

    def test_reset_failure_still_closes_port(self):
        flasher, esp = self.make()
        with mock.patch("esptool.cmds.reset_chip", side_effect=FatalError("gone")):
            with self.assertLogs("pixelboard.flasher", level="WARNING"):
                flasher.close(reset=True)
        esp._port.close.assert_called_once()
        self.assertIsNone(flasher._esp)

    def test_no_connection_no_reset(self):
        flasher = Flasher("COM11")
        with mock.patch("esptool.cmds.reset_chip") as reset:
            flasher.close(reset=True)
        reset.assert_not_called()

    def test_plain_close_does_not_reset(self):
        flasher, esp = self.make()
        with mock.patch("esptool.cmds.reset_chip") as reset:
            flasher.close()
        reset.assert_not_called()
        esp._port.close.assert_called_once()
