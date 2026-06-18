from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from DietaProg_functions import importa_db, calcola_dieta, calcola_calorie, verifica_pasto, deriva_macro_pasto
import os
import pandas as pd
from openpyxl import load_workbook
from werkzeug.security import generate_password_hash, check_password_hash


app = Flask(__name__, template_folder=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates'))
app.config["TEMPLATES_AUTO_RELOAD"] = True  # always re-read templates from disk
# Secret key for session management (override in env for production)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")

# Base directory for data files (supports env override)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DIETAPRO_DATA_DIR", BASE_DIR)  # keep for Excel and static data

# NEW: dedicated persistent state directory (outside repo by default)
if os.environ.get("VERCEL"):
    STATE_DIR = "/tmp/.dieta_pro"
else:
    STATE_DIR = os.environ.get("DIETAPRO_STATE_DIR", os.path.join(os.path.expanduser("~"), ".dieta_pro"))
os.makedirs(STATE_DIR, exist_ok=True)
# Directory for per-user persisted state (under STATE_DIR)
USER_DATA_DIR = os.path.join(STATE_DIR, "user_data")
os.makedirs(USER_DATA_DIR, exist_ok=True)

# ===== Users registry (account + 4-digit PIN) =====
USERS_DB_FILE = os.path.join(STATE_DIR, "users.json")  # moved under STATE_DIR

def _atomic_write_json(path: str, payload: dict):
    import json, tempfile
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)

def _load_users() -> dict:
    try:
        import json
        if os.path.isfile(USERS_DB_FILE):
            with open(USERS_DB_FILE, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
                users = data.get("users") or {}
                return {str(k): v for k, v in users.items()}
    except Exception as e:
        print(f"[WARN] _load_users failed: {e}")
    return {}

def _save_users(users: dict) -> None:
    try:
        _atomic_write_json(USERS_DB_FILE, {"users": users or {}})
    except Exception as e:
        print(f"[WARN] _save_users failed: {e}")

def _is_valid_pin(pin: str) -> bool:
    return isinstance(pin, str) and len(pin) == 4 and pin.isdigit()

"""Percorso del file Excel degli alimenti
Ordine di risoluzione:
- Se esiste DIETAPRO_DATA_DIR, cerca lì.
- Altrimenti, cerca nella cartella del file app.py.
Preferisce .xlsm, poi .xlsx. Ritorna percorso assoluto.
"""
def _excel_file_path():
    # supporta .xlsm (macro) o .xlsx, preferendo .xlsm se presente
    candidates = []
    for name in ("alimenti.xlsm", "alimenti.xlsx"):
        p = os.path.join(DATA_DIR, name)
        if os.path.isfile(p):
            return p
    # fallback: primo .xlsm/.xlsx presente nella DATA_DIR
    try:
        files = [f for f in os.listdir(DATA_DIR) if f.lower().endswith((".xlsm", ".xlsx"))]
    except Exception:
        files = []
    files.sort(key=lambda x: (0 if x.lower().endswith(".xlsm") else 1, x.lower()))
    if files:
        print(f"Using Excel file: {files[0]}")
        return os.path.join(DATA_DIR, files[0])
    # di default ritorna percorso atteso di alimenti.xlsx nella DATA_DIR
    return os.path.join(DATA_DIR, "alimenti.xlsx")

def load_dbs():
    excel_file = _excel_file_path()
    if not os.path.isfile(excel_file):
        # In produzione non fallire all'import: restituisci DB vuoti e mostra warning
        print(f"[WARN] File Excel non trovato: {excel_file}. L'app partirà con database vuoti.")
        return ({}, {}, {}, {}, {})
    xls = pd.read_excel(excel_file, sheet_name=['carboidrati','proteine','grassi','frutta','verdura'])

    def df_to_db(df):
        # Sanitize: ensure 'nome' is a non-empty string and numeric fields are numeric
        if df is None:
            return {}
        if 'nome' not in df.columns:
            return {}
        df = df.copy()
        # normalize 'nome'
        df['nome'] = df['nome'].astype(str).str.strip()
        df = df[df['nome'].astype(bool)]
        # coerce nutrient columns
        for col in ['calorie', 'carboidrati', 'proteine', 'grassi']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0.0)
            else:
                df[col] = 0.0
        # drop duplicates keeping last
        df = df.drop_duplicates(subset=['nome'], keep='last')
        # build dict {nome: {calorie, carboidrati, proteine, grassi}}
        return df.set_index('nome')[['calorie','carboidrati','proteine','grassi']].to_dict(orient='index')

    return (
        df_to_db(xls.get('carboidrati')),
        df_to_db(xls.get('proteine')),
        df_to_db(xls.get('grassi')),
        df_to_db(xls.get('frutta')),
        df_to_db(xls.get('verdura'))
    )


