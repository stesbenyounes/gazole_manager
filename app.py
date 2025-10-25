from flask import Flask, render_template, request, redirect, url_for, Response, session, flash
from datetime import datetime, date, timedelta
from io import TextIOWrapper, StringIO
from pathlib import Path
from functools import wraps
import csv
import os

from sqlalchemy import text, func

# ORM centralisé
from extensions import db

# ================================================================================
# App & DB
# ================================================================================
app = Flask(__name__, instance_relative_config=True)

# Assure le dossier instance/
Path(app.instance_path).mkdir(parents=True, exist_ok=True)

# Config BDD (fichier dans instance/)
# Utilise PostgreSQL si disponible, sinon SQLite en local
DATABASE_URL = os.environ.get('DATABASE_URL', f"sqlite:///{Path(app.instance_path) / 'gazole.db'}")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

app.config.update(
    SQLALCHEMY_DATABASE_URI=DATABASE_URL,
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    TEMPLATES_AUTO_RELOAD=True,
    PROPAGATE_EXCEPTIONS=True,
    SECRET_KEY="CHANGE_THIS_RANDOM_KEY_987654321_VERY_SECRET",
)

# Initialise l'ORM
db.init_app(app)

# Identifiants (CHANGE LES MOTS DE PASSE!)
USERS = {
    'admin': 'zied123',      # Change ça!
    'employe': 'hela123'   # Change ça!
}

# Décorateur pour protéger les routes
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# ================================================================================
# Routes d'authentification
# ================================================================================
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        if username in USERS and USERS[username] == password:
            session['logged_in'] = True
            session['username'] = username
            return redirect(url_for('dashboard'))
        else:
            flash('Identifiants incorrects', 'error')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ================================================================================
# Helpers
# ================================================================================
def ensure_column_exists(table: str, column: str, column_sql: str):
    """Ajoute la colonne si absente (compatible SQLite et PostgreSQL)."""
    # Vérifie si on utilise PostgreSQL ou SQLite
    engine = db.engine
    if engine.dialect.name == 'postgresql':
        # PostgreSQL
        from sqlalchemy import inspect
        inspector = inspect(engine)
        columns = [col['name'] for col in inspector.get_columns(table)]
        if column not in columns:
            # PostgreSQL supporte ADD COLUMN IF NOT EXISTS depuis version 9.6
            db.session.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {column_sql}"))
            db.session.commit()
    else:
        # SQLite
        info = db.session.execute(text(f"PRAGMA table_info({table})")).fetchall()
        cols = {row[1] for row in info}
        if column not in cols:
            db.session.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {column_sql}"))
            db.session.commit()

def ensure_fuel_types():
    defaults = [("Gazole", 1.985), ("Gazole 50", 2.205), ("Essence", 2.525)]
    for name, price in defaults:
        if not FuelType.query.filter_by(name=name).first():
            db.session.add(FuelType(name=name, price=price))
    db.session.commit()

def per_entry_consumption(entries):
    """Calcule la consommation L/100km pour chaque entrée."""
    entries_sorted = sorted(entries, key=lambda x: (x.vehicle_id, x.date, x.id))
    consos = {}
    last_odo = {}
    for e in entries_sorted:
        vid = e.vehicle_id
        odo = e.odometer_km
        if vid not in last_odo:
            last_odo[vid] = odo
            continue
        prev = last_odo[vid]
        if odo is not None and prev is not None and e.liters and odo > prev:
            distance = odo - prev
            consos[e.id] = round((e.liters / distance) * 100.0, 2)
        last_odo[vid] = odo
    return consos

