import os
import re
import socket
import ipaddress
import urllib.parse

# Recognized and permitted video domains
ALLOWED_HOST_PATTERNS = [
    r"(.*\.)?youtube\.com$",
    r"youtu\.be$",
    r"(.*\.)?tiktok\.com$",
    r"(.*\.)?twitch\.tv$",
    r"(.*\.)?instagram\.com$",
    r"(.*\.)?twitter\.com$",
    r"(.*\.)?x\.com$",
    r"(.*\.)?facebook\.com$",
    r"(.*\.)?fb\.watch$",
    r"(.*\.)?rumble\.com$",
    r"(.*\.)?vimeo\.com$",
    r"(.*\.)?streamable\.com$",
]

def is_private_ip(ip_str):
    """Checks if an IP string belongs to private, loopback, link-local, or reserved ranges."""
    try:
        ip = ipaddress.ip_address(ip_str)
        return (
            ip.is_private or
            ip.is_loopback or
            ip.is_link_local or
            ip.is_multicast or
            ip.is_reserved or
            ip.is_unspecified
        )
    except ValueError:
        return True

def validate_video_url(url):
    """
    Validates a video URL against SSRF and illegal protocols.
    Ensures URL uses http/https, resolves to a public IP, and is well-formed.
    """
    if not url or not isinstance(url, str):
        raise ValueError("URL must be a non-empty string.")

    url = url.strip()
    if url.startswith("upload://"):
        upload_id = url.replace("upload://", "").strip()
        if not re.match(r"^[a-zA-Z0-9_\-]+$", upload_id):
            raise ValueError("Invalid upload ID format.")
        from config import WORK_DIR
        upload_path = safe_join_path(WORK_DIR, "uploads", f"{upload_id}.mp4")
        if not os.path.exists(upload_path):
            raise ValueError(f"Uploaded video file not found: {upload_id}")
        return url

    parsed = urllib.parse.urlparse(url)

    if parsed.scheme.lower() not in ("http", "https"):
        raise ValueError(f"Invalid URL protocol '{parsed.scheme}'. Only http:// and https:// are allowed.")

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("Invalid URL: missing hostname.")

    hostname = hostname.lower()

    # Reject localhost or obvious internal names
    if hostname in ("localhost", "127.0.0.1", "::1", "0.0.0.0", "metadata.google.internal"):
        raise ValueError("Access to internal/localhost addresses is prohibited.")

    # Check if host is direct IP address
    try:
        if is_private_ip(hostname):
            raise ValueError("Direct access to private or internal IP addresses is prohibited.")
    except Exception:
        pass

    # Resolve domain to IP and verify resolved IP is public (SSRF prevention)
    try:
        addr_info = socket.getaddrinfo(hostname, parsed.port or 443, proto=socket.IPPROTO_TCP)
        for _, _, _, _, sockaddr in addr_info:
            ip = sockaddr[0]
            if is_private_ip(ip):
                raise ValueError(f"Domain resolves to private/internal IP ({ip}), access denied.")
    except socket.gaierror:
        # If hostname cannot be resolved, raise clear error
        raise ValueError(f"Unable to resolve domain '{hostname}'. Please check the URL.")

    return url

def safe_join_path(base_dir, *paths):
    """
    Safely joins paths ensuring the target file/folder strictly resides within base_dir.
    Guards against path traversal attacks (e.g. ../../etc/passwd).
    """
    base_abs = os.path.abspath(base_dir)
    target_abs = os.path.abspath(os.path.join(base_abs, *paths))
    
    # Must have base_abs as common prefix
    try:
        common = os.path.commonpath([base_abs, target_abs])
        if common != base_abs:
            raise PermissionError("Path traversal attempt detected.")
    except ValueError:
        raise PermissionError("Path traversal attempt detected across drives.")

    return target_abs

def sanitize_filename(name):
    """Sanitizes filename removing directory separators and invalid characters."""
    if not name:
        return "unnamed"
    clean = os.path.basename(name)
    clean = re.sub(r'[\\/*?:"<>|]', "", clean)
    clean = clean.strip(". ")
    return clean or "unnamed"
