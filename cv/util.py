import itertools
from collections import deque

__all__ = """
format_size
moving_average
""".split()

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
