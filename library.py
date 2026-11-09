import os, json, time, threading, shutil
from config import WORK_DIR, OUT_DIR

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
        idx = next((i for i, x in enumerate(lib) if x.get("id") == entry["id"]), None)
        if idx is not None:
            # Preserve created_at if not in entry
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
            new_lib = [x for x in lib if x.get("id") != entry_id]
            with open(path, "w", encoding="utf-8") as f:
                json.dump(new_lib, f, indent=2)

            # Clean up disk folder
            for p in (os.path.join(OUT_DIR, entry_id), os.path.join(OUT_DIR, "faceless", entry_id)):
                if os.path.exists(p):
                    try: shutil.rmtree(p, ignore_errors=True)
                    except Exception: pass
            return True
        except Exception:
            return False

def delete_library_clip(entry_id, filename):
    path = _get_lib_path()
    with _lock:
        if not os.path.exists(path):
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                lib = json.load(f)
            entry = next((x for x in lib if x.get("id") == entry_id), None)
            if not entry:
                return False

            # Delete physical file
            entry_type = entry.get("type", "clipper")
            if entry_type == "faceless":
                file_p = os.path.join(OUT_DIR, "faceless", entry_id, filename)
            else:
                file_p = os.path.join(OUT_DIR, entry_id, filename)

            if os.path.exists(file_p):
                try: os.remove(file_p)
                except Exception: pass

            entry["clips"] = [c for c in entry.get("clips", []) if c.get("file") != filename]
            entry["clips_count"] = len(entry["clips"])
            with open(path, "w", encoding="utf-8") as f:
                json.dump(lib, f, indent=2)
            return True
        except Exception:
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
                if item.get("done") or item.get("status") in ("Done", "Cancelled", "Cancelled by user"):
                    cleared_count += 1
                    eid = item.get("id")
                    for p in (os.path.join(OUT_DIR, eid), os.path.join(OUT_DIR, "faceless", eid)):
                        if os.path.exists(p):
                            try: shutil.rmtree(p, ignore_errors=True)
                            except Exception: pass
                else:
                    kept.append(item)

            with open(path, "w", encoding="utf-8") as f:
                json.dump(kept, f, indent=2)
            return cleared_count
        except Exception:
            return 0
