import os
import json
from datetime import datetime
from functools import wraps

from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    send_from_directory,
    url_for,
    redirect,
    session,
    abort,
)

from transformers import MarianMTModel, MarianTokenizer
import opencc
from authlib.integrations.flask_client import OAuth

# ------------------------------
# Flask 基本設定
# ------------------------------
app = Flask(__name__)

# 用於 session 加密（請在環境變數中設定）
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-key-change-me")

# 開發者密碼（第二層驗證）
DEV_PASSWORD = os.getenv("DEV_PASSWORD", "change-me-dev-password")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 使用者任務與錄音資料夾
TASKS_DIR = os.path.join(BASE_DIR, "tasks")
SESS_DIR = os.path.join(BASE_DIR, "sessions")
os.makedirs(TASKS_DIR, exist_ok=True)
os.makedirs(SESS_DIR, exist_ok=True)

# ------------------------------
# Google OAuth 設定
# ------------------------------
oauth = OAuth(app)

oauth.register(
    name="google",
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


# ------------------------------
# 登入保護裝飾器
# ------------------------------
def login_required(f):
    """需要：Google 登入 + 開發者密碼驗證 才能使用的頁面 / API。"""

    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        if not session.get("dev_verified"):
            return redirect(url_for("verify_dev"))
        return f(*args, **kwargs)

    return wrapper


def get_current_user_id() -> str:
    """取得目前登入的 Google user id（sub）。未登入會回傳 401。"""
    uid = session.get("user_id")
    if not uid:
        abort(401)
    return uid


def get_user_tasks_file() -> str:
    """每個 user 各自一個 tasks_xxx.json。"""
    uid = get_current_user_id()
    return os.path.join(TASKS_DIR, f"tasks_{uid}.json")


def load_user_tasks():
    path = get_user_tasks_file()
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_user_tasks(tasks):
    path = get_user_tasks_file()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2)


def get_user_audio_dir() -> str:
    """每個 user 各自一個錄音資料夾：sessions/<user_id>/"""
    uid = get_current_user_id()
    user_dir = os.path.join(SESS_DIR, uid)
    os.makedirs(user_dir, exist_ok=True)
    return user_dir


# ------------------------------
# 翻譯模型（本地、免費）
# ------------------------------
model_en_zh = MarianMTModel.from_pretrained("Helsinki-NLP/opus-mt-en-zh")
token_en_zh = MarianTokenizer.from_pretrained("Helsinki-NLP/opus-mt-en-zh")

model_zh_en = MarianMTModel.from_pretrained("Helsinki-NLP/opus-mt-zh-en")
token_zh_en = MarianTokenizer.from_pretrained("Helsinki-NLP/opus-mt-zh-en")

converter_s2t = opencc.OpenCC("s2t")
converter_t2s = opencc.OpenCC("t2s")

translation_cache = {}


def detect_lang(text: str) -> str:
    zh = sum("\u4e00" <= c <= "\u9fff" for c in text)
    en = sum(c.isalpha() for c in text)
    return "zh-CN" if zh > en else "en"


def translate_text_process(text, src, tgt):
    if not text:
        return ""

    if src == "auto":
        src = detect_lang(text)

    key = (text, src, tgt)
    if key in translation_cache:
        return translation_cache[key]

    if src == "en" and tgt == "zh-CN":
        batch = token_en_zh([text], return_tensors="pt", padding=True)
        out = model_en_zh.generate(**batch)
        result = token_en_zh.batch_decode(out, skip_special_tokens=True)[0]

    elif src == "zh-CN" and tgt == "en":
        batch = token_zh_en([text], return_tensors="pt", padding=True)
        out = model_zh_en.generate(**batch)
        result = token_zh_en.batch_decode(out, skip_special_tokens=True)[0]

    elif src == "zh-CN" and tgt == "zh-TW":
        result = converter_s2t.convert(text)

    elif src == "zh-TW" and tgt == "zh-CN":
        result = converter_t2s.convert(text)

    elif src == "en" and tgt == "zh-TW":
        mid = translate_text_process(text, "en", "zh-CN")
        result = translate_text_process(mid, "zh-CN", "zh-TW")

    elif src == "zh-TW" and tgt == "en":
        mid = translate_text_process(text, "zh-TW", "zh-CN")
        result = translate_text_process(mid, "zh-CN", "en")

    else:
        result = text

    translation_cache[key] = result
    return result


# ------------------------------
# Auth / Login Routes
# ------------------------------
@app.route("/login")
def login():
    """Step 1：使用 Google 登入。"""
    redirect_uri = "https://speechtranslate.replit.app/auth/callback"
    return oauth.google.authorize_redirect(redirect_uri)



@app.route("/auth/callback")
def auth_callback():
    """Google OAuth 回調。"""
    token = oauth.google.authorize_access_token()
    userinfo = token.get("userinfo")

    # 某些情況下 userinfo 可能不存在，就用 id_token
    if not userinfo:
        userinfo = oauth.google.parse_id_token(token)

    # Google 給的 user id（全域唯一）
    session["user_id"] = userinfo["sub"]
    session["email"] = userinfo.get("email")
    session["name"] = userinfo.get("name")

    # 還需要第二層「開發者密碼」驗證
    session["dev_verified"] = False
    return redirect(url_for("verify_dev"))


@app.route("/verify", methods=["GET", "POST"])
def verify_dev():
    """Step 2：輸入開發者密碼。"""
    if "user_id" not in session:
        return redirect(url_for("login"))

    error = None
    if request.method == "POST":
        pwd = request.form.get("password", "")
        if pwd == DEV_PASSWORD:
            session["dev_verified"] = True
            return redirect(url_for("index"))
        else:
            error = "開發者密碼錯誤，請再試一次。"

    return render_template("verify.html", error=error)


@app.route("/logout")
def logout():
    """登出：清除 session 資料。"""
    session.clear()
    return redirect(url_for("login"))


# ------------------------------
# 主畫面 & API Routes（需要登入 + 密碼）
# ------------------------------
@app.route("/")
@login_required
def index():
    return render_template("index.html", user_email=session.get("email"))


@app.route("/translate_api", methods=["POST"])
@login_required
def translate_api():
    data = request.get_json()
    text = data.get("text", "")
    src = data.get("src_lang", "auto")
    tgt = data.get("tgt_lang", "zh-TW")

    translated = translate_text_process(text, src, tgt)
    return jsonify({"translated": translated})


@app.route("/tasks")
@login_required
def get_tasks():
    """只回傳當前登入使用者的 tasks。"""
    tasks = load_user_tasks()
    return jsonify(tasks)


@app.route("/task/<int:task_id>")
@login_required
def get_task_detail(task_id):
    """回傳單一任務詳細資訊（僅限自己的）。"""
    tasks = load_user_tasks()
    for t in tasks:
        if t["id"] == task_id:
            audio_url = url_for("serve_audio", filename=t["audio"])
            data = {
                "id": t["id"],
                "name": t["name"],
                "original": t.get("original", ""),
                "translated": t.get("translated", ""),
                "created_at": t.get("created_at", ""),
                "audio_url": audio_url,
            }
            return jsonify(data)
    abort(404)


@app.route("/save_session", methods=["POST"])
@login_required
def save_session():
    """保存當前使用者的錄音 + 原文 + 翻譯。"""
    name = request.form.get("name", "").strip()
    original = request.form.get("original_text", "")
    translated = request.form.get("translated_text", "")
    audio_file = request.files.get("audio")

    if audio_file is None or audio_file.filename == "" or audio_file.content_length == 0:
        return jsonify({"success": False, "error": "audio empty"})

    tasks = load_user_tasks()
    new_id = max([task["id"] for task in tasks], default=0) + 1

    # 自動命名
    if not name:
        name = f"語音{new_id:03d}"

    # 存音檔到「自己的資料夾」底下
    user_audio_dir = get_user_audio_dir()
    audio_filename = f"session_{new_id}.webm"
    audio_path = os.path.join(user_audio_dir, audio_filename)
    audio_file.save(audio_path)

    task = {
        "id": new_id,
        "name": name,
        "audio": audio_filename,
        "original": original,
        "translated": translated,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    tasks.append(task)
    save_user_tasks(tasks)

    return jsonify({"success": True, "name": name})


@app.route("/audio/<path:filename>")
@login_required
def serve_audio(filename):
    """只從「目前使用者自己的音檔資料夾」提供檔案。"""
    user_audio_dir = get_user_audio_dir()
    return send_from_directory(user_audio_dir, filename)


if __name__ == "__main__":
    # 本機開發用，部署到 Replit / HF 時會用他們自己的 run command
    app.run(host="0.0.0.0", port=7860, debug=True)
