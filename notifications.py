import os, smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

def send_completion_email(to_email=None, subject="", html_content="", text_content=""):
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port_str = os.getenv("SMTP_PORT", "587")
    try:
        smtp_port = int(smtp_port_str)
    except Exception:
        smtp_port = 587

    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASSWORD") or os.getenv("SMTP_PASS")
    smtp_from = os.getenv("SMTP_FROM") or smtp_user or "no-reply@clipforge.local"

    # Graceful fallback when email environment variables are not configured
    if not smtp_host or not smtp_user or not smtp_pass:
        print("[EMAIL] SMTP environment variables not configured (SMTP_HOST, SMTP_USER, SMTP_PASSWORD). Skipping email notification.")
        return False

    recipient = (to_email or "").strip() or os.getenv("NOTIFICATION_EMAIL", "").strip()
    if not recipient:
        print("[EMAIL] No recipient email specified or configured in NOTIFICATION_EMAIL. Skipping email notification.")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject or "🎉 ClipForge: Your videos are ready!"
        msg["From"] = smtp_from
        msg["To"] = recipient

        if text_content:
            msg.attach(MIMEText(text_content, "plain", "utf-8"))
        if html_content:
            msg.attach(MIMEText(html_content, "html", "utf-8"))

        if smtp_port == 465:
            server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=12)
        else:
            server = smtplib.SMTP(smtp_host, smtp_port, timeout=12)
            server.starttls()

        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_from, [recipient], msg.as_string())
        server.quit()
        print(f"[EMAIL] Successfully sent completion notification email to {recipient}")
        return True
    except Exception as e:
        print(f"[EMAIL] Failed to send email to {recipient}: {e}")
        return False

def build_clipper_email(title, clips, count, app_url=""):
    clips_html = ""
    for idx, c in enumerate(clips, 1):
        score = c.get("score", 94)
        cat = c.get("category", "Viral Clip")
        c_title = c.get("title", f"Clip #{idx}")
        dl_url = f"{app_url}/clips/{c.get('vid')}/{c.get('file')}?dl=1" if app_url else "#"
        clips_html += f"""
        <div style="background:#1a1f2e;border:1px solid #262c3e;border-radius:8px;padding:12px;margin-bottom:10px;">
            <div style="font-weight:bold;font-size:14px;color:#f8fafc;margin-bottom:4px;">{idx}. {c_title}</div>
            <div style="font-size:12px;color:#34d399;margin-bottom:6px;">⚡ {score} Virality • {cat}</div>
            <a href="{dl_url}" style="display:inline-block;background:#10b981;color:#000;font-weight:bold;font-size:12px;padding:6px 14px;border-radius:6px;text-decoration:none;">Download Clip MP4</a>
        </div>
        """

    html = f"""
    <!DOCTYPE html>
    <html>
    <body style="background:#0b0d14;color:#f8fafc;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;padding:20px;">
        <div style="max-width:560px;margin:0 auto;background:#131722;border:1px solid #262c3e;border-radius:12px;padding:24px;">
            <div style="font-size:22px;font-weight:800;color:#10b981;margin-bottom:6px;">⚡ ClipForge</div>
            <h2 style="font-size:18px;margin-top:0;margin-bottom:8px;">Your viral clips are ready!</h2>
            <p style="color:#94a3b8;font-size:13px;line-height:1.5;margin-bottom:18px;">
                Successfully generated <b>{len(clips)} clips</b> from <i>"{title}"</i>.
            </p>
            <div style="margin-bottom:20px;">
                {clips_html}
            </div>
            <div style="text-align:center;border-top:1px solid #262c3e;padding-top:16px;">
                <a href="{app_url}" style="background:#10b981;color:#000;font-weight:bold;padding:10px 20px;border-radius:8px;text-decoration:none;font-size:13px;">Open ClipForge Library</a>
            </div>
        </div>
    </body>
    </html>
    """
    text = f"Your ClipForge batch is ready! Generated {len(clips)} clips from {title}."
    return html, text

def build_faceless_email(title, duration, file_url, script, app_url=""):
    full_dl = f"{app_url}{file_url}?dl=1" if app_url else file_url
    html = f"""
    <!DOCTYPE html>
    <html>
    <body style="background:#0b0d14;color:#f8fafc;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;padding:20px;">
        <div style="max-width:560px;margin:0 auto;background:#131722;border:1px solid #262c3e;border-radius:12px;padding:24px;">
            <div style="font-size:22px;font-weight:800;color:#c084fc;margin-bottom:6px;">🎮 GTA 6 Faceless Studio</div>
            <h2 style="font-size:18px;margin-top:0;margin-bottom:8px;">Your GTA 6 video is ready!</h2>
            <p style="color:#94a3b8;font-size:13px;line-height:1.5;margin-bottom:14px;">
                Duration: <b>{duration}s</b> • Original sound removed • Neural voice & captions burned in.
            </p>
            <div style="background:#1a1f2e;border:1px solid #262c3e;border-radius:8px;padding:12px;margin-bottom:18px;font-size:12px;color:#cbd5e1;font-style:italic;">
                "{script[:200]}..."
            </div>
            <div style="text-align:center;">
                <a href="{full_dl}" style="background:#a855f7;color:#fff;font-weight:bold;padding:10px 20px;border-radius:8px;text-decoration:none;font-size:13px;display:inline-block;">Download MP4 Video</a>
            </div>
        </div>
    </body>
    </html>
    """
    text = f"Your GTA 6 Faceless video is ready! Duration: {duration}s."
    return html, text
