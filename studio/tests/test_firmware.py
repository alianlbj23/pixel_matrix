import json
import pathlib
import tempfile
import unittest

from pixelboard.board import BoardError
from pixelboard.firmware import IMAGE_OFFSETS, load_images


def make_build(root: pathlib.Path, flash_files: dict) -> None:
    (root / "bootloader").mkdir()
    (root / "partition_table").mkdir()
    (root / "bootloader" / "bootloader.bin").write_bytes(b"BOOT")
    (root / "partition_table" / "partition-table.bin").write_bytes(b"PART")
    (root / "pixel-board.bin").write_bytes(b"APP!")
    (root / "flasher_args.json").write_text(json.dumps({"flash_files": flash_files}))


GOOD = {"0x0": "bootloader/bootloader.bin", "0x8000": "partition_table/partition-table.bin",
        "0x10000": "pixel-board.bin", "0x310000": "audio.bin"}


class LoadImagesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_loads_three_images_and_ignores_data_partitions(self):
        make_build(self.root, GOOD)
        images = load_images(self.root)
        self.assertEqual(images, {"bootloader": b"BOOT", "partition_table": b"PART", "app": b"APP!"})
        self.assertEqual(IMAGE_OFFSETS, {"bootloader": 0x0, "partition_table": 0x8000, "app": 0x10000})

    def test_missing_dir_is_board_error(self):
        with self.assertRaises(BoardError):
            load_images(self.root / "nope")

    def test_unexpected_offset_is_board_error(self):
        bad = dict(GOOD)
        bad["0x20000"] = bad.pop("0x10000")
        make_build(self.root, bad)
        with self.assertRaises(BoardError):
            load_images(self.root)



class BundledAppInfoTest(unittest.TestCase):
    def test_reads_version_from_app_image(self):
        from pixelboard.board import AppInfo
        from pixelboard.firmware import bundled_app_info

        head = (pathlib.Path(__file__).resolve().parent / "fixtures" / "app_head.bin").read_bytes()
        self.assertEqual(bundled_app_info({"app": head + b"x"}), AppInfo("pixel-board", "1.0.0"))

    def test_app_without_descriptor_is_board_error(self):
        from pixelboard.firmware import bundled_app_info

        with self.assertRaises(BoardError):
            bundled_app_info({"app": b"\xff" * 512})


if __name__ == "__main__":
    unittest.main()
