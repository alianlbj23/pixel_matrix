import threading
import unittest
from unittest import mock

import serial
from esptool.util import FatalError

from pixelboard.board import AUDIO_OFFSET, GIF_OFFSET, BoardError, PortInfo, find_board_ports
from pixelboard.flasher import Flasher, to_board_error


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

    def test_connect_failed_mentions_boot(self):
        err = to_board_error(FatalError("Failed to connect to ESP32-S3: No serial data received."))
        self.assertIn("BOOT", err.user_message)

    def test_write_interrupted(self):
        err = to_board_error(serial.SerialException("write failed: device disconnected"))
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