def month_series_for(query):
    """Retourne les séries mensuelles (labels, liters, cost)."""
    engine = db.engine
    
    # PostgreSQL: utiliser to_char
    if engine.dialect.name == 'postgresql':
        rows = (
            query.with_entities(
                func.to_char(FuelEntry.date, 'YYYY-MM').label('ym'),
                func.sum(FuelEntry.liters).label('liters'),
                func.sum(FuelEntry.total_cost).label('cost'),
            )
            .group_by('ym').order_by('ym')
            .all()
        )
    else:
        # SQLite: utiliser strftime
        rows = (
            query.with_entities(
                func.strftime('%Y-%m', FuelEntry.date).label('ym'),
                func.sum(FuelEntry.liters).label('liters'),
                func.sum(FuelEntry.total_cost).label('cost'),
            )
            .group_by(func.strftime('%Y-%m', FuelEntry.date))
            .order_by(func.strftime('%Y-%m', FuelEntry.date))
            .all()
        )
    
    labels = [r.ym or "—" for r in rows]
    liters = [float(r.liters or 0) for r in rows]
    cost   = [float(r.cost   or 0) for r in rows]
    return labels, liters, cost

def stats_for_entries(entries, consos_map):
    """Calcule les stats globales pour une liste d'entrées."""
    total_liters = round(sum((e.liters or 0) for e in entries), 2)
    total_cost   = round(sum((e.total_cost or 0) for e in entries), 3)
    consos_list  = [consos_map.get(e.id) for e in entries if consos_map.get(e.id) is not None]
    avg_l_100    = round(sum(consos_list)/len(consos_list), 2) if consos_list else 0.0
    return total_liters, total_cost, avg_l_100

MOIS_FR = {
    "janvier":1, "fevrier":2, "février":2, "mars":3, "avril":4, "mai":5, "juin":6,
    "juillet":7, "aout":8, "août":8, "septembre":9, "octobre":10,
    "novembre":11, "decembre":12, "décembre":12
}

def parse_month_to_range(s: str):
    """Parse un mois en deux dates (start, end)."""
    if not s:
        return (None, None)
    s0 = s.strip().lower()
    try:
        if "-" in s0 and len(s0) == 7:
            y, m = s0.split("-", 1); y, m = int(y), int(m)
        elif "/" in s0:
            m, y = s0.split("/", 1); y, m = int(y), int(m)
        elif s0.isdigit():
            y, m = date.today().year, int(s0)
        else:
            y, m = date.today().year, MOIS_FR.get(s0)
            if not m: return (None, None)
        start = date(y, m, 1)
    except Exception:
        return (None, None)
    end = date(y+1,1,1) if m == 12 else date(y, m+1, 1)
    return (start, end)

# ================================================================================
# Import des modèles
# ================================================================================
with app.app_context():
    from models import Vehicle, Driver, FuelEntry, FuelType
    db.create_all()
    ensure_column_exists('fuel_entries', 'fuel_type_id', 'INTEGER')
    ensure_fuel_types()

