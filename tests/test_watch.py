import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from to_streamable.watch import (
    is_stable,
    is_video_file,
    move_uploaded,
    process_cycle,
)


class WatchTests(unittest.TestCase):
    def test_video_filter(self):
        self.assertTrue(is_video_file("clip.MP4"))
        self.assertTrue(is_video_file("clip.mkv"))
        self.assertFalse(is_video_file("clip.mp4.part"))
        self.assertFalse(is_video_file("clip.mp4.crdownload"))
        self.assertFalse(is_video_file(".clip.mp4"))
        self.assertFalse(is_video_file("notes.txt"))

    def test_stable_resets_when_size_changes(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.mp4"
            path.write_bytes(b"1234")
            stable, state = is_stable(path, None, now=10, stable_seconds=2)
            self.assertFalse(stable)
            path.write_bytes(b"12345")
            stable, state = is_stable(path, state, now=13, stable_seconds=2)
            self.assertFalse(stable)
            stable, state = is_stable(path, state, now=16, stable_seconds=2)
            self.assertTrue(stable)

    def test_cycle_uploads_once_then_again_if_replaced(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            path = folder / "a.mp4"
            path.write_bytes(b"x" * 20)
            pending = {}
            handled = {}
            done = []

            process_cycle(folder, pending, handled, now=0, stable_seconds=2, on_stable=done.append)
            self.assertEqual(done, [])
            process_cycle(folder, pending, handled, now=1, stable_seconds=2, on_stable=done.append)
            self.assertEqual(done, [])
            process_cycle(folder, pending, handled, now=3, stable_seconds=2, on_stable=done.append)
            self.assertEqual(done, [path.resolve()])

            process_cycle(folder, pending, handled, now=10, stable_seconds=2, on_stable=done.append)
            self.assertEqual(len(done), 1)

            path.write_bytes(b"y" * 30)
            process_cycle(folder, pending, handled, now=20, stable_seconds=2, on_stable=done.append)
            process_cycle(folder, pending, handled, now=23, stable_seconds=2, on_stable=done.append)
            self.assertEqual(len(done), 2)

    def test_move_avoids_clobber(self):
        with TemporaryDirectory() as tmp:
            src = Path(tmp) / "clip.mp4"
            src.write_bytes(b"video")
            dest_dir = Path(tmp) / "uploaded"
            dest_dir.mkdir()
            (dest_dir / "clip.mp4").write_bytes(b"old")
            moved = move_uploaded(src, dest_dir)
            self.assertTrue(moved.exists())
            self.assertNotEqual(moved.name, "clip.mp4")
            self.assertFalse(src.exists())
            self.assertEqual(moved.read_bytes(), b"video")


if __name__ == "__main__":
    unittest.main()
