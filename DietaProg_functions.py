import numpy as np
import pandas as pd
import os
from scipy.optimize import lsq_linear

def carica_database():
    """Carica i CSV e restituisce i dizionari degli alimenti."""
    categorie = ["carboidrati", "proteine", "grassi", "frutta", "verdura"]
    db = {}
    for cat in categorie:
        df = pd.read_csv(f"{cat}.csv")
        db[cat] = df.set_index("nome").T.to_dict()
    return db

def importa_db(filepath):
    """
    Importa il database dei carboidrati da un file CSV.
    Restituisce un dizionario con il nome come chiave e i valori nutrizionali come sotto-dizionario.
    """
    # comportamento backward-compatible:
    # - se 'filepath' è un file esistente e termina con .csv -> legge CSV
    # - se 'filepath' è un file excel (.xls/.xlsx) -> legge il foglio di default (first sheet)
    # - se 'filepath' non esiste come file, interpretiamo il base name come nome della categoria
    #   e cerchiamo un file excel nella working directory (nomi comuni) contenente un foglio
    #   con quel nome (es. sheet 'carboidrati'). Questo permette di mantenere le chiamate
    #   esistenti tipo importa_db("carboidrati.csv") senza dover cambiare il resto del codice.

    # 1) se il path fornito esiste
    if os.path.isfile(filepath):
        if filepath.lower().endswith(('.xls', '.xlsx')):
            # leggi primo foglio se non specificato
            df = pd.read_excel(filepath, sheet_name=0)
        else:
            df = pd.read_csv(filepath)
        db = df.set_index("nome").T.to_dict()
        return db

    # 2) altrimenti, prova a interpretare 'filepath' come nome categoria e cerca in un workbook
    sheet_name = os.path.splitext(os.path.basename(filepath))[0]

    # candidate excel filenames (puoi aggiungere altri nomi comuni qui)
    candidates = [
        'alimenti.xlsx', 'database.xlsx', 'db.xlsx', 'alimenti_db.xlsx', 'dati_alimenti.xlsx'
    ]

    # se viene passato esplicitamente un percorso assoluto o relativo senza estensione, prova anche quello
    if os.path.isfile(sheet_name + '.xlsx'):
        candidates.insert(0, sheet_name + '.xlsx')

    # cerca i file candidati
    for ex in candidates:
        if os.path.isfile(ex):
            try:
                df = pd.read_excel(ex, sheet_name=sheet_name)
                db = df.set_index("nome").T.to_dict()
                return db
            except Exception:
                # se il foglio non esiste o errore di parsing, prosegui con il prossimo candidato
                continue

    # ultima risorsa: prova a leggere da qualsiasi file excel presente nella cartella di lavoro
    for f in os.listdir('.'):
        if f.lower().endswith(('.xls', '.xlsx')):
            try:
                df = pd.read_excel(f, sheet_name=sheet_name)
                db = df.set_index("nome").T.to_dict()
                return db
            except Exception:
                continue

    raise FileNotFoundError(f"Nessun file CSV trovabile e nessun foglio '{sheet_name}' in file excel nella cartella corrente.")

def calcola_target(calorie_tot, perc_macro):
    """Converte percentuali macro in grammi target."""
    carb_target = calorie_tot * perc_macro["carbo"] / 4
    prot_target = calorie_tot * perc_macro["prot"] / 4
    fat_target  = calorie_tot * perc_macro["fat"] / 9
    return carb_target, prot_target, fat_target


