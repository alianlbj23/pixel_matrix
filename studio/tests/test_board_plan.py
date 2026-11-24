import hashlib
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
    def plan(self, app, table=PART, gif=GIF, audio=AUDIO, app_matches=None):
        return plan_flash(BoardState(app, table, app_matches), BUNDLED, BUNDLED_APP, gif, audio)

    def test_factory_demo_is_first_install(self):
        plan = self.plan(AppInfo("factory_demo", "v2"))
        self.assertEqual(plan.kind, "first_install")
        self.assertEqual(offsets(plan), [0x0, 0x8000, 0x10000, 0x210000, 0x510000])

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
        self.assertEqual(offsets(plan), [0x10000, 0x210000, 0x510000])

    def test_old_partition_table_is_rewritten(self):
        plan = self.plan(BUNDLED_APP, table=OLD_PART)
        self.assertEqual(plan.kind, "update_firmware")
        self.assertEqual(offsets(plan), [0x8000, 0x10000, 0x210000, 0x510000])

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
        self.assertTrue(plan.music_may_be_lost)

    def test_music_kept_or_replaced_is_not_lost(self):
        self.assertFalse(self.plan(BUNDLED_APP, audio=None).music_may_be_lost)
        self.assertFalse(self.plan(AppInfo("pixel-board", "0.9.0"), audio=None).music_may_be_lost)
        self.assertFalse(self.plan(BUNDLED_APP, table=OLD_PART).music_may_be_lost)
        self.assertFalse(self.plan(None, audio=None).music_may_be_lost)

    def test_nothing_to_flash(self):
        with self.assertRaises(BoardError) as cm:
            self.plan(BUNDLED_APP, gif=None, audio=None)
        self.assertIn("沒有要燒錄的內容", cm.exception.user_message)

    def test_same_version_but_app_md5_differs_rewrites_app(self):
        plan = self.plan(BUNDLED_APP, gif=None, audio=None, app_matches=False)
        self.assertEqual(plan.kind, "update_firmware")
        self.assertEqual(plan.regions, [(0x10000, BUNDLED["app"])])
        self.assertTrue(any("不完整" in n for n in plan.notes), plan.notes)

    def test_corrupt_app_with_content_rewrites_app_first(self):
        plan = self.plan(BUNDLED_APP, app_matches=False)
        self.assertEqual(plan.kind, "update_firmware")
        self.assertEqual(offsets(plan), [0x10000, 0x210000, 0x510000])

    def test_app_md5_matches_updates_content_only(self):
        plan = self.plan(BUNDLED_APP, audio=None, app_matches=True)
        self.assertEqual(plan.kind, "update_content")
        self.assertEqual(plan.regions, [(0x210000, GIF)])

    def test_app_md5_matches_nothing_to_flash(self):
        with self.assertRaises(BoardError) as cm:
            self.plan(BUNDLED_APP, gif=None, audio=None, app_matches=True)
        self.assertIn("沒有要燒錄的內容", cm.exception.user_message)


class FakeFlasher:
    def __init__(self, mem, md5=None):
        self.mem = mem
        self.md5 = md5
        self.reads = []
        self.md5_calls = []

    def read(self, offset, size):
        self.reads.append((offset, size))
        return self.mem[offset]

    def flash_md5(self, offset, size):
        self.md5_calls.append((offset, size))
        return self.md5


class ReadStateTest(unittest.TestCase):
    def test_reads_app_head_and_partition_table(self):
        f = FakeFlasher({0x10000: APP_HEAD, 0x8000: PART})
        state = read_state(f)
        self.assertEqual(state, BoardState(AppInfo("pixel-board", "1.0.0"), PART))
        self.assertEqual(sorted(f.reads), [(0x8000, 0xC00), (0x10000, 256)])
        self.assertIsNone(state.app_matches)
        self.assertEqual(f.md5_calls, [])

    def test_app_md5_matches_bundled(self):
        app = BUNDLED["app"]
        f = FakeFlasher({0x10000: APP_HEAD, 0x8000: PART}, md5=hashlib.md5(app).hexdigest())
        state = read_state(f, app)
        self.assertTrue(state.app_matches)
        self.assertEqual(f.md5_calls, [(0x10000, len(app))])

    def test_app_md5_differs_from_bundled(self):
        app = BUNDLED["app"]
        f = FakeFlasher({0x10000: APP_HEAD, 0x8000: PART}, md5=hashlib.md5(app + b"x").hexdigest())
        self.assertIs(read_state(f, app).app_matches, False)

    def test_no_md5_for_other_firmware(self):
        f = FakeFlasher({0x10000: b"\xff" * 256, 0x8000: PART}, md5="0" * 32)
        state = read_state(f, BUNDLED["app"])
        self.assertIsNone(state.app_matches)
        self.assertEqual(f.md5_calls, [])

    def test_md5_error_propagates(self):
        f = FakeFlasher({0x10000: APP_HEAD, 0x8000: PART})
        f.flash_md5 = lambda offset, size: (_ for _ in ()).throw(BoardError("燒錄中斷"))
        with self.assertRaises(BoardError):
            read_state(f, BUNDLED["app"])


if __name__ == "__main__":
    unittest.main()