# esegui load
carbo_db, prot_db, grassi_db, frutta_db, verdura_db = load_dbs()

# stampa diagnostica all'avvio per capire se i DB sono stati caricati correttamente
def _count_db(db):
    try:
        return len(db)
    except Exception:
        return 0

print('DB load summary:')
print(f"  carboidrati: {_count_db(carbo_db)} items")
print(f"  proteine:    {_count_db(prot_db)} items")
print(f"  grassi:      {_count_db(grassi_db)} items")
print(f"  frutta:      {_count_db(frutta_db)} items")
print(f"  verdura:     {_count_db(verdura_db)} items")

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

def _user_state_path(username: str) -> str:
    safe = _sanitize_username(username)
    return os.path.join(USER_DATA_DIR, f"{safe}.json")

def load_user_state(username: str) -> dict:
    try:
        p = _user_state_path(username)
        if os.path.isfile(p):
            import json
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f) or {}
    except Exception as e:
        print(f"[WARN] load_user_state failed for {username}: {e}")
    return {}

def save_user_state(username: str, data: dict) -> None:
    try:
        p = _user_state_path(username)
        _atomic_write_json(p, data or {})
    except Exception as e:
        print(f"[WARN] save_user_state failed for {username}: {e}")

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
        if not session.get("username"):
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

        users = _load_users()

        if action == "register":
            # enforce unique username
            if username in users:
                err = "Nome utente già utilizzato"
                return render_template("login.html", error=err), 400
            # create user with hashed PIN
            users[username] = {
                "password_hash": generate_password_hash(str(pin)),
                "created_at": __import__("datetime").datetime.utcnow().isoformat() + "Z"
            }
            _save_users(users)
            session["username"] = username
            nxt = request.args.get("next") or url_for("index")
            return redirect(nxt)

        # default: login
        data = users.get(username)
        if not data or not check_password_hash(str(data.get("password_hash") or ""), str(pin)):
            err = "Credenziali non valide"
            return render_template("login.html", error=err), 401

        session["username"] = username
        nxt = request.args.get("next") or url_for("index")
        return redirect(nxt)

    # GET
    return render_template("login.html")

@app.route("/logout", methods=["POST", "GET"])
def logout():
    session.pop("username", None)
    return redirect(url_for("login"))

@app.route("/api/user/state", methods=["GET", "POST"])
def user_state_api():
    username = session.get("username")
    if not username:
        return jsonify({"error": "not authenticated"}), 401
    if request.method == "GET":
        data = load_user_state(username)
        return jsonify({"username": username, "state": data})
    # POST save
    payload = request.get_json() or {}
    # keep only known keys to avoid bloat
    allowed_keys = {
        "calorie_tot", "perc_pasti", "macro_tot",
        "choices", "fruit_ratios", "veg_ratios",
        "locked_macros",          # pasti con macro bloccate dall'utente
        "food_constraints",       # per-meal per-food {min, max} grammi
        "splits_per_meal",        # per-meal slider splits [{food_a,food_b,alpha}]
        "min_grams",
        "weekly", "selected_day"
    }
    cleaned = {k: payload.get(k) for k in allowed_keys if k in payload}
    save_user_state(username, cleaned)
    return jsonify({"ok": True})

