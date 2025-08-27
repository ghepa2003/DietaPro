from DietaProg_functions import calcola_dieta, bilancia_conservando_macros, calcola_calorie

# Build minimal fake DBs
carbo_db = {
    'pasta': {'calorie': 350.0, 'carboidrati': 70.0, 'proteine': 12.0, 'grassi': 2.0},
}
prot_db = {
    'prosciutto crudo': {'calorie': 250.0, 'carboidrati': 0.5, 'proteine': 25.0, 'grassi': 15.0},
}
grassi_db = {
    'avocado': {'calorie': 160.0, 'carboidrati': 9.0, 'proteine': 2.0, 'grassi': 15.0},
}
frutta_db = {
    'pesca': {'calorie': 50.0, 'carboidrati': 10.0, 'proteine': 1.0, 'grassi': 0.2},
}
verdura_db = {}

choices = {
    'carboidrati': ['pasta'],
    'proteine': ['prosciutto crudo'],
    'grassi': ['avocado'],
    'frutta': ['pesca'],
    'verdura': []
}

cal_pasto = 600
macro = {'carbo': 0.5, 'prot': 0.3, 'fat': 0.2}

scelti, sol, qf, qv = calcola_dieta(cal_pasto, macro, choices, carbo_db, prot_db, grassi_db, frutta_db, verdura_db, fruit_ratio=0.1, veg_ratio=0.0)
print('Scelti:', scelti)
print('Sol original (g):', sol)

splits = {('avocado','prosciutto crudo'): 0.6}
new_sol, info = bilancia_conservando_macros(scelti, sol, splits, carbo_db, prot_db, grassi_db, frutta_db, verdura_db)
print('New sol (g):', new_sol)
per_food, totals = calcola_calorie(scelti, new_sol, carbo_db, prot_db, grassi_db, frutta_db, verdura_db)
print('Per food:', per_food)
print('Totals:', totals)
print('Info:', info)

# Triple-split test: pasta, prosciutto, avocado share given ratios
splits3 = {('pasta','avocado','prosciutto crudo'): (50,30,20)}
new_sol3, info3 = bilancia_conservando_macros(scelti, sol, splits3, carbo_db, prot_db, grassi_db, frutta_db, verdura_db)
print('\nTriple split new sol (g):', new_sol3)
per_food3, totals3 = calcola_calorie(scelti, new_sol3, carbo_db, prot_db, grassi_db, frutta_db, verdura_db)
print('Per food (triple):', per_food3)
print('Totals (triple):', totals3)
print('Info (triple):', info3)

# Reproduction case: pane, prosciutto, avocado, pesca; fruit_ratio=0 -> pesca should not supply carbs
carbo_db2 = {'pane': {'calorie': 270.0, 'carboidrati': 55.0, 'proteine': 9.0, 'grassi': 2.0}}
prot_db2 = {'prosciutto crudo': {'calorie': 250.0, 'carboidrati': 0.5, 'proteine': 25.0, 'grassi': 15.0}}
grassi_db2 = {'avocado': {'calorie': 160.0, 'carboidrati': 9.0, 'proteine': 2.0, 'grassi': 15.0}}
frutta_db2 = {'pesca': {'calorie': 50.0, 'carboidrati': 10.0, 'proteine': 1.0, 'grassi': 0.2}}
choices2 = {'carboidrati': ['pane'], 'proteine': ['prosciutto crudo'], 'grassi': ['avocado'], 'frutta': ['pesca'], 'verdura': []}
cal_pasto = 600
mac = {'carbo':0.5,'prot':0.3,'fat':0.2}
scelti2, sol2, qf2, qv2 = calcola_dieta(cal_pasto, mac, choices2, carbo_db2, prot_db2, grassi_db2, frutta_db2, verdura_db, fruit_ratio=0.0, veg_ratio=0.0)
print('\nScelti2:', scelti2)
print('Sol2 original (g):', sol2)
spl = {('prosciutto crudo','avocado'): 0.6}
new_sol2, info2 = bilancia_conservando_macros(scelti2, sol2, spl, carbo_db2, prot_db2, grassi_db2, frutta_db2, {})
print('New sol2 (g):', new_sol2)
pf2, tot2 = calcola_calorie(scelti2, new_sol2, carbo_db2, prot_db2, grassi_db2, frutta_db2, {})
print('Per food2:', pf2)
print('Totals2:', tot2)
print('Info2:', info2)

# Now test protection with min_grams to prevent pane being zeroed
scelti2b, sol2b, qf2b, qv2b = calcola_dieta(cal_pasto, mac, choices2, carbo_db2, prot_db2, grassi_db2, frutta_db2, {}, fruit_ratio=0.0, veg_ratio=0.0, min_grams=20.0)
print('\nWith min_grams protection, sol2b original (g):', sol2b)
new_sol2b, info2b = bilancia_conservando_macros(scelti2b, sol2b, spl, carbo_db2, prot_db2, grassi_db2, frutta_db2, {}, min_grams=20.0)
print('New sol2b (g):', new_sol2b)
pf2b, tot2b = calcola_calorie(scelti2b, new_sol2b, carbo_db2, prot_db2, grassi_db2, frutta_db2, {})
print('Per food2b:', pf2b)
print('Totals2b:', tot2b)
print('Info2b:', info2b)
