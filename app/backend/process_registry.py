"""Live handles for the processes tasks were started as.

The watcher needs the Popen object, not just the pid. Python's subprocess
module reaps any Popen that was garbage-collected while still running --
every new ``Popen(...)`` call runs ``subprocess._cleanup()`` -- so a task
whose handle we dropped could be reaped before the watcher next polled it.
``os.waitpid`` then raised ChildProcessError, which was recorded as exit
-1: a task that had in fact succeeded was marked Failed and its workflow
stopped. It only showed up once two workflows ran at once, because every
extra dispatch is another chance for that cleanup to run.

Holding the Popen keeps it out of that cleanup and lets us read the real
exit status with ``poll()``.
"""

import threading

_lock = threading.Lock()
_processes = {}


def remember(pid, process):
    """Keep the handle for a task we just started."""
    with _lock:
        _processes[pid] = process


def get(pid):
    """The handle for this pid, or None if we did not start it."""
    with _lock:
        return _processes.get(pid)


def forget(pid):
    """Drop the handle once the task is finished with."""
    with _lock:
        return _processes.pop(pid, None)