# endpoint per aggiungere un alimento e salvarlo direttamente nel file excel
@app.route("/add_food", methods=["POST"])
def add_food():
    data = request.get_json() or {}
    nome = (data.get('nome') or '').strip()
    categoria = (data.get('categoria') or '').strip().lower()
    try:
        calorie = float(data.get('calorie'))
        carbo = float(data.get('carboidrati'))
        prot = float(data.get('proteine'))
        grassi = float(data.get('grassi'))
    except Exception:
        return jsonify({"error":"Valori nutrizionali non validi"}), 400

    if not nome or categoria not in {"carboidrati","proteine","grassi","frutta","verdura"}:
        return jsonify({"error":"Dati mancanti o categoria non valida"}), 400

    # scegli il db in memoria da aggiornare
    db_map = {
        'carboidrati': carbo_db,
        'proteine': prot_db,
        'grassi': grassi_db,
        'frutta': frutta_db,
        'verdura': verdura_db,
    }
    target_db = db_map[categoria]

    excel_file = _excel_file_path()
    if not os.path.isfile(excel_file):
        return jsonify({"error": f"File excel '{excel_file}' non trovato"}), 500

    # scrivi/aggiorna nel workbook mantenendo macro se .xlsm
    keep_vba = excel_file.lower().endswith('.xlsm')
    wb = load_workbook(excel_file, keep_vba=keep_vba)
    if categoria not in wb.sheetnames:
        return jsonify({"error": f"Foglio '{categoria}' non trovato"}), 500
    ws = wb[categoria]

    # trova indici delle colonne (assume header alla riga 1)
    headers = {str(c.value).strip().lower(): idx for idx, c in enumerate(ws[1], start=1) if c.value is not None}
    required = ['nome','calorie','carboidrati','proteine','grassi']
    if not all(k in headers for k in required):
        return jsonify({"error":"Intestazioni del foglio non valide"}), 500

    # cerca se esiste già una riga con lo stesso nome (case-insensitive) nella colonna 'nome'
    nome_col = headers['nome']
    existing_row_idx = None
    for row in range(2, ws.max_row+1):
        cell_val = ws.cell(row=row, column=nome_col).value
        if cell_val and str(cell_val).strip().lower() == nome.lower():
            existing_row_idx = row
            break

    def _write_row(row_idx):
        ws.cell(row=row_idx, column=headers['nome'], value=nome)
        ws.cell(row=row_idx, column=headers['calorie'], value=calorie)
        ws.cell(row=row_idx, column=headers['carboidrati'], value=carbo)
        ws.cell(row=row_idx, column=headers['proteine'], value=prot)
        ws.cell(row=row_idx, column=headers['grassi'], value=grassi)

    if existing_row_idx is not None:
        _write_row(existing_row_idx)
    else:
        # append in fondo
        new_row_idx = ws.max_row + 1
        _write_row(new_row_idx)

    wb.save(excel_file)

    # aggiorna il DB in memoria
    target_db[nome] = {
        'calorie': calorie,
        'carboidrati': carbo,
        'proteine': prot,
        'grassi': grassi
    }

    return jsonify({
        'ok': True,
        'item': {
            'nome': nome,
            'categoria': categoria,
            'calorie': calorie,
            'carboidrati': carbo,
            'proteine': prot,
            'grassi': grassi
        }
    })

@app.route("/get_food", methods=["GET"])
def get_food():
    categoria = (request.args.get("categoria") or "").strip().lower()
    nome_req = (request.args.get("nome") or "").strip()
    if not categoria or not nome_req:
        return jsonify({"error": "Parametri mancanti"}), 400

    db_map = {
        'carboidrati': carbo_db,
        'proteine': prot_db,
        'grassi': grassi_db,
        'frutta': frutta_db,
        'verdura': verdura_db,
    }
    db = db_map.get(categoria)
    if db is None:
        return jsonify({"error": "Categoria non valida"}), 400

    # lookup case-insensitive
    found_key = None
    found_val = None
    nome_req_l = nome_req.lower()
    for k, v in db.items():
        if str(k).strip().lower() == nome_req_l:
            found_key = k
            found_val = v
            break
    if not found_val:
        return jsonify({"error": "Alimento non trovato"}), 404

    def _f(x): 
        try: 
            return float(x)
        except Exception: 
            return 0.0

    return jsonify({
        "nome": str(found_key),
        "categoria": categoria,
        "calorie": _f(found_val.get("calorie", 0.0)),
        "carboidrati": _f(found_val.get("carboidrati", 0.0)),
        "proteine": _f(found_val.get("proteine", 0.0)),
        "grassi": _f(found_val.get("grassi", 0.0)),
    })

MEALS = ["colazione", "pranzo", "cena"]
WEEK_DAYS = ["lunedi", "martedi", "mercoledi", "giovedi", "venerdi", "sabato", "domenica"]


