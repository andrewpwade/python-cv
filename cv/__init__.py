"""
CV
"""
from __future__ import print_function
from collections import defaultdict
import sys
import argparse
import signal
from time import sleep, time
import curses

from procutil import find_pids_by_binary_name, find_fd_for_pid, get_fdinfo
from util import format_size, moving_average

APP_NAME = "cv"
PROC_NAMES = ["cp", "mv", "dd", "tar", "gzip", "gunzip", "cat", "grep", "fgrep", "egrep", "cut", "sort", "rsync"]
MAX_PIDS = 32
MAX_FD_PER_PID = 512
THROUGHPUT_SAMPLE_SIZE = 3

class Result(object):
    def __init__(self):
        self.pid = None
        self.fd = None
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

class Main(object):
    def nprint(self, str):
        if self.config.curses:
            self.mainwin.addstr(str)
        else:
            print(str, end="")

    def parse_options(self):
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

    def monitor_processes(self):
        """
        Find pids whose binary names (i.e. basename of path) match those in config.proc_name.

        """
        if not self.config.proc_names:
            raise ValueError("no proc names defined")
        results = []

        pidinfos = []

        for name in self.config.proc_names:
            p = find_pids_by_binary_name(name)
            if p:
                pidinfos.extend(p)

        pidinfos = pidinfos[:MAX_PIDS]
        if not pidinfos:
            if self.config.quiet:
                return 0
            if self.config.curses:
                self.mainwin.clear()
                self.mainwin.refresh()
            self.nprint("No command currently running: %s. exiting" % (", ".join(self.config.proc_names)))
            return 0

        for pidinfo in pidinfos:
            fds = find_fd_for_pid(pidinfo.pid)
            fds = fds[:MAX_FD_PER_PID]
            if not fds:
                self.nprint("[%5d] %s inactive/flushing/streaming/...\n" % (pidinfo.pid, pidinfo.name))
                # FIXME: why is this needed here?
                if self.config.curses:
                    self.mainwin.refresh()
                continue

            fd_infos = [get_fdinfo(pidinfo.pid, fd) for fd in fds]
            fd_biggest = sorted(fd_infos, key=lambda fdinfo: fdinfo.size)[-1]

            # We've got our biggest_fd now, let's store the result
            results.append((pidinfo, fd_biggest))

        # wait a bit, so we can estimate throughput
        if self.config.throughput:
            sleep(self.config.throughput_wait_secs)
        if self.config.curses:
            self.mainwin.clear()
            self.mainwin.refresh()

        for pidinfo, fd_stale in results:
            progress_pcnt = 0
            fd = None
            if self.config.throughput:
                fd = get_fdinfo(pidinfo.pid, fd.num)

            if fd_stale.pos > 0.0 and fd_stale.size > 0.0:
                progress_pcnt = float(fd_stale.pos)/fd_stale.size
            self.nprint("[%5d] %s %s %.1f%% (%s / %s)" % (
                pidinfo.pid,
                pidinfo.name,
                fd_stale.name,
                progress_pcnt,
                format_size(float(fd_stale.pos)),
                format_size(float(fd_stale.size))))

            if self.config.throughput and fd is not None:
                bytes_per_sec = 0
                sec_diff = float(fd.tv) - fd.tv
                byte_diff = fd.pos - fd_stale.pos
                tkey = (pidinfo.pid, fd_stale.num)
                self.throughputs[tkey] = self.throughputs[tkey][:THROUGHPUT_SAMPLE_SIZE-1]
                self.throughputs[tkey].append(byte_diff/sec_diff)
                throughput_moving_avg = list(moving_average(self.throughputs[tkey]))
                if throughput_moving_avg:
                    bytes_per_sec = throughput_moving_avg.pop()
                self.nprint(" %s/s" % format_size(bytes_per_sec))
            self.nprint("\n")

        return results

    def make_config(self):
        config = AppConfig()
        args = self.parse_options()
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
        return config

    def endwin(self):
        if not self.mainwin:
            return
        curses.nocbreak()
        self.mainwin.keypad(0)
        curses.echo()
        curses.endwin()

    def int_handler(self, signum, frame):
        if self.mainwin is not None:
            try:
                self.endwin()
            except curses.error as ce:
                print(ce, file=sys.stderr)
        sys.exit(0)

    def main(self):
        self.config = self.make_config()

        if self.config.curses:
            self.mainwin = curses.initscr()

        signal.signal(signal.SIGINT, self.int_handler)

        try:
            if self.config.monitor or self.config.monitor_continuous:
                results = []
                while True:
                    results = self.monitor_processes()
                    if self.config.curses:
                        self.mainwin.refresh()
                    if self.config.monitor_continuous and not results:
                        sleep(self.config.throughput_wait_secs)
                    if not ((self.config.monitor and results) or self.config.monitor_continuous):
                        break
            else:
                self.monitor_processes()
        finally:
            try:
                if self.mainwin:
                    self.endwin()
            except curses.error as ce:
                print(ce, file=sys.stderr)

    def __init__(self):
        self.config = None
        self.mainwin = None
        self.throughputs = defaultdict(list)