# ================================================================================
# Routes protégées (TOUTES avec @login_required)
# ================================================================================
@app.route('/')
@login_required
def dashboard():
    """
    Dashboard principal avec:
    - Cartes de statistiques globales
    - Top 5 chauffeurs du mois en cours
    - Dernières entrées
    """
    from models import FuelEntry, Vehicle, Driver, FuelType

    # Statistiques globales
    total_vehicles = Vehicle.query.count()
    total_drivers  = Driver.query.count()
    total_entries  = FuelEntry.query.count()

    total_cost   = db.session.query(func.sum(FuelEntry.total_cost)).scalar() or 0.0
    total_liters = db.session.query(func.sum(FuelEntry.liters)).scalar() or 0.0
    totals = {"cost": round(total_cost, 2), "liters": round(total_liters, 2)}

    # Données mensuelles pour graphique
    engine = db.engine
    if engine.dialect.name == 'postgresql':
        monthly = (
            db.session.query(
                func.to_char(FuelEntry.date, 'YYYY-MM').label('ym'),
                func.sum(FuelEntry.liters).label('liters'),
                func.sum(FuelEntry.total_cost).label('cost'),
            )
            .group_by('ym')
            .order_by('ym')
            .all()
        )
    else:
        # SQLite
        monthly = (
            db.session.query(
                func.strftime('%Y-%m', FuelEntry.date).label('ym'),
                func.sum(FuelEntry.liters).label('liters'),
                func.sum(FuelEntry.total_cost).label('cost'),
            )
            .group_by(func.strftime('%Y-%m', FuelEntry.date))
            .order_by(func.strftime('%Y-%m', FuelEntry.date))
            .all()
        )
    labels_month = [row.ym or "—" for row in monthly]
    liters_month = [float(row.liters or 0) for row in monthly]
    cost_month   = [float(row.cost   or 0) for row in monthly]

    # Consommation par type de carburant
    by_fuel = (
        db.session.query(
            (FuelType.name).label('name'),
            func.sum(FuelEntry.liters).label('liters')
        )
        .outerjoin(FuelType, FuelEntry.fuel_type_id == FuelType.id)
        .group_by(FuelType.id)
        .all()
    )
    fuel_labels = [row.name or "Inconnu" for row in by_fuel]
    fuel_liters = [float(row.liters or 0) for row in by_fuel]

    # Moyennes globales
    all_entries = FuelEntry.query.all()
    consos = per_entry_consumption(all_entries)
    consos_list = [consos.get(e.id) for e in all_entries if consos.get(e.id) is not None]
    avg_l_per_100 = round(sum(consos_list) / len(consos_list), 2) if consos_list else 0.0

    # Dernières 10 entrées
    last_entries = FuelEntry.query.order_by(FuelEntry.date.desc()).limit(10).all()

    # ========================================================================
    # TOP 5 CHAUFFEURS DU MOIS EN COURS
    # ========================================================================
    current_month_start = date(date.today().year, date.today().month, 1)
    current_month_entries = FuelEntry.query.filter(FuelEntry.date >= current_month_start).all()

    driver_stats = {}
    entries_by_driver = {}

    # Grouper les entrées par chauffeur
    for e in sorted(current_month_entries, key=lambda x: (x.driver_id, x.date)):
        did = e.driver_id
        if did not in entries_by_driver:
            entries_by_driver[did] = []
        entries_by_driver[did].append(e)

    # Calculer les KM et consommation par chauffeur
    for did, entries in entries_by_driver.items():
        total_km = 0
        total_cost = 0
        consos_list = []
        
        for i in range(1, len(entries)):
            prev_odo = entries[i-1].odometer_km
            curr_odo = entries[i].odometer_km
            if prev_odo and curr_odo and curr_odo > prev_odo:
                distance = curr_odo - prev_odo
                total_km += distance
                if entries[i].liters:
                    conso = (entries[i].liters / distance) * 100
                    consos_list.append(conso)
            total_cost += entries[i].total_cost or 0
        
        avg_conso = sum(consos_list) / len(consos_list) if consos_list else 0
        
        # FIX SQLALCHEMY: Remplacer Driver.query.get() par db.session.get()
        driver = db.session.get(Driver, did)
        
        if driver:
            driver_stats[did] = {
                'name': driver.name,
                'km': total_km,
                'consumption': round(avg_conso, 2),
                'cost': round(total_cost, 2)
            }

    # Trier et obtenir les Top 5
    top_drivers = sorted(driver_stats.items(), key=lambda x: x[1]['km'], reverse=True)[:5]
    top_drivers = [(i+1, data) for i, (_, data) in enumerate(top_drivers)]

    return render_template(
        'dashboard.html',
        total_vehicles=total_vehicles,
        total_drivers=total_drivers,
        total_entries=total_entries,
        totals=totals,
        labels_month=labels_month,
        liters_month=liters_month,
        cost_month=cost_month,
        fuel_labels=fuel_labels,
        fuel_liters=fuel_liters,
        last_entries=last_entries,
        avg_l_per_100=avg_l_per_100,
        top_drivers=top_drivers,
    )

