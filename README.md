# Gazole Manager (Flask)

Une application Flask simple pour gérer la consommation de gazole : véhicules, chauffeurs, pleins, statistiques et export CSV.

## 🚀 Installation

```bash
# 1) Créez un virtualenv (fortement recommandé)
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 2) Installer les dépendances
pip install -r requirements.txt

# 3) Démarrer l'app
export FLASK_APP=app.py        # Windows PowerShell: $env:FLASK_APP="app.py"
flask run --debug
```

Par défaut, la base SQLite `gazole.db` se crée à la racine du projet.

## 📦 Structure

```
gazole_manager/
├── app.py
├── models.py
├── utils.py
├── requirements.txt
├── .env.example
├── templates/
│   ├── base.html
│   ├── dashboard.html
│   ├── entries_list.html
│   ├── entry_form.html
│   ├── vehicles_list.html
│   ├── vehicle_form.html
│   ├── drivers_list.html
│   ├── driver_form.html
│   └── import_form.html
└── static/
    └── styles.css
```

## 🔑 Identifiants / Config
- Secret key : définie via `.env` (copiez `.env.example` → `.env`).
- Base de données : `SQLALCHEMY_DATABASE_URI` dans `.env` (sqlite par défaut).

## 🧠 Fonctionnalités
- CRUD Véhicules & Chauffeurs
- Enregistrements de pleins (date, véhicule, chauffeur, km, litres, prix)
- Calcul automatique du coût (litres × prix)
- Statistiques : totaux, coût, moyenne (L/100km) estimée par véhicule
- Import CSV (colonnes : date,vehicle,driver,odometer_km,liters,price_unit,station,notes)
- Export CSV des enregistrements

## 📈 Graphiques
- Dashboard avec Chart.js (via CDN), récapitulatif par mois.

## 🧪 Données d'exemple
- Au premier lancement, si la base est vide, l'app ajoute 1 véhicule, 1 chauffeur, 2 pleins.

Bon usage !
