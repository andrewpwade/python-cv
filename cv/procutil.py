import fcntl
import os
import time
from stat import *

__all__ = """
find_fd_for_pid
find_pids_by_binary_name
get_fdinfo
get_pids
""".split()

PROC_PATH = '/proc'

class FdInfo(object):
    def __init__(self):
        self.num = None
        self.size = 0
        self.pos = 0
        self.name = None
        self.tv = None
    def __str__(self):
        return str(self.__dict__)

class PidInfo(object):
    def __init__(self, pid=None, name=None):
        self.pid = pid
        self.name = name
    def __str__(self):
        return str(self.__dict__)

def get_pids():
    return [e for e in os.listdir('/proc') if e.isdigit() and os.path.isdir('/proc/'+e)]

def find_pids_by_binary_name(bin_name, max_pids=None):
    """
    Returns a list of PidInfo objects
    """
    if max_pids is not None and max_pids < 0:
        raise ValueError('limit must be non-negative')
    if not bin_name:
        raise ValueError('invalid bin_name')
    pids = get_pids()
    pid_count = 0
    ret = []
    for pid in pids:
        try:
            exe = os.readlink('/proc/{0}/exe'.format(pid))
        except OSError:
            # Usually permission denied
            continue
        if os.path.basename(exe) == bin_name:
            ret.append(PidInfo(int(pid), os.path.basename(bin_name)))
            pid_count += 1
            if max_pids is not None and pid_count == max_pids:
                break
    return ret

def find_fd_for_pid(pid, max_fd=None):
    """
    Returns a list of a process' file descriptors.

    If the pid does not exist, or permission is denied, an empty list is returned.
    """
    fds = []
    dir_list = []
    try:
        dir_list = os.listdir('/proc/{0}/fd'.format(pid))
    except OSError:
        return []
    for fd in dir_list:
        fd = int(fd)
        fdpath = '/proc/{0}/fd/{1}'.format(pid, fd)
        try:
            st_mode = os.stat(fdpath).st_mode
        except OSError:
            continue
        if not (S_ISREG(st_mode) or S_ISBLK(st_mode)):
            continue
        fds.append(fd)
        if max_fd is not None and len(fds) == max_fd:
            break
    return fds

def get_fdinfo(pid, fdnum, proc_path=PROC_PATH):
    assert proc_path
    fdpath = "%s/%d/fd/%d" % (proc_path, pid, fdnum)

    fdinfo = FdInfo()
    fdinfo.num = fdnum
    fdinfo.name = os.readlink(fdpath)

    stat_buf = os.stat(fdpath)
    if S_ISBLK(stat_buf.st_mode):
        with open(fdinfo.name, 'r') as dev:
            BLKGETSIZE64 = 0x80081272
            buf = fcntl.ioctl(dev.fileno(), BLKGETSIZE64, ' '*8)
            fdinfo.size = struct.unpack('L', buf)[0]
    else:
        fdinfo.size = stat_buf.st_size

    fdinfo.pos = 0
    fdinfo.tv = time.time()
    fdinfo.pos = int(open("%s/%d/fdinfo/%d" % (proc_path, pid, fdnum)).readlines()[0].strip().split("\t")[1])
    return fdinfo
