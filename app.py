import os
import threading
import uuid
import requests
from urllib.parse import urlparse, unquote
from flask import Flask, request, send_from_directory, redirect, url_for, Response

APP_PORT = int(os.getenv("APP_PORT", "8080"))
DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "/app/downloads")
AUTH_USER = os.getenv("AUTH_USER", "")
AUTH_PASS = os.getenv("AUTH_PASS", "")

os.makedirs(DOWNLOAD_DIR, exist_ok=True)
app = Flask(__name__)

# Basit Basic Auth (opsiyonel: AUTH_USER/AUTH_PASS vermezsen devre dışı)
def check_auth():
    if not AUTH_USER or not AUTH_PASS:
        return True
    auth = request.authorization
    return auth and auth.username == AUTH_USER and auth.password == AUTH_PASS

def require_auth():
    if not check_auth():
        return Response(
            "Authentication required", 401,
            {"WWW-Authenticate": 'Basic realm="Downloader"'}
        )

def safe_filename_from_url(url, fallback="download.bin"):
    # Content-Disposition'dan isim okumayı deneyebilirsiniz;
    # ama çoğu sunucu düzgün set etmez — URL bazlı alıyoruz:
    path = urlparse(url).path
    name = os.path.basename(unquote(path)) or fallback
    # Çok uzun/bozuk isimleri sadeleştir
    name = "".join(ch for ch in name if ch.isprintable() and ch not in ('\n', '\r', '\t', '/','\\'))
    return name[:180] or fallback

# Basit bir durum takibi (indirme sırasında)
DOWNLOAD_STATUS = {}  # id -> dict(status, filename, error)

def download_worker(job_id, url, final_name):
    tmp_path = os.path.join(DOWNLOAD_DIR, f".{final_name}.part")
    final_path = os.path.join(DOWNLOAD_DIR, final_name)
    try:
        DOWNLOAD_STATUS[job_id] = {"status": "starting", "filename": final_name}
        with requests.get(url, stream=True, timeout=(10, 900), allow_redirects=True, verify=True) as r:
            r.raise_for_status()
            total = int(r.headers.get("Content-Length", 0))
            downloaded = 0
            DOWNLOAD_STATUS[job_id] = {"status": "downloading", "filename": final_name, "total": total, "downloaded": 0}
            with open(tmp_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024*1024):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        DOWNLOAD_STATUS[job_id]["downloaded"] = downloaded
        os.replace(tmp_path, final_path)  # atomik rename
        DOWNLOAD_STATUS[job_id]["status"] = "finished"
    except Exception as e:
        DOWNLOAD_STATUS[job_id] = {"status": "error", "filename": final_name, "error": str(e)}
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except:
            pass

@app.route("/", methods=["GET"])
def index():
    if (resp := require_auth()):
        return resp
    return f"""
    <h2>URL'den Sunucuya İndir</h2>
    <form method="post" action="/download">
      <input name="url" placeholder="https://..." style="width:420px" required>
      <input name="name" placeholder="(Opsiyonel) Kaydedilecek dosya adı">
      <button type="submit">Sunucuya indir</button>
    </form>
    <p><a href="/list">📂 İndirilenler</a></p>
    """

@app.route("/download", methods=["POST"])
def download_file():
    if (resp := require_auth()):
        return resp
    url = request.form.get("url", "").strip()
    custom_name = (request.form.get("name") or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return "Geçerli bir URL girin (http/https).", 400
    filename = custom_name or safe_filename_from_url(url)
    job_id = str(uuid.uuid4())
    threading.Thread(target=download_worker, args=(job_id, url, filename), daemon=True).start()
    return f"""
    <p>İndirme başladı: <b>{filename}</b></p>
    <p>Durum: <a href="/status/{job_id}">/status/{job_id}</a></p>
    <p>Liste: <a href="/list">/list</a></p>
    """

@app.route("/status/<job_id>")
def status(job_id):
    if (resp := require_auth()):
        return resp
    st = DOWNLOAD_STATUS.get(job_id)
    if not st:
        return "İş bulunamadı.", 404
    if st["status"] == "downloading":
        total = st.get("total", 0)
        downloaded = st.get("downloaded", 0)
        pct = f"{(downloaded/total*100):.1f}%" if total else "?"
        return {
            "status": st["status"],
            "filename": st["filename"],
            "downloaded": downloaded,
            "total": total,
            "progress": pct
        }
    return st

@app.route("/list")
def list_files():
    if (resp := require_auth()):
        return resp
    items = sorted(os.listdir(DOWNLOAD_DIR))
    links = []
    for x in items:
        p = os.path.join(DOWNLOAD_DIR, x)
        if os.path.isfile(p) and not x.endswith(".part"):
            links.append(f'<li><a href="/files/{x}">{x}</a> '
                         f'— <a href="/inline/{x}">(önizle)</a></li>')
    return "<h3>İndirilenler</h3><ul>" + "\n".join(links) + "</ul><p><a href='/'>← Geri</a></p>"

@app.route("/files/<path:filename>")
def serve_file(filename):
    if (resp := require_auth()):
        return resp
    # as_attachment=True -> tarayıcıya "indir" olarak sunar
    return send_from_directory(DOWNLOAD_DIR, filename, as_attachment=True)

@app.route("/inline/<path:filename>")
def serve_inline(filename):
    if (resp := require_auth()):
        return resp
    # as_attachment=False -> tarayıcıda görüntülemeye çalışır (metin/görsel)
    return send_from_directory(DOWNLOAD_DIR, filename, as_attachment=False)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=APP_PORT)
