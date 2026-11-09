import os, json, time, uuid, threading
from config import WORK_DIR

_lock = threading.Lock()

def _get_profiles_path():
    os.makedirs(WORK_DIR, exist_ok=True)
    return os.path.join(WORK_DIR, "voice_profiles.json")

def _get_profiles_dir():
    d = os.path.join(WORK_DIR, "voice_profiles")
    os.makedirs(d, exist_ok=True)
    return d

def list_voice_profiles():
    path = _get_profiles_path()
    if not os.path.exists(path):
        return []
    with _lock:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception:
            return []

def get_voice_profile(vpid):
    for p in list_voice_profiles():
        if p.get("id") == vpid:
            return p
    return None

def create_voice_profile(name, consent=False, base_voice="en-US-ChristopherNeural", pitch="+0Hz", rate="+0%", audio_bytes=None, filename=""):
    if not consent:
        raise ValueError("Consent required: You must confirm you own or have authorized permission to use this voice.")

    name = (name or "").strip()
    if not name:
        name = "Custom Voice"

    vpid = f"vp_{uuid.uuid4().hex[:8]}"
    profiles_dir = _get_profiles_dir()

    sample_filename = ""
    if audio_bytes:
        ext = os.path.splitext(filename)[1].lower() if filename else ".mp3"
        if ext not in (".mp3", ".wav", ".m4a", ".ogg", ".webm"):
            ext = ".mp3"
        sample_filename = f"{vpid}{ext}"
        sample_path = os.path.join(profiles_dir, sample_filename)
        with open(sample_path, "wb") as f:
            f.write(audio_bytes)

    profile = {
        "id": vpid,
        "name": name,
        "consent_confirmed": True,
        "consent_statement": "The user verified ownership or explicit authorized permission to synthesize this voice profile.",
        "base_voice": base_voice,
        "pitch": pitch,
        "rate": rate,
        "sample_filename": sample_filename,
        "created_at": time.time()
    }

    path = _get_profiles_path()
    with _lock:
        profiles = []
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    profiles = json.load(f)
                    if not isinstance(profiles, list):
                        profiles = []
            except Exception:
                profiles = []

        profiles.append(profile)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(profiles, f, indent=2)

    return profile

def rename_voice_profile(vpid, new_name):
    path = _get_profiles_path()
    with _lock:
        if not os.path.exists(path):
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                profiles = json.load(f)
            found = False
            for p in profiles:
                if p.get("id") == vpid:
                    p["name"] = (new_name or "").strip() or p["name"]
                    found = True
                    break
            if found:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(profiles, f, indent=2)
            return found
        except Exception:
            return False

def get_voice_preview_file(vpid):
    vp = get_voice_profile(vpid)
    if not vp:
        return None

    # If uploaded sample exists, return it
    if vp.get("sample_filename"):
        sample_path = os.path.join(_get_profiles_dir(), vp["sample_filename"])
        if os.path.exists(sample_path):
            return sample_path

    # Synthesize quick preview sample if not yet created
    preview_file = os.path.join(_get_profiles_dir(), f"{vpid}_preview.mp3")
    if os.path.exists(preview_file):
        return preview_file

    try:
        from faceless import synthesize_speech
        dummy_srt = os.path.join(_get_profiles_dir(), f"{vpid}_preview.srt")
        synthesize_speech("Hello, this is a preview of your custom voice profile.", vpid, preview_file, dummy_srt)
        if os.path.exists(preview_file):
            return preview_file
    except Exception as e:
        print(f"Preview synthesis error: {e}")
    return None

def delete_voice_profile(vpid):
    path = _get_profiles_path()
    with _lock:
        if not os.path.exists(path):
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                profiles = json.load(f)
            p_to_del = next((x for x in profiles if x.get("id") == vpid), None)
            if p_to_del and p_to_del.get("sample_filename"):
                sample_p = os.path.join(_get_profiles_dir(), p_to_del["sample_filename"])
                if os.path.exists(sample_p):
                    try: os.remove(sample_p)
                    except Exception: pass

            new_profiles = [x for x in profiles if x.get("id") != vpid]
            with open(path, "w", encoding="utf-8") as f:
                json.dump(new_profiles, f, indent=2)
            return True
        except Exception:
            return False
