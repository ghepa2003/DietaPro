# DietaPro — Funzionamento del programma

Questo documento spiega come funziona l’applicazione (logica di calcolo, struttura dati e API) così da capire cosa succede dietro l’interfaccia web.

## Panoramica
- Web app in Flask (`app.py`) con una pagina (`templates/index.html`).
- Database alimenti in un file Excel: `alimenti.xlsx` oppure `alimenti.xlsm`.
- Il cuore dei calcoli è in `DietaProg_functions.py`.

## Dati di input (Excel)
- Un unico file Excel con 5 fogli: `carboidrati`, `proteine`, `grassi`, `frutta`, `verdura`.
- Ogni foglio deve avere almeno queste colonne (valori per 100 g):
  - `nome` (stringa), `calorie`, `carboidrati`, `proteine`, `grassi` (numerici)
- L’app carica i fogli all’avvio in 5 dizionari in memoria (uno per categoria).

## Flusso generale
1. L’utente imposta calorie totali e percentuali macro per pasto (o usa i default).
2. Seleziona gli alimenti per categoria e, opzionalmente, quote di frutta/verdura (in percentuale di kcal del pasto) e “split” tra coppie di alimenti.
3. Clic su calcola: il browser chiama l’endpoint Flask che esegue i calcoli e restituisce grammi e totali.

## Calcolo del pasto (calcola_dieta)
Obiettivo: determinare i grammi di ciascun alimento selezionato rispettando i macronutrienti richiesti.

Passi chiave:
- Quote frutta/verdura:
  - `calorie_fruit = calorie_pasto * fruit_ratio`
  - `calorie_veg   = calorie_pasto * veg_ratio`
- Per i macronutrienti si considera il pasto esclusa la sola verdura:
  - `calorie_for_macros = calorie_pasto - calorie_veg`
- Target in grammi dei macro nella “parte principale” (inclusa la frutta):
  - carbo = `calorie_for_macros * perc_macro["carbo"] / 4`
  - prot  = `calorie_for_macros * perc_macro["prot"] / 4`
  - grassi= `calorie_for_macros * perc_macro["fat"] / 9`
- Si costruisce un sistema lineare A·x ≈ b con vincoli x ≥ 0:
  - colonne di A: per ogni alimento scelto (carbo/prot/grassi per grammo; i dati Excel sono per 100 g, quindi si divide per 100).
  - b: vettore dei target macro.
  - Se c’è frutta tra i “principali”, si aggiunge anche una riga che limita le kcal totali della frutta a `calorie_fruit`.
- Si risolve un least-squares vincolato (SciPy `lsq_linear`) per ottenere i grammi `x`.
- Verdura: non entra nel sistema dei macro; si ripartisce `calorie_veg` in modo uniforme tra le verdure selezionate e si convertono in grammi.

Risultato: lista alimenti principali e grammi; eventuali grammi dedicati a verdura; la frutta, se presente tra i principali, esce con i suoi grammi nella soluzione.

## Bilanciamento con split (bilancia_conservando_macros)
Consente di imporre rapporti di kcal tra coppie di alimenti senza alterare i macro globali.
- Input: una mappa del tipo `{"AlimentoA,AlimentoB": 0.6}` (60% delle kcal della coppia ad A, 40% a B) oppure una coppia `0.6,0.4`.
- Tecnica: si introduce una trasformazione `x = M·y` dove alcune colonne guidano le coppie con il rapporto desiderato in kcal, poi si risolve ancora un least-squares con i macro come vincolo (stesso b), ottenendo una nuova `x` che rispetta gli split e mantiene i macro.
- Output: nuova soluzione e diagnostica (scarti, delta dall’originale, split applicati).

## Calcolo calorie (calcola_calorie)
Dato `food_list` e i grammi `x`, calcola per ogni alimento kcal, carboidrati, proteine, grassi (scalando i valori per 100 g), e produce anche i totali. Usato per mostrare i dettagli all’utente e per il riepilogo giornaliero.

## API principali (Flask)
- `GET /` — serve la pagina HTML con le liste di alimenti e i default.
- `POST /compute_meal` — calcola un singolo pasto.
  - Input JSON (principali campi):
    - `meal`: "colazione" | "pranzo" | "cena"
    - `calorie_tot`, `perc_pasti` (quota del pasto), `macro_pasti[meal]`
    - `choices`: alimenti scelti per categoria
    - `fruit_ratio`, `veg_ratio`
    - `splits_text`: testo multilinea per specificare gli split (es. `pollo,tonno=60`)
  - Output JSON: per alimento (nome, grammi, kcal, macro) e totali.
- `POST /compute_day` — calcola i tre pasti e ritorna il riepilogo giornaliero (somma di kcal e macro).
- `POST /add_food` — aggiunge/aggiorna un alimento nel foglio Excel della categoria, aggiornando anche il DB in memoria.
- `GET /get_food` — restituisce i valori nutrizionali di un alimento per categoria (ricerca case-insensitive).

## Comportamenti e assunzioni importanti
- I valori nutrizionali sono per 100 g; internamente vengono scalati per ottenere per-grammo.
- Se un alimento non viene trovato nel suo DB, viene sollevato un errore chiaro.
- Calorie per grammo della frutta sono usate per imporre il vincolo di `calorie_fruit` se la frutta è tra i principali.
- La verdura è ripartita uniformemente tra le scelte (stessa quantità per ogni verdura selezionata).
- Piccoli numeri negativi possono comparire per errori numerici e vengono forzati a 0.

## Errori comuni e diagnosi
- "File 'alimenti.xlsx' non trovato": inserire l’Excel nella stessa cartella dell’app con i fogli corretti.
- Colonne non valide nel foglio: assicurarsi che `nome, calorie, carboidrati, proteine, grassi` esistano e siano numeriche (escluso `nome`).
- Alimenti duplicati: l’ultimo valore nel foglio prevale (in fase di load vengono rimossi duplicati mantenendo l’ultima riga).

## Estensioni possibili
- Pesi personalizzati per la ripartizione della verdura (non uniforme).
- Altri vincoli (min/max grammi per alimento).
- Salvataggio e caricamento dei piani pasto.
