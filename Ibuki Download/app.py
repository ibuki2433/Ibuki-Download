import os
import sys
import uuid
import time
import socket
import threading
import subprocess
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_from_directory, send_file

# Ensure ffmpeg and ffprobe are in PATH
try:
    import static_ffmpeg
    static_ffmpeg.add_paths()
except Exception as e:
    print(f"Warning loading static-ffmpeg: {e}")

import yt_dlp
from url_resolver import resolve_video_url

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "templates"),
    static_folder=os.path.join(BASE_DIR, "static")
)

# In-memory store for background tasks
tasks = {}
tasks_lock = threading.Lock()

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def format_bytes(bytes_num):
    if not bytes_num or bytes_num <= 0:
        return "0 B"
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_num < 1024.0:
            return f"{bytes_num:.1f} {unit}"
        bytes_num /= 1024.0
    return f"{bytes_num:.1f} TB"

def format_duration(seconds):
    if not seconds:
        return "ไม่ระบุ"
    seconds = int(seconds)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/favicon.ico")
@app.route("/app_icon.ico")
def favicon():
    icon_path = os.path.join(BASE_DIR, "app_icon.ico")
    if os.path.exists(icon_path):
        return send_file(icon_path, mimetype="image/x-icon")
    return "", 404

@app.route("/api/info", methods=["POST"])
def get_info():
    data = request.get_json() or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"success": False, "error": "กรุณาระบุลิงก์วิดีโอ"}), 400

    target_url = url
    custom_headers = {}
    forced_title = None

    try:
        resolved = resolve_video_url(url)
        if resolved:
            target_url = resolved.get('resolved_url', url)
            custom_headers = resolved.get('headers', {})
            forced_title = resolved.get('title')
    except Exception as res_err:
        print(f"URL Resolver error: {res_err}")

    ydl_opts = {
        'extract_flat': False,
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
    }
    if custom_headers:
        ydl_opts['http_headers'] = custom_headers

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(target_url, download=False)
            if not info and not forced_title:
                return jsonify({"success": False, "error": "ไม่พบข้อมูลวิดีโอนี้"}), 404

            title = forced_title or (info.get("title") if info else "วิดีโอพร้อมดาวน์โหลด")
            thumbnail = ""
            if info:
                thumbnail = info.get("thumbnail") or ""
                if not thumbnail and info.get("thumbnails"):
                    thumbnail = info["thumbnails"][-1].get("url")

            uploader = "ไม่ระบุช่อง"
            if info:
                uploader = info.get("uploader") or info.get("channel") or info.get("creator") or "ไม่ระบุช่อง"

            duration = format_duration(info.get("duration")) if info else "ไม่ระบุ"
            extractor = info.get("extractor_key", "Web Video") if info else "Web Video"

            return jsonify({
                "success": True,
                "data": {
                    "title": title,
                    "thumbnail": thumbnail,
                    "duration": duration,
                    "uploader": uploader,
                    "extractor": extractor,
                    "webpage_url": url,
                }
            })
    except Exception as e:
        if forced_title:
            return jsonify({
                "success": True,
                "data": {
                    "title": forced_title,
                    "thumbnail": "",
                    "duration": "ไม่ระบุ",
                    "uploader": "เว็บวิดีโอ",
                    "extractor": "Direct Stream",
                    "webpage_url": url,
                }
            })
        err_msg = str(e)
        if "is not a valid URL" in err_msg:
            err_msg = "รูปแบบลิงก์ไม่ถูกต้อง"
        return jsonify({"success": False, "error": f"เกิดข้อผิดพลาด: {err_msg}"}), 500

