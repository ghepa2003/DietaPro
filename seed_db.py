from app import app
from models import db, Food
import pandas as pd
import os

def seed():
    with app.app_context():
        # Creiamo le tabelle (solo in ambiente locale SQLite o test)
        # Su Vercel di solito le migrazioni si fanno diversamente, ma per ora questo script serve a popolare.
        db.create_all()
        
        # Elimina vecchi alimenti per non avere duplicati
        try:
            db.session.query(Food).delete()
            db.session.commit()
            print("Vecchi alimenti eliminati dal DB.")
        except Exception as e:
            db.session.rollback()
            print(f"Errore cancellazione: {e}")

        excel_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'alimenti.xlsx')
        if not os.path.exists(excel_path):
            print(f"File {excel_path} non trovato!")
            return

        excel = pd.ExcelFile(excel_path)
        for sheet in excel.sheet_names:
            df = pd.read_excel(excel_path, sheet_name=sheet)
            
            # Normalizziamo le colonne (alcuni fogli potrebbero avere intestazioni diverse, ma assumiamo standard)
            # Standard: ['nome', 'calorie', 'carboidrati', 'proteine', 'grassi']
            for _, row in df.iterrows():
                if pd.isna(row.get("nome")): continue
                nome = str(row.get("nome", "")).strip()
                if not nome or nome.lower() == "nan": continue
                
                c = sheet.lower().strip()
                if c not in ["carboidrati", "proteine", "grassi", "frutta", "verdura"]:
                    c = "carboidrati"
                
                try:
                    cal = float(row.get("calorie", 0))
                    carb = float(row.get("carboidrati", 0))
                    prot = float(row.get("proteine", 0))
                    fat = float(row.get("grassi", 0))
                    
                    if pd.isna(cal): cal = 0.0
                    if pd.isna(carb): carb = 0.0
                    if pd.isna(prot): prot = 0.0
                    if pd.isna(fat): fat = 0.0
                    
                    food = Food(
                        nome=nome,
                        categoria=c,
                        calorie=cal,
                        carboidrati=carb,
                        proteine=prot,
                        grassi=fat
                    )
                    db.session.add(food)
                except Exception as e:
                    print(f"Errore su {sheet} riga {row}: {e}")

        try:
            db.session.commit()
            print("Semina completata con successo! Tutti gli alimenti sono ora nel Database.")
        except Exception as e:
            db.session.rollback()
            print(f"Errore durante il commit: {e}")

if __name__ == '__main__':
    seed()
