from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from DietaProg_functions import importa_db, calcola_dieta, bilancia_conservando_macros, calcola_calorie, verifica_pasto
import os
import pandas as pd
from openpyxl import load_workbook
from werkzeug.security import generate_password_hash, check_password_hash


app = Flask(__name__, template_folder=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates'))
# Secret key for session management (override in env for production)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")

# Base directory for data files (supports env override)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DIETAPRO_DATA_DIR", BASE_DIR)  # keep for Excel and static data

# NEW: dedicated persistent state directory (outside repo by default)
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
        "calorie_tot","perc_pasti","macro_tot","macro_pasti",
        "choices","fruit_ratios","veg_ratios","splits_per_meal","min_grams",
        "weekly","selected_day"  # NEW
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
WEEK_DAYS = ["lunedi", "martedi", "mercoledi", "giovedi", "venerdi", "sabato", "domenica"]  # NEW

def parse_splits(text):
    if not text:
        return None
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if "=" in line:
            pair, val = line.split("=", 1)
            parts_pair = [p.strip() for p in pair.split(",") if p.strip()]
            if len(parts_pair) < 2 or len(parts_pair) > 3:
                continue
            val = val.strip()
            if "," in val:
                try:
                    parts = [float(x) for x in val.split(",")]
                except Exception:
                    continue
                # allow same length or a single value
                out[tuple(parts_pair)] = tuple(parts)
            else:
                try:
                    out[tuple(parts_pair)] = float(val)
                except Exception:
                    continue
    return out or None

# NEW: helpers for split handling
def _has_same_category_split(local_splits, category: str) -> bool:
    if not local_splits:
        return False
    wanted = {"carboidrati","proteine","grassi"}
    for names in local_splits.keys():
        cats = set()
        for n in names:
            c, _ = _find_cat_and_nut(n)
            if c in wanted:
                cats.add(c)
        if cats == {category} and len(names) >= 2:
            return True
    return False

def _enforce_split_presence(per_food_serial, local_splits, floor: float = 1.0):
    """
    Ensure each item referenced in a same-category split has at least 'floor' grams.
    Grams are redistributed within the same category (taking from largest donors).
    Returns (new_per_food_serial, new_totals) or None if no changes.
    """
    if not local_splits or floor <= 0:
        return None
    # Map current items
    items = {}  # name -> {grams, cat}
    order = []  # preserve order for stable output
    for it in per_food_serial:
        nm = str(it.get("nome"))
        if nm not in items:
            order.append(nm)
        cat, _ = _find_cat_and_nut(nm)
        items[nm] = {"grams": float(it.get("grammi") or 0.0), "cat": cat}

    changed = False
    main_cats = {"carboidrati","proteine","grassi"}
    for names, _ratio in local_splits.items():
        # Determine category set
        cats = set()
        for n in names:
            c, _ = _find_cat_and_nut(n)
            if c in main_cats:
                cats.add(c)
        if len(cats) != 1:
            continue  # only enforce on pure same-category splits
        cat = cats.pop()
        # Ensure all referenced names exist in items map
        for n in names:
            if n not in items:
                c, _ = _find_cat_and_nut(n)
                if c == cat:
                    items[n] = {"grams": 0.0, "cat": c}
                    order.append(n)
        # Compute needed increases
        needs = []
        for n in names:
            rec = items.get(n)
            if not rec or rec["cat"] != cat:
                continue
            g = rec["grams"]
            if g + 1e-9 < floor:
                needs.append((n, floor - g))
        if not needs:
            continue
        # Build donors in same category (prefer those not in the split, then the largest)
        donors = [(n, rec["grams"]) for n, rec in items.items() if rec["cat"] == cat and n not in names]
        if not donors:
            donors = [(n, items[n]["grams"]) for n in names]  # fallback: take from within the split
        donors.sort(key=lambda t: t[1], reverse=True)
        # Redistribute
        for n, delta in needs:
            remaining = delta
            for i, (dn, dg) in enumerate(donors):
                if remaining <= 0:
                    break
                take = min(dg, remaining)
                if take > 0:
                    items[dn]["grams"] = max(0.0, items[dn]["grams"] - take)
                    dg -= take
                    donors[i] = (dn, dg)
                    items[n]["grams"] += take
                    remaining -= take
            # If still remaining, just set it (may increase total slightly; mixed-macro correction will handle)
            if remaining > 1e-9:
                items[n]["grams"] += remaining
                changed = True

    if not changed:
        return None

    # Build new per_food list (preserve existing order, then any new referenced items)
    new_list = []
    seen = set()
    # Keep only main-category items and any others already present
    for nm in order:
        rec = items.get(nm)
        if not rec:
            continue
        seen.add(nm)
        new_list.append((nm, rec["grams"]))
    # Recompute totals using DB nutrients
    new_per_food, new_totals = _recompute_items_and_totals(new_list)
    return new_per_food, new_totals

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
        if isinstance(state.get("splits_per_meal"), dict):
            spm = {}
            for m, val in state["splits_per_meal"].items():
                if isinstance(val, list):
                    spm[m] = val
                elif isinstance(val, str):
                    spm[m] = val.splitlines()
            splits_per_meal = spm
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

    # if no weekly state, initialize same settings for all days from current single-day inputs
    if weekly_state is None:
        weekly_state = {}
        # build a simple copy from current prefilled values
        base = {
            "choices": meal_choices or {},
            "fruit_ratios": {m: 0.0 for m in MEALS},
            "veg_ratios": {m: 0.0 for m in MEALS},
            "splits_per_meal": {m: (splits_per_meal.get(m) or []) for m in MEALS},
        }
        for d in WEEK_DAYS:
            weekly_state[d] = base

    return render_template(
        "index.html",
        foods_by_cat=foods_by_cat,
        defaults=defaults,
        MEALS=MEALS,
        WEEK_DAYS=WEEK_DAYS,           # NEW
        weekly_state=weekly_state,     # NEW
        selected_day=selected_day,     # NEW
        username=username,
        meal_choices=meal_choices,
        splits_per_meal=splits_per_meal,
    )

@app.route("/compute_meal", methods=["POST"])
def compute_meal():
    data = request.get_json() or {}
    meal = data.get("meal")
    if not meal:
        return jsonify({"error": "meal missing"}), 400

    calorie_tot = float(data.get("calorie_tot", 2000))
    perc_pasti = data.get("perc_pasti", {})
    perc = float(perc_pasti.get(meal, 0.0) or 0.0)
    cal_pasto = calorie_tot * perc

    macro_pasti = data.get("macro_pasti", {})
    macro_for_meal = macro_pasti.get(meal, {"carbo": 0.5, "prot": 0.3, "fat": 0.2})

    choices = data.get("choices", {"carboidrati": [], "proteine": [], "grassi": [], "frutta": [], "verdura": []})
    fruit_ratio = float(data.get("fruit_ratio", 0.0))
    veg_ratio = float(data.get("veg_ratio", 0.0))
    # min_ratio is removed
    try:
        min_grams = float(data.get("min_grams", 0.0) or 0.0)
    except Exception:
        min_grams = 0.0
    splits_text = data.get("splits_text", "")
    local_splits = parse_splits(splits_text)

    # NEW: effective min_grams (ensure small floor when same-category protein split exists)
    has_prot_split = _has_same_category_split(local_splits, "proteine")
    min_grams_eff = max(min_grams, 1.0) if has_prot_split else min_grams

    scelti, sol, quant_frutta, quant_verdura = calcola_dieta(
        cal_pasto, macro_for_meal, choices,
        carbo_db=carbo_db, prot_db=prot_db, grassi_db=grassi_db,
        frutta_db=frutta_db, verdura_db=verdura_db,
        fruit_ratio=fruit_ratio, veg_ratio=veg_ratio,
        min_grams=min_grams_eff  # CHANGED
    )

    if local_splits:
        sol_bilanciata, info = bilancia_conservando_macros(
            scelti, sol, local_splits,
            carbo_db, prot_db, grassi_db, frutta_db, verdura_db,
            min_grams=min_grams_eff  # CHANGED
        )
    else:
        sol_bilanciata = sol.copy() if hasattr(sol, "copy") else sol
        info = {"skipped": True}

    per_food, totals = calcola_calorie(scelti, sol_bilanciata, carbo_db, prot_db, grassi_db, frutta_db, verdura_db)
    per_food_serial = []
    for p in per_food:
        per_food_serial.append({
            "nome": p.get("nome"),
            "grammi": float(p.get("grammi") or 0),
            "kcal": float(p.get("kcal") or 0),
            "carbo": float(p.get("carbo") or 0),
            "proteine": float(p.get("proteine") or 0),
            "grassi": float(p.get("grassi") or 0)
        })

    # Aggiungi frutta e verdura come voci nel per_food (una voce per elemento scelto), se calcolate
    if quant_frutta is not None and choices.get("frutta"):
        for fr in choices.get("frutta", []):
            nut = frutta_db.get(fr)
            if not nut:
                continue
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
            if not nut:
                continue
            grams = float(quant_verdura)
            kcal = float(nut.get("calorie", 0.0)) * grams / 100.0
            carbo = float(nut.get("carboidrati", 0.0)) * grams / 100.0
            proteine = float(nut.get("proteine", 0.0)) * grams / 100.0
            grassi = float(nut.get("grassi", 0.0)) * grams / 100.0
            per_food_serial.append({"nome": vd, "grammi": grams, "kcal": kcal, "carbo": carbo, "proteine": proteine, "grassi": grassi})
            totals["kcal"] += kcal; totals["carbo"] += carbo; totals["proteine"] += proteine; totals["grassi"] += grassi

    totals_serial = {k: float(totals.get(k, 0.0)) for k in ("kcal", "carbo", "proteine", "grassi")}

    # NEW: enforce presence for split-referenced items before macro correction
    try:
        enforced = _enforce_split_presence(per_food_serial, local_splits, floor=min_grams_eff if min_grams_eff > 0 else 1.0)
    except Exception:
        enforced = None
    if enforced:
        per_food_serial, totals_serial = enforced

    # Attempt mixed-macro correction (skips if not applicable)
    try:
        corr = _apply_mixed_macro_correction(per_food_serial, cal_pasto, macro_for_meal, local_splits)
    except Exception:
        corr = None
    if corr:
        per_food_serial, totals_serial, corr_meta = corr
        if isinstance(info, dict):
            info["mixed_macro_correction"] = corr_meta

    # calorie confronto richieste vs ottenute
    requested_kcal = float(cal_pasto)
    kcal_delta = totals_serial.get("kcal", 0.0) - requested_kcal

    # contributo calorico per macronutriente (kcal) e percentuale
    carb_kcal = totals_serial.get("carbo", 0.0) * 4.0
    prot_kcal = totals_serial.get("proteine", 0.0) * 4.0
    fat_kcal  = totals_serial.get("grassi", 0.0) * 9.0
    macro_kcal_sum = carb_kcal + prot_kcal + fat_kcal
    # Use macro kcal sum as base for macro % to avoid calorie-field inconsistencies
    base_kcal = macro_kcal_sum if macro_kcal_sum > 0 else totals_serial.get("kcal", 0.0)
    macro_percent = {
        "carbo": {"kcal": carb_kcal, "percent": (carb_kcal / base_kcal) * 100.0 if base_kcal else 0.0},
        "proteine": {"kcal": prot_kcal, "percent": (prot_kcal / base_kcal) * 100.0 if base_kcal else 0.0},
        "grassi": {"kcal": fat_kcal, "percent": (fat_kcal / base_kcal) * 100.0 if base_kcal else 0.0},
    }

    return jsonify({
        "meal": meal,
        "per_food": per_food_serial,
        "totals": totals_serial,
        "quant_frutta": float(quant_frutta) if quant_frutta is not None else None,
        "quant_verdura": float(quant_verdura) if quant_verdura is not None else None,
        "info": info,
        "requested_kcal": requested_kcal,
        "kcal_delta": float(kcal_delta),
        "macro_kcal": macro_kcal_sum,
        "macro_breakdown": macro_percent
    })

@app.route("/compute_day", methods=["POST"])
def compute_day():
    data = request.get_json() or {}
    calorie_tot = float(data.get("calorie_tot", 2000))
    perc_pasti = data.get("perc_pasti", {})
    # normalize perc_pasti
    try:
        s = sum([float(v) for v in perc_pasti.values()]) or 1.0
    except Exception:
        s = 1.0
    for k in list(perc_pasti.keys()):
        try:
            perc_pasti[k] = float(perc_pasti[k]) / s
        except Exception:
            perc_pasti[k] = 0.0

    macro_pasti = data.get("macro_pasti", {})
    choices_all = data.get("choices", {})
    fruit_ratios = data.get("fruit_ratios", {}) or {}
    veg_ratios = data.get("veg_ratios", {}) or {}
    splits_per_meal_raw = data.get("splits_per_meal", {}) or {}
    # min_ratio is removed
    try:
        min_grams = float(data.get("min_grams", 0.0) or 0.0)
    except Exception:
        min_grams = 0.0

    results = {}
    total_day = {"kcal": 0.0, "carbo": 0.0, "proteine": 0.0, "grassi": 0.0}
    daily_per_food = []

    for m in MEALS:
        perc = float(perc_pasti.get(m, 0.0) or 0.0)
        cal_pasto = calorie_tot * perc
        macro_for_meal = macro_pasti.get(m, {"carbo": 0.5, "prot": 0.3, "fat": 0.2})
        choices = choices_all.get(m, {"carboidrati": [], "proteine": [], "grassi": [], "frutta": [], "verdura": []})
        fruit_ratio = float(fruit_ratios.get(m, 0.0) or 0.0)
        veg_ratio = float(veg_ratios.get(m, 0.0) or 0.0)
        splits_txt = splits_per_meal_raw.get(m, "")

        local_splits = parse_splits(splits_txt)

        # NEW: effective min_grams for protein split
        has_prot_split = _has_same_category_split(local_splits, "proteine")
        min_grams_eff = max(min_grams, 1.0) if has_prot_split else min_grams

        scelti, sol, quant_frutta, quant_verdura = calcola_dieta(
            cal_pasto, macro_for_meal, choices,
            carbo_db=carbo_db, prot_db=prot_db, grassi_db=grassi_db,
            frutta_db=frutta_db, verdura_db=verdura_db,
            fruit_ratio=fruit_ratio, veg_ratio=veg_ratio
            , min_grams=min_grams_eff  # CHANGED
        )

        if local_splits:
            sol_bilanciata, info = bilancia_conservando_macros(
                scelti, sol, local_splits, carbo_db, prot_db, grassi_db, frutta_db, verdura_db, min_grams=min_grams_eff  # CHANGED
            )
        else:
            sol_bilanciata = sol.copy() if hasattr(sol, "copy") else sol
            info = {"skipped": True}

        per_food, totals = calcola_calorie(scelti, sol_bilanciata, carbo_db, prot_db, grassi_db, frutta_db, verdura_db)
        per_food_serial = [
            {"nome": p.get("nome"), "grammi": float(p.get("grammi") or 0), "kcal": float(p.get("kcal") or 0),
             "carbo": float(p.get("carbo") or 0), "proteine": float(p.get("proteine") or 0), "grassi": float(p.get("grassi") or 0)}
            for p in per_food
        ]

        # aggiungi frutta
        if quant_frutta is not None and choices.get("frutta"):
            for fr in choices.get("frutta", []):
                nut = frutta_db.get(fr)
                if not nut:
                    continue
                grams = float(quant_frutta)
                kcal = float(nut.get("calorie", 0.0)) * grams / 100.0
                carbo = float(nut.get("carboidrati", 0.0)) * grams / 100.0
                proteine = float(nut.get("proteine", 0.0)) * grams / 100.0
                grassi = float(nut.get("grassi", 0.0)) * grams / 100.0
                per_food_serial.append({"nome": fr, "grammi": grams, "kcal": kcal, "carbo": carbo, "proteine": proteine, "grassi": grassi})
                totals["kcal"] += kcal; totals["carbo"] += carbo; totals["proteine"] += proteine; totals["grassi"] += grassi

        # aggiungi verdura
        if quant_verdura is not None and choices.get("verdura"):
            for vd in choices.get("verdura", []):
                nut = verdura_db.get(vd)
                if not nut:
                    continue
                grams = float(quant_verdura)
                kcal = float(nut.get("calorie", 0.0)) * grams / 100.0
                carbo = float(nut.get("carboidrati", 0.0)) * grams / 100.0
                proteine = float(nut.get("proteine", 0.0)) * grams / 100.0
                grassi = float(nut.get("grassi", 0.0)) * grams / 100.0
                per_food_serial.append({"nome": vd, "grammi": grams, "kcal": kcal, "carbo": carbo, "proteine": proteine, "grassi": grassi})
                totals["kcal"] += kcal; totals["carbo"] += carbo; totals["proteine"] += proteine; totals["grassi"] += grassi

        # NEW: enforce split presence before macro correction
        try:
            enforced = _enforce_split_presence(per_food_serial, local_splits, floor=min_grams_eff if min_grams_eff > 0 else 1.0)
        except Exception:
            enforced = None
        if enforced:
            per_food_serial, totals = enforced

        # Apply mixed-macro correction for this meal (skips if not applicable)
        try:
            corr = _apply_mixed_macro_correction(per_food_serial, cal_pasto, macro_for_meal, local_splits)
        except Exception:
            corr = None
        if corr:
            per_food_serial, totals = corr[0], corr[1]

        results[m] = {
            "scelti": scelti,
            "per_food": per_food_serial,
            "totals": {k: float(totals.get(k, 0.0)) for k in ("kcal", "carbo", "proteine", "grassi")},
            # macro kcal breakdown per pasto
            "macro_kcal": {
                "carbo": float(totals.get("carbo", 0.0)) * 4.0,
                "proteine": float(totals.get("proteine", 0.0)) * 4.0,
                "grassi": float(totals.get("grassi", 0.0)) * 9.0
            },
            "macro_percent": {},
            "quant_frutta": float(quant_frutta) if quant_frutta is not None else None,
            "quant_verdura": float(quant_verdura) if quant_verdura is not None else None,
            "info": info,
            "requested_kcal": cal_pasto
        }

        total_day["kcal"] += results[m]["totals"]["kcal"]
        total_day["carbo"] += results[m]["totals"]["carbo"]
        total_day["proteine"] += results[m]["totals"]["proteine"]
        total_day["grassi"] += results[m]["totals"]["grassi"]

        for p in results[m]["per_food"]:
            daily_per_food.append({"meal": m, **p})

    # compute macro percent per meal and daily averages
    # add macro_percent fields to each meal result
    for m in MEALS:
        tot = results[m]["totals"]
        mk = results[m].get("macro_kcal") or {
            "carbo": float(tot.get("carbo", 0.0)) * 4.0,
            "proteine": float(tot.get("proteine", 0.0)) * 4.0,
            "grassi": float(tot.get("grassi", 0.0)) * 9.0
        }
        sum_mk = mk.get("carbo", 0.0) + mk.get("proteine", 0.0) + mk.get("grassi", 0.0)
        if sum_mk <= 0:
            results[m]["macro_percent"] = {"carbo": 0.0, "proteine": 0.0, "grassi": 0.0}
        else:
            results[m]["macro_percent"] = {
                "carbo": (mk["carbo"] / sum_mk) * 100.0,
                "proteine": (mk["proteine"] / sum_mk) * 100.0,
                "grassi": (mk["grassi"] / sum_mk) * 100.0
            }

    # daily average percent contribution per macro (weighted by meal kcal)
    total_macro_kcal = {"carbo": 0.0, "proteine": 0.0, "grassi": 0.0}
    total_macro_kcal_sum = 0.0
    for m in MEALS:
        mk = results[m].get("macro_kcal") or {}
        total_macro_kcal["carbo"] += mk.get("carbo", 0.0)
        total_macro_kcal["proteine"] += mk.get("proteine", 0.0)
        total_macro_kcal["grassi"] += mk.get("grassi", 0.0)
    total_macro_kcal_sum = total_macro_kcal["carbo"] + total_macro_kcal["proteine"] + total_macro_kcal["grassi"]
    if total_macro_kcal_sum > 0:
        daily_macro_percent = {
            "carbo": (total_macro_kcal["carbo"] / total_macro_kcal_sum) * 100.0,
            "proteine": (total_macro_kcal["proteine"] / total_macro_kcal_sum) * 100.0,
            "grassi": (total_macro_kcal["grassi"] / total_macro_kcal_sum) * 100.0
        }
    else:
        daily_macro_percent = {"carbo": 0.0, "proteine": 0.0, "grassi": 0.0}

    return jsonify({
    "results": results,
    "total_day": total_day,
    "daily_per_food": daily_per_food,
    "daily_macro_percent": daily_macro_percent
    })

@app.get("/healthz")
def healthz():
    try:
        # minimal check: DBs loaded and template exists
        _ = build_foods_by_category()
        # NEW: verify state dir is writable
        test_path = os.path.join(STATE_DIR, ".writetest")
        with open(test_path, "w", encoding="utf-8") as f:
            f.write("ok")
            f.flush()
            os.fsync(f.fileno())
        os.remove(test_path)
        return {"ok": True, "state_dir": STATE_DIR}, 200
    except Exception as e:
        return {"ok": False, "error": str(e)}, 500

# ===== Helpers: lookup and mixed-macro correction =====
def _build_lc_map(db: dict) -> dict:
    return {str(k).strip().lower(): v for k, v in (db or {}).items()}

_CARBO_LC = _build_lc_map(carbo_db)
_PROT_LC = _build_lc_map(prot_db)
_GRASSI_LC = _build_lc_map(grassi_db)
_FRUTTA_LC = _build_lc_map(frutta_db)
_VERDURA_LC = _build_lc_map(verdura_db)

def _find_cat_and_nut(nome: str):
    k = (nome or "").strip().lower()
    if k in _CARBO_LC:  return "carboidrati", _CARBO_LC[k]
    if k in _PROT_LC:   return "proteine", _PROT_LC[k]
    if k in _GRASSI_LC: return "grassi", _GRASSI_LC[k]
    if k in _FRUTTA_LC: return "frutta", _FRUTTA_LC[k]
    if k in _VERDURA_LC:return "verdura", _VERDURA_LC[k]
    return None, None

def _split_has_cross_category(local_splits) -> bool:
    if not local_splits:
        return False
    for names in local_splits.keys():
        cats = set()
        for n in names:
            c, _ = _find_cat_and_nut(n)
            if c:
                cats.add(c)
        # consider only main categories in conflict check
        main = {c for c in cats if c in {"carboidrati","proteine","grassi"}}
        if len(main) > 1:
            return True
    return False

def _det3(a):
    # a: 3x3 list
    return (
        a[0][0]*(a[1][1]*a[2][2]-a[1][2]*a[2][1]) -
        a[0][1]*(a[1][0]*a[2][2]-a[1][2]*a[2][0]) +
        a[0][2]*(a[1][0]*a[2][1]-a[1][1]*a[2][0])
    )

def _inv3(a):
    d = _det3(a)
    if abs(d) < 1e-8:
        return None
    invd = 1.0/d
    # adjugate
    m00 =  (a[1][1]*a[2][2]-a[1][2]*a[2][1])*invd
    m01 = -(a[0][1]*a[2][2]-a[0][2]*a[2][1])*invd
    m02 =  (a[0][1]*a[1][2]-a[0][2]*a[1][1])*invd
    m10 = -(a[1][0]*a[2][2]-a[1][2]*a[2][0])*invd
    m11 =  (a[0][0]*a[2][2]-a[0][2]*a[2][0])*invd
    m12 = -(a[0][0]*a[1][2]-a[0][2]*a[1][0])*invd
    m20 =  (a[1][0]*a[2][1]-a[1][1]*a[2][0])*invd
    m21 = -(a[0][0]*a[2][1]-a[0][1]*a[2][0])*invd
    m22 =  (a[0][0]*a[1][1]-a[0][1]*a[1][0])*invd
    return [[m00,m01,m02],[m10,m11,m12],[m20,m21,m22]]

def _matvec(m, v):
    return [m[0][0]*v[0] + m[0][1]*v[1] + m[0][2]*v[2],
            m[1][0]*v[0] + m[1][1]*v[1] + m[1][2]*v[2],
            m[2][0]*v[0] + m[2][1]*v[1] + m[2][2]*v[2]]

def _recompute_items_and_totals(items_with_grams):
    # items_with_grams: list of tuples (nome, grams)
    per_food = []
    totals = {"kcal":0.0,"carbo":0.0,"proteine":0.0,"grassi":0.0}
    for nome, grams in items_with_grams:
        cat, nut = _find_cat_and_nut(nome)
        if not nut:
            continue
        g = float(grams) or 0.0
        cal = float(nut.get("calorie", 0.0)) * g / 100.0
        c   = float(nut.get("carboidrati", 0.0)) * g / 100.0
        p   = float(nut.get("proteine", 0.0)) * g / 100.0
        f   = float(nut.get("grassi", 0.0)) * g / 100.0
        per_food.append({"nome": nome, "grammi": g, "kcal": cal, "carbo": c, "proteine": p, "grassi": f})
        totals["kcal"] += cal; totals["carbo"] += c; totals["proteine"] += p; totals["grassi"] += f
    return per_food, totals

def _apply_mixed_macro_correction(per_food_serial, cal_pasto, macro_for_meal, local_splits):
    # Skip if splits bind across main categories
    if _split_has_cross_category(local_splits):
        return None
    # Build A (3x3) and fixed macro kcal from fruit/veg
    cols = ["carboidrati","proteine","grassi"]
    col_idx = {c:i for i,c in enumerate(cols)}
    A = [[0.0,0.0,0.0], [0.0,0.0,0.0], [0.0,0.0,0.0]]  # rows: C,P,F kcal; cols: categories
    fv = [0.0,0.0,0.0]  # fixed fruit/veg macro kcal
    # Collect original grams per item
    orig_items = []
    for it in per_food_serial:
        nome = it.get("nome")
        grams = float(it.get("grammi") or 0.0)
        cat, nut = _find_cat_and_nut(nome)
        if not nut:
            continue
        c = float(nut.get("carboidrati", 0.0)) * grams / 100.0
        p = float(nut.get("proteine", 0.0)) * grams / 100.0
        f = float(nut.get("grassi", 0.0)) * grams / 100.0
        ck, pk, fk = 4.0*c, 4.0*p, 9.0*f
        if cat in col_idx:
            j = col_idx[cat]
            A[0][j] += ck; A[1][j] += pk; A[2][j] += fk
        else:
            fv[0] += ck; fv[1] += pk; fv[2] += fk
        orig_items.append((nome, grams, cat))
    # If no main-category items, nothing to do
    if all(A[0][j]==0 and A[1][j]==0 and A[2][j]==0 for j in range(3)):
        return None
    # Target macro kcal
    targ = [
        cal_pasto * float(macro_for_meal.get("carbo", 0.0) or 0.0),
        cal_pasto * float(macro_for_meal.get("prot",  0.0) or 0.0),
        cal_pasto * float(macro_for_meal.get("fat",   0.0) or 0.0),
    ]
    b = [targ[0]-fv[0], targ[1]-fv[1], targ[2]-fv[2]]
    invA = _inv3(A)
    if invA is None:
        return None
    s = _matvec(invA, b)  # scale factors for [carboidrati, proteine, grassi]
    # Sanity: positive and bounded
    if any((not (s_i == s_i)) or s_i <= 0 or s_i > 20 for s_i in s):  # NaN check and bounds
        return None
    # Apply scaling
    s_map = {cols[i]: s[i] for i in range(3)}
    new_items = []
    for nome, grams, cat in orig_items:
        if cat in s_map:
            new_items.append((nome, grams * s_map[cat]))
        else:
            new_items.append((nome, grams))  # fruit/veg unchanged
    new_per_food, new_totals = _recompute_items_and_totals(new_items)
    meta = {"applied": True, "scale_factors": s_map}
    return new_per_food, new_totals, meta

if __name__ == "__main__":
    # Avvio locale/standalone: usa env o default
    debug = (os.environ.get("FLASK_DEBUG", "0") == "1")
    host = os.environ.get("HOST", "0.0.0.0")
    try:
        port = int(os.environ.get("PORT", "5000"))
    except Exception:
        port = 5000
    app.run(debug=debug, host=host, port=port)