@app.route('/entries')
@login_required
def entries_list():
    """Liste des entrées de carburant avec filtres."""
    from models import Vehicle, Driver, FuelEntry
    
    q_vehicle = request.args.get('vehicle', type=int)
    q_driver  = request.args.get('driver',  type=int)
    q_month   = request.args.get('month', type=str)

    query = FuelEntry.query
    if q_vehicle:
        query = query.filter(FuelEntry.vehicle_id == q_vehicle)
    if q_driver:
        query = query.filter(FuelEntry.driver_id == q_driver)
    if q_month:
        start, end = parse_month_to_range(q_month)
        if start and end:
            query = query.filter(FuelEntry.date >= start, FuelEntry.date < end)

    entries = query.order_by(FuelEntry.date.desc()).all()
    vehicles = Vehicle.query.all()
    drivers = Driver.query.all()
    consos = per_entry_consumption(query.order_by(FuelEntry.date.asc()).all())

    return render_template(
        'entries_list.html',
        entries=entries,
        vehicles=vehicles,
        drivers=drivers,
        q_vehicle=q_vehicle,
        q_driver=q_driver,
        q_month=q_month,
        consos=consos
    )

@app.route('/entries/add', methods=['GET', 'POST'])
@login_required
def entry_add():
    """Ajouter une nouvelle entrée de carburant."""
    from models import Vehicle, Driver, FuelEntry, FuelType
    
    if request.method == 'POST':
        date_str = request.form.get('date')
        vehicle_id = request.form.get('vehicle_id', type=int)
        driver_id = request.form.get('driver_id', type=int)
        odo = request.form.get('odometer_km', type=float)
        liters = request.form.get('liters', type=float)
        price = request.form.get('price_unit', type=float)
        fuel_type_id = request.form.get('fuel_type_id', type=int)
        station = request.form.get('station', '').strip()
        notes = request.form.get('notes', '').strip()

        try:
            dt = datetime.strptime(date_str, '%Y-%m-%d').date() if date_str else date.today()
            e = FuelEntry(
                date=dt, vehicle_id=vehicle_id, driver_id=driver_id,
                odometer_km=odo, liters=liters, price_unit=price,
                fuel_type_id=fuel_type_id, station=station, notes=notes
            )
            e.compute_total()
            db.session.add(e)
            db.session.commit()
            flash("Entrée ajoutée!", "success")
            return redirect(url_for('entries_list'))
        except Exception as ex:
            db.session.rollback()
            flash(f"Erreur: {str(ex)}", "danger")

    vehicles = Vehicle.query.all()
    drivers = Driver.query.all()
    fuel_types = FuelType.query.all()
    return render_template('entry_form.html', vehicles=vehicles, drivers=drivers, fuel_types=fuel_types)

@app.route('/entries/<int:eid>/edit', methods=['GET', 'POST'])
@login_required
def entry_edit(eid):
    """Modifier une entrée."""
    from models import FuelEntry, Vehicle, Driver, FuelType
    
    e = db.session.get(FuelEntry, eid)
    if not e:
        flash("Entrée non trouvée", "danger")
        return redirect(url_for('entries_list'))

    if request.method == 'POST':
        try:
            e.date = datetime.strptime(request.form.get('date'), '%Y-%m-%d').date()
            e.vehicle_id = request.form.get('vehicle_id', type=int)
            e.driver_id = request.form.get('driver_id', type=int)
            e.odometer_km = request.form.get('odometer_km', type=float)
            e.liters = request.form.get('liters', type=float)
            e.price_unit = request.form.get('price_unit', type=float)
            e.fuel_type_id = request.form.get('fuel_type_id', type=int)
            e.station = request.form.get('station', '').strip()
            e.notes = request.form.get('notes', '').strip()
            e.compute_total()
            db.session.commit()
            flash("Entrée modifiée!", "success")
            return redirect(url_for('entries_list'))
        except Exception as ex:
            db.session.rollback()
            flash(f"Erreur: {str(ex)}", "danger")

    vehicles = Vehicle.query.all()
    drivers = Driver.query.all()
    fuel_types = FuelType.query.all()
    return render_template('entry_form.html', entry=e, vehicles=vehicles, drivers=drivers, fuel_types=fuel_types)

