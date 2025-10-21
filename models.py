from datetime import date
from extensions import db

class Vehicle(db.Model):
    __tablename__ = 'vehicle'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    plate = db.Column(db.String(40))

    def __repr__(self):
        return f"<Vehicle {self.id} {self.name}>"

class Driver(db.Model):
    __tablename__ = 'driver'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)

    def __repr__(self):
        return f"<Driver {self.id} {self.name}>"

class FuelType(db.Model):
    __tablename__ = 'fuel_types'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    price = db.Column(db.Float, nullable=False)

    def __repr__(self):
        return f"<FuelType {self.id} {self.name} {self.price}>"

class FuelEntry(db.Model):
    __tablename__ = 'fuel_entries'
    id = db.Column(db.Integer, primary_key=True)

    date = db.Column(db.Date, nullable=False)

    vehicle_id = db.Column(db.Integer, db.ForeignKey('vehicle.id'), nullable=False)
    driver_id  = db.Column(db.Integer, db.ForeignKey('driver.id'))

    odometer_km = db.Column(db.Float)
    liters      = db.Column(db.Float, nullable=False)
    price_unit  = db.Column(db.Float)    # prix/L saisi (facultatif)
    total_cost  = db.Column(db.Float)    # calculé

    station = db.Column(db.String(120))
    notes   = db.Column(db.Text)

    # Type de carburant (optionnel)
    fuel_type_id = db.Column(db.Integer, db.ForeignKey('fuel_types.id'))
    fuel_type    = db.relationship('FuelType')

    # Relations
    vehicle = db.relationship('Vehicle', backref=db.backref('fuel_entries', lazy='dynamic'))
    driver  = db.relationship('Driver',  backref=db.backref('fuel_entries', lazy='dynamic'))

    def compute_total(self):
        """Calcule le coût total du plein."""
        unit = self.price_unit
        if unit is None and self.fuel_type is not None:
            unit = self.fuel_type.price
        self.total_cost = round((unit or 0.0) * (self.liters or 0.0), 3)

    def __repr__(self):
        return f"<FuelEntry {self.id} {self.date} v={self.vehicle_id} {self.liters}L>"

# (Optionnel) Journal brut si tu veux garder une table simple “CSV-like”
class RefuelLog(db.Model):
    __tablename__ = 'refuel_logs'
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, default=date.today)
    truck_plate = db.Column(db.String(20), nullable=False)
    driver = db.Column(db.String(50), nullable=False)
    km_start = db.Column(db.Integer)
    km_end = db.Column(db.Integer)
    distance = db.Column(db.Float)
    liters = db.Column(db.Float, nullable=False)
    fuel_type = db.Column(db.String(20))
    amount_paid = db.Column(db.Float)
    consumption = db.Column(db.Float)

    def __repr__(self):
        return f"<Refuel {self.truck_plate} {self.date}>"

