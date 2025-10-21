import csv
from datetime import datetime
from app import create_app, db
from models import RefuelLog

app = create_app()

def to_float(v):
    if not v: 
        return None
    return float(str(v).replace(',', '.'))

def to_int(v):
    if not v: 
        return None
    return int(float(v))

with app.app_context():
    with open("static/data/consommation.csv", newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        count = 0
        for row in reader:
            log = RefuelLog(
                date=datetime.strptime(row['Date'], "%d/%m/%Y").date(),
                truck_plate=row['Camion (Matricule)'],
                driver=row['Chauffeur'],
                km_start=to_int(row.get('Km départ')),
                km_end=to_int(row.get('Km arrivée')),
                distance=to_float(row.get('Distance (km)')),
                liters=to_float(row.get('Litres consommés')),
                fuel_type=row.get('Type carburant', 'Gazole'),
                amount_paid=to_float(row.get('Montant pay')),
                consumption=to_float(row.get('Consommation (L/100)'))
            )
            db.session.add(log)
            count += 1
        db.session.commit()
        print(f"✅ {count} lignes importées.")
