from flask import Flask, render_template, request, jsonify
from DietaProg_functions import importa_db, calcola_dieta, bilancia_conservando_macros, calcola_calorie, verifica_pasto
import os
import pandas as pd
from openpyxl import load_workbook

app = Flask(__name__)

# Base directory for data files (supports env override)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DIETAPRO_DATA_DIR", BASE_DIR)

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

    # fallback: mantieni import CSV per compatibilità
    return (
        importa_db(filepath="carboidrati.csv"),
        importa_db(filepath="proteine.csv"),
        importa_db(filepath="grassi.csv"),
        importa_db(filepath="frutta.csv"),
        importa_db(filepath="verdura.csv")
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
            parts_pair = [p.strip() for p in pair.split(",")]
            if len(parts_pair) != 2:
                continue
            a, b = parts_pair
            val = val.strip()
            if "," in val:
                try:
                    parts = [float(x) for x in val.split(",")]
                except Exception:
                    continue
                out[(a, b)] = tuple(parts)
            else:
                try:
                    out[(a, b)] = float(val)
                except Exception:
                    continue
    return out or None

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
        }
    }
    return render_template("index.html", foods_by_cat=foods_by_cat, defaults=defaults, MEALS=MEALS)

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
    splits_text = data.get("splits_text", "")

    local_splits = parse_splits(splits_text)

    scelti, sol, quant_frutta, quant_verdura = calcola_dieta(
        cal_pasto, macro_for_meal, choices,
        carbo_db=carbo_db, prot_db=prot_db, grassi_db=grassi_db,
        frutta_db=frutta_db, verdura_db=verdura_db,
        fruit_ratio=fruit_ratio, veg_ratio=veg_ratio
    )

    if local_splits:
        sol_bilanciata, info = bilancia_conservando_macros(scelti, sol, local_splits, carbo_db, prot_db, grassi_db, frutta_db, verdura_db)
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

    return jsonify({
        "meal": meal,
        "per_food": per_food_serial,
        "totals": totals_serial,
    "quant_frutta": float(quant_frutta) if quant_frutta is not None else None,
    "quant_verdura": float(quant_verdura) if quant_verdura is not None else None,
        "info": info
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

        scelti, sol, quant_frutta, quant_verdura = calcola_dieta(
            cal_pasto, macro_for_meal, choices,
            carbo_db=carbo_db, prot_db=prot_db, grassi_db=grassi_db,
            frutta_db=frutta_db, verdura_db=verdura_db,
            fruit_ratio=fruit_ratio, veg_ratio=veg_ratio
        )

        if local_splits:
            sol_bilanciata, info = bilancia_conservando_macros(scelti, sol, local_splits, carbo_db, prot_db, grassi_db, frutta_db, verdura_db)
        else:
            sol_bilanciata = sol.copy() if hasattr(sol, "copy") else sol
            info = {"skipped": True}

        per_food, totals = calcola_calorie(scelti, sol_bilanciata, carbo_db, prot_db, grassi_db, frutta_db, verdura_db)

        # costruisco per_food seriale e includo frutta/verdura come voci se presenti
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

        results[m] = {
            "scelti": scelti,
            "per_food": per_food_serial,
            "totals": {k: float(totals.get(k, 0.0)) for k in ("kcal", "carbo", "proteine", "grassi")},
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

    return jsonify({
        "results": results,
        "total_day": total_day,
        "daily_per_food": daily_per_food
    })

@app.get("/healthz")
def healthz():
    try:
        # minimal check: DBs loaded and template exists
        _ = build_foods_by_category()
        return {"ok": True}, 200
    except Exception as e:
        return {"ok": False, "error": str(e)}, 500

if __name__ == "__main__":
    # Avvio locale/standalone: usa env o default
    debug = (os.environ.get("FLASK_DEBUG", "0") == "1")
    host = os.environ.get("HOST", "0.0.0.0")
    try:
        port = int(os.environ.get("PORT", "5000"))
    except Exception:
        port = 5000
    app.run(debug=debug, host=host, port=port)