def build_foods_by_category():
    # Safe sort keys as lowercase strings, skip empties
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

@app.route("/", methods=["GET"])
def index():
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
    username = session.get("username")
    meal_choices = {}
    splits_per_meal = {}
    weekly_state = None  # NEW
    selected_day = WEEK_DAYS[0]  # NEW default
    if username:
        state = load_user_state(username)
        # merge defaults with user state
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
        # prefill meal choices and splits
        if isinstance(state.get("choices"), dict):
            meal_choices = state["choices"] or {}
        # splits_per_meal removed (replaced by food_constraints)
        # min_grams
        try:
            mg = float(state.get("min_grams"))
            if mg >= 0.0:
                defaults["min_grams"] = mg
        except Exception:
            pass
        # NEW: weekly state
        if isinstance(state.get("weekly"), dict):
            # ensure keys exist for all days
            weekly_state = {}
            for d in WEEK_DAYS:
                v = state["weekly"].get(d, {}) if isinstance(state["weekly"], dict) else {}
                weekly_state[d] = {
                    "choices": (v.get("choices") if isinstance(v.get("choices"), dict) else {}),
                    "fruit_ratios": (v.get("fruit_ratios") if isinstance(v.get("fruit_ratios"), dict) else {}),
                    "veg_ratios": (v.get("veg_ratios") if isinstance(v.get("veg_ratios"), dict) else {}),
                    "splits_per_meal": (v.get("splits_per_meal") if isinstance(v.get("splits_per_meal"), dict) else {}),
                }
            # optional: selected day
            if isinstance(state.get("selected_day"), str) and state["selected_day"] in WEEK_DAYS:
                selected_day = state["selected_day"]
        else:
            weekly_state = None

    # weekly state: fallback per stati salvati senza weekly (vecchio formato)
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
        username=username,
        meal_choices=meal_choices,
    )

@app.route("/compute_meal", methods=["POST"])
def compute_meal():
    data = request.get_json() or {}
    meal = data.get("meal")
    if not meal:
        return jsonify({"error": "meal missing"}), 400

    calorie_tot = float(data.get("calorie_tot", 2000))
    perc_pasti  = data.get("perc_pasti", {})
    perc        = float(perc_pasti.get(meal, 0.0) or 0.0)
    cal_pasto   = calorie_tot * perc

    # Deriva i macro per pasto dal target giornaliero, rispettando eventuali pasti bloccati
    macro_tot     = data.get("macro_tot") or {"carbo": 0.5, "prot": 0.3, "fat": 0.2}
    locked_macros = data.get("locked_macros") or {}
    derived       = deriva_macro_pasto(macro_tot, perc_pasti, locked_macros)
    macro_for_meal = derived.get(meal, {"carbo": 0.5, "prot": 0.3, "fat": 0.2})

    choices = data.get("choices", {"carboidrati": [], "proteine": [], "grassi": [], "frutta": [], "verdura": []})
    fruit_ratio = float(data.get("fruit_ratio", 0.0))
    veg_ratio   = float(data.get("veg_ratio",   0.0))
    try:
        min_grams = float(data.get("min_grams", 0.0) or 0.0)
    except Exception:
        min_grams = 0.0
    food_constraints = data.get("food_constraints") or {}
    splits           = data.get("splits") or []

    scelti, sol, quant_frutta, quant_verdura = calcola_dieta(
        cal_pasto, macro_for_meal, choices,
        carbo_db=carbo_db, prot_db=prot_db, grassi_db=grassi_db,
        frutta_db=frutta_db, verdura_db=verdura_db,
        fruit_ratio=fruit_ratio, veg_ratio=veg_ratio,
        min_grams=min_grams,
        food_constraints=food_constraints,
        splits=splits,
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
        "meal": meal,
        "per_food": per_food_serial,
        "totals": totals_serial,
        "quant_frutta":  float(quant_frutta)  if quant_frutta  is not None else None,
        "quant_verdura": float(quant_verdura) if quant_verdura is not None else None,
        "requested_kcal": requested_kcal,
        "kcal_delta":     float(kcal_delta),
        "macro_kcal":     macro_kcal_sum,
        "macro_breakdown": macro_breakdown,
        "derived_macros": {m: {k: round(v, 4) for k, v in mc.items()} for m, mc in derived.items()},
    })

