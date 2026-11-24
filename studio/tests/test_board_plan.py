import pathlib
import unittest

from pixelboard.board import AppInfo, BoardError, BoardState, parse_app_desc, plan_flash, read_state

FIX = pathlib.Path(__file__).resolve().parent / "fixtures"
APP_HEAD = (FIX / "app_head.bin").read_bytes()
PART = (FIX / "partition-table.bin").read_bytes()
OLD_PART = PART[:40] + bytes([PART[40] ^ 0xFF]) + PART[41:]   # any difference = old layout
BUNDLED = {"bootloader": b"B" * 16, "partition_table": PART, "app": APP_HEAD + b"A" * 64}
BUNDLED_APP = AppInfo("pixel-board", "1.0.0")
GIF, AUDIO = b"G" * 32, b"M" * 32


def offsets(plan):
    return [off for off, _ in plan.regions]


class ParseAppDescTest(unittest.TestCase):
    def test_parse_app_desc(self):
        self.assertEqual(parse_app_desc(APP_HEAD), AppInfo("pixel-board", "1.0.0"))

    def test_erased_or_short_is_none(self):
        self.assertIsNone(parse_app_desc(b"\xff" * 256))
        self.assertIsNone(parse_app_desc(APP_HEAD[:40]))


class PlanFlashTest(unittest.TestCase):
    def plan(self, app, table=PART, gif=GIF, audio=AUDIO):
        return plan_flash(BoardState(app, table), BUNDLED, BUNDLED_APP, gif, audio)

    def test_factory_demo_is_first_install(self):
        plan = self.plan(AppInfo("factory_demo", "v2"))
        self.assertEqual(plan.kind, "first_install")
        self.assertEqual(offsets(plan), [0x0, 0x8000, 0x10000, 0x210000, 0x310000])

    def test_erased_flash_is_first_install(self):
        plan = self.plan(parse_app_desc(b"\xff" * 256), table=b"\xff" * 0xC00)
        self.assertEqual(plan.kind, "first_install")

    def test_same_version_updates_content_only(self):
        plan = self.plan(BUNDLED_APP, audio=None)
        self.assertEqual(plan.kind, "update_content")
        self.assertEqual(plan.regions, [(0x210000, GIF)])

    def test_other_version_updates_firmware_without_bootloader(self):
        plan = self.plan(AppInfo("pixel-board", "0.9.0"))
        self.assertEqual(plan.kind, "update_firmware")
        self.assertEqual(offsets(plan), [0x10000, 0x210000, 0x310000])

    def test_old_partition_table_is_rewritten(self):
        plan = self.plan(BUNDLED_APP, table=OLD_PART)
        self.assertEqual(plan.kind, "update_firmware")
        self.assertEqual(offsets(plan), [0x8000, 0x10000, 0x210000, 0x310000])

    def test_keep_music_note_when_audio_none(self):
        plan = self.plan(BUNDLED_APP, audio=None)
        self.assertTrue(any("保留" in n for n in plan.notes), plan.notes)

    def test_replace_music_note_when_audio_given(self):
        plan = self.plan(BUNDLED_APP)
        self.assertTrue(any("取代" in n for n in plan.notes), plan.notes)

    def test_first_install_without_music_says_no_music(self):
        plan = self.plan(None, audio=None)
        self.assertTrue(any("沒有背景音樂" in n for n in plan.notes), plan.notes)
        self.assertFalse(any("保留" in n for n in plan.notes))

    def test_new_table_without_music_warns_to_readd(self):
        plan = self.plan(BUNDLED_APP, table=OLD_PART, audio=None)
        self.assertTrue(any("重新加入音樂" in n for n in plan.notes), plan.notes)

    def test_nothing_to_flash(self):
        with self.assertRaises(BoardError) as cm:
            self.plan(BUNDLED_APP, gif=None, audio=None)
        self.assertIn("沒有要燒錄的內容", cm.exception.user_message)


class FakeFlasher:
    def __init__(self, mem):
        self.mem = mem
        self.reads = []

    def read(self, offset, size):
        self.reads.append((offset, size))
        return self.mem[offset]


class ReadStateTest(unittest.TestCase):
    def test_reads_app_head_and_partition_table(self):
        f = FakeFlasher({0x10000: APP_HEAD, 0x8000: PART})
        state = read_state(f)
        self.assertEqual(state, BoardState(AppInfo("pixel-board", "1.0.0"), PART))
        self.assertEqual(sorted(f.reads), [(0x8000, 0xC00), (0x10000, 256)])


if __name__ == "__main__":
    unittest.main()
