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

# -----------------------------------------------------------------------------
# App & DB
# -----------------------------------------------------------------------------
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

# -----------------------------------------------------------------------------
# Routes d'authentification
# -----------------------------------------------------------------------------
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

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
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
    rows = (
        query.with_entities(
            func.to_char(FuelEntry.date, 'YYYY-MM').label('ym'),
            func.sum(FuelEntry.liters).label('liters'),
            func.sum(FuelEntry.total_cost).label('cost'),
        )
        .group_by('ym').order_by('ym')
        .all()
    )
    labels = [r.ym or "—" for r in rows]
    liters = [float(r.liters or 0) for r in rows]
    cost   = [float(r.cost   or 0) for r in rows]
    return labels, liters, cost

def stats_for_entries(entries, consos_map):
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

# -----------------------------------------------------------------------------
# Import des modèles
# -----------------------------------------------------------------------------
with app.app_context():
    from models import Vehicle, Driver, FuelEntry, FuelType
    db.create_all()
    ensure_column_exists('fuel_entries', 'fuel_type_id', 'INTEGER')
    ensure_fuel_types()

# -----------------------------------------------------------------------------
# Routes protégées (TOUTES avec @login_required)
# -----------------------------------------------------------------------------
@app.route('/')
@login_required
def dashboard():
    from models import FuelEntry, Vehicle, Driver, FuelType

    total_vehicles = Vehicle.query.count()
    total_drivers  = Driver.query.count()
    total_entries  = FuelEntry.query.count()

    total_cost   = db.session.query(func.sum(FuelEntry.total_cost)).scalar() or 0.0
    total_liters = db.session.query(func.sum(FuelEntry.liters)).scalar() or 0.0
    totals = {"cost": round(total_cost, 2), "liters": round(total_liters, 2)}

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
    labels_month = [row.ym or "—" for row in monthly]
    liters_month = [float(row.liters or 0) for row in monthly]
    cost_month   = [float(row.cost   or 0) for row in monthly]

    by_fuel = (
        db.session.query(
            (FuelType.name).label('name'),
            func.sum(FuelEntry.liters).label('liters')
        )
        .outerjoin(FuelType, FuelEntry.fuel_type_id == FuelType.id)
        .group_by(FuelType.name)
        .order_by(FuelType.name)
        .all()
    )
    fuel_labels = [row.name or "—" for row in by_fuel]
    fuel_liters = [float(row.liters or 0) for row in by_fuel]

    last_entries = (
        FuelEntry.query
        .order_by(FuelEntry.date.desc(), FuelEntry.id.desc())
        .limit(5)
        .all()
    )

    consos = per_entry_consumption(FuelEntry.query.order_by(FuelEntry.date.asc(), FuelEntry.id.asc()).all())
    avg_l_per_100 = round(sum(consos.values())/len(consos), 2) if consos else 0.0

    period = request.args.get('period', '30')
    
    if period == 'all':
        start_date = None
        period_value = 'all'
    else:
        try:
            days = int(period)
            start_date = date.today() - timedelta(days=days)
            period_value = days
        except:
            start_date = date.today() - timedelta(days=30)
            period_value = 30

    query = db.session.query(
        Driver.id,
        Driver.name,
        func.sum(FuelEntry.total_cost).label('total_cost'),
        func.sum(FuelEntry.liters).label('total_liters'),
        func.count(FuelEntry.id).label('num_entries')
    ).join(FuelEntry, FuelEntry.driver_id == Driver.id)
    
    if start_date:
        query = query.filter(FuelEntry.date >= start_date)
    
    driver_stats = query.group_by(Driver.id, Driver.name).all()

    top_drivers = []
    for stat in driver_stats:
        driver_query = FuelEntry.query.filter(FuelEntry.driver_id == stat.id)
        if start_date:
            driver_query = driver_query.filter(FuelEntry.date >= start_date)
        
        driver_entries = driver_query.order_by(FuelEntry.date.asc()).all()
        
        total_km = 0
        entries_by_vehicle = {}
        for e in driver_entries:
            vid = e.vehicle_id
            if vid not in entries_by_vehicle:
                entries_by_vehicle[vid] = []
            entries_by_vehicle[vid].append(e)
        
        for vid, entries in entries_by_vehicle.items():
            sorted_entries = sorted(entries, key=lambda x: x.date)
            for i in range(1, len(sorted_entries)):
                prev_odo = sorted_entries[i-1].odometer_km
                curr_odo = sorted_entries[i].odometer_km
                if prev_odo and curr_odo and curr_odo > prev_odo:
                    total_km += curr_odo - prev_odo
        
        driver_consos = per_entry_consumption(driver_entries)
        consos_list = [driver_consos.get(e.id) for e in driver_entries if driver_consos.get(e.id) is not None]
        avg_conso = round(sum(consos_list) / len(consos_list), 2) if consos_list else 0.0
        
        if total_km > 0:
            top_drivers.append({
                'id': stat.id,
                'name': stat.name,
                'total_cost': round(stat.total_cost, 2),
                'total_liters': round(stat.total_liters, 2),
                'num_entries': stat.num_entries,
                'total_km': round(total_km, 0),
                'avg_consumption': avg_conso
            })
    
    top_drivers = sorted(top_drivers, key=lambda x: x['total_km'], reverse=True)[:5]

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
        period=period_value
    )

