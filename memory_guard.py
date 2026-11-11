"""
ClipForge Memory Guard & Adaptive Resource Limiter
==================================================
Protects low-RAM cloud containers (Render Free 512MB, Render Starter 512MB-2GB)
from Linux Out-Of-Memory (OOM) killer terminations.

Key Responsibilities:
1. Accurate container memory limit detection (cgroup v2, cgroup v1, /proc/meminfo, env vars).
2. Dynamic memory tier classification (TINY <= 768MB, LOW <= 1.5GB, MEDIUM <= 3GB, HIGH > 3GB).
3. Adaptive clip worker concurrency calculation (forces serial rendering on 512MB containers).
4. Adaptive FFmpeg parameters (thread clamping, queue buffers, ultrafast preset).
5. Global render semaphore to prevent multiple jobs/clips from spawning concurrent FFmpegs.
6. Memory pressure detection, automatic garbage collection, and system diagnostics.
"""

import os
import sys
import gc
import time
import threading
from contextlib import contextmanager

# Environment variable overrides
ENV_MEMORY_LIMIT_MB = os.getenv("CLIPFORGE_MEMORY_LIMIT_MB", os.getenv("MAX_MEMORY_MB", "")).strip()
ENV_FORCE_SERIAL = os.getenv("FORCE_SERIAL_RENDER", "").strip().lower() in ("1", "true", "yes")
ENV_FFMPEG_THREADS = os.getenv("FFMPEG_THREADS", "").strip()

# Memory Tier Thresholds (in Megabytes)
TIER_TINY_MAX_MB = 768.0      # Render Free (512MB)
TIER_LOW_MAX_MB = 1536.0     # Render Starter (1GB - 1.5GB)
TIER_MEDIUM_MAX_MB = 3072.0  # Medium Container (2GB - 3GB)

_memory_cache = {
    "limit_mb": None,
    "last_checked": 0.0
}
_memory_cache_lock = threading.Lock()

def _read_int_from_file(filepath):
    try:
        if os.path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                val = f.read().strip()
                if val and val != "max":
                    return int(val)
    except Exception:
        pass
    return None

