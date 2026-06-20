from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from DietaProg_functions import importa_db, calcola_dieta, calcola_calorie, verifica_pasto, deriva_macro_pasto
import os
import pandas as pd
from openpyxl import load_workbook
from werkzeug.security import generate_password_hash, check_password_hash

# Integrations
from models import db, User, Food, UserState
from authlib.integrations.flask_client import OAuth

app = Flask(__name__, template_folder=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates'))
app.config["TEMPLATES_AUTO_RELOAD"] = True  # always re-read templates from disk
# Secret key for session management
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")

# Database Configuration
if os.environ.get("VERCEL"):
    # Vercel Postgres usually exposes POSTGRES_URL
    db_url = os.environ.get("POSTGRES_URL")
    if db_url and db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    
    # Se per caso non è stato ancora configurato il DB su Vercel, mettiamo un fallback
    if not db_url:
        db_url = "sqlite:////tmp/local.db"
    app.config['SQLALCHEMY_DATABASE_URI'] = db_url
else:
    # Local SQLite fallback
    local_db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "local.db")
    app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{local_db_path}"

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

# Assicura che le tabelle vengano create se non esistono (fondamentale per Vercel serverless / db temporaneo)
with app.app_context():
    db.create_all()
    
    # Migrazione per aggiungere is_admin se il DB esisteva già
    from sqlalchemy import text
    try:
        db.session.execute(text("ALTER TABLE users ADD COLUMN is_admin BOOLEAN DEFAULT FALSE"))
        db.session.commit()
    except Exception:
        db.session.rollback() # la colonna esiste già
        
    # Assegna automaticamente i privilegi di admin a ghessi2003@gmail.com
    from models import User
    admin_user = User.query.filter_by(email="ghessi2003@gmail.com").first()
    if admin_user and not admin_user.is_admin:
        admin_user.is_admin = True
        db.session.commit()

# OAuth Configuration
oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=os.environ.get("GOOGLE_CLIENT_ID"),
    client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)

# Base directory for data files (supports env override)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ===== User Utilities (SQLAlchemy based) =====
def get_current_user():
    user_id = session.get("user_id")
    if user_id:
        return db.session.get(User, user_id)
    return None

from functools import wraps