def calcola_dieta(calorie_pasto, perc_macro, scelta, carbo_db, prot_db, grassi_db, frutta_db, verdura_db, fruit_ratio=0.1, veg_ratio=0.1, min_ratio: float = 0.05):
    """
    Calcola i grammi di alimenti scelti per un pasto.
    - calorie_pasto: kcal totali del pasto
    - perc_macro: dict con % macro {"carbo":x, "prot":y, "fat":z}
    - scelta: dict {"carboidrati":[...], "proteine":[...], "grassi":[...], "frutta":[...], "verdura":[...]}
    - db: database alimenti
    - fruit_ratio: quota kcal da frutta
    - veg_ratio: quota kcal da verdura
    """
    # quote frutta/verdura
    calorie_fruit = calorie_pasto * fruit_ratio
    calorie_veg   = calorie_pasto * veg_ratio
    # vogliamo includere la frutta nell'equazione dei macronutrienti, mantenendo il limite
    # di calorie per la frutta; invece la verdura rimane fuori dalla risoluzione

    # target macro: calcoliamo sui kcal totali meno la verdura (includendo quindi frutta)
    calorie_for_macros = calorie_pasto - calorie_veg
    carb_target, prot_target, fat_target = calcola_target(calorie_for_macros, perc_macro)
    b = np.array([carb_target, prot_target, fat_target])

    # costruisco lista di alimenti principali includendo anche la frutta
    food_list = []
    food_db_list = []
    food_origin = []  # 'carboidrati' | 'proteine' | 'grassi' | 'frutta'
    for f in scelta.get("carboidrati", []):
        food_list.append(f); food_db_list.append(carbo_db); food_origin.append("carboidrati")
    for f in scelta.get("proteine", []):
        food_list.append(f); food_db_list.append(prot_db); food_origin.append("proteine")
    for f in scelta.get("grassi", []):
        food_list.append(f); food_db_list.append(grassi_db); food_origin.append("grassi")
    # includi frutta nella risoluzione (se presente)
    for f in scelta.get("frutta", []):
        food_list.append(f); food_db_list.append(frutta_db); food_origin.append("frutta")

    if not food_list:
        raise ValueError("Devi selezionare almeno un alimento tra carboidrati, proteine o grassi.")

    # costruzione matrice A (3 x n_foods), valori nutrienti per grammo (i dati sono per 100 g -> divido per 100)
    A_cols = []
    kcal_per_g_list = []
    for f, db in zip(food_list, food_db_list):
        nut = db.get(f)
        if nut is None:
            raise KeyError(f"Alimento '{f}' non trovato nel database corrispondente.")
        col = np.array([
            nut.get("carboidrati", 0),
            nut.get("proteine", 0),
            nut.get("grassi", 0)
        ]) / 100.0
        A_cols.append(col)
        kcal_per_g_list.append(nut.get("calorie", 0.0) / 100.0)
    A_macros = np.column_stack(A_cols)  # forma (3, n_foods)

    # se ci sono frutta, aggiungiamo una riga che impone il vincolo delle kcal di frutta
    if scelta.get("frutta"):
        # costruisco la riga dei kcal: solo le colonne corrispondenti a frutta contengono valori
        kcal_row = np.array([ (k if db is frutta_db else 0.0) for k, db in zip(kcal_per_g_list, food_db_list)])
        # unisco le righe (3 macro + 1 kcal_frutta)
        A = np.vstack([A_macros, kcal_row])
        b = np.concatenate([b, np.array([calorie_fruit])])
    else:
        A = A_macros

    # vincoli di partecipazione minima: ogni alimento principale (carbo/prot/grassi)
    # deve contribuire almeno a min_ratio delle calorie della parte principale
    MIN_RATIO = max(0.0, min(0.5, float(min_ratio)))
    CAP_RATIO = 0.90  # cap sulla somma dei minimi per stabilità
    n = len(food_list)
    lb = np.zeros(n, dtype=float)
    # kcal per grammo
    kpg = np.array(kcal_per_g_list, dtype=float)
    is_main = np.array([orig in ("carboidrati","proteine","grassi") for orig in food_origin], dtype=bool)
    is_fruit = np.array([orig == "frutta" for orig in food_origin], dtype=bool)
    # min kcal per ciascun gruppo
    min_kcal_main = np.where(is_main, MIN_RATIO * calorie_for_macros, 0.0)
    total_min_main = float(min_kcal_main.sum())
    cap_main = CAP_RATIO * max(0.0, float(calorie_for_macros))
    scale_main = 1.0
    if total_min_main > cap_main and total_min_main > 0:
        scale_main = cap_main / total_min_main
    min_kcal_main *= scale_main

    min_kcal_fruit = np.where(is_fruit, MIN_RATIO * calorie_fruit, 0.0)
    total_min_fruit = float(min_kcal_fruit.sum())
    cap_fruit = CAP_RATIO * max(0.0, float(calorie_fruit))
    scale_fruit = 1.0
    if total_min_fruit > cap_fruit and total_min_fruit > 0:
        scale_fruit = cap_fruit / total_min_fruit
    min_kcal_fruit *= scale_fruit

    min_kcal_each = min_kcal_main + min_kcal_fruit
    # converti in grammi: g = kcal / (kcal/g)
    with np.errstate(divide='ignore', invalid='ignore'):
        lb = np.where((is_main) & (kpg > 0), min_kcal_each / kpg, 0.0)

    # risolvo il problema least-squares con vincoli lb <= x
    res = lsq_linear(A, b, bounds=(lb, np.inf))
    sol = res.x  # grammi per ciascun alimento in food_list

    # distribuzione verdura (rimane come prima)
    quant_verdura = None
    if scelta.get("verdura"):
        kcal_per_food = calorie_veg / len(scelta["verdura"])
        kcal_f = verdura_db[scelta["verdura"][0]]["calorie"]
        quant_verdura = (kcal_per_food / kcal_f) * 100  # grammi

    # non restituiamo più un singolo valore quant_frutta calcolato in modo separato
    # dato che la frutta è ora parte della soluzione (le sue gramme sono in 'sol' per gli elementi corrispondenti)
    quant_frutta = None

    return food_list, sol, quant_frutta, quant_verdura


