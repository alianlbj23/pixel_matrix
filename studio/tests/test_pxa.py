import importlib.util
import pathlib
import struct
import tempfile
import unittest
import zlib

from pixelboard import pxa
from pixelboard.mp3info import Info
from pixelboard.pxa import PxaError, Track, pack, unpack, validate_track

from tests import mp3_fixtures as fx

_TOOL = pathlib.Path(__file__).resolve().parents[2] / "tools" / "pack_audio.py"


class PxaTest(unittest.TestCase):
    def test_pack_roundtrip_order(self):
        tracks = [Track("b.mp3", fx.stereo_44k(3)), Track("歌.mp3", fx.mono_44k(5)), Track("a.mp3", b"x")]
        data = pack(tracks)
        self.assertEqual(unpack(data), tracks)
        self.assertEqual(data[:4], b"PXA1")
        count, payload_len, crc = struct.unpack_from("<HII", data, 6)
        self.assertEqual((count, payload_len, crc), (3, len(data) - 32, zlib.crc32(data[32:])))
        for i in range(3):
            offset = struct.unpack_from("<I", data, 32 + 72 * i + 64)[0]
            self.assertEqual(offset % 4, 0)
            self.assertGreaterEqual(offset, 32 + 72 * 3)

    def test_empty_list_valid(self):
        data = pack([])
        self.assertEqual(struct.unpack_from("<H", data, 6)[0], 0)
        self.assertEqual(unpack(data), [])

    def test_crc_mismatch(self):
        data = bytearray(pack([Track("a.mp3", fx.stereo_44k(2))]))
        data[-1] ^= 0xFF
        with self.assertRaises(PxaError):
            unpack(bytes(data))

    def test_bad_magic_and_truncated(self):
        data = pack([Track("a.mp3", fx.stereo_44k(2))])
        with self.assertRaises(PxaError):
            unpack(b"XXXX" + data[4:])
        with self.assertRaises(PxaError):
            unpack(data[:-1])

    def test_capacity_exceeded(self):
        big = b"\x00" * (pxa.CAPACITY // 2)
        with self.assertRaises(PxaError) as cm:
            pack([Track("a.mp3", big), Track("b.mp3", big)])
        self.assertIn("容量", cm.exception.user_message)

    def test_capacity_constant(self):
        self.assertEqual(pxa.CAPACITY, 12 * 1024 * 1024)

    def test_too_many_files(self):
        with self.assertRaises(PxaError):
            pack([Track(f"{i}.mp3", b"x") for i in range(65)])


class ValidateTrackTest(unittest.TestCase):
    def test_valid_returns_info(self):
        self.assertEqual(validate_track("歌.mp3", fx.id3v2() + fx.stereo_44k()), Info(44100, 2))

    def test_name_61_bytes_rejected(self):
        with self.assertRaises(PxaError) as cm:
            validate_track("a" * 57 + ".mp3", fx.stereo_44k())  # 61 bytes
        self.assertIn("檔名", cm.exception.user_message)
        validate_track("a" * 56 + ".mp3", fx.stereo_44k())  # 60 bytes is fine

    def test_cjk_name_counted_in_bytes(self):
        with self.assertRaises(PxaError):
            validate_track("歌" * 20 + ".mp3", fx.stereo_44k())  # 64 bytes

    def test_not_mp3_rejected_as_pxa_error(self):
        with self.assertRaises(PxaError) as cm:
            validate_track("x.mp3", b"not audio" * 100)
        self.assertIn("MP3", cm.exception.user_message)


class PackAudioCliTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self._tmp.name)
        spec = importlib.util.spec_from_file_location("pack_audio", _TOOL)
        self.tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.tool)

    def tearDown(self):
        self._tmp.cleanup()

    def test_packs_sorted_mp3_only(self):
        (self.dir / "b.mp3").write_bytes(fx.stereo_44k(3))
        (self.dir / "a.MP3").write_bytes(fx.mono_44k(3))
        (self.dir / "cover.jpg").write_bytes(b"jpg")
        out = self.dir / "audio.pxa"
        self.assertEqual(self.tool.main([str(self.dir), str(out)]), 0)
        self.assertEqual([t.name for t in unpack(out.read_bytes())], ["a.MP3", "b.mp3"])

    def test_invalid_mp3_fails_build(self):
        (self.dir / "bad.mp3").write_bytes(b"nope" * 100)
        out = self.dir / "audio.pxa"
        self.assertEqual(self.tool.main([str(self.dir), str(out)]), 1)
        self.assertFalse(out.exists())

    def test_empty_dir_gives_empty_image(self):
        out = self.dir / "audio.pxa"
        self.assertEqual(self.tool.main([str(self.dir), str(out)]), 0)
        self.assertEqual(unpack(out.read_bytes()), [])


if __name__ == "__main__":
    unittest.main()
