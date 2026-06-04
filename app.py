import sqlite3
import csv
import io
import os
import hashlib
import functools
from pathlib import Path
from flask import (Flask, request, redirect, url_for, render_template,
                   session, flash, jsonify, Response)

# ---------------- Config ----------------
BASE = str(Path.home() / ".callsviewer")
os.makedirs(BASE, exist_ok=True)
DB_PATH = os.path.join(BASE, "local.db")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret-key-in-production")

AREA_MAP = {
    '334':'AL','907':'AK','602':'AZ','501':'AR','916':'CA','303':'CO','860':'CT',
    '302':'DE','850':'FL','404':'GA','808':'HI','208':'ID','217':'IL','317':'IN',
    '515':'IA','785':'KS','502':'KY','225':'LA','207':'ME','410':'MD','617':'MA',
    '517':'MI','651':'MN','601':'MS','573':'MO','406':'MT','402':'NE','775':'NV',
    '603':'NH','609':'NJ','505':'NM','518':'NY','919':'NC','701':'ND','614':'OH',
    '405':'OK','503':'OR','717':'PA','401':'RI','803':'SC','605':'SD','615':'TN',
    '512':'TX','801':'UT','802':'VT','804':'VA','360':'WA','304':'WV','608':'WI','307':'WY'}

EDIT_FIELDS = ("call_date","phone_number","status","length_in_sec","campaign","term_reason","user")