@app.route('/entries/<int:eid>/delete', methods=['POST'])
@login_required
def entry_delete(eid):
    """Supprimer une entrée."""
    from models import FuelEntry
    
    e = db.session.get(FuelEntry, eid)
    if e:
        db.session.delete(e)
        db.session.commit()
        flash("Entrée supprimée!", "success")
    return redirect(url_for('entries_list'))

# Routes Véhicules
@app.route('/vehicles')
@login_required
def vehicles_list():
    """Liste des véhicules."""
    from models import Vehicle
    vehicles = Vehicle.query.all()
    return render_template('vehicles_list.html', vehicles=vehicles)

@app.route('/vehicles/add', methods=['GET', 'POST'])
@login_required
def vehicle_add():
    """Ajouter un véhicule."""
    from models import Vehicle
    
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if name:
            v = Vehicle(name=name)
            db.session.add(v)
            db.session.commit()
            flash("Véhicule ajouté!", "success")
            return redirect(url_for('vehicles_list'))
        else:
            flash("Le nom est requis", "danger")

    return render_template('vehicle_form.html')

@app.route('/vehicles/<int:vid>/edit', methods=['GET', 'POST'])
@login_required
def vehicle_edit(vid):
    """Modifier un véhicule."""
    from models import Vehicle
    
    v = db.session.get(Vehicle, vid)
    if not v:
        flash("Véhicule non trouvé", "danger")
        return redirect(url_for('vehicles_list'))

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if name:
            v.name = name
            db.session.commit()
            flash("Véhicule modifié!", "success")
            return redirect(url_for('vehicles_list'))
        else:
            flash("Le nom est requis", "danger")

    return render_template('vehicle_form.html', vehicle=v)

@app.route('/vehicles/<int:vid>/delete', methods=['POST'])
@login_required
def vehicle_delete(vid):
    """Supprimer un véhicule."""
    from models import Vehicle
    
    v = db.session.get(Vehicle, vid)
    if v:
        db.session.delete(v)
        db.session.commit()
        flash("Véhicule supprimé!", "success")
    return redirect(url_for('vehicles_list'))

@app.route('/vehicles/<int:vid>')
@login_required
def vehicle_detail(vid):
    """Détails d'un véhicule."""
    from models import Vehicle, FuelEntry
    
    v = db.session.get(Vehicle, vid)
    if not v:
        flash("Véhicule non trouvé", "danger")
        return redirect(url_for('vehicles_list'))

    entries = FuelEntry.query.filter_by(vehicle_id=vid).order_by(FuelEntry.date.desc()).all()
    consos = per_entry_consumption(FuelEntry.query.filter_by(vehicle_id=vid).order_by(FuelEntry.date.asc()).all())
    total_liters, total_cost, avg_l_100 = stats_for_entries(entries, consos)

    return render_template(
        'vehicle_detail.html',
        vehicle=v,
        entries=entries,
        consos=consos,
        total_liters=total_liters,
        total_cost=total_cost,
        avg_l_100=avg_l_100
    )

# Routes Chauffeurs
@app.route('/drivers')
@login_required
def drivers_list():
    """Liste des chauffeurs."""
    from models import Driver
    drivers = Driver.query.all()
    return render_template('drivers_list.html', drivers=drivers)

@app.route('/drivers/add', methods=['GET', 'POST'])
@login_required
def driver_add():
    """Ajouter un chauffeur."""
    from models import Driver
    
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if name:
            d = Driver(name=name)
            db.session.add(d)
            db.session.commit()
            flash("Chauffeur ajouté!", "success")
            return redirect(url_for('drivers_list'))
        else:
            flash("Le nom est requis", "danger")

    return render_template('driver_form.html')

