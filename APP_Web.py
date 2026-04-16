
# quiz_web_server.py
#
# FULL version (restores all quiz functionality) with:
# - Login page WITHOUT top menu/header
# - Header/menu appears only AFTER login
# - Range selection (FROM/TO), ordered or random within range
# - One question at a time
# - Optional immediate feedback
# - History tracking (overall + per-question)
# - Per-question "last 3 attempts" indicator on each question page
# - Login (username/password) + Admin user management (add/remove users/change passwords)
#
# Files used (stored next to this script):
#   - questions.json         (your question bank)
#   - quiz_history.json      (attempt history)
#   - users.json             (user accounts)
#
# Render start command:
#   gunicorn quiz_web_server:app --bind 0.0.0.0:$PORT

import json
import os
import random
from datetime import datetime
from pathlib import Path
from functools import wraps

from flask import Flask, request, redirect, url_for, render_template_string, session, abort, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
from pymongo import MongoClient

BASE_DIR = Path(__file__).resolve().parent
STATIC_IMG_DIR = BASE_DIR / "static" / "questions"
IMG_DIR = Path(os.environ.get("QUIZ_IMG_DIR", str(BASE_DIR / "question_images"))).resolve()
QUESTIONS_FILE = BASE_DIR / "questions.json"

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret-key-12345")

# ---------------------- MongoDB Setup ----------------------
MONGO_URI = os.environ.get("MONGO_URI", "")
_mongo_client = None
_mongo_db = None

def get_db():
    global _mongo_client, _mongo_db
    if _mongo_db is None:
        _mongo_client = MongoClient(MONGO_URI)
        _mongo_db = _mongo_client["quiz_app"]
    return _mongo_db


# ---------------------- JSON file helpers (kept for questions.json only) ----------------------
def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, data):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception as e:
        print(f"[WARN] Failed to write {path}: {e}")
        return False


# ---------------------- Questions ----------------------
def load_questions():
    data = _read_json(QUESTIONS_FILE, {"questions": []})
    qs = data.get("questions") or []
    result = []
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

    for idx, q in enumerate(qs):
        question = (q.get("question") or "").strip()
        choices = q.get("choices") or []
        image_url = (q.get("image_url") or "").strip()
        if not question or len(choices) < 2:
            continue

        ans = q.get("answer")
        if isinstance(ans, int):
            correct_indexes = [ans]
        elif isinstance(ans, list):
            try:
                correct_indexes = [int(x) for x in ans]
            except Exception:
                correct_indexes = []
        else:
            correct_indexes = []

        correct_letters = [letters[i - 1] for i in correct_indexes if 1 <= i <= len(choices)]

        result.append(
            {
                "original_index": idx + 1,  # 1-based from original file
                "question": question,
                "choices": choices,
                "correct_letters": correct_letters,
                "image_url": image_url,
            }
        )
    return result

def load_questions_raw():
    """Load full questions.json structure (for editing)."""
    data = _read_json(QUESTIONS_FILE, {"shuffle": False, "questions": []})
    if not isinstance(data, dict):
        data = {"shuffle": False, "questions": []}
    if "questions" not in data or not isinstance(data["questions"], list):
        data["questions"] = []
    return data


def save_questions_raw(data):
    """Save full questions.json structure."""
    if not isinstance(data, dict):
        raise ValueError("questions.json root must be a dict")
    if "questions" not in data or not isinstance(data["questions"], list):
        raise ValueError('"questions" key must be a list')
    return _write_json(QUESTIONS_FILE, data)


# ---------------------- History ----------------------
def load_history():
    db = get_db()
    return list(db.history.find({}, {"_id": 0}))


def save_history(history):
    # history is a list; we store the latest attempt as a new document
    pass  # we use append_history instead


def append_history(attempt: dict):
    db = get_db()
    db.history.insert_one(attempt)


def last_three_for_question(q_number: int):
    """Return up to last 3 statuses ["R","W",...] for this question number."""
    db = get_db()
    out = []
    for attempt in db.history.find({}, {"_id": 0}).sort("_id", -1):
        qitems = attempt.get("questions") or []
        for qi in qitems:
            if qi.get("q") == q_number:
                out.append("R" if qi.get("correct") else "W")
                break
        if len(out) >= 3:
            break
    return out


# ---------------------- Progress (Resume) ----------------------
def get_user_progress(username: str):
    db = get_db()
    doc = db.progress.find_one({"username": username}, {"_id": 0})
    if doc:
        return doc.get("data")
    return None


def set_user_progress(username: str, payload: dict):
    db = get_db()
    db.progress.update_one(
        {"username": username},
        {"$set": {"username": username, "data": payload}},
        upsert=True
    )


def clear_user_progress(username: str):
    db = get_db()
    db.progress.delete_one({"username": username})



# ---------------------- Users / Auth ----------------------
def ensure_default_admin():
    """If no users exist in DB, create a default admin (admin/admin123)."""
    db = get_db()
    if db.users.count_documents({}) == 0:
        db.users.insert_one({
            "username": "admin",
            "password_hash": generate_password_hash("admin123"),
            "is_admin": True,
        })


def load_users():
    ensure_default_admin()
    db = get_db()
    users = list(db.users.find({}, {"_id": 0}))
    cleaned = []
    for u in users:
        username = (u.get("username") or "").strip()
        ph = u.get("password_hash") or ""
        is_admin = bool(u.get("is_admin", False))
        if username and ph:
            cleaned.append({"username": username, "password_hash": ph, "is_admin": is_admin})
    return cleaned


def save_users(users):
    db = get_db()
    db.users.delete_many({})
    if users:
        db.users.insert_many([dict(u) for u in users])


def get_user(username: str):
    db = get_db()
    u = db.users.find_one({"username": {"$regex": f"^{username}$", "$options": "i"}}, {"_id": 0})
    return u


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("user"):
            return redirect(url_for("login", next=request.path))
        return fn(*args, **kwargs)
    return wrapper


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("user"):
            return redirect(url_for("login", next=request.path))
        if not session.get("is_admin"):
            abort(403)
        return fn(*args, **kwargs)
    return wrapper