def stampa_risultati(pasto, scelti, sol, quant_frutta, quant_verdura):
    print(f"\n=== {pasto.upper()} ===")
    for food, grams in zip(scelti, sol):
        print(f"{food}: {grams:.1f} g")
    # frutta/verdura output removed (handled in web UI)


def bilancia_conservando_macros(food_list, sol, splits, carbo_db, prot_db, grassi_db, frutta_db, verdura_db, min_ratio: float = 0.05):
    """
    Impone gli split (come rapporto di kcal tra coppie) mantenendo i vincoli globali sui macronutrienti.
    - food_list: lista nomi alimenti (nell'ordine di 'sol')
    - sol: soluzione originale (grammi per alimento)
    - splits: dict, chiave 'a,b' o (a,b); valore frazione (0..1 o %) oppure (a,b) -> a/(a+b)
    - *_db: dizionari dei nutrienti (alimenti per 100g)
    Ritorna: (new_sol, info)
    """
    if splits is None or len(splits) == 0:
        return np.array(sol, dtype=float), {
            "success": True,
            "message": "Nessuno split richiesto",
            "resid_macro_norm": 0.0,
            "delta_from_original_norm": 0.0,
            "applied_splits": []
        }

    def norm_name(s):
        return " ".join(str(s).strip().lower().split())

    # mappa nome normalizzato -> indice
    idx_map = {norm_name(n): i for i, n in enumerate(food_list)}

    # helper: trova DB per alimento
    def find_db(food):
        if food in carbo_db:   return carbo_db
        if food in prot_db:    return prot_db
        if food in grassi_db:  return grassi_db
        if food in frutta_db:  return frutta_db
        if food in verdura_db: return verdura_db
        # assumiamo input corretto; al limite KeyError esplicito
        raise KeyError(f"Alimento '{food}' non trovato in nessun DB.")

    # costruisci matrice dei macro A (3 x n) e kcal_per_g (n,)
    n = len(food_list)
    A_cols = []
    kcal_per_g = np.zeros(n, dtype=float)
    food_origin = []
    for i, name in enumerate(food_list):
        db = find_db(name)
        nut = db[name]
        col = np.array([
            float(nut.get("carboidrati", 0.0)),
            float(nut.get("proteine", 0.0)),
            float(nut.get("grassi", 0.0)),
        ]) / 100.0
        A_cols.append(col)
        kcal_per_g[i] = float(nut.get("calorie", 0.0)) / 100.0
        if name in carbo_db:
            food_origin.append("carboidrati")
        elif name in prot_db:
            food_origin.append("proteine")
        elif name in grassi_db:
            food_origin.append("grassi")
        elif frutta_db and name in frutta_db:
            food_origin.append("frutta")
        elif verdura_db and name in verdura_db:
            food_origin.append("verdura")
        else:
            food_origin.append("altro")
    A = np.column_stack(A_cols)  # (3 x n)
    b = A.dot(np.array(sol, dtype=float))  # target macro totali

    # parser split
    def parse_pair(key):
        if isinstance(key, (tuple, list)) and len(key) == 2:
            return str(key[0]), str(key[1])
        if isinstance(key, str):
            parts = [p.strip() for p in key.split(",")]
            if len(parts) == 2:
                return parts[0], parts[1]
        raise ValueError(f"Chiave split non valida: {key}")

    def to_fraction(v):
        if isinstance(v, (tuple, list)) and len(v) == 2:
            a, b = float(v[0]), float(v[1])
            s = a + b
            return 0.5 if s == 0 else (a / s)
        f = float(v)
        if f > 1.0:
            f /= 100.0
        # assumiamo input corretto 0<alpha<1; clamp minimo per stabilità
        return max(1e-9, min(1 - 1e-9, f))

    # vincoli minimi per alimento (solo principali): almeno 5% delle kcal totali della parte principale
    MIN_RATIO = max(0.0, min(0.5, float(min_ratio)))
    CAP_RATIO = 0.90
    is_main = np.array([o in ("carboidrati","proteine","grassi") for o in food_origin], dtype=bool)
    is_fruit = np.array([o == "frutta" for o in food_origin], dtype=bool)
    total_kcal_main = float(np.dot(kcal_per_g, np.array(sol, dtype=float) * (is_main.astype(float))))
    if total_kcal_main <= 0:
        total_kcal_main = float(np.dot(kcal_per_g, np.array(sol, dtype=float)))
    min_kcal_main = np.where(is_main, MIN_RATIO * total_kcal_main, 0.0)
    total_min_main = float(min_kcal_main.sum())
    cap_main = CAP_RATIO * total_kcal_main if total_kcal_main > 0 else 0.0
    scale_main = 1.0
    if total_kcal_main > 0 and total_min_main > cap_main and total_min_main > 0:
        scale_main = cap_main / total_min_main
    min_kcal_main *= scale_main

    total_kcal_fruit = float(np.dot(kcal_per_g, np.array(sol, dtype=float) * (is_fruit.astype(float))))
    min_kcal_fruit = np.where(is_fruit, MIN_RATIO * total_kcal_fruit, 0.0)
    total_min_fruit = float(min_kcal_fruit.sum())
    cap_fruit = CAP_RATIO * total_kcal_fruit if total_kcal_fruit > 0 else 0.0
    scale_fruit = 1.0
    if total_kcal_fruit > 0 and total_min_fruit > cap_fruit and total_min_fruit > 0:
        scale_fruit = cap_fruit / total_min_fruit
    min_kcal_fruit *= scale_fruit

    min_kcal_each = min_kcal_main + min_kcal_fruit
    # lb in grammi per x
    lb_x = np.where((is_main) & (kcal_per_g > 0), min_kcal_each / kcal_per_g, 0.0)

    # costruisci la trasformazione M: x = M·y
    # - per ogni coppia (i,j,alpha): una colonna che rappresenta i kcal totali della coppia
    #   x_i = (alpha / k_i) * y, x_j = ((1 - alpha) / k_j) * y
    # - per ogni indice non usato in coppia: colonna identità
    assigned = set()
    cols = []
    applied = []
    # per bounds su y
    lb_y_list = []

    for key, val in splits.items():
        a_name, b_name = parse_pair(key)
        ia = idx_map[norm_name(a_name)]
        ib = idx_map[norm_name(b_name)]
        alpha = to_fraction(val)
        ka = kcal_per_g[ia]
        kb = kcal_per_g[ib]
        # sicurezza anti-divisione per zero
        if ka <= 0: ka = 1e-9
        if kb <= 0: kb = 1e-9
        col = np.zeros(n, dtype=float)
        col[ia] = alpha / ka
        col[ib] = (1.0 - alpha) / kb
        cols.append(col)
        assigned.add(ia)
        assigned.add(ib)
        applied.append({
            "pair": (food_list[ia], food_list[ib]),
            "alpha": float(alpha)
        })
        # lower bound su y per rispettare lb_x su entrambe le componenti
        # x_i = (alpha/ka)*y >= lb_x[i]  => y >= lb_x[i]*ka/alpha
        # x_j = ((1-alpha)/kb)*y >= lb_x[j] => y >= lb_x[j]*kb/(1-alpha)
        ya = (lb_x[ia] * ka / max(alpha, 1e-9))
        yb = (lb_x[ib] * kb / max(1.0 - alpha, 1e-9))
        lb_y_list.append(max(0.0, ya, yb))

    for i in range(n):
        if i in assigned:
            continue
        col = np.zeros(n, dtype=float)
        col[i] = 1.0
    cols.append(col)
    lb_y_list.append(max(0.0, lb_x[i]))

    M = np.column_stack(cols) if cols else np.eye(n)
    A_red = A.dot(M)

    # risolvo per y imponendo non negatività
    lb_y = np.array(lb_y_list, dtype=float) if lb_y_list else 0.0
    res = lsq_linear(A_red, b, bounds=(lb_y, np.inf))
    y = res.x
    new_sol = M.dot(y)

    # post: elimina numeri molto piccoli negativi dovuti a num. errors
    new_sol[new_sol < 0] = 0.0

    resid_macro = A.dot(new_sol) - b
    info = {
        "success": bool(res.success),
        "message": getattr(res, "message", ""),
        "resid_macro_norm": float(np.linalg.norm(resid_macro)),
        "delta_from_original_norm": float(np.linalg.norm(new_sol - np.array(sol, dtype=float))),
        "applied_splits": applied
    }
    return new_sol, info

