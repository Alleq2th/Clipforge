import os
import shutil
import time
from config import WORK_DIR, OUT_DIR

def get_job_work_dir(jid):
    """Returns isolated temporary processing directory for a specific job."""
    p = os.path.join(WORK_DIR, "jobs", str(jid))
    os.makedirs(p, exist_ok=True)
    return p

def get_job_out_dir(jid):
    """Returns persistent output directory for a specific job's clips."""
    p = os.path.join(OUT_DIR, str(jid))
    os.makedirs(p, exist_ok=True)
    return p

def get_dir_size_mb(path):
    if not os.path.exists(path):
        return 0.0
    total = 0
    for dirpath, _, filenames in os.walk(path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            if not os.path.islink(fp):
                try:
                    total += os.path.getsize(fp)
                except Exception:
                    pass
    return total / (1024 * 1024)

def cleanup_job_work_dir(jid):
    """
    Deletes the temporary processing directory for a specific job.
    Frees up raw download files and temp audio instantly.
    """
    if not jid:
        return
    p = os.path.join(WORK_DIR, "jobs", str(jid))
    if os.path.exists(p):
        try:
            shutil.rmtree(p, ignore_errors=True)
            print(f"[STORAGE] Cleaned temporary work directory for job {jid}.")
        except Exception as e:
            print(f"[STORAGE] Notice cleaning work dir for job {jid}: {e}")

def cleanup_source_video(video_path):
    """
    Deletes raw downloaded video file immediately after clips are cut.
    """
    if video_path and os.path.exists(video_path):
        try:
            if os.path.isfile(video_path):
                os.remove(video_path)
            elif os.path.isdir(video_path):
                shutil.rmtree(video_path, ignore_errors=True)
            print(f"[STORAGE] Cleaned source video {video_path} to conserve storage.")
        except Exception as e:
            print(f"[STORAGE] Notice cleaning source video: {e}")

def cleanup_storage(max_mb=400, max_saved_jobs=6):
    """
    Protects Render storage:
    1. Removes stray temporary files in WORK_DIR not tied to active jobs.
    2. Prunes oldest completed/failed output video folders if storage exceeds max_mb or max_saved_jobs.
    3. NEVER removes files belonging to active/in-progress jobs.
    """
    os.makedirs(WORK_DIR, exist_ok=True)
    os.makedirs(OUT_DIR, exist_ok=True)

    # Get active job IDs to never prune them
    active_ids = set()
    try:
        from jobs_db import get_active_job_ids
        active_ids = get_active_job_ids()
    except Exception:
        pass

    now = time.time()

    # 1. Clean stale work folders older than 20 minutes and not active
    jobs_work_dir = os.path.join(WORK_DIR, "jobs")
    if os.path.exists(jobs_work_dir):
        try:
            for d in os.listdir(jobs_work_dir):
                if d in active_ids:
                    continue
                dp = os.path.join(jobs_work_dir, d)
                if os.path.isdir(dp) and (now - os.path.getmtime(dp) > 1200):
                    shutil.rmtree(dp, ignore_errors=True)
        except Exception as e:
            print(f"[STORAGE] Notice during work jobs cleanup: {e}")

    # 2. Prune old outputs if exceeding budget (preserving active jobs)
    try:
        vids = []
        for d in os.listdir(OUT_DIR):
            dp = os.path.join(OUT_DIR, d)
            if os.path.isdir(dp) and d != "faceless" and d not in active_ids:
                vids.append(dp)

        vids.sort(key=lambda p: os.path.getmtime(p))

        total_mb = get_dir_size_mb(OUT_DIR) + get_dir_size_mb(WORK_DIR)
        while (len(vids) > max_saved_jobs or total_mb > max_mb) and vids:
            oldest = vids.pop(0)
            try:
                shutil.rmtree(oldest, ignore_errors=True)
                print(f"[STORAGE] Pruned oldest output directory {oldest} to preserve storage.")
            except Exception:
                pass
            total_mb = get_dir_size_mb(OUT_DIR) + get_dir_size_mb(WORK_DIR)
    except Exception as e:
        print(f"[STORAGE] Notice during output pruning: {e}")

# ================= CLOUD STORAGE INTERFACE (SUPABASE / S3 DURABLE STORAGE) =================
class StorageProvider:
    """Abstract interface for permanent video asset storage."""
    name = "abstract"
    def is_configured(self):
        return False
    def save_clip(self, local_path, destination_path):
        raise NotImplementedError
    def get_public_url(self, destination_path):
        raise NotImplementedError
    def delete_clip(self, destination_path):
        raise NotImplementedError
    def save_json(self, destination_path, data):
        return False

    def load_json(self, destination_path):
        return None


class LocalStorageProvider(StorageProvider):
    name = "local"
    is_cloud = False
    def is_configured(self):
        return False

    def save_clip(self, local_path, destination_path):
        # Stored on local filesystem; returns None for cloud URL
        return None

    def get_public_url(self, destination_path):
        return f"/clips/{destination_path}"

    def delete_clip(self, destination_path):
        full_p = os.path.join(OUT_DIR, destination_path)
        if os.path.exists(full_p):
            try: os.remove(full_p)
            except Exception: pass
        return True

class SupabaseStorageProvider(StorageProvider):
    is_cloud = True
    """
    Durable cloud storage provider using Supabase Storage.
    Clips uploaded here survive Render server sleep, restarts, and redeployments permanently.
    Configured via:
      - SUPABASE_URL: e.g. https://xyzcompany.supabase.co
      - SUPABASE_SERVICE_KEY (or SUPABASE_KEY / SUPABASE_ANON_KEY)
      - SUPABASE_STORAGE_BUCKET: default 'clips'
    """
    name = "supabase"

    def __init__(self):
        self.supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
        self.supabase_key = (
            os.getenv("SUPABASE_SERVICE_KEY")
            or os.getenv("SUPABASE_KEY")
            or os.getenv("SUPABASE_ANON_KEY", "")
        ).strip()
        self.bucket = os.getenv("SUPABASE_STORAGE_BUCKET", "clips").strip()
        self._bucket_checked = False

    def is_configured(self):
        return bool(self.supabase_url and self.supabase_key)

    def _ensure_bucket(self):
        """Automatically creates the public clips bucket if it does not already exist."""
        if self._bucket_checked or not self.is_configured():
            return
        try:
            import requests
            url = f"{self.supabase_url}/storage/v1/bucket"
            headers = {
                "Authorization": f"Bearer {self.supabase_key}",
                "apikey": self.supabase_key,
                "Content-Type": "application/json"
            }
            resp = requests.post(
                url,
                headers=headers,
                json={"id": self.bucket, "name": self.bucket, "public": True},
                timeout=10
            )
            # 200/201 = created, 400/409 = already exists
            self._bucket_checked = True
        except Exception as e:
            print(f"[STORAGE] Notice checking/creating Supabase bucket '{self.bucket}': {e}")

    def save_clip(self, local_path, destination_path):
        if not self.is_configured() or not os.path.exists(local_path):
            return None
        try:
            self._ensure_bucket()
            import requests
            clean_dest = destination_path.lstrip("/")
            upload_url = f"{self.supabase_url}/storage/v1/object/{self.bucket}/{clean_dest}"
            headers = {
                "Authorization": f"Bearer {self.supabase_key}",
                "apikey": self.supabase_key,
                "Content-Type": "video/mp4",
                "x-upsert": "true"
            }
            with open(local_path, "rb") as f:
                resp = requests.post(upload_url, headers=headers, data=f, timeout=120)
                if resp.status_code in (200, 201):
                    public_url = self.get_public_url(clean_dest)
                    print(f"[STORAGE] Uploaded clip to Supabase Storage: {clean_dest}")
                    return public_url
                else:
                    print(f"[STORAGE] Supabase upload error ({resp.status_code}): {resp.text[:200]}")
                    return None
        except Exception as e:
            print(f"[STORAGE] Error uploading to Supabase: {e}")
            return None

    def get_public_url(self, destination_path):
        clean_dest = destination_path.lstrip("/")
        return f"{self.supabase_url}/storage/v1/object/public/{self.bucket}/{clean_dest}"

    def delete_clip(self, destination_path):
        if not self.is_configured():
            return False
        try:
            import requests
            clean_dest = destination_path.lstrip("/")
            del_url = f"{self.supabase_url}/storage/v1/object/{self.bucket}/{clean_dest}"
            headers = {
                "Authorization": f"Bearer {self.supabase_key}",
                "apikey": self.supabase_key
            }
            resp = requests.delete(del_url, headers=headers, timeout=15)
            return resp.status_code in (200, 204)
        except Exception as e:
            print(f"[STORAGE] Error deleting from Supabase: {e}")
            return False

    def save_json(self, destination_path, data):
        if not self.is_configured():
            return False
        try:
            self._ensure_bucket()
            import requests, json
            clean_dest = destination_path.lstrip("/")
            upload_url = f"{self.supabase_url}/storage/v1/object/{self.bucket}/{clean_dest}"
            headers = {
                "Authorization": f"Bearer {self.supabase_key}",
                "apikey": self.supabase_key,
                "Content-Type": "application/json",
                "x-upsert": "true"
            }
            body = json.dumps(data, indent=2).encode("utf-8")
            resp = requests.post(upload_url, headers=headers, data=body, timeout=15)
            return resp.status_code in (200, 201)
        except Exception as e:
            print(f"[STORAGE] Error saving JSON to Supabase: {e}")
            return False

    def load_json(self, destination_path):
        if not self.is_configured():
            return None
        try:
            import requests
            clean_dest = destination_path.lstrip("/")
            dl_url = f"{self.supabase_url}/storage/v1/object/public/{self.bucket}/{clean_dest}"
            headers = {
                "Authorization": f"Bearer {self.supabase_key}",
                "apikey": self.supabase_key
            }
            resp = requests.get(dl_url, headers=headers, timeout=15)
            if resp.status_code == 200:
                return resp.json()
            return None
        except Exception as e:
            print(f"[STORAGE] Error loading JSON from Supabase: {e}")
            return None


# Active storage provider
def get_storage_provider():
    sb = SupabaseStorageProvider()
    if sb.is_configured():
        return sb
    return LocalStorageProvider()
