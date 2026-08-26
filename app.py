import os, random, string, sqlite3, time
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_from_directory, make_response
from flask_socketio import SocketIO, join_room, emit

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static")
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "raja-rani-secret-key")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# Render-safe CSS fallback routes. These bypass any static-file/proxy issue while
# keeping the normal /static URL available. Cache is disabled during deployments
# so an older stylesheet cannot remain stuck in the browser/CDN cache.
@app.route("/assets/css/style.css")
def css_fallback():
    response = make_response(send_from_directory(STATIC_DIR, "css/style.css"))
    response.headers["Content-Type"] = "text/css; charset=utf-8"
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response

@app.route("/style.css")
def css_short_fallback():
    return css_fallback()

@app.route("/favicon.ico")
def favicon_ico():
    return send_from_directory(STATIC_DIR, "favicon.svg", mimetype="image/svg+xml")
DB = os.path.join(BASE_DIR, "database.db")

CHARACTERS = {
    "King": {"tamil": "இராசா / அரசன்", "points": 1000},
    "Queen": {"tamil": "ராணி / அரசி", "points": 800},
    "Prime Minister": {"tamil": "மந்திரி", "points": 400},
    "Chief Judge": {"tamil": "நீதிபதி", "points": 350},
    "Commander": {"tamil": "சேனாபதி", "points": 300},
    "Police": {"tamil": "காவல்துறை", "points": 500},
    "Soldier": {"tamil": "படைவீரர்", "points": 250},
    "Courtier": {"tamil": "சபையோர்", "points": 200},
    "Citizen": {"tamil": "குடிமகன்", "points": 100},
    "Thief": {"tamil": "திருடன் / கள்ளன்", "points": 0},
}
MAIN_REQUIRED = ["King", "Queen", "Police", "Thief"]

def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    con = db()
    con.execute("""CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        email TEXT UNIQUE,
        password TEXT NOT NULL,
        kint_credits INTEGER NOT NULL DEFAULT 0,
        profile_pic TEXT
    )""")
    cols = {row[1] for row in con.execute("PRAGMA table_info(users)").fetchall()}
    if "kint_credits" not in cols:
        con.execute("ALTER TABLE users ADD COLUMN kint_credits INTEGER NOT NULL DEFAULT 0")
    if "profile_pic" not in cols:
        con.execute("ALTER TABLE users ADD COLUMN profile_pic TEXT")
    con.execute("""CREATE TABLE IF NOT EXISTS friend_requests(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sender_id INTEGER NOT NULL,
        receiver_id INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(sender_id, receiver_id)
    )""")
    con.commit()
    con.close()

# Initialize the SQLite database when Gunicorn imports this module.
# (The __main__ block does not run under Gunicorn.)
init_db()

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper

rooms = {}

def code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

@app.route("/")
def index():
    return redirect(url_for("dashboard") if "user_id" in session else url_for("login"))

@app.route("/register", methods=["GET","POST"])
def register():
    error = None
    if request.method == "POST":
        username = request.form["username"].strip()
        email = request.form["email"].strip()
        password = request.form["password"]
        if not username or not password:
            error = "Username and password are required."
        else:
            try:
                con = db()
                con.execute("INSERT INTO users(username,email,password) VALUES(?,?,?)",
                            (username, email, password))
                con.commit(); con.close()
                return redirect(url_for("login"))
            except sqlite3.IntegrityError:
                error = "Username or email already exists."
    return render_template("register.html", error=error)