@app.route('/entries')
@login_required
def entries_list():
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

    entries = query.order_by(FuelEntry.date.desc(), FuelEntry.id.desc()).all()
    vehicles = Vehicle.query.order_by(Vehicle.name.asc()).all()
    drivers = Driver.query.order_by(Driver.name.asc()).all()
    consos = per_entry_consumption(entries)

    total_liters = round(sum((e.liters or 0) for e in entries), 2)
    total_cost   = round(sum((e.total_cost or 0) for e in entries), 3)
    consos_list = [consos.get(e.id) for e in entries if consos.get(e.id) is not None]
    avg_l_per_100_view = round(sum(consos_list)/len(consos_list), 2) if consos_list else 0.0

    if request.args.get('export') == '1':
        si = StringIO()
        w = csv.writer(si)
        w.writerow(['date','vehicle','driver','odometer_km','liters','fuel_type','price_unit','total_cost','station','notes'])
        for e in entries:
            w.writerow([
                e.date.isoformat() if e.date else '',
                e.vehicle.name if e.vehicle else '',
                e.driver.name if e.driver else '',
                e.odometer_km or '',
                e.liters or '',
                e.fuel_type.name if e.fuel_type else '',
                e.price_unit or '',
                e.total_cost or '',
                e.station or '',
                e.notes or '',
            ])
        return Response(si.getvalue(), mimetype='text/csv',
                        headers={'Content-Disposition':'attachment; filename=entries_export.csv'})

    return render_template(
        'entries_list.html',
        entries=entries,
        vehicles=vehicles,
        drivers=drivers,
        consos=consos,
        q_vehicle=q_vehicle,
        q_driver=q_driver,
        q_month=q_month,
        total_liters=total_liters,
        total_cost=total_cost,
        avg_l_per_100_view=avg_l_per_100_view,
    )

@app.route('/entry/new', methods=['GET', 'POST'])
@login_required
def entry_new():
    from models import Vehicle, Driver, FuelType, FuelEntry
    vehicles = Vehicle.query.order_by(Vehicle.name.asc()).all()
    drivers = Driver.query.order_by(Driver.name.asc()).all()
    fuel_types = FuelType.query.order_by(FuelType.name.asc()).all()

    ret_vehicle = request.args.get('vehicle')
    ret_driver  = request.args.get('driver')
    ret_month   = request.args.get('month')

    if request.method == 'POST':
        date_str = request.form.get('date')
        if not date_str:
            dt = datetime.today().date()
        else:
            try:
                dt = datetime.strptime(date_str, '%Y-%m-%d').date()
            except ValueError:
                dt = datetime.today().date()

        vehicle_id = int(request.form.get('vehicle_id')) if request.form.get('vehicle_id') else None
        driver_id  = int(request.form.get('driver_id')) if request.form.get('driver_id') else None
        odometer_km = float(request.form.get('odometer_km')) if request.form.get('odometer_km') else None
        liters      = float(request.form.get('liters')) if request.form.get('liters') else None
        price_unit  = request.form.get('price_unit')
        price_unit  = float(price_unit) if price_unit else None
        station = request.form.get('station') or ''
        notes   = request.form.get('notes') or ''
        fuel_type_id = request.form.get('fuel_type_id')
        fuel_type_id = int(fuel_type_id) if fuel_type_id else None

        ft = FuelType.query.get(fuel_type_id) if fuel_type_id else None
        if price_unit is None and ft:
            price_unit = ft.price

        entry = FuelEntry(
            date=dt,
            vehicle_id=vehicle_id,
            driver_id=driver_id,
            odometer_km=odometer_km,
            liters=liters,
            price_unit=price_unit,
            station=station,
            notes=notes,
            fuel_type_id=fuel_type_id
        )
        entry.compute_total()
        db.session.add(entry)
        db.session.commit()

        rv = request.form.get('ret_vehicle') or None
        rd = request.form.get('ret_driver') or None
        rm = request.form.get('ret_month') or None
        return redirect(url_for('entries_list', vehicle=rv, driver=rd, month=rm))

    return render_template(
        'entry_form.html',
        vehicles=vehicles,
        drivers=drivers,
        fuel_types=fuel_types,
        entry=None,
        ret_vehicle=ret_vehicle,
        ret_driver=ret_driver,
        ret_month=ret_month
    )

