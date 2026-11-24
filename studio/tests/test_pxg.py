import struct
import unittest
import zlib

from pixelboard import layout, pxg
from pixelboard.pxg import PxgError, pack, unpack


def frames(n, chain=1, ms=100):
    size = 64 * chain * 32 * 3
    return [(bytes([i % 256]) * size, ms + i) for i in range(n)]


def row(n):
    return layout.preset("row", n)


def v1_bytes(src, chain, brightness, swap):
    """Builds a v1 file the way the pre-v2 packer did (kept here as the compatibility reference)."""
    durations = b"".join(struct.pack("<H", ms) for _, ms in src)
    durations += bytes(((len(durations) + 3) & ~3) - len(durations))
    payload = durations + b"".join(rgb for rgb, _ in src)
    header = struct.pack("<4sHHHBBB3xHHII4x", b"PXG1", 1, 64, 32, chain, brightness, 1 if swap else 0,
                         len(src), 0, len(payload), zlib.crc32(payload))
    return header + payload


class PxgTest(unittest.TestCase):
    def test_v2_roundtrip_with_panels(self):
        arr = layout.Arrangement((layout.Panel(0, 10, 5, 0), layout.Panel(1, 74, 5, 0),
                                  layout.Panel(2, 74, 37, 180)))
        src = frames(3, chain=3)
        data = pack(src, arr, brightness=90, split=True)
        anim = unpack(data)
        self.assertEqual((anim.chain, anim.brightness, anim.swap, anim.split), (3, 90, False, True))
        self.assertEqual(anim.panels, [(0, 0, 0), (64, 0, 0), (64, 32, 180)])  # normalized
        self.assertEqual(anim.frames, src)
        self.assertEqual(data[:4], b"PXG1")
        self.assertEqual(struct.unpack_from("<H", data, 4)[0], 2)
        self.assertEqual(data[12], 0x02)
        payload_len, crc = struct.unpack_from("<II", data, 20)
        self.assertEqual(payload_len, len(data) - 32)
        self.assertEqual(crc, zlib.crc32(data[32:]))
        # durations (3 x u16 padded to 8) then the table: <hhHH per panel
        self.assertEqual(struct.unpack_from("<hhHH", data, 32 + 8 + 16), (64, 32, 180, 0))

    def test_v1_still_unpacks(self):
        src = frames(2, chain=2)
        anim = unpack(v1_bytes(src, 2, 70, True))
        self.assertEqual((anim.chain, anim.brightness, anim.swap, anim.split, anim.panels), (2, 70, True, False, []))
        self.assertEqual(anim.frames, src)

    def test_chain_8_ok_9_rejected(self):
        anim = unpack(pack(frames(1, chain=8), row(8), 90, False))
        self.assertEqual(anim.chain, 8)
        self.assertEqual(len(anim.panels), 8)
        nine = layout.Arrangement(tuple(layout.Panel(i, 64 * i, 0, 0) for i in range(9)))
        with self.assertRaises(PxgError):
            pack(frames(1, chain=9), nine, 90, False)

    def test_max_frames(self):
        self.assertEqual(pxg.max_frames(1), 64)
        self.assertEqual(pxg.max_frames(7), 64)
        self.assertEqual(pxg.max_frames(8), 63)
        self.assertLessEqual(pxg.max_frames(8) * 8 * 6144, pxg.MAX_BYTES - 4096)

    def test_too_many_frames_for_chain(self):
        pack(frames(63, chain=8, ms=100), row(8), 90, False)
        with self.assertRaises(PxgError):
            pack(frames(64, chain=8), row(8), 90, False)

    def test_frame_count_must_match_arrangement(self):
        with self.assertRaises(PxgError):
            pack(frames(1, chain=1), row(2), 90, False)

    def test_durations_aligned_to_4_bytes(self):
        data = pack(frames(3), row(1), brightness=90, split=False)
        # 3 x u16 = 6 bytes, padded to 8, then 1 table entry (8), then frame data.
        self.assertEqual(len(data), 32 + 8 + 8 + 3 * 64 * 32 * 3)

    def test_bad_magic(self):
        data = bytearray(pack(frames(1), row(1), 90, False))
        data[0:4] = b"XXXX"
        with self.assertRaises(PxgError):
            unpack(bytes(data))

    def test_crc_mismatch(self):
        data = bytearray(pack(frames(1), row(1), 90, False))
        data[-1] ^= 0xFF
        with self.assertRaises(PxgError):
            unpack(bytes(data))

    def test_truncated(self):
        data = pack(frames(2), row(1), 90, False)
        with self.assertRaises(PxgError):
            unpack(data[:-1])
        with self.assertRaises(PxgError):
            unpack(data[:20])

    def test_too_big(self):
        with self.assertRaises(PxgError):
            pack(frames(65), row(1), 90, False)
        with self.assertRaises(PxgError):
            pack([], row(1), 90, False)

    def test_rejects_unknown_version(self):
        data = bytearray(pack(frames(1), row(1), 90, False))
        data[4] = 3
        with self.assertRaises(PxgError):
            unpack(bytes(data))

    def test_brightness_range(self):
        for bad in (9, 129):
            with self.assertRaises(PxgError):
                pack(frames(1), row(1), bad, False)
        pack(frames(1), row(1), 10, False)
        pack(frames(1), row(1), 128, False)

    def test_frame_size_must_match_chain(self):
        with self.assertRaises(PxgError):
            pack(frames(1, chain=1), row(2), brightness=90, split=False)

    def test_fits_partition(self):
        self.assertEqual(pxg.MAX_BYTES, 3 * 1024 * 1024)
        self.assertLessEqual(len(pack(frames(64, chain=2), row(2), 90, False)), pxg.MAX_BYTES)


if __name__ == "__main__":
    unittest.main()