def calcola_calorie(food_list, sol, carbo_db, prot_db, grassi_db, frutta_db=None, verdura_db=None):
    """
    Restituisce il dettaglio per alimento e i totali reali (kcal e grammi macro).
    - food_list: lista nomi alimenti nell'ordine corrispondente a sol
    - sol: array-like di grammi per ogni alimento
    - *_db: dizionari importati con importa_db
    Ritorna (per_food, totals) dove:
      per_food = [ { "nome":..., "grammi":..., "kcal":..., "carbo":..., "proteine":..., "grassi":... }, ... ]
      totals = { "kcal":..., "carbo":..., "proteine":..., "grassi":... }
    """
    def find_db_for(food):
        if food in carbo_db:
            return carbo_db
        if food in prot_db:
            return prot_db
        if food in grassi_db:
            return grassi_db
        if frutta_db and food in frutta_db:
            return frutta_db
        if verdura_db and food in verdura_db:
            return verdura_db
        raise KeyError(f"Alimento '{food}' non trovato in nessun DB.")

    per_food = []
    total_kcal = 0.0
    total_carbo = 0.0
    total_proteine = 0.0
    total_grassi = 0.0

    for nome, grams in zip(food_list, sol):
        db = find_db_for(nome)
        nut = db.get(nome)
        if nut is None:
            raise KeyError(f"Alimento '{nome}' non trovato nel DB selezionato.")
        kcal_per_100 = float(nut.get("calorie", 0.0))
        carbo_per_100 = float(nut.get("carboidrati", 0.0))
        prot_per_100  = float(nut.get("proteine", 0.0))
        fat_per_100   = float(nut.get("grassi", 0.0))

        factor = grams / 100.0
        kcal = kcal_per_100 * factor
        carbo = carbo_per_100 * factor
        proteine = prot_per_100 * factor
        grassi = fat_per_100 * factor

        per_food.append({
            "nome": nome,
            "grammi": float(grams),
            "kcal": kcal,
            "carbo": carbo,
            "proteine": proteine,
            "grassi": grassi,
        })

        total_kcal += kcal
        total_carbo += carbo
        total_proteine += proteine
        total_grassi += grassi

    totals = {
        "kcal": total_kcal,
        "carbo": total_carbo,
        "proteine": total_proteine,
        "grassi": total_grassi
    }

    return per_food, totals