@app.route('/entry/delete/<int:eid>', methods=['POST'])
@login_required
def entry_delete(eid):
    from models import FuelEntry
    e = FuelEntry.query.get_or_404(eid)
    db.session.delete(e)
    db.session.commit()

    v = request.args.get('vehicle') or None
    d = request.args.get('driver') or None
    m = request.args.get('month') or None
    return redirect(url_for('entries_list', vehicle=v, driver=d, month=m))

@app.route('/vehicles')
@login_required
def vehicles_list():
    from models import Vehicle
    vehicles = Vehicle.query.order_by(Vehicle.name.asc()).all()
    return render_template('vehicles_list.html', vehicles=vehicles)

@app.route('/vehicle/<int:vid>')
@login_required
def vehicle_detail(vid):
    from models import Vehicle, FuelEntry
    v = Vehicle.query.get_or_404(vid)
    q = FuelEntry.query.filter(FuelEntry.vehicle_id == vid)
    entries = q.order_by(FuelEntry.date.desc(), FuelEntry.id.desc()).limit(50).all()
    consos = per_entry_consumption(q.order_by(FuelEntry.date.asc(), FuelEntry.id.asc()).all())
    total_liters, total_cost, avg_l_100 = stats_for_entries(entries, consos)
    labels, liters_month, cost_month = month_series_for(q)
    last_entries = q.order_by(FuelEntry.date.desc(), FuelEntry.id.desc()).limit(10).all()

    return render_template(
        'vehicle_detail.html',
        v=v,
        entries=entries,
        last_entries=last_entries,
        consos=consos,
        total_liters=total_liters,
        total_cost=total_cost,
        avg_l_100=avg_l_100,
        labels=labels,
        liters_month=liters_month,
        cost_month=cost_month,
    )

@app.route('/vehicle/new', methods=['GET', 'POST'])
@login_required
def vehicle_new():
    from models import Vehicle
    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        plate = (request.form.get('plate') or '').strip()
        plate = plate or None
        if name:
            db.session.add(Vehicle(name=name, plate=plate))
            db.session.commit()
        return redirect(url_for('vehicles_list'))
    return render_template('vehicle_form.html')

@app.route('/vehicle/edit/<int:vid>', methods=['GET', 'POST'])
@login_required
def vehicle_edit(vid):
    from models import Vehicle
    v = Vehicle.query.get_or_404(vid)
    if request.method == 'POST':
        v.name = (request.form.get('name') or '').strip()
        plate = (request.form.get('plate') or '').strip()
        v.plate = plate or None
        db.session.commit()
        return redirect(url_for('vehicles_list'))
    return render_template('vehicle_form.html', vehicle=v)

@app.route('/vehicle/delete/<int:vid>', methods=['POST'])
@login_required
def vehicle_delete(vid):
    from models import Vehicle
    v = Vehicle.query.get_or_404(vid)
    db.session.delete(v)
    db.session.commit()
    return redirect(url_for('vehicles_list'))

@app.route('/drivers')
@login_required
def drivers_list():
    from models import Driver
    drivers = Driver.query.order_by(Driver.name.asc()).all()
    return render_template('drivers_list.html', drivers=drivers)

@app.route('/driver/<int:did>')
@login_required
def driver_detail(did):
    from models import Driver, FuelEntry
    d = Driver.query.get_or_404(did)
    q = FuelEntry.query.filter(FuelEntry.driver_id == did)
    entries = q.order_by(FuelEntry.date.desc(), FuelEntry.id.desc()).limit(50).all()
    consos = per_entry_consumption(q.order_by(FuelEntry.date.asc(), FuelEntry.id.asc()).all())
    total_liters, total_cost, avg_l_100 = stats_for_entries(entries, consos)
    labels, liters_month, cost_month = month_series_for(q)
    last_entries = q.order_by(FuelEntry.date.desc(), FuelEntry.id.desc()).limit(10).all()

    return render_template(
        'driver_detail.html',
        d=d,
        entries=entries,
        last_entries=last_entries,
        consos=consos,
        total_liters=total_liters,
        total_cost=total_cost,
        avg_l_100=avg_l_100,
        labels=labels,
        liters_month=liters_month,
        cost_month=cost_month,
    )

