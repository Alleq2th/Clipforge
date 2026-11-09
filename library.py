import os
import json
import time
import shutil
import threading
from config import WORK_DIR, OUT_DIR
from security import safe_join_path

_lock = threading.Lock()

def _get_lib_path():
    os.makedirs(WORK_DIR, exist_ok=True)
    return os.path.join(WORK_DIR, "library.json")

def get_library():
    path = _get_lib_path()
    if not os.path.exists(path):
        return []
    with _lock:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
                return []
        except Exception:
            return []

def save_library_entry(entry):
    if not entry or not entry.get("id"):
        return
    path = _get_lib_path()
    with _lock:
        lib = []
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    lib = json.load(f)
                    if not isinstance(lib, list):
                        lib = []
            except Exception:
                lib = []

        entry["updated_at"] = time.time()
        idx = next((i for i, x in enumerate(lib) if str(x.get("id")) == str(entry["id"])), None)
        if idx is not None:
            if "created_at" not in entry and "created_at" in lib[idx]:
                entry["created_at"] = lib[idx]["created_at"]
            lib[idx] = entry
        else:
            if "created_at" not in entry:
                entry["created_at"] = time.time()
            lib.insert(0, entry)

        # Cap library at 60 items to manage storage
        lib = lib[:60]
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(lib, f, indent=2)
        except Exception as e:
            print(f"[LIBRARY] Error saving library.json: {e}")

def delete_library_entry(entry_id):
    path = _get_lib_path()
    with _lock:
        if not os.path.exists(path):
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                lib = json.load(f)
            new_lib = [x for x in lib if str(x.get("id")) != str(entry_id)]
            with open(path, "w", encoding="utf-8") as f:
                json.dump(new_lib, f, indent=2)

            # Safely clean up disk folders with path traversal guards
            for folder_rel in [str(entry_id), os.path.join("faceless", str(entry_id))]:
                try:
                    target_dir = safe_join_path(OUT_DIR, folder_rel)
                    if os.path.exists(target_dir):
                        shutil.rmtree(target_dir, ignore_errors=True)
                except Exception:
                    pass

            # Also clean work folder if any exists
            try:
                work_job_dir = safe_join_path(WORK_DIR, "jobs", str(entry_id))
                if os.path.exists(work_job_dir):
                    shutil.rmtree(work_job_dir, ignore_errors=True)
            except Exception:
                pass

            return True
        except Exception as e:
            print(f"[LIBRARY] Error deleting library entry {entry_id}: {e}")
            return False

def delete_library_clip(entry_id, filename):
    path = _get_lib_path()
    with _lock:
        if not os.path.exists(path):
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                lib = json.load(f)
            entry = next((x for x in lib if str(x.get("id")) == str(entry_id)), None)
            if not entry:
                return False

            entry_type = entry.get("type", "clipper")
            clean_filename = os.path.basename(filename)

            if entry_type == "faceless":
                folder = safe_join_path(OUT_DIR, "faceless", str(entry_id))
            else:
                folder = safe_join_path(OUT_DIR, str(entry_id))

            file_p = safe_join_path(folder, clean_filename)
            if os.path.exists(file_p):
                try: os.remove(file_p)
                except Exception: pass

            entry["clips"] = [c for c in entry.get("clips", []) if os.path.basename(c.get("file", "")) != clean_filename]
            entry["clips_count"] = len(entry["clips"])
            with open(path, "w", encoding="utf-8") as f:
                json.dump(lib, f, indent=2)
            return True
        except Exception as e:
            print(f"[LIBRARY] Error deleting clip {filename} in {entry_id}: {e}")
            return False

def clear_completed_entries():
    path = _get_lib_path()
    with _lock:
        if not os.path.exists(path):
            return 0
        try:
            with open(path, "r", encoding="utf-8") as f:
                lib = json.load(f)
            kept = []
            cleared_count = 0
            for item in lib:
                if item.get("done") or item.get("status") in ("Done", "Completed", "Cancelled", "Cancelled by user"):
                    cleared_count += 1
                    eid = str(item.get("id"))
                    for folder_rel in [eid, os.path.join("faceless", eid)]:
                        try:
                            target_dir = safe_join_path(OUT_DIR, folder_rel)
                            if os.path.exists(target_dir):
                                shutil.rmtree(target_dir, ignore_errors=True)
                        except Exception: pass
                    try:
                        work_dir = safe_join_path(WORK_DIR, "jobs", eid)
                        if os.path.exists(work_dir):
                            shutil.rmtree(work_dir, ignore_errors=True)
                    except Exception: pass
                else:
                    kept.append(item)

            with open(path, "w", encoding="utf-8") as f:
                json.dump(kept, f, indent=2)
            return cleared_count
        except Exception:
            return 0
