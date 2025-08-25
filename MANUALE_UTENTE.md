# DietaPro — Manuale Utente (Guida all’uso)

Questo manuale spiega come usare l’app DietaPro senza entrare nei dettagli tecnici. L’obiettivo è aiutarti a impostare i dati e leggere i risultati per costruire i tuoi pasti.

Se devi prima installare/avviare il programma, vedi il file `README.md` (sezione Avvio rapido con `run.bat`).

## 1) Apri la pagina
- Avvia il programma e apri il browser su: http://127.0.0.1:5000
- Vedrai una pagina con diverse sezioni: impostazioni giornaliere, scelta alimenti, opzioni, risultati.

## 2) Imposta i dati giornalieri
- Calorie giornaliere: il totale di kcal che vuoi raggiungere in una giornata.
- Distribuzione dei pasti: percentuale di calorie da dedicare a colazione, pranzo e cena. Puoi adattare i valori alle tue abitudini.
- Macronutrienti per pasto: per ogni pasto puoi indicare le percentuali di carboidrati, proteine e grassi.

Suggerimento: se non sai cosa scegliere, usa i valori proposti dalla pagina (default) e modifica in seguito.

## 3) Scegli gli alimenti per categoria
Per ogni pasto seleziona gli alimenti che vuoi utilizzare:
- Carboidrati (es. riso, pasta, pane)
- Proteine (es. pollo, tonno, uova)
- Grassi (es. olio, frutta secca)
- Frutta
- Verdura

Puoi selezionare anche più di un alimento nella stessa categoria. Più alternative dai al sistema, più facilmente troverà una combinazione equilibrata.

## 4) Frutta e Verdura (opzionale)
- Quota frutta: indica la percentuale di kcal del singolo pasto che vuoi dedicare alla frutta (es. 10%).
- Quota verdura: indica la percentuale di kcal del singolo pasto che vuoi destinare alla verdura (es. 10%).

Nota: in genere è sufficiente tenere valori modesti (5–15%).

## 5) Split fra alimenti (opzionale, utenti avanzati)
Gli “split” servono a fissare un rapporto tra due alimenti all’interno dello stesso pasto. Esempi di formati accettati (uno per riga):
- `riso,patate=60`  → circa 60% delle kcal della coppia al riso e 40% alle patate
- `pollo,tonno=0.5` → 50% e 50%
- `pasta,riso=70,30` → 70% pasta, 30% riso

Usali solo se vuoi controllare il bilanciamento tra due ingredienti specifici. Altrimenti lascia vuoto.

## 6) Calcola i risultati
Hai due modalità d’uso:
- Calcolo singolo pasto: scegli il pasto (colazione/pranzo/cena) e premi il pulsante per calcolare. La pagina mostrerà le quantità in grammi per ogni alimento e i totali del pasto.
- Calcolo dell’intera giornata: se presente, usa l’azione “Calcola giornata” per ottenere i tre pasti insieme e un riepilogo totale giornaliero.

Se qualcosa non torna (es. una quantità a 0 g), prova ad aggiungere più alimenti o a rilassare gli split.

## 7) Leggi e interpreta i risultati
- Per ogni alimento: grammi suggeriti, kcal, carboidrati, proteine, grassi.
- Totali pasto: somma di kcal e macronutrienti per il singolo pasto.
- Totale giornata (se calcoli tutti e tre i pasti): somma complessiva.

Suggerimento: arrotonda le quantità in modo pratico per la cucina (es. 85 g → 85–90 g). Piccole differenze non sono critiche.

## 8) Aggiungi un alimento (sezione dedicata)
Se nella pagina è presente la sezione “Aggiungi alimento”, puoi inserire un nuovo ingrediente nel database:
- Compila: Nome, Categoria (carboidrati/proteine/grassi/frutta/verdura), e i valori per 100 g (kcal, carboidrati, proteine, grassi).
- Conferma per salvare. L’alimento diventerà selezionabile nelle liste.

Nota: Inserisci valori realistici e completi per ottenere calcoli attendibili.

## 9) Cerca informazioni su un alimento
Se disponibile la sezione “Cerca alimento”, inserisci Nome e Categoria per visualizzare rapidamente i valori nutrizionali di riferimento (per 100 g).

## 10) Consigli d’uso
- Inizia semplice: seleziona pochi alimenti principali e quote moderate di frutta/verdura.
- Evita troppi vincoli (split) alla prima prova; aggiungili solo se hai esigenze precise.
- Se i risultati non compaiono o sembrano strani, verifica che l’alimento esista e che i valori siano completi.
- Ricorda che i calcoli sono uno strumento: adatta le quantità alla praticità in cucina.

## 11) Domande frequenti (FAQ)
- Non vedo alimenti nelle liste: controlla che il file Excel con i fogli `carboidrati`, `proteine`, `grassi`, `frutta`, `verdura` sia nella stessa cartella dell’app e compilato correttamente.
- Le quantità sono 0 g: prova ad aggiungere più alternative in quella categoria o rimuovi split stringenti.
- Non riesco ad avviare la pagina: vedi `README.md` per i passi di installazione e avvio.

---
Se hai bisogno di aiuto ulteriore, condividi schermate della pagina e degli alimenti selezionati: sarà più semplice capire cosa regolare.