def detect_container_memory_limit_mb():
    """
    Detects container memory limit in MB with priority:
    1. Explicit env var (CLIPFORGE_MEMORY_LIMIT_MB or MAX_MEMORY_MB)
    2. Linux cgroup v2 (/sys/fs/cgroup/memory.max)
    3. Linux cgroup v1 (/sys/fs/cgroup/memory/memory.limit_in_bytes)
    4. /proc/meminfo (MemTotal)
    5. Fallback to 512.0 MB (safe Render Free default)
    """
    # 1. Environment override
    if ENV_MEMORY_LIMIT_MB:
        try:
            val = float(ENV_MEMORY_LIMIT_MB)
            if val > 0:
                return val
        except ValueError:
            pass

    # 2. Cgroups v2
    cgroup2_bytes = _read_int_from_file("/sys/fs/cgroup/memory.max")
    if cgroup2_bytes and cgroup2_bytes > 0:
        return round(cgroup2_bytes / (1024.0 * 1024.0), 2)

    # 3. Cgroups v1 (Ignore ~9EB default unlimited values)
    cgroup1_bytes = _read_int_from_file("/sys/fs/cgroup/memory/memory.limit_in_bytes")
    if cgroup1_bytes and 0 < cgroup1_bytes < (100 * 1024 * 1024 * 1024 * 1024):
        return round(cgroup1_bytes / (1024.0 * 1024.0), 2)

    # 4. Use /proc/meminfo only outside container namespaces. In Docker/Render,
    # /proc often reports the host's RAM rather than the container's quota.
    try:
        in_container = os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv")
        if not in_container and os.path.exists("/proc/1/cgroup"):
            with open("/proc/1/cgroup", "r", encoding="utf-8") as f:
                cgroup_text = f.read().lower()
                in_container = any(token in cgroup_text for token in ("docker", "kubepods", "containerd", "lxc"))
        if not in_container and os.path.exists("/proc/meminfo"):
            with open("/proc/meminfo", "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        parts = line.split()
                        if len(parts) >= 2:
                            kb = float(parts[1])
                            return round(kb / 1024.0, 2)
    except Exception:
        pass

    # 5. Default to Render Free's memory tier rather than risk trusting host RAM.
    return 512.0

def get_container_memory_limit_mb():
    """Cached memory limit reader."""
    now = time.time()
    with _memory_cache_lock:
        if _memory_cache["limit_mb"] is None or (now - _memory_cache["last_checked"] > 60.0):
            _memory_cache["limit_mb"] = detect_container_memory_limit_mb()
            _memory_cache["last_checked"] = now
        return _memory_cache["limit_mb"]

def get_available_memory_mb():
    """
    Computes available memory in MB for the container.
    Takes the minimum of container cgroup headroom and system MemAvailable.
    """
    limit_mb = get_container_memory_limit_mb()
    headroom_mb = limit_mb

    # Check cgroup current usage
    usage_bytes = _read_int_from_file("/sys/fs/cgroup/memory.current")
    if usage_bytes is None:
        usage_bytes = _read_int_from_file("/sys/fs/cgroup/memory/memory.usage_in_bytes")

    if usage_bytes is not None and usage_bytes > 0:
        used_mb = usage_bytes / (1024.0 * 1024.0)
        headroom_mb = max(0.0, limit_mb - used_mb)

    # Check system MemAvailable from /proc/meminfo
    sys_avail_mb = limit_mb
    try:
        if os.path.exists("/proc/meminfo"):
            with open("/proc/meminfo", "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("MemAvailable:"):
                        parts = line.split()
                        if len(parts) >= 2:
                            sys_avail_mb = float(parts[1]) / 1024.0
                            break
    except Exception:
        pass

    return round(min(headroom_mb, sys_avail_mb), 2)

def get_memory_tier(limit_mb=None):
    """
    Classifies memory tier into:
    - TINY: <= 768MB (e.g. Render Free 512MB) -> Strict serial, 1 FFmpeg thread
    - LOW: <= 1536MB (1GB - 1.5GB) -> Serial or bounded 2 workers
    - MEDIUM: <= 3072MB (2GB - 3GB) -> Up to 2 workers
    - HIGH: > 3072MB -> Up to 4 workers
    """
    limit = limit_mb if limit_mb is not None else get_container_memory_limit_mb()
    if limit <= TIER_TINY_MAX_MB:
        return "TINY"
    elif limit <= TIER_LOW_MAX_MB:
        return "LOW"
    elif limit <= TIER_MEDIUM_MAX_MB:
        return "MEDIUM"
    return "HIGH"

def get_adaptive_clip_workers(requested_workers=None, export_profile="540p", options=None, limit_mb=None):
    """
    Computes safe worker count for clip rendering.
    On low-RAM containers (512MB), clamps to 1 to guarantee zero OOM restarts.
    """
    # Explicit force serial override
    if ENV_FORCE_SERIAL or (options and options.get("force_serial")):
        return 1

    tier = get_memory_tier(limit_mb)
    is_1080p = str(export_profile).lower() in ("1080p", "hd", "studio")

    req = requested_workers
    if req is None and options:
        req = options.get("max_workers")
    try:
        req_val = int(req) if req is not None else 2
    except (ValueError, TypeError):
        req_val = 2

    if tier == "TINY":
        # 512MB container: STRICTLY 1 worker to prevent memory spikes
        return 1
    elif tier == "LOW":
        # 1GB - 1.5GB container: 1 worker for 1080p, max 2 for 540p
        if is_1080p:
            return 1
        return max(1, min(2, req_val))
    elif tier == "MEDIUM":
        # 2GB container: max 2 workers
        return max(1, min(2, req_val))
    else:
        # HIGH memory (> 3GB): allow up to 4 workers
        return max(1, min(4, req_val))

def get_adaptive_ffmpeg_params(export_profile="540p", limit_mb=None):
    """
    Returns memory-optimized FFmpeg CLI parameters tailored for container limits.
    """
    if ENV_FFMPEG_THREADS:
        threads = str(max(1, min(4, int(ENV_FFMPEG_THREADS))))
    else:
        tier = get_memory_tier(limit_mb)
        is_1080p = str(export_profile).lower() in ("1080p", "hd", "studio")
        if tier == "TINY":
            threads = "1"
        elif tier == "LOW":
            threads = "1" if is_1080p else "2"
        else:
            threads = "2"

    return {
        "threads": threads,
        "preset": "ultrafast" if get_memory_tier(limit_mb) in ("TINY", "LOW") else "veryfast",
        "max_muxing_queue_size": "1024",
        "cli_flags": [
            "-threads", threads,
            "-max_muxing_queue_size", "1024"
        ]
    }

def enforce_gc(reason=""):
    """Explicitly triggers garbage collection to free lingering memory buffers."""
    try:
        collected = gc.collect()
        if reason and collected > 0:
            print(f"[MEMORY] Enforced GC ({reason}): reclaimed {collected} objects.")
    except Exception:
        pass

def check_memory_pressure(threshold_mb=120.0):
    """
    Checks if remaining container headroom is critically low.
    Automatically triggers garbage collection when pressure is detected.
    """
    avail = get_available_memory_mb()
    is_pressure = avail < threshold_mb
    if is_pressure:
        enforce_gc("critical memory pressure")
    return is_pressure

class GlobalRenderLimiter:
    """
    Process-wide semaphore that prevents simultaneous FFmpeg instances across
    multiple jobs from overwhelming container memory.
    """
    _instance = None
    _lock = threading.Lock()

    def __init__(self):
        self._max_slots = self._compute_max_slots()
        self._semaphore = threading.BoundedSemaphore(self._max_slots)
        self._active_count = 0
        self._count_lock = threading.Lock()

    @classmethod
    def get_instance(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def _compute_max_slots(self):
        tier = get_memory_tier()
        if tier == "TINY":
            return 1
        elif tier == "LOW":
            return 1
        elif tier == "MEDIUM":
            return 2
        return 4

    @property
    def max_slots(self):
        return self._max_slots

    @property
    def active_slots(self):
        with self._count_lock:
            return self._active_count

    @contextmanager
    def acquire_slot(self, timeout=None):
        acquired = self._semaphore.acquire(timeout=timeout if timeout is not None else -1)
        if not acquired:
            raise TimeoutError("Timed out waiting for render slot")
        with self._count_lock:
            self._active_count += 1
        try:
            yield
        finally:
            with self._count_lock:
                self._active_count = max(0, self._active_count - 1)
            self._semaphore.release()
            enforce_gc("render slot released")

def acquire_render_slot(timeout=None):
    """Context manager for acquiring a global render slot."""
    return GlobalRenderLimiter.get_instance().acquire_slot(timeout=timeout)

def get_memory_status():
    """
    Returns comprehensive system memory status for API and diagnostics.
    """
    limit_mb = get_container_memory_limit_mb()
    avail_mb = get_available_memory_mb()
    used_mb = max(0.0, round(limit_mb - avail_mb, 2))
    tier = get_memory_tier(limit_mb)
    limiter = GlobalRenderLimiter.get_instance()

    tier_labels = {
        "TINY": "Render Free 512MB (Safe Serial Mode)",
        "LOW": "Render Starter 1GB-1.5GB (Adaptive Guard)",
        "MEDIUM": "Render Standard 2GB",
        "HIGH": "High Performance Server"
    }

    oom_risk = "low"
    if avail_mb < 80.0:
        oom_risk = "critical"
    elif avail_mb < 140.0:
        oom_risk = "medium"

    return {
        "limit_mb": limit_mb,
        "used_mb": used_mb,
        "available_mb": avail_mb,
        "tier": tier,
        "tier_description": tier_labels.get(tier, tier),
        "adaptive_workers": get_adaptive_clip_workers(limit_mb=limit_mb),
        "adaptive_threads": int(get_adaptive_ffmpeg_params(limit_mb=limit_mb)["threads"]),
        "active_render_slots": limiter.active_slots,
        "max_render_slots": limiter.max_slots,
        "low_memory_mode": tier in ("TINY", "LOW"),
        "oom_risk": oom_risk
    }