@app.route('/driver/new', methods=['GET', 'POST'])
@login_required
def driver_new():
    from models import Driver
    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        if name:
            db.session.add(Driver(name=name))
            db.session.commit()
        return redirect(url_for('drivers_list'))
    return render_template('driver_form.html')

@app.route('/driver/edit/<int:did>', methods=['GET', 'POST'])
@login_required
def driver_edit(did):
    from models import Driver
    d = Driver.query.get_or_404(did)
    if request.method == 'POST':
        d.name = (request.form.get('name') or '').strip()
        db.session.commit()
        return redirect(url_for('drivers_list'))
    return render_template('driver_form.html', driver=d)

@app.route('/driver/delete/<int:did>', methods=['POST'])
@login_required
def driver_delete(did):
    from models import Driver
    d = Driver.query.get_or_404(did)
    db.session.delete(d)
    db.session.commit()
    return redirect(url_for('drivers_list'))

@app.route('/driver-reports')
@login_required
def driver_reports():
    from models import Driver, FuelEntry, Vehicle
    
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
    
    total_liters = round(sum((e.liters or 0) for e in report_data), 2)
    total_cost = round(sum((e.total_cost or 0) for e in report_data), 3)
    
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
    
    consos = per_entry_consumption(query.order_by(FuelEntry.date.asc()).all())
    consos_list = [consos.get(e.id) for e in report_data if consos.get(e.id) is not None]
    avg_consumption = round(sum(consos_list) / len(consos_list), 2) if consos_list else 0.0
    
    if export == 1:
        si = StringIO()
        w = csv.writer(si)
        w.writerow(['chauffeur', 'date', 'vehicule', 'odometre_km', 'distance_km', 'litres', 'cout_total', 'consommation_l100km', 'station'])
        for e in report_data:
            w.writerow([
                e.driver.name if e.driver else '',
                e.date.isoformat() if e.date else '',
                e.vehicle.name if e.vehicle else '',
                e.odometer_km or '',
                distances.get(e.id, ''),
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
        total_km=total_km,
        avg_consumption=avg_consumption,
        consos=consos,
        distances=distances
    )

@app.route('/import-csv', methods=['GET', 'POST'])
@login_required
def import_csv():
    from models import Vehicle, Driver, FuelEntry, FuelType
    if request.method == 'POST':
        f = request.files.get('file')
        if not f:
            return "Aucun fichier sélectionné", 400

        reader = csv.DictReader(TextIOWrapper(f.stream, encoding='utf-8'))
        for row in reader:
            date_str = (row.get('date') or '').strip()
            if not date_str:
                dt = datetime.today().date()
            else:
                try:
                    dt = datetime.strptime(date_str, '%Y-%m-%d').date()
                except ValueError:
                    dt = datetime.today().date()

            vehicle_name = (row.get('vehicle') or '').strip()
            driver_name  = (row.get('driver') or '').strip()
            v = Vehicle.query.filter_by(name=vehicle_name).first() if vehicle_name else None
            d = Driver.query.filter_by(name=driver_name).first() if driver_name else None

            odometer_km = float(row['odometer_km']) if (row.get('odometer_km') or '').strip() else None
            liters      = float(row['liters'])       if (row.get('liters')       or '').strip() else None
            price_unit  = float(row['price_unit'])   if (row.get('price_unit')   or '').strip() else None
            station     = (row.get('station') or '').strip()
            notes       = (row.get('notes') or '').strip()

            fuel_type_name = (row.get('fuel_type') or '').strip()
            ft = FuelType.query.filter_by(name=fuel_type_name).first() if fuel_type_name else None

            e = FuelEntry(
                date=dt,
                vehicle_id=v.id if v else None,
                driver_id=d.id if d else None,
                odometer_km=odometer_km,
                liters=liters,
                price_unit=price_unit,
                station=station,
                notes=notes,
                fuel_type_id=ft.id if ft else None
            )
            e.compute_total()
            db.session.add(e)

        db.session.commit()
        return redirect(url_for('entries_list'))

    return render_template('import_form.html')

@app.route('/export-csv')
@login_required
def export_csv():
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
    return "OK"

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)