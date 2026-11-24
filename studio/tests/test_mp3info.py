import unittest

from pixelboard.mp3info import Mp3Error, probe

from tests import mp3_fixtures as fx


class ProbeTest(unittest.TestCase):
    def test_probe_plain(self):
        info = probe(fx.stereo_44k())
        self.assertEqual((info.sample_rate, info.channels), (44100, 2))

    def test_probe_id3v2(self):
        info = probe(fx.id3v2() + fx.stereo_44k())
        self.assertEqual((info.sample_rate, info.channels), (44100, 2))

    def test_probe_mono(self):
        self.assertEqual(probe(fx.mono_44k()).channels, 1)

    def test_probe_skips_leading_junk(self):
        self.assertEqual(probe(b"\x00\x11garbage" + fx.stereo_44k()).sample_rate, 44100)

    def test_reject_reserved_sample_rate(self):
        # Sample-rate index 3 is reserved in every MPEG version.
        bad = fx.frames(bytes([0xFF, 0xFB, 0x9C, 0x64]), 417)
        with self.assertRaises(Mp3Error) as cm:
            probe(bad)
        self.assertIn("取樣率", cm.exception.user_message)

    def test_reject_layer2(self):
        mp2 = fx.frames(bytes([0xFF, 0xFD, 0x90, 0x64]), 417)  # layer bits 10 = Layer II
        with self.assertRaises(Mp3Error) as cm:
            probe(mp2)
        self.assertIn("MP3", cm.exception.user_message)

    def test_reject_not_mp3(self):
        for data in (b"", b"hello world" * 100, bytes(5000)):
            with self.assertRaises(Mp3Error):
                probe(data)

    def test_reject_single_isolated_header(self):
        # One sync word followed by junk is not a stream: 3 chained frames are required.
        with self.assertRaises(Mp3Error):
            probe(bytes([0xFF, 0xFB, 0x90, 0x64]) + b"\x01" * 2000)


if __name__ == "__main__":
    unittest.main()