def verifica_split(per_food_new, totals_new, per_food, totals_orig, splits):
    """
    Verifica e stampa il confronto tra split richiesti e split ottenuti.
    Matching dei nomi robusto a maiuscole/minuscole e spazi extra.
    """
    print("\n--- VERIFICA CALORIE / MACRO ---")
    print(f"Originale: kcal={totals_orig['kcal']:.1f}, carbo={totals_orig['carbo']:.1f}g, prot={totals_orig['proteine']:.1f}g, grassi={totals_orig['grassi']:.1f}g")
    print(f"Bilanciata: kcal={totals_new['kcal']:.1f}, carbo={totals_new['carbo']:.1f}g, prot={totals_new['proteine']:.1f}g, grassi={totals_new['grassi']:.1f}g")
    print(f"Delta kcal = {totals_new['kcal'] - totals_orig['kcal']:.3f} kcal")

    print("\n--- Dettaglio per alimento (bilanciata) ---")
    for p in per_food_new:
        print(f"{p['nome']}: {p['grammi']:.1f} g, {p['kcal']:.1f} kcal, C:{p['carbo']:.1f}g P:{p['proteine']:.1f}g F:{p['grassi']:.1f}g")

    print("\n--- Controllo splits ---")
    def _frac_from_val(v):
        if isinstance(v, (list,tuple)) and len(v)==2:
            a,b = float(v[0]), float(v[1]); s=a+b
            return a/s if s!=0 else 0.0
        f = float(v)
        return (f/100.0) if f>1 else f

    def _norm_name(s):
        return " ".join(str(s).strip().lower().split())

    def _find_kcal_by_name(name):
        n = _norm_name(name)
        for x in per_food_new:
            if _norm_name(x['nome']) == n:
                return x['kcal']
        return None

    for key, val in (splits or {}).items():
        if isinstance(key, (tuple,list)):
            a_name, b_name = key[0], key[1]
        else:
            parts = [x.strip() for x in key.split(",")]
            if len(parts) != 2:
                print(f"{key}: formato non valido (usa 'a,b' o (a,b))")
                continue
            a_name, b_name = parts[0], parts[1]
        kcal_a = _find_kcal_by_name(a_name)
        kcal_b = _find_kcal_by_name(b_name)
        if kcal_a is None or kcal_b is None:
            print(f"{a_name},{b_name}: non presenti nella lista dei principali")
            continue
        frac_req = _frac_from_val(val)
        frac_ach = kcal_a / (kcal_a + kcal_b) if (kcal_a + kcal_b) > 0 else 0.0
        print(f"{a_name}/{b_name} richiesta={frac_req*100:.1f}%, ottenuta={frac_ach*100:.2f}%  (kcal {kcal_a:.1f}/{kcal_b:.1f})")
    return None