def require_admin_decorator(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user = get_current_user()
        if not user or not user.is_admin:
            if request.is_json:
                return jsonify({"error": "Accesso negato"}), 403
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

def _is_valid_pin(pin: str) -> bool:
    return isinstance(pin, str) and len(pin) == 4 and pin.isdigit()


def load_dbs():
    from models import Food
    carbo, prot, grassi, frutta, verdura = {}, {}, {}, {}, {}
    try:
        foods = Food.query.all()
        for f in foods:
            nut_dict = {'calorie': f.calorie, 'carboidrati': f.carboidrati, 'proteine': f.proteine, 'grassi': f.grassi}
            if f.categoria == 'carboidrati': carbo[f.nome] = nut_dict
            elif f.categoria == 'proteine': prot[f.nome] = nut_dict
            elif f.categoria == 'grassi': grassi[f.nome] = nut_dict
            elif f.categoria == 'frutta': frutta[f.nome] = nut_dict
            elif f.categoria == 'verdura': verdura[f.nome] = nut_dict
    except Exception as e:
        print(f"[WARN] Error loading DBs: {e}")
    return carbo, prot, grassi, frutta, verdura

# ====== Simple username-only login and per-user state helpers ======
def _sanitize_username(u: str) -> str:
    import re
    import unicodedata
    u = (u or "").strip()
    # Normalize accents (é -> e), keep ASCII only
    u = unicodedata.normalize("NFKD", u).encode("ascii", "ignore").decode("ascii")
    # Replace whitespace with single hyphen
    u = re.sub(r"\s+", "-", u)
    # Keep only allowed chars
    u = re.sub(r"[^A-Za-z0-9._-]", "", u)
    # collapse multiple hyphens
    u = re.sub(r"-{2,}", "-", u)
    return u[:64]

@app.before_request
def require_login():
    # allow public endpoints
    public_paths = {"/login", "/healthz"}
    if request.path.startswith("/static/"):
        return None
    if request.path in public_paths:
        return None
    if request.method == "GET" and request.path == "/":
        # index requires login
        if not session.get("user_id"):
            return redirect(url_for("login", next=request.url))
        return None
    # APIs and compute endpoints can be used without login, but if username exists it's used
    return None

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        # Accept both form and JSON
        if request.is_json:
            body = request.get_json(silent=True) or {}
            raw_username = body.get("username")
            pin = body.get("pin") or body.get("password")  # accept either key
            action = (body.get("action") or "login").strip().lower()
        else:
            raw_username = request.form.get("username")
            pin = request.form.get("pin") or request.form.get("password")
            action = (request.form.get("action") or "login").strip().lower()

        username = _sanitize_username(raw_username)
        if not username:
            err = "Inserisci un nome utente valido"
            return render_template("login.html", error=err), 400
        if not _is_valid_pin(str(pin or "")):
            err = "PIN non valido: deve essere di 4 cifre numeriche"
            return render_template("login.html", error=err), 400

        user = User.query.filter_by(username=username).first()

        if action == "register":
            # enforce unique username
            if user:
                err = "Nome utente già utilizzato"
                return render_template("login.html", error=err), 400
            # create user with hashed PIN
            new_user = User(username=username, pin_hash=generate_password_hash(str(pin)))
            db.session.add(new_user)
            db.session.commit()
            session["user_id"] = new_user.id
            nxt = request.args.get("next") or url_for("index")
            return redirect(nxt)

        # default: login
        if not user or not check_password_hash(str(user.pin_hash or ""), str(pin)):
            err = "Credenziali non valide"
            return render_template("login.html", error=err), 401

        session["user_id"] = user.id
        nxt = request.args.get("next") or url_for("index")
        return redirect(nxt)

    # GET
    return render_template("login.html")

@app.route("/logout", methods=["POST", "GET"])
def logout():
    session.pop("user_id", None)
    return redirect(url_for("login"))

@app.route('/login/google')
def login_google():
    redirect_uri = url_for('authorize_google', _external=True)
    return oauth.google.authorize_redirect(redirect_uri)

@app.route('/login/google/authorize')
def authorize_google():
    try:
        token = oauth.google.authorize_access_token()
        user_info = token.get('userinfo')
        if not user_info:
            return render_template("login.html", error="Google non ha restituito le informazioni utente")
            
        email = user_info.get("email")
        if not email:
            return render_template("login.html", error="Nessuna email ricevuta da Google")
            
        user = User.query.filter_by(email=email).first()
        if not user:
            # check if username exists
            base_username = email.split('@')[0]
            username = base_username
            i = 1
            while User.query.filter_by(username=username).first():
                username = f"{base_username}{i}"
                i += 1
            user = User(username=username, email=email, auth_provider='google')
            db.session.add(user)
            db.session.commit()
            
        session["user_id"] = user.id
        return redirect(url_for('index'))
    except Exception as e:
        return render_template("login.html", error=f"Errore Google Login: {str(e)}")

@app.route("/api/user/state", methods=["GET", "POST"])
def user_state_api():
    user = get_current_user()
    if not user:
        return jsonify({"error": "not authenticated"}), 401
    if request.method == "GET":
        state_obj = UserState.query.filter_by(user_id=user.id).first()
        data = state_obj.state_json if state_obj else {}
        return jsonify({"username": user.username, "state": data})
    # POST save
    payload = request.get_json() or {}
    allowed_keys = {
        "calorie_tot", "perc_pasti", "macro_tot",
        "choices", "fruit_ratios", "veg_ratios",
        "locked_macros",
        "food_constraints",
        "splits_per_meal",
        "min_grams",
        "weekly", "selected_day"
    }
    cleaned = {k: payload.get(k) for k in allowed_keys if k in payload}
    state_obj = UserState.query.filter_by(user_id=user.id).first()
    if not state_obj:
        state_obj = UserState(user_id=user.id, state_json=cleaned)
        db.session.add(state_obj)
    else:
        state_obj.state_json = cleaned
    db.session.commit()
    return jsonify({"ok": True})

MEALS = ["colazione", "pranzo", "cena"]
WEEK_DAYS = ["lunedi", "martedi", "mercoledi", "giovedi", "venerdi", "sabato", "domenica"]

def build_foods_by_category():
    carbo_db, prot_db, grassi_db, frutta_db, verdura_db = load_dbs()
    def safe_sorted_keys(d):
        names = [str(k).strip() for k in d.keys() if str(k).strip()]
        return sorted(names, key=lambda s: s.lower())
    return {
        "carboidrati": safe_sorted_keys(carbo_db),
        "proteine": safe_sorted_keys(prot_db),
        "grassi": safe_sorted_keys(grassi_db),
        "frutta": safe_sorted_keys(frutta_db),
        "verdura": safe_sorted_keys(verdura_db),
    }

@app.route("/", methods=["GET", "POST"])
def index():
    if not session.get('user_id'):
        return redirect(url_for('login'))
    user = get_current_user()
    if not user:
        session.clear()
        return redirect(url_for('login'))
        
    carbo_db, prot_db, grassi_db, frutta_db, verdura_db = load_dbs()

    def safe_sorted_keys(d):
        return sorted(d.keys()) if isinstance(d, dict) else []

    foods_by_cat = build_foods_by_category()
    defaults = {
        "calorie_tot": 2000,
        "perc_pasti": {"colazione": 0.25, "pranzo": 0.40, "cena": 0.35},
        "macro_tot": {"carbo": 0.50, "prot": 0.30, "fat": 0.20},
        "macro_pasti": {
            "colazione": {"carbo": 0.45, "prot": 0.25, "fat": 0.30},
            "pranzo": {"carbo": 0.55, "prot": 0.30, "fat": 0.15},
            "cena": {"carbo": 0.45, "prot": 0.35, "fat": 0.20},
        },
        "min_grams": 0.0,
    }
    
    meal_choices = {}
    weekly_state = None
    selected_day = WEEK_DAYS[0]
    
    state_obj = UserState.query.filter_by(user_id=user.id).first()
    state = state_obj.state_json if state_obj else {}
    
    if isinstance(state.get("calorie_tot"), (int, float)):
        defaults["calorie_tot"] = state["calorie_tot"]
    if isinstance(state.get("perc_pasti"), dict):
        defaults["perc_pasti"].update(state["perc_pasti"])
    if isinstance(state.get("macro_tot"), dict):
        defaults["macro_tot"].update(state["macro_tot"])
    if isinstance(state.get("macro_pasti"), dict):
        for m, cfg in state["macro_pasti"].items():
            if m in defaults["macro_pasti"] and isinstance(cfg, dict):
                defaults["macro_pasti"][m].update(cfg)
    if isinstance(state.get("choices"), dict):
        meal_choices = state["choices"] or {}
    try:
        mg = float(state.get("min_grams"))
        if mg >= 0.0:
            defaults["min_grams"] = mg
    except Exception:
        pass
    if isinstance(state.get("weekly"), dict):
        weekly_state = {}
        for d in WEEK_DAYS:
            v = state["weekly"].get(d, {}) if isinstance(state["weekly"], dict) else {}
            weekly_state[d] = {
                "choices": (v.get("choices") if isinstance(v.get("choices"), dict) else {}),
                "fruit_ratios": (v.get("fruit_ratios") if isinstance(v.get("fruit_ratios"), dict) else {}),
                "veg_ratios": (v.get("veg_ratios") if isinstance(v.get("veg_ratios"), dict) else {}),
                "splits_per_meal": (v.get("splits_per_meal") if isinstance(v.get("splits_per_meal"), dict) else {}),
            }
        if isinstance(state.get("selected_day"), str) and state["selected_day"] in WEEK_DAYS:
            selected_day = state["selected_day"]
    else:
        weekly_state = None

    if weekly_state is None:
        weekly_state = {}
        base = {
            "choices": meal_choices or {},
            "fruit_ratios": {m: 0.0 for m in MEALS},
            "veg_ratios": {m: 0.0 for m in MEALS},
            "food_constraints": {m: {} for m in MEALS},
        }
        for d in WEEK_DAYS:
            weekly_state[d] = base

    return render_template(
        "index.html",
        foods_by_cat=foods_by_cat,
        defaults=defaults,
        MEALS=MEALS,
        WEEK_DAYS=WEEK_DAYS,
        weekly_state=weekly_state,
        selected_day=selected_day,
        username=user.username,
        is_admin=user.is_admin,
        meal_choices=meal_choices,
    )

@app.route("/compute_meal", methods=["POST"])
def compute_meal():
    if not session.get("user_id"): return jsonify({"error": "Non autorizzato"}), 401
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON payload"}), 400
        meal = data.get("meal", "colazione")
        calorie_tot = float(data.get("calorie_tot", 2000.0))
        perc_pasti = data.get("perc_pasti", {})
        macro_tot = data.get("macro_tot", {"carbo":0.5, "prot":0.3, "fat":0.2})
        locked_macros = data.get("locked_macros", {})
        
        derived_macros = deriva_macro_pasto(macro_tot, perc_pasti, locked_macros)
        macro_for_meal = derived_macros.get(meal, {"carbo":0.5, "prot":0.3, "fat":0.2})
        
        try:
            s = sum(float(v) for v in perc_pasti.values()) or 1.0
        except Exception:
            s = 1.0
        perc = (float(perc_pasti.get(meal, 0.0)) / s) if s else 0.0
        cal_pasto = calorie_tot * perc

        choices = data.get("choices", {})
        fruit_ratio = float(data.get("fruit_ratio", 0.0))
        veg_ratio = float(data.get("veg_ratio", 0.0))
        min_grams = float(data.get("min_grams", 10.0))
        food_constraints = data.get("food_constraints", {})
        meal_splits = data.get("splits", [])
        
        carbo_db, prot_db, grassi_db, frutta_db, verdura_db = load_dbs()

        scelti, sol, quant_frutta, quant_verdura = calcola_dieta(
            cal_pasto, macro_for_meal, choices,
            carbo_db=carbo_db, prot_db=prot_db, grassi_db=grassi_db,
            frutta_db=frutta_db, verdura_db=verdura_db,
            fruit_ratio=fruit_ratio, veg_ratio=veg_ratio,
            min_grams=min_grams,
            food_constraints=food_constraints,
            splits=meal_splits,
        )

        per_food, totals = calcola_calorie(scelti, sol, carbo_db, prot_db, grassi_db, frutta_db, verdura_db)
        per_food_serial = [
            {"nome": p.get("nome"), "grammi": float(p.get("grammi") or 0),
             "kcal": float(p.get("kcal") or 0), "carbo": float(p.get("carbo") or 0),
             "proteine": float(p.get("proteine") or 0), "grassi": float(p.get("grassi") or 0)}
            for p in per_food
        ]

        if quant_frutta is not None and choices.get("frutta"):
            for fr in choices.get("frutta", []):
                nut = frutta_db.get(fr)
                if not nut: continue
                grams = float(quant_frutta)
                kcal = float(nut.get("calorie", 0.0)) * grams / 100.0
                carbo = float(nut.get("carboidrati", 0.0)) * grams / 100.0
                proteine = float(nut.get("proteine", 0.0)) * grams / 100.0
                grassi = float(nut.get("grassi", 0.0)) * grams / 100.0
                per_food_serial.append({"nome": fr, "grammi": grams, "kcal": kcal, "carbo": carbo, "proteine": proteine, "grassi": grassi})
                totals["kcal"] += kcal; totals["carbo"] += carbo; totals["proteine"] += proteine; totals["grassi"] += grassi

        if quant_verdura is not None and choices.get("verdura"):
            for vd in choices.get("verdura", []):
                nut = verdura_db.get(vd)
                if not nut: continue
                grams = float(quant_verdura)
                kcal = float(nut.get("calorie", 0.0)) * grams / 100.0
                carbo = float(nut.get("carboidrati", 0.0)) * grams / 100.0
                proteine = float(nut.get("proteine", 0.0)) * grams / 100.0
                grassi = float(nut.get("grassi", 0.0)) * grams / 100.0
                per_food_serial.append({"nome": vd, "grammi": grams, "kcal": kcal, "carbo": carbo, "proteine": proteine, "grassi": grassi})
                totals["kcal"] += kcal; totals["carbo"] += carbo; totals["proteine"] += proteine; totals["grassi"] += grassi

        totals_serial = {k: float(totals.get(k, 0.0)) for k in ("kcal", "carbo", "proteine", "grassi")}
        requested_kcal = float(cal_pasto)
        kcal_delta     = totals_serial.get("kcal", 0.0) - requested_kcal
        carb_kcal = totals_serial.get("carbo", 0.0) * 4.0
        prot_kcal = totals_serial.get("proteine", 0.0) * 4.0
        fat_kcal  = totals_serial.get("grassi",  0.0) * 9.0
        macro_kcal_sum = carb_kcal + prot_kcal + fat_kcal
        base_kcal = macro_kcal_sum if macro_kcal_sum > 0 else totals_serial.get("kcal", 0.0)
        macro_breakdown = {
            "carbo":    {"kcal": carb_kcal, "percent": (carb_kcal / base_kcal) * 100.0 if base_kcal else 0.0},
            "proteine": {"kcal": prot_kcal, "percent": (prot_kcal / base_kcal) * 100.0 if base_kcal else 0.0},
            "grassi":   {"kcal": fat_kcal,  "percent": (fat_kcal  / base_kcal) * 100.0 if base_kcal else 0.0},
        }

        return jsonify({
            "per_food": per_food_serial,
            "totals": totals_serial,
            "quant_frutta":  float(quant_frutta)  if quant_frutta  is not None else None,
            "quant_verdura": float(quant_verdura) if quant_verdura is not None else None,
            "requested_kcal": requested_kcal,
            "kcal_delta":     float(kcal_delta),
            "macro_kcal":     macro_kcal_sum,
            "macro_breakdown": macro_breakdown,
            "derived_macros": derived_macros,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/compute_day", methods=["POST"])
def compute_day():
    if not session.get("user_id"): return jsonify({"error": "Non autorizzato"}), 401
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON payload"}), 400

        calorie_tot = float(data.get("calorie_tot", 2000.0))
        perc_pasti = data.get("perc_pasti", {"colazione":0.25,"pranzo":0.40,"cena":0.35})
        try:
            s = sum(float(v) for v in perc_pasti.values()) or 1.0
        except Exception:
            s = 1.0
        perc_pasti = {k: float(v) / s for k, v in perc_pasti.items()}

        macro_tot = data.get("macro_tot", {"carbo":0.5, "prot":0.3, "fat":0.2})
        locked_macros = data.get("locked_macros", {})

        choices_all = data.get("choices", {})
        fruit_ratios = data.get("fruit_ratios", {}) or {}
        veg_ratios = data.get("veg_ratios", {}) or {}
        min_grams = data.get("min_grams", 10.0)
        food_constraints_all = data.get("food_constraints", {}) or {}
        splits_per_meal_all = data.get("splits_per_meal", {}) or {}

        derived_macros = deriva_macro_pasto(macro_tot, perc_pasti, locked_macros)
        carbo_db, prot_db, grassi_db, frutta_db, verdura_db = load_dbs()

        results_by_meal = {}
        total_day  = {"kcal": 0.0, "carbo": 0.0, "proteine": 0.0, "grassi": 0.0}
        daily_per_food = []

        for m in MEALS:
            perc           = float(perc_pasti.get(m, 0.0) or 0.0)
            cal_pasto      = calorie_tot * perc
            macro_for_meal = derived_macros.get(m, {"carbo": 0.5, "prot": 0.3, "fat": 0.2})
            choices        = choices_all.get(m, {"carboidrati": [], "proteine": [], "grassi": [], "frutta": [], "verdura": []})
            fruit_ratio    = float(fruit_ratios.get(m, 0.0) or 0.0)
            veg_ratio      = float(veg_ratios.get(m,   0.0) or 0.0)
            food_constraints = food_constraints_all.get(m, {}) or {}
            meal_splits      = splits_per_meal_all.get(m, []) or []

            try:
                scelti, sol, quant_frutta, quant_verdura = calcola_dieta(
                    cal_pasto, macro_for_meal, choices,
                    carbo_db=carbo_db, prot_db=prot_db, grassi_db=grassi_db,
                    frutta_db=frutta_db, verdura_db=verdura_db,
                    fruit_ratio=fruit_ratio, veg_ratio=veg_ratio,
                    min_grams=min_grams,
                    food_constraints=food_constraints,
                    splits=meal_splits,
                )
            except Exception as e:
                print(f"[{m}] calcola_dieta error: {e}")
                return jsonify({"error": f"Errore nel pasto '{m.capitalize()}': compilalo selezionando gli alimenti prima di calcolare la giornata!"}), 400

            per_food, totals = calcola_calorie(scelti, sol, carbo_db, prot_db, grassi_db, frutta_db, verdura_db)
            per_food_serial = [
                {"nome": p.get("nome"), "grammi": float(p.get("grammi") or 0),
                 "kcal": float(p.get("kcal") or 0), "carbo": float(p.get("carbo") or 0),
                 "proteine": float(p.get("proteine") or 0), "grassi": float(p.get("grassi") or 0)}
                for p in per_food
            ]

            if quant_frutta is not None and choices.get("frutta"):
                for fr in choices.get("frutta", []):
                    nut = frutta_db.get(fr)
                    if not nut: continue
                    grams = float(quant_frutta)
                    kcal = float(nut.get("calorie", 0.0)) * grams / 100.0
                    carbo = float(nut.get("carboidrati", 0.0)) * grams / 100.0
                    proteine = float(nut.get("proteine", 0.0)) * grams / 100.0
                    grassi = float(nut.get("grassi", 0.0)) * grams / 100.0
                    per_food_serial.append({"nome": fr, "grammi": grams, "kcal": kcal, "carbo": carbo, "proteine": proteine, "grassi": grassi})
                    totals["kcal"] += kcal; totals["carbo"] += carbo; totals["proteine"] += proteine; totals["grassi"] += grassi

            if quant_verdura is not None and choices.get("verdura"):
                for vd in choices.get("verdura", []):
                    nut = verdura_db.get(vd)
                    if not nut: continue
                    grams = float(quant_verdura)
                    kcal = float(nut.get("calorie", 0.0)) * grams / 100.0
                    carbo = float(nut.get("carboidrati", 0.0)) * grams / 100.0
                    proteine = float(nut.get("proteine", 0.0)) * grams / 100.0
                    grassi = float(nut.get("grassi", 0.0)) * grams / 100.0
                    per_food_serial.append({"nome": vd, "grammi": grams, "kcal": kcal, "carbo": carbo, "proteine": proteine, "grassi": grassi})
                    totals["kcal"] += kcal; totals["carbo"] += carbo; totals["proteine"] += proteine; totals["grassi"] += grassi

            mk = {
                "carbo":    float(totals.get("carbo", 0.0)) * 4.0,
                "proteine": float(totals.get("proteine", 0.0)) * 4.0,
                "grassi":   float(totals.get("grassi", 0.0)) * 9.0,
            }
            sum_mk = sum(mk.values())
            macro_percent = {k: (mk[k] / sum_mk * 100.0 if sum_mk > 0 else 0.0) for k in mk}

            totals_s = {k: float(totals.get(k, 0.0)) for k in ("kcal", "carbo", "proteine", "grassi")}
            results_by_meal[m] = {
                "per_food":     per_food_serial,
                "totals":       totals_s,
                "macro_kcal":   mk,
                "macro_percent": macro_percent,
                "quant_frutta":  float(quant_frutta)  if quant_frutta  is not None else None,
                "quant_verdura": float(quant_verdura) if quant_verdura is not None else None,
                "requested_kcal": cal_pasto,
            }
            total_day["kcal"]      += totals_s["kcal"]
            total_day["carbo"]     += totals_s["carbo"]
            total_day["proteine"]  += totals_s["proteine"]
            total_day["grassi"]    += totals_s["grassi"]
            for p in per_food_serial:
                daily_per_food.append({"meal": m, **p})

        total_mk = {
            "carbo":    total_day["carbo"]    * 4.0,
            "proteine": total_day["proteine"] * 4.0,
            "grassi":   total_day["grassi"]  * 9.0,
        }
        sum_total_mk = sum(total_mk.values())
        daily_macro_percent = {k: (total_mk[k] / sum_total_mk * 100.0 if sum_total_mk > 0 else 0.0) for k in total_mk}

        return jsonify({
            "results":              results_by_meal,
            "total_day":            total_day,
            "daily_per_food":       daily_per_food,
            "daily_macro_percent":  daily_macro_percent,
            "derived_macros":       {m: {k: round(v, 4) for k, v in mc.items()} for m, mc in derived_macros.items()},
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/shopping_list", methods=["POST"])
def shopping_list():
    if not session.get("user_id"): return jsonify({"error": "Non autorizzato"}), 401
    try:
        data = request.get_json()
        if not data: return jsonify({"error": "No JSON payload"}), 400

        calorie_tot = float(data.get("calorie_tot", 2000.0))
        perc_pasti = data.get("perc_pasti", {})
        try:
            s = sum(float(v) for v in perc_pasti.values()) or 1.0
        except Exception:
            s = 1.0
        perc_pasti = {k: float(v) / s for k, v in perc_pasti.items()}

        macro_tot = data.get("macro_tot", {"carbo":0.5, "prot":0.3, "fat":0.2})
        weekly_state = data.get("weekly_state", {})
        min_grams = float(data.get("min_grams", 10.0))
        locked_macros = data.get("locked_macros", {})
        
        derived_macros = deriva_macro_pasto(macro_tot, perc_pasti, locked_macros)
        carbo_db, prot_db, grassi_db, frutta_db, verdura_db = load_dbs()

        shopping = { "carboidrati": {}, "proteine": {}, "grassi": {}, "frutta": {}, "verdura": {} }

        for d in WEEK_DAYS:
            day_state = weekly_state.get(d) or {}
            choices_all          = day_state.get("choices", {})
            fruit_ratios         = day_state.get("fruit_ratios", {}) or {}
            veg_ratios           = day_state.get("veg_ratios",   {}) or {}
            food_constraints_all = day_state.get("food_constraints", {}) or {}
            splits_per_meal_all  = day_state.get("splits_per_meal", {}) or {}

            for m in MEALS:
                perc           = float(perc_pasti.get(m, 0.0) or 0.0)
                cal_pasto      = calorie_tot * perc
                macro_for_meal = derived_macros.get(m, {"carbo": 0.5, "prot": 0.3, "fat": 0.2})
                choices        = choices_all.get(m, {"carboidrati": [], "proteine": [], "grassi": [], "frutta": [], "verdura": []})
                fruit_ratio    = float(fruit_ratios.get(m, 0.0) or 0.0)
                veg_ratio      = float(veg_ratios.get(m,   0.0) or 0.0)
                food_constraints = food_constraints_all.get(m, {}) or {}
                meal_splits      = splits_per_meal_all.get(m, []) or []

                try:
                    scelti, sol, quant_frutta, quant_verdura = calcola_dieta(
                        cal_pasto, macro_for_meal, choices,
                        carbo_db=carbo_db, prot_db=prot_db, grassi_db=grassi_db,
                        frutta_db=frutta_db, verdura_db=verdura_db,
                        fruit_ratio=fruit_ratio, veg_ratio=veg_ratio,
                        min_grams=min_grams,
                        food_constraints=food_constraints,
                        splits=meal_splits,
                    )
                except Exception:
                    continue

                per_food, _ = calcola_calorie(scelti, sol, carbo_db, prot_db, grassi_db, frutta_db, verdura_db)
                
                for item in per_food:
                    nome = item.get("nome")
                    g = float(item.get("grammi", 0))
                    if not nome or g <= 0: continue
                    
                    cat = None
                    for c_name, c_list in choices.items():
                        if nome in c_list:
                            cat = c_name
                            break
                    if cat in shopping:
                        shopping[cat][nome] = shopping[cat].get(nome, 0) + g
                        
                if quant_frutta is not None and choices.get("frutta"):
                    g = float(quant_frutta)
                    if g > 0:
                        for fr in choices.get("frutta", []):
                            shopping["frutta"][fr] = shopping["frutta"].get(fr, 0) + g
                            
                if quant_verdura is not None and choices.get("verdura"):
                    g = float(quant_verdura)
                    if g > 0:
                        for vd in choices.get("verdura", []):
                            shopping["verdura"][vd] = shopping["verdura"].get(vd, 0) + g

        result = {}
        for cat, items in shopping.items():
            sorted_items = [{"nome": k, "grammi": round(v, 1)} for k, v in items.items() if v > 0]
            sorted_items.sort(key=lambda x: x["nome"])
            result[cat] = sorted_items

        return jsonify({"shopping_list": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/get_food", methods=["GET"])
def get_food():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Non autorizzato"}), 401
    nome = request.args.get("nome")
    if not nome:
        return jsonify({"error": "Nome mancante"}), 400
    food = Food.query.filter_by(nome=nome).first()
    if not food:
        return jsonify({"error": "Alimento non trovato"}), 404
    return jsonify({
        "nome": food.nome,
        "categoria": food.categoria,
        "calorie": food.calorie,
        "carboidrati": food.carboidrati,
        "proteine": food.proteine,
        "grassi": food.grassi
    })

@app.route("/add_food", methods=["POST"])
@require_admin_decorator
def add_food():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Non autorizzato"}), 401
        
    payload = request.get_json()
    if not payload:
        return jsonify({"error": "Dati mancanti"}), 400
        
    nome = payload.get("nome")
    categoria = payload.get("categoria")
    
    if not nome or not categoria:
        return jsonify({"error": "Nome e categoria obbligatori"}), 400
        
    try:
        f = Food.query.filter_by(nome=nome).first()
        if f:
            f.categoria = categoria.lower()
            f.calorie = float(payload.get("calorie", 0))
            f.carboidrati = float(payload.get("carboidrati", 0))
            f.proteine = float(payload.get("proteine", 0))
            f.grassi = float(payload.get("grassi", 0))
        else:
            f = Food(
                nome=nome,
                categoria=categoria.lower(),
                calorie=float(payload.get("calorie", 0)),
                carboidrati=float(payload.get("carboidrati", 0)),
                proteine=float(payload.get("proteine", 0)),
                grassi=float(payload.get("grassi", 0))
            )
            db.session.add(f)
        db.session.commit()
        return jsonify({"success": True, "item": {"nome": nome, "categoria": categoria.lower()}})
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500
@app.route("/api/seed", methods=["GET"])
def api_seed():
    import pandas as pd
    excel_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'alimenti.xlsx')
    if not os.path.exists(excel_path):
        return jsonify({"error": "File Excel non trovato"}), 404

    try:
        db.session.query(Food).delete()
        excel = pd.ExcelFile(excel_path)
        for sheet in excel.sheet_names:
            df = pd.read_excel(excel_path, sheet_name=sheet)
            df.columns = [str(c).lower().strip() for c in df.columns]
            for _, row in df.iterrows():
                try:
                    nome = str(row['nome']).strip() if 'nome' in row else ''
                    if pd.isna(row.get('nome')) or not nome: continue
                    food = Food(
                        nome=nome,
                        categoria=sheet.lower(),
                        calorie=float(row.get('calorie', 0.0)) if not pd.isna(row.get('calorie', 0.0)) else 0.0,
                        carboidrati=float(row.get('carboidrati', 0.0)) if not pd.isna(row.get('carboidrati', 0.0)) else 0.0,
                        proteine=float(row.get('proteine', 0.0)) if not pd.isna(row.get('proteine', 0.0)) else 0.0,
                        grassi=float(row.get('grassi', 0.0)) if not pd.isna(row.get('grassi', 0.0)) else 0.0
                    )
                    db.session.add(food)
                except Exception as ex:
                    print(f"Skipping {row.get('nome')}: {ex}")
        db.session.commit()
        return jsonify({"success": "Database alimenti popolato correttamente!"})
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route("/database_alimenti")
def database_alimenti():
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))
    foods = Food.query.order_by(Food.nome).all()
    return render_template("database_alimenti.html", foods=foods, is_admin=user.is_admin)

@app.route("/admin")
@require_admin_decorator
def admin_dashboard():
    users = User.query.all()
    foods = Food.query.all()
    return render_template("admin.html", users=users, foods=foods)

@app.route("/admin/delete_user/<int:user_id>", methods=["POST"])
@require_admin_decorator
def admin_delete_user(user_id):
    u = User.query.get(user_id)
    if not u: return jsonify({"error": "Utente non trovato"}), 404
    db.session.delete(u)
    db.session.commit()
    return jsonify({"success": True})

@app.route("/admin/toggle_admin/<int:user_id>", methods=["POST"])
@require_admin_decorator
def admin_toggle_admin(user_id):
    u = User.query.get(user_id)
    if not u: return jsonify({"error": "Utente non trovato"}), 404
    if u.id == session.get("user_id"):
        return jsonify({"error": "Non puoi rimuovere i tuoi stessi privilegi"}), 400
    u.is_admin = not u.is_admin
    db.session.commit()
    return jsonify({"success": True, "is_admin": u.is_admin})

@app.route("/admin/delete_food/<int:food_id>", methods=["POST"])
@require_admin_decorator
def admin_delete_food(food_id):
    f = Food.query.get(food_id)
    if not f: return jsonify({"error": "Alimento non trovato"}), 404
    db.session.delete(f)
    db.session.commit()
    return jsonify({"success": True})

@app.route("/admin/edit_food/<int:food_id>", methods=["POST"])
@require_admin_decorator
def admin_edit_food(food_id):
    f = Food.query.get(food_id)
    if not f: return jsonify({"error": "Alimento non trovato"}), 404
    payload = request.get_json()
    if not payload: return jsonify({"error": "Dati mancanti"}), 400
    
    f.nome = payload.get("nome", f.nome)
    f.categoria = payload.get("categoria", f.categoria).lower()
    f.calorie = float(payload.get("calorie", f.calorie))
    f.carboidrati = float(payload.get("carboidrati", f.carboidrati))
    f.proteine = float(payload.get("proteine", f.proteine))
    f.grassi = float(payload.get("grassi", f.grassi))
    
    db.session.commit()
    return jsonify({"success": True})

if __name__ == "__main__":
    debug = (os.environ.get("FLASK_DEBUG", "0") == "1")
    host = os.environ.get("HOST", "0.0.0.0")
    try:
        port = int(os.environ.get("PORT", "5000"))
    except Exception:
        port = 5000
    app.run(debug=debug, host=host, port=port)