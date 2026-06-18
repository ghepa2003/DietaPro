from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON

db = SQLAlchemy()

# Use JSONB for PostgreSQL if possible, fallback to JSON for SQLite
# In Flask-SQLAlchemy, JSON type handles this transparently in most cases, 
# but for Vercel Postgres JSONB is preferred for performance.
# We'll use the generic db.JSON for cross-compatibility between dev (SQLite) and prod (Postgres).

class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=True) # Used for Google Auth
    username = db.Column(db.String(80), unique=True, nullable=True) # Used for Local Auth
    pin_hash = db.Column(db.String(256), nullable=True) # Nullable because Google users don't need a PIN
    auth_provider = db.Column(db.String(20), nullable=False, default='local') # 'local' or 'google'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    state = db.relationship('UserState', backref='user', uselist=False, cascade='all, delete-orphan')

class Food(db.Model):
    __tablename__ = 'foods'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    categoria = db.Column(db.String(50), nullable=False) # 'carboidrati', 'proteine', 'grassi', 'frutta', 'verdura'
    calorie = db.Column(db.Float, nullable=False, default=0.0)
    carboidrati = db.Column(db.Float, nullable=False, default=0.0)
    proteine = db.Column(db.Float, nullable=False, default=0.0)
    grassi = db.Column(db.Float, nullable=False, default=0.0)
    
    # Unique constraint on nome within a category, or globally? 
    # Globally is better to avoid confusion (e.g. 'Avocado' in both frutta and grassi)
    __table_args__ = (
        db.UniqueConstraint('nome', name='uq_food_nome'),
    )

class UserState(db.Model):
    __tablename__ = 'user_states'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), unique=True, nullable=False)
    state_json = db.Column(db.JSON, nullable=False, default={})
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