@app.route('/drivers/<int:did>/edit', methods=['GET', 'POST'])
@login_required
def driver_edit(did):
    """Modifier un chauffeur."""
    from models import Driver
    
    d = db.session.get(Driver, did)
    if not d:
        flash("Chauffeur non trouvé", "danger")
        return redirect(url_for('drivers_list'))

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if name:
            d.name = name
            db.session.commit()
            flash("Chauffeur modifié!", "success")
            return redirect(url_for('drivers_list'))
        else:
            flash("Le nom est requis", "danger")

    return render_template('driver_form.html', driver=d)

@app.route('/drivers/<int:did>/delete', methods=['POST'])
@login_required
def driver_delete(did):
    """Supprimer un chauffeur."""
    from models import Driver
    
    d = db.session.get(Driver, did)
    if d:
        db.session.delete(d)
        db.session.commit()
        flash("Chauffeur supprimé!", "success")
    return redirect(url_for('drivers_list'))

@app.route('/drivers/<int:did>')
@login_required
def driver_detail(did):
    """Détails d'un chauffeur."""
    from models import Driver, FuelEntry
    
    d = db.session.get(Driver, did)
    if not d:
        flash("Chauffeur non trouvé", "danger")
        return redirect(url_for('drivers_list'))

    entries = FuelEntry.query.filter_by(driver_id=did).order_by(FuelEntry.date.desc()).all()
    consos = per_entry_consumption(FuelEntry.query.filter_by(driver_id=did).order_by(FuelEntry.date.asc()).all())
    total_liters, total_cost, avg_l_100 = stats_for_entries(entries, consos)

    return render_template(
        'driver_detail.html',
        driver=d,
        entries=entries,
        consos=consos,
        total_liters=total_liters,
        total_cost=total_cost,
        avg_l_100=avg_l_100
    )

@app.route('/driver-reports')
@login_required
def driver_reports():
    """Rapports par chauffeur."""
    from models import Driver, FuelEntry
    
    driver_id = request.args.get('driver_id', type=int)
    date_start = request.args.get('date_start', type=str)
    date_end = request.args.get('date_end', type=str)
    export = request.args.get('export', type=int)
    
    query = FuelEntry.query
    
    if driver_id:
        query = query.filter(FuelEntry.driver_id == driver_id)
    
    if date_start:
        try:
            start_date = datetime.strptime(date_start, '%Y-%m-%d').date()
            query = query.filter(FuelEntry.date >= start_date)
        except ValueError:
            pass
    
    if date_end:
        try:
            end_date = datetime.strptime(date_end, '%Y-%m-%d').date()
            query = query.filter(FuelEntry.date <= end_date)
        except ValueError:
            pass
    
    report_data = query.order_by(FuelEntry.date.desc()).all()
    
    # Calculs
    total_liters = round(sum((e.liters or 0) for e in report_data), 2)
    total_cost = round(sum((e.total_cost or 0) for e in report_data), 3)
    
    consos = per_entry_consumption(query.order_by(FuelEntry.date.asc()).all())
    consos_list = [consos.get(e.id) for e in report_data if consos.get(e.id) is not None]
    avg_consumption = round(sum(consos_list) / len(consos_list), 2) if consos_list else 0.0
    
    # Export CSV
    if export == 1:
        si = StringIO()
        w = csv.writer(si)
        w.writerow(['chauffeur', 'date', 'vehicule', 'odometre_km', 'litres', 'cout_total', 'consommation_l100km', 'station'])
        for e in report_data:
            w.writerow([
                e.driver.name if e.driver else '',
                e.date.isoformat() if e.date else '',
                e.vehicle.name if e.vehicle else '',
                e.odometer_km or '',
                e.liters or '',
                e.total_cost or '',
                consos.get(e.id, ''),
                e.station or '',
            ])
        return Response(si.getvalue(), mimetype='text/csv',
                       headers={'Content-Disposition': 'attachment; filename=rapport_chauffeurs.csv'})
    
    drivers = Driver.query.order_by(Driver.name.asc()).all()
    
    return render_template(
        'driver_reports.html',
        drivers=drivers,
        driver_id=driver_id,
        date_start=date_start,
        date_end=date_end,
        report_data=report_data,
        total_liters=total_liters,
        total_cost=total_cost,
        avg_consumption=avg_consumption,
        consos=consos
    )