def download_worker(task_id, url, quality):
    with tasks_lock:
        tasks[task_id] = {
            "status": "downloading",
            "percent": 0,
            "speed": "0 KB/s",
            "eta": "--",
            "downloaded": "0 B",
            "total": "0 B",
            "title": "กำลังเตรียมข้อมูล...",
            "filename": None,
            "error": None
        }

    def progress_hook(d):
        with tasks_lock:
            if task_id not in tasks:
                return
            
            if d['status'] == 'downloading':
                total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
                downloaded = d.get('downloaded_bytes') or 0
                percent = 0.0
                if total > 0:
                    percent = round((downloaded / total) * 100, 1)

                speed = d.get('speed')
                speed_str = f"{format_bytes(speed)}/s" if speed else "-- MB/s"

                eta = d.get('eta')
                eta_str = f"{int(eta)} วินาที" if eta is not None else "--"

                tasks[task_id]["status"] = "downloading"
                tasks[task_id]["percent"] = percent
                tasks[task_id]["speed"] = speed_str
                tasks[task_id]["eta"] = eta_str
                tasks[task_id]["downloaded"] = format_bytes(downloaded)
                tasks[task_id]["total"] = format_bytes(total)
                
                fn = d.get('filename')
                if fn:
                    tasks[task_id]["title"] = os.path.basename(fn)

            elif d['status'] == 'finished':
                tasks[task_id]["status"] = "processing"
                tasks[task_id]["percent"] = 100
                tasks[task_id]["speed"] = "--"
                tasks[task_id]["eta"] = "กำลังรวมไฟล์/แปลงรูปแบบ..."

    target_url = url
    custom_headers = {}
    forced_title = None

    try:
        resolved = resolve_video_url(url)
        if resolved:
            target_url = resolved.get('resolved_url', url)
            custom_headers = resolved.get('headers', {})
            forced_title = resolved.get('title')
    except Exception as res_err:
        print(f"URL Resolver warning: {res_err}")

    if forced_title:
        safe_title = "".join(c for c in forced_title if c.isalnum() or c in (' ', '-', '_', '.', '#')).strip()[:100]
        if not safe_title:
            safe_title = "video"
        outtmpl = os.path.join(DOWNLOAD_DIR, f'{safe_title} [%(id)s].%(ext)s')
    else:
        outtmpl = os.path.join(DOWNLOAD_DIR, '%(title).120B [%(id)s].%(ext)s')

    ydl_opts = {
        'outtmpl': outtmpl,
        'progress_hooks': [progress_hook],
        'noplaylist': True,
        'windowsfilenames': True,
        'no_warnings': True,
        'quiet': False,
    }
    if custom_headers:
        ydl_opts['http_headers'] = custom_headers

    if quality == "mp3":
        ydl_opts['format'] = 'bestaudio/best'
        ydl_opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]
    elif quality == "1080":
        ydl_opts['format'] = 'bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio/best[height<=1080]/best'
        ydl_opts['merge_output_format'] = 'mp4'
    elif quality == "720":
        ydl_opts['format'] = 'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=720]+bestaudio/best[height<=720]/best'
        ydl_opts['merge_output_format'] = 'mp4'
    elif quality == "480":
        ydl_opts['format'] = 'bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=480]+bestaudio/best[height<=480]/best'
        ydl_opts['merge_output_format'] = 'mp4'
    else:  # "best"
        ydl_opts['format'] = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best'
        ydl_opts['merge_output_format'] = 'mp4'

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(target_url, download=True)
            final_filename = None
            if info:
                tasks[task_id]["title"] = info.get("title", forced_title or "ดาวน์โหลดเสร็จสมบูรณ์")
                if 'requested_downloads' in info and info['requested_downloads']:
                    final_filename = os.path.basename(info['requested_downloads'][0]['filepath'])
                else:
                    final_filename = os.path.basename(ydl.prepare_filename(info))
                    if quality == "mp3":
                        base, _ = os.path.splitext(final_filename)
                        final_filename = f"{base}.mp3"
            elif forced_title:
                # Find the most recently created file in downloads
                files = [os.path.join(DOWNLOAD_DIR, f) for f in os.listdir(DOWNLOAD_DIR)]
                if files:
                    latest = max(files, key=os.path.getctime)
                    final_filename = os.path.basename(latest)

            with tasks_lock:
                tasks[task_id]["status"] = "finished"
                tasks[task_id]["percent"] = 100
                tasks[task_id]["filename"] = final_filename
    except Exception as e:
        with tasks_lock:
            tasks[task_id]["status"] = "error"
            tasks[task_id]["error"] = str(e)

@app.route("/api/download", methods=["POST"])
def start_download():
    data = request.get_json() or {}
    url = (data.get("url") or "").strip()
    quality = (data.get("quality") or "best").strip()

    if not url:
        return jsonify({"success": False, "error": "กรุณาระบุลิงก์วิดีโอ"}), 400

    task_id = str(uuid.uuid4())
    thread = threading.Thread(target=download_worker, args=(task_id, url, quality), daemon=True)
    thread.start()

    return jsonify({"success": True, "task_id": task_id})

@app.route("/api/progress/<task_id>", methods=["GET"])
def get_progress(task_id):
    with tasks_lock:
        task = tasks.get(task_id)
        if not task:
            return jsonify({"success": False, "error": "ไม่พบงานดาวน์โหลดนี้"}), 404
        return jsonify({"success": True, "task": task})

@app.route("/api/downloads", methods=["GET"])
def list_downloads():
    files = []
    try:
        for f in os.listdir(DOWNLOAD_DIR):
            fpath = os.path.join(DOWNLOAD_DIR, f)
            if os.path.isfile(fpath) and not f.endswith('.part') and not f.endswith('.ytdl'):
                stat = os.stat(fpath)
                files.append({
                    "name": f,
                    "size": format_bytes(stat.st_size),
                    "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
                    "mtime": stat.st_mtime
                })
        files.sort(key=lambda x: x["mtime"], reverse=True)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

    return jsonify({"success": True, "files": files[:20]})

@app.route("/download-file/<path:filename>")
def download_file(filename):
    return send_from_directory(DOWNLOAD_DIR, os.path.basename(filename), as_attachment=True)

if __name__ == "__main__":
    local_ip = get_local_ip()
    print("=" * 65)
    print("   🌸 Ibuki Download - Web Application")
    print("   พร้อมใช้งานทั้งบนคอมพิวเตอร์และมือถือ!")
    print(f"   🌐 เปิดบนคอมเครื่องนี้:   http://127.0.0.1:5000")
    print(f"   📱 เปิดบนมือถือ (Wi-Fi): http://{local_ip}:5000")
    print(f"   📁 โฟลเดอร์เก็บไฟล์:      {DOWNLOAD_DIR}")
    print("=" * 65)

    import webbrowser
    threading.Timer(1.2, lambda: webbrowser.open("http://127.0.0.1:5000")).start()
    app.run(host="0.0.0.0", port=5000, debug=False)
