import os
import sys
import subprocess

try:
    import static_ffmpeg
    static_ffmpeg.add_paths()
except Exception:
    pass

import yt_dlp

import json

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloads")
CONFIG_FILE = os.path.join(BASE_DIR, "settings.json")

if os.path.exists(CONFIG_FILE):
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if data.get("save_dir") and os.path.isdir(data["save_dir"]):
                DOWNLOAD_DIR = data["save_dir"]
    except Exception:
        pass

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

def main():
    os.system('cls' if os.name == 'nt' else 'clear')
    print("=" * 60)
    print("   🎬 Video Downloader (โปรแกรมดาวน์โหลดวิดีโอด่วน)")
    print("   รองรับ YouTube, TikTok, Facebook, IG, X, ฯลฯ")
    print(f"   โฟลเดอร์บันทึก: {DOWNLOAD_DIR}")
    print("=" * 60)
    print()

    while True:
        url = input("👉 วางลิงก์วิดีโอที่ต้องการดาวน์โหลด (หรือพิมพ์ q เพื่อออก): ").strip()
        if not url:
            continue
        if url.lower() in ['q', 'exit', 'quit']:
            print("ปิดโปรแกรม...")
            break

        print()
        print("เลือกรูปแบบที่ต้องการ:")
        print("  1. ชัดสุดอัตโนมัติ (Best Quality - สูงสุด 1080p/4K)")
        print("  2. วิดีโอ 1080p (Full HD)")
        print("  3. วิดีโอ 720p (HD)")
        print("  4. แยกเฉพาะไฟล์เสียง MP3")
        choice = input("เลือกตัวเลือก (กด Enter เพื่อเลือก 1): ").strip()

        target_url = url
        custom_headers = {}
        forced_title = None

        try:
            from url_resolver import resolve_video_url
            resolved = resolve_video_url(url)
            if resolved:
                target_url = resolved.get('resolved_url', url)
                custom_headers = resolved.get('headers', {})
                forced_title = resolved.get('title')
        except Exception as res_err:
            pass

        if forced_title:
            safe_title = "".join(c for c in forced_title if c.isalnum() or c in (' ', '-', '_', '.', '#')).strip()[:100]
            if not safe_title:
                safe_title = "video"
            outtmpl = os.path.join(DOWNLOAD_DIR, f'{safe_title} [%(id)s].%(ext)s')
        else:
            outtmpl = os.path.join(DOWNLOAD_DIR, '%(title).120B [%(id)s].%(ext)s')

        ydl_opts = {
            'outtmpl': outtmpl,
            'noplaylist': True,
            'windowsfilenames': True,
        }
        if custom_headers:
            ydl_opts['http_headers'] = custom_headers

        if choice == '2':
            ydl_opts['format'] = 'bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio/best[height<=1080]/best'
            ydl_opts['merge_output_format'] = 'mp4'
        elif choice == '3':
            ydl_opts['format'] = 'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=720]+bestaudio/best[height<=720]/best'
            ydl_opts['merge_output_format'] = 'mp4'
        elif choice == '4':
            ydl_opts['format'] = 'bestaudio/best'
            ydl_opts['postprocessors'] = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }]
        else:
            ydl_opts['format'] = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best'
            ydl_opts['merge_output_format'] = 'mp4'

        print("\n🚀 กำลังดาวน์โหลด กรุณารอสักครู่...\n")
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([target_url])
            print("\n✅ ดาวน์โหลดเสร็จสมบูรณ์!")
            if sys.platform == "win32":
                os.startfile(DOWNLOAD_DIR)
        except Exception as e:
            print(f"\n❌ เกิดข้อผิดพลาด: {e}")

        print("\n" + "-" * 60 + "\n")

if __name__ == "__main__":
    main()
