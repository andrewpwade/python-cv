import fcntl
import os
import struct
from collections import namedtuple
from stat import S_ISREG, S_ISBLK

__all__ = """
get_pids
procs_by_binary_name
Process
openfile
fdinfo
""".split()

openfile = namedtuple('openfile', ['fd', 'path', 'fdinfo'])
fdinfo = namedtuple('fdinfo', ['fd', 'size', 'pos'])

def get_pids():
    return [int(e) for e in os.listdir('/proc') if e.isdigit() and os.path.isdir('/proc/'+e)]

class Process(object):
    def __init__(self, pid):
        assert pid, "pid can not false"
        assert pid > 0, "pid can not be <= 0"
        if not isinstance(pid, int):
            raise TypeError('pid must be an int')
        self.pid = pid

    @property
    def exe(self):
        """
        Returns the target of /proc/<pid>/exe or an empty string on error.
        """
        try:
            exe = os.readlink('/proc/{0}/exe'.format(self.pid))
        except (IOError, OSError):
            # FIXME: handle 'no such file' for low pids (<=20)
            # FIXME: handle /proc/<pid> not existing
            # FIXME: handle EPERM/EACCESS
            return ""
        exe = exe.split('\x00')[0]
        return exe

    @property
    def exe_name(self):
        """Returns the basename of the exe path"""
        return os.path.basename(self.exe)

    @property
    def name(self):
        try:
            with open('/proc/{0}/stat'.format(self.pid)) as f:
                return f.read().split(' ')[1].replace('(', '').replace(')', '')
        except (IOError, OSError):
            return ''

    @property
    def open_files(self):
        """
        Returns list of openfile namedtuples for regular and block files.
        """
        ret = []
        dir_list = []
        try:
            dir_list = os.listdir('/proc/{0}/fd'.format(self.pid))
        except OSError:
            return []
        for fd in dir_list:
            fd = int(fd)
            fdpath = '/proc/{0}/fd/{1}'.format(self.pid, fd)
            try:
                if os.path.islink(fdpath):
                    fdpath = os.readlink(fdpath)
                stat_buf = os.stat(fdpath)
            except OSError:
                continue
            if not (S_ISREG(stat_buf.st_mode) or S_ISBLK(stat_buf.st_mode)):
                continue

            if S_ISBLK(stat_buf.st_mode):
                with open(fdpath, 'r') as dev:
                    BLKGETSIZE64 = 0x80081272
                    buf = fcntl.ioctl(dev.fileno(), BLKGETSIZE64, ' '*8)
                    fsize = struct.unpack('L', buf)[0]
            else:
                fsize = stat_buf.st_size

            fpos = open('/proc/{0}/fdinfo/{1}'.format(self.pid, fd)).readlines()[0].strip().split("\t")[1]
            fd_info = fdinfo(fd, fsize, int(fpos))
            ret.append(openfile(fd, fdpath, fd_info))
        return ret

def procs_by_binary_name(bin_name):
    procs = []
    for pid in get_pids():
        proc = Process(pid)
        if proc.exe_name == bin_name or proc.name == bin_name:
            procs.append(proc)
    return procs
