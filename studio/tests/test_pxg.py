import struct
import unittest
import zlib

from pixelboard import pxg
from pixelboard.pxg import PxgError, pack, unpack


def frames(n, chain=1, ms=100):
    size = 64 * chain * 32 * 3
    return [(bytes([i % 256]) * size, ms + i) for i in range(n)]


class PxgTest(unittest.TestCase):
    def test_roundtrip(self):
        src = frames(3, chain=2)
        data = pack(src, chain=2, brightness=90, swap=True)
        anim = unpack(data)
        self.assertEqual((anim.chain, anim.brightness, anim.swap), (2, 90, True))
        self.assertEqual(anim.frames, src)
        self.assertEqual(data[:4], b"PXG1")
        payload_len, crc = struct.unpack_from("<II", data, 20)
        self.assertEqual(payload_len, len(data) - 32)
        self.assertEqual(crc, zlib.crc32(data[32:]))

    def test_durations_aligned_to_4_bytes(self):
        data = pack(frames(3), chain=1, brightness=90, swap=False)
        # 3 x u16 = 6 bytes, padded to 8, then frame data.
        self.assertEqual(len(data), 32 + 8 + 3 * 64 * 32 * 3)

    def test_bad_magic(self):
        data = bytearray(pack(frames(1), 1, 90, False))
        data[0:4] = b"XXXX"
        with self.assertRaises(PxgError):
            unpack(bytes(data))

    def test_crc_mismatch(self):
        data = bytearray(pack(frames(1), 1, 90, False))
        data[-1] ^= 0xFF
        with self.assertRaises(PxgError):
            unpack(bytes(data))

    def test_truncated(self):
        data = pack(frames(2), 1, 90, False)
        with self.assertRaises(PxgError):
            unpack(data[:-1])
        with self.assertRaises(PxgError):
            unpack(data[:20])

    def test_too_big(self):
        with self.assertRaises(PxgError):
            pack(frames(65), 1, 90, False)
        with self.assertRaises(PxgError):
            pack([], 1, 90, False)

    def test_brightness_range(self):
        for bad in (9, 129):
            with self.assertRaises(PxgError):
                pack(frames(1), 1, bad, False)
        pack(frames(1), 1, 10, False)
        pack(frames(1), 1, 128, False)

    def test_frame_size_must_match_chain(self):
        with self.assertRaises(PxgError):
            pack(frames(1, chain=1), chain=2, brightness=90, swap=False)

    def test_bad_chain(self):
        with self.assertRaises(PxgError):
            pack(frames(1), chain=3, brightness=90, swap=False)

    def test_fits_partition(self):
        self.assertEqual(pxg.MAX_BYTES, 0x100000)
        self.assertLessEqual(len(pack(frames(64, chain=2), 2, 90, False)), pxg.MAX_BYTES)


if __name__ == "__main__":
    unittest.main()
