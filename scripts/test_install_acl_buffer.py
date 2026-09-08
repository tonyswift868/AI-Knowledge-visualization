"""Failure boundaries of fresh OS ACL reads and bounded conversion reuse."""
import ctypes as c
from ctypes import wintypes as w
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from scripts import windows_install_trust as t


class AclBufferTests(unittest.TestCase):
    def setUp(self):
        self.saved = getattr(t._acl_buffers, 'buffer', None)
        t._acl_buffers.buffer = c.create_string_buffer(4096)

    def tearDown(self):
        if self.saved is None:
            del t._acl_buffers.buffer
        else:
            t._acl_buffers.buffer = self.saved

    def invoke(self, callback, convert=None):
        with patch.object(t, '_api', return_value=(SimpleNamespace(GetFileSecurityW=callback), None)), patch.object(t, '_descriptor_text', return_value='descriptor') as text:
            result = t.security_descriptor(Path('test-only'))
            if convert is not None:
                self.assertEqual(text.call_args.args[0], convert)
            return result

    def test_reads_again_and_copies_only_complete_reported_bytes(self):
        calls = []
        def read(path, flags, buffer, capacity, needed):
            calls.append((path, flags))
            c.memmove(buffer, b'x' * 20, 20)
            c.cast(needed, c.POINTER(w.DWORD)).contents.value = 20
            return True
        for _ in range(2):
            self.assertEqual(self.invoke(read, b'x' * 20), 'descriptor')
        self.assertEqual(calls, [('test-only', 5)] * 2)

    def test_insufficient_buffer_retries_once_with_required_capacity(self):
        sizes = []
        def read(path, flags, buffer, capacity, needed):
            sizes.append(capacity)
            c.cast(needed, c.POINTER(w.DWORD)).contents.value = 5000
            if capacity < 5000:
                c.set_last_error(122)
                return False
            return True
        self.assertEqual(self.invoke(read), 'descriptor')
        self.assertEqual(sizes, [4096, 5000])

    def test_access_denied_oversize_and_invalid_success_reject(self):
        for error, length, success in ((5, 0, False), (122, 131073, False), (0, 0, True), (0, 4097, True)):
            with self.subTest(error=error, length=length):
                def read(path, flags, buffer, capacity, needed):
                    c.set_last_error(error)
                    c.cast(needed, c.POINTER(w.DWORD)).contents.value = length
                    return success
                with self.assertRaises(t.RepairRequired):
                    self.invoke(read)

    def test_descriptor_growth_during_retry_rejects(self):
        def read(path, flags, buffer, capacity, needed):
            c.set_last_error(122)
            c.cast(needed, c.POINTER(w.DWORD)).contents.value = capacity + 1
            return False
        with self.assertRaises(t.RepairRequired):
            self.invoke(read)

    def test_concurrent_os_reads_have_separate_buffers(self):
        from concurrent.futures import ThreadPoolExecutor
        import time
        def read(path, flags, buffer, capacity, needed):
            c.memmove(buffer, bytes([int(path)]) * 20, 20)
            c.cast(needed, c.POINTER(w.DWORD)).contents.value = 20
            time.sleep(0.002)  # another thread may enter before this read returns
            return True
        with patch.object(t, '_api', return_value=(SimpleNamespace(GetFileSecurityW=read), None)), patch.object(t, '_descriptor_text', side_effect=lambda raw: raw):
            with ThreadPoolExecutor(max_workers=4) as pool:
                values = list(pool.map(lambda i: t.security_descriptor(Path(str(i))), range(16)))
        self.assertEqual(values, [bytes([i]) * 20 for i in range(16)])


if __name__ == '__main__':
    unittest.main()
