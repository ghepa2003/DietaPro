# DietaPro - Guida rapida (Windows)

Questa guida spiega, passo per passo, come avviare la pagina web in locale anche se sul PC non c'è ancora Python.

## Requisiti
- Windows 10/11
- File Excel con i dati degli alimenti nella cartella del progetto: `alimenti.xlsx` (o `alimenti.xlsm`) con 5 fogli: `carboidrati`, `proteine`, `grassi`, `frutta`, `verdura`.

## Passi per l'utente

1) Installare Python (se non presente)
- Apri il browser e vai su https://www.python.org/downloads/windows/
- Scarica la versione consigliata di Python 3.x per Windows.
- Avvia l'installer e spunta "Add python.exe to PATH" (Aggiungi Python al PATH), poi clicca Install Now.
- Al termine chiudi l'installer.

2) Aprire il Terminale nella cartella del progetto
- Premi Win+E e vai nella cartella del progetto: `C:\Users\...\DietaPro`.
- Clic nella barra dell'indirizzo, digita `powershell` e premi Invio (oppure tasto destro in uno spazio vuoto > "Apri in Terminale PowerShell").

3) Creare un ambiente virtuale (consigliato)
- Nel terminale PowerShell esegui:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Se vedi un messaggio di errore sulla Execution Policy, esegui una sola volta (come Amministratore):

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

poi riattiva:

```powershell
.\.venv\Scripts\Activate.ps1
```

4) Installare le dipendenze

```powershell
pip install -r requirements.txt
```

5) Verificare il file Excel degli alimenti
- Assicurati che in questa cartella ci sia `alimenti.xlsx` (o `alimenti.xlsm`).
- Deve contenere i fogli: `carboidrati`, `proteine`, `grassi`, `frutta`, `verdura` con almeno le colonne: `nome`, `calorie`, `carboidrati`, `proteine`, `grassi`.

6) Avviare il server web

```powershell
python app.py
```

Dovresti vedere qualcosa come: `Running on http://127.0.0.1:5000/`.

7) Aprire la pagina
- Apri il browser e vai su: http://127.0.0.1:5000/

8) Arrestare il server
- Nel terminale premi Ctrl+C.

## Problemi comuni
- Errore: "openpyxl not found" → non è stata installata la dipendenza: riesegui `pip install -r requirements.txt`.
- Errore: "File 'alimenti.xlsx' non trovato" → copia il file Excel nella cartella del progetto o rinominalo esattamente come indicato.
- Porta occupata (5000): avvia con un'altra porta, ad es. `set FLASK_RUN_PORT=5001; python app.py`.

## Avvio rapido con script (opzionale)
Puoi usare lo script `run.bat` al posto dei passaggi manuali 3), 4) e 6):
- 3) Creare/attivare l'ambiente virtuale
- 4) Installare le dipendenze
- 6) Avviare il server web

In pratica, dopo aver soddisfatto i punti 1) (installare Python), 2) (aprire la cartella del progetto) e 5) (verificare il file Excel), ti basta fare doppio clic su `run.bat` e poi aprire il browser su http://127.0.0.1:5000/.