@app.route('/vehicle-reports')
@login_required
def vehicle_reports():
    """Rapports par véhicule."""
    from models import Vehicle, FuelEntry
    
    vehicle_id = request.args.get('vehicle_id', type=int)
    date_start = request.args.get('date_start', type=str)
    date_end = request.args.get('date_end', type=str)
    export = request.args.get('export', type=int)
    
    query = FuelEntry.query
    
    if vehicle_id:
        query = query.filter(FuelEntry.vehicle_id == vehicle_id)
    
    if date_start:
        try:
            start_date = datetime.strptime(date_start, '%Y-%m-%d').date()
            query = query.filter(FuelEntry.date >= start_date)
        except ValueError:
            pass
    
    if date_end:
        try:
            end_date = datetime.strptime(date_end, '%Y-%m-%d').date()
            query = query.filter(FuelEntry.date <= end_date)
        except ValueError:
            pass
    
    report_data = query.order_by(FuelEntry.date.desc()).all()
    
    # Calculs des totaux
    total_liters = round(sum((e.liters or 0) for e in report_data), 2)
    total_cost = round(sum((e.total_cost or 0) for e in report_data), 3)
    
    # Calcul des distances par véhicule
    distances = {}
    total_km = 0
    entries_by_vehicle = {}
    
    for e in sorted(report_data, key=lambda x: (x.vehicle_id, x.date)):
        vid = e.vehicle_id
        if vid not in entries_by_vehicle:
            entries_by_vehicle[vid] = []
        entries_by_vehicle[vid].append(e)
    
    for vid, entries in entries_by_vehicle.items():
        for i in range(1, len(entries)):
            prev_odo = entries[i-1].odometer_km
            curr_odo = entries[i].odometer_km
            if prev_odo and curr_odo and curr_odo > prev_odo:
                distance = curr_odo - prev_odo
                distances[entries[i].id] = round(distance, 0)
                total_km += distance
    
    total_km = round(total_km, 0)
    
    # Calcul de la consommation
    consos = per_entry_consumption(query.order_by(FuelEntry.date.asc()).all())
    consos_list = [consos.get(e.id) for e in report_data if consos.get(e.id) is not None]
    avg_consumption = round(sum(consos_list) / len(consos_list), 2) if consos_list else 0.0
    
    # Export CSV
    if export == 1:
        si = StringIO()
        w = csv.writer(si)
        w.writerow(['vehicule', 'date', 'chauffeur', 'odometre_km', 'distance_km', 'litres', 'cout_total', 'consommation_l100km', 'station'])
        for e in report_data:
            w.writerow([
                e.vehicle.name if e.vehicle else '',
                e.date.isoformat() if e.date else '',
                e.driver.name if e.driver else '',
                e.odometer_km or '',
                distances.get(e.id, ''),
                e.liters or '',
                e.total_cost or '',
                consos.get(e.id, ''),
                e.station or '',
            ])
        return Response(si.getvalue(), mimetype='text/csv',
                       headers={'Content-Disposition': 'attachment; filename=rapport_vehicules.csv'})
    
    vehicles = Vehicle.query.order_by(Vehicle.name.asc()).all()
    
    return render_template(
        'vehicle_reports.html',
        vehicles=vehicles,
        vehicle_id=vehicle_id,
        date_start=date_start,
        date_end=date_end,
        report_data=report_data,
        total_liters=total_liters,
        total_cost=total_cost,
        total_km=total_km,
        avg_consumption=avg_consumption,
        consos=consos,
        distances=distances
    )

