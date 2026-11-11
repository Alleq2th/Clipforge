import os
import signal
import threading
import subprocess
from concurrent.futures import ThreadPoolExecutor
from memory_guard import get_memory_tier, acquire_render_slot

def _get_default_max_jobs():
    raw = os.getenv("MAX_CONCURRENT_JOBS", "").strip()
    try:
        requested = max(1, int(raw)) if raw else 2
    except ValueError:
        requested = 2
    # Hard cap concurrent whole jobs by detected RAM tier, even when env asks for more.
    tier = get_memory_tier()
    safe_cap = 1 if tier in ("TINY", "LOW") else (2 if tier == "MEDIUM" else 4)
    return min(requested, safe_cap)

# Maximum concurrent video processing pipelines on Render container
MAX_CONCURRENT_JOBS = _get_default_max_jobs()

_active_processes_lock = threading.Lock()
# Mapping of jid -> set of active subprocess.Popen instances
_active_processes = {}

_executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_JOBS, thread_name_prefix="clipforge-worker")

def submit_job(fn, *args, **kwargs):
    """Submits a job to the bounded thread pool."""
    return _executor.submit(fn, *args, **kwargs)

def register_process(jid, proc):
    """Registers an active child process (ffmpeg, yt-dlp) for a specific job."""
    if not jid or not proc:
        return
    with _active_processes_lock:
        if jid not in _active_processes:
            _active_processes[jid] = set()
        _active_processes[jid].add(proc)

def unregister_process(jid, proc):
    """Unregisters a process after normal exit."""
    if not jid or not proc:
        return
    with _active_processes_lock:
        if jid in _active_processes:
            _active_processes[jid].discard(proc)
            if not _active_processes[jid]:
                del _active_processes[jid]

def terminate_job_processes(jid):
    """Terminates all active processes for a cancelled job."""
    if not jid:
        return
    with _active_processes_lock:
        procs = list(_active_processes.get(jid, []))

    for p in procs:
        try:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    p.kill()
        except Exception as e:
            print(f"[PROCESS] Notice while terminating process for job {jid}: {e}")

    with _active_processes_lock:
        _active_processes.pop(jid, None)

def run_tracked_subprocess(cmd, jid=None, cwd=None, check=True, capture_output=True, timeout=None):
    """Runs a tracked subprocess. FFmpeg calls share a global memory-bounded render slot."""
    executable = os.path.basename(str(cmd[0])).lower() if cmd else ""
    is_ffmpeg = executable in ("ffmpeg", "ffmpeg.exe")

    def _run():
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE if capture_output else None,
            stderr=subprocess.PIPE if capture_output else None
        )
        if jid:
            register_process(jid, proc)

        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            retcode = proc.poll()
            if check and retcode != 0:
                raise subprocess.CalledProcessError(retcode, cmd, output=stdout, stderr=stderr)
            return subprocess.CompletedProcess(cmd, retcode, stdout, stderr)
        finally:
            if jid:
                unregister_process(jid, proc)

    if is_ffmpeg:
        with acquire_render_slot():
            return _run()
    return _run()
