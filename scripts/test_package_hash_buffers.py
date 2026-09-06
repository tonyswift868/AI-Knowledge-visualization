"""Hash reader regressions, standalone: no application or database imports."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from scripts import verify_windows_package as v


class HashBufferTests(unittest.TestCase):
    def test_empty_short_and_multiblock_with_reused_prefix(self):
        with tempfile.TemporaryDirectory(prefix="e107-hash-", dir="E:/Codex/Temp") as directory:
            path = Path(directory) / "bytes.bin"
            prefix = bytearray(b"old unrelated bytes")
            for data in (b"", b"abc", v.SQLITE_MAGIC, bytes(range(256)) * 2200, b"x"):
                path.write_bytes(data)
                for size in (1, 7, 256 * 1024):
                    self.assertEqual(v.sha256_file(path, prefix=prefix, chunk_size=size), hashlib.sha256(data).hexdigest())
                    self.assertEqual(prefix, data[:16])

    def test_short_reads_accumulate_sqlite_prefix_and_hash_every_byte(self):
        data = v.SQLITE_MAGIC + b"different payload after header" * 100
        class ShortReader(io.BytesIO):
            def readinto(self, view):
                return super().readinto(view[:3])
        class FixturePath:
            def open(self, mode, *, buffering):
                self.mode, self.buffering = mode, buffering
                return ShortReader(data)
        path = FixturePath()
        prefix = bytearray()
        self.assertEqual(v.sha256_file(path, prefix=prefix), hashlib.sha256(data).hexdigest())
        self.assertEqual(prefix, v.SQLITE_MAGIC)
        self.assertEqual((path.mode, path.buffering), ("rb", 0))

    def test_concurrent_readers_do_not_share_buffers(self):
        with tempfile.TemporaryDirectory(prefix="e107-hash-", dir="E:/Codex/Temp") as directory:
            inputs = []
            for index in range(16):
                data = bytes([index]) * (1024 * 1024 + index)
                path = Path(directory) / str(index)
                path.write_bytes(data)
                inputs.append((path, hashlib.sha256(data).hexdigest()))
            def check(pair):
                path, expected = pair
                for _ in range(3):
                    self.assertEqual(v.sha256_file(path), expected)
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(check, inputs))

    def test_invalid_chunks_and_io_errors_fail_closed(self):
        for size in (0, -1, True, 1.5):
            with self.assertRaises(ValueError):
                v.sha256_file(Path("unused"), chunk_size=size)
        class FailingPath:
            def open(self, *args, **kwargs):
                raise OSError("read failed")
        with self.assertRaisesRegex(v.PackageError, "read failed"):
            v.sha256_file(FailingPath())


if __name__ == "__main__":
    unittest.main()