@app.route('/import-csv', methods=['GET', 'POST'])
@login_required
def import_csv():
    """Import de données depuis un fichier CSV."""
    from models import Vehicle, Driver, FuelEntry, FuelType
    if request.method == 'POST':
        f = request.files.get('file')
        if not f:
            flash("Aucun fichier sélectionné", "danger")
            return redirect(url_for('import_csv'))

        try:
            reader = csv.DictReader(TextIOWrapper(f.stream, encoding='utf-8'))
            imported = 0
            
            for row in reader:
                date_str = (row.get('date') or '').strip()
                if not date_str:
                    dt = datetime.today().date()
                else:
                    try:
                        dt = datetime.strptime(date_str, '%Y-%m-%d').date()
                    except ValueError:
                        continue  # Skip ligne invalide

                # Véhicule : crée s'il n'existe pas
                vehicle_name = (row.get('vehicle') or '').strip()
                if vehicle_name:
                    v = Vehicle.query.filter_by(name=vehicle_name).first()
                    if not v:
                        v = Vehicle(name=vehicle_name)
                        db.session.add(v)
                        db.session.flush()
                else:
                    v = None

                # Chauffeur : crée s'il n'existe pas
                driver_name = (row.get('driver') or '').strip()
                if driver_name:
                    d = Driver.query.filter_by(name=driver_name).first()
                    if not d:
                        d = Driver(name=driver_name)
                        db.session.add(d)
                        db.session.flush()
                else:
                    d = None

                odometer_km = float(row['odometer_km']) if (row.get('odometer_km') or '').strip() else None
                liters = float(row['liters']) if (row.get('liters') or '').strip() else None
                price_unit = float(row['price_unit']) if (row.get('price_unit') or '').strip() else None
                station = (row.get('station') or '').strip()
                notes = (row.get('notes') or '').strip()

                e = FuelEntry(
                    date=dt,
                    vehicle_id=v.id if v else None,
                    driver_id=d.id if d else None,
                    odometer_km=odometer_km,
                    liters=liters,
                    price_unit=price_unit,
                    station=station,
                    notes=notes,
                )
                e.compute_total()
                db.session.add(e)
                imported += 1

            db.session.commit()
            flash(f"{imported} entrées importées avec succès!", "success")
            return redirect(url_for('entries_list'))
            
        except Exception as e:
            db.session.rollback()
            flash(f"Erreur lors de l'import : {str(e)}", "danger")
            return redirect(url_for('import_csv'))

    return render_template('import_form.html')

@app.route('/export-csv')
@login_required
def export_csv():
    """Export de toutes les données en CSV."""
    from models import FuelEntry, FuelType
    headers = [
        'date','vehicle','driver','odometer_km','liters',
        'fuel_type','fuel_price','price_unit','total_cost','station','notes'
    ]
    si = StringIO()
    w = csv.writer(si)
    w.writerow(headers)

    q = FuelEntry.query.order_by(FuelEntry.date.asc(), FuelEntry.id.asc()).all()
    for e in q:
        w.writerow([
            e.date.isoformat() if e.date else '',
            (e.vehicle.name if e.vehicle else ''),
            (e.driver.name if e.driver else ''),
            (e.odometer_km if e.odometer_km is not None else ''),
            (e.liters if e.liters is not None else ''),
            (e.fuel_type.name if e.fuel_type else ''),
            (e.fuel_type.price if e.fuel_type else ''),
            (e.price_unit if e.price_unit is not None else ''),
            (e.total_cost if e.total_cost is not None else ''),
            (e.station or ''),
            (e.notes or ''),
        ])

    csv_data = si.getvalue()
    return Response(
        csv_data,
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=gazole_export.csv'}
    )

@app.route('/health')
def health():
    """Health check pour Render."""
    return "OK"

# ================================================================================
# Main
# ================================================================================
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port)