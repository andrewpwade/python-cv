"""
CV
"""
from __future__ import print_function

from collections import namedtuple, defaultdict, deque
import itertools
import os
import sys
import argparse
import signal
from time import sleep, time
from stat import *
import curses
import fcntl

APP_NAME = "cv"
PROC_PATH = "/proc"
PROC_NAMES = ["cp", "mv", "dd", "tar", "gzip", "gunzip", "cat", "grep", "fgrep", "egrep", "cut", "sort", "rsync"]
MAX_PIDS = 32
MAX_FD_PER_PID = 512
THROUGHPUT_SAMPLE_SIZE = 3

class PidInfo(object):
    def __init__(self, pid=None, name=None):
        self.pid = pid
        self.name = name
    def __str__(self):
        return str(self.__dict__)

class Result(object):
    def __init__(self):
        self.pid = None
        self.fd = None
        self.hbegin = None
        self.hlist = None
        self.hsize = None
    def __str__(self):
        return str(self.__dict__)

class FdInfo(object):
    def __init__(self):
        self.num = None
        self.size = 0
        self.pos = 0
        self.name = None
        self.tv = None
    def __str__(self):
        return str(self.__dict__)
    
class AppConfig(object):
    curses = False
    throughput = False
    quiet = False
    monitor = False
    monitor_continuous = False
    throughput_wait_secs = 1
    proc_names = []

config = AppConfig()

def moving_average(iterable, n=3):
    # moving_average([40, 30, 50, 46, 39, 44]) --> 40.0 42.0 45.0 43.0
    # http://en.wikipedia.org/wiki/Moving_average
    it = iter(iterable)
    d = deque(itertools.islice(it, n-1))
    d.appendleft(0)
    s = sum(d)
    for elem in it:
        s += elem - d.popleft()
        d.append(elem)
        yield s / float(n)

def format_size(n):
    # source: http://stackoverflow.com/questions/1094841/reusable-library-to-get-human-readable-version-of-file-size
    fmt = "%3.1f %s"
    for x in 'bytes','KB','MB','GB':
        if n < 1024.0 and n > -1024.0:
            return fmt % (n, x)
        n /= 1024.0
    return fmt % (n, 'TB')

def nprint(mainwin, str):
    if config.monitor or config.monitor_continuous:
        mainwin.addstr(str)
    else:
        print(str, end="")

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
    except OSError as e:
        return []
    for fd in dir_list:
        fd = int(fd)
        fdpath = '/proc/{0}/fd/{1}'.format(pid, fd)
        try:
            st_mode = os.stat(fdpath).st_mode
        except OSError as e:
            print(e, file=sys.stderr)
            continue
        if not (S_ISREG(st_mode) or S_ISBLK(st_mode)):
            continue
        fds.append(fd)
        if max_fd is not None and len(fds) == max_fd:
            break
    return fds

def get_fdinfo(pid, fdnum):
    fdpath = "%s/%d/fd/%d" % (PROC_PATH, pid, fdnum)

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
    fdinfo.tv = time()
    fdinfo.pos = int(open("%s/%d/fdinfo/%d" % (PROC_PATH, pid, fdnum)).readlines()[0].strip().split("\t")[1])
    return fdinfo

def parse_options():
    """
    Usage: ./cv [-vqwmMh] [-W] [-c command]
  -v --version            show version
  -q --quiet              hides some warning/error messages
  -w --wait               estimate I/O throughput and ETA (slower display)
  -W --wait-delay secs    wait 'secs' seconds for I/O estimation (implies -w, default=1.0)
  -m --monitor            loop while monitored processes are still running
  -M --monitor-continuous  like monitor but never stop (similar to watch ./cv)
  -h --help               this message
  -c --command cmd        monitor only this command name (ex: firefox)
  """
    ap = argparse.ArgumentParser(description=APP_NAME)
    ap.add_argument('-v', '--version', action='store_true', help='show version')
    ap.add_argument('-q', '--quiet', action='store_true', help='hides some warning/error messages')
    ap.add_argument('-w', '--wait', action='store_true', help='estimate I/O throughput and ETA (slower display)')
    ap.add_argument('-W', '--wait-delay', type=int, metavar='<secs>', help='''wait 'secs' seconds for I/O estimation (implies -w, default=1.0)''')
    ap.add_argument('-c', '--commands', action='append', help='commands to monitor')
    ap.add_argument('-m', '--monitor', action='store_true', help='loop while monitored processes are still running')
    ap.add_argument('-M', '--monitor-continuous', action='store_true', help='like monitor but never stop (similar to watch ./cv)')
    args = ap.parse_args()

    # -w implies -W
    if args.wait_delay is not None:
        args.wait = True
    return args

