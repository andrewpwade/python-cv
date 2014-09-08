import unittest
import subprocess
import os
from tempfile import NamedTemporaryFile
from cv.procutil import *

class TestProcutil(unittest.TestCase):
    def setUp(self):
        self.devnull = open(os.devnull, 'wb')

    def test_get_pids(self):
        # PID 1 = init
        self.assertTrue(1 in get_pids())
        

        # spawn some processes as it's more reliable than comparing
        # "ps" output (race conditions)
        procs = [subprocess.Popen(["/bin/sleep", "1000"], stdout=self.devnull, stderr=self.devnull) for _ in range(10)]
        try:
            for p in procs:
                self.assertIn(p.pid, get_pids())
        finally:
            for p in procs:
                p.kill()

    def test_procs_by_binary_name(self):
        for n in ["init", "python"]:
            procs = procs_by_binary_name(n)
            self.assertIn(n, [p.name for p in procs])

    def test_process_invalid_pid(self):
        self.assertRaises(AssertionError, Process, -1)
        self.assertRaises(AssertionError, Process, 0)
        self.assertRaises(TypeError, Process, 'sausage')
        self.assertRaises(TypeError, Process, 1.0)        

    def test_process(self):
        p = Process(1)
        self.assertEquals(p.pid, 1)
        self.assertEquals(p.name, "init")

        # this test suite should not be running as root, so permission
        # will be denied to dir /proc/1/
        if not os.access("/proc/1/exe", os.R_OK):
            self.assertEquals(p.exe_name, "")
            self.assertEquals(p.exe, "")

    def test_open_files(self):
        # this test suite should not be running as root, so permission
        # will be denied to dir /proc/1/
        p = Process(1)
        if not os.access('/proc/1/fd', os.R_OK):
            self.assertEquals(p.open_files, [])

        tempfiles = []
        try:
            p = Process(os.getpid())
            [tempfiles.append(NamedTemporaryFile()) for _ in range(10)]
            for tf in tempfiles:
                ofs = p.open_files
                self.assert_(ofs[0].fd)
                self.assert_(ofs[0].path)
                self.assert_(tf.name in [f.path for f in ofs])
        finally:
            for tf in tempfiles:
                tf.close()

    def test_fdinfo(self):
        tf = None
        try:
            p = Process(os.getpid())
            tf = NamedTemporaryFile()
            ofs = p.open_files
            self.assertEqual(ofs[0].fdinfo.fd, tf.file.fileno())
            self.assertEqual(ofs[0].fdinfo.size, 0)
            self.assertEqual(ofs[0].fdinfo.pos, 0)
        finally:
            if tf is not None:
                tf.close()
        
