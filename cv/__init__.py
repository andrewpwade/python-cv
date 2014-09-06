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

from procutil import find_pids_by_binary_name, find_fd_for_pid, get_fdinfo, procs_by_binary_name
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
        timestamps = dict()
        procs = []
        out = ""

        for name in self.config.proc_names:
            procs.extend(procs_by_binary_name(name))
            if len(procs) >= MAX_PIDS:
                procs = procs[:MAX_PIDS]
                break

        if not procs:
            if self.config.quiet:
                return results, ""
            return results, "No command currently running: %s. exiting\n" % (", ".join(self.config.proc_names))

        for proc in procs:
            open_files = proc.open_files
            open_files = open_files[:MAX_FD_PER_PID]
            if not open_files:
                continue
            fd_biggest = sorted(open_files, key=lambda x: x.fdinfo.size)[-1]
            timestamps[(proc.pid, fd_biggest.fd)] = time()
            results.append((proc, fd_biggest))

        # wait a bit, so we can estimate throughput
        if self.config.throughput:
            sleep(self.config.throughput_wait_secs)

        for proc, fd_stale in results:
            progress_pcnt = 0
            fd = None
            if self.config.throughput:
                open_files = proc.open_files
                newfd = next((x for x in open_files if x.fd == fd_stale.fd), None)
                if newfd and newfd.path == fd_stale.path:
                    fd = newfd

            if fd and fd_stale.fdinfo.pos > 0.0 and fd_stale.fdinfo.size > 0.0:
                progress_pcnt = float(fd.fdinfo.pos)/fd.fdinfo.size
            else:
                progress_pcnt = float(fd_stale.fdinfo.pos)/fd_stale.fdinfo.size

            out += "[%5d] %s %s %.1f%% (%s / %s)" % (
                proc.pid,
                proc.exe_name,
                fd_stale.path,
                progress_pcnt*100,
                format_size(float(fd_stale.fdinfo.pos)),
                format_size(float(fd_stale.fdinfo.size)))

            if self.config.throughput and fd is not None:
                bytes_per_sec = 0
                sec_diff = float(time()) - timestamps[(proc.pid, fd_stale.fd)]
                byte_diff = fd.fdinfo.pos - fd_stale.fdinfo.pos
                tkey = (proc.pid, fd_stale.fd)
                self.throughputs[tkey] = self.throughputs[tkey][:THROUGHPUT_SAMPLE_SIZE-1]
                self.throughputs[tkey].append(byte_diff/sec_diff)
                throughput_moving_avg = list(moving_average(self.throughputs[tkey]))
                if throughput_moving_avg:
                    bytes_per_sec = throughput_moving_avg.pop()
                out += " %s/s" % format_size(bytes_per_sec)
            out += "\n"

        return results, out

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
            results = []
            while True:
                results, text = self.monitor_processes()
                if self.config.curses:
                    self.mainwin.clear()
                if text:
                    self.nprint(text)
                if self.config.curses:
                    self.mainwin.refresh()
                if self.config.monitor_continuous and not results:
                    sleep(self.config.throughput_wait_secs)
                if not ((self.config.monitor and results) or self.config.monitor_continuous):
                    break
                if not (self.config.monitor or self.config.monitor_continuous):
                    break
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
