@echo off
setlocal

REM Avvio rapido DietaPro (Windows)

if not exist .venv (
  echo [1/3] Creo ambiente virtuale...
  py -3 -m venv .venv
)

call .venv\Scripts\activate.bat

echo [2/3] Installo dipendenze...
pip install --upgrade pip
pip install -r requirements.txt

if not exist alimenti.xlsx if not exist alimenti.xlsm (
  echo ATTENZIONE: manca il file Excel degli alimenti (alimenti.xlsx o alimenti.xlsm) nella cartella del progetto.
  echo Crea o copia il file con i fogli: carboidrati, proteine, grassi, frutta, verdura.
)

echo [3/3] Avvio server su http://127.0.0.1:5000
python app.py

endlocal