# ---------------- Database ----------------
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    conn.execute("""CREATE TABLE IF NOT EXISTS country (
        state_code TEXT PRIMARY KEY, state_name TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS calls (
        id INTEGER PRIMARY KEY AUTOINCREMENT, uniqueid TEXT, lead_id TEXT, list_id TEXT,
        campaign TEXT, call_date TEXT, start_epoch TEXT, end_epoch TEXT, length_in_sec TEXT,
        status TEXT, phone_code TEXT, phone_number TEXT, user TEXT, comments TEXT,
        sub_status TEXT, user_group TEXT, xfer_status TEXT, term_reason TEXT,
        call_type TEXT, state_code TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        role TEXT DEFAULT 'user',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")

    default_pass = hashlib.sha256("admin123".encode()).hexdigest()
    conn.execute("INSERT OR IGNORE INTO users (username, password, role) VALUES (?, ?, ?)",
                 ("admin", default_pass, "admin"))

    states = [('AL','Alabama'),('AK','Alaska'),('AZ','Arizona'),('AR','Arkansas'),
        ('CA','California'),('CO','Colorado'),('CT','Connecticut'),('DE','Delaware'),
        ('FL','Florida'),('GA','Georgia'),('HI','Hawaii'),('ID','Idaho'),
        ('IL','Illinois'),('IN','Indiana'),('IA','Iowa'),('KS','Kansas'),
        ('KY','Kentucky'),('LA','Louisiana'),('ME','Maine'),('MD','Maryland'),
        ('MA','Massachusetts'),('MI','Michigan'),('MN','Minnesota'),('MS','Mississippi'),
        ('MO','Missouri'),('MT','Montana'),('NE','Nebraska'),('NV','Nevada'),
        ('NH','New Hampshire'),('NJ','New Jersey'),('NM','New Mexico'),('NY','New York'),
        ('NC','North Carolina'),('ND','North Dakota'),('OH','Ohio'),('OK','Oklahoma'),
        ('OR','Oregon'),('PA','Pennsylvania'),('RI','Rhode Island'),('SC','South Carolina'),
        ('SD','South Dakota'),('TN','Tennessee'),('TX','Texas'),('UT','Utah'),
        ('VT','Vermont'),('VA','Virginia'),('WA','Washington'),('WV','West Virginia'),
        ('WI','Wisconsin'),('WY','Wyoming')]
    conn.executemany("INSERT OR IGNORE INTO country VALUES (?,?)", states)
    conn.commit()
    conn.close()

init_db()


# ---------------- Auth helper ----------------
def login_required(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()


# ---------------- Auth routes ----------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        if not username or not password:
            flash("Username aur Password dono likhein!", "error")
            return render_template("login.html")
        conn = get_conn()
        user = conn.execute(
            "SELECT id, username, role FROM users WHERE username=? AND password=?",
            (username, hash_pw(password))).fetchone()
        conn.close()
        if user:
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user["role"]
            return redirect(url_for("dashboard"))
        flash("Galat Username ya Password!", "error")
    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        password2 = request.form.get("password2", "").strip()
        if not username or not password:
            flash("Sab fields bharein!", "error")
        elif len(username) < 3:
            flash("Username kam az kam 3 characters ka ho!", "error")
        elif len(password) < 4:
            flash("Password kam az kam 4 characters ka ho!", "error")
        elif password != password2:
            flash("Passwords match nahi kar rahe!", "error")
        else:
            conn = get_conn()
            try:
                conn.execute("INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
                             (username, hash_pw(password), "user"))
                conn.commit()
                conn.close()
                flash(f"Account '{username}' ban gaya! Ab login karein.", "success")
                return redirect(url_for("login"))
            except sqlite3.IntegrityError:
                conn.close()
                flash("Ye username pehle se mojood hai!", "error")
    return render_template("register.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------- Main routes ----------------
@app.route("/")
@login_required
def dashboard():
    conn = get_conn()
    states = conn.execute(
        "SELECT state_code, state_name FROM country ORDER BY state_name").fetchall()
    conn.close()
    return render_template("dashboard.html", states=states, username=session["username"])


@app.route("/api/summary/<state_code>")
@login_required
def api_summary(state_code):
    conn = get_conn()
    row = conn.execute("""SELECT COUNT(*) AS total,
        SUM(CASE WHEN status='A' THEN 1 ELSE 0 END) AS answered,
        SUM(CASE WHEN status IN ('N','NA','NP','NI') THEN 1 ELSE 0 END) AS noanswer,
        ROUND(AVG(CAST(length_in_sec AS REAL)),1) AS avg_dur,
        MAX(call_date) AS last_call
        FROM calls WHERE state_code=?""", (state_code,)).fetchone()
    conn.close()
    return jsonify({
        "total": row["total"] or 0,
        "answered": row["answered"] or 0,
        "noanswer": row["noanswer"] or 0,
        "avg_dur": row["avg_dur"] or 0,
        "last": (row["last_call"] or "")[:10],
    })


@app.route("/api/calls/<state_code>")
@login_required
def api_calls(state_code):
    conn = get_conn()
    rows = conn.execute("""SELECT id,call_date,phone_number,status,length_in_sec,
        campaign,term_reason,user FROM calls WHERE state_code=?
        ORDER BY call_date DESC LIMIT 500""", (state_code,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/calls/add", methods=["POST"])
@login_required
def api_add():
    data = request.json
    state_code = data.get("state_code")
    vals = [data.get(f, "") for f in EDIT_FIELDS]
    phone = vals[1]
    area = phone[-10:-7] if len(phone) >= 10 else None
    sc = AREA_MAP.get(area, state_code)
    conn = get_conn()
    cur = conn.execute("""INSERT INTO calls
        (call_date,phone_number,status,length_in_sec,campaign,term_reason,user,state_code)
        VALUES (?,?,?,?,?,?,?,?)""", (*vals, sc))
    new_id = cur.lastrowid
    conn.commit()
    conn.close()
    return jsonify({"id": new_id, "status": "ok"})


@app.route("/api/calls/update/<int:row_id>", methods=["POST"])
@login_required
def api_update(row_id):
    data = request.json
    vals = [data.get(f, "") for f in EDIT_FIELDS]
    conn = get_conn()
    conn.execute("""UPDATE calls SET call_date=?,phone_number=?,status=?,
        length_in_sec=?,campaign=?,term_reason=?,user=? WHERE id=?""", (*vals, row_id))
    conn.commit()
    conn.close()
    return jsonify({"status": "ok"})


@app.route("/api/calls/delete", methods=["POST"])
@login_required
def api_delete():
    ids = request.json.get("ids", [])
    conn = get_conn()
    conn.executemany("DELETE FROM calls WHERE id=?", [(i,) for i in ids])
    conn.commit()
    conn.close()
    return jsonify({"status": "ok", "deleted": len(ids)})


@app.route("/api/import", methods=["POST"])
@login_required
def api_import():
    file = request.files.get("file")
    if not file:
        return jsonify({"error": "No file"}), 400
    conn = get_conn()
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_calls_uniqueid ON calls(uniqueid)")
    conn.commit()
    inserted = skipped = 0
    batch = []
    stream = io.StringIO(file.stream.read().decode("utf-8", errors="replace"))
    for row in csv.reader(stream):
        if len(row) < 18:
            continue
        phone = row[10]
        area = phone[-10:-7] if len(phone) >= 10 else None
        sc = AREA_MAP.get(area)
        batch.append((*row, sc))
        if len(batch) == 5000:
            r = conn.executemany("""INSERT OR IGNORE INTO calls
                (uniqueid,lead_id,list_id,campaign,call_date,start_epoch,end_epoch,
                 length_in_sec,status,phone_code,phone_number,user,comments,sub_status,
                 user_group,xfer_status,term_reason,call_type,state_code)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", batch)
            inserted += r.rowcount
            skipped += len(batch) - r.rowcount
            batch = []
            conn.commit()
    if batch:
        r = conn.executemany("""INSERT OR IGNORE INTO calls
            (uniqueid,lead_id,list_id,campaign,call_date,start_epoch,end_epoch,
             length_in_sec,status,phone_code,phone_number,user,comments,sub_status,
             user_group,xfer_status,term_reason,call_type,state_code)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", batch)
        inserted += r.rowcount
        skipped += len(batch) - r.rowcount
        conn.commit()
    conn.close()
    return jsonify({"inserted": inserted, "skipped": skipped})


@app.route("/api/export/<state_code>")
@login_required
def api_export(state_code):
    conn = get_conn()
    rows = conn.execute("""SELECT id,call_date,phone_number,status,length_in_sec,
        campaign,term_reason,user FROM calls WHERE state_code=?
        ORDER BY call_date DESC""", (state_code,)).fetchall()
    conn.close()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID","Call Date","Phone Number","Status","Duration (s)",
                     "Campaign","Term Reason","User"])
    writer.writerows([tuple(r) for r in rows])
    return Response(output.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment;filename=calls_{state_code}.csv"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)