from collections import defaultdict
from datetime import datetime

def month_key(d):
    return d.strftime('%Y-%m')

def summarize_entries(entries):
    """Totaux + agrégats par mois et par véhicule."""
    totals = {'liters': 0.0, 'cost': 0.0}
    per_month = defaultdict(lambda: {'liters': 0.0, 'cost': 0.0})
    per_vehicle = defaultdict(lambda: {'liters': 0.0, 'cost': 0.0})

    for e in entries:
        liters = e.liters or 0.0
        cost = e.total_cost or 0.0
        totals['liters'] += liters
        totals['cost'] += cost

        mk = month_key(e.date)
        per_month[mk]['liters'] += liters
        per_month[mk]['cost'] += cost

        vname = e.vehicle.name if getattr(e, "vehicle", None) else '—'
        per_vehicle[vname]['liters'] += liters
        per_vehicle[vname]['cost'] += cost

    return totals, dict(per_month), dict(per_vehicle)

def estimate_consumption_l_per_100km(entries_for_vehicle):
    """
    Moyenne simple des L/100 km pour un véhicule :
    on calcule L/100 entre pleins consécutifs (delta km > 0), puis moyenne.
    """
    entries_sorted = sorted(entries_for_vehicle, key=lambda x: (x.date, x.id))
    samples = []
    prev = None
    for e in entries_sorted:
        if prev and e.odometer_km is not None and prev.odometer_km is not None:
            delta_km = e.odometer_km - prev.odometer_km
            if delta_km > 0 and e.liters:
                samples.append((e.liters / delta_km) * 100.0)
        prev = e
    return round(sum(samples) / len(samples), 2) if samples else None

def per_entry_consumption(entries):
    """
    Conso par plein : L/100 = litres du plein courant / (km depuis plein précédent du même véhicule) * 100
    Renvoie un dict {entry.id: valeur_L_100}.
    """
    # Tri par véhicule -> date -> id pour reconstruire l'ordre réel des pleins
    entries_sorted = sorted(entries, key=lambda x: (x.vehicle_id, x.date, x.id))
    consos = {}
    last_odo_by_vehicle = {}

    for e in entries_sorted:
        vid = e.vehicle_id
        odo = e.odometer_km
        if vid not in last_odo_by_vehicle:
            # premier plein pour ce véhicule : pas de conso calculable
            last_odo_by_vehicle[vid] = odo
            continue

        prev = last_odo_by_vehicle[vid]
        if odo is not None and prev is not None and e.liters and odo > prev:
            distance = odo - prev
            consos[e.id] = round((e.liters / distance) * 100.0, 2)

        # mémoriser le dernier odomètre (même si None)
        last_odo_by_vehicle[vid] = odo

    return consos