def monitor_processes(throughputs=None, mainwin=None):
    """
    Find pids whose binary names (i.e. basename of path) match those in config.proc_name.

    """
    if throughputs is None:
        raise ValueError("throughputs is None")
    if not config.proc_names:
        raise ValueError("no proc names defined")
    fd_count = 0
    result_count = 0
    results = []

    pidinfos = []

    for name in config.proc_names:
        p = find_pids_by_binary_name(name)
        if p:
            pidinfos.extend(p)

    pidinfos = pidinfos[:MAX_PIDS]
    if not pidinfos:
        if config.quiet:
            return 0
        if config.curses:
            mainwin.clear()
            mainwin.refresh()
        nprint(mainwin, "No command currently running: %s. exiting" % (", ".join(config.proc_names)))
        return 0

    for pidinfo in pidinfos:
        fds = find_fd_for_pid(pidinfo.pid)
        fds = fds[:MAX_FD_PER_PID]
        fd_size_max = 0
        fd_biggest = 0
        if not fds:
            nprint(mainwin, "[%5d] %s inactive/flushing/streaming/...\n" % (pidinfo.pid, pidinfo.name))
            # FIXME: why is this needed here?
            if config.curses:
                mainwin.refresh()
            continue
        for fd in fds:
            fd_info = get_fdinfo(pidinfo.pid, fd)
            if fd_info.size > fd_size_max:
                fd_size_max = fd_info.size
                fd_biggest = fd_info

        # We've got our biggest_fd now, let's store the result
        result = Result()
        result.pid = pidinfo
        result.fd = fd_biggest
        result.hbegin = None
        result.hend = None
        result.hsize = 0
        results.append(result)

    # wait a bit, so we can estimate throughput
    if config.throughput:
        sleep(config.throughput_wait_secs)
    if config.curses:
        mainwin.clear()
        mainwin.refresh()
    
    for result in results:
        progress_pcnt = 0
        cur_fd_info = None

        fd = result.fd
        if config.throughput:
            cur_fd_info = get_fdinfo(result.pid.pid, result.fd.num)
            if cur_fd_info.name == result.fd.name:
                fd = cur_fd_info
            else:
                cur_fd_info = None

        if fd.pos > 0.0 and fd.size > 0.0:
            progress_pcnt = float(fd.pos)/fd.size
        nprint(mainwin, "[%5d] %s %s %.1f%% (%s / %s)" % (
            result.pid.pid,
            result.pid.name,
            fd.name,
            progress_pcnt,
            format_size(float(fd.pos)),
            format_size(float(fd.size))))

        if config.throughput and cur_fd_info:
            bytes_per_sec = 0
            sec_diff = float(fd.tv) - result.fd.tv
            byte_diff = fd.pos - result.fd.pos
            tkey = (result.pid.pid, fd.num)
            throughputs[tkey] = throughputs[tkey][:THROUGHPUT_SAMPLE_SIZE-1]
            throughputs[tkey].append(byte_diff/sec_diff)
            throughput_moving_avg = list(moving_average(throughputs[tkey]))
            if throughput_moving_avg:
                bytes_per_sec = throughput_moving_avg.pop()
            nprint(mainwin, " %s/s" % format_size(bytes_per_sec))
        nprint(mainwin, "\n")

    return results

def make_config():
    args = parse_options()
    if args.quiet:
        config.quiet = True
    if args.commands:
        config.proc_names = args.commands
    else:
        config.proc_names = PROC_NAMES
    if args.wait:
        config.throughput = True
    if args.monitor:
        config.monitor = True
    if args.monitor_continuous:
        config.monitor_continuous = True
    if args.wait_delay:
        config.throughput = True
        config.throughput_wait_secs = int(args.wait_delay)
    else:
        config.throughput_wait_secs = 1
    if config.monitor or config.monitor_continuous:
        config.curses = True

def endwin(window):
    if not window:
        return
    curses.nocbreak()
    window.keypad(0)
    curses.echo()
    curses.endwin()

def main():
    make_config()
    mainwin = None

    if config.curses:
        mainwin = curses.initscr()

    def int_handler(signum, frame):
        if mainwin is not None:
            try:
                endwin(mainwin)
            except curses.error as ce:
                print(ce, file=sys.stderr)
        sys.exit(0)
    signal.signal(signal.SIGINT, int_handler)

    throughputs = defaultdict(list)
    try:
        if config.monitor or config.monitor_continuous:
            results = []
            while True:
                results = monitor_processes(mainwin=mainwin, throughputs=throughputs)
                mainwin.refresh()
                if config.monitor_continuous and not results:
                    sleep(config.throughput_wait_secs)
                if not ((config.monitor and results) or config.monitor_continuous):
                    break
        else:
            monitor_processes(throughputs=throughputs)
    finally:
        try:
            if mainwin:
                endwin(mainwin)
        except curses.error as ce:
            print(ce, file=sys.stderr)

if __name__ == '__main__':
    main()