@app.route("/compute_day", methods=["POST"])
def compute_day():
    data = request.get_json() or {}
    calorie_tot = float(data.get("calorie_tot", 2000))
    perc_pasti  = data.get("perc_pasti", {})
    # normalizza le percentuali pasto
    try:
        s = sum(float(v) for v in perc_pasti.values()) or 1.0
    except Exception:
        s = 1.0
    perc_pasti = {k: float(v) / s for k, v in perc_pasti.items()}

    macro_tot     = data.get("macro_tot") or {"carbo": 0.5, "prot": 0.3, "fat": 0.2}
    locked_macros = data.get("locked_macros") or {}
    derived_macros = deriva_macro_pasto(macro_tot, perc_pasti, locked_macros)

    choices_all          = data.get("choices", {})
    fruit_ratios         = data.get("fruit_ratios", {}) or {}
    veg_ratios           = data.get("veg_ratios",   {}) or {}
    food_constraints_all = data.get("food_constraints", {}) or {}
    splits_per_meal_all  = data.get("splits_per_meal", {}) or {}
    try:
        min_grams = float(data.get("min_grams", 0.0) or 0.0)
    except Exception:
        min_grams = 0.0

    results    = {}
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
        per_food_serial  = [
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
        results[m] = {
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
        "results":              results,
        "total_day":            total_day,
        "daily_per_food":       daily_per_food,
        "daily_macro_percent":  daily_macro_percent,
        "derived_macros":       {m: {k: round(v, 4) for k, v in mc.items()} for m, mc in derived_macros.items()},
    })

@app.get("/healthz")
def healthz():
    try:
        # minimal check: DBs loaded and template exists
        _ = build_foods_by_category()
        # verify state dir is writable
        test_path = os.path.join(STATE_DIR, ".writetest")
        with open(test_path, "w", encoding="utf-8") as f:
            f.write("ok")
            f.flush()
            os.fsync(f.fileno())
        os.remove(test_path)
        return {"ok": True, "state_dir": STATE_DIR}, 200
    except Exception as e:
        return {"ok": False, "error": str(e)}, 500

@app.route("/shopping_list", methods=["POST"])
def shopping_list():
    data = request.get_json() or {}
    calorie_tot = float(data.get("calorie_tot", 2000))
    perc_pasti  = data.get("perc_pasti", {})
    try:
        s = sum(float(v) for v in perc_pasti.values()) or 1.0
    except Exception:
        s = 1.0
    perc_pasti = {k: float(v) / s for k, v in perc_pasti.items()}

    macro_tot     = data.get("macro_tot") or {"carbo": 0.5, "prot": 0.3, "fat": 0.2}
    locked_macros = data.get("locked_macros") or {}
    derived_macros = deriva_macro_pasto(macro_tot, perc_pasti, locked_macros)

    try:
        min_grams = float(data.get("min_grams", 0.0) or 0.0)
    except Exception:
        min_grams = 0.0

    weekly_state = data.get("weekly_state") or {}
    
    # Aggregato lista spesa: { 'carboidrati': {'Pane': 150.0}, 'proteine': {...} }
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
            except ValueError as ve:
                print(f"[{d} {m}] Skipped ValueError: {ve}")
                continue
            except Exception as e:
                import traceback
                print(f"[{d} {m}] calcola_dieta error: {e}")
                traceback.print_exc()
                continue

            per_food, _ = calcola_calorie(scelti, sol, carbo_db, prot_db, grassi_db, frutta_db, verdura_db)
            
            print(f"[{d} {m}] scelti: {scelti}, sol: {sol}, per_food: {per_food}")

            # Aggiungi grammi
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

    # Convert mapping to a sorted list per category
    result = {}
    for cat, items in shopping.items():
        sorted_items = [{"nome": k, "grammi": round(v, 1)} for k, v in items.items() if v > 0]
        sorted_items.sort(key=lambda x: x["nome"])
        result[cat] = sorted_items

    return jsonify({"shopping_list": result})
if __name__ == "__main__":
    # Avvio locale/standalone: usa env o default
    debug = (os.environ.get("FLASK_DEBUG", "0") == "1")
    host = os.environ.get("HOST", "0.0.0.0")
    try:
        port = int(os.environ.get("PORT", "5000"))
    except Exception:
        port = 5000
    app.run(debug=debug, host=host, port=port)