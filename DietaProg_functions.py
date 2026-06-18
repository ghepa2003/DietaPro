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


def deriva_macro_pasto(macro_tot: dict, perc_pasti: dict, locked_macros: dict = None) -> dict:
    """
    Deriva i target macro per ogni pasto garantendo che la media ponderata
    (pesata per le calorie di ciascun pasto) coincida con il target giornaliero.

    Args:
        macro_tot:     {"carbo": float, "prot": float, "fat": float}  target giornaliero (frazioni)
        perc_pasti:    {"colazione": float, ...}  frazione calorica per pasto (idealmente somma a 1)
        locked_macros: {"colazione": {"carbo": float, ...}, ...}  pasti con macro bloccate dall'utente

    Returns:
        {"colazione": {"carbo": float, "prot": float, "fat": float}, ...} per tutti i pasti
    """
    locked_macros = locked_macros or {}
    keys = ("carbo", "prot", "fat")
    result = {}

    # Normalizza perc_pasti in modo che sommino a 1
    total_perc = sum(float(v) for v in perc_pasti.values())
    if total_perc > 1e-9:
        norm_perc = {m: float(v) / total_perc for m, v in perc_pasti.items()}
    else:
        n = max(len(perc_pasti), 1)
        norm_perc = {m: 1.0 / n for m in perc_pasti}

    # Calcola il "budget" macro residuo dopo aver sottratto i pasti bloccati
    residual = {k: float(macro_tot.get(k, 0.0)) for k in keys}
    free_perc_total = 0.0

    for meal, perc in norm_perc.items():
        if meal in locked_macros:
            locked = locked_macros[meal]
            result[meal] = {k: float(locked.get(k, macro_tot.get(k, 0.0))) for k in keys}
            for k in keys:
                residual[k] -= perc * result[meal][k]
        else:
            free_perc_total += perc

    # Distribuisce il residuo equamente tra i pasti liberi
    if free_perc_total > 1e-9:
        free_macro = {k: max(0.0, min(1.0, residual[k] / free_perc_total)) for k in keys}
    else:
        # Tutti i pasti sono bloccati; usa il target globale come fallback
        free_macro = {k: float(macro_tot.get(k, 0.0)) for k in keys}

    for meal in norm_perc:
        if meal not in result:
            result[meal] = {k: free_macro[k] for k in keys}

    return result


def calcola_dieta(calorie_pasto, perc_macro, scelta, carbo_db, prot_db, grassi_db, frutta_db, verdura_db, fruit_ratio=0.1, veg_ratio=0.1, min_grams: float = 0.0, food_constraints: dict = None, splits: list = None):
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
    # includi frutta nella risoluzione (se presente) solo se viene richiesta quota kcal per frutta
    # in caso fruit_ratio==0 l'utente non vuole che la frutta contribuisca alla parte principale
    if fruit_ratio and float(fruit_ratio) > 0.0:
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

    # vincoli di partecipazione minima sono ora gestiti tramite min_grams (protezione per alimento)
    CAP_RATIO = 0.90  # cap sulla somma dei minimi per stabilità (non usato)
    n = len(food_list)
    lb = np.zeros(n, dtype=float)
    # kcal per grammo
    kpg = np.array(kcal_per_g_list, dtype=float)
    is_main = np.array([orig in ("carboidrati","proteine","grassi") for orig in food_origin], dtype=bool)
    is_fruit = np.array([orig == "frutta" for orig in food_origin], dtype=bool)
    # min kcal per ciascun gruppo
    # senza vincoli percentuali, inizialmente lb a zero; min_grams verrà applicato successivamente
    with np.errstate(divide='ignore', invalid='ignore'):
        lb = np.zeros(n, dtype=float)

    # Vincolo minimo globale (protegge tutti gli alimenti dall'azzeramento)
    try:
        mg = float(min_grams)
    except Exception:
        mg = 0.0
    if mg > 0.0:
        lb = np.maximum(lb, np.full(n, mg, dtype=float))

    # Vincoli per-alimento (min/max grammi): sovrascrivono o restringono lb/ub per food specifici
    ub = np.full(n, np.inf, dtype=float)
    fc = food_constraints or {}
    for i, food in enumerate(food_list):
        entry = fc.get(food) or {}
        if "min" in entry:
            try:
                lb[i] = max(lb[i], float(entry["min"]))
            except Exception:
                pass
        if "max" in entry:
            try:
                max_val = float(entry["max"])
                if max_val >= lb[i]:  # evita bounds infeasibili
                    ub[i] = max_val
            except Exception:
                pass

    # Integrazione splits: costruisce trasformazione M e risolve in unico passaggio
    def _norm(s): return ' '.join(str(s).strip().lower().split())
    name_to_idx = {_norm(f): i for i, f in enumerate(food_list)}

    assigned_idx = set()
    M_cols = []
    lb_y_list = []
    ub_y_list = []

    for sp in (splits or []):
        if not isinstance(sp, dict): continue
        fa = _norm(sp.get('food_a', ''))
        fb = _norm(sp.get('food_b', ''))
        alpha = max(0.01, min(0.99, float(sp.get('alpha', 0.5))))
        i = name_to_idx.get(fa, -1)
        j = name_to_idx.get(fb, -1)
        if i < 0 or j < 0 or i == j or i in assigned_idx or j in assigned_idx:
            continue
        ki = kcal_per_g_list[i] if kcal_per_g_list[i] > 0 else 1e-9
        kj = kcal_per_g_list[j] if kcal_per_g_list[j] > 0 else 1e-9
        col = np.zeros(n, dtype=float)
        col[i] = alpha / ki
        col[j] = (1.0 - alpha) / kj
        M_cols.append(col)
        lb_y_list.append(max(0.0, lb[i] * ki / alpha, lb[j] * kj / (1.0 - alpha)))
        ub_i_y = (ub[i] * ki / alpha)           if ub[i] < np.inf else np.inf
        ub_j_y = (ub[j] * kj / (1.0 - alpha))  if ub[j] < np.inf else np.inf
        ub_y_list.append(min(ub_i_y, ub_j_y))
        assigned_idx.add(i); assigned_idx.add(j)

    for i in range(n):
        if i not in assigned_idx:
            col = np.zeros(n, dtype=float); col[i] = 1.0
            M_cols.append(col)
            lb_y_list.append(float(lb[i]))
            ub_y_list.append(float(ub[i]))

    if M_cols and assigned_idx:
        M = np.column_stack(M_cols)
        A_red = A.dot(M)
        res_y = lsq_linear(A_red, b, bounds=(np.array(lb_y_list), np.array(ub_y_list)))
        sol = M.dot(res_y.x)
        sol[sol < 0] = 0.0
    else:
        res = lsq_linear(A, b, bounds=(lb, ub))
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


# bilancia_conservando_macros rimosso: sostituito da vincoli min/max in calcola_dieta()


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