import unittest

import serial
from esptool.util import FatalError

from pixelboard.board import BoardError, PortInfo, find_board_ports
from pixelboard.flasher import to_board_error


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


if __name__ == "__main__":
    unittest.main()