@app.route("/login", methods=["GET","POST"])
def login():
    error = None
    if request.method == "POST":
        identity = request.form["identity"].strip()
        password = request.form["password"]
        con = db()
        user = con.execute(
            "SELECT * FROM users WHERE (username=? OR email=?) AND password=?",
            (identity, identity, password)
        ).fetchone()
        con.close()
        if user:
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            return redirect(url_for("dashboard"))
        error = "Invalid username/email or password."
    return render_template("login.html", error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html", username=session["username"])

@app.route("/api/me")
@login_required
def me_api():
    con = db()
    row = con.execute("SELECT id,username,email,profile_pic,kint_credits FROM users WHERE id=?",
                      (session["user_id"],)).fetchone()
    con.close()
    return jsonify(dict(row))

@app.route("/api/profile", methods=["POST"])
@login_required
def update_home_profile():
    data = request.get_json(silent=True) or {}
    pic = data.get("profile_pic")
    if pic is not None and (not isinstance(pic, str) or len(pic) > 2_000_000 or not pic.startswith("data:image/")):
        return jsonify({"error":"Invalid profile image"}), 400
    con = db()
    con.execute("UPDATE users SET profile_pic=? WHERE id=?", (pic, session["user_id"]))
    con.commit(); con.close()
    return jsonify({"profile_pic": pic})

@app.route("/api/friends")
@login_required
def friends_api():
    uid = session["user_id"]
    con = db()
    pending = con.execute("""
        SELECT fr.id, u.username, u.profile_pic
        FROM friend_requests fr JOIN users u ON u.id=fr.sender_id
        WHERE fr.receiver_id=? AND fr.status='pending' ORDER BY fr.id DESC
    """, (uid,)).fetchall()
    friends = con.execute("""
        SELECT u.id,u.username,u.profile_pic
        FROM users u
        WHERE u.id IN (
          SELECT CASE WHEN sender_id=? THEN receiver_id ELSE sender_id END
          FROM friend_requests
          WHERE status='accepted' AND (sender_id=? OR receiver_id=?)
        )
        ORDER BY u.username
    """, (uid,uid,uid)).fetchall()
    sent = con.execute("""
        SELECT u.username
        FROM friend_requests fr JOIN users u ON u.id=fr.receiver_id
        WHERE fr.sender_id=? AND fr.status='pending'
    """, (uid,)).fetchall()
    con.close()
    return jsonify({
        "pending":[dict(x) for x in pending],
        "friends":[dict(x) for x in friends],
        "sent":[dict(x) for x in sent]
    })

@app.route("/api/friends/search")
@login_required
def search_users():
    q = request.args.get("q","").strip()
    if not q: return jsonify({"users":[]})
    con = db()
    rows = con.execute("""
        SELECT id,username,profile_pic FROM users
        WHERE username LIKE ? AND id != ?
        ORDER BY username LIMIT 10
    """, (f"%{q}%",session["user_id"])).fetchall()
    con.close()
    return jsonify({"users":[dict(x) for x in rows]})

@app.route("/api/friends/request", methods=["POST"])
@login_required
def send_friend_request():
    target = (request.get_json(silent=True) or {}).get("username","").strip()
    con = db()
    user = con.execute("SELECT id FROM users WHERE username=?", (target,)).fetchone()
    if not user or user["id"] == session["user_id"]:
        con.close(); return jsonify({"error":"Player not found."}),404
    a,b=session["user_id"],user["id"]
    existing=con.execute("""
        SELECT id,status FROM friend_requests
        WHERE (sender_id=? AND receiver_id=?) OR (sender_id=? AND receiver_id=?)
    """,(a,b,b,a)).fetchone()
    if existing:
        if existing["status"]=="accepted":
            msg="Already friends."
        elif existing["status"]=="pending":
            msg="Friend request already pending."
        else:
            con.execute("UPDATE friend_requests SET sender_id=?,receiver_id=?,status='pending' WHERE id=?",
                        (a,b,existing["id"]))
            con.commit(); msg="Friend request sent."
    else:
        con.execute("INSERT INTO friend_requests(sender_id,receiver_id,status) VALUES(?,?, 'pending')",(a,b))
        con.commit(); msg="Friend request sent."
    con.close()
    return jsonify({"message":msg})

@app.route("/api/friends/respond", methods=["POST"])
@login_required
def respond_friend_request():
    data=request.get_json(silent=True) or {}
    rid=int(data.get("request_id",0))
    action=data.get("action")
    if action not in ("accept","decline"):
        return jsonify({"error":"Invalid action"}),400
    con=db()
    row=con.execute("SELECT * FROM friend_requests WHERE id=? AND receiver_id=? AND status='pending'",
                    (rid,session["user_id"])).fetchone()
    if not row:
        con.close(); return jsonify({"error":"Request not found"}),404
    con.execute("UPDATE friend_requests SET status=? WHERE id=?",
                ("accepted" if action=="accept" else "declined",rid))
    con.commit(); con.close()
    return jsonify({"message":"Done"})

@app.route("/create", methods=["GET","POST"])
@login_required
def create():
    if request.method=="POST":
        selected=request.form.getlist("characters")
        if not all(x in selected for x in MAIN_REQUIRED):
            return render_template("create_game.html", error="King, Queen, Police and Thief are mandatory.", chars=CHARACTERS)
        if len(selected)<4 or len(selected)>10:
            return render_template("create_game.html", error="Select between 4 and 10 characters.", chars=CHARACTERS)
        timer_raw=(request.form.get("police_timer_seconds") or "60").strip()
        try:
            if ":" in timer_raw:
                mm,ss=timer_raw.split(":",1)
                timer_seconds=int(mm)*60+int(ss)
            else:
                timer_seconds=int(timer_raw)
        except (ValueError,TypeError):
            return render_template("create_game.html", error="Police timer must be between 1 and 60 seconds (for example 60 or 0:45).", chars=CHARACTERS)
        if not 1 <= timer_seconds <= 60:
            return render_template("create_game.html", error="Police timer must be between 1 and 60 seconds.", chars=CHARACTERS)
        room=code()
        rooms[room]={
            "_room_code":room, "host":session["username"], "players":{}, "characters":selected,
            "started":False, "closed":False, "final_results":None,
            "police_timer_seconds":timer_seconds,
            "round_number":0, "round_history":[], "last_reveal":None, "round_started_at":None, "police_deadline":None, "police_sheet_viewed":False, "police_timer_round":0
        }
        rooms[room]["players"][session["username"]]=new_player(session["username"],False)
        return redirect(url_for("room",room_code=room))
    return render_template("create_game.html", error=None, chars=CHARACTERS)

def new_player(username, system=False):
    return {"username":username,"sid":None,"system":system,"character":None,
            "score":0,"viewed":False,"profile_pic":None,"guessed":False}

def is_friend(username):
    con=db()
    row=con.execute("""
        SELECT 1 FROM friend_requests a JOIN users u ON u.id=a.receiver_id
        WHERE a.sender_id=? AND u.username=? AND a.status='accepted'
        UNION
        SELECT 1 FROM friend_requests a JOIN users u ON u.id=a.sender_id
        WHERE a.receiver_id=? AND u.username=? AND a.status='accepted'
    """,(session["user_id"],username,session["user_id"],username)).fetchone()
    con.close()
    return bool(row)

@app.route("/join", methods=["GET","POST"])
@login_required
def join():
    error=None
    if request.method=="POST":
        room=request.form["room_code"].strip().upper()
        if room not in rooms: error="Room not found."
        elif rooms[room]["started"]: error="Game already started."
        elif len(rooms[room]["players"])>=10: error="Room is full."
        elif session["username"] in rooms[room]["players"]: return redirect(url_for("room",room_code=room))
        else:
            rooms[room]["players"][session["username"]]=new_player(session["username"],False)
            return redirect(url_for("room",room_code=room))
    return render_template("join_game.html", error=error)

@app.route("/room/<room_code>")
@login_required
def room(room_code):
    if room_code not in rooms: return "Room not found",404
    return render_template("room.html", room_code=room_code, host=rooms[room_code]["host"])

@app.route("/game/<room_code>")
@login_required
def game(room_code):
    if room_code not in rooms or not rooms[room_code]["started"]:
        return redirect(url_for("room",room_code=room_code))
    return render_template("game.html",room_code=room_code)

def get_kint_credits(username):
    con=db(); row=con.execute("SELECT kint_credits FROM users WHERE username=?",(username,)).fetchone()
    con.close(); return int(row["kint_credits"] if row else 0)

def add_kint_credit(username, amount=1):
    con=db(); con.execute("UPDATE users SET kint_credits=COALESCE(kint_credits,0)+? WHERE username=?",(amount,username))
    con.commit(); con.close()

def use_kint_credit(username):
    con=db()
    row=con.execute("SELECT kint_credits FROM users WHERE username=?",(username,)).fetchone()
    credits=int(row["kint_credits"] if row else 0)
    if credits<=0: con.close(); return False
    con.execute("UPDATE users SET kint_credits=kint_credits-1 WHERE username=?",(username,))
    con.commit(); con.close(); return True

def public_player(p, include_score=False):
    x={"username":p["username"],"system":p["system"]}
    if include_score: x["score"]=p.get("score",0)
    return x

@app.route("/api/room/<room_code>")
@login_required
def room_api(room_code):
    r=rooms.get(room_code)
    if not r:return jsonify({"error":"Room not found"}),404
    closed=bool(r.get("closed"))
    police=next((p for p in r["players"].values() if p.get("character")=="Police"),None)
    deadline=r.get("police_deadline")
    remaining=max(0,int(deadline-time.time())) if deadline and r.get("police_sheet_viewed") and not r.get("last_reveal") and not closed else 0
    return jsonify({
        "host":r["host"],"started":r["started"],"closed":closed,
        "round":r.get("round_number",0),
        "players":[public_player(p,closed) for p in r["players"].values()],
        "round_history":r.get("round_history",[]),
        "last_reveal":r.get("last_reveal") if r.get("last_reveal") else None,
        "police":police.get("username") if police else None,
        "police_system":bool(police and police.get("system")),
        "timer_remaining":remaining,"police_deadline":deadline,"police_sheet_viewed":bool(r.get("police_sheet_viewed")),"police_timer_seconds":r.get("police_timer_seconds",60)
    })

@app.route("/api/my-sheet/<room_code>")
@login_required
def my_sheet(room_code):
    r=rooms.get(room_code); p=r["players"].get(session["username"]) if r else None
    if not p or not p["character"]: return jsonify({"error":"Sheet not assigned"}),404
    p["viewed"] = True
    # The Police clock starts ONLY after the Police opens their private sheet.
    if p.get("character")=="Police" and r.get("started") and not r.get("closed") and not r.get("last_reveal") and not r.get("police_sheet_viewed"):
        r["police_sheet_viewed"] = True
        r["police_deadline"] = time.time() + r.get("police_timer_seconds",60)
        r["police_timer_round"] = r.get("round_number", 0)
        room_code_local=room_code
        socketio.start_background_task(_round_timer, room_code_local, r.get("round_number", 0))
        emit_data={"round":r.get("round_number",0),"police_deadline":r["police_deadline"]}
        socketio.emit("police_timer_started", emit_data, room=room_code)
        police=p
        if police.get("system"):
            pass
    c=CHARACTERS[p["character"]]
    return jsonify({"character":p["character"],"tamil":c["tamil"],"role_points":c["points"],"base_points":c["points"],"score":p.get("score",0),"timer_started":bool(r.get("police_sheet_viewed")),"police_timer_seconds":r.get("police_timer_seconds",60)})

@app.route("/api/results/<room_code>")
@login_required
def results(room_code):
    r=rooms.get(room_code)
    if not r:return jsonify({"error":"Room not found"}),404
    rows=sorted(r["players"].values(),key=lambda x:x["score"],reverse=True)
    return jsonify([{"rank":i+1,"username":p["username"],"score":p["score"],
                     "grade":grade_for_score(p["score"]),"character":p["character"]}
                    for i,p in enumerate(rows)])

def grade_for_score(score):
    if score>=1000:return "A+"
    if score>=800:return "A"
    if score>=600:return "B+"
    if score>=500:return "B"
    if score>=300:return "C"
    return "D"

@socketio.on("connect")
def connect(): pass

@socketio.on("join_room_game")
def socket_join(data):
    room=data["room"]; username=session.get("username")
    if room not in rooms or username not in rooms[room]["players"]: return
    rooms[room]["players"][username]["sid"]=request.sid
    join_room(room)
    emit("room_update",room_state(room),room=room)

def room_state(room):
    r=rooms[room]
    return {"host":r["host"],"started":r["started"],
            "players":[public_player(p) for p in r["players"].values()]}

@socketio.on("invite_friend")
def invite_friend(data):
    room=data.get("room"); username=(data.get("username") or "").strip()
    r=rooms.get(room)
    if not r or r.get("started") or session.get("username")!=r.get("host"):
        return
    if username==session["username"] or not is_friend(username):
        emit("room_notice",{"message":"Only accepted friends can be invited."},to=request.sid); return
    if username in r["players"]:
        emit("room_notice",{"message":"That friend is already in the room."},to=request.sid); return
    if len(r["players"])>=10:
        emit("room_notice",{"message":"Room is full."},to=request.sid); return
    r["players"][username]=new_player(username,False)
    emit("room_update",room_state(room),room=room)

@app.route("/api/add-system/<room_code>", methods=["POST"])
@login_required
def add_system_http(room_code):
    r=rooms.get(room_code)
    if not r:
        return jsonify({"error":"Room not found."}),404
    if session.get("username") != r.get("host"):
        return jsonify({"error":"Only the room host can add system players."}),403
    if r.get("started"):
        return jsonify({"error":"Game already started."}),400
    while len(r["players"]) < 4:
        name=f"System{len([p for p in r['players'].values() if p['system']])+1}"
        while name in r["players"]:
            name += "X"
        r["players"][name]=new_player(name,True)
    socketio.emit("room_update", room_state(room_code), room=room_code)
    return jsonify(room_state(room_code))

@socketio.on("add_system")
def add_system(data):
    room=data["room"]; r=rooms.get(room)
    if not r or r["started"]: return
    while len(r["players"])<4:
        name=f"System{len([p for p in r['players'].values() if p['system']])+1}"
        while name in r["players"]: name+="X"
        r["players"][name]=new_player(name,True)
    emit("room_update",room_state(room),room=room)

def assign_round(r, first=False):
    chars=r["characters"][:]
    optional=["Prime Minister","Chief Judge","Commander","Soldier","Courtier","Citizen"]
    for c in optional:
        if len(chars)<len(r["players"]) and c not in chars: chars.append(c)
    chars=chars[:len(r["players"])]
    random.shuffle(chars)
    for name,p in r["players"].items():
        p["character"]=chars.pop(0); p["viewed"]=False; p["guessed"]=False
    r["last_reveal"]=None

def award_round_role_points(r):
    """Add each non-Police/non-Thief role's value once for the current round.

    Police and Thief NEVER receive their role value as a base score. Their score
    changes only through the Police lock/timeout rules (+500 to the correct side).
    """
    round_no = r.get("round_number", 1)
    if r.get("role_points_awarded_round") == round_no:
        return []

    awarded=[]
    for p in r["players"].values():
        role=p.get("character")
        if role in ("Police", "Thief"):
            continue
        points=CHARACTERS.get(role, {}).get("points", 0)
        p["score"] = p.get("score", 0) + points
        if points:
            awarded.append({"username":p["username"],"character":role,"points":points})

    r["role_points_awarded_round"] = round_no
    return awarded

def perform_start_game(room):
    """Start a room using the same authoritative logic for Socket.IO and HTTP fallback."""
    r=rooms.get(room)
    if not r:
        return False, "Room not found."
    if session.get("username") != r["host"]:
        return False, "Only the room host can start the game."
    if r.get("started"):
        return False, "Game has already started."

    # Always top up the room to exactly 4 players with system/bot players.
    while len(r["players"]) < 4:
        n=f"System{len([p for p in r['players'].values() if p['system']])+1}"
        while n in r["players"]:
            n += "X"
        r["players"][n]=new_player(n,True)

    if len(r["players"]) > 10:
        return False, "Maximum 10 players allowed."

    if len(r["characters"]) < len(r["players"]):
        optional=["Prime Minister","Chief Judge","Commander","Soldier","Courtier","Citizen"]
        for c in optional:
            if c not in r["characters"]:
                r["characters"].append(c)
            if len(r["characters"]) >= len(r["players"]):
                break
    r["characters"] = r["characters"][:len(r["players"])]
    assign_round(r,True)
    r["started"]=True; r["closed"]=False; r["round_number"]=1
    r["round_history"]=[]; r["last_reveal"]=None; r["kint_active"]={}; r["system_king_pending"]=False
    r["role_points_awarded_round"] = 0
    r["round_started_at"]=time.time()
    round_role_points = award_round_role_points(r)
    r["current_round_role_points"] = round_role_points
    r["police_deadline"]=None
    r["police_sheet_viewed"]=False
    r["police_timer_round"]=0

    police=next((p for p in r["players"].values() if p["character"]=="Police"),None)
    if police and police.get("system"):
        police["viewed"] = True
        r["police_sheet_viewed"] = True
        r["police_deadline"] = time.time()+r.get("police_timer_seconds",60)
        r["police_timer_round"] = 1
        socketio.start_background_task(_round_timer,room,1)
        socketio.emit("police_timer_started",{"round":1,"police_deadline":r["police_deadline"]},room=room)
        candidates=[p["username"] for p in r["players"].values() if p["username"]!=police["username"]]
        if candidates:
            limit=max(1,r.get("police_timer_seconds",60))
            delay=random.uniform(1,max(1,limit-1)) if limit>1 else 0.2
            socketio.start_background_task(_system_police_act,room,1,random.choice(candidates),delay)

    return True, {"round":1,"police_deadline":r["police_deadline"],"role_points_added":round_role_points}

@app.route("/api/start-game/<room_code>", methods=["POST"])
@login_required
def start_game_http(room_code):
    ok, result = perform_start_game(room_code)
    if not ok:
        return jsonify({"error":result}), 400
    socketio.emit("room_update", room_state(room_code), room=room_code)
    socketio.emit("game_started", result, room=room_code)
    return jsonify({"ok":True, **result, "redirect":url_for("game", room_code=room_code)})

@socketio.on("start_game")
def start_game(data):
    room=data.get("room")
    ok, result = perform_start_game(room)
    if not ok:
        emit("start_error", {"message":result}, to=request.sid)
        return
    emit("game_started", result, room=room)

def build_reveal(r, record):
    # Only called after the Police locks. This is the public reveal for the round.
    rows=[]
    for p in r["players"].values():
        rows.append({"username":p["username"],"character":p["character"],
                     "base_points":CHARACTERS[p["character"]]["points"],
                     "score":p["score"]})
    return {"round":record["round"],"police":record["police"],
            "guessed":record["guessed"],"thief":record["thief"],
            "correct":record["correct"],"outcome":record["outcome"],
            "players":rows}

def finish_guess(r, police, thief, guess):
    correct=(guess==thief["username"])
    if correct:
        police["score"]+=500
        outcome=f"RIGHT! {guess} is the Thief. Police gets +500 bonus."
    else:
        thief["score"]+=500
        outcome=f"WRONG! {guess} is not the Thief. The Thief is {thief['username']}. Thief gets +500 bonus."
    police["guessed"]=True
    record={"round":r.get("round_number",1),"police":police["username"],"guessed":guess,
            "thief":thief["username"],"correct":correct,"outcome":outcome,
            "points_added":{"username":police["username"] if correct else thief["username"],"points":500},
            "round_role_points": r.get("current_round_role_points", []),
            "round_complete":True}
    r["last_reveal"]=build_reveal(r,record)
    record["players"]=r["last_reveal"]["players"]
    r["round_history"].append(record)
    r["police_deadline"]=None
    r["police_sheet_viewed"]=False
    r["police_timer_round"]=0
    _maybe_schedule_system_king(r.get("_room_code")) if r.get("_room_code") else None
    return record

def _round_timer(room, round_number):
    # Server-authoritative police window configured when the room was created.
    socketio.sleep(r.get("police_timer_seconds",60) if (r:=rooms.get(room)) else 60)
    r=rooms.get(room)
    if not r or r.get("closed") or not r.get("started"): return
    if r.get("round_number")!=round_number or r.get("last_reveal") or not r.get("police_sheet_viewed") or r.get("police_timer_round")!=round_number: return
    police=next((p for p in r["players"].values() if p.get("character")=="Police"),None)
    thief=next((p for p in r["players"].values() if p.get("character")=="Thief"),None)
    if not police or not thief or police.get("guessed"): return

    thief["score"]+=500
    police["guessed"]=True
    record={"round":r.get("round_number",1),"police":police["username"],"guessed":None,
            "thief":thief["username"],"correct":False,"timed_out":True,
            "outcome":f"TIME OUT! Police did not lock within {r.get('police_timer_seconds',60)} seconds. {thief['username']} is the Thief and gets +500.",
            "points_added":{"username":thief["username"],"points":500},
            "round_role_points": r.get("current_round_role_points", []),
            "round_complete":True}
    r["last_reveal"]=build_reveal(r,record)
    record["players"]=r["last_reveal"]["players"]
    r["round_history"].append(record)
    r["police_deadline"]=None
    r["police_sheet_viewed"]=False
    r["police_timer_round"]=0
    emit("police_timeout",record,room=room)
    _maybe_schedule_system_king(room)

def _system_police_act(room, round_number, guess, delay):
    # System players behave like a normal player: they think for a while, then lock.
    socketio.sleep(delay)
    r=rooms.get(room)
    if not r or r.get("closed") or not r.get("started") or r.get("round_number")!=round_number:
        return
    police=next((p for p in r["players"].values() if p.get("character")=="Police"),None)
    if not police or not police.get("system") or police.get("guessed") or r.get("last_reveal"):
        return
    thief=next((p for p in r["players"].values() if p.get("character")=="Thief"),None)
    if not thief: return
    # A human-like system may be right or wrong; it never gets special information.
    target=thief["username"] if random.random()<0.65 else random.choice([p["username"] for p in r["players"].values() if p["username"]!=police["username"]])
    record=finish_guess(r,police,thief,target)
    r["police_deadline"]=None
    emit("guess_result",record,room=room)
    _maybe_schedule_system_king(room)

@socketio.on("police_guess")
def police_guess(data):
    room=data["room"]; r=rooms.get(room)
    if not r or not r["started"] or r.get("closed"): return
    police=next((p for p in r["players"].values() if p["character"]=="Police"),None)
    thief=next((p for p in r["players"].values() if p["character"]=="Thief"),None)
    guess=(data.get("username") or "").strip()
    if not police or not thief or not guess: return
    if session.get("username")!=police["username"]:
        emit("guess_error",{"message":"Only the Police player can lock the guess."},to=request.sid); return
    if police.get("guessed"):
        emit("guess_error",{"message":"The Police guess is already locked."},to=request.sid); return
    if not r.get("police_sheet_viewed"):
        emit("guess_error",{"message":"Open your Police sheet first. The 60-second clock starts when you view it."},to=request.sid); return
    if r.get("police_deadline") and time.time() >= r["police_deadline"]:
        emit("guess_error",{"message":f"⏰ {r.get('police_timer_seconds',60)} seconds are over. The Thief gets the +500 bonus."},to=request.sid); return
    if guess not in r["players"] or guess==police["username"]:
        emit("guess_error",{"message":"Please select a valid player."},to=request.sid); return
    record=finish_guess(r,police,thief,guess)
    emit("guess_result",record,room=room)
    _maybe_schedule_system_king(room)

def _maybe_schedule_system_king(room):
    r=rooms.get(room)
    if not r or r.get("closed") or not r.get("last_reveal"): return
    king=next((p for p in r["players"].values() if p.get("character")=="King"),None)
    if king and king.get("system") and not r.get("system_king_pending"):
        r["system_king_pending"]=True
        socketio.start_background_task(_system_king_next,room,r.get("round_number",1))

def _advance_round(room):
    r=rooms.get(room)
    if not r or not r.get("started") or r.get("closed"): return False
    r["round_number"]+=1
    assign_round(r)
    r["round_started_at"]=time.time()
    r["police_deadline"]=None
    r["police_sheet_viewed"]=False
    r["police_timer_round"]=0
    r["system_king_pending"]=False
    round_role_points = award_round_role_points(r)
    r["current_round_role_points"] = round_role_points
    emit("new_round",{"round":r["round_number"],"police_deadline":None,"role_points_added":round_role_points},room=room)

    police=next((p for p in r["players"].values() if p["character"]=="Police"),None)
    if police and police.get("system"):
        police["viewed"] = True
        r["police_sheet_viewed"] = True
        r["police_deadline"] = time.time()+r.get("police_timer_seconds",60)
        r["police_timer_round"] = r["round_number"]
        socketio.start_background_task(_round_timer,room,r["round_number"])
        socketio.emit("police_timer_started",{"round":r["round_number"],"police_deadline":r["police_deadline"]},room=room)
        candidates=[p["username"] for p in r["players"].values() if p["username"]!=police["username"]]
        if candidates:
            limit=max(1,r.get("police_timer_seconds",60))
            delay=random.uniform(1,max(1,limit-1)) if limit>1 else 0.2
            socketio.start_background_task(_system_police_act,room,r["round_number"],random.choice(candidates),delay)
    return True

def _system_king_next(room, completed_round):
    socketio.sleep(15)
    r=rooms.get(room)
    if not r or r.get("closed") or r.get("round_number")!=completed_round or not r.get("last_reveal"):
        if r: r["system_king_pending"]=False
        return
    _advance_round(room)

@socketio.on("next_round")
def next_round(data):
    room=data.get("room"); r=rooms.get(room)
    if not r or not r.get("started") or r.get("closed"): return
    king=next((p for p in r["players"].values() if p.get("character")=="King"),None)
    if not king or session.get("username")!=king.get("username"):
        emit("start_error",{"message":"Only the King can start the next round."},to=request.sid); return
    if not r.get("last_reveal"):
        emit("start_error",{"message":"Police must lock the guess or the 60 second timer must expire first."},to=request.sid); return
    _advance_round(room)

@socketio.on("close_game")
def close_game(data):
    room=data["room"]; r=rooms.get(room)
    if not r or not r["started"] or r.get("closed"): return
    king=next((p for p in r["players"].values() if p.get("character")=="King"),None)
    if not king or session.get("username")!=king.get("username"):
        emit("close_error",{"message":"Only the King can close the game."},to=request.sid); return
    if r.get("round_number",0)>0 and not r.get("last_reveal"):
        emit("close_error",{"message":"Complete the current round before closing the game."},to=request.sid); return
    r["closed"]=True
    r["police_deadline"]=None
    r["police_sheet_viewed"]=False
    r["police_timer_round"]=0
    rows=sorted(r["players"].values(),key=lambda x:x["score"],reverse=True)
    results=[]
    for i,p in enumerate(rows,1):
        results.append({"rank":i,"username":p["username"],"score":p["score"],
                        "grade":grade_for_score(p["score"]),"character":p["character"]})
    winner=rows[0] if rows else None
    credit_awarded=None
    if winner and not winner.get("system"):
        add_kint_credit(winner["username"],1); credit_awarded=winner["username"]
    for x in results:
        x["kint_credits"]=get_kint_credits(x["username"]) if not r["players"][x["username"]]["system"] else 0
    r["final_results"]=results
    emit("game_closed",{"results":results,"round_history":r.get("round_history",[]),
                         "message":f"🥇 {credit_awarded} earned 1 KINT credit for the next game." if credit_awarded else "Game closed."},
         room=room)

if __name__=="__main__":
    init_db()
    socketio.run(app,host="0.0.0.0",port=int(os.environ.get("PORT",5000)),debug=True)