# ---------------------- Layouts ----------------------
def page_layout(title, body_html):
    """Layout WITH menu (used after login)."""
    return render_template_string(
        r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{{ title }}</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 0; background: #f5f5f5; }
    header { background: #333; color: #fff; padding: 10px 20px; }
    header h1 { margin: 0; font-size: 20px; display: inline-block; }
    nav { display: inline-block; margin-left: 20px; }
    nav a { color: #fff; margin-right: 15px; text-decoration: none; }
    .right { float: right; }
    .right span, .right a { color: #fff; margin-left: 12px; text-decoration: none; }
    main { max-width: 960px; margin: 20px auto; background: #fff; padding: 20px;
           border-radius: 8px; box-shadow: 0 2px 6px rgba(0,0,0,0.1); }
    h2 { margin-top: 0; }
    .btn { display: inline-block; padding: 8px 14px; background: #1976d2; color: #fff;
           border-radius: 4px; text-decoration: none; border: none; cursor: pointer; }
    .btn-secondary { background: #555; }
    .btn-danger { background: #b71c1c; }
    .field { margin-bottom: 12px; }
    label { display: inline-block; margin-bottom: 4px; font-weight: bold; }
    input[type="number"], input[type="text"], input[type="password"] { padding: 6px 8px; }
    input[type="number"] { width: 90px; }
    input[type="checkbox"], input[type="radio"] { margin-right: 6px; }
    .question-block { border: 1px solid #ddd; padding: 10px 12px; border-radius: 6px;
                      margin-bottom: 14px; }
    .question-title { font-weight: bold; margin-bottom: 6px; }
    .choices { margin-left: 10px; }
    .choice-line { margin-bottom: 4px; }
    .correct { color: #2e7d32; font-weight: bold; }
    .wrong { color: #c62828; font-weight: bold; }
    table { border-collapse: collapse; width: 100%; margin-top: 8px; }
    th, td { border: 1px solid #ddd; padding: 6px 8px; text-align: left; }
    th { background: #f0f0f0; }
    .small { font-size: 13px; color: #555; }
    code { background: #eee; padding: 2px 4px; border-radius: 3px; }
    .badge { display:inline-block; padding:2px 8px; border-radius: 999px; font-size: 12px; }
    .badge-r { background:#e8f5e9; color:#1b5e20; }
    .badge-w { background:#ffebee; color:#b71c1c; }
    .badge-n { background:#eeeeee; color:#444; }
    .row { display:flex; gap:16px; flex-wrap: wrap; }
    .card { border:1px solid #ddd; padding:12px; border-radius:8px; background:#fafafa; }
    .error { background:#ffebee; color:#b71c1c; padding:10px; border-radius:6px; }
    .ok { background:#e8f5e9; color:#1b5e20; padding:10px; border-radius:6px; }
  </style>
</head>
<body>
  <header>
    <h1>Quiz Runner (Web)</h1>
    <nav>
      <a href="{{ url_for('home') }}">Home</a>
      <a href="{{ url_for('quiz_setup') }}">Start Quiz</a>
      {% if has_progress %}<a href="{{ url_for('resume_quiz') }}">Resume</a>{% endif %}
      <a href="{{ url_for('history_page') }}">History</a>
      <a href="{{ url_for('print_questions') }}">Print PDF</a>
      {% if is_admin %}<a href="{{ url_for('admin_users') }}">Users</a><a href="{{ url_for('admin_questions') }}">Questions</a>{% endif %}
    </nav>
    <div class="right">
      <span>Logged in as <strong>{{ user }}</strong></span>
      <a href="{{ url_for('logout') }}">Logout</a>
    </div>
    <div style="clear:both;"></div>
  </header>
  <main>
    {{ body|safe }}
  </main>
</body>
</html>""",
        title=title,
        body=body_html,
        user=session.get("user"),
        is_admin=bool(session.get("is_admin")),
        has_progress=bool(session.get("user")) and (get_user_progress(session.get("user")) is not None),
    )


def login_layout(title, body_html):
    """Layout WITHOUT menu (used only for login)."""
    return render_template_string(
        r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{{ title }}</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 0; background: #f5f5f5; }
    .wrap { min-height: 100vh; display:flex; align-items:center; justify-content:center; padding: 20px; }
    .box { width: 100%; max-width: 420px; background:#fff; padding: 24px; border-radius: 10px;
           box-shadow: 0 2px 10px rgba(0,0,0,0.12); }
    h2 { margin-top: 0; }
    .field { margin-bottom: 12px; }
    label { display:block; margin-bottom: 6px; font-weight: bold; }
    input[type="text"], input[type="password"] { width: 100%; padding: 10px; box-sizing:border-box; }
    .btn { width: 100%; padding: 10px 14px; background: #1976d2; color: #fff; border-radius: 6px;
           border:none; cursor:pointer; font-size: 15px; }
    .small { font-size: 13px; color:#666; }
    .error { background:#ffebee; color:#b71c1c; padding:10px; border-radius:6px; margin-bottom: 12px; }
    code { background: #eee; padding: 2px 4px; border-radius: 3px; }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="box">
      {{ body|safe }}
    </div>
  </div>
</body>
</html>""",
        title=title,
        body=body_html,
    )


# ---------------------- Auth Routes ----------------------

@app.route("/qimg/<path:filename>")
@login_required
def question_image(filename):
    """
    Serve question images from IMG_DIR (useful when BASE_DIR is read-only, e.g., on some hosts).
    """
    try:
        IMG_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return send_from_directory(IMG_DIR, filename)


@app.route("/login", methods=["GET", "POST"])
def login():
    ensure_default_admin()
    error = ""
    next_url = request.args.get("next") or url_for("home")

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        u = get_user(username)

        if not u or not check_password_hash(u["password_hash"], password):
            error = "Invalid username or password."
        else:
            session["user"] = u["username"]
            session["is_admin"] = bool(u.get("is_admin"))
            return redirect(next_url)

    body = render_template_string(
        r"""
        <h2>Login</h2>
        <p class="small">Use your username and password to access the application.</p>
        {% if error %}<div class="error">{{ error }}</div>{% endif %}
        <form method="post">
          <div class="field">
            <label>Username</label>
            <input type="text" name="username" required>
          </div>
          <div class="field">
            <label>Password</label>
            <input type="password" name="password" required>
          </div>
          <button class="btn" type="submit">Login</button>
        </form>
        <p class="small">
        </p>
        """,
        error=error,
    )
    return login_layout("Login", body)



@app.errorhandler(500)
def internal_error(e):
    # Friendly message; the real stack trace will be in console / Render logs.
    body = render_template_string(
        r"""
        <h2>Internal Server Error</h2>
        <p class="small">Something went wrong on the server.</p>
        <ul class="small">
          <li>If you're running locally: check the terminal output for the traceback.</li>
          <li>If you're on Render: open your service → <strong>Logs</strong> and copy the traceback.</li>
        </ul>
        <p class="small">After you copy the traceback, paste it here and I can fix it quickly.</p>
        """
    )
    # If not logged in, show login layout; otherwise show normal layout
    if not session.get("user"):
        return login_layout("Server Error", body), 500
    return page_layout("Server Error", body), 500


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------- Admin: Users ----------------------

@app.route("/admin/questions")
@admin_required
def admin_questions():
    """List all questions with options to add/edit/remove."""
    data = load_questions_raw()
    questions = data.get("questions") or []
    # Enrich with 1-based index and short preview
    enriched = []
    for idx, q in enumerate(questions, start=1):
        text = (q.get("question") or "").strip()
        preview = (text[:60] + "...") if len(text) > 60 else text
        enriched.append(
            {
                "num": idx,
                "text": text,
                "preview": preview,
                "image_url": (q.get("image_url") or "").strip(),
                "choices": q.get("choices") or [],
            }
        )

    body = render_template_string(
        r"""
<h2>Question Bank</h2>
<p class="small">Add, edit, or remove questions. Changes are saved to <code>questions.json</code>.</p>
<p>
          <a class="btn" href="{{ url_for('admin_question_new') }}">Add new question</a>
          <form method="post" action="{{ url_for('admin_auto_attach_images') }}" style="display:inline;margin-left:8px;">
            <button class="btn btn-secondary" type="submit" onclick="return confirm('Automatically attach images based on filenames in static/questions?')">Auto attach images</button>
          </form>
        </p>

{% if not questions %}
  <p class="small">No questions found.</p>
{% else %}
  <table>
    <tr>
      <th>#</th>
      <th>Question</th>
      <th>Choices</th>
      <th>Image</th>
      <th>Actions</th>
    </tr>
    {% for q in questions %}
      <tr>
        <td>{{ q.num }}</td>
        <td>{{ q.preview }}</td>
        <td>{{ q.choices|length }}</td>
        <td>{% if q.image_url %}Yes{% else %}No{% endif %}</td>
        <td>
          <a class="btn btn-secondary" href="{{ url_for('admin_question_edit', qnum=q.num) }}">Edit</a>
          <form method="post" action="{{ url_for('admin_question_delete', qnum=q.num) }}" style="display:inline;">
            <button class="btn btn-danger" type="submit">Delete</button>
          </form>
        </td>
      </tr>
    {% endfor %}
  </table>
{% endif %}
""",
        questions=enriched,
    )
    return page_layout("Question Bank", body)


def _question_form_html(q=None, action_url="", title="Question Editor"):
    """Shared HTML for add/edit question forms."""
    q = q or {}
    question_text = (q.get("question") or "").strip()
    choices = q.get("choices") or []
    explanation = (q.get("explanation") or "").strip()
    image_url = (q.get("image_url") or "").strip()

    # Prepare up to 6 choices for the form
    max_choices = 6
    padded_choices = list(choices) + [""] * (max_choices - len(choices))
    letters = "ABCDEF"

    # Determine correct answers: answer may be int or list of ints (1-based)
    ans = q.get("answer")
    if isinstance(ans, int):
        correct_indices = [ans]
    elif isinstance(ans, list):
        try:
            correct_indices = [int(x) for x in ans]
        except Exception:
            correct_indices = []
    else:
        correct_indices = []

    # Convert to letter set
    correct_letters = set()
    for i in correct_indices:
        if 1 <= i <= max_choices:
            correct_letters.add(letters[i - 1])

    return render_template_string(
        r"""
        <h2>{{ title }}</h2>
        <form method="post">
          <div class="field">
            <label>Question text</label><br>
            <textarea name="question" rows="3" style="width:100%;" required>{{ question_text }}</textarea>
          </div>

          <div class="field">
            <label>Answer choices (leave blank to skip a slot)</label>
            <div class="small">Use the checkboxes to mark which letters are correct. You can select more than one.</div>
            <table>
              <tr><th>Letter</th><th>Choice text</th><th>Correct?</th></tr>
              {% for i in range(max_choices) %}
                <tr>
                  <td>{{ letters[i] }})</td>
                  <td>
                    <input type="text" name="choice_{{ i }}" style="width:100%;" value="{{ padded_choices[i] }}">
                  </td>
                  <td style="text-align:center;">
                    <input type="checkbox" name="correct" value="{{ letters[i] }}" {% if letters[i] in correct_letters %}checked{% endif %}>
                  </td>
                </tr>
              {% endfor %}
            </table>
          </div>

          <div class="field">
            <label>Explanation (optional)</label><br>
            <textarea name="explanation" rows="3" style="width:100%;">{{ explanation }}</textarea>
          </div>

          <div class="field">
            <label>Image URL (optional)</label>
            <div class="small">
              You can copy an image address from the web and paste it here (for example,
              a direct <code>https://...</code> URL). The image will appear under the question during the quiz.
            </div>
            <input type="text" name="image_url" style="width:100%;" value="{{ image_url }}">
          </div>

          <p>
            <button class="btn" type="submit">Save</button>
            <a class="btn btn-secondary" href="{{ url_for('admin_questions') }}">Cancel</a>
          </p>
        </form>
        """,
        title=title,
        question_text=question_text,
        padded_choices=padded_choices,
        explanation=explanation,
        image_url=image_url,
        max_choices=max_choices,
        letters=letters,
        correct_letters=correct_letters,
    )


@app.route("/admin/questions/new", methods=["GET", "POST"])
@admin_required
def admin_question_new():
    data = load_questions_raw()
    questions = data.get("questions") or []

    if request.method == "POST":
        question_text = (request.form.get("question") or "").strip()
        explanation = (request.form.get("explanation") or "").strip()
        image_url = (request.form.get("image_url") or "").strip()

        max_choices = 6
        letters = "ABCDEF"
        choices = []
        letter_to_index = {}
        for i in range(max_choices):
            txt = (request.form.get(f"choice_{i}") or "").strip()
            if txt:
                choices.append(txt)
                letter_to_index[letters[i]] = len(choices)  # 1-based

        selected_letters = request.form.getlist("correct")
        correct_indices = []
        for lt in selected_letters:
            idx = letter_to_index.get(lt)
            if idx is not None:
                correct_indices.append(idx)

        if not question_text or len(choices) < 2 or not correct_indices:
            # Re-render with same form + an error message
            body = _question_form_html(
                {
                    "question": question_text,
                    "choices": choices,
                    "explanation": explanation,
                    "image_url": image_url,
                    "answer": correct_indices,
                },
                action_url=url_for("admin_question_new"),
                title="Add Question (fix errors)",
            )
            return page_layout("Question Bank - Add Question", body)

        # Store answer as int if single, else list
        if len(correct_indices) == 1:
            answer_value = correct_indices[0]
        else:
            answer_value = correct_indices

        questions.append(
            {
                "question": question_text,
                "choices": choices,
                "answer": answer_value,
                "explanation": explanation,
                "image_url": image_url,
            }
        )
        data["questions"] = questions
        save_questions_raw(data)
        return redirect(url_for("admin_questions"))

    body = _question_form_html(
        q=None,
        action_url=url_for("admin_question_new"),
        title="Add Question",
    )
    return page_layout("Question Bank - Add Question", body)


@app.route("/admin/questions/<int:qnum>/edit", methods=["GET", "POST"])
@admin_required
def admin_question_edit(qnum):
    data = load_questions_raw()
    questions = data.get("questions") or []
    idx = qnum - 1
    if idx < 0 or idx >= len(questions):
        abort(404)
    q = questions[idx]

    if request.method == "POST":
        question_text = (request.form.get("question") or "").strip()
        explanation = (request.form.get("explanation") or "").strip()
        image_url = (request.form.get("image_url") or "").strip()

        max_choices = 6
        letters = "ABCDEF"
        choices = []
        letter_to_index = {}
        for i in range(max_choices):
            txt = (request.form.get(f"choice_{i}") or "").strip()
            if txt:
                choices.append(txt)
                letter_to_index[letters[i]] = len(choices)  # 1-based

        selected_letters = request.form.getlist("correct")
        correct_indices = []
        for lt in selected_letters:
            idx_choice = letter_to_index.get(lt)
            if idx_choice is not None:
                correct_indices.append(idx_choice)

        if not question_text or len(choices) < 2 or not correct_indices:
            body = _question_form_html(
                {
                    "question": question_text,
                    "choices": choices,
                    "explanation": explanation,
                    "image_url": image_url,
                    "answer": correct_indices,
                },
                action_url=url_for("admin_question_edit", qnum=qnum),
                title=f"Edit Question {qnum} (fix errors)",
            )
            return page_layout(f"Question Bank - Edit Question {qnum}", body)

        if len(correct_indices) == 1:
            answer_value = correct_indices[0]
        else:
            answer_value = correct_indices

        questions[idx] = {
            "question": question_text,
            "choices": choices,
            "answer": answer_value,
            "explanation": explanation,
            "image_url": image_url,
        }
        data["questions"] = questions
        save_questions_raw(data)
        return redirect(url_for("admin_questions"))

    body = _question_form_html(
        q=q,
        action_url=url_for("admin_question_edit", qnum=qnum),
        title=f"Edit Question {qnum}",
    )
    return page_layout(f"Question Bank - Edit Question {qnum}", body)


@app.route("/admin/questions/<int:qnum>/delete", methods=["POST"])
@admin_required
def admin_question_delete(qnum):
    data = load_questions_raw()
    questions = data.get("questions") or []
    idx = qnum - 1
    if 0 <= idx < len(questions):
        del questions[idx]
        data["questions"] = questions
        save_questions_raw(data)
    return redirect(url_for("admin_questions"))


@app.route("/admin/users", methods=["GET", "POST"])
@admin_required
def admin_users():
    msg = ""
    err = ""
    users = load_users()

    if request.method == "POST":
        action = request.form.get("action")

        if action == "add":
            username = (request.form.get("new_username") or "").strip()
            password = request.form.get("new_password") or ""
            is_admin = request.form.get("new_is_admin") == "yes"

            if not username or not password:
                err = "Username and password are required."
            elif get_user(username):
                err = "That username already exists."
            else:
                users.append(
                    {
                        "username": username,
                        "password_hash": generate_password_hash(password),
                        "is_admin": is_admin,
                    }
                )
                save_users(users)
                msg = f"User '{username}' added."
                users = load_users()

        elif action == "remove":
            username = (request.form.get("del_username") or "").strip()
            if username.lower() == (session.get("user") or "").lower():
                err = "You cannot delete the user you are currently logged in as."
            else:
                new_users = [u for u in users if u["username"].lower() != username.lower()]
                if len(new_users) == len(users):
                    err = "User not found."
                else:
                    save_users(new_users)
                    msg = f"User '{username}' removed."
                    users = load_users()

        elif action == "passwd":
            username = (request.form.get("pw_username") or "").strip()
            newpw = request.form.get("pw_new") or ""
            if not username or not newpw:
                err = "Username and new password are required."
            else:
                updated = False
                for u in users:
                    if u["username"].lower() == username.lower():
                        u["password_hash"] = generate_password_hash(newpw)
                        updated = True
                        break
                if not updated:
                    err = "User not found."
                else:
                    save_users(users)
                    msg = f"Password updated for '{username}'."
                    users = load_users()

    body = render_template_string(
        r"""
        <h2>User Management</h2>
        <p class="small">Add or remove users who can access the application.</p>
        {% if msg %}<div class="ok">{{ msg }}</div>{% endif %}
        {% if err %}<div class="error">{{ err }}</div>{% endif %}

        <div class="row">
          <div class="card">
            <h3>Add User</h3>
            <form method="post">
              <input type="hidden" name="action" value="add">
              <div class="field">
                <label>Username</label><br>
                <input type="text" name="new_username" required>
              </div>
              <div class="field">
                <label>Password</label><br>
                <input type="password" name="new_password" required>
              </div>
              <div class="field">
                <label>Admin?</label><br>
                <label><input type="radio" name="new_is_admin" value="no" checked> No</label>
                <label><input type="radio" name="new_is_admin" value="yes"> Yes</label>
              </div>
              <button class="btn" type="submit">Add</button>
            </form>
          </div>

          <div class="card">
            <h3>Remove User</h3>
            <form method="post">
              <input type="hidden" name="action" value="remove">
              <div class="field">
                <label>Username to remove</label><br>
                <input type="text" name="del_username" required>
              </div>
              <button class="btn btn-danger" type="submit">Remove</button>
            </form>
          </div>

          <div class="card">
            <h3>Change Password</h3>
            <form method="post">
              <input type="hidden" name="action" value="passwd">
              <div class="field">
                <label>Username</label><br>
                <input type="text" name="pw_username" required>
              </div>
              <div class="field">
                <label>New Password</label><br>
                <input type="password" name="pw_new" required>
              </div>
              <button class="btn" type="submit">Update</button>
            </form>
          </div>
        </div>

        <h3>Current Users</h3>
        <table>
          <tr><th>Username</th><th>Role</th></tr>
          {% for u in users %}
            <tr>
              <td>{{ u.username }}</td>
              <td>{% if u.is_admin %}Admin{% else %}User{% endif %}</td>
            </tr>
          {% endfor %}
        </table>
        """,
        users=users,
        msg=msg,
        err=err,
    )
    return page_layout("User Management", body)


# ---------------------- Main Pages ----------------------
@app.route("/")
@login_required
def home():
    questions = load_questions()
    total = len(questions)
    body = render_template_string(
        r"""
        <h2>Welcome</h2>
        <p>This web app reads from <code>questions.json</code>.</p>
        <ul><li>Total questions available: <strong>{{ total }}</strong></li></ul>
        {% if total == 0 %}
          <p class="wrong">No questions found. Make sure <code>questions.json</code> is in the same folder as the server.</p>
        {% else %}
<p>
  <a class="btn" href="{{ url_for('quiz_setup') }}">Start a quiz</a>
  {% if has_progress %}
    <a class="btn btn-secondary" href="{{ url_for('resume_quiz') }}">Resume quiz</a>
    <form method="post" action="{{ url_for('clear_progress') }}" style="display:inline;">
      <button class="btn btn-danger" type="submit" onclick="return confirm('Clear saved progress?')">Clear progress</button>
    </form>
  {% endif %}
</p>
        {% endif %}
        """,
        total=total,
        has_progress=(get_user_progress(session.get("user")) is not None),
    )
    return page_layout("Quiz Runner - Home", body)


@app.route("/quiz", methods=["GET", "POST"])
@login_required
def quiz_setup():
    questions = load_questions()
    total = len(questions)

    if request.method == "POST" and total > 0:
        try:
            from_q = int(request.form.get("from_q", 1))
        except ValueError:
            from_q = 1
        try:
            to_q = int(request.form.get("to_q", total))
        except ValueError:
            to_q = total

        from_q = max(1, min(from_q, total))
        to_q = max(1, min(to_q, total))
        if from_q > to_q:
            from_q, to_q = to_q, from_q

        indices = list(range(from_q - 1, to_q))
        order = request.form.get("order", "range")
        if order == "random":
            random.shuffle(indices)

        try:
            pass_percent = int(request.form.get("pass_percent", 70))
        except ValueError:
            pass_percent = 70
        pass_percent = max(1, min(pass_percent, 100))

        show_immediate = request.form.get("show_immediate") == "yes"

        session["quiz_indices"] = indices
        session["pass_percent"] = pass_percent
        session["show_immediate"] = show_immediate
        session["current_index"] = 0
        session["answers"] = {}

        # Starting a new quiz overwrites any previous saved progress
        clear_user_progress(session.get("user"))

        return redirect(url_for("quiz_question"))

    body = render_template_string(
        r"""
        <h2>Quiz Setup</h2>
        {% if total == 0 %}
          <p class="wrong">No questions available. Add questions first.</p>
        {% else %}
          <form method="post">
            <div class="field">
              <label>Question range</label><br>
              From:
              <input type="number" name="from_q" min="1" max="{{ total }}" value="1">
              &nbsp;To:
              <input type="number" name="to_q" min="1" max="{{ total }}" value="{{ total }}">
              <span class="small">(1 to {{ total }})</span>
            </div>
            <div class="field">
              <label for="pass_percent">Percent to pass</label><br>
              <input type="number" id="pass_percent" name="pass_percent" min="1" max="100" value="70"> %
            </div>
            <div class="field">
              <label>Question order</label><br>
              <label><input type="radio" name="order" value="range" checked> In order</label>
              <label><input type="radio" name="order" value="random"> Random inside range</label>
            </div>
            <div class="field">
              <label>Show feedback after each question?</label><br>
              <label><input type="radio" name="show_immediate" value="yes" checked> Yes</label>
              <label><input type="radio" name="show_immediate" value="no"> No (only at the end)</label>
            </div>
            <button class="btn" type="submit">Start quiz</button>
          </form>
        {% endif %}
        """,
        total=total,
    )
    return page_layout("Quiz Runner - Setup", body)


def _get_quiz_state():
    questions = load_questions()
    indices = session.get("quiz_indices") or []
    pass_percent = session.get("pass_percent", 70)
    show_immediate = bool(session.get("show_immediate", False))
    current_index = int(session.get("current_index", 0))
    indices = [i for i in indices if 0 <= i < len(questions)]
    return questions, indices, current_index, pass_percent, show_immediate


@app.route("/quiz/question", methods=["GET", "POST"])
@login_required
def quiz_question():
    questions, indices, current_index, pass_percent, show_immediate = _get_quiz_state()
    if not questions or not indices:
        return redirect(url_for("quiz_setup"))

    answers = session.get("answers") or {}
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

    if request.method == "POST":
        if current_index >= len(indices):
            return redirect(url_for("quiz_result"))

        global_idx = indices[current_index]
        q = questions[global_idx]
        q_number = q["original_index"]

        chosen = request.form.getlist("answer")
        chosen = [c for c in chosen if c in letters]
        answers[str(q_number)] = chosen
        session["answers"] = answers

        current_index += 1
        session["current_index"] = current_index
        # Save progress to disk so you can resume later
        try:
            set_user_progress(
                session.get("user"),
                {
                    "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "quiz_indices": indices,
                    "pass_percent": pass_percent,
                    "show_immediate": bool(show_immediate),
                    "current_index": current_index,
                    "answers": answers,
                },
            )
        except Exception as _e:
            print(f"[WARN] Could not save progress: {_e}")

        if show_immediate:
            correct_set = set(q["correct_letters"])
            chosen_set = set(chosen)
            is_correct = bool(correct_set) and chosen_set == correct_set

            body = render_template_string(
                r"""
                <h2>Question {{ q_number }} Feedback</h2>
                <div class="question-block">
                  <div class="question-title">Q{{ q_number }}. {{ question }}</div>
                  {% if q_image_url %}
                    <div style="margin:8px 0;"><img src="{{ q_image_url }}" alt="Question image" style="max-width:100%;max-height:300px;"></div>
                  {% endif %}
                  <div class="choices">
                    {% for letter, choice in letter_choices %}
                      {% if letter in correct and letter in chosen %}
                        <div class="choice-line correct">✔ {{ letter }}) {{ choice }}</div>
                      {% elif letter in correct %}
                        <div class="choice-line correct">{{ letter }}) {{ choice }} (correct)</div>
                      {% elif letter in chosen %}
                        <div class="choice-line wrong">{{ letter }}) {{ choice }} (chosen)</div>
                      {% else %}
                        <div class="choice-line">{{ letter }}) {{ choice }}</div>
                      {% endif %}
                    {% endfor %}
                  </div>
                  <p>
                    {% if is_correct %}<span class="correct">Your answer is CORRECT.</span>
                    {% else %}<span class="wrong">Your answer is WRONG.</span>{% endif %}
                  </p>
                </div>
                <p>
                  {% if has_more %}
                    <a class="btn" href="{{ url_for('quiz_question') }}">Next question</a>
                  {% else %}
                    <a class="btn" href="{{ url_for('quiz_result') }}">See final result</a>
                  {% endif %}
                </p>
                """,
                q_number=q_number,
                question=q["question"],
                letter_choices=[(letters[i], ch) for i, ch in enumerate(q["choices"])],
                correct=correct_set,
                chosen=chosen_set,
                is_correct=is_correct,
                has_more=current_index < len(indices),
                q_image_url=q.get("image_url") or "",
            )
            return page_layout(f"Quiz - Q{q_number} Feedback", body)

        if current_index >= len(indices):
            return redirect(url_for("quiz_result"))
        return redirect(url_for("quiz_question"))

    if current_index >= len(indices):
        return redirect(url_for("quiz_result"))

    global_idx = indices[current_index]
    q = questions[global_idx]
    q_number = q["original_index"]
    total_in_quiz = len(indices)
    position_in_quiz = current_index + 1
    last3 = last_three_for_question(q_number)

    body = render_template_string(
        r"""
        <h2>Q{{ q_number }} ({{ position }} of {{ total }})</h2>

        <div class="field small">
          <strong>Last 3 attempts for this question:</strong>
          {% if not last3 %}
            <span class="badge badge-n">-</span>
          {% else %}
            {% for s in last3 %}
              {% if s == 'R' %}
                <span class="badge badge-r">Right</span>
              {% else %}
                <span class="badge badge-w">Wrong</span>
              {% endif %}
            {% endfor %}
          {% endif %}
        </div>

        <p class="small">
          Answer the question below, then click <strong>Submit</strong>.
          {% if show_immediate %}
            You will see if you are correct right away.
          {% else %}
            You will see all answers at the end.
          {% endif %}
        </p>

        <form method="post">
          <div class="question-block">
            <div class="question-title">Q{{ q_number }}. {{ question }}</div>
            {% if q_image_url %}
              <div style="margin:8px 0;"><img src="{{ q_image_url }}" alt="Question image" style="max-width:100%;max-height:300px;"></div>
            {% endif %}
            <div class="choices">
              {% for letter, choice in letter_choices %}
                <div class="choice-line">
                  <label>
                    <input type="checkbox" name="answer" value="{{ letter }}">
                    {{ letter }}) {{ choice }}
                  </label>
                </div>
              {% endfor %}
            </div>
          </div>
          <button class="btn" type="submit">Submit</button>
        </form>
        """,
        q_number=q_number,
        position=position_in_quiz,
        total=total_in_quiz,
        question=q["question"],
        letter_choices=[(letters[i], ch) for i, ch in enumerate(q["choices"])],
        show_immediate=show_immediate,
        last3=last3,
        q_image_url=q.get("image_url") or "",
    )
    return page_layout(f"Quiz - Q{q_number}", body)


@app.route("/quiz/result")
@login_required
def quiz_result():
    questions, indices, current_index, pass_percent, show_immediate = _get_quiz_state()
    if not questions or not indices:
        return redirect(url_for("quiz_setup"))

    answers = session.get("answers") or {}
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

    details = []
    per_question_results = []
    num_correct = 0

    for global_idx in indices:
        q = questions[global_idx]
        q_number = q["original_index"]
        correct_letters = set(q["correct_letters"])
        chosen_letters = set(answers.get(str(q_number), []))
        is_correct = bool(correct_letters) and chosen_letters == correct_letters
        if is_correct:
            num_correct += 1
        per_question_results.append({"q": q_number, "correct": is_correct})

        details.append(
            {
                "q_number": q_number,
                "question": q["question"],
                "letter_choices": [(letters[i], ch) for i, ch in enumerate(q["choices"])],
                "correct_letters": sorted(correct_letters),
                "chosen_letters": sorted(chosen_letters),
                "is_correct": is_correct,
            }
        )

    num_questions = len(indices)
    num_wrong = num_questions - num_correct
    percent = int(round((num_correct / num_questions) * 100)) if num_questions else 0
    passed = percent >= pass_percent

    try:
        append_history({
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "user": session.get("user"),
            "num_questions": num_questions,
            "num_correct": num_correct,
            "num_wrong": num_wrong,
            "percent": percent,
            "questions": per_question_results,
            "q_min": min([x["q"] for x in per_question_results]) if per_question_results else None,
            "q_max": max([x["q"] for x in per_question_results]) if per_question_results else None,
        })
    except Exception as _e:
        print(f"[WARN] Could not save history: {_e}")

    # Quiz completed - clear saved progress for this user
    try:
        clear_user_progress(session.get("user"))
    except Exception as _e:
        print(f"[WARN] Could not clear progress: {_e}")

    body = render_template_string(
        r"""
        <h2>Result</h2>
        <p>
          Correct: <strong>{{ num_correct }}</strong> &nbsp;
          Wrong: <strong>{{ num_wrong }}</strong> &nbsp;
          Score: <strong>{{ percent }}%</strong> &nbsp;
          Status:
          {% if passed %}<span class="correct">PASSED</span>{% else %}<span class="wrong">FAILED</span>{% endif %}
        </p>
        <p class="small">Passing threshold: {{ pass_percent }}%</p>

        <h3>Details</h3>
        {% for item in details %}
          <div class="question-block">
            <div class="question-title">Q{{ item.q_number }}. {{ item.question }}</div>
            {% if item.image_url %}
              <div style="margin:8px 0;"><img src="{{ item.image_url }}" alt="Question image" style="max-width:100%;max-height:300px;"></div>
            {% endif %}
            <div class="choices">
              {% for letter, choice in item.letter_choices %}
                {% if letter in item.correct_letters and letter in item.chosen_letters %}
                  <div class="choice-line correct">✔ {{ letter }}) {{ choice }}</div>
                {% elif letter in item.correct_letters %}
                  <div class="choice-line correct">{{ letter }}) {{ choice }} (correct)</div>
                {% elif letter in item.chosen_letters %}
                  <div class="choice-line wrong">{{ letter }}) {{ choice }} (chosen)</div>
                {% else %}
                  <div class="choice-line">{{ letter }}) {{ choice }}</div>
                {% endif %}
              {% endfor %}
            </div>
            <div class="small">
              Your answer: {{ item.chosen_letters|join(", ") if item.chosen_letters else "(none)" }}<br>
              Correct answer: {{ item.correct_letters|join(", ") if item.correct_letters else "(not set)" }}
            </div>
          </div>
        {% endfor %}

        <p>
          <a class="btn" href="{{ url_for('quiz_setup') }}">Start another quiz</a>
          <a class="btn btn-secondary" href="{{ url_for('home') }}">Home</a>
        </p>
        """,
        num_correct=num_correct,
        num_wrong=num_wrong,
        percent=percent,
        passed=passed,
        pass_percent=pass_percent,
        details=details,
    )
    return page_layout("Quiz - Result", body)


@app.route("/history")
@login_required
def history_page():
    history = load_history()

    # Ensure each history record has q_min/q_max (works for older history files too)
    for h in history:
        if h.get("q_min") is None or h.get("q_max") is None:
            qitems = h.get("questions") or []
            qnums = []
            for qi in qitems:
                try:
                    qnums.append(int(qi.get("q")))
                except Exception:
                    pass
            if qnums:
                h["q_min"] = min(qnums)
                h["q_max"] = max(qnums)

    # ---- Collect filter params from query string ----
    f_user      = (request.args.get("f_user") or "").strip()
    f_result    = (request.args.get("f_result") or "all").strip()   # all | passed | failed
    f_date_from = (request.args.get("f_date_from") or "").strip()   # YYYY-MM-DD
    f_date_to   = (request.args.get("f_date_to") or "").strip()
    f_score_min = request.args.get("f_score_min", "")
    f_score_max = request.args.get("f_score_max", "")
    f_range_from = request.args.get("f_range_from", "")
    f_range_to   = request.args.get("f_range_to", "")

    # Collect unique users for dropdown
    all_users = sorted({(h.get("user") or "") for h in history if (h.get("user") or "")})

    # Apply filters
    filtered = list(reversed(history))  # newest first

    if f_user:
        filtered = [h for h in filtered if (h.get("user") or "").lower() == f_user.lower()]

    if f_result == "passed":
        filtered = [h for h in filtered if h.get("percent", 0) >= h.get("pass_percent", 70)]
    elif f_result == "failed":
        filtered = [h for h in filtered if h.get("percent", 0) < h.get("pass_percent", 70)]

    if f_date_from:
        filtered = [h for h in filtered if (h.get("timestamp") or "") >= f_date_from]
    if f_date_to:
        # Include the whole day of f_date_to by appending end-of-day marker
        filtered = [h for h in filtered if (h.get("timestamp") or "") <= f_date_to + "~"]

    if f_score_min != "":
        try:
            smin = int(f_score_min)
            filtered = [h for h in filtered if h.get("percent", 0) >= smin]
        except ValueError:
            pass
    if f_score_max != "":
        try:
            smax = int(f_score_max)
            filtered = [h for h in filtered if h.get("percent", 0) <= smax]
        except ValueError:
            pass

    # Range filter: keep records whose q_min/q_max overlap with the requested range
    if f_range_from != "":
        try:
            rf = int(f_range_from)
            filtered = [h for h in filtered if (h.get("q_max") or 0) >= rf]
        except ValueError:
            pass
    if f_range_to != "":
        try:
            rt = int(f_range_to)
            filtered = [h for h in filtered if (h.get("q_min") or 999999) <= rt]
        except ValueError:
            pass

    body = render_template_string(
        r"""
        <h2>History</h2>

        <!-- Filter bar -->
        <form method="get" style="background:#f8f8f8;border:1px solid #ddd;padding:12px 16px;border-radius:8px;margin-bottom:16px;">
          <div style="display:flex;flex-wrap:wrap;gap:12px;align-items:flex-end;">
            <div>
              <label class="small">User</label><br>
              <select name="f_user" style="padding:5px 8px;">
                <option value="">All users</option>
                {% for u in all_users %}
                  <option value="{{ u }}" {% if f_user == u %}selected{% endif %}>{{ u }}</option>
                {% endfor %}
              </select>
            </div>
            <div>
              <label class="small">Result</label><br>
              <select name="f_result" style="padding:5px 8px;">
                <option value="all"    {% if f_result == "all"    %}selected{% endif %}>All</option>
                <option value="passed" {% if f_result == "passed" %}selected{% endif %}>Passed</option>
                <option value="failed" {% if f_result == "failed" %}selected{% endif %}>Failed</option>
              </select>
            </div>
            <div>
              <label class="small">Date from</label><br>
              <input type="date" name="f_date_from" value="{{ f_date_from }}" style="padding:5px 6px;">
            </div>
            <div>
              <label class="small">Date to</label><br>
              <input type="date" name="f_date_to" value="{{ f_date_to }}" style="padding:5px 6px;">
            </div>
            <div>
              <label class="small">Score % min</label><br>
              <input type="number" name="f_score_min" value="{{ f_score_min }}" min="0" max="100"
                     style="width:70px;padding:5px 6px;">
            </div>
            <div>
              <label class="small">Score % max</label><br>
              <input type="number" name="f_score_max" value="{{ f_score_max }}" min="0" max="100"
                     style="width:70px;padding:5px 6px;">
            </div>
            <div>
              <label class="small">Q range from</label><br>
              <input type="number" name="f_range_from" value="{{ f_range_from }}" min="1"
                     style="width:70px;padding:5px 6px;" placeholder="e.g. 1">
            </div>
            <div>
              <label class="small">Q range to</label><br>
              <input type="number" name="f_range_to" value="{{ f_range_to }}" min="1"
                     style="width:70px;padding:5px 6px;" placeholder="e.g. 50">
            </div>
            <div style="display:flex;gap:8px;">
              <button class="btn" type="submit">Apply</button>
              <a class="btn btn-secondary" href="{{ url_for('history_page') }}">Reset</a>
            </div>
          </div>
        </form>

        {% if not filtered %}
          <p class="small">No records match the selected filters.</p>
        {% else %}
          <p class="small">Showing <strong>{{ filtered|length }}</strong> record(s).</p>
          <table>
            <tr>
              <th>Date / Time</th>
              <th>User</th>
              <th>Range</th>
              <th>Questions</th>
              <th>Correct</th>
              <th>Wrong</th>
              <th>Score</th>
              <th>Result</th>
            </tr>
            {% for h in filtered %}
              {% set passed = h.percent >= h.get('pass_percent', 70) if h.percent is defined else false %}
              <tr>
                <td>{{ h.timestamp }}</td>
                <td>{{ h.user or "" }}</td>
                <td>{% if h.q_min is not none and h.q_max is not none %}{{ h.q_min }} – {{ h.q_max }}{% else %}–{% endif %}</td>
                <td>{{ h.num_questions }}</td>
                <td>{{ h.num_correct }}</td>
                <td>{{ h.num_wrong }}</td>
                <td>{{ h.percent }}%</td>
                <td>
                  {% if h.percent >= h.get('pass_percent', 70) %}
                    <span class="correct">PASSED</span>
                  {% else %}
                    <span class="wrong">FAILED</span>
                  {% endif %}
                </td>
              </tr>
            {% endfor %}
          </table>
        {% endif %}
        """,
        filtered=filtered,
        all_users=all_users,
        f_user=f_user,
        f_result=f_result,
        f_date_from=f_date_from,
        f_date_to=f_date_to,
        f_score_min=f_score_min,
        f_score_max=f_score_max,
        f_range_from=f_range_from,
        f_range_to=f_range_to,
    )
    return page_layout("Quiz - History", body)

@app.route("/resume")
@login_required
def resume_quiz():
    """Load saved progress for the current user and resume the quiz."""
    username = session.get("user")
    p = get_user_progress(username)
    if not p:
        return redirect(url_for("quiz_setup"))

    # Restore to session
    session["quiz_indices"] = p.get("quiz_indices") or []
    session["pass_percent"] = p.get("pass_percent", 70)
    session["show_immediate"] = bool(p.get("show_immediate", False))
    session["current_index"] = int(p.get("current_index", 0))
    session["answers"] = p.get("answers") or {}

    return redirect(url_for("quiz_question"))


@app.route("/progress/clear", methods=["POST"])
@login_required
def clear_progress():
    """Clear saved progress for the current user."""
    username = session.get("user")
    clear_user_progress(username)
    for k in ["quiz_indices", "pass_percent", "show_immediate", "current_index", "answers"]:
        session.pop(k, None)
    return redirect(url_for("home"))




@app.route("/admin/questions/auto-images", methods=["POST"])
@admin_required
def admin_auto_attach_images():
    """
    Automatically attach images to questions based on filenames.

    It searches in TWO places:
      1) BASE_DIR/static/questions   (good if you commit images into the repo)
      2) IMG_DIR (defaults to DATA_DIR/question_images) (good if you copy images beside your data / persistent disk)

    Supported names: 1.png, 2.jpg, 101.jpeg, etc (number must match question number).
    """
    # Try to create IMG_DIR; if not possible we still proceed with static folder.
    try:
        IMG_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(f"[WARN] Could not create IMG_DIR {IMG_DIR}: {e}")

    # NOTE: Do NOT mkdir STATIC_IMG_DIR; in some hosts BASE_DIR may be read-only.
    static_dir = STATIC_IMG_DIR

    data = load_questions_raw()
    questions = data.get("questions") or []

    exts = [".png", ".jpg", ".jpeg", ".gif", ".webp"]

    def build_map(folder: Path):
        mapping = {}
        try:
            if folder.exists():
                for p in folder.iterdir():
                    if not p.is_file():
                        continue
                    lower = p.name.lower()
                    for ext in exts:
                        if lower.endswith(ext):
                            stem = p.stem
                            if stem.isdigit():
                                mapping[int(stem)] = p.name
                            break
        except Exception as e:
            print(f"[WARN] Could not scan {folder}: {e}")
        return mapping

    static_map = build_map(static_dir)
    imgdir_map = build_map(IMG_DIR)

    attached = 0
    skipped = 0
    source_static = 0
    source_imgdir = 0

    for idx, q in enumerate(questions, start=1):
        if idx in static_map:
            q["image_url"] = f"/static/questions/{static_map[idx]}"
            attached += 1
            source_static += 1
        elif idx in imgdir_map:
            q["image_url"] = f"/qimg/{imgdir_map[idx]}"
            attached += 1
            source_imgdir += 1
        else:
            skipped += 1

    data["questions"] = questions

    ok = save_questions_raw(data)
    # save_questions_raw returns None; _write_json returns bool but we don't propagate.
    # We'll verify file write by attempting to write and catching warnings in console.

    body = render_template_string(
        r"""
        <h2>Auto attach images</h2>
        <p><strong>Done.</strong></p>
        <ul>
          <li>Attached: {{ attached }}</li>
          <li>Missing match: {{ skipped }}</li>
          <li>From <code>static/questions</code>: {{ source_static }}</li>
          <li>From <code>IMG_DIR</code>: {{ source_imgdir }}</li>
        </ul>

        <p class="small">
          <strong>How it matches:</strong> it looks for files named like <code>1.png</code>, <code>25.jpg</code>, <code>101.jpeg</code>.
          The number must match the question number shown in the Question Bank.
        </p>

        <p class="small">
          <strong>Where to put images:</strong><br>
          Option A: commit them into <code>static/questions</code> (read-only but fine for reading).<br>
          Option B: copy them into <code>{{ img_dir }}</code> (best with a persistent disk). Those will be served via <code>/qimg/...</code>.
        </p>

        <p><a class="btn" href="{{ url_for('admin_questions') }}">Back to Question Bank</a></p>
        """,
        attached=attached,
        skipped=skipped,
        source_static=source_static,
        source_imgdir=source_imgdir,
        img_dir=str(IMG_DIR),
    )
    return page_layout("Auto attach images", body)


# ---------------------- Print / PDF ----------------------

@app.route("/print", methods=["GET", "POST"])
@login_required
def print_questions():
    """
    GET  – Show a checklist of all questions; user selects which to include.
    POST – Render a print-friendly HTML page that the browser can print/save-as-PDF.
    """
    questions = load_questions()
    total = len(questions)

    if request.method == "POST":
        # Collect selected question numbers (1-based original_index)
        selected_nums_raw = request.form.getlist("q_sel")
        try:
            selected_set = {int(x) for x in selected_nums_raw}
        except ValueError:
            selected_set = set()

        show_answers = request.form.get("show_answers") == "yes"

        selected_qs = [q for q in questions if q["original_index"] in selected_set]

        if not selected_qs:
            # Re-render selection page with an error
            return _print_select_page(questions, error="Please select at least one question.")

        letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

        # Build a standalone print-ready HTML page (no Flask layout)
        html_rows = []
        for q in selected_qs:
            rows = []
            for i, choice in enumerate(q["choices"]):
                letter = letters[i]
                if show_answers and letter in q["correct_letters"]:
                    rows.append(
                        f'<div class="choice correct-ans">✔ {letter}) {choice}</div>'
                    )
                else:
                    rows.append(f'<div class="choice">{letter}) {choice}</div>')

            answer_note = ""
            if show_answers:
                ans_str = ", ".join(q["correct_letters"]) if q["correct_letters"] else "?"
                answer_note = f'<div class="ans-note">Answer: {ans_str}</div>'

            img_html = ""
            if q.get("image_url"):
                img_html = f'<div class="q-img"><img src="{q["image_url"]}" alt="Q image"></div>'

            html_rows.append(
                f'<div class="question-block">'
                f'<div class="q-title">Q{q["original_index"]}. {q["question"]}</div>'
                f'{img_html}'
                f'<div class="choices">{"".join(rows)}</div>'
                f'{answer_note}'
                f'</div>'
            )

        all_html = "\n".join(html_rows)
        title_str = f"Quiz Questions ({len(selected_qs)} selected)"

        print_page = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{title_str}</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 20px; background: #fff; color: #000; }}
    h1 {{ font-size: 18px; margin-bottom: 6px; }}
    .meta {{ font-size: 12px; color: #666; margin-bottom: 20px; }}
    .question-block {{ border: 1px solid #ccc; padding: 10px 14px; border-radius: 6px;
                       margin-bottom: 14px; page-break-inside: avoid; }}
    .q-title {{ font-weight: bold; margin-bottom: 6px; }}
    .choices {{ margin-left: 10px; }}
    .choice {{ margin-bottom: 3px; font-size: 14px; }}
    .correct-ans {{ color: #1b5e20; font-weight: bold; }}
    .ans-note {{ margin-top: 6px; font-size: 12px; color: #555; font-style: italic; }}
    .q-img img {{ max-width: 100%; max-height: 240px; margin: 6px 0; }}
    .no-print {{ margin-bottom: 16px; }}
    @media print {{ .no-print {{ display: none; }} }}
  </style>
</head>
<body>
  <div class="no-print">
    <button onclick="window.print()" style="padding:8px 16px;background:#1976d2;color:#fff;
      border:none;border-radius:4px;cursor:pointer;font-size:14px;">🖨 Print / Save as PDF</button>
    &nbsp;
    <a href="/print" style="font-size:14px;">← Back to selection</a>
  </div>
  <h1>{title_str}</h1>
  <div class="meta">Generated {datetime.now().strftime("%Y-%m-%d %H:%M")}
  {"| Answers shown" if show_answers else "| Answers hidden"}</div>
  {all_html}
</body>
</html>"""
        return print_page

    # GET – show selection page
    return _print_select_page(questions)


def _print_select_page(questions, error=""):
    """Render the question-selection form for printing."""
    total = len(questions)
    body = render_template_string(
        r"""
        <h2>Print Questions to PDF</h2>
        {% if error %}<div class="error">{{ error }}</div>{% endif %}

        {% if total == 0 %}
          <p class="wrong">No questions available.</p>
        {% else %}
          <form method="post" action="{{ url_for('print_questions') }}">

            <!-- Quick-select controls -->
            <div style="margin-bottom:12px;display:flex;flex-wrap:wrap;gap:10px;align-items:center;">
              <strong>Select:</strong>
              <button type="button" class="btn btn-secondary"
                onclick="document.querySelectorAll('.q-cb').forEach(c=>c.checked=true)">All</button>
              <button type="button" class="btn btn-secondary"
                onclick="document.querySelectorAll('.q-cb').forEach(c=>c.checked=false)">None</button>

              <!-- Range quick-select -->
              <span class="small" style="margin-left:8px;">Range:</span>
              <input type="number" id="sel_from" min="1" max="{{ total }}" value="1"
                     style="width:70px;padding:4px 6px;">
              <span class="small">to</span>
              <input type="number" id="sel_to" min="1" max="{{ total }}" value="{{ total }}"
                     style="width:70px;padding:4px 6px;">
              <button type="button" class="btn btn-secondary" onclick="selectRange()">Select range</button>
            </div>

            <!-- Show answers toggle -->
            <div class="field" style="margin-bottom:14px;">
              <label>Include answers?</label>&nbsp;
              <label><input type="radio" name="show_answers" value="yes"> Yes (show correct answers)</label>&nbsp;
              <label><input type="radio" name="show_answers" value="no" checked> No (questions only)</label>
            </div>

            <!-- Question checklist -->
            <div style="max-height:420px;overflow-y:auto;border:1px solid #ddd;border-radius:6px;
                        padding:10px 14px;margin-bottom:14px;">
              {% for q in questions %}
                <div style="margin-bottom:6px;">
                  <label>
                    <input class="q-cb" type="checkbox" name="q_sel" value="{{ q.original_index }}" checked>
                    <strong>Q{{ q.original_index }}.</strong>
                    {{ q.question[:100] }}{% if q.question|length > 100 %}…{% endif %}
                  </label>
                </div>
              {% endfor %}
            </div>

            <button class="btn" type="submit">Generate PDF preview</button>
            <a class="btn btn-secondary" href="{{ url_for('home') }}" style="margin-left:8px;">Cancel</a>
          </form>

          <script>
          function selectRange() {
            var from = parseInt(document.getElementById('sel_from').value) || 1;
            var to   = parseInt(document.getElementById('sel_to').value)   || {{ total }};
            document.querySelectorAll('.q-cb').forEach(function(cb) {
              var n = parseInt(cb.value);
              cb.checked = (n >= from && n <= to);
            });
          }
          </script>
        {% endif %}
        """,
        questions=questions,
        total=total,
        error=error,
    )
    return page_layout("Print Questions", body)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)