def verifica_pasto(pasto,
                   per_food_new, totals_new,
                   per_food_orig=None, totals_orig=None,
                   splits=None,
                   requested_total_kcal=None, requested_fruit_kcal=None, requested_veg_kcal=None,
                   perc_macro=None,
                   frutta_db=None, verdura_db=None,
                   quant_frutta=None, quant_verdura=None):
    """
    Verifica una soluzione per un pasto:
      - confronto originale vs bilanciata (se forniti per_food_orig/totals_orig)
      - confronto vs richiesta iniziale (se forniti requested_* e perc_macro)
      - controllo degli split (usa la funzione verifica_split già presente)

    Parametri principali:
      - pasto: nome del pasto (string)
      - per_food_new, totals_new: risultati reali della soluzione (lista + dict) (parte principale)
      - per_food_orig, totals_orig: risultati originali (opzionale)
      - splits: dict con split richiesti (opzionale)
      - requested_total_kcal, requested_fruit_kcal, requested_veg_kcal: valori richiesti (kcal)
      - perc_macro: dict macro per la parte principale es. {"carbo":0.5,"prot":0.3,"fat":0.2}
      - frutta_db, verdura_db: db per calcolo kcal frutta/verdura (opzionale)
      - quant_frutta, quant_verdura: grammi calcolati per frutta/verdura (opzionale)
    """
    print(f"\n=== VERIFICA PASTO: {pasto.upper()} ===")

    # confronto originale vs bilanciata
    if totals_orig is not None and per_food_orig is not None:
        print("\n--- Originale vs Bilanciata (parte principale) ---")
        print(f"Originale: kcal={totals_orig['kcal']:.1f}, C={totals_orig['carbo']:.1f} g, P={totals_orig['proteine']:.1f} g, F={totals_orig['grassi']:.1f} g")
        print(f"Bilanciata: kcal={totals_new['kcal']:.1f}, C={totals_new['carbo']:.1f} g, P={totals_new['proteine']:.1f} g, F={totals_new['grassi']:.1f} g")
        print(f"Delta kcal = {totals_new['kcal'] - totals_orig['kcal']:.3f} kcal")

    # confronto con richiesta iniziale (se presente)
    if requested_total_kcal is not None and perc_macro is not None:
        # se non forniti, assume 0 per frutta/verdura
        req_fruit = requested_fruit_kcal or 0.0
        req_veg   = requested_veg_kcal or 0.0
        requested_main_kcal = requested_total_kcal - (req_fruit + req_veg)
        req_carb_g, req_prot_g, req_fat_g = calcola_target(requested_main_kcal, perc_macro)

        print("\n--- CONFRONTO CON RICHIESTA INIZIALE ---")
        print(f"Richiesto totale pasto: {requested_total_kcal:.1f} kcal  (frutta {req_fruit:.1f} kcal, verdura {req_veg:.1f} kcal, parte principale {requested_main_kcal:.1f} kcal)")
        print(f"Richiesti macronutrienti (parte principale): C={req_carb_g:.1f} g, P={req_prot_g:.1f} g, F={req_fat_g:.1f} g\n")

        
        print(f"Ottenuto (parte principale): kcal={totals_new['kcal']:.1f}, C={totals_new['carbo']:.1f} g, P={totals_new['proteine']:.1f} g, F={totals_new['grassi']:.1f} g")
        print(f"Richiesti (parte principale): kcal={requested_main_kcal:.1f}, C={req_carb_g:.1f} g, P={req_prot_g:.1f} g, F={req_fat_g:.1f} g")
        print(f"Delta kcal parte principale = {totals_new['kcal'] - requested_main_kcal:.3f} kcal, Delta C = {totals_new['carbo'] - req_carb_g:.3f} g, Delta P = {totals_new['proteine'] - req_prot_g:.3f} g, Delta F = {totals_new['grassi'] - req_fat_g:.3f} g\n")

        # confronto frutta/verdura ottenuta (se grammi e db forniti)
        if quant_frutta is not None and frutta_db is not None and requested_fruit_kcal is not None:
            # quant_frutta in grammi e frutta_db contiene "calorie" per 100g
            # se il nome non è noto qui, caller dovrebbe passare kcal già calcolate in requested_fruit_kcal
            # qui proviamo a calcolare kcal reali se possibile: caller deve passare nome tramite quant_frutta (float) + frutta_db + scelta elsewhere
            try:
                real_fruit_kcal = (quant_frutta / 100.0) * float(next(iter(frutta_db.values()))["calorie"])
            except Exception:
                real_fruit_kcal = None
            if real_fruit_kcal is not None:
                print(f"Frutta richiesta: {req_fruit:.1f} kcal  - ottenuta: {real_fruit_kcal:.1f} kcal ({quant_frutta:.1f} g)")
        elif requested_fruit_kcal is not None:
            # se non abbiamo dati reali, mostra solo i richiesti
            print(f"Frutta richiesta: {req_fruit:.1f} kcal  - ottenuta: (dati frutta non forniti)")

        if quant_verdura is not None and verdura_db is not None and requested_veg_kcal is not None:
            try:
                real_veg_kcal = (quant_verdura / 100.0) * float(next(iter(verdura_db.values()))["calorie"])
            except Exception:
                real_veg_kcal = None
            if real_veg_kcal is not None:
                print(f"Verdura richiesta: {req_veg:.1f} kcal - ottenuta: {real_veg_kcal:.1f} kcal ({quant_verdura:.1f} g)")
        elif requested_veg_kcal is not None:
            print(f"Verdura richiesta: {req_veg:.1f} kcal - ottenuta: (dati verdura non forniti)")

    # controllo splits: se esiste la funzione verifica_split già definita la richiamiamo
    try:
        # la vecchia funzione prende (per_food_new, totals_new, per_food_orig, totals_orig, splits)
        if 'verifica_split' in globals():
            # se abbiamo anche i dati originali passamoli, altrimenti passiamo None
            _per_orig = per_food_orig if per_food_orig is not None else []
            _tot_orig = totals_orig if totals_orig is not None else {"kcal":0,"carbo":0,"proteine":0,"grassi":0}
            verifica_split(per_food_new, totals_new, _per_orig, _tot_orig, splits)
    except Exception as e:
        print(f"Errore nel controllo splits: {e}")

    # ritorniamo un riassunto utile anche per test automatici
    return {
        "pasto": pasto,
        "totals_new": totals_new,
        "totals_orig": totals_orig,
        "requested_total_kcal": requested_total_kcal,
        "requested_main_kcal": (requested_total_kcal - (requested_fruit_kcal or 0) - (requested_veg_kcal or 0)) if requested_total_kcal is not None else None
    }