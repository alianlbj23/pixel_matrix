import time
import unittest

import serial

from pixelboard import serialcmd
from pixelboard.board import BoardError


class FakeSerial:
    """Records how the port was opened; readline() hands out the queued chunks, then times out."""

    def __init__(self, chunks=(), open_error=None):
        self.port = None
        self.dtr = None
        self.rts = None
        self.timeout = None
        self.write_timeout = None
        self.opened_with = None
        self.written = b""
        self.closed = False
        self._chunks = list(chunks)
        self._open_error = open_error

    def open(self):
        if self._open_error:
            raise self._open_error
        self.opened_with = {"port": self.port, "dtr": self.dtr, "rts": self.rts}

    def reset_input_buffer(self):
        pass

    def write(self, data):
        self.written += data
        return len(data)

    def readline(self):
        if self._chunks:
            return self._chunks.pop(0)
        time.sleep(0.005)
        return b""

    def close(self):
        self.closed = True


def opener(fake):
    return lambda: fake


class IdentifyTest(unittest.TestCase):
    def test_identify_ok(self):
        fake = FakeSerial([b"I (1234) main: gif ok\r\n", b"PXB OK IDEN", b"TIFY 2\r\n"])
        msg = serialcmd.identify("COM11", 2, open_serial=opener(fake), timeout=0.5)
        self.assertIn("2", msg)
        self.assertEqual(fake.written, b"PXB IDENTIFY 2\n")
        self.assertTrue(fake.closed)

    def test_identify_no_reply(self):
        fake = FakeSerial([b"I (1) boot: something\r\n"])
        with self.assertRaises(BoardError) as ctx:
            serialcmd.identify("COM11", 2, open_serial=opener(fake), timeout=0.05)
        self.assertIn("安裝/更新韌體", ctx.exception.user_message)
        self.assertTrue(fake.closed)

    def test_identify_err(self):
        fake = FakeSerial([b"PXB ERR panel count must be 1..8\r\n"])
        with self.assertRaises(BoardError) as ctx:
            serialcmd.identify("COM11", 2, open_serial=opener(fake), timeout=0.5)
        self.assertIn("板子回報錯誤", ctx.exception.user_message)
        self.assertIn("panel count must be 1..8", ctx.exception.user_message)

    def test_identify_opens_with_dtr(self):
        fake = FakeSerial([b"PXB OK IDENTIFY 3\r\n"])
        serialcmd.identify("COM7", 3, open_serial=opener(fake), timeout=0.5)
        self.assertEqual(fake.opened_with, {"port": "COM7", "dtr": True, "rts": False})
        self.assertIsNotNone(fake.timeout)
        self.assertIsNotNone(fake.write_timeout)

    def test_identify_ignores_other_counts(self):
        fake = FakeSerial([b"PXB OK IDENTIFY 4\r\n"])  # stale reply to an earlier command
        with self.assertRaises(BoardError):
            serialcmd.identify("COM11", 2, open_serial=opener(fake), timeout=0.05)

    def test_identify_port_busy(self):
        fake = FakeSerial(open_error=serial.SerialException(
            "could not open port 'COM11': PermissionError(13, 'Access is denied.', None, 5)"))
        with self.assertRaises(BoardError) as ctx, self.assertLogs("pixelboard.serialcmd", "ERROR"):
            serialcmd.identify("COM11", 2, open_serial=opener(fake), timeout=0.05)
        self.assertIn("被其他程式占用", ctx.exception.user_message)

    def test_identify_bad_count(self):
        for n in (0, 9, True, 2.0, "2"):
            with self.assertRaises(ValueError, msg=repr(n)):
                serialcmd.identify("COM11", n, open_serial=opener(FakeSerial()), timeout=0.05)


if __name__ == "__main__":
    unittest.main()
