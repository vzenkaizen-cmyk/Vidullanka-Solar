
import os
import io
import json
import base64
import uuid
import hashlib
import hmac
import smtplib
from datetime import date, datetime, timedelta
from email.message import EmailMessage

import pandas as pd
import streamlit as st

# PDF report generation
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
)
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase import pdfmetrics
from sqlalchemy import (
    create_engine, Column, Integer, String, Boolean, Date, DateTime,
    Text, ForeignKey, Float, func, or_, text
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

# ============================================================
# SOLAR POWER PLANT MAINTENANCE AUTOMATION SYSTEM v2
#
# Added:
#   1. Red-status notifications to staff/supervisors
#   2. In-app notification center
#   3. Email notification support (SMTP)
#   4. Monthly maintenance reminders
#   5. Dashboard date / worker / category filters
#   6. Power BI-ready PostgreSQL data structure + export
# ============================================================

st.set_page_config(
    page_title="Solar Operation and Maintenance Platform",
    page_icon="☀️",
    layout="wide",
    initial_sidebar_state="expanded",
)

APP_TITLE = "Solar Operation and Maintenance Platform"
DEFAULT_DB = "sqlite:///solar_maintenance.db"

# Management target times used on the compact Overview cards.
# These are deliberately kept in one place so management can change them later.
PANEL_CLEANING_TARGET_DAYS = 90
GRASS_CUTTING_TARGET_DAYS = 60
REPAIR_MAINTENANCE_TARGET_DAYS = 7
# ============================================================
# NEON POSTGRESQL CONFIGURATION
# ============================================================
# Production database = Neon PostgreSQL.
# Put these values in .streamlit/secrets.toml locally and in
# Streamlit Cloud -> Settings -> Secrets when deployed.
#
# The helper below is intentionally safe when secrets.toml is
# missing, so the app displays a useful configuration error
# instead of crashing with StreamlitSecretNotFoundError.
# ============================================================

DEFAULT_DB = ""

def get_config(key, default=""):
    try:
        if key in st.secrets:
            value = st.secrets[key]
            if value is not None and str(value).strip():
                return str(value)
    except Exception:
        # secrets.toml does not exist yet
        pass
    return os.getenv(key, default)


DATABASE_URL = get_config("DATABASE_URL", DEFAULT_DB)

if not DATABASE_URL:
    st.error(
        "DATABASE_URL is not configured. "
        "Create .streamlit/secrets.toml and add your Neon PostgreSQL connection string."
    )
    st.stop()

# Accept both postgres:// and postgresql:// forms returned by Neon.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql+psycopg2://" + DATABASE_URL[len("postgres://"):]
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = "postgresql+psycopg2://" + DATABASE_URL[len("postgresql://"):]

ADMIN_EMAIL = get_config("ADMIN_EMAIL", "samodas@vidullanka.com")
ADMIN_PASSWORD = get_config("ADMIN_PASSWORD", "ChangeThisPassword123!")

# SMTP / Email settings
SMTP_HOST = get_config("SMTP_HOST", "")
SMTP_PORT = int(get_config("SMTP_PORT", "587"))
SMTP_USERNAME = get_config("SMTP_USERNAME", "")
SMTP_PASSWORD = get_config("SMTP_PASSWORD", "")
SMTP_FROM = get_config("SMTP_FROM", SMTP_USERNAME)
SMTP_USE_TLS = get_config("SMTP_USE_TLS", "true").lower() == "true"
ENABLE_EMAIL = get_config("ENABLE_EMAIL", "false").lower() == "true"

# Optional Power BI Push Dataset configuration.
# Direct PostgreSQL/Neon connection is recommended.
POWERBI_PUSH_URL = get_config("POWERBI_PUSH_URL", "")

@st.cache_resource(show_spinner=False)
def get_db_engine(database_url: str):
    return create_engine(
        database_url,
        pool_pre_ping=True,
        pool_recycle=1800,
        connect_args={"connect_timeout": 10},
    )


engine = get_db_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
Base = declarative_base()


# ============================================================
# Database models
# ============================================================

class Plant(Base):
    __tablename__ = "plants"

    id = Column(Integer, primary_key=True)
    code = Column(String(50), unique=True, nullable=False)
    name = Column(String(200), nullable=False)
    capacity_mw = Column(Float, default=0)
    inverter_count = Column(Integer, default=10)
    table_count = Column(Integer, default=80)
    zone_count = Column(Integer, default=10)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    users = relationship("User", back_populates="plant")
    records = relationship("MaintenanceRecord", back_populates="plant")
    repairs = relationship("RepairItem", back_populates="plant")
    reminders = relationship("MaintenanceReminder", back_populates="plant")


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    full_name = Column(String(200), nullable=False)
    email = Column(String(200), unique=True, nullable=False)
    password_hash = Column(String(300), nullable=False)
    role = Column(String(30), default="staff")  # admin/engineer/supervisor/staff
    plant_id = Column(Integer, ForeignKey("plants.id"), nullable=True)
    active = Column(Boolean, default=True)
    approved = Column(Boolean, default=True)  # Legacy field; account registration no longer requires approval
    created_at = Column(DateTime, default=datetime.utcnow)

    plant = relationship("Plant", back_populates="users")


class MaintenanceRecord(Base):
    __tablename__ = "maintenance_records"

    id = Column(Integer, primary_key=True)
    plant_id = Column(Integer, ForeignKey("plants.id"), nullable=False)
    category = Column(String(100), nullable=False)
    asset_id = Column(String(100), nullable=False)
    task_date = Column(Date, nullable=False)
    worker_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    status = Column(String(30), default="Pending")
    details_json = Column(Text, default="{}")
    remarks = Column(Text, default="")
    completed_at = Column(DateTime, nullable=True)
    approval_status = Column(String(30), default="Pending")
    approved_by = Column(Integer, nullable=True)
    approved_at = Column(DateTime, nullable=True)
    approval_signature = Column(Text, default="")
    approval_comment = Column(Text, default="")

    plant = relationship("Plant", back_populates="records")


class RepairItem(Base):
    __tablename__ = "repair_items"

    id = Column(Integer, primary_key=True)
    plant_id = Column(Integer, ForeignKey("plants.id"), nullable=False)
    source_category = Column(String(100), nullable=False)
    asset_id = Column(String(100), nullable=False)
    component = Column(String(200), nullable=False)
    issue = Column(Text, default="")
    reason_not_completed = Column(Text, default="")
    status = Column(String(30), default="Open")
    loss_percent = Column(Float, default=0)
    assigned_to = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    plant = relationship("Plant", back_populates="repairs")


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    plant_id = Column(Integer, ForeignKey("plants.id"), nullable=True)
    notification_type = Column(String(50), default="RED_ALERT")
    title = Column(String(250), nullable=False)
    message = Column(Text, nullable=False)
    category = Column(String(100), nullable=True)
    asset_id = Column(String(100), nullable=True)
    severity = Column(String(30), default="High")
    is_read = Column(Boolean, default=False)
    email_sent = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class MaintenanceReminder(Base):
    __tablename__ = "maintenance_reminders"

    id = Column(Integer, primary_key=True)
    plant_id = Column(Integer, ForeignKey("plants.id"), nullable=False)
    category = Column(String(100), nullable=False)
    frequency = Column(String(50), nullable=False)
    due_date = Column(Date, nullable=False)
    status = Column(String(30), default="Pending")
    created_at = Column(DateTime, default=datetime.utcnow)
    sent_at = Column(DateTime, nullable=True)

    plant = relationship("Plant", back_populates="reminders")


class PlantOperationsProfile(Base):
    """Editable operating-staff and contact details for each solar site."""
    __tablename__ = "plant_operations_profiles"

    id = Column(Integer, primary_key=True)
    plant_id = Column(Integer, ForeignKey("plants.id"), unique=True, nullable=False)
    operating_staff_count = Column(Integer, default=0, nullable=False)
    supervisor_name = Column(String(200), default="", nullable=False)
    engineer_name = Column(String(200), default="", nullable=False)
    hof_name = Column(String(200), default="", nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


@st.cache_resource(show_spinner=False)
def initialize_database(_engine):
    Base.metadata.create_all(_engine)
    if _engine.dialect.name == "postgresql":
        with _engine.begin() as conn:
            # Existing Neon projects may pre-date the operating-staff feature.
            # create_all() does not modify an existing schema, so explicitly create
            # this table if it is missing. This prevents UndefinedTable on Overview.
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS plant_operations_profiles (
                    id SERIAL PRIMARY KEY,
                    plant_id INTEGER NOT NULL UNIQUE REFERENCES plants(id),
                    operating_staff_count INTEGER NOT NULL DEFAULT 0,
                    supervisor_name VARCHAR(200) NOT NULL DEFAULT '',
                    engineer_name VARCHAR(200) NOT NULL DEFAULT '',
                    hof_name VARCHAR(200) NOT NULL DEFAULT '',
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
            conn.execute(text("ALTER TABLE maintenance_records ADD COLUMN IF NOT EXISTS approval_status VARCHAR(30) DEFAULT 'Pending'"))
            conn.execute(text("ALTER TABLE maintenance_records ADD COLUMN IF NOT EXISTS approved_by INTEGER"))
            conn.execute(text("ALTER TABLE maintenance_records ADD COLUMN IF NOT EXISTS approved_at TIMESTAMP"))
            conn.execute(text("ALTER TABLE maintenance_records ADD COLUMN IF NOT EXISTS approval_signature TEXT DEFAULT ''"))
            conn.execute(text("ALTER TABLE maintenance_records ADD COLUMN IF NOT EXISTS approval_comment TEXT DEFAULT ''"))

            # Older versions of this app may have created a CHECK constraint on
            # users.role that does not allow the current role values. The seed
            # routine updates existing users to admin/engineer/supervisor, which
            # would then fail during SQLAlchemy autoflush with CheckViolation.
            # Replace only role-related CHECK constraints and keep the supported
            # roles consistent with the application.
            conn.execute(text("""
                DO $$
                DECLARE constraint_row RECORD;
                BEGIN
                    FOR constraint_row IN
                        SELECT c.conname
                        FROM pg_constraint c
                        JOIN pg_class t ON t.oid = c.conrelid
                        JOIN pg_namespace n ON n.oid = t.relnamespace
                        WHERE t.relname = 'users'
                          AND n.nspname = current_schema()
                          AND c.contype = 'c'
                          AND pg_get_constraintdef(c.oid) ILIKE '%role%'
                    LOOP
                        EXECUTE format(
                            'ALTER TABLE %I.%I DROP CONSTRAINT %I',
                            current_schema(), 'users', constraint_row.conname
                        );
                    END LOOP;
                END $$;
            """))
            conn.execute(text("""
                UPDATE users
                SET role = 'staff'
                WHERE role IS NULL
                   OR role NOT IN ('admin', 'engineer', 'supervisor', 'staff')
            """))
            conn.execute(text("""
                ALTER TABLE users
                ADD CONSTRAINT users_role_allowed_check
                CHECK (role IN ('admin', 'engineer', 'supervisor', 'staff'))
            """))
    return True


initialize_database(engine)


# ============================================================
# Password helpers
# ============================================================

def hash_password(password: str) -> str:
    """Return a consistently formatted PBKDF2-SHA256 password hash.

    Format: pbkdf2_sha256<iterations><salt hex>$<digest hex>
    Keep this format stable because it is stored in the existing users table.
    """
    salt = os.urandom(16)
    iterations = 180_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256iterations{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Verify the current format and safely reject malformed/legacy broken hashes."""
    try:
        parts = (stored or "").split("$")
        if len(parts) != 4 or parts[0] != "pbkdf2_sha256":
            # Older app versions wrote a malformed string without the required
            # separators. It cannot be reliably verified; reset that account's
            # password through the configured admin password or account recovery.
            return False
        iterations = int(parts[1])
        salt = bytes.fromhex(parts[2])
        expected = parts[3]
        if iterations < 1 or not salt or not expected:
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(digest.hex(), expected)
    except (TypeError, ValueError, OverflowError):
        return False


# ============================================================
# Configuration from supplied process document
# ============================================================

PROCESS_CATEGORIES = {
    "Daily Inspection": {
        "frequency": "Daily",
        "asset_type": "Inverter",
        "items": [
            "Inverter Communication",
            "Inverter Fan",
            "Inverter AC, DC Parameters",
            "All CCTV Cameras Functioning",
            "Lightning Arresters ",
            "Plant Fire Extinguisher",
        ],
    },
    "Inverter Inspection": {
        "frequency": "Monthly",
        "asset_type": "Inverter",
        "items": [
            "Inverter Communication",
            "MC4 Connection",
            "DC Switches",
            "Cooling Fans Works",
            "AC Combiner Box",
            "No Corrosion",
            "Ventilation Duct",
            "Inverter Shelter",
        ],
    },
    "Panel Inspection": {
        "frequency": "Monthly",
        "asset_type": "Table",
        "items": [
            "Front Glass Condition",
            "Back Glass Condition",
            "Diode Damage",
        ],
    },
    "DC Cable Inspection": {
        "frequency": "Monthly",
        "asset_type": "Table",
        "items": [
            "DC Cable Condition ",
        ],
    },
    "MDB Inspection": {
        "frequency": "Monthly",
        "asset_type": "MDB",
        "items": [
            "ACB Push Buttons",
            "ACB Indicator",
            "Bus Bar Insulation",
            "No Loose Fasteners",
            "Switch Gear Cable Insulation Condition",
            "MCCB Tripping Function Works",
            "MCCB Contacts Are Tight",
            "CT Clearance Is Available",
            "CT Insulation",
            "Auto Transformer Functionality",
            "Multi-Function Meter Readings ",
            "Fans / Ventilation Duct Is Clean",
            "Thermo Works",
            "Enclosure ",
            "Surge Protection Active",
            "Fuse - All Works",
            "Meter Panel Battery Status Functions",
            "Protection Module ",
        ],
    },
    "Switch Yard Inspection": {
        "frequency": "Monthly",
        "asset_type": "Switch Yard",
        "items": [
            "Transformer - No Abnormal Sound",
            "Transformer - No Oil Leaks",
            "Transformer - No Visible Corrosion",
            "Transformer Insulators Are Good",
            "Transformer Surge Protectors Are Good",
            "OCB Tripping Works",
            "OCB Tripping Count Recorded",
            "OCB Auto / Manual Works",
            "DDLO ",
            "Fence ",
            "Warning Signs clear and visible",
        ],
    },
    "AC Inspection": {
        "frequency": "Every 4 Months",
        "asset_type": "AC Unit",
        "items": [
            "Outdoor - No Noise/Vibration of Compressor",
            "Outdoor - No Noise/Vibration of Condenser Fan",
            "Outdoor - Gas Tubeline Insulation ",
            "Outdoor - Isolator Operation",
            "Outdoor - Clean Body of Compressor",
            "Outdoor - Clean Condenser Coil",
            "Outdoor - Clean Fan Blades",
            "Indoor - No Noise/Vibration of Blower Motor",
            "Indoor - Thermostat Operation Against Temperature",
            "Indoor - Check Remote",
            "Indoor - Clean Air Filter",
            "Indoor - Clean Louvers",
            "Indoor - Clean Evaporator Coil",
            "Indoor - Clean Blower Motor",
            "Indoor - Clean Control Panel",
        ],
    },
}

# These are dashboard work categories.
DASHBOARD_CATEGORIES = [
    "All",
    "Panel Cleaning",
    "Grass Cutting",
    *PROCESS_CATEGORIES.keys(),
]

LOSS_REPAIR_CATEGORIES = list(PROCESS_CATEGORIES.keys())


# Approved work-email access map. These users choose their own password at first registration.
# These four named accounts have full Administrator access, including all Engineer permissions.
MANAGEMENT_ROLE_BY_EMAIL = {
    "roshants@vidullanka.com": "admin",
    "manchanayakeu@vidullanka.com": "admin",
    "sampathr@vidullanka.com": "admin",
    "damithab@vidullanka.com": "admin",
    "prasannak@vidullanka.com": "engineer",
    "mahelaw@vidullanka.com": "engineer",
    "keshand@vidullanka.com": "engineer",
    "kosalaj@vidullanka.com": "engineer",
    "dulanw@vidullanka.com": "engineer",
}
PLANT_SUPERVISOR_EMAILS = {
    "mtr.ops@vidullanka.com": "MTR",
    "mdp.ops@vidullanka.com": "MDP",
    "hrn.ops@vidullanka.com": "HS1",
    "ork.ops@vidullanka.com": "ORK",
}

def _plant_id_for_code(db, code):
    if not code:
        return None
    plant = db.query(Plant).filter(Plant.code.ilike(code)).first()
    return plant.id if plant else None


# ============================================================
# Seed
# ============================================================

def seed_database():
    if st.session_state.get("_database_seeded"):
        return

    db = SessionLocal()
    try:
        if db.query(Plant).count() == 0:
            db.add(
                Plant(
                    code="HRN",
                    name="HRN Solar Power Plant",
                    capacity_mw=2,
                    inverter_count=17,
                    table_count=150,
                    zone_count=10,
                )
            )
            db.commit()

        # Ensure the requested sites are selectable even before their detailed plant
        # information is available. Unknown capacities/headcounts stay at zero and can
        # be updated later from Plants and the site staff section.
        configured_sites = [
            # Keep only the plants requested for the selector. HS1/HS2 are the
            # internal database codes; the user-facing names are HRN 1 and HRN 2.
            ("HS1", "HRN 1 Solar Power Plant"),
            ("HS2", "HRN 2 Solar Power Plant"),
            ("MDP", "MDP Solar Power Plant"),
            ("MTR", "MTR Solar Power Plant"),
            ("ORK", "ORK Solar Power Plant"),
            ("DPV", "DPV Solar Power Plant (Ranna)"),
        ]
        for site_code, site_name in configured_sites:
            if not db.query(Plant).filter(Plant.code == site_code).first():
                db.add(Plant(code=site_code, name=site_name, capacity_mw=0,
                             inverter_count=10, table_count=80, zone_count=10, active=True))
        db.flush()
        # ORK has 52 solar tables. Keep the displayed count and generated table IDs aligned.
        ork_plant = db.query(Plant).filter(Plant.code == "ORK").first()
        if ork_plant and ork_plant.table_count != 52:
            ork_plant.table_count = 52
        dpv_plant = db.query(Plant).filter(Plant.code == "DPV").first()
        if dpv_plant:
            dpv_plant.zone_count = 15
            dpv_plant.table_count = 366
        db.commit()

        # HRN has been renamed to HS1 in the application. If an older Neon database
        # still contains HRN, migrate it to HS1 without deleting the plant data.
        old_hrn = db.query(Plant).filter(Plant.code == "HRN").first()
        hs1_plant = db.query(Plant).filter(Plant.code == "HS1").first()
        if old_hrn and not hs1_plant:
            old_hrn.code = "HS1"
            old_hrn.name = "HRN 1 Solar Power Plant"
            hs1_plant = old_hrn
        elif old_hrn and hs1_plant:
            # Merge legacy HRN records into the existing HS1 row so maintenance
            # history, repairs, reminders, notifications and staff assignments are
            # not stranded on an inactive plant.
            for table_name in ("users", "maintenance_records", "repair_items", "notifications", "maintenance_reminders"):
                db.execute(text(f"UPDATE {table_name} SET plant_id = :new_id WHERE plant_id = :old_id"),
                           {"new_id": hs1_plant.id, "old_id": old_hrn.id})
            old_profile = db.query(PlantOperationsProfile).filter(PlantOperationsProfile.plant_id == old_hrn.id).first()
            new_profile = db.query(PlantOperationsProfile).filter(PlantOperationsProfile.plant_id == hs1_plant.id).first()
            if old_profile and not new_profile:
                old_profile.plant_id = hs1_plant.id
            elif old_profile and new_profile:
                db.delete(old_profile)
            old_hrn.active = False
        if hs1_plant:
            hs1_plant.name = "HRN 1 Solar Power Plant"
            if hs1_plant.capacity_mw in (None, 0):
                hs1_plant.capacity_mw = 2

        # Remove legacy/duplicate plants from the selector without deleting their
        # historical records. HOF is no longer a selectable plant, and only one MTR
        # plant is kept. If an older MTR-I row exists, move its linked records to MTR
        # before hiding the old row.
        mtr = db.query(Plant).filter(Plant.code == "MTR").first()
        old_mtr = db.query(Plant).filter(Plant.code == "MTR-I").first()
        if old_mtr and not mtr:
            old_mtr.code = "MTR"
            old_mtr.name = "MTR Solar Power Plant"
            old_mtr.active = True
            mtr = old_mtr
        elif old_mtr and mtr and old_mtr.id != mtr.id:
            for table_name in ("users", "maintenance_records", "repair_items", "notifications", "maintenance_reminders"):
                db.execute(text(f"UPDATE {table_name} SET plant_id = :new_id WHERE plant_id = :old_id"),
                           {"new_id": mtr.id, "old_id": old_mtr.id})
            old_profile = db.query(PlantOperationsProfile).filter(PlantOperationsProfile.plant_id == old_mtr.id).first()
            new_profile = db.query(PlantOperationsProfile).filter(PlantOperationsProfile.plant_id == mtr.id).first()
            if old_profile and not new_profile:
                old_profile.plant_id = mtr.id
            elif old_profile and new_profile:
                db.delete(old_profile)
            old_mtr.active = False
        if mtr:
            mtr.name = "MTR Solar Power Plant"
            mtr.active = True
            # The supplied MTR layout contains 102 table rows/cells in total.
            if mtr.table_count != 102:
                mtr.table_count = 102

        for legacy_code in ("HOF", "MTR-II"):
            legacy = db.query(Plant).filter(Plant.code == legacy_code).first()
            if legacy:
                legacy.active = False

        # Hide duplicate legacy Horana I/II entries from selectors and overview cards.
        # Keep their records in the database; the active HS1/HS2 entries are the
        # canonical HRN 1 and HRN 2 plants.
        for legacy_plant in db.query(Plant).all():
            legacy_name = (legacy_plant.name or "").strip().lower()
            if legacy_plant.code not in {"HS1", "HS2"} and legacy_name in {
                "horana i solar power plant", "horana ii solar power plant",
                "hrn 1 (horana) solar power plant", "hrn 2 (horana) solar power plant",
            }:
                legacy_plant.active = False

        hs2_plant = db.query(Plant).filter(Plant.code == "HS2").first()
        if hs2_plant:
            hs2_plant.name = "HRN 2 Solar Power Plant"
            hs2_plant.active = True

        # Make the configured administrator account an approved active admin.
        admin_email = ADMIN_EMAIL.lower().strip()
        admin = db.query(User).filter(User.email == admin_email).first()

        if not admin:
            admin = User(
                full_name="System Administrator",
                email=admin_email,
                password_hash=hash_password(ADMIN_PASSWORD),
                role="admin",
                approved=True,
                active=True,
            )
            db.add(admin)
        else:
            # Keep the configured administrator active without rewriting its
            # password hash on every app startup. Rewriting hashes on every run
            # is unnecessary and can violate constraints in an existing schema.
            admin.full_name = "System Administrator"
            admin.role = "admin"
            admin.approved = True
            admin.active = True
            # Only replace the password if the stored value is not verifiable
            # against the currently configured ADMIN_PASSWORD. This also repairs
            # older malformed hashes when the configured password is known.
            if not verify_password(ADMIN_PASSWORD, admin.password_hash):
                admin.password_hash = hash_password(ADMIN_PASSWORD)

        # Keep the named Vidullanka accounts on their intended least-privilege role/scope.
        # Passwords are never changed here; each person sets their own during registration.
        for email_value, role_value in MANAGEMENT_ROLE_BY_EMAIL.items():
            existing = db.query(User).filter(User.email == email_value).first()
            if existing:
                existing.role = role_value
                existing.plant_id = None
                existing.approved = True
        for email_value, plant_code in PLANT_SUPERVISOR_EMAILS.items():
            existing = db.query(User).filter(User.email == email_value).first()
            if existing:
                existing.role = "supervisor"
                existing.plant_id = _plant_id_for_code(db, plant_code)
                existing.approved = True

        db.commit()
        st.session_state["_database_seeded"] = True
    finally:
        db.close()


seed_database()


# ============================================================
# General helpers
# ============================================================

def db_session():
    return SessionLocal()


def get_current_user():
    return st.session_state.get("user")


def allowed_plants(db, user):
    if user.role in ("admin", "engineer"):
        return db.query(Plant).filter(Plant.active == True).order_by(Plant.name).all()
    if user.plant_id:
        p = db.query(Plant).filter(Plant.id == user.plant_id).first()
        return [p] if p and p.active else []
    return []


@st.cache_data(show_spinner=False)
def _asset_list_cached(asset_type, count):
    if asset_type == "Inverter":
        return tuple(f"INV-{i:02d}" for i in range(1, count + 1))
    if asset_type == "Table":
        return tuple(f"T-{i:02d}" for i in range(1, count + 1))
    if asset_type == "Zone":
        return tuple(f"Z-{i:02d}" for i in range(1, count + 1))
    if asset_type == "MDB":
        return ("MDB-01",)
    if asset_type == "Switch Yard":
        return ("SY-01",)
    if asset_type == "AC Unit":
        return ("AC-01",)
    return ("SITE-01",)


def asset_list(plant, asset_type):
    count = {"Inverter": plant.inverter_count, "Table": plant.table_count, "Zone": plant.zone_count}.get(asset_type, 1)
    return list(_asset_list_cached(asset_type, count))


def status_map(db, plant_id, category, task_date):
    rows = db.query(MaintenanceRecord).filter(
        MaintenanceRecord.plant_id == plant_id,
        MaintenanceRecord.category == category,
        MaintenanceRecord.task_date == task_date,
    ).all()
    return {r.asset_id: r for r in rows}


def record_status(db, plant_id, category, asset_id, task_date):
    return (
        db.query(MaintenanceRecord)
        .filter(
            MaintenanceRecord.plant_id == plant_id,
            MaintenanceRecord.category == category,
            MaintenanceRecord.asset_id == asset_id,
            MaintenanceRecord.task_date == task_date,
        )
        .first()
    )


def parse_details(rec):
    try:
        return json.loads(rec.details_json or "{}")
    except Exception:
        return {}


def send_email(to_email, subject, body):
    if not ENABLE_EMAIL or not SMTP_HOST or not SMTP_USERNAME or not SMTP_PASSWORD or not to_email:
        return False

    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = SMTP_FROM or SMTP_USERNAME
        msg["To"] = to_email
        msg.set_content(body)

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
            if SMTP_USE_TLS:
                server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.send_message(msg)
        return True
    except Exception:
        return False


def notify_users(db, plant_id, title, message, category, asset_id, notification_type="RED_ALERT"):
    recipients = (
        db.query(User)
        .filter(
            User.active == True,
            User.approved == True,
            or_(
                User.plant_id == plant_id,
                User.role.in_(["admin", "engineer"]),
            ),
        )
        .all()
    )

    for recipient in recipients:
        # Avoid generating duplicate unread alerts for exactly the same active issue.
        duplicate = (
            db.query(Notification)
            .filter(
                Notification.user_id == recipient.id,
                Notification.plant_id == plant_id,
                Notification.category == category,
                Notification.asset_id == asset_id,
                Notification.title == title,
                Notification.is_read == False,
            )
            .first()
        )
        if duplicate:
            continue

        email_sent = send_email(
            recipient.email,
            f"[Solar Maintenance] {title}",
            message,
        )

        db.add(
            Notification(
                user_id=recipient.id,
                plant_id=plant_id,
                notification_type=notification_type,
                title=title,
                message=message,
                category=category,
                asset_id=asset_id,
                severity="High",
                is_read=False,
                email_sent=email_sent,
            )
        )

    db.commit()


def ensure_repair_from_failed_item(db, plant_id, category, asset_id, component, issue, user_id):
    existing = (
        db.query(RepairItem)
        .filter(
            RepairItem.plant_id == plant_id,
            RepairItem.source_category == category,
            RepairItem.asset_id == asset_id,
            RepairItem.component == component,
            RepairItem.status != "Completed",
        )
        .first()
    )

    if not existing:
        item = RepairItem(
            plant_id=plant_id,
            source_category=category,
            asset_id=asset_id,
            component=component,
            issue=issue,
            reason_not_completed=issue,
            status="Open",
            loss_percent=0,
            created_by=user_id,
        )
        db.add(item)
        db.commit()

        plant = db.query(Plant).filter(Plant.id == plant_id).first()
        title = f"🔴 {asset_id} requires maintenance"
        message = (
            f"Plant: {plant.name}\n"
            f"Category: {category}\n"
            f"Asset: {asset_id}\n"
            f"Component: {component}\n"
            f"Reason: {issue or 'Not specified'}\n\n"
            "Action required: inspect the Repair & Maintenance section and update the repair status."
        )
        notify_users(
            db,
            plant_id,
            title,
            message,
            category,
            asset_id,
            notification_type="RED_ALERT",
        )


def upsert_record(db, plant_id, category, asset_id, task_date, worker_id, status, details, remarks=""):
    rec = record_status(db, plant_id, category, asset_id, task_date)
    old_status = rec.status if rec else None

    if not rec:
        rec = MaintenanceRecord(
            plant_id=plant_id,
            category=category,
            asset_id=asset_id,
            task_date=task_date,
        )
        db.add(rec)

    rec.worker_id = worker_id
    rec.status = status
    rec.details_json = json.dumps(details, ensure_ascii=False)
    rec.remarks = remarks
    rec.completed_at = datetime.utcnow() if status in ("Completed", "Failed") else None
    db.commit()
    db.refresh(rec)

    # Red transition -> notify staff/supervisors/admins.
    if status == "Failed" and old_status != "Failed":
        plant = db.query(Plant).filter(Plant.id == plant_id).first()
        notify_users(
            db,
            plant_id,
            f"🔴 {asset_id} marked RED",
            (
                f"Plant: {plant.name}\n"
                f"Category: {category}\n"
                f"Asset: {asset_id}\n"
                f"Date: {task_date}\n"
                f"Remarks: {remarks or 'A checklist item was marked Not OK / Red.'}\n\n"
                "Please review Repair & Maintenance."
            ),
            category,
            asset_id,
            notification_type="RED_ALERT",
        )

    return rec


def progress_for(db, plant_id, category, task_date):
    plant = db.query(Plant).filter(Plant.id == plant_id).first()
    assets = asset_list(plant, PROCESS_CATEGORIES[category]["asset_type"])
    done = 0
    failed = 0

    for a in assets:
        r = record_status(db, plant_id, category, a, task_date)
        if r:
            if r.status == "Completed":
                done += 1
            elif r.status == "Failed":
                failed += 1

    return len(assets), done, failed


def panel_cleaning_percent(db, plant_id, task_date):
    plant = db.query(Plant).filter(Plant.id == plant_id).first()
    total = plant.table_count
    records = status_map(db, plant_id, "Panel Cleaning", task_date)
    done = sum(1 for r in records.values() if r.status == "Completed")
    return (round(done / total * 100, 1) if total else 0), done, total


def grass_cutting_percent(db, plant_id, task_date):
    plant = db.query(Plant).filter(Plant.id == plant_id).first()
    total = plant.zone_count
    records = status_map(db, plant_id, "Grass Cutting", task_date)
    done = sum(1 for r in records.values() if r.status == "Completed")
    return (round(done / total * 100, 1) if total else 0), done, total


# ============================================================
# Monthly reminder engine
# ============================================================

def next_due_date(frequency, start=None):
    start = start or date.today()

    if frequency == "Daily":
        return start
    if frequency == "Monthly":
        # Due on the first day of the next month.
        if start.month == 12:
            return date(start.year + 1, 1, 1)
        return date(start.year, start.month + 1, 1)
    if frequency == "Every 4 Months":
        month = start.month + 4
        year = start.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        return date(year, month, 1)
    return start


def generate_monthly_reminders(db, plant_id, today=None):
    today = today or date.today()
    created = 0

    for category, cfg in PROCESS_CATEGORIES.items():
        if cfg["frequency"] not in ("Monthly", "Every 4 Months"):
            continue

        existing = (
            db.query(MaintenanceReminder)
            .filter(
                MaintenanceReminder.plant_id == plant_id,
                MaintenanceReminder.category == category,
                MaintenanceReminder.due_date >= today.replace(day=1),
            )
            .first()
        )

        if not existing:
            due = next_due_date(cfg["frequency"], today)
            db.add(
                MaintenanceReminder(
                    plant_id=plant_id,
                    category=category,
                    frequency=cfg["frequency"],
                    due_date=due,
                    status="Pending",
                )
            )
            created += 1

    db.commit()
    return created


def send_due_reminders(db, plant_id, today=None):
    today = today or date.today()
    generated = generate_monthly_reminders(db, plant_id, today)

    reminders = (
        db.query(MaintenanceReminder)
        .filter(
            MaintenanceReminder.plant_id == plant_id,
            MaintenanceReminder.status != "Completed",
            MaintenanceReminder.due_date <= today,
        )
        .all()
    )

    sent = 0
    plant = db.query(Plant).filter(Plant.id == plant_id).first()

    recipients = (
        db.query(User)
        .filter(
            User.active == True,
            User.approved == True,
            or_(User.plant_id == plant_id, User.role.in_(["admin", "engineer"])),
        )
        .all()
    )

    for reminder in reminders:
        # Send once per reminder record.
        if reminder.sent_at:
            continue

        title = f"📅 Maintenance reminder: {reminder.category}"
        message = (
            f"Plant: {plant.name}\n"
            f"Maintenance: {reminder.category}\n"
            f"Frequency: {reminder.frequency}\n"
            f"Due date: {reminder.due_date}\n\n"
            "Please complete the maintenance checklist in the Solar Maintenance System."
        )

        for recipient in recipients:
            db.add(
                Notification(
                    user_id=recipient.id,
                    plant_id=plant_id,
                    notification_type="MONTHLY_REMINDER",
                    title=title,
                    message=message,
                    category=reminder.category,
                    asset_id=None,
                    severity="Medium",
                    is_read=False,
                    email_sent=send_email(
                        recipient.email,
                        f"[Solar Maintenance] {title}",
                        message,
                    ),
                )
            )

        reminder.sent_at = datetime.utcnow()
        sent += 1

    db.commit()
    return generated, sent


# ============================================================
# Dashboard filter helpers
# ============================================================

def filtered_records(db, plant_id, start_date, end_date, worker_id=None, category="All"):
    q = db.query(MaintenanceRecord).filter(
        MaintenanceRecord.plant_id == plant_id,
        MaintenanceRecord.task_date >= start_date,
        MaintenanceRecord.task_date <= end_date,
    )

    if worker_id not in (None, "All"):
        q = q.filter(MaintenanceRecord.worker_id == int(worker_id))

    if category != "All":
        q = q.filter(MaintenanceRecord.category == category)

    return q.order_by(MaintenanceRecord.task_date.desc()).all()


def dashboard_metrics(db, plant, records, start_date, end_date):
    panel_total = plant.table_count
    grass_total = plant.zone_count

    panel_done = sum(
        1 for r in records
        if r.category == "Panel Cleaning" and r.status == "Completed"
    )
    grass_done = sum(
        1 for r in records
        if r.category == "Grass Cutting" and r.status == "Completed"
    )

    inspection_records = [
        r for r in records if r.category in PROCESS_CATEGORIES
    ]
    inspection_done = sum(1 for r in inspection_records if r.status == "Completed")
    inspection_failed = sum(1 for r in inspection_records if r.status == "Failed")

    panel_pct = round(panel_done / panel_total * 100, 1) if panel_total else 0
    grass_pct = round(grass_done / grass_total * 100, 1) if grass_total else 0
    inspection_pct = (
        round(inspection_done / len(inspection_records) * 100, 1)
        if inspection_records else 0
    )

    open_repairs = (
        db.query(RepairItem)
        .filter(
            RepairItem.plant_id == plant.id,
            RepairItem.status != "Completed",
            RepairItem.created_at >= datetime.combine(start_date, datetime.min.time()),
            RepairItem.created_at <= datetime.combine(end_date, datetime.max.time()),
        )
        .count()
    )

    return {
        "panel_pct": panel_pct,
        "panel_done": panel_done,
        "panel_total": panel_total,
        "grass_pct": grass_pct,
        "grass_done": grass_done,
        "grass_total": grass_total,
        "inspection_pct": inspection_pct,
        "inspection_done": inspection_done,
        "inspection_failed": inspection_failed,
        "inspection_total": len(inspection_records),
        "open_repairs": open_repairs,
    }


# ============================================================
# CSS / UI
# ============================================================

def css():
    st.markdown(
        """<style>
        :root{--bg:#f6f8fb;--surface:#fff;--surface2:#f8fafc;--text:#14213d;--muted:#61748a;--border:#dfe6ef;--green:#16a66a;--red:#e5484d;--yellow:#d9a400;--shadow:0 4px 16px rgba(25,42,70,.07)}
        @media (prefers-color-scheme:dark){:root{--bg:#0f1724;--surface:#172233;--surface2:#1d2a3b;--text:#f4f7fb;--muted:#b2bfd0;--border:#304055;--green:#20c77a;--red:#ff6268;--yellow:#f2c14e;--shadow:0 4px 18px rgba(0,0,0,.25)}}
        .stApp{background:var(--bg);color:var(--text)}
        /* Clear, readable tables and form fields */
        [data-testid="stDataFrame"] thead, [data-testid="stTable"] thead {background:#d9ecfb!important;color:#123b63!important}
        [data-testid="stDataFrame"] th, [data-testid="stTable"] th {background:#d9ecfb!important;color:#123b63!important;font-weight:750!important}
        [data-testid="stDataFrame"] th,[data-testid="stTable"] th{font-size:13px!important}
        [data-testid="stDataFrame"] td,[data-testid="stTable"] td{font-size:12px!important}
        [data-testid="stTextInput"] input,[data-testid="stTextArea"] textarea,[data-testid="stNumberInput"] input,[data-testid="stDateInput"] input {color:#14213d!important;background:#fff!important;-webkit-text-fill-color:#14213d!important;font-size:15px!important;caret-color:#14213d!important}
        [data-testid="stTextInput"] input::placeholder,[data-testid="stTextArea"] textarea::placeholder {color:#718096!important;opacity:1!important}
        [data-testid="stMainBlockContainer"]{padding-top:1.1rem!important}
        [data-testid="stHeader"]{background:transparent!important}
        .login-form-title,.login-form-sub{font-family:Arial,Helvetica,sans-serif!important;letter-spacing:normal!important}
        /* Improve welcome and sign-in readability without changing the layout. */
        .login-hero-title{font-size:clamp(42px,4.6vw,68px)!important;line-height:1.08!important}
        .login-hero-sub{font-size:22px!important}
        .login-hero-point{font-size:15px!important}
        .login-hero-point b{font-size:18px!important}
        .login-form-title{font-size:40px!important}
        .login-form-sub{font-size:16px!important}
        [data-testid="stColumn"]:has(.login-card-anchor) [data-testid="stTabs"] button,
        [data-testid="stColumn"]:has(.login-card-anchor) [data-testid="stTabs"] button p{font-size:16px!important}
        [data-testid="stColumn"]:has(.login-card-anchor) [data-testid="stTextInput"] input{font-size:17px!important}
        .login-form-title{font-weight:750!important}
        [data-testid="stMain"] h1,[data-testid="stMain"] h2{scroll-margin-top:1rem}
        [data-testid="stSidebar"]{display:block!important;min-width:250px!important}
        [data-testid="stSidebar"]>div{background:var(--surface);border-right:1px solid var(--border)}
        .sidebar-brand{font-size:20px;font-weight:850;color:var(--text);padding:8px 0 2px}
        .sidebar-sub{font-size:12px;color:var(--muted);margin-bottom:14px}
        .top-title{font-size:30px;font-weight:800;color:var(--text);line-height:1.15}.top-subtitle{color:var(--muted);font-size:14px}
        .topbar{background:var(--surface);border:1px solid var(--border);border-radius:16px;padding:12px 16px;margin:0 0 10px;box-shadow:var(--shadow)}
        .nav-wrap{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:4px 8px;margin:0 0 18px;box-shadow:var(--shadow);position:sticky;top:0;z-index:50}
        .nav-wrap [role="radiogroup"]{gap:4px;flex-wrap:wrap}.nav-wrap label{border-radius:9px;padding:5px 10px!important;border:1px solid transparent;min-height:34px}.nav-wrap label:hover{background:var(--surface2);border-color:var(--border)}
        .card,.dashboard-card,.status-tile{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:18px;margin-bottom:12px;box-shadow:var(--shadow)}
        .dashboard-card{height:108px;min-height:108px;display:flex;flex-direction:column;justify-content:flex-start;gap:8px;padding:14px 14px 12px;box-sizing:border-box;overflow:hidden}.dashboard-card h4{margin:0;color:var(--muted);font-size:13px;line-height:1.25;min-height:17px}.dashboard-value{color:var(--text);font-size:28px;font-weight:800;line-height:1.1;margin-top:2px}.dashboard-label,.small-muted{color:var(--muted);font-size:11px;line-height:1.25;overflow-wrap:anywhere}.dashboard-label{margin-top:auto;min-height:29px}.pending-inline{display:block;margin-top:2px;white-space:normal;line-height:1.2}.good{color:var(--green);font-weight:800}.bad{color:var(--red);font-weight:800}.warn{color:var(--yellow);font-weight:800}.section-title{font-size:20px;font-weight:800;color:var(--text)}
        div[data-testid="stMetric"]{background:var(--surface);border:1px solid var(--border);padding:12px;border-radius:12px;box-shadow:var(--shadow);min-height:108px;box-sizing:border-box;min-width:0}
        div[data-testid="stMetric"] label{white-space:normal!important;overflow-wrap:anywhere;line-height:1.2;font-size:11px!important;display:block;min-width:0;max-width:100%;height:auto;overflow:visible;text-overflow:clip}
        div[data-testid="stMetric"] [data-testid="stMetricLabel"],div[data-testid="stMetric"] [data-testid="stMetricLabel"] p{font-size:11px!important;line-height:1.2!important;white-space:normal!important;overflow-wrap:anywhere!important;word-break:break-word!important;display:block!important;width:100%!important;max-width:100%!important;overflow:visible!important;text-overflow:clip!important}
        div[data-testid="stMetric"] [data-testid="stMetricLabel"] *{font-size:11px!important;line-height:1.2!important;white-space:normal!important;overflow:visible!important;text-overflow:clip!important;overflow-wrap:anywhere!important}
        div[data-testid="stMetric"] [data-testid="stMetricLabel"] p{margin:0!important}
        /* Keep approval counts and their two-line labels fully inside each KPI card. */
        div[data-testid="stMetric"]{padding:8px 7px!important;overflow:hidden!important;min-width:0!important;height:90px!important;min-height:90px!important;display:flex!important;flex-direction:column!important;justify-content:center!important;box-sizing:border-box!important}
        div[data-testid="stMetric"] [data-testid="stMetricValue"],
        div[data-testid="stMetric"] [data-testid="stMetricValue"] div{font-size:15px!important;line-height:1.05!important;white-space:normal!important;overflow-wrap:anywhere!important}
        div[data-testid="stMetric"] [data-testid="stMetricLabel"],
        div[data-testid="stMetric"] [data-testid="stMetricLabel"] p{font-size:10px!important;line-height:1.15!important;white-space:normal!important;overflow-wrap:anywhere!important;word-break:normal!important}
        /* Keep Streamlit's image/element toolbar compact when it appears. */
        [data-testid="stElementToolbar"]{transform:scale(.72);transform-origin:top right;right:3px!important;top:3px!important}
        .modebar-container{transform:scale(.72);transform-origin:top right}
        div[data-baseweb="select"]>div,div[data-baseweb="input"]>div,div[data-baseweb="textarea"]>div{background:var(--surface)}
        .status-tile{padding:13px 14px;min-height:78px;box-sizing:border-box}.status-dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:8px}.status-green{background:var(--green)}.status-red{background:var(--red)}.status-pending{background:var(--yellow)}
        .group-card{background:var(--surface2);border:1px solid var(--border);border-radius:14px;padding:18px 20px;margin:8px 0 20px;width:100%;box-sizing:border-box}.group-title{font-size:16px;font-weight:800;color:var(--text);margin:12px 0 8px}.check-item{background:var(--surface);border:1px solid var(--border);border-radius:9px;padding:10px 13px;margin:4px 0;min-height:46px;width:100%;display:flex;align-items:center;box-sizing:border-box}.group-card [data-testid="stHorizontalBlock"]{gap:.8rem;align-items:center}.group-card [data-testid="stSelectbox"]{min-width:160px}.group-card [data-testid="stTextInput"]{min-width:190px}
        .layout-wrap{background:var(--surface);border:1px solid var(--border);border-radius:16px;padding:12px;box-shadow:var(--shadow)}
        .solar-map{position:relative;width:100%;min-height:900px;aspect-ratio:1280/1000;overflow:hidden;border-radius:14px;background:radial-gradient(ellipse at 50% 45%,#173d4b 0%,#09233b 55%,#06162a 100%);border:1px solid #244765;box-shadow:inset 0 0 0 8px rgba(35,93,125,.12)}
        .solar-map-title{position:absolute;left:14px;top:12px;background:rgba(4,24,45,.90);color:#fff;padding:8px 12px;border-radius:8px;font-weight:800;z-index:5}.solar-map-legend{position:absolute;right:12px;bottom:12px;background:rgba(4,24,45,.92);color:#fff;padding:8px 12px;border-radius:8px;z-index:5;font-size:12px}.legend-dot{display:inline-block;width:12px;height:8px;border-radius:2px;margin-right:5px}.legend-green{background:#16e879}.legend-red{background:#ff4e59}
        .map-zone{position:absolute;padding:7px;background:rgba(6,24,42,.66);border:2px solid rgba(255,78,89,.9);border-radius:10px;box-sizing:border-box;pointer-events:none;box-shadow:0 4px 16px rgba(0,0,0,.2)}.map-zone-title{display:inline-block;color:#fff;padding:5px 8px;border-radius:5px;font-size:12px;font-weight:800;margin-bottom:6px;box-shadow:0 1px 3px rgba(0,0,0,.4)}.map-tables{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:4px}.map-table{height:12px;border-radius:3px;opacity:1;box-shadow:0 0 0 1px rgba(0,0,0,.28)}.map-green{background:#16e879}.map-red{background:#ff4e59}.map-controls{position:absolute;right:10px;top:10px;width:108px;max-width:18%;background:rgba(4,24,45,.88);color:#fff;padding:5px 7px;border-radius:7px;z-index:5;font-size:9px;line-height:1.35;box-sizing:border-box;overflow-wrap:anywhere}.map-controls b{display:block;margin-bottom:2px}
        .mini-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}.mini-item{background:var(--surface);border:1px solid var(--border);border-radius:9px;padding:8px 7px;min-height:50px;box-sizing:border-box}.mini-item .name{font-weight:800;font-size:12px}.mini-item .state{font-size:10px;margin-top:3px}.loss-tile{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:10px;min-height:76px;box-sizing:border-box}.loss-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}
        .map-section-title{font-size:18px;font-weight:800;color:var(--text);margin:2px 0 10px}.top-user{font-size:12px;color:var(--muted);text-align:right;padding-top:4px}
        .overview-notification{background:var(--surface);border:1px solid var(--border);border-left:4px solid var(--red);border-radius:12px;padding:9px 11px;min-height:68px;box-shadow:var(--shadow);box-sizing:border-box}.overview-notification .small-muted{display:block;margin:6px 0;overflow-wrap:anywhere}.overview-empty{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:14px;color:var(--muted);box-shadow:var(--shadow)}
        .overview-work-card{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:14px;min-height:145px;box-shadow:var(--shadow);margin-bottom:8px}.overview-work-title{font-size:17px;font-weight:800;color:var(--text)}.overview-work-percent{font-size:31px;font-weight:900;color:var(--green);line-height:1.05;margin-top:10px}.overview-work-target{font-size:11px;color:var(--muted);margin-top:8px}.overview-inverter-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px;max-height:250px;overflow:auto;padding:2px 3px 4px 2px;box-sizing:border-box;width:100%;min-width:0}.overview-inverter{background:var(--surface);border:1px solid var(--border);border-radius:9px;padding:7px 8px;font-size:10px;line-height:1.25;min-width:0;width:100%;box-sizing:border-box;overflow:hidden;overflow-wrap:anywhere;word-break:normal}.overview-inverter b{white-space:nowrap}.overview-inverter-grid .good{color:var(--green)}.overview-inverter-grid .bad{color:var(--red)}
        .plant-detail-card{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:14px;min-height:105px;margin-bottom:12px;box-shadow:var(--shadow);box-sizing:border-box;overflow:hidden}.plant-detail-label{color:var(--muted);font-size:12px;margin-bottom:7px}.plant-detail-value{color:var(--text);font-size:19px;font-weight:800;line-height:1.2;overflow-wrap:anywhere;word-break:break-word}.plant-detail-hint{color:var(--muted);font-size:10px;margin-top:6px}.staff-summary-card{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:16px;min-height:122px;box-shadow:var(--shadow);box-sizing:border-box;overflow:hidden}.staff-summary-label{color:var(--muted);font-size:12px;margin-bottom:10px}.staff-summary-value{color:var(--text);font-size:29px;font-weight:800;line-height:1.15;overflow-wrap:anywhere;word-break:break-word;white-space:normal}.staff-summary-text{font-size:22px}
        /* Sidebar navigation: button-based navigation, no radio circles. */
        .sidebar-navigation-title{font-size:12px;color:var(--text);font-weight:700;margin:0 0 7px 0}
        [data-testid="stSidebar"] .stButton{margin:0!important}
        [data-testid="stSidebar"] .stButton>button{border-radius:9px!important;min-height:38px!important;padding:7px 10px!important;text-align:left!important;justify-content:flex-start!important;font-size:13px!important;font-weight:500!important;box-shadow:none!important}
        [data-testid="stSidebar"] .stButton>button p{font-size:13px!important;text-align:left!important;margin:0!important}
        [data-testid="stSidebar"] .stButton>button:hover{border-color:#cdd9e8!important;background:#f5f8fc!important}
        [data-testid="stSidebar"] .stButton>button[kind="primary"]{background:#edf4ff!important;border-color:#d5e5fb!important;color:#2563eb!important;font-weight:700!important}
        [data-testid="stSidebar"] .stButton>button[kind="primary"] p{color:#2563eb!important}
        /* Inspection status controls: compact circular ✓ / ✗ controls. */
        .inspection-asset-strip{display:flex;align-items:center;justify-content:space-between;gap:12px;background:var(--surface2);border:1px solid var(--border);border-radius:10px;padding:8px 12px;margin:8px 0 12px;box-sizing:border-box}
        .inspection-asset-name{font-weight:800;color:var(--text);font-size:15px}
        .inspection-status-legend{font-size:12px;white-space:nowrap}.inspection-status-legend .done{color:var(--green);font-weight:900}.inspection-status-legend .notdone{color:var(--red);font-weight:900}.inspection-status-legend .unrecorded{color:var(--muted);font-weight:900}
        .inspection-group-item{background:var(--surface);border:1px solid var(--border);border-radius:9px;padding:7px 8px;margin:3px 0;min-height:70px;box-sizing:border-box;box-shadow:var(--shadow);overflow:hidden}.inspection-group-item .inspection-item-name{font-size:11px;font-weight:800;color:var(--text);line-height:1.2;min-height:28px;display:flex;align-items:flex-start}.inspection-group-item [data-testid="stRadio"]{margin-top:2px}
        .inspection-row{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:6px 8px;margin:5px 0;box-sizing:border-box}
        [data-testid="stMain"] [data-testid="stRadio"] [role="radiogroup"]{gap:7px!important;flex-wrap:nowrap!important;justify-content:center!important}
        [data-testid="stMain"] [data-testid="stRadio"] [role="radiogroup"]>label{border:1px solid var(--border)!important;border-radius:50%!important;padding:0!important;width:30px!important;min-width:30px!important;height:30px!important;min-height:30px!important;cursor:pointer!important;background:var(--surface)!important;box-sizing:border-box!important;display:flex!important;align-items:center!important;justify-content:center!important;overflow:hidden!important}
        [data-testid="stMain"] [data-testid="stRadio"] [role="radiogroup"]>label>div:first-child{display:none!important}
        [data-testid="stMain"] [data-testid="stRadio"] [role="radiogroup"] input{display:none!important}
        [data-testid="stMain"] [data-testid="stRadio"] [role="radiogroup"]>label:nth-child(1):has(input:checked){color:var(--muted)!important;border-color:var(--border)!important;background:var(--surface2)!important;font-weight:900!important}
        [data-testid="stMain"] [data-testid="stRadio"] [role="radiogroup"]>label:nth-child(2):has(input:checked){color:var(--green)!important;border-color:var(--green)!important;background:rgba(22,166,106,.10)!important;font-weight:900!important}
        [data-testid="stMain"] [data-testid="stRadio"] [role="radiogroup"]>label:nth-child(3):has(input:checked){color:#fff!important;border-color:var(--red)!important;background:var(--red)!important;font-weight:900!important}
        [data-testid="stMain"] [data-testid="stRadio"] [role="radiogroup"]>label:nth-child(3):has(input:checked) p{color:#fff!important}
        [data-testid="stMain"] [data-testid="stRadio"] [role="radiogroup"] p{margin:0!important;font-size:17px!important;line-height:1!important;font-weight:900!important}
        [data-testid="stMain"] [data-testid="stTextInput"] input{font-size:12px!important}

        /* Main action buttons: preserve the full label without ellipses. */
        [data-testid="stMain"] [data-testid="stButton"]>button{
            white-space:normal!important;
            overflow:visible!important;
            text-overflow:clip!important;
            font-size:12px!important;
            line-height:1.2!important;
            padding:7px 8px!important;
            min-width:0!important;
            min-height:38px!important;
            height:auto!important;
            width:100%!important;
            border:1px solid var(--border)!important;
            border-radius:9px!important;
            background:var(--surface)!important;
            box-shadow:none!important;
            text-decoration:none!important;
            outline:none!important;
            transition:none!important;
        }
        /* Inspection labels wrap onto a second line when needed, rather than truncating. */
        .st-key-inspection_tabs [data-testid="stHorizontalBlock"]{
            gap:.8rem!important;
            align-items:stretch!important;
        }
        .st-key-inspection_tabs [data-testid="stButton"]>button{
            min-width:0!important;
            white-space:normal!important;
            overflow:visible!important;
            overflow-wrap:normal!important;
            word-break:normal!important;
            height:auto!important;
            min-height:40px!important;
            padding:7px 6px!important;
        }
        .st-key-inspection_tabs [data-testid="stButton"]>button p,
        .st-key-inspection_tabs [data-testid="stButton"]>button span,
        .st-key-inspection_tabs [data-testid="stButton"]>button div{
            white-space:normal!important;
            overflow:visible!important;
            text-overflow:clip!important;
            overflow-wrap:normal!important;
            word-break:normal!important;
            text-align:center!important;
            font-size:12px!important;
            line-height:1.2!important;
            margin:0!important;
        }
        [data-testid="stMain"] [data-testid="stButton"]>button p{
            white-space:normal!important;
            overflow:visible!important;
            text-overflow:clip!important;
            font-size:12px!important;
            line-height:1.2!important;
            text-decoration:none!important;
            margin:0!important;
        }
        [data-testid="stMain"] [data-testid="stButton"]>button:hover,
        [data-testid="stMain"] [data-testid="stButton"]>button:focus,
        [data-testid="stMain"] [data-testid="stButton"]>button:focus-visible,
        [data-testid="stMain"] [data-testid="stButton"]>button:active{
            text-decoration:none!important;
            box-shadow:none!important;
            outline:none!important;
            border-bottom:1px solid var(--border)!important;
        }

        .inspection-check-title{display:flex;align-items:center;justify-content:space-between;gap:10px;background:var(--surface2);border:1px solid var(--border);border-radius:9px;padding:7px 10px;margin:7px 0 5px;font-weight:800;color:var(--text);box-sizing:border-box}
        .inspection-check-hint{font-size:10px;color:var(--muted);font-weight:600;white-space:nowrap}
        .inspection-asset-box{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:7px 5px;text-align:center;font-weight:800;font-size:11px;color:var(--text);min-height:30px;box-sizing:border-box;margin-top:2px}
        .inspection-item-divider{height:1px;background:var(--border);margin:6px 0 3px}
        .inspection-single-box{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:8px 11px;margin:6px 0 9px;box-shadow:var(--shadow);display:flex;align-items:center;justify-content:space-between;gap:12px;box-sizing:border-box}.inspection-single-title{font-size:13px;font-weight:850;color:var(--text)}
        .inspection-single-item{background:var(--surface2);border:1px solid var(--border);border-radius:9px;padding:9px 12px;margin:7px 0 4px;font-weight:750;color:var(--text);box-sizing:border-box}.inspection-item-name{font-size:13px}
        .inspection-tabs-line{height:1px;background:var(--border);margin:8px 0 10px}
        /* Keep inspection process buttons separated; labels must stay within each tab. */
        [data-testid="stMain"] [data-testid="stHorizontalBlock"]:has(button[key^="inspection_tab_"]){
            gap:10px!important;
            align-items:stretch!important;
        }
        [data-testid="stMain"] [data-testid="stHorizontalBlock"]:has(button[key^="inspection_tab_"]) [data-testid="column"]{
            min-width:0!important;
        }
        [data-testid="stMain"] button[key^="inspection_tab_"]{
            min-width:0!important;
            width:100%!important;
            white-space:normal!important;
            overflow-wrap:anywhere!important;
            padding:6px 7px!important;
            font-size:11px!important;
            line-height:1.2!important;
        }
        [data-testid="stMain"] button[key^="inspection_tab_"] p{
            white-space:normal!important;
            overflow-wrap:anywhere!important;
            font-size:11px!important;
            line-height:1.2!important;
        }
        /* Selected inspection tab. */
        [data-testid="stMain"] [data-testid="stButton"]>button[kind="primary"]{
            border:1px solid #16a66a!important;
            background:rgba(22,166,106,.08)!important;
            color:#16a66a!important;
            font-weight:800!important;
        }
        /* Unselected tabs and action buttons are full clickable boxes, not underlined text. */
        [data-testid="stMain"] [data-testid="stButton"]>button[kind="secondary"]{
            background:var(--surface)!important;
            border:1px solid var(--border)!important;
            color:var(--text)!important;
            font-weight:600!important;
        }
        [data-testid="stMain"] [data-testid="stButton"]>button[kind="secondary"]:hover{
            border:1px solid #b8c7d9!important;
            color:var(--text)!important;
            background:var(--surface2)!important;
        }
        .inspection-status-legend .small-muted{font-weight:500}

        @media(max-width:900px){.top-title{font-size:23px}.mini-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.loss-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.solar-map{min-height:980px;aspect-ratio:1/1.15}.map-zone-title{font-size:10px;padding:4px 5px}.map-controls{width:92px;font-size:8px;max-width:24%}}
        </style>""",unsafe_allow_html=True)

def display_plant_name(plant):
    """Return the approved user-facing site label without changing internal codes."""
    code = (getattr(plant, "code", "") or "").strip().upper()
    if code == "HS1":
        return "HRN 1 Solar Power Plant"
    if code == "HS2":
        return "HRN 2 Solar Power Plant"
    return getattr(plant, "name", "") or ""


def display_site_code(plant):
    """Return the approved user-facing site code while preserving internal DB codes."""
    code = (getattr(plant, "code", "") or "").strip().upper()
    if code == "HS1":
        return "HRN1"
    if code == "HS2":
        return "HRN2"
    return code


def header(plant):
    st.markdown(
        f"""
        <div class="top-title">{display_plant_name(plant)} <span style="color:#16a66a;">Maintenance Dashboard</span></div>
        <div class="top-subtitle">
            Capacity {plant.capacity_mw:g} MW &nbsp; • &nbsp;
            {plant.inverter_count} Inverters &nbsp; • &nbsp;
            {plant.table_count} Tables &nbsp; • &nbsp;
            {plant.zone_count} Zones &nbsp; • &nbsp;
            <span class="good">● System Online</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def donut_chart(percent, label, key):
    import plotly.graph_objects as go

    fig = go.Figure(
        go.Pie(
            values=[percent, max(0, 100 - percent)],
            hole=0.72,
            textinfo="none",
            marker=dict(colors=["#16a66a", "#e7edf3"]),
        )
    )
    fig.add_annotation(
        text=f"<b>{percent:.0f}%</b><br><span style='font-size:12px'>{label}</span>",
        x=0.5, y=0.5, showarrow=False,
        font=dict(color="#14213d", size=22),
    )
    fig.update_layout(
        showlegend=False, margin=dict(l=0,r=0,t=0,b=0), height=170,
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False}, key=key)


# ============================================================
# Login
# ============================================================

LOGIN_BACKGROUND_FILE = "Solar Sunrise Over a Green Energy Farm.png"
LOGIN_LOGO_FILE = "vidullanka-logo.svg"

def _login_asset_uri(filename, mime_type):
    """Load a login-page asset from the same folder as app.py."""
    image_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    try:
        with open(image_path, "rb") as asset_file:
            encoded = base64.b64encode(asset_file.read()).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"
    except Exception:
        return ""

def _login_background_uri():
    return _login_asset_uri(LOGIN_BACKGROUND_FILE, "image/png")

def _login_logo_uri():
    return _login_asset_uri(LOGIN_LOGO_FILE, "image/svg+xml")

def _login_background_uri():
    """Load the supplied solar image from the same folder as app.py for deployment."""
    image_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), LOGIN_BACKGROUND_FILE)
    try:
        with open(image_path, "rb") as image_file:
            encoded = base64.b64encode(image_file.read()).decode("ascii")
        return f"data:image/png;base64,{encoded}"
    except Exception:
        return ""

def login_page():
    css()
    bg_uri = _login_background_uri()
    logo_uri = _login_logo_uri()
    if bg_uri:
        st.markdown(
            f"""<style>
            .stApp{{
                background-image:linear-gradient(90deg,rgba(255,255,255,.78) 0%,rgba(255,255,255,.62) 38%,rgba(247,250,253,.28) 65%,rgba(247,250,253,.92) 100%),url('{bg_uri}');
                background-size:cover;
                background-position:center center;
                background-attachment:fixed;
            }}
            [data-testid="stHeader"]{{background:transparent!important}}
            [data-testid="stAppViewContainer"]{{background:transparent!important}}
            [data-testid="stMainBlockContainer"]{{max-width:1420px!important;padding-top:1.8rem!important;padding-bottom:2rem!important}}
            [data-testid="stColumn"]:has(.login-card-anchor){{background:rgba(255,255,255,.94);border:1px solid rgba(214,226,240,.95);border-radius:22px;padding:28px 30px 24px;box-shadow:0 18px 50px rgba(24,58,100,.16);backdrop-filter:blur(7px);align-self:flex-start}}
            .login-logo{{width:225px;max-width:72%;height:auto;display:block;margin:2px 0 4px}}
            .login-brand-sub{{font-size:13px;color:#315b83;margin-top:2px;font-weight:600;text-shadow:0 1px 2px rgba(255,255,255,.75)}}
            .login-hero-title{{font-size:clamp(38px,4.2vw,62px);font-weight:850;line-height:1.02;color:#102e57;margin-top:4.2rem;max-width:720px;text-shadow:0 2px 7px rgba(255,255,255,.8)}}
            .login-hero-sub{{font-size:19px;color:#315b83;font-weight:700;margin-top:16px;max-width:620px;text-shadow:0 1px 4px rgba(255,255,255,.85)}}
            .login-hero-points{{display:flex;gap:30px;margin-top:44px;max-width:720px}}
            .login-hero-point{{font-size:13px;color:#244c73;line-height:1.35;max-width:175px;font-weight:600;text-shadow:0 1px 3px rgba(255,255,255,.85)}}
            .login-hero-point b{{display:block;color:#102e57;font-size:16px;margin-bottom:5px;font-weight:850}}
            .login-form-title{{font-size:34px;font-weight:850;color:#102e57;margin-top:2px}}
            .login-form-sub{{font-size:13px;color:#67809b;margin-top:3px}}
            .login-accent{{height:4px;width:60px;border-radius:4px;background:#1687e8;margin:12px 0 22px}}
            [data-testid="stColumn"]:has(.login-card-anchor) [data-testid="stTabs"]{{margin-top:6px}}
            [data-testid="stTextInput"] input,[data-testid="stTextArea"] textarea{{color:#14213d!important;background:#fff!important;-webkit-text-fill-color:#14213d!important;font-size:16px!important;caret-color:#14213d!important}}
            /* Keep labels above compact input boxes, matching the requested sketch. */
            [data-testid="stColumn"]:has(.login-card-anchor) [data-testid="stTextInput"]{{
                border:0!important;
                box-shadow:none!important;
                background:transparent!important;
                margin-bottom:.35rem!important;
            }}
            /* Keep a green outline visible on every sign-in/create-account field, even before focus. */
            html body [data-testid="stColumn"]:has(.login-card-anchor) [data-testid="stTextInput"] [data-baseweb="input"] {{
                border:1.5px solid #111827!important;
                border-radius:7px!important;
                box-shadow:none!important;
                background:#fff!important;
                box-sizing:border-box!important;
                outline:0!important;
            }}
            html body [data-testid="stColumn"]:has(.login-card-anchor) [data-testid="stTextInput"] [data-baseweb="input"] > div {{
                border:0!important;
                box-shadow:none!important;
                background:transparent!important;
                box-sizing:border-box!important;
            }}
            [data-testid="stTextArea"] [data-baseweb="textarea"],
            [data-testid="stTextArea"] [data-baseweb="textarea"] > div,
            [data-testid="stNumberInput"] [data-baseweb="input"],
            [data-testid="stNumberInput"] [data-baseweb="input"] > div{{
                border:1.5px solid #16a765!important;
                border-radius:7px!important;
                box-shadow:inset 0 0 0 1px rgba(22,167,101,.10)!important;
                background:#fff!important;
                box-sizing:border-box!important;
            }}
            html body [data-testid="stColumn"]:has(.login-card-anchor) div[data-testid="stTextInput"] div[data-baseweb="input"]:not(:focus-within){{
                border-color:#111827!important;
            }}
            [data-testid="stTextInput"] input,
            [data-testid="stTextArea"] textarea,
            [data-testid="stNumberInput"] input{{
                border:0!important;
                outline:0!important;
                box-shadow:none!important;
                background:transparent!important;
            }}
            [data-testid="stTextInput"]:focus-within [data-baseweb="input"],
            [data-testid="stTextInput"]:focus-within [data-baseweb="input"] > div,
            [data-testid="stTextArea"]:focus-within [data-baseweb="textarea"],
            [data-testid="stTextArea"]:focus-within [data-baseweb="textarea"] > div,
            [data-testid="stNumberInput"]:focus-within [data-baseweb="input"],
            [data-testid="stNumberInput"]:focus-within [data-baseweb="input"] > div{{
                border-color:#07894b!important;
                box-shadow:inset 0 0 0 1px rgba(22,167,101,.18)!important;
            }}
            [data-testid="stTabs"] [role="tab"]{{color:#183b2c!important}}
            [data-testid="stTabs"] [role="tab"][aria-selected="true"]{{border-bottom-color:#16a765!important;color:#087443!important}}
            [data-testid="stTextInput"] input::placeholder{{color:#718096!important;opacity:1!important}}
            /* Compact sign-in and create-account fields: label above a 34px box. */
            [data-testid="stColumn"]:has(.login-card-anchor) [data-testid="stTextInput"]{{
                height:auto!important;min-height:0!important;margin-bottom:.35rem!important;
            }}
            [data-testid="stColumn"]:has(.login-card-anchor) [data-testid="stTextInput"] label{{
                display:block!important;font-size:12px!important;margin-bottom:3px!important;line-height:14px!important;
            }}
            [data-testid="stColumn"]:has(.login-card-anchor) [data-testid="stTextInput"] [data-baseweb="input"]{{
                height:40px!important;min-height:40px!important;max-height:40px!important;
                padding:0 8px!important;box-sizing:border-box!important;align-items:center!important;
                border:2px solid #111827!important;border-radius:7px!important;background:#fff!important;
                box-shadow:none!important;outline:none!important;
            }}
            [data-testid="stColumn"]:has(.login-card-anchor) [data-testid="stTextInput"] [data-baseweb="input"] > div{{
                height:36px!important;min-height:36px!important;max-height:36px!important;
                padding:0!important;box-sizing:border-box!important;align-items:center!important;
                border:0!important;box-shadow:none!important;background:transparent!important;
            }}
            [data-testid="stColumn"]:has(.login-card-anchor) [data-testid="stTextInput"] input{{
                height:36px!important;min-height:36px!important;max-height:36px!important;
                padding:0!important;font-size:16px!important;line-height:20px!important;box-sizing:border-box!important;
                border:0!important;outline:0!important;box-shadow:none!important;background:transparent!important;
            }}
            [data-testid="stMainBlockContainer"]{{padding-top:1rem!important}}
            .login-form-title,.login-form-sub,[data-testid="stMain"] h1,[data-testid="stMain"] h2{{font-family:Arial,Helvetica,sans-serif!important;letter-spacing:normal!important}}
            [data-testid="stMain"] label,[data-testid="stMain"] p{{color:#14213d}}
            
            /* Persistent black borders on login and account fields before focus. */
            html body [data-testid="stColumn"]:has(.login-card-anchor) [data-testid="stTextInput"] [data-baseweb="input"],
            html body [data-testid="stColumn"]:has(.login-card-anchor) [data-testid="stTextInput"] div[data-baseweb="input"],
            html body [data-testid="stColumn"]:has(.login-card-anchor) [data-testid="stTextInput"] [data-baseweb="input-container"]{{
                border:2px solid #111827!important;border-radius:7px!important;
                background:#eaf5ff!important;box-shadow:inset 0 0 0 1px rgba(17,24,39,.06)!important;outline:none!important;
                min-height:40px!important;box-sizing:border-box!important;
            }}
            html body [data-testid="stColumn"]:has(.login-card-anchor) [data-testid="stTextInput"] [data-baseweb="input"] > div{{
                border:0!important;background:transparent!important;box-shadow:none!important;
            }}
            html body [data-testid="stColumn"]:has(.login-card-anchor) [data-testid="stTextInput"]:not(:focus-within) [data-baseweb="input"]{{
                border:2px solid #6b9fc9!important;background:#eaf5ff!important;
            }}
            html body [data-testid="stColumn"]:has(.login-card-anchor) [data-testid="stTextInput"] input{{
                border:0!important;background:transparent!important;box-shadow:none!important;outline:none!important;
            }}
            /* Strong, persistent full-box borders for Email, Password and
               Supervisor Account fields, including before the user types. */
            html body [data-testid="stColumn"]:has(.login-card-anchor) [data-testid="stTextInput"] [data-baseweb="input"],
            html body [data-testid="stColumn"]:has(.login-card-anchor) [data-testid="stTextInput"] [data-baseweb="input-container"],
            html body [data-testid="stColumn"]:has(.login-card-anchor) [data-testid="stTextInput"] div[role="presentation"] {{
                border:2px solid #16a765!important;
                border-top:2px solid #16a765!important;
                border-bottom:2px solid #16a765!important;
                border-left:2px solid #16a765!important;
                border-right:2px solid #16a765!important;
                border-radius:7px!important;
                background:#ffffff!important;
                box-shadow:none!important;
                box-sizing:border-box!important;
                min-height:42px!important;
            }}
            html body [data-testid="stColumn"]:has(.login-card-anchor) [data-testid="stTextInput"] [data-baseweb="input"] > div {{
                border:0!important;background:transparent!important;box-shadow:none!important;
            }}
            html body [data-testid="stColumn"]:has(.login-card-anchor) [data-testid="stTextInput"] input {{
                color:#14213d!important;-webkit-text-fill-color:#14213d!important;
                caret-color:#14213d!important;background:transparent!important;
            }}
            html body [data-testid="stColumn"]:has(.login-card-anchor) [data-testid="stTextInput"]:not(:focus-within) [data-baseweb="input"] {{
                border:2px solid #16a765!important;
            }}
            /* Login card and fields: pale blue fill with visible borders even when empty. */
            html body [data-testid="stColumn"]:has(.login-card-anchor) {{
                background:#f0f8ff!important;
                border:1px solid #c4def3!important;
                border-radius:22px!important;
                padding:26px 28px!important;
                box-sizing:border-box!important;
            }}
            html body [data-testid="stColumn"]:has(.login-card-anchor) [data-testid="stTextInput"] [data-baseweb="input"],
            html body [data-testid="stColumn"]:has(.login-card-anchor) [data-testid="stTextInput"] [data-baseweb="input-container"] {{
                background:#eaf5ff!important;
                border:2px solid #6b9fc9!important;
                border-radius:7px!important;
                min-height:42px!important;
                box-sizing:border-box!important;
            }}
            html body [data-testid="stColumn"]:has(.login-card-anchor) [data-testid="stTextInput"] input {{
                background:transparent!important;
                color:#14213d!important;
                -webkit-text-fill-color:#14213d!important;
            }}
            @media(max-width:900px){{.login-logo{{width:190px}}.login-hero-title{{margin-top:2rem;font-size:38px}}.login-hero-points{{gap:14px;margin-top:25px}}[data-testid="stColumn"]:has(.login-card-anchor){{margin-top:20px;padding:22px}}}}
            </style>""",
            unsafe_allow_html=True,
        )

    db = db_session()
    plants = db.query(Plant).filter(Plant.active == True).order_by(Plant.name).all()

    left, right = st.columns([1.18, 0.82], gap="large")

    with left:
        st.markdown(
            f"""
            <img class="login-logo" src="{logo_uri}" alt="VIDULLANKA">
            <div class="login-hero-title">Solar Operation and<br>Maintenance Platform</div>
            <div class="login-hero-sub">Monitor &nbsp; • &nbsp; Maintain &nbsp; • &nbsp; Sustain</div>
            <div class="login-hero-points">
                <div class="login-hero-point"><b>📊 Monitor</b>Track performance in real-time</div>
                <div class="login-hero-point"><b>🔧 Maintain</b>Ensure reliability and uptime</div>
                <div class="login-hero-point"><b>🌿 Sustain</b>A cleaner tomorrow with renewable energy</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with right:
        st.markdown(
            """
            <div class="login-card-anchor"></div>
            <div class="login-form-title">Sign In</div>
            <div class="login-form-sub">Access your solar plant management system</div>
            <div class="login-accent"></div>
            """,
            unsafe_allow_html=True,
        )
        tab1, tab2 = st.tabs(["🔐 Sign In", "📝 Create Account"])

        with tab1:
            st.subheader("Sign in")
            email = st.text_input("Email", key="login_email")
            password = st.text_input("Password", type="password", key="login_password")

            if st.button("Sign In", type="primary", use_container_width=True):
                user = db.query(User).filter(User.email == email.strip().lower()).first()
                if not user or not user.active:
                    st.error("Account not found or inactive.")
                elif not verify_password(password, user.password_hash):
                    st.error("Incorrect email or password.")
                else:
                    st.session_state.user = user
                    st.session_state.selected_plant_id = user.plant_id
                    st.rerun()

        with tab2:
            st.subheader("Create an account")
            st.caption("Use your Vidullanka work email. Access level and plant scope are assigned from the registered email address.")
            r1, r2 = st.tabs(["👷 Supervisor Account", "🧑‍💻 Engineer / Administrator Account"])

            def registration_form(account_kind, title, prefix):
                st.markdown(f"### {title}")
                name = st.text_input("Full name", key=f"{prefix}_name")
                reg_email = st.text_input("Vidullanka work email", key=f"{prefix}_email")
                reg_password = st.text_input("Create password", type="password", key=f"{prefix}_password")
                confirm = st.text_input("Confirm password", type="password", key=f"{prefix}_confirm")
                if st.button(f"Create {title}", key=f"{prefix}_create", use_container_width=True):
                    email_value = reg_email.strip().lower()
                    if not name.strip() or not email_value or not reg_password:
                        st.error("Complete all required fields.")
                    elif reg_password != confirm:
                        st.error("Passwords do not match.")
                    elif len(reg_password) < 10:
                        st.error("Use at least 10 characters for a stronger password.")
                    elif db.query(User).filter(User.email == email_value).first():
                        st.error("An account with this email already exists. Please sign in or contact an administrator.")
                    else:
                        if account_kind == "supervisor":
                            role = "supervisor"
                            plant_code = PLANT_SUPERVISOR_EMAILS.get(email_value)
                            if email_value in PLANT_SUPERVISOR_EMAILS:
                                plant_id = _plant_id_for_code(db, plant_code)
                                if not plant_id:
                                    st.error(f"The {plant_code} plant is not configured yet. Ask an administrator to add it before registering this account.")
                                    return
                            else:
                                plant_id = None
                        else:
                            role = MANAGEMENT_ROLE_BY_EMAIL.get(email_value)
                            if not role:
                                st.error("This email is not on the Engineer / Administrator access list. Use your registered Vidullanka work email or contact the system administrator.")
                                return
                            plant_id = None
                        db.add(User(full_name=name.strip(), email=email_value, password_hash=hash_password(reg_password), role=role, plant_id=plant_id, active=True, approved=True))
                        db.commit()
                        scope = f" Assigned plant: {plant_code}." if account_kind == "supervisor" and email_value in PLANT_SUPERVISOR_EMAILS else (" Access: all active plants." if role in ("admin", "engineer") else " Plant assignment is required before using plant features.")
                        st.success(f"{title} created successfully as {role.title()}. You can sign in immediately.{scope}")

            with r1:
                registration_form("supervisor", "Supervisor Account", "reg_supervisor")
                st.caption("The MTR, MDP, HS1 and ORK in-charge emails are automatically assigned to their matching plant. Other supervisor accounts need a plant assignment from an administrator.")
            with r2:
                registration_form("management", "Engineer / Administrator Account", "reg_management")
                st.caption("Role is assigned from the registered email list; users cannot promote themselves to Administrator.")

    db.close()


# ============================================================
# Notifications
# ============================================================

def notification_center(db, user):
    unread = db.query(Notification).filter(
        Notification.user_id == user.id, Notification.is_read == False
    ).count()
    label = f"🔔 Notifications ({unread})" if unread else "🔔 Notifications"
    with st.expander(label, expanded=False):
        notifications = db.query(Notification).filter(
            Notification.user_id == user.id
        ).order_by(Notification.created_at.desc()).limit(30).all()
        if not notifications:
            st.caption("No notifications.")
            return
        if st.button("Mark all as read", key="mark_all_notifications_top"):
            for n in notifications:
                n.is_read = True
            db.commit(); st.rerun()
        for n in notifications:
            icon = "🔴" if n.notification_type == "RED_ALERT" else "📅"
            cls = "bad" if n.severity == "High" else "warn"
            st.markdown(
                f'<div class="status-tile"><b>{icon} {n.title}</b><br><span class="small-muted">{n.message.replace(chr(10), "<br>")}</span><br><span class="{cls}">{n.created_at}</span></div>',
                unsafe_allow_html=True,
            )
            if not n.is_read and st.button("Mark as read", key=f"read_{n.id}"):
                n.is_read = True; db.commit(); st.rerun()

# ============================================================
# Sidebar
# ============================================================

def top_nav(db, user):
    """Render the sidebar using clickable buttons instead of radio controls."""
    plants = allowed_plants(db, user)
    # Keep DPV (Ranna) as the final item in the site selector.
    plants = sorted(
        plants,
        key=lambda p: (
            "DPV" in f"{getattr(p, 'code', '')} {getattr(p, 'name', '')}".upper(),
            display_plant_name(p).casefold(),
        ),
    )
    if not plants:
        st.sidebar.markdown('<div class="sidebar-brand">Solar Maintenance</div>', unsafe_allow_html=True)
        st.sidebar.error("No plant is assigned to this account. Ask an administrator to assign a plant.")
        if st.sidebar.button("🚪 Sign out", key="sidebar_signout_unassigned"):
            st.session_state.clear(); st.rerun()
        return None, None

    plant_options = {display_plant_name(p): p.id for p in plants}
    current_id = st.session_state.get("selected_plant_id") or (user.plant_id if user.role not in ("admin", "engineer") else plants[0].id)
    current_name = next((n for n, pid in plant_options.items() if pid == current_id), plants[0].name)

    st.markdown(f'<div class="top-user" style="text-align:left">{user.full_name} • {user.role.title()}</div>', unsafe_allow_html=True)

    with st.sidebar:
        st.markdown('<div class="sidebar-brand">Solar Maintenance</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="sidebar-sub">{user.full_name} • {user.role.title()}</div>', unsafe_allow_html=True)
        selected_name = st.selectbox("Plant", list(plant_options.keys()), index=list(plant_options.keys()).index(current_name), key="top_plant")
        st.session_state.selected_plant_id = plant_options[selected_name]
        st.divider()
        st.caption("🟢 System Online")

    if user.role == "supervisor":
        pages = ["🏠 Overview", "🧹 Panel Cleaning", "🌿 Grass Cutting"]
    else:
        pages = ["🏠 Overview", "🧹 Panel Cleaning", "🌿 Grass Cutting", "🔧 Maintenance Inspections", "⚠️ Repair & Maintenance", "📋 Activity Log"]
    if user.role in ("admin", "engineer"):
        pages += ["📊 Reports", "✅ Approvals & Sign-off", "⏰ Reminders"]
    if user.role == "engineer":
        pages += ["👥 Users"]
    if user.role == "admin":
        pages += ["👥 Users", "⚙️ Admin"]

    # No radio buttons/circles.  Each navigation item is a normal Streamlit
    # button; the active page uses the primary button style like the reference UI.
    current_page = st.session_state.get("top_navigation_page", pages[0])
    if current_page not in pages:
        current_page = pages[0]
        st.session_state.top_navigation_page = current_page

    with st.sidebar:
        st.divider()
        st.markdown('<div class="sidebar-navigation-title">Navigation</div>', unsafe_allow_html=True)
        for idx, nav_page in enumerate(pages):
            if st.button(
                nav_page,
                key=f"sidebar_nav_{idx}",
                use_container_width=True,
                type="primary" if nav_page == current_page else "secondary",
            ):
                st.session_state.top_navigation_page = nav_page
                st.rerun()

        st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)
        if st.button("🚪 Sign out", use_container_width=True, key="sidebar_signout_bottom"):
            st.session_state.clear()
            st.rerun()

    reminder_key = f"_reminders_checked_{st.session_state.selected_plant_id}_{date.today().isoformat()}"
    if user.role == "admin" and not st.session_state.get(reminder_key):
        send_due_reminders(db, st.session_state.selected_plant_id)
        st.session_state[reminder_key] = True

    return current_page, st.session_state.selected_plant_id

# ============================================================
# Dashboard
# ============================================================
def render_solar_layout(db, plant, task_date):
    """Render a live schematic solar layout; each table is green/red from saved status."""
    assets = asset_list(plant, "Table")
    panel_records = status_map(db, plant.id, "Panel Cleaning", task_date)
    grass_records = status_map(db, plant.id, "Grass Cutting", task_date)
    completed_panel = {a for a, r in panel_records.items() if r.status == "Completed"}
    completed_grass = {a for a, r in grass_records.items() if r.status == "Completed"}

    # Arrange up to 10 zones in a 3-column schematic. The fourth row leaves
    # enough bottom space for Zone 10 and the legend never covers a zone.
    positions = [
        (3, 16, 29, 17), (35.5, 16, 29, 17), (68, 16, 29, 17),
        (3, 36, 29, 17), (35.5, 36, 29, 17), (68, 36, 29, 17),
        (3, 56, 29, 17), (35.5, 56, 29, 17), (68, 56, 29, 17),
        (3, 76, 29, 17),
    ]
    zones = max(1, plant.zone_count)
    per_zone = (len(assets) + zones - 1) // zones
    html = [
        '<div class="solar-map">',
        '<div class="solar-map-title">🛰️ Solar Plant Map • Live Status</div>',
        '<div class="map-controls"><b>Live Status</b><span style="color:#16e879">● GREEN = Completed</span><br><span style="color:#ff4e59">● RED = Not Completed</span><br>Table = panel cleaning<br>Border = grass cutting</div>',
        '<div class="solar-map-legend"><span class="legend-dot legend-green"></span>Completed &nbsp;&nbsp; <span class="legend-dot legend-red"></span>Not Completed</div>'
    ]
    for z in range(zones):
        items = assets[z*per_zone:(z+1)*per_zone]
        if not items:
            continue
        left, top, width, height = positions[z] if z < len(positions) else (3 + (z % 3)*32.5, 12 + (z//3)*20, 29, 17)
        zone_id = f"Z-{z+1:02d}"
        grass_done = zone_id in completed_grass
        zone_color = "#16e879" if grass_done else "#ff4e59"
        cells = ''.join(
            f'<span class="map-table {"map-green" if a in completed_panel else "map-red"}" title="{a}: {"Completed" if a in completed_panel else "Pending / Pending"}"></span>'
            for a in items
        )
        done = sum(1 for a in items if a in completed_panel)
        pct = done / len(items) * 100 if items else 0
        grass_label = "Completed" if grass_done else "Pending"
        html.append(
            f'<div class="map-zone" style="left:{left}%;top:{top}%;width:{width}%;height:{height}%;border-color:{zone_color}">'
            f'<div class="map-zone-title" style="background:{zone_color};color:#07131f">Zone {z+1} • Grass {grass_label}</div>'
            f'<div class="map-tables">{cells}</div>'
            f'<div style="color:#fff;font-size:11px;margin-top:6px;text-shadow:0 1px 2px #000">Panel cleaning: {done}/{len(items)} • {pct:.0f}%</div></div>'
        )
    html.append('</div>')
    st.markdown(''.join(html), unsafe_allow_html=True)

def satellite_table_map_page(db, user, plant, embedded=False, task_date=None, show_metrics=True, map_mode="both"):
    """Show the same satellite photo with status overlays limited to the current page.

    map_mode='both' shows panel-cleaning fills and grass-cutting zone borders;
    'panel' shows only panel-cleaning status; 'grass' shows only grass-cutting status.
    """
    import plotly.graph_objects as go
    from PIL import Image

    # A page can render the same plant/map mode more than once (for example,
    # Overview cards plus a detailed map). Give every chart instance a distinct
    # Streamlit identity to prevent StreamlitDuplicateElementId errors.
    # Stable chart identity lets Streamlit reuse the rendered chart across reruns.
    chart_instance = "embedded" if embedded else "standalone"
    chart_token = "stable"

    if not embedded:
        header(plant)
        st.markdown("## Solar Plant Map")
        st.caption("Each solar-table strip is coloured by panel-cleaning status. Zone outlines show grass-cutting status.")

    if task_date is None:
        task_date = st.date_input("Satellite status date", value=date.today(), key=f"satellite_map_date_{plant.id}")
    # Plant-specific satellite photos. The same selected photo is used on
    # Overview, Panel Cleaning, and Grass Cutting; other plants retain HR1.jpeg.
    plant_code = str(getattr(plant, "code", "") or "").strip().upper()
    # Resolve plant identity from both the database code and display name. Some
    # existing database rows use a descriptive name rather than the short code.
    plant_identity = " ".join([
        plant_code,
        str(getattr(plant, "name", "") or "").strip().upper(),
        str(getattr(plant, "display_name", "") or "").strip().upper(),
    ])
    if "DPV" in plant_identity or "RANNA" in plant_identity:
        plant_code = "DPV"
    elif "ORK" in plant_identity:
        plant_code = "ORK"
    elif "HRN 1" in plant_identity or "HRN1" in plant_identity or "HS1" in plant_identity:
        plant_code = "HRN1"
    elif "HRN 2" in plant_identity or "HRN2" in plant_identity or "HS2" in plant_identity:
        plant_code = "HRN2"
    satellite_files = {
        "HS1": "HRN1.jpg",
        "HRN1": "HRN1.jpg",
        "HRN": "HRN1.jpg",
        "HS2": "HRN2.jpg",
        "HRN2": "HRN2.jpg",
        "DPV": "DPV Image.jpg",
        "ORK": "ORK.jpg",
        "MDP": "MDP view .png",
        "MTR": "MTR view .png",
        "MTR-I": "MTR view .png",
        "MTR-II": "MTR view .png",
    }
    # ORK Panel Cleaning keeps its dedicated panel layout; Grass Cutting uses
    # the separate supplied aerial reference without added table/zone overlays.
    # DPV uses the same supplied clean satellite image for both map modes.
    ork_grass_reference = plant_code == "ORK" and map_mode == "grass"
    dpv_reference = plant_code == "DPV"
    if ork_grass_reference:
        selected_satellite = "Aerial Solar Farm Zone Map.png"
    elif plant_code == "DPV" and map_mode == "grass":
        selected_satellite = "Color-Coded Solar Farm Zones.png"
    else:
        selected_satellite = satellite_files.get(plant_code, "HR1.jpeg")

    image_candidates = [
        os.path.join(os.path.dirname(__file__), "assets", selected_satellite),
        os.path.join(os.path.dirname(__file__), selected_satellite),
        # Backward-compatible fallback if an older deployment only has HR1.jpeg.
        *([] if (ork_grass_reference or dpv_reference) else [
            os.path.join(os.path.dirname(__file__), "assets", "HR1.jpeg"),
            os.path.join(os.path.dirname(__file__), "HR1.jpeg"),
        ]),
    ]
    image_path = next((candidate for candidate in image_candidates if os.path.exists(candidate)), None)
    if not image_path:
        st.error(f"Satellite image not found: {selected_satellite}. Ensure the image is included in the assets folder.")
        return

    rotate_hrn2_map = plant_code in {"HS2", "HRN2"}
    with Image.open(image_path) as im:
        if rotate_hrn2_map:
            # Rotate the HRN 2 satellite image 90 degrees anti-clockwise.
            rotated = im.convert("RGB").rotate(90, expand=True)
            if map_mode == "grass":
                # Grass-cutting view: crop away the outer ground/road margins so
                # the panel-array area is enlarged and fills the zone map.
                rw, rh = rotated.size
                rotated = rotated.crop((
                    int(rw * 0.10), int(rh * 0.12),
                    int(rw * 0.90), int(rh * 0.88),
                ))
            img_width, img_height = rotated.size
            image_buffer = io.BytesIO()
            rotated.save(image_buffer, format="PNG")
            image_bytes = image_buffer.getvalue()
            image_mime = "image/png"
        else:
            img_width, img_height = im.size
            with open(image_path, "rb") as image_file:
                image_bytes = image_file.read()
            image_mime = "image/png" if image_path.lower().endswith(".png") else "image/jpeg"
    encoded = base64.b64encode(image_bytes).decode("ascii")
    image_uri = f"data:{image_mime};base64,{encoded}"

    if ork_grass_reference or dpv_reference:
        # ORK Grass Cutting uses its separate clean aerial photo without added
        # status overlays. DPV uses its supplied aerial photo for both work maps,
        # without guessed table/zone overlays.
        import plotly.graph_objects as go
        clean_fig = go.Figure()
        clean_fig.add_layout_image(dict(
            source=image_uri, xref="x", yref="y", x=0, y=img_height,
            sizex=img_width, sizey=img_height, sizing="stretch", layer="below"
        ))
        if dpv_reference:
            # DPV: show 15 zones on grass map and table-status markers on panel map.
            grass_records = status_map(db, plant.id, "Grass Cutting", task_date)
            zone_assets = asset_list(plant, "Zone")
            # Keep every DPV grass-zone marker inside the visible solar-array
            # footprint (not on the access road or the buildings at the right).
            # Normalized centre points are aligned to the 15 actual coloured
            # zone regions in Color-Coded Solar Farm Zones.png. Coordinates are
            # relative to the source image, not the stretched display size.
            zone_centres = [
                (.175, .295),  # Z1 — upper-left red zone
                (.292, .295),  # Z2 — upper yellow zone
                (.434, .295),  # Z3 — upper green zone
                (.609, .270),  # Z4 — upper blue zone
                (.765, .190),  # Z5 — top-right purple zone
                (.169, .500),  # Z6 — middle-left orange zone
                (.291, .500),  # Z7 — middle turquoise zone
                (.417, .500),  # Z8 — middle magenta zone
                (.535, .500),  # Z9 — middle blue zone
                (.638, .475),  # Z10 — middle-right green zone
                (.170, .690),  # Z11 — lower-left purple zone
                (.317, .690),  # Z12 — lower blue zone
                (.452, .655),  # Z13 — lower-centre yellow zone
                (.374, .755),  # Z14 — lower-middle turquoise zone; keep marker inside its own zone
                (.593, .620),  # Z15 — lower-right pink zone
            ]
            if map_mode == "grass":
                dx, dy, dtext = [], [], []
                for idx, (cx, cy) in enumerate(zone_centres[:len(zone_assets)]):
                    asset = zone_assets[idx]
                    rec = grass_records.get(asset)
                    done = bool(rec and rec.status == "Completed")
                    dx.append(cx * img_width); dy.append((1-cy) * img_height)
                    dtext.append(f"{asset} — Grass cutting: {'Completed' if done else 'Pending'}")
                clean_fig.add_trace(go.Scatter(
                    x=dx, y=dy, mode="markers+text",
                    marker=dict(size=25, color=["#16e879" if "Completed" in t else "#ff2638" for t in dtext],
                                line=dict(color="#ffffff", width=2)),
                    text=[f"Z{i+1}" for i in range(len(dx))], textposition="middle center",
                    textfont=dict(size=9, color="#ffffff"), hovertext=dtext, hoverinfo="text",
                    showlegend=False))
            else:
                # DPV panel cleaning: draw each table as a horizontal red/green
                # strip within the real solar-array footprints, matching the
                # grouped layout in the supplied DPV hand sketch. The strip
                # geometry is normalized to the DPV satellite photo.
                panel_records = status_map(db, plant.id, "Panel Cleaning", task_date)
                # DPV panel cleaning: use the real rectangular panel banks in the
                # satellite image. Each bank is divided into its actual 10/12
                # visible rows; each displayed row represents its share of the
                # underlying table records. A row turns green only when every
                # table assigned to it is completed. Thus a whole rectangle is
                # green only after all of its rows are completed.
                panel_records = status_map(db, plant.id, "Panel Cleaning", task_date)
                dpv_blocks = [
                    (.145, .300, .255, .425, 12), (.265, .300, .375, .425, 12),
                    (.385, .300, .495, .425, 12), (.505, .300, .615, .425, 12),
                    (.625, .300, .755, .425, 10),
                    (.145, .475, .255, .610, 10), (.265, .475, .375, .610, 12),
                    (.385, .475, .495, .610, 12), (.505, .475, .615, .610, 12),
                    (.625, .475, .755, .610, 12),
                ]
                table_assets = asset_list(plant, "Table")
                # Split all table records among the visual rows without losing
                # any records. A row can represent multiple tables where needed.
                total_visual_rows = sum(block[4] for block in dpv_blocks)
                row_asset_counts = [len(table_assets) // total_visual_rows] * total_visual_rows
                for extra_idx in range(len(table_assets) % total_visual_rows):
                    row_asset_counts[extra_idx] += 1

                asset_cursor = 0
                visual_row_cursor = 0
                for bank_idx, (left, top, right, bottom, row_count) in enumerate(dpv_blocks):
                    row_h = (bottom - top) / row_count
                    for row_idx in range(row_count):
                        asset_count = row_asset_counts[visual_row_cursor]
                        row_assets = table_assets[asset_cursor:asset_cursor + asset_count]
                        asset_cursor += asset_count
                        visual_row_cursor += 1
                        # A visual row is complete only when all records mapped
                        # to it are completed. Pending rows remain red.
                        row_done = bool(row_assets) and all(
                            (rec := panel_records.get(asset)) is not None and rec.status == "Completed"
                            for asset in row_assets
                        )
                        y_top = top + row_idx * row_h
                        y_bottom = min(bottom, y_top + row_h * .94)
                        clean_fig.add_shape(
                            type="rect",
                            x0=left * img_width, x1=right * img_width,
                            y0=(1 - y_bottom) * img_height,
                            y1=(1 - y_top) * img_height,
                            line=dict(color="#d9e7f5", width=0.7),
                            # Dark, high-contrast colours remain visible over
                            # the blue solar panels while preserving image detail.
                            fillcolor="rgba(0,112,96,0.82)" if row_done else "rgba(139,30,63,0.88)",
                            layer="above",
                        )
                        labels = ", ".join(row_assets)
                        clean_fig.add_trace(go.Scatter(
                            x=[((left + right) / 2) * img_width],
                            y=[(1 - (y_top + y_bottom) / 2) * img_height],
                            mode="markers",
                            marker=dict(size=1, opacity=0),
                            hovertext=[f"DPV rectangle {bank_idx + 1}, row {row_idx + 1} — "
                                       f"{'Completed' if row_done else 'Pending'}"
                                       + (f" — {labels}" if labels else "")],
                            hoverinfo="text", showlegend=False,
                        ))
        if ork_grass_reference:
            # Show a large, high-contrast status dot in each of ORK's 10 zones.
            # Positions follow the visible zone layout on the supplied aerial image.
            grass_records = status_map(db, plant.id, "Grass Cutting", task_date)
            zone_assets = asset_list(plant, "Zone")
            # Circle positions are aligned to the corresponding coloured zone,
            # rather than the old positions which placed several labels in the
            # neighbouring zone. Coordinates are normalized to the ORK image.
            zone_centres = [
                (.505, .105),  # Z1 — top-centre zone
                (.315, .245),  # Z2 — upper-left zone
                (.590, .285),  # Z3 — upper-centre/right zone
                (.365, .420),  # Z4 — middle-left zone
                (.590, .485),  # Z5 — positioned inside the blue Zone 5 area
                (.355, .610),  # Z6 — moved inside the red/orange lower-left zone
                (.665, .625),  # Z7 — inside the green centre-right zone
                (.355, .815),  # Z8 — inside the blue lower-left zone
                (.600, .875),  # Z9 — inside the pink bottom-centre zone
                (.870, .850),  # Z10 — bottom-right zone
            ]
            dot_x, dot_y, dot_colors, dot_text = [], [], [], []
            for idx, (cx, cy) in enumerate(zone_centres):
                if idx >= len(zone_assets):
                    break
                zone_name = zone_assets[idx]
                rec = grass_records.get(zone_name)
                done = bool(rec and rec.status == "Completed")
                dot_x.append(cx * img_width)
                dot_y.append((1 - cy) * img_height)
                dot_colors.append("#00e676" if done else "#ff2638")
                dot_text.append(f"{zone_name} — Grass cutting: {'Completed' if done else 'Pending'}")
            clean_fig.add_trace(go.Scatter(
                x=dot_x, y=dot_y, mode="markers+text",
                marker=dict(size=19, color=dot_colors, line=dict(color="#ffffff", width=2)),
                text=[f"Z{i+1}" for i in range(len(dot_x))],
                textposition="middle center", textfont=dict(size=8, color="#07131f"),
                hovertext=dot_text, hoverinfo="text", showlegend=False,
            ))
        clean_fig.update_xaxes(range=[0, img_width], visible=False, fixedrange=True, constrain="domain")
        clean_fig.update_yaxes(range=[0, img_height], visible=False, fixedrange=True)
        clean_fig.update_layout(
            height=245 if embedded else 560, margin=dict(l=0, r=0, t=0, b=0),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            showlegend=False, hovermode=False,
        )
        st.plotly_chart(
            clean_fig,
            use_container_width=True,
            config={"displayModeBar": False},
            key=f"satellite_map_{plant.id}_{map_mode}_{'embedded' if embedded else 'standalone'}_{'dpv' if dpv_reference else 'ork'}_clean_{chart_instance}_{chart_token}",
        )
        return

    assets = asset_list(plant, "Table")
    panel_records = status_map(db, plant.id, "Panel Cleaning", task_date)
    completed = {a for a, r in panel_records.items() if r.status == "Completed"}
    grass_records = status_map(db, plant.id, "Grass Cutting", task_date)
    zone_assets = asset_list(plant, "Zone")
    completed_grass = {a for a, r in grass_records.items() if r.status == "Completed"}
    pending_count = max(0, len(assets) - len(completed))

    # Plant-specific array footprints. Coordinates are normalized to each image,
    # so the same status logic follows the real panel rows on each satellite photo.
    # Each block is (left, top, right, bottom, split_direction).
    # split_direction="x" colours vertical table strips; "y" colours horizontal strips.
    plant_code = str(getattr(plant, "code", "") or "").strip().upper()
    if plant_code in {"HS1", "HRN1", "HRN", "HRN 1"}:
        # HRN 1 layout matches the first Python version: four actual
        # solar-table groups traced from the supplied HRN1 satellite image.
        # Each table row is coloured from top to bottom ("y").
        block_specs = [
            # Trace/table order starts at the upper-right solar-panel group,
            # so Trace 0 is on the right-side top array as requested.
            (.738, .081, .992, .438, "y"),  # Group 1: upper-right array
            (.512, .114, .738, .438, "y"),  # Group 2: upper-middle array
            (.258, .139, .512, .847, "y"),  # Group 3: tall centre array
            (.014, .201, .258, .889, "y"),  # Group 4: left array, including lower rows
        ]
    elif plant_code in {"HS2", "HRN2"}:
        # HRN 2 panel-cleaning layout: eight separate panel groups matching
        # the supplied sketch. Groups 1-4 have 9, 6, 7 and 8 rows; groups
        # 5-8 have 10 rows each. These are panel-cleaning overlays only.
        # Keep the red row overlays inside the panel-array footprints.
        block_specs = [
            (.075, .18, .245, .49, "y"),  # Group 1: 9 rows
            (.275, .30, .405, .49, "y"),  # Group 2: 6 rows
            (.435, .28, .575, .49, "y"),  # Group 3: 7 rows
            (.615, .22, .785, .49, "y"),  # Group 4: 8 rows
            (.075, .53, .245, .91, "y"),  # Group 5: 10 rows
            (.275, .53, .405, .91, "y"),  # Group 6: 10 rows
            (.435, .53, .575, .91, "y"),  # Group 7: 10 rows
            (.615, .53, .785, .91, "y"),  # Group 8: 10 rows
        ]
    elif plant_code == "MDP":
        # MDP panel rows follow the supplied hand-drawn site plan: the large
        # upper-left bank, smaller banks across the upper/middle rows, and the
        # three lower banks. Coordinates are normalized to the MDP satellite.
        # Counts are read directly from the supplied sketch. The first bank is
        # a true 11-row x 5-column grid (55 cells), not 55 full-width stripes.
        block_specs = [
            (.035, .135, .485, .335, "grid55"),  # 11 rows x 5 columns
            (.500, .170, .585, .300, "y"),       # 9 rows
            (.610, .200, .700, .380, "y"),       # 11 rows
            (.715, .200, .785, .285, "y"),       # 5 rows
            (.800, .200, .895, .380, "y"),       # 11 rows
            (.905, .200, .985, .380, "y"),       # 11 rows
            (.925, .405, .985, .535, "y"),       # 8 rows
            (.025, .510, .115, .565, "y"),       # 2 rows
            (.145, .500, .220, .660, "y"),       # 11 rows
            (.245, .500, .320, .660, "y"),       # 11 rows
            (.345, .500, .420, .660, "y"),       # 11 rows
            (.435, .500, .500, .610, "y"),       # 7 rows
            (.515, .500, .575, .610, "y"),       # 7 rows
            (.590, .500, .650, .610, "y"),       # 7 rows
            (.665, .500, .725, .610, "y"),       # 7 rows
            (.740, .500, .800, .590, "y"),       # 5 rows
            (.810, .500, .870, .590, "y"),       # 5 rows
            (.880, .500, .940, .590, "y"),       # 5 rows
            (.150, .720, .245, .900, "y"),       # 9 rows
            (.270, .720, .365, .940, "y"),       # 11 rows
            (.390, .720, .470, .985, "y"),       # 12 rows
        ]
    elif plant_code == "MTR":
        # MTR panel-cleaning footprints follow the hand-drawn plan and the
        # actual satellite arrays. The three short 5-row groups and three
        # single-row strips stay over the lower-left panel strips. The four
        # tall groups are moved down and left so they cover the long central /
        # right-hand solar arrays rather than the upper panel rows.
        block_specs = [
            (.090, .555, .205, .625, "y"),  # 5 rows: first short bank
            (.225, .555, .345, .625, "y"),  # 5 rows: second short bank
            (.365, .555, .485, .625, "y"),  # 5 rows: third short bank
            (.105, .645, .245, .675, "y"),  # 1 row: short strip
            (.275, .645, .420, .675, "y"),  # 1 row: short strip
            (.185, .690, .335, .720, "y"),  # 1 row: lower short strip
            (.455, .395, .525, .825, "y"),  # 15 rows: long array
            (.535, .345, .615, .900, "y"),  # 20 rows: long array
            (.625, .330, .705, .965, "y"),  # 23 rows: long array
            (.715, .355, .805, .910, "y"),  # 21 rows: long array
            (.830, .555, .885, .625, "y"),  # 5 rows: final short bank
        ]
    elif plant_code == "DPV":
        # DPV panel-cleaning layout: two clear rows of five panel banks,
        # positioned over the visible solar tables and inside the array boundary.
        block_specs = [
            (.145, .300, .255, .425, "y"), (.265, .300, .375, .425, "y"),
            (.385, .300, .495, .425, "y"), (.505, .300, .615, .425, "y"),
            (.625, .300, .755, .425, "y"),
            (.145, .475, .255, .610, "y"), (.265, .475, .375, .610, "y"),
            (.385, .475, .495, .610, "y"), (.505, .475, .615, .610, "y"),
            (.625, .475, .755, .610, "y"),
        ]
    elif plant_code == "ORK":
        # ORK panel-cleaning footprints follow the user's hand-drawn layout.
        # These seven groups cover all visible panel rows; their row counts are
        # 1 + 6 + 7 + 13 + 14 + 2 + 9 = 52 tables.
        block_specs = [
            (.095, .555, .235, .590, "y"),  # Group 1: far-left single row
            (.160, .265, .300, .405, "y"),  # Group 2: left group, 6 rows
            (.300, .205, .430, .395, "y"),  # Group 3: upper-middle group, 7 rows
            (.430, .200, .565, .720, "y"),  # Group 4: central tall group, 13 rows
            (.565, .185, .705, .720, "y"),  # Group 5: right tall group, 14 rows
            (.420, .085, .555, .155, "y"),  # Group 6: top group, 2 rows
            (.700, .500, .835, .895, "y"),  # Group 7: lower-right group, 9 rows
        ]
    else:
        # Preserve the original map overlay geometry for other sites.
        block_specs = [
            (.195, .011, .363, .444, "y"), (.364, .183, .525, .486, "y"),
            (.508, .081, .664, .472, "y"), (.651, .010, .838, .431, "y"),
            (.238, .542, .402, .986, "y"), (.379, .514, .539, .986, "y"),
            (.520, .456, .684, .978, "y"), (.664, .431, .855, .931, "y"),
        ]

    # The HRN 2 satellite image is rotated 90 degrees anti-clockwise. Rotate
    # its normalized panel footprints by the same amount and swap the split axis,
    # so panel-cleaning rows stay aligned with the image on both work pages.
    if plant_code in {"HS2", "HRN2"}:
        block_specs = [
            (top, 1 - right, bottom, 1 - left, "x" if direction == "y" else "y")
            for left, top, right, bottom, direction in block_specs
        ]

    # Convert normalized footprints into pixel coordinates. The displayed image
    # and status overlays use the same orientation.
    blocks = []
    for left, top, right, bottom, direction in block_specs:
        blocks.append((
            (left * img_width, top * img_height),
            (right * img_width, top * img_height),
            (left * img_width, bottom * img_height),
            (right * img_width, bottom * img_height),
            direction,
        ))
    if plant_code in {"HS1", "HRN1", "HRN", "MDP"}:
        # Horizontal panel rows follow the visible row count/vertical span in
        # the satellite layout, rather than the overall footprint area.
        # This keeps MDP rows aligned with the solar-panel banks in its image.
        weights = [max(1, int((bottom - top) * 1000))
                   for _left, top, _right, bottom, _direction in block_specs]
    else:
        weights = [max(1, int((r-l) * (b-t) * (1.6 if direction == "x" else 1.0)))
                   for l, t, r, b, direction in block_specs]
    if assets:
        if plant_code in {"HS2", "HRN2"} and len(block_specs) == 8 and len(assets) == 70:
            # HRN 2 panel-cleaning layout follows the supplied sketch exactly:
            # four upper groups with 9, 6, 7, and 8 rows, followed by four
            # lower groups with 10 rows each (70 rows total).
            counts = [9, 6, 7, 8, 10, 10, 10, 10]
        elif plant_code == "ORK" and len(block_specs) == 7 and len(assets) == 52:
            # Match the user's ORK table-row counts exactly.
            counts = [1, 6, 7, 13, 14, 2, 9]
        elif plant_code == "MDP" and len(block_specs) == 21 and len(assets) == 220:
            # Exact MDP sketch counts: 11x5 main grid, then the remaining banks.
            counts = [55, 9, 11, 5, 11, 11, 8, 2, 11, 11, 11, 7, 7, 7, 7, 5, 5, 5, 9, 11, 12]
        elif plant_code == "MTR" and len(block_specs) == 11 and len(assets) == 102:
            # Exact MTR sketch counts: 5, 5, 5, 1, 1, 1, 15, 20, 23, 21, 5.
            counts = [5, 5, 5, 1, 1, 1, 15, 20, 23, 21, 5]
        elif plant_code == "DPV" and len(block_specs) == 10 and len(assets) == 366:
            # Keep all 366 table indicators distributed across the two-row sketch.
            counts = [37, 37, 37, 37, 37, 37, 36, 36, 36, 36]
        else:
            total_weight = sum(weights)
            counts = [len(assets) * w // total_weight for w in weights]
            for i in range(len(assets) - sum(counts)):
                counts[i % len(counts)] += 1
    else:
        counts = [0] * len(blocks)

    scale_x, scale_y = 1.0, 1.0
    fig = go.Figure()
    fig.add_layout_image(dict(source=image_uri, xref="x", yref="y", x=0, y=img_height,
                              sizex=img_width, sizey=img_height, sizing="stretch", layer="below"))

    # Zone borders are coloured only on the Overview and Grass Cutting pages.
    # HRN 1 has 10 zones, following the same four array groups as the first
    # Python version. Other sites keep their existing geometry.
    zone_count = max(1, len(zone_assets))
    zone_blocks = blocks
    if plant_code in {"HS1", "HRN1", "HRN"} and zone_assets:
        # HRN 1 grass-cutting zones follow the same four table groups as
        # the first Python version: four zones in each of the two tall groups,
        # then one zone for each of the two shorter upper groups.
        zone_rects = [
            (.014, .201, .258, .347),  # Zone 1: left group, upper
            (.017, .347, .258, .521),  # Zone 2: left group, upper-middle
            (.021, .521, .258, .718),  # Zone 3: left group, lower-middle
            (.024, .718, .258, .889),  # Zone 4: left group, lower
            (.258, .159, .512, .336),  # Zone 5: centre group, upper
            (.258, .336, .512, .493),  # Zone 6: centre group, upper-middle
            (.258, .493, .512, .692),  # Zone 7: centre group, lower-middle
            (.258, .692, .512, .847),  # Zone 8: centre group, lower
            (.512, .146, .738, .438),  # Zone 9: upper-middle group
            (.738, .111, .992, .438),  # Zone 10: upper-right group
        ]
        zone_blocks = [
            ((l * img_width, t * img_height), (r * img_width, t * img_height),
             (l * img_width, b * img_height), (r * img_width, b * img_height), "y")
            for l, t, r, b in zone_rects
        ]
    elif plant_code in {"HS2", "HRN2"} and zone_assets:
        # HRN 2 grass-cutting zones follow the user's 10-zone sketch:
        # zones 1-4 cover the left-side panel groups, zones 5-8 cover the
        # right-side panel groups, and zones 9-10 cover the two open-ground
        # sections on the far right. Coordinates are normalized to HRN2.jpg.
        # Non-overlapping zones are defined in the *displayed* (rotated)
        # image coordinate system. Each zone has a shared edge at most; no
        # zone rectangle crosses into another zone.
        zone_rects = [
            (.015, .020, .465, .250),  # Zone 1: upper-left section
            (.015, .250, .465, .480),  # Zone 2: upper-middle-left section
            (.015, .480, .465, .715),  # Zone 3: lower-middle-left section
            (.015, .715, .465, .985),  # Zone 4: lower-left section
            (.465, .020, .805, .250),  # Zone 5: upper-centre section
            (.465, .250, .805, .480),  # Zone 6: upper-middle-centre section
            (.465, .480, .805, .715),  # Zone 7: lower-middle-centre section
            (.465, .715, .805, .985),  # Zone 8: lower-centre section
            (.805, .020, .995, .500),  # Zone 9: upper-right section
            (.805, .500, .995, .985),  # Zone 10: lower-right section
        ]
        zone_blocks = [
            ((l * img_width, t * img_height), (r * img_width, t * img_height),
             (l * img_width, b * img_height), (r * img_width, b * img_height), "y")
            for l, t, r, b in zone_rects
        ]
    elif plant_code in {"MDP", "MTR", "MTR-I", "MTR-II"} and zone_assets:
        # MDP and MTR: use a clean, non-overlapping 5-column x 2-row
        # zone layout. Shared edges meet exactly, so zone outlines cannot
        # cross over one another as in the previous overlapping footprints.
        zone_rects = [
            (col * 0.20 + 0.008, 0.035, (col + 1) * 0.20 - 0.008, 0.495)
            for col in range(5)
        ] + [
            (col * 0.20 + 0.008, 0.505, (col + 1) * 0.20 - 0.008, 0.965)
            for col in range(5)
        ]
        zone_blocks = [
            ((l * img_width, t * img_height), (r * img_width, t * img_height),
             (l * img_width, b * img_height), (r * img_width, b * img_height), "y")
            for l, t, r, b in zone_rects
        ]
    elif plant_code == "ORK" and zone_assets:
        # ORK grass-cutting divisions follow the supplied marked-up satellite:
        # two left zones, two centre-left zones, central upper/lower zones,
        # upper/middle right zones, plus lower-right and bottom-right zones.
        zone_rects = [
            (.025, .245, .270, .535),  # Zone 1: upper-left
            (.025, .535, .270, .835),  # Zone 2: lower-left
            (.270, .190, .505, .430),  # Zone 3: upper centre-left
            (.270, .430, .505, .650),  # Zone 4: lower centre-left
            (.420, .010, .575, .430),  # Zone 5: top-centre
            (.420, .430, .575, .900),  # Zone 6: lower-centre
            (.575, .105, .785, .430),  # Zone 7: upper-right
            (.575, .430, .785, .690),  # Zone 8: mid-right
            (.785, .430, .965, .735),  # Zone 9: lower-right
            (.785, .735, .985, .925),  # Zone 10: bottom-right
        ]
        zone_blocks = [
            ((l * img_width, t * img_height), (r * img_width, t * img_height),
             (l * img_width, b * img_height), (r * img_width, b * img_height), "y")
            for l, t, r, b in zone_rects
        ]
        # Keep the existing panel-table geometry separate from these grass zones.
    # Grass-cutting zone borders belong only on Overview and Grass Cutting maps.
    # Do not draw these borders at all on Panel Cleaning: they are unrelated to
    # panel-cleaning rows and were appearing as dark lines over the panel map.
    if map_mode in ("both", "grass"):
        for block_idx, block in enumerate(zone_blocks):
            if not zone_assets:
                break
            left_top, right_top, left_bottom, right_bottom, _direction = block
            zone_idx = min(zone_count - 1, round(block_idx * (zone_count - 1) / max(1, len(zone_blocks) - 1)))
            zone_name = zone_assets[zone_idx]
            grass_done = zone_name in completed_grass
            points = [left_top, right_top, right_bottom, left_bottom, left_top]
            border_color = "#13c982" if grass_done else "#ff4e59"
            fig.add_trace(go.Scatter(
                x=[x * scale_x for x, y in points],
                y=[img_height - y * scale_y for x, y in points],
                mode="lines", line=dict(color=border_color, width=3),
                hoverinfo="text",
                text=[f"{zone_name} — Grass cutting: {'Completed' if grass_done else 'Pending'}"] * len(points),
                showlegend=False,
            ))

    # A table is represented by a narrow polygon matching one real panel row.
    # On the Grass Cutting map, omit table polygons entirely: even transparent
    # fills retain dark outline strokes and made black borders appear over zones.
    asset_index = 0
    for block_idx, (block, count) in enumerate(zip(blocks, counts)):
        if map_mode == "grass":
            break
        if count <= 0:
            continue
        top_left, top_right, bottom_left, bottom_right, direction = block
        # MDP's first bank is 11 rows by 5 columns, exactly as drawn in the sketch.
        if plant_code == "MDP" and block_idx == 0 and direction == "grid55":
            inset = 0.045
            for row_idx in range(11):
                for col_idx in range(5):
                    if asset_index >= len(assets):
                        break
                    asset = assets[asset_index]
                    asset_index += 1
                    fx0, fx1 = col_idx / 5 + inset / 5, (col_idx + 1) / 5 - inset / 5
                    fy0, fy1 = row_idx / 11 + inset / 11, (row_idx + 1) / 11 - inset / 11
                    def interp(a, b, f):
                        return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)
                    tl = interp(interp(top_left, top_right, fx0), interp(bottom_left, bottom_right, fx0), fy0)
                    tr = interp(interp(top_left, top_right, fx1), interp(bottom_left, bottom_right, fx1), fy0)
                    br = interp(interp(top_left, top_right, fx1), interp(bottom_left, bottom_right, fx1), fy1)
                    bl = interp(interp(top_left, top_right, fx0), interp(bottom_left, bottom_right, fx0), fy1)
                    polygon = [tl, tr, br, bl, tl]
                    is_done = asset in completed
                    fill_color = "#ff4e59" if map_mode in ("both", "panel") else "rgba(0,0,0,0)"
                    hover_text = f"{asset}<br>Panel cleaning: {'Completed' if is_done else 'Pending'}"
                    fig.add_trace(go.Scatter(x=[x for x, y in polygon], y=[img_height-y for x, y in polygon],
                        mode="lines", fill="toself", fillcolor=fill_color,
                        line=dict(color="#ff4e59" if map_mode in ("both", "panel") else "#062337", width=1),
                        hoverinfo="text", text=[hover_text] * len(polygon), showlegend=False))
            continue
        # A small gap between strips keeps the satellite roads and row gaps visible.
        inset = 0.045
        for row_idx in range(count):
            if asset_index >= len(assets):
                break
            asset = assets[asset_index]
            asset_index += 1
            f0 = row_idx / count + inset / count
            f1 = (row_idx + 1) / count - inset / count

            def interp(a, b, f):
                return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)

            if direction == "x":
                # Vertical table strips: divide the footprint from left to right.
                tl = interp(top_left, top_right, f0)
                tr = interp(top_left, top_right, f1)
                br = interp(bottom_left, bottom_right, f1)
                bl = interp(bottom_left, bottom_right, f0)
            else:
                # Horizontal table strips: divide the footprint from top to bottom.
                tl = interp(top_left, bottom_left, f0)
                tr = interp(top_right, bottom_right, f0)
                br = interp(top_right, bottom_right, f1)
                bl = interp(top_left, bottom_left, f1)
            polygon = [tl, tr, br, bl, tl]
            is_done = asset in completed
            if map_mode in ("both", "panel"):
                # MTR panel-cleaning layout: every table remains red until the
                # requested design is changed; completion status does not recolour it.
                if plant_code in {"MDP", "MTR"}:
                    fill_color = "#ff4e59"
                else:
                    fill_color = "#16c982" if is_done else "#ff4e59"
                hover_text = f"{asset}<br>Panel cleaning: {'Completed' if is_done else 'Pending'}"
                if map_mode == "both" and zone_assets:
                    zone_idx = min(zone_count - 1, int(asset_index * zone_count / max(1, len(assets))))
                    zone_name = zone_assets[zone_idx]
                    grass_done = zone_name in completed_grass
                    hover_text += f"<br>{zone_name} grass cutting: {'Completed' if grass_done else 'Pending'}"
            else:
                # Grass Cutting page: panel rows are only a neutral map reference;
                # no panel-cleaning status is coloured or shown.
                fill_color = "rgba(0,0,0,0)"
                hover_text = f"{zone_name}<br>Grass cutting: {'Completed' if grass_done else 'Pending'}"
            fig.add_trace(go.Scatter(
                x=[x * scale_x for x, y in polygon],
                y=[img_height - y * scale_y for x, y in polygon],
                mode="lines", fill="toself",
                fillcolor=fill_color,
                line=dict(color="#ff4e59" if plant_code == "MTR" and map_mode in ("both", "panel") else "#062337", width=1),
                hoverinfo="text",
                text=[hover_text] * len(polygon),
                showlegend=False,
            ))

    fig.update_xaxes(range=[0, img_width], visible=False, fixedrange=True, constrain="domain")
    fig.update_yaxes(range=[0, img_height], visible=False, fixedrange=True)
    fig.update_layout(
        # Fixed compact height keeps Panel Cleaning and Grass Cutting maps identical in size.
        height=245 if embedded else 560, margin=dict(l=0, r=0, t=0, b=0),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        showlegend=False, hovermode="closest",
    )
    # Show the map toolbar only on hover and keep only the useful export control;
    # this prevents the dark toolbar from covering the satellite image.
    st.plotly_chart(
        fig,
        use_container_width=True,
        config={
            "displayModeBar": "hover",
            "displaylogo": False,
            "scrollZoom": True,
            "modeBarButtonsToRemove": [
                "zoom2d", "pan2d", "select2d", "lasso2d", "zoomIn2d",
                "zoomOut2d", "autoScale2d", "resetScale2d"
            ],
        },
        key=f"satellite_map_{plant.id}_{map_mode}_{'embedded' if embedded else 'standalone'}_{chart_instance}_{chart_token}",
    )
    if show_metrics:
        green_col, red_col, grass_col = st.columns([1, 1, 1.15], gap="small")
        green_col.metric("Panel cleaning completed", len(completed))
        red_col.metric("Panel cleaning pending", pending_count)
        grass_col.metric("Grass-cutting zones completed", len(completed_grass))
    if not embedded:
        st.caption("Save progress in Panel Cleaning and Grass Cutting. The satellite table colours and zone borders update from the saved records for the selected date.")


def operations_staff_section(db, user, plant, embedded=False):
    """Show/edit per-site operating headcount and contacts; values persist in Neon."""
    profile = db.query(PlantOperationsProfile).filter(PlantOperationsProfile.plant_id == plant.id).first()
    if profile is None:
        defaults = {
            "HRN1": ("HRN1 In-Charge", "Mr. Prasanna Kottege", "Mr. Mahela Wanigasooriya"),
            "HRN2": ("HRN2 In-Charge", "Mr. Prasanna Kottege", "Mr. Mahela Wanigasooriya"),
            "MTR": ("MTR In-Charge", "Mr. Prasanna Kottege", "Mr. Mahela Wanigasooriya"),
            "MDP": ("MDP In-Charge", "Mr. Prasanna Kottege", "Mr. Mahela Wanigasooriya"),
            "ORK": ("ORK In-Charge", "Mr. Prasanna Kottege", "Mr. Mahela Wanigasooriya"),
        }
        sup, reportee, hof = defaults.get(plant.code.upper(), ("", "", "Mr. Mahela Wanigasooriya"))
        profile = PlantOperationsProfile(plant_id=plant.id, operating_staff_count=0,
                                         supervisor_name=sup, engineer_name=reportee, hof_name=hof)
        db.add(profile)
        db.commit()
        db.refresh(profile)

    if not embedded:
        st.markdown("### 👷 Operating Staff & Site Contacts")
        st.caption("Headcount and contact details are site-specific. This section is maintained from User Management.")

    leadership = [
        ("Mr. Roshan Siriwardana", "Director – Operations"),
        ("Mr. Manchanayeke Upul", "General Manager – Operations"),
        ("Mr. Sampath Raweendra", "Chief Electrical Engineer"),
        ("Mr. Damitha Bandulawansha", "Assistant Manager – Operations"),
        ("Mr. Prasanna Kottege", "Electrical Engineer"),
        ("Mr. Mahela Wanigasooriya", "Mechanical Engineer"),
    ]
    with st.expander("Operations leadership roster", expanded=False):
        st.dataframe(pd.DataFrame(leadership, columns=["Name", "Position"]), use_container_width=True, hide_index=True)

    editable = user.role in ("admin", "engineer", "supervisor")
    with st.form(f"site_staff_profile_{plant.id}"):
        c1, c2 = st.columns([0.9, 1.35], gap="large")
        with c1:
            count = st.number_input("No. of operating staff members", min_value=0, max_value=10000,
                                    value=int(profile.operating_staff_count or 0), step=1, disabled=not editable)
            supervisor = st.text_input("Site supervisor / in-charge", value=profile.supervisor_name or "",
                                       placeholder="Enter supervisor name", disabled=not editable)
        with c2:
            reportee = st.text_input("Immediate Reportee", value=profile.engineer_name or "",
                                     placeholder="Enter immediate reportee", disabled=not editable)
            hof = st.text_input("HOF / mechanical contact", value=profile.hof_name or "Mr. Mahela Wanigasooriya",
                                disabled=not editable)
        submitted = st.form_submit_button("Save site staff details", use_container_width=True, disabled=not editable)
        if submitted:
            profile.operating_staff_count = int(count)
            profile.supervisor_name = supervisor.strip()
            profile.engineer_name = reportee.strip()
            profile.hof_name = hof.strip()
            profile.updated_at = datetime.utcnow()
            db.commit()
            st.success(f"Operating staff and contacts saved for {plant.name}.")
            st.rerun()

    c1, c2, c3 = st.columns([0.9, 1.15, 1.55], gap="large")
    c1.markdown(f'<div class="staff-summary-card"><div class="staff-summary-label">Operating staff</div><div class="staff-summary-value">{int(profile.operating_staff_count or 0)}</div></div>', unsafe_allow_html=True)
    c2.markdown(f'<div class="staff-summary-card"><div class="staff-summary-label">Supervisor / In-charge</div><div class="staff-summary-value staff-summary-text">{profile.supervisor_name or "Not set"}</div></div>', unsafe_allow_html=True)
    c3.markdown(f'<div class="staff-summary-card"><div class="staff-summary-label">Immediate Reportee</div><div class="staff-summary-value staff-summary-text">{profile.engineer_name or "Not set"}</div></div>', unsafe_allow_html=True)


def overview_context(db, user, plant):
    """Keep notifications available in a compact dropdown, not as large cards."""
    unread = db.query(Notification).filter(
        Notification.user_id == user.id,
        Notification.is_read == False,
        or_(Notification.plant_id == plant.id, Notification.plant_id == None),
    ).count()
    with st.expander(f"🔔 Notifications ({unread} unread) — show / hide", expanded=False):
        notifications = db.query(Notification).filter(
            Notification.user_id == user.id,
            or_(Notification.plant_id == plant.id, Notification.plant_id == None),
        ).order_by(Notification.created_at.desc()).limit(10).all()
        if not notifications:
            st.caption("No notifications for this plant.")
        else:
            if st.button("Mark all as read", key=f"overview_mark_all_{plant.id}"):
                db.query(Notification).filter(
                    Notification.user_id == user.id,
                    or_(Notification.plant_id == plant.id, Notification.plant_id == None),
                ).update({Notification.is_read: True}, synchronize_session=False)
                db.commit()
                st.rerun()
            for n in notifications:
                icon = "🔴" if n.notification_type == "RED_ALERT" else "📅"
                st.markdown(
                    f'<div class="overview-notification"><b>{icon} {n.title}</b>'
                    f'<div class="small-muted">{n.message}</div>'
                    f'<div class="small-muted">{n.created_at:%d %b %Y %H:%M}</div></div>',
                    unsafe_allow_html=True,
                )
                if not n.is_read and st.button("Mark as read", key=f"read_notification_{n.id}"):
                    n.is_read = True
                    db.commit()
                    st.rerun()


def render_plant_details_and_specs(db, plant):
    """Render site identity/contact details and the technical-specification table at the bottom."""
    profile = db.query(PlantOperationsProfile).filter(
        PlantOperationsProfile.plant_id == plant.id
    ).first()
    staff_count = int(profile.operating_staff_count or 0) if profile else 0
    supervisors = {
        "ORK": "Pasindu", "HS1": "Roshan", "HS2": "Roshan",
        "MTR": "Sudheera", "MDP": "Sansala", "HOF": "Not provided",
    }
    supervisor = (profile.supervisor_name.strip() if profile and profile.supervisor_name else "") or supervisors.get(plant.code, "Not provided")
    engineers = {
        "HS1": "Mr. Prasanna Kottege", "HS2": "Mr. Prasanna Kottege",
        "MTR": "Mr. Mahela Wanigasooriya", "MTR-I": "Mr. Mahela Wanigasooriya",
        "MTR-II": "Mr. Mahela Wanigasooriya", "MDP": "Mr. Mahela Wanigasooriya",
        "ORK": "Not provided", "HOF": "Mr. Mahela Wanigasooriya",
    }
    engineer = (profile.engineer_name.strip() if profile and profile.engineer_name else "") or engineers.get(plant.code, "Not provided")

    st.markdown("## Power Plant Details")
    detail_rows = [
        ("Plant", display_plant_name(plant)), ("Site code", display_site_code(plant)),
        ("AC Capacity", f"{plant.capacity_mw:g} MW"),
        ("Inverters", f"{plant.inverter_count}"), ("Solar tables", f"{plant.table_count}"),
        ("Zones", f"{plant.zone_count}"), ("Operating staff", str(staff_count)),
        ("Plant In-Charge / Supervisor", supervisor), ("Immediate Reportee", engineer),
    ]
    cols = st.columns(3)
    for i, (label, value) in enumerate(detail_rows):
        with cols[i % 3]:
            st.markdown(
                f'<div class="plant-detail-card"><div class="plant-detail-label">{label}</div>'
                f'<div class="plant-detail-value">{value}</div></div>',
                unsafe_allow_html=True,
            )

    # Technical values are populated only where supplied in the plant-specification sheet.
    specs = {
        "HS1": {"Power Plant": "HRN 1 (Horana)", "AC Capacity": "2 MW", "Panels": "535 Wp × 4,480 Nos", "Panel Size": "2285 mm × 1134 mm", "Cell Type": "Mono", "Inverters": "200 kW × 10 Nos", "Panel Connection for Inverter": "28 panels × 28 strings", "Table Arrangement": "56 panels × 80 tables", "Main Transformer": "2200 kVA, 33 kV/800 V", "Auxiliary Transformer": "10 kVA, 230 V/800 V", "Plant factor": "21%", "Project Company": "Vidulsolar (Pvt) Ltd", "Ownership": "100%", "Land Area": "8.99 acres", "Plant In-Charge": "Roshan"},
        "HS2": {"Power Plant": "HRN 2 (Horana)", "AC Capacity": "2 MW", "Panels": "615 Wp × 4,060 Nos", "Panel Size": "2382 mm × 1134 mm", "Cell Type": "N-Type TOPCon", "Inverters": "300 kW × 7 Nos", "Panel Connection for Inverter": "29 panels × 20 strings", "Table Arrangement": "58 panels × 70 tables", "Main Transformer": "2250 kVA, 33 kV/800 V", "Auxiliary Transformer": "5 kVA, 230 V/800 V", "Plant factor": "20%", "Project Company": "Vidulsolar (Pvt) Ltd", "Ownership": "100%", "Land Area": "6.5 acres", "Plant In-Charge": "Roshan"},
        "ORK": {"Power Plant": "ORK (Monaragala)", "AC Capacity": "0.88 MW", "Panels": "535 Wp × 1,870 Nos", "Panel Size": "2279 × 1134 mm", "Cell Type": "Mono", "Inverters": "100 kW × 8 Nos", "Panel Connection for Inverter": "18 panels × 13 strings", "Table Arrangement": "34 panels × 1 table + 36 panels × 51 tables", "Main Transformer": "1000 kVA, 33 kV/400 V", "Auxiliary Transformer": "NA", "Plant factor": "18%", "Project Company": "Orik Corporation (Pvt) Ltd", "Ownership": "87%", "Land Area": "6.67 acres", "Plant In-Charge": "Pasindu"},
        "MTR": {"Power Plant": "MTR (Matara)", "AC Capacity": "3 MW", "Panels": "615 Wp × 6,160 Nos", "Panel Size": "2382 × 1134 mm", "Cell Type": "N-Type TOPCon", "Inverters": "300 kW × 10 Nos", "Panel Connection for Inverter": "28 panels × 22 strings", "Table Arrangement": "56 panels × 110 tables", "Main Transformer": "3300 kVA, 33 kV/800 V", "Auxiliary Transformer": "5 kVA, 230 V/800 V", "Plant factor": "21.04%", "Project Company": "Vidul Matara Solar Power (Pvt) Ltd", "Ownership": "100%", "Land Area": "10.5 acres", "Plant In-Charge": "Sudheera"},
        "MDP": {"Power Plant": "MDP (Madampe)", "AC Capacity": "6 MW", "Panels": "615 Wp × 12,320 Nos", "Panel Size": "2382 × 1134 mm", "Cell Type": "N-Type TOPCon", "Inverters": "300 kW × 20 Nos", "Panel Connection for Inverter": "28 panels × 22 strings", "Table Arrangement": "56 panels × 220 tables", "Main Transformer": "6000 kVA, 33 kV/800 V", "Auxiliary Transformer": "50 kVA, 230 V/800 V", "Plant factor": "21.00%", "Project Company": "Vidulsolar (Pvt) Ltd", "Ownership": "100%", "Land Area": "20 acres", "Plant In-Charge": "Sansala"},
        "DPV": {"Power Plant": "DPV (Ranna)", "AC Capacity": "10 MW", "DC Capacity": "12810 kW", "DC-to-AC sizing ratio": "1.281", "Panels": "625 Wp × 20496 Nos", "Panel Size": "2382 × 1134 × 30 mm", "Cell Type": "N-type mono-crystalline", "Inverters": "300 kW × 34 Nos", "Panel Connection for Inverter": "28 panels × 22 strings", "Table Arrangement": "56 panels × 366 tables", "Main Transformer": "6600 kVA, 33 kV/800 V"},
    }
    # Resolve the specification table from both the database code and display name.
    # This supports canonical codes (HS1/HS2), older HRN codes, and labels such as
    # "HRN 1 Solar Power Plant" / "HRN 2 Solar Power Plant" without mixing sites.
    import re

    def normalize_site_key(value):
        return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())

    raw_code = getattr(plant, "code", "") or ""
    raw_name = getattr(plant, "name", "") or ""
    try:
        display_name = display_plant_name(plant) or ""
    except Exception:
        display_name = ""

    code_key = normalize_site_key(raw_code)
    name_key = normalize_site_key(raw_name)
    display_key = normalize_site_key(display_name)
    identity = " ".join((code_key, name_key, display_key))

    # Check HRN 2 first so it can never be mistaken for HRN 1.
    if (
        code_key in {"HS2", "HRN2"}
        or name_key in {"HS2", "HRN2", "HRN2HORANA", "HRN2SOLARPOWERPLANT"}
        or display_key in {"HS2", "HRN2", "HRN2HORANA", "HRN2SOLARPOWERPLANT"}
        or "HRN2" in identity
        or "HS2" in identity
        or "HORANAII" in identity
    ):
        spec_key = "HS2"
    elif (
        code_key in {"HS1", "HRN1", "HRN"}
        or name_key in {"HS1", "HRN1", "HRN1HORANA", "HRN1SOLARPOWERPLANT"}
        or display_key in {"HS1", "HRN1", "HRN1HORANA", "HRN1SOLARPOWERPLANT"}
        or "HRN1" in identity
        or "HS1" in identity
        or "HORANAI" in identity
        or code_key == "HRN"
    ):
        spec_key = "HS1"
    else:
        # Keep the existing plant specifications working for all other sites.
        normalized_spec_keys = {normalize_site_key(key): key for key in specs}
        spec_key = (
            normalized_spec_keys.get(code_key)
            or normalized_spec_keys.get(name_key)
            or normalized_spec_keys.get(display_key)
        )
        if spec_key is None:
            if "ORK" in identity:
                spec_key = "ORK"
            elif "MTR" in identity:
                spec_key = "MTR-I"
            elif "MDP" in identity:
                spec_key = "MDP"
            elif "DPV" in identity or "RANNA" in identity:
                spec_key = "DPV"

    spec = specs.get(spec_key, {}) if spec_key else {}

    st.markdown("## Technical Specifications")
    if spec:
        st.dataframe(pd.DataFrame([{"Specification": k, "Value": v} for k, v in spec.items()]),
                     use_container_width=True, hide_index=True)
    else:
        st.info("Technical specifications have not yet been provided for this site. Add them to the site specification configuration when available.")


def _overview_target_label(done, total, target_days, unit):
    remaining = max(0, total - done)
    if remaining == 0:
        return "Target complete"
    return f"Estimated target: {target_days} days / cycle" if unit == "cycle" else f"Estimated target: {target_days} days"


def _render_overview_card(title, icon, pct, done, total, target_text, map_renderer=None):
    st.markdown(
        f'<div class="overview-work-card"><div class="overview-work-title">{icon} {title}</div>'
        f'<div class="overview-work-percent">{pct:.0f}%</div>'
        f'<div class="small-muted">{done} / {total} completed</div>'
        f'<div class="overview-work-target">{target_text}</div></div>',
        unsafe_allow_html=True,
    )
    if map_renderer:
        map_renderer()


def overview_page(db, user, plant):
    header(plant)
    overview_context(db, user, plant)

    # Keep the existing date filters, but make the operational area the focus.
    f1, f2 = st.columns([1, 1])
    with f1:
        start_date = st.date_input("From", date.today() - timedelta(days=30), key="dash_start")
    with f2:
        end_date = st.date_input("To", date.today(), key="dash_end")
    if start_date > end_date:
        st.error("The start date cannot be after the end date.")
        return

    records = filtered_records(db, plant.id, start_date, end_date, None, "All")
    metrics = dashboard_metrics(db, plant, records, start_date, end_date)

    # Panel Cleaning and Grass Cutting are live work-status indicators.
    # Read the saved records for the selected end date so the Overview always
    # matches the completion status saved from those two maintenance pages.
    panel_pct, panel_done, panel_total = panel_cleaning_percent(db, plant.id, end_date)
    grass_pct, grass_done, grass_total = grass_cutting_percent(db, plant.id, end_date)
    metrics["panel_pct"] = panel_pct
    metrics["panel_done"] = panel_done
    metrics["panel_total"] = panel_total
    metrics["grass_pct"] = grass_pct
    metrics["grass_done"] = grass_done
    metrics["grass_total"] = grass_total

    repair_rows = db.query(RepairItem.asset_id, func.count(RepairItem.id)).filter(
        RepairItem.plant_id == plant.id, RepairItem.status != "Completed"
    ).group_by(RepairItem.asset_id).all()
    repair_counts = {a: int(c) for a, c in repair_rows}

    st.markdown("## Maintenance Productivity")
    st.caption("Compact operational view — cleaning, grass cutting and repair/maintenance status.")

    panel_col, grass_col, repair_col = st.columns(3, gap="medium")

    with panel_col:
        _render_overview_card(
            "Panel Cleaning", "", metrics["panel_pct"], metrics["panel_done"], metrics["panel_total"],
            _overview_target_label(metrics["panel_done"], metrics["panel_total"], PANEL_CLEANING_TARGET_DAYS, "cycle")
        )
        satellite_table_map_page(db, user, plant, embedded=True, task_date=end_date, show_metrics=False, map_mode="panel")
        st.progress(metrics["panel_pct"] / 100, text=f"Panel cleaning • {metrics['panel_pct']:.0f}%")

    with grass_col:
        _render_overview_card(
            "Grass Cutting", "", metrics["grass_pct"], metrics["grass_done"], metrics["grass_total"],
            _overview_target_label(metrics["grass_done"], metrics["grass_total"], GRASS_CUTTING_TARGET_DAYS, "cycle")
        )
        satellite_table_map_page(db, user, plant, embedded=True, task_date=end_date, show_metrics=False, map_mode="grass")
        st.progress(metrics["grass_pct"] / 100, text=f"Grass cutting • {metrics['grass_pct']:.0f}%")

    with repair_col:
        _render_overview_card(
            "Repair & Maintenance", "",
            100 if metrics["open_repairs"] == 0 else 0,
            plant.inverter_count - len([a for a in asset_list(plant, "Inverter") if repair_counts.get(a, 0)]),
            plant.inverter_count,
            _overview_target_label(0 if metrics["open_repairs"] else 1, 1, REPAIR_MAINTENANCE_TARGET_DAYS, "days")
        )
        st.markdown("**Inverter status**")
        inverter_items = []
        for inverter in asset_list(plant, "Inverter"):
            count = repair_counts.get(inverter, 0)
            if count:
                inverter_items.append(f'<div class="overview-inverter bad">🔴 <b>{inverter}</b> — {count} open issue(s)</div>')
            else:
                inverter_items.append(f'<div class="overview-inverter good">🟢 <b>{inverter}</b> — No issue</div>')
        st.markdown('<div class="overview-inverter-grid">' + ''.join(inverter_items) + '</div>', unsafe_allow_html=True)
        if metrics["open_repairs"]:
            st.error(f"{metrics['open_repairs']} active repair/maintenance item(s)")
        else:
            st.success("No repair maintenance items")

    # Recent Maintenance Activity intentionally removed from Overview.
    st.divider()
    render_plant_details_and_specs(db, plant)


MTR_EMPLOYEES = [
    "Sudheera Athukorala — Assistant Supervisor",
    "VIraj Samarasinghe",
    "Alaththuge Gunarathne",
    "Saman Kumara",
    "Chiwantha Dahanayake",
    "Minidu Gawesh",
]

def worker_options_for_plant(plant, db, first_names_only=False):
    """Return the configured worker list for the selected solar plant."""
    code = (plant.code or "").strip().upper()
    if code == "DPV":
        values = ["Harsha-Supervisor", "Nihal", "Nishantha", "Priyanka", "Saman", "Chinthaka"]
    elif code == "MTR":
        values = list(MTR_EMPLOYEES)
    else:
        # HRN 1 and HRN 2 use the requested supervisor label consistently.
        if code in {"HS1", "HRN1", "HRN", "HRN 1", "HS2", "HRN2", "HRN 2"}:
            supervisor_label = "Roshan - Supervisor"
        else:
            supervisor = (db.query(PlantOperationsProfile)
                          .filter(PlantOperationsProfile.plant_id == plant.id).first())
            supervisor_name = (supervisor.supervisor_name or "").strip() if supervisor else ""
            supervisor_label = f"{supervisor_name} — Supervisor" if supervisor_name else "Supervisor"
        values = [supervisor_label, "Worker 1", "Worker 2"]

    # Legacy records occasionally contain a single 'R'; do not offer it as a worker.
    values = [v.strip() for v in values if v and v.strip().upper() != "R"]
    # Grass Cutting uses this helper with first_names_only=True. Preserve the
    # complete supervisor label and numbered worker options in that dropdown.
    return list(dict.fromkeys(values))

def panel_cleaning_page(db, user, plant):
    header(plant)
    st.markdown("## Panel Cleaning")

    task_date = st.date_input("Work date", value=date.today(), key="panel_date")
    # Compact map — same size as the Grass Cutting map.
    satellite_table_map_page(db, user, plant, embedded=True, task_date=task_date, show_metrics=False, map_mode="panel")
    search = st.text_input("Search table", placeholder="T-01")

    assets = asset_list(plant, "Table")
    if search:
        assets = [a for a in assets if search.lower() in a.lower()]

    panel_records = status_map(db, plant.id, "Panel Cleaning", task_date)
    completed = sum(1 for r in panel_records.values() if r.status == "Completed")
    pct = round(completed / plant.table_count * 100, 1) if plant.table_count else 0

    st.progress(pct / 100, text=f"Cleaning progress: {pct:.1f}%")

    cols = st.columns(5)
    for i, asset in enumerate(assets):
        r = panel_records.get(asset)
        done = bool(r and r.status == "Completed")
        saved_worker_name = (r.remarks or "").strip() if r else ""
        if saved_worker_name.upper() == "R":
            saved_worker_name = ""

        with cols[i % 5]:
            st.markdown(f"**{asset}**  \n{'🟢 Completed' if done else '🔴 Pending'}")
            worker_options = worker_options_for_plant(plant, db)
            worker_choices = list(dict.fromkeys(([saved_worker_name] if saved_worker_name else []) + worker_options))
            worker_name = st.selectbox(
                "Worker / Supervisor", worker_choices,
                index=worker_choices.index(saved_worker_name) if saved_worker_name in worker_choices else 0,
                label_visibility="collapsed",
                key=f"panel_worker_name_{plant.id}_{asset}_{task_date}",
            ).strip()
            if st.button(
                "Mark Pending" if done else "Mark Completed",
                key=f"panel_{asset}_{task_date}",
                use_container_width=True,
            ):
                if not done and not worker_name:
                    st.warning(f"Enter the worker's name for {asset} before marking it completed.")
                else:
                    upsert_record(
                        db, plant.id, "Panel Cleaning", asset, task_date, user.id,
                        "Pending" if done else "Completed",
                        {"items": {"Panel/Table Cleaning": "Not Done" if done else "Done"}},
                        remarks="" if done else worker_name,
                    )
                    st.rerun()


# ============================================================
# Grass Cutting
# ============================================================

def grass_cutting_page(db, user, plant):
    header(plant)
    st.markdown("## 🌿 Grass Cutting")

    task_date = st.date_input("Work date", value=date.today(), key="grass_date")
    # Compact map — same size as the Panel Cleaning map.
    satellite_table_map_page(db, user, plant, embedded=True, task_date=task_date, show_metrics=False, map_mode="grass")
    grass_records = status_map(db, plant.id, "Grass Cutting", task_date)
    completed = sum(1 for r in grass_records.values() if r.status == "Completed")
    pct = round(completed / plant.zone_count * 100, 1) if plant.zone_count else 0

    st.progress(pct / 100, text=f"Grass cutting progress: {pct:.1f}%")

    cols = st.columns(5)
    for i, zone in enumerate(asset_list(plant, "Zone")):
        r = grass_records.get(zone)
        done = bool(r and r.status == "Completed")
        saved_worker_name = (r.remarks or "").strip() if r else ""
        if saved_worker_name.upper() == "R":
            saved_worker_name = ""

        with cols[i % 5]:
            st.markdown(f"**{zone}**  \n{'🟢 Completed' if done else '🔴 Pending'}")
            worker_options = worker_options_for_plant(plant, db, first_names_only=True)
            saved_first_name = saved_worker_name.split()[0] if saved_worker_name else ""
            worker_choices = list(dict.fromkeys(([saved_first_name] if saved_first_name and saved_first_name.upper() != "R" else []) + worker_options))
            worker_name = st.selectbox(
                "Worker / Supervisor", worker_choices,
                index=worker_choices.index(saved_first_name) if saved_first_name in worker_choices else 0,
                label_visibility="collapsed",
                key=f"grass_worker_name_{plant.id}_{zone}_{task_date}",
            ).strip()
            if st.button(
                "Mark Pending" if done else "Mark Completed",
                key=f"grass_{zone}_{task_date}",
                use_container_width=True,
            ):
                if not done and not worker_name:
                    st.warning(f"Enter the worker's name for {zone} before marking it completed.")
                else:
                    upsert_record(
                        db, plant.id, "Grass Cutting", zone, task_date, user.id,
                        "Pending" if done else "Completed",
                        {"items": {"Grass Cutting": "Not Done" if done else "Done"}},
                        remarks="" if done else worker_name,
                    )
                    st.rerun()


# ============================================================
# Maintenance inspections
# ============================================================

def _inspection_saved_item(old_record, item):
    """Return saved UI status and reason for one checklist item.

    A checklist item that has never been saved is deliberately kept as
    UNRECORDED.  It must not silently become RED/Not OK just because the
    inspection page was opened or saved.
    """
    if not old_record:
        return "UNRECORDED", ""
    details = parse_details(old_record)
    items = details.get("items", {}) if isinstance(details, dict) else {}
    if item not in items:
        return "UNRECORDED", ""

    saved = items.get(item)
    if saved in ("OK / Green", "GREEN", "Completed", "DONE"):
        status = "GREEN"
    elif saved in ("RED", "Not Done", "Failed"):
        status = "RED"
    else:
        status = "UNRECORDED"
    return status, str(items.get(f"{item}__reason", "") or "")


def _inspection_display_item(item):
    """Clean checklist labels for display only; preserve original database keys."""
    label = str(item or "").strip()
    if label.startswith("Outdoor -"):
        label = label[len("Outdoor -"):].strip()
    elif label.startswith("Indoor -"):
        label = label[len("Indoor -"):].strip()

    if label.startswith("CT "):
        label = "Current Transformers " + label[3:]
    elif label == "CT":
        label = "Current Transformers"
    return label


def _inspection_groups(category, assets):
    """Return compact visual groups without changing saved checklist keys."""
    cfg = PROCESS_CATEGORIES[category]

    if category == "Daily Inspection":
        one_inverter = [assets[0]] if assets else ["INV-01"]
        return [
            ("🔌 Inverter Checks", cfg["items"][:3], list(assets)),
            ("🛡️ Safety Checks", cfg["items"][3:], one_inverter),
        ]

    if category == "AC Inspection":
        return [
            ("🌤️ Outdoor Unit Checks", cfg["items"][:7], assets),
            ("❄️ Indoor Unit Checks", cfg["items"][7:], assets),
        ]

    # MDB and Switch Yard inspections use a single continuous checklist.
    # Category headers such as ACB, MCCB, Transformers, OCB, Outdoor and Indoor
    # are not rendered as separate sections.
    return [("Checklist", cfg["items"], assets)]


def _render_inspection_status(db, user, plant, category, task_date, item, item_index,
                              asset, old_record, values, reason_widgets):
    """Render one checklist status control and keep the three-state behaviour."""
    saved_status, saved_reason = _inspection_saved_item(old_record, item)
    status_options = ["UNRECORDED", "GREEN", "RED"]

    status = st.radio(
        "Status",
        status_options,
        index=status_options.index(saved_status),
        format_func=lambda v: "○" if v == "UNRECORDED" else ("✓" if v == "GREEN" else "✗"),
        key=f"inspection_status_{category}_{task_date}_{asset}_{item_index}",
        horizontal=True,
        label_visibility="collapsed",
    )
    values.setdefault(asset, {})[item] = status

    if status == "RED":
        is_panel_condition = category == "Panel Inspection"
        reason_label = "Panel number / issue" if is_panel_condition else "Reason / issue"
        reason_placeholder = "Enter affected panel number(s) and issue" if is_panel_condition else "Reason / issue"
        reason = st.text_input(
            reason_label,
            value=saved_reason,
            key=f"inspection_reason_{category}_{task_date}_{asset}_{item_index}",
            placeholder=reason_placeholder,
            label_visibility="visible" if is_panel_condition else "collapsed",
        ).strip()
        values[asset][f"{item}__reason"] = reason
        reason_widgets.append((f"{asset} – {item}", reason))
    else:
        values[asset][f"{item}__reason"] = ""

    if category == "Switch Yard Inspection" and item.strip() == "OCB Tripping Count Recorded" and status == "GREEN":
        saved_items = parse_details(old_record).get("items", {}) if old_record else {}
        previous_count = saved_items.get(f"{item}__count", 0)
        try:
            previous_count = int(previous_count or 0)
        except (TypeError, ValueError):
            previous_count = 0
        count = st.number_input(
            "Tripping count", min_value=0, step=1, value=previous_count,
            key=f"inspection_trip_count_{category}_{task_date}_{asset}_{item_index}",
        )
        values[asset][f"{item}__count"] = int(count)


def _render_inspection_category(db, user, plant, category):
    """Render one inspection process with grouped checks and an explicit Unrecorded state."""
    cfg = PROCESS_CATEGORIES[category]

    c1, c2, c3 = st.columns(3)
    c1.markdown(
        f'<div class="dashboard-card"><h4>Frequency</h4><div class="dashboard-value">{cfg["frequency"]}</div></div>',
        unsafe_allow_html=True,
    )
    c2.markdown(
        f'<div class="dashboard-card"><h4>Asset Type</h4><div class="dashboard-value">{cfg["asset_type"]}</div></div>',
        unsafe_allow_html=True,
    )
    c3.markdown(
        f'<div class="dashboard-card"><h4>Checklist Items</h4><div class="dashboard-value">{len(cfg["items"])}</div></div>',
        unsafe_allow_html=True,
    )

    task_date = st.date_input(
        "Inspection date", value=date.today(), key=f"inspection_date_{category}"
    )

    assets = asset_list(plant, cfg["asset_type"])
    asset_label = {
        "Inverter": "Inverters",
        "Table": "Tables",
        "MDB": "MDB",
        "Switch Yard": "Switch Yard",
        "AC Unit": "AC Unit",
    }.get(cfg["asset_type"], cfg["asset_type"])

    old_records = status_map(db, plant.id, category, task_date)
    single_asset = cfg["asset_type"] in ("MDB", "Switch Yard", "AC Unit")

    if single_asset:
        st.markdown(
            '<div class="inspection-single-box">'
            f'<div class="inspection-single-title">{asset_label} Inspection</div>'
            '<div class="inspection-status-legend"><span class="unrecorded">○</span>&nbsp;&nbsp;'
            '<span class="done">✓</span>&nbsp;&nbsp;'
            '<span class="notdone">✗</span>&nbsp;&nbsp;'
            '<span class="small-muted">○ not recorded &nbsp; ✗ requires a reason</span></div>'
            '</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div class="inspection-asset-strip">'
            f'<span class="inspection-asset-name">{asset_label} • {len(assets)} assets</span>'
            '<span class="inspection-status-legend"><span class="unrecorded">○</span>&nbsp;&nbsp;'
            '<span class="done">✓</span>&nbsp;&nbsp;'
            '<span class="notdone">✗</span>&nbsp;&nbsp;'
            '<span class="small-muted">○ not recorded &nbsp; ✗ requires a reason</span></span>'
            '</div>',
            unsafe_allow_html=True,
        )

    groups = _inspection_groups(category, assets)
    values = {}
    reason_widgets = []

    # Daily inspection gets separate tabs so CCTV, Lightning Arresters and Fire
    # Extinguishers each use only one inverter (INV-01) and are not repeated 10 times.
    if category == "Panel Inspection":
        tab_titles = ["Front Glass Condition", "Back Glass Condition", "Diode Damage"]
        tabs = st.tabs(tab_titles)
        groups = [(tab_titles[i], [item], group_assets) for i, (group_title, group_items, group_assets) in enumerate(groups) for item in group_items]
        group_contexts = list(zip(tabs, groups))
    elif category in ("Daily Inspection", "AC Inspection"):
        tabs = st.tabs([title for title, _, _ in groups])
        group_contexts = list(zip(tabs, groups))
    else:
        group_contexts = [(st.container(), group) for group in groups]

    item_number_map = {item: i + 1 for i, item in enumerate(cfg["items"])}

    for context, (group_title, group_items, group_assets) in group_contexts:
        with context:
            if category == "AC Inspection":
                st.markdown(f"### {group_title}")
                row_size = 4
                for row_start in range(0, len(group_items), row_size):
                    row_items = group_items[row_start:row_start + row_size]
                    cols = st.columns(len(row_items), gap="small")
                    for col, item in zip(cols, row_items):
                        with col:
                            asset = group_assets[0] if group_assets else "AC-01"
                            old_record = old_records.get(asset)
                            st.markdown(
                                f'<div class="inspection-group-item">'
                                f'<div class="inspection-item-name">'
                                f'{item_number_map[item]}. {_inspection_display_item(item)}</div>',
                                unsafe_allow_html=True,
                            )
                            _render_inspection_status(
                                db, user, plant, category, task_date, item,
                                item_number_map[item] - 1, asset, old_record,
                                values, reason_widgets,
                            )
                            st.markdown('</div>', unsafe_allow_html=True)
                continue

            if category == "Daily Inspection":
                # Each inverter is shown once, with its applicable checks beneath it.
                # Site Safety & Security checks intentionally apply only to INV-01.
                if group_title.startswith("🛡️"):
                    st.caption("Site Safety & Security Checks are recorded against INV-01 only.")
                    asset_rows = [group_assets[0] if group_assets else "INV-01"]
                else:
                    asset_rows = group_assets

                cards_per_row = 3
                for row_start in range(0, len(asset_rows), cards_per_row):
                    row_assets = asset_rows[row_start:row_start + cards_per_row]
                    cols = st.columns(len(row_assets), gap="small")
                    for col, asset in zip(cols, row_assets):
                        with col:
                            st.markdown(
                                f'<div class="inspection-asset-box">{asset}</div>',
                                unsafe_allow_html=True,
                            )
                            old_record = old_records.get(asset)
                            for item in group_items:
                                item_index = item_number_map[item] - 1
                                st.markdown(
                                    f'<div class="inspection-item-name" style="margin-top:8px;">'
                                    f'{item_number_map[item]}. {_inspection_display_item(item)}</div>',
                                    unsafe_allow_html=True,
                                )
                                _render_inspection_status(
                                    db, user, plant, category, task_date, item,
                                    item_index, asset, old_record, values, reason_widgets,
                                )
                            st.markdown('<div class="inspection-item-divider"></div>', unsafe_allow_html=True)
                continue

            # All other inspections are displayed as one continuous checklist.
            if single_asset:
                row_size = 4
                for row_start in range(0, len(group_items), row_size):
                    row_items = group_items[row_start:row_start + row_size]
                    cols = st.columns(len(row_items), gap="small")
                    for col, item in zip(cols, row_items):
                        with col:
                            asset = group_assets[0] if group_assets else "SITE-01"
                            old_record = old_records.get(asset)
                            st.markdown(
                                f'<div class="inspection-group-item">'
                                f'<div class="inspection-item-name">'
                                f'{item_number_map[item]}. {_inspection_display_item(item)}</div>',
                                unsafe_allow_html=True,
                            )
                            _render_inspection_status(
                                db, user, plant, category, task_date, item,
                                item_number_map[item] - 1, asset, old_record,
                                values, reason_widgets,
                            )
                            st.markdown('</div>', unsafe_allow_html=True)
                    if row_start + row_size < len(group_items):
                        st.markdown('<div class="inspection-item-divider"></div>', unsafe_allow_html=True)
            else:
                st.caption("Each checklist item is checked asset-by-asset. ○ = Not recorded, ✓ = OK, ✗ = Not OK.")
                grid_cols = 5
                for item in group_items:
                    item_index = item_number_map[item] - 1
                    st.markdown(
                        f'<div class="inspection-check-title">'
                        f'<span>{item_number_map[item]}. {_inspection_display_item(item)}</span>'
                        f'<span class="inspection-check-hint">{asset_label}</span>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                    for row_start in range(0, len(group_assets), grid_cols):
                        row_assets = group_assets[row_start:row_start + grid_cols]
                        cols = st.columns(len(row_assets), gap="small")
                        for col, asset in zip(cols, row_assets):
                            with col:
                                old_record = old_records.get(asset)
                                st.markdown(
                                    f'<div class="inspection-asset-box">{asset}</div>',
                                    unsafe_allow_html=True,
                                )
                                _render_inspection_status(
                                    db, user, plant, category, task_date, item,
                                    item_index, asset, old_record, values, reason_widgets,
                                )
                    st.markdown('<div class="inspection-item-divider"></div>', unsafe_allow_html=True)

    if st.button(
        "💾 Save Inspection",
        type="primary",
        use_container_width=True,
        key=f"save_inspection_{category}",
    ):
        missing = [item for item, reason in reason_widgets if not reason.strip()]
        if missing:
            st.error("Please enter the reason / issue for every item marked ✗ Not OK.")
            st.write("Missing reason for: " + ", ".join(missing[:30]) + (" …" if len(missing) > 30 else ""))
            return

        # Only assets/items that were actually displayed are allowed to change.
        # This is important for the Daily Inspection one-inverter checks.
        touched_assets = set(values.keys())
        failed_assets = []
        saved_count = 0
        removed_count = 0

        for asset in touched_assets:
            asset_values = values.get(asset, {})
            if not asset_values:
                continue

            rec = old_records.get(asset)
            existing_details = parse_details(rec) if rec else {}
            merged_items = dict(existing_details.get("items", {}) if isinstance(existing_details, dict) else {})

            for item, selected_status in asset_values.items():
                if item.endswith("__reason"):
                    continue
                if item.endswith("__count"):
                    merged_items[item] = int(selected_status or 0)
                    continue
                if selected_status == "UNRECORDED":
                    # Do not create a record for a never-recorded item. If the user
                    # deliberately reset an existing item, remove only that item.
                    merged_items.pop(item, None)
                    merged_items.pop(f"{item}__reason", None)
                elif selected_status == "GREEN":
                    merged_items[item] = "GREEN"
                    merged_items[f"{item}__reason"] = ""
                elif selected_status == "RED":
                    merged_items[item] = "RED"
                    merged_items[f"{item}__reason"] = asset_values.get(f"{item}__reason", "").strip()

            recorded_items = [
                item for item in cfg["items"]
                if merged_items.get(item) in ("GREEN", "RED", "OK / Green", "Completed", "DONE", "Not Done", "Failed")
            ]

            if not recorded_items:
                # Leave a completely untouched asset unrecorded. If an old record
                # was explicitly cleared, remove that date-specific record only.
                if rec:
                    db.delete(rec)
                    db.commit()
                    removed_count += 1
                continue

            all_recorded = len(recorded_items) == len(cfg["items"])
            any_red = any(merged_items.get(item) in ("RED", "Not Done", "Failed") for item in cfg["items"])
            overall = "Failed" if any_red else ("Completed" if all_recorded else "Pending")

            upsert_record(
                db, plant.id, category, asset, task_date, user.id, overall,
                {"items": merged_items}, remarks="",
            )
            saved_count += 1
            if overall == "Failed":
                failed_assets.append(asset)
                for item in cfg["items"]:
                    if merged_items.get(item) == "RED":
                        reason = str(merged_items.get(f"{item}__reason", "") or "").strip()
                        ensure_repair_from_failed_item(
                            db, plant.id, category, asset, item, reason, user.id
                        )

        if saved_count or removed_count:
            st.success(f"{category} saved successfully.")
        else:
            st.info("No checklist items were recorded. Unselected items remain Not recorded.")
        if failed_assets:
            st.warning(
                "🔴 Not OK: " + ", ".join(sorted(set(failed_assets)))
                + ". Staff/supervisors/admins assigned to this plant have been notified."
            )
        st.rerun()


def inspection_page(db, user, plant):
    header(plant)
    st.markdown("## Maintenance Inspections")
    st.caption(
        "Select a maintenance process tab. Inverter and safety checks are separated, and AC inspections are split into outdoor and indoor units."
    )

    categories = list(PROCESS_CATEGORIES.keys())
    selected = st.session_state.get("inspection_category", categories[0])
    if selected not in categories:
        selected = categories[0]

    # Compact horizontal tab bar. Unlike equal-width columns, this keeps the
    # complete tab names visible and allows horizontal scrolling on narrow screens.
    tab_labels = {
        "Daily Inspection": "Inverter & Safety Checks",
        "Inverter Inspection": "Inverter Inspection",
        "Panel Inspection": "Panel Inspection",
        "DC Cable Inspection": "DC Cable Inspection",
        "MDB Inspection": "MDB Inspection",
        "Switch Yard Inspection": "Switch Yard Inspection",
        "AC Inspection": "AC Inspection",
    }
    with st.container(key="inspection_tabs"):
        tab_cols = st.columns([1.25, 1.15, 1.0, 1.2, .95, 1.35, .85], gap="small")
        for col, category in zip(tab_cols, categories):
            with col:
                if st.button(
                    tab_labels[category],
                    key=f"inspection_tab_{category}",
                    use_container_width=True,
                    type="primary" if category == selected else "secondary",
                ):
                    st.session_state.inspection_category = category
                    st.rerun()

    st.markdown('<div class="inspection-tabs-line"></div>', unsafe_allow_html=True)
    _render_inspection_category(db, user, plant, selected)

def loss_repair_page(db, user, plant):
    header(plant)
    st.markdown("## Repair & Maintenance")

    tab1, tab2 = st.tabs(["Open Repair Items", "Create Repair Item"])

    with tab1:
        status_filter = st.selectbox("Status", ["All", "Open", "In Progress", "Completed"])

        q = db.query(RepairItem).filter(RepairItem.plant_id == plant.id)
        if status_filter != "All":
            q = q.filter(RepairItem.status == status_filter)

        repairs = q.order_by(RepairItem.created_at.desc()).all()

        if not repairs:
            st.success("No repair items found.")
        else:
            for item in repairs:
                status_icon = {"Open": "🔴", "In Progress": "🟠", "Completed": "🟢"}.get(
                    item.status, "⚪"
                )

                with st.expander(
                    f"{status_icon} {item.asset_id} • {item.source_category} • {item.component}"
                ):
                    a, b, c = st.columns(3)
                    a.write(f"**Issue:** {item.issue or 'Not specified'}")
                    b.write(f"**Loss:** {item.loss_percent:.1f}%")
                    c.write(f"**Created:** {item.created_at.strftime('%d %b %Y')}")

                    new_status = st.selectbox(
                        "Repair status",
                        ["Open", "In Progress", "Completed"],
                        index=["Open", "In Progress", "Completed"].index(item.status),
                        key=f"repair_status_{item.id}",
                    )

                    new_loss = st.number_input(
                        "Estimated loss %",
                        min_value=0.0, max_value=100.0,
                        value=float(item.loss_percent or 0),
                        step=0.5,
                        key=f"loss_{item.id}",
                    )

                    reason = st.text_area(
                        "Why is it not completed? / repair remarks",
                        value=item.reason_not_completed or "",
                        key=f"repair_reason_{item.id}",
                    )

                    if st.button("Save Repair Update", key=f"save_repair_{item.id}"):
                        item.status = new_status
                        item.loss_percent = new_loss
                        item.reason_not_completed = reason

                        if new_status == "Completed":
                            item.completed_at = datetime.utcnow()

                        db.commit()

                        if new_status == "Completed":
                            notify_users(
                                db,
                                plant.id,
                                f"🟢 Repair completed: {item.asset_id}",
                                (
                                    f"Plant: {plant.name}\n"
                                    f"Category: {item.source_category}\n"
                                    f"Asset: {item.asset_id}\n"
                                    f"Component: {item.component}\n"
                                    "The repair item has been marked Completed."
                                ),
                                item.source_category,
                                item.asset_id,
                                notification_type="REPAIR_COMPLETED",
                            )

                        st.success("Repair item updated.")
                        st.rerun()

    with tab2:
        st.subheader("Create a repair / loss item")
        category = st.selectbox("Source category", LOSS_REPAIR_CATEGORIES, key="new_repair_cat")
        cfg = PROCESS_CATEGORIES[category]
        asset = st.selectbox("Asset", asset_list(plant, cfg["asset_type"]), key="new_repair_asset")
        component = st.selectbox("Component / subcategory", cfg["items"])
        issue = st.text_area("Issue")
        reason = st.text_area("Reason if work cannot be completed")
        loss = st.number_input("Estimated loss %", 0.0, 100.0, 0.0, 0.5)

        if st.button("Create Repair Item", type="primary"):
            if not issue.strip():
                st.error("Enter the issue.")
            else:
                item = RepairItem(
                    plant_id=plant.id,
                    source_category=category,
                    asset_id=asset,
                    component=component,
                    issue=issue.strip(),
                    reason_not_completed=reason.strip(),
                    loss_percent=loss,
                    status="Open",
                    created_by=user.id,
                )
                db.add(item)
                db.commit()

                notify_users(
                    db,
                    plant.id,
                    f"🔴 New repair item: {asset}",
                    (
                        f"Plant: {plant.name}\n"
                        f"Category: {category}\n"
                        f"Asset: {asset}\n"
                        f"Component: {component}\n"
                        f"Issue: {issue.strip()}"
                    ),
                    category,
                    asset,
                    notification_type="RED_ALERT",
                )

                st.success("Repair item created and notifications sent.")
                st.rerun()


# ============================================================
# Activity Log + filters
# ============================================================

def activity_page(db, user, plant):
    header(plant)
    st.markdown("## Activity Log")

    f1, f2, f3 = st.columns(3)
    with f1:
        start = st.date_input("From", date.today() - timedelta(days=30), key="act_start")
    with f2:
        end = st.date_input("To", date.today(), key="act_end")
    with f3:
        category = st.selectbox("Category", DASHBOARD_CATEGORIES, key="act_cat")

    records = filtered_records(db, plant.id, start, end, None, category)
    users = {u.id: u.full_name for u in db.query(User).all()}
    rows = [{
        "Date": r.task_date, "Category": r.category, "Asset": r.asset_id,
        "Worker": users.get(r.worker_id, "—"), "Status": r.status,
        "Approval": getattr(r, "approval_status", "Pending") or "Pending",
        "Remarks": r.remarks or "", "Completed At": r.completed_at,
    } for r in records]

    if not rows:
        st.info("No activity for the selected filters.")
        return

    df = pd.DataFrame(rows)
    st.dataframe(df, hide_index=True, use_container_width=True)
    e1, e2 = st.columns(2)
    with e1:
        st.download_button("⬇️ Export Excel", _excel_bytes(df, "Activity"), "maintenance_activity.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)
    with e2:
        st.download_button("⬇️ Download CSV", df.to_csv(index=False).encode("utf-8"), "maintenance_activity.csv", "text/csv", use_container_width=True)

    if user.role in ("admin", "engineer"):
        st.markdown("### ✏️ Edit / 🗑️ Delete activity")
        st.caption("Use the action controls below when a record needs correction or removal.")
        for r in records[:50]:
            with st.expander(f"{r.task_date:%d %b %Y} • {r.category} • {r.asset_id} • {r.status}"):
                c1, c2 = st.columns(2)
                new_status = c1.selectbox("Status", ["Pending", "Completed", "Failed"], index=["Pending", "Completed", "Failed"].index(r.status) if r.status in ["Pending", "Completed", "Failed"] else 0, key=f"edit_status_{r.id}")
                new_remarks = c2.text_input("Remarks", r.remarks or "", key=f"edit_remarks_{r.id}")
                b1, b2 = st.columns(2)
                if b1.button("💾 Save changes", key=f"edit_save_{r.id}", use_container_width=True):
                    r.status = new_status
                    r.remarks = new_remarks
                    r.completed_at = datetime.utcnow() if new_status in ("Completed", "Failed") else None
                    db.commit()
                    st.success("Activity updated.")
                    st.rerun()
                if b2.button("🗑️ Delete", key=f"edit_delete_{r.id}", use_container_width=True):
                    db.delete(r)
                    db.commit()
                    st.success("Activity deleted.")
                    st.rerun()


# ============================================================
# Admin: Users
# ============================================================

def users_page(db, user, selected_plant=None):
    st.title("👥 User Management")
    st.caption("Manage user accounts, site staffing and operating contacts from one place.")

    user_tab, staff_tab = st.tabs(["👥 User Accounts", "👷 Operating Staff & Site Contacts"])

    with user_tab:
        users = db.query(User).order_by(User.created_at.desc()).all()
        plants = db.query(Plant).filter(Plant.active == True).order_by(Plant.name).all()
        plant_map = {p.name: p.id for p in plants}

        for u in users:
            with st.expander(f"{u.full_name} • {u.email} • {u.role}"):
                c1, c2, c3 = st.columns(3)

                role = c1.selectbox(
                    "Role", ["staff", "supervisor", "engineer", "admin"],
                    index=["staff", "supervisor", "engineer", "admin"].index(u.role) if u.role in ["staff", "supervisor", "engineer", "admin"] else 0,
                    key=f"role_{u.id}",
                )

                names = ["Unassigned"] + list(plant_map.keys())
                current = next((n for n, pid in plant_map.items() if pid == u.plant_id), "Unassigned")
                plant_name = c2.selectbox(
                    "Assigned plant", names,
                    index=names.index(current),
                    key=f"plant_{u.id}",
                )

                active = c3.checkbox("Active", bool(u.active), key=f"active_{u.id}")

                if st.button("Save User", key=f"save_user_{u.id}"):
                    u.role = role
                    u.plant_id = plant_map.get(plant_name)
                    u.approved = True
                    u.active = active
                    db.commit()
                    st.success("User updated.")
                    st.rerun()

    with staff_tab:
        st.markdown("### 👷 Operating Staff & Site Contacts")
        st.caption("Manage site headcount, supervisor and immediate reportee details here. This replaces the separate navigation page.")
        if selected_plant is None:
            st.info("No plant is selected. Select a plant from the left navigation bar.")
        else:
            st.caption(f"Showing site contacts for **{display_plant_name(selected_plant)}**. The site follows the plant selected in the left navigation bar.")
            operations_staff_section(db, user, selected_plant, embedded=True)


# ============================================================
# Admin: Plants
# ============================================================

def plants_page(db):
    st.title("🏭 Plant Management")
    st.caption("Configure all solar plants.")

    plants = db.query(Plant).order_by(Plant.name).all()

    for p in plants:
        with st.expander(f"{p.code} — {p.name}"):
            c1, c2 = st.columns(2)
            name = c1.text_input("Plant name", p.name, key=f"pname_{p.id}")
            code = c1.text_input("Plant code", p.code, key=f"pcode_{p.id}")
            capacity = c1.number_input(
                "Capacity MW", 0.0, 1000.0, float(p.capacity_mw), 0.1,
                key=f"pcap_{p.id}"
            )

            inv = c2.number_input(
                "Inverters", 1, 1000, int(p.inverter_count), key=f"pinv_{p.id}"
            )
            tables = c2.number_input(
                "Tables", 1, 10000, int(p.table_count), key=f"ptab_{p.id}"
            )
            zones = c2.number_input(
                "Zones", 1, 1000, int(p.zone_count), key=f"pzone_{p.id}"
            )
            active = c2.checkbox("Active", p.active, key=f"pactive_{p.id}")

            save_col, delete_col = st.columns([1, 1])

            with save_col:
                if st.button("💾 Save Plant", key=f"save_plant_{p.id}"):
                    new_code_value = code.strip().upper()

                    duplicate = (
                        db.query(Plant)
                        .filter(Plant.code == new_code_value, Plant.id != p.id)
                        .first()
                    )
                    if not new_code_value or not name.strip():
                        st.error("Plant code and plant name are required.")
                    elif duplicate:
                        st.error("Another plant already uses this plant code.")
                    else:
                        p.name = name.strip()
                        p.code = new_code_value
                        p.capacity_mw = capacity
                        p.inverter_count = inv
                        p.table_count = tables
                        p.zone_count = zones
                        p.active = active
                        db.commit()
                        st.success("Plant updated.")
                        st.rerun()

            with delete_col:
                if st.button(
                    "🗑️ Delete Plant",
                    key=f"delete_plant_{p.id}",
                    type="secondary",
                ):
                    # Prevent accidental deletion with a second confirmation step.
                    st.session_state[f"confirm_delete_plant_{p.id}"] = True
                    st.rerun()

            if st.session_state.get(f"confirm_delete_plant_{p.id}", False):
                st.warning(
                    f"Delete **{p.name} ({p.code})**? "
                    "This permanently removes the plant and its linked maintenance data."
                )
                confirm_col, cancel_col = st.columns(2)

                with confirm_col:
                    if st.button(
                        "⚠️ Yes, permanently delete",
                        key=f"confirm_delete_plant_yes_{p.id}",
                        type="primary",
                    ):
                        # Delete dependent rows first because several relationships
                        # are not configured with database-level cascade rules.
                        db.query(MaintenanceRecord).filter(
                            MaintenanceRecord.plant_id == p.id
                        ).delete(synchronize_session=False)
                        db.query(RepairItem).filter(
                            RepairItem.plant_id == p.id
                        ).delete(synchronize_session=False)
                        db.query(Notification).filter(
                            Notification.plant_id == p.id
                        ).delete(synchronize_session=False)
                        db.query(MaintenanceReminder).filter(
                            MaintenanceReminder.plant_id == p.id
                        ).delete(synchronize_session=False)
                        db.query(PlantOperationsProfile).filter(
                            PlantOperationsProfile.plant_id == p.id
                        ).delete(synchronize_session=False)
                        db.query(User).filter(
                            User.plant_id == p.id
                        ).update(
                            {User.plant_id: None},
                            synchronize_session=False,
                        )

                        db.delete(p)
                        db.commit()
                        st.session_state.pop(f"confirm_delete_plant_{p.id}", None)
                        st.session_state.pop("selected_plant_id", None)
                        st.success("Plant deleted successfully.")
                        st.rerun()

                with cancel_col:
                    if st.button(
                        "Cancel",
                        key=f"cancel_delete_plant_{p.id}",
                    ):
                        st.session_state.pop(f"confirm_delete_plant_{p.id}", None)
                        st.rerun()

    st.markdown("---")
    st.subheader("Add new solar plant")
    c1, c2 = st.columns(2)

    new_code = c1.text_input("New plant code")
    new_name = c1.text_input("New plant name")
    new_capacity = c1.number_input("Capacity MW", 0.0, 1000.0, 1.0)

    new_inv = c2.number_input("Inverters", 1, 1000, 10)
    new_tables = c2.number_input("Tables", 1, 10000, 80)
    new_zones = c2.number_input("Zones", 1, 1000, 10)

    if st.button("Add Plant", type="primary"):
        if not new_code.strip() or not new_name.strip():
            st.error("Plant code and name are required.")
        elif db.query(Plant).filter(
            Plant.code == new_code.strip().upper()
        ).first():
            st.error("Plant code already exists.")
        else:
            db.add(
                Plant(
                    code=new_code.strip().upper(),
                    name=new_name.strip(),
                    capacity_mw=new_capacity,
                    inverter_count=new_inv,
                    table_count=new_tables,
                    zone_count=new_zones,
                )
            )
            db.commit()
            st.success("Plant added.")
            st.rerun()


# ============================================================
# Admin: Reminders
# ============================================================

# ============================================================
# Admin: Reminders
# ============================================================

def reminders_page(db):
    st.title("⏰ Automatic Maintenance Reminders")

    plants = db.query(Plant).filter(Plant.active == True).all()
    plants = sorted(
        plants,
        key=lambda p: (
            "DPV" in f"{getattr(p, 'code', '')} {getattr(p, 'name', '')}".upper(),
            (p.name or "").casefold(),
        ),
    )
    plant_map = {p.name: p.id for p in plants}

    selected_name = st.selectbox("Plant", list(plant_map.keys()))
    plant_id = plant_map[selected_name]

    if st.button("Generate / Send Due Reminders Now", type="primary"):
        generated, sent = send_due_reminders(db, plant_id)
        st.success(f"Generated {generated} reminder(s); sent {sent} due reminder(s).")

    reminders = (
        db.query(MaintenanceReminder)
        .filter(MaintenanceReminder.plant_id == plant_id)
        .order_by(MaintenanceReminder.due_date.desc())
        .limit(100)
        .all()
    )

    rows = [
        {
            "Category": r.category,
            "Frequency": r.frequency,
            "Due Date": r.due_date,
            "Status": r.status,
            "Sent": bool(r.sent_at),
        }
        for r in reminders
    ]

    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    else:
        st.info("No reminders generated yet.")



# ============================================================
# Power BI
# ============================================================

def powerbi_page(db):
    st.title("📊 Power BI Integration")

    st.markdown(
        """
        ### Recommended architecture

        ```text
        Solar Maintenance App
                ↓
        PostgreSQL / Neon
                ↓
        Power BI Desktop
                ↓
        Power BI Service
        ```

        The application stores maintenance records, repair records, users, plants,
        and notifications in PostgreSQL. Power BI can connect directly to the same
        PostgreSQL database and refresh the dashboard without copying the data.
        """
    )

    st.subheader("Tables for Power BI")
    st.code(
        """plants
users
maintenance_records
repair_items
notifications
maintenance_reminders""",
        language="text",
    )

    st.subheader("Recommended Power BI fields")

    powerbi_fields = pd.DataFrame(
        [
            ["Maintenance Records", "task_date", "Date slicer"],
            ["Maintenance Records", "category", "Category slicer"],
            ["Maintenance Records", "worker_id", "Worker filter"],
            ["Maintenance Records", "asset_id", "Asset / inverter / table"],
            ["Maintenance Records", "status", "Green / Red / Pending"],
            ["Repair Items", "loss_percent", "Loss KPI"],
            ["Repair Items", "status", "Repair status"],
            ["Repair Items", "component", "Failure component"],
            ["Notifications", "notification_type", "Alert type"],
            ["Maintenance Reminders", "due_date", "Maintenance due date"],
        ],
        columns=["Table", "Field", "Purpose"],
    )
    st.dataframe(powerbi_fields, hide_index=True, use_container_width=True)

    st.subheader("Export current maintenance data")
    records = db.query(MaintenanceRecord).order_by(MaintenanceRecord.task_date.desc()).all()

    rows = []
    for r in records:
        worker = db.query(User).filter(User.id == r.worker_id).first() if r.worker_id else None
        plant = db.query(Plant).filter(Plant.id == r.plant_id).first()
        rows.append(
            {
                "PlantCode": plant.code if plant else "",
                "PlantName": plant.name if plant else "",
                "RecordID": r.id,
                "TaskDate": r.task_date,
                "Category": r.category,
                "AssetID": r.asset_id,
                "Worker": worker.full_name if worker else "",
                "WorkerEmail": worker.email if worker else "",
                "Status": r.status,
                "Remarks": "; ".join(filter(None, [r.remarks or "", *[
                    f"{key[:-8]}: {value}" for key, value in (parse_details(r).get("items", {}) or {}).items()
                    if key.endswith("__reason") and str(value or "").strip()
                ], *[
                    f"{key[:-7]}: {value}" for key, value in (parse_details(r).get("items", {}) or {}).items()
                    if key.endswith("__count")
                ]])),
                "CompletedAt": r.completed_at,
            }
        )

    df = pd.DataFrame(rows)
    st.download_button(
        "⬇️ Download Power BI CSV",
        df.to_csv(index=False).encode("utf-8"),
        "solar_maintenance_powerbi.csv",
        "text/csv",
    )

    if POWERBI_PUSH_URL:
        st.success("Optional Power BI push URL is configured.")
    else:
        st.caption("No optional Power BI push URL configured. Direct PostgreSQL/Neon connection is recommended.")


# ============================================================
# Reports + approval/sign-off
# ============================================================

def _records_dataframe(db,plant_id,start,end):
    rows=db.query(MaintenanceRecord).filter(MaintenanceRecord.plant_id==plant_id,MaintenanceRecord.task_date>=start,MaintenanceRecord.task_date<=end).order_by(MaintenanceRecord.task_date.desc()).all()
    users={u.id:u.full_name for u in db.query(User).all()}
    report_rows=[]
    for r in rows:
        details=parse_details(r)
        items=details.get("items", {}) if isinstance(details, dict) else {}
        issue_details=[]
        for key, value in items.items():
            if key.endswith("__reason") and str(value or "").strip():
                issue_details.append(f"{key[:-8]}: {str(value).strip()}")
            elif key.endswith("__count"):
                issue_details.append(f"{key[:-7]}: {value}")
        remarks="; ".join(filter(None, [r.remarks or "", *issue_details]))
        report_rows.append({"Date":r.task_date,"Category":r.category,"Asset":r.asset_id,"Worker":users.get(r.worker_id,"Unassigned"),"Status":r.status,"Approval":getattr(r,"approval_status","Pending") or "Pending","Remarks":remarks,"Completed At":r.completed_at,"Approved At":getattr(r,"approved_at",None),"Approved By":users.get(getattr(r,"approved_by",None),"")})
    return pd.DataFrame(report_rows)

def _excel_bytes(df,sheet_name="Maintenance Report"):
    output=io.BytesIO()
    export_df = df.copy()
    if "Status" in export_df.columns:
        export_df["Status"] = export_df["Status"].replace({
            "Completed": "Green - Completed",
            "Failed": "Red - Not Completed",
            "Pending": "Red - Not Completed",
        })
    with pd.ExcelWriter(output,engine="openpyxl") as writer:
        export_df.to_excel(writer,index=False,sheet_name=sheet_name[:31])
    return output.getvalue()


def _pdf_report_bytes(df, plant, start, end, panel_pct, grass_pct, report_type="Overall Summary"):
    """Create a one-page work-category report or the full overall management report."""
    if report_type in ("Panel Cleaning", "Grass Cutting"):
        # Dedicated work reports contain only their own category; inspections and
        # approvals, as well as the other work category, are intentionally excluded.
        df = df[df["Category"] == report_type].copy() if len(df) else df.copy()
        output = io.BytesIO()
        doc = SimpleDocTemplate(
            output, pagesize=landscape(A4), rightMargin=9*mm, leftMargin=9*mm,
            topMargin=8*mm, bottomMargin=8*mm,
            title=f"{plant.name} - {report_type} Report",
            author="Solar Operation and Maintenance Platform",
        )
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle("WorkReportTitle", parent=styles["Title"], alignment=TA_CENTER,
                                     fontSize=17, leading=20, spaceAfter=4,
                                     textColor=colors.HexColor("#123b63"))
        subtitle_style = ParagraphStyle("WorkReportSubtitle", parent=styles["Normal"], alignment=TA_CENTER,
                                        fontSize=8, textColor=colors.HexColor("#5b6573"), spaceAfter=8)
        heading_style = ParagraphStyle("WorkReportHeading", parent=styles["Heading2"], fontSize=10,
                                       leading=12, textColor=colors.HexColor("#123b63"), spaceBefore=5, spaceAfter=4)
        small = ParagraphStyle("WorkReportSmall", parent=styles["Normal"], fontSize=7, leading=8)
        pct, done_count, item_total = (panel_pct, 0, 0) if report_type == "Panel Cleaning" else (grass_pct, 0, 0)
        progress_db = SessionLocal()
        try:
            if report_type == "Panel Cleaning":
                pct, done_count, item_total = panel_cleaning_percent(progress_db, plant.id, end)
                unit = "tables"
            else:
                pct, done_count, item_total = grass_cutting_percent(progress_db, plant.id, end)
                unit = "zones"
        finally:
            progress_db.close()
        completed_count = int((df["Status"] == "Completed").sum()) if len(df) else 0
        not_completed_count = int((df["Status"] != "Completed").sum()) if len(df) else 0
        story = [
            Paragraph(f"{report_type} Summary Report", title_style),
            Paragraph(f"<b>{plant.name}</b> &nbsp; | &nbsp; Period: {start:%d %b %Y} - {end:%d %b %Y} &nbsp; | &nbsp; Generated: {datetime.now():%d %b %Y %H:%M}", subtitle_style),
        ]
        kpi_data = [[
            Paragraph(f"<b>{len(df)}</b><br/><font size='8'>Recorded Activities</font>", small),
            Paragraph(f"<b>{completed_count}</b><br/><font size='8'>Completed</font>", small),
            Paragraph(f"<b>{not_completed_count}</b><br/><font size='8'>Not Completed / Pending</font>", small),
            Paragraph(f"<b>{pct:.1f}%</b><br/><font size='8'>Overall {report_type} Progress</font><br/><font size='8'>{'4/4 cycles completed' if report_type == 'Panel Cleaning' and item_total > 0 and done_count >= item_total else '6/6 cycles completed' if report_type == 'Grass Cutting' and item_total > 0 and done_count >= item_total else '0/4 cycles completed' if report_type == 'Panel Cleaning' else '0/6 cycles completed'}</font>", small),
        ]]
        kpi = Table(kpi_data, colWidths=[48*mm]*4, rowHeights=[15*mm])
        kpi.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,-1), colors.HexColor("#f4f7fb")),
            ("BOX", (0,0), (-1,-1), .6, colors.HexColor("#d7e0ea")),
            ("INNERGRID", (0,0), (-1,-1), .4, colors.HexColor("#e2e8f0")),
            ("ALIGN", (0,0), (-1,-1), "CENTER"), ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ]))
        story += [kpi, Spacer(1, 5*mm), Paragraph(f"{report_type} Progress Summary", heading_style)]
        story.append(Paragraph(f"{done_count} of {item_total} {unit} completed; {max(0, item_total-done_count)} remaining ({pct:.1f}% complete).", small))
        story += [Spacer(1, 4*mm), Paragraph(f"{report_type} Activities", heading_style)]
        if len(df):
            columns = ["Date", "Category", "Asset", "Worker", "Status", "Remarks"]
            rows = [[Paragraph(f"<b>{c}</b>", small) for c in columns]]
            for _, row in df.head(12).iterrows():
                vals = [row.get("Date", ""), row.get("Category", ""), row.get("Asset", ""),
                        row.get("Worker", ""), row.get("Status", ""), row.get("Remarks", "") or ""]
                formatted = []
                for value in vals:
                    if pd.isna(value): value = ""
                    if hasattr(value, "strftime"): value = value.strftime("%d %b %Y")
                    formatted.append(Paragraph(str(value)[:100], small))
                rows.append(formatted)
            activity = Table(rows, colWidths=[22*mm, 32*mm, 22*mm, 35*mm, 28*mm, 65*mm], repeatRows=1)
            activity.setStyle(TableStyle([
                ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#d9ecfb")),
                ("TEXTCOLOR", (0,0), (-1,0), colors.HexColor("#123b63")),
                ("GRID", (0,0), (-1,-1), .3, colors.HexColor("#d7e0ea")),
                ("VALIGN", (0,0), (-1,-1), "TOP"),
                ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor("#f7fafc")]),
                ("LEFTPADDING", (0,0), (-1,-1), 4), ("RIGHTPADDING", (0,0), (-1,-1), 4),
                ("TOPPADDING", (0,0), (-1,-1), 3), ("BOTTOMPADDING", (0,0), (-1,-1), 3),
            ]))
            story.append(activity)
            if len(df) > 12:
                story.append(Paragraph(f"Showing the latest 12 of {len(df)} activities. Export to Excel for the complete report.", small))
        else:
            story.append(Paragraph("No activities found for the selected period.", small))
        story += [Spacer(1, 4*mm), Paragraph("Prepared for operational management review.", small)]
        doc.build(story)
        return output.getvalue()
    output = io.BytesIO()
    doc = SimpleDocTemplate(
        output,
        pagesize=landscape(A4),
        rightMargin=7 * mm,
        leftMargin=7 * mm,
        topMargin=6 * mm,
        bottomMargin=6 * mm,
        title=f"{plant.name} - Solar Maintenance Report",
        author="Solar Operation and Maintenance Platform",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ReportTitle", parent=styles["Title"], alignment=TA_CENTER,
        fontSize=16, leading=18, spaceAfter=3
    )
    subtitle_style = ParagraphStyle(
        "ReportSubtitle", parent=styles["Normal"], alignment=TA_CENTER,
        fontSize=7, textColor=colors.HexColor("#5b6573"), spaceAfter=4
    )
    h2 = ParagraphStyle(
        "ReportH2", parent=styles["Heading2"], fontSize=9, leading=11,
        textColor=colors.HexColor("#123b63"), spaceBefore=3, spaceAfter=2
    )
    small = ParagraphStyle(
        "ReportSmall", parent=styles["Normal"], fontSize=6, leading=7
    )

    total = len(df)
    # Current selected-end-date progress, consistent with the live site maps.
    progress_db = SessionLocal()
    try:
        panel_pct, panel_done, panel_total = panel_cleaning_percent(progress_db, plant.id, end)
        grass_pct, grass_done, grass_total = grass_cutting_percent(progress_db, plant.id, end)
    finally:
        progress_db.close()
    green = int((df["Status"] == "Completed").sum()) if total else 0
    red = int((df["Status"] != "Completed").sum()) if total else 0
    approval_db = SessionLocal()
    try:
        inspection_filter = [
            MaintenanceRecord.plant_id == plant.id,
            MaintenanceRecord.category.in_(list(PROCESS_CATEGORIES.keys())),
            MaintenanceRecord.task_date >= start,
            MaintenanceRecord.task_date <= end,
        ]
        pending = approval_db.query(MaintenanceRecord).filter(
            *inspection_filter, MaintenanceRecord.approval_status == "Pending"
        ).count()
        approved = approval_db.query(MaintenanceRecord).filter(
            *inspection_filter, MaintenanceRecord.approval_status == "Approved"
        ).count()
    finally:
        approval_db.close()

    story = [
        Paragraph("Solar Plant Maintenance Management Report", title_style),
        Paragraph(
            f"<b>{plant.name}</b> &nbsp; | &nbsp; Period: {start:%d %b %Y} - {end:%d %b %Y} &nbsp; | &nbsp; Generated: {datetime.now():%d %b %Y %H:%M}",
            subtitle_style,
        ),
    ]

    # KPI cards represented as a compact table in the PDF.
    kpi_data = [[
        Paragraph(f"<font size='9'><b>{total}</b></font><br/><font size='6'>Activities</font>", small),
        Paragraph(f"<font size='9'><b>{green}</b></font><br/><font size='6'>Green - Completed</font>", small),
        Paragraph(f"<font size='9'><b>{red}</b></font><br/><font size='6'>Red - Not Completed</font>", small),
        Paragraph(f"<font size='10'><b>{pending}</b></font><br/><font size='6'>Pending Inspection<br/>Approvals</font>", small),
        Paragraph(f"<font size='10'><b>{approved}</b></font><br/><font size='6'>Approved Inspection<br/>Approvals</font>", small),
        Paragraph(f"<b>{panel_pct:.0f}%</b><br/><font size='8'>Panel Cleaning</font><br/><font size='7'>{'4/4 cycles completed' if panel_total and panel_done >= panel_total else '0/4 cycles completed'}</font>", small),
        Paragraph(f"<b>{grass_pct:.0f}%</b><br/><font size='8'>Grass Cutting</font><br/><font size='7'>{'6/6 cycles completed' if grass_total and grass_done >= grass_total else '0/6 cycles completed'}</font>", small),
    ]]
    kpi_table = Table(kpi_data, colWidths=[35*mm]*7, rowHeights=[15*mm])
    kpi_table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), colors.HexColor("#f4f7fb")),
        ("BOX", (0,0), (-1,-1), 0.6, colors.HexColor("#d7e0ea")),
        ("INNERGRID", (0,0), (-1,-1), 0.4, colors.HexColor("#e2e8f0")),
        ("ALIGN", (0,0), (-1,-1), "CENTER"),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("LEFTPADDING", (0,0), (-1,-1), 3),
        ("RIGHTPADDING", (0,0), (-1,-1), 3),
        ("TOPPADDING", (0,0), (-1,-1), 3),
        ("BOTTOMPADDING", (0,0), (-1,-1), 3),
    ]))
    story += [kpi_table, Spacer(1, 2*mm)]

    # Graphical summary focuses on the actual site work areas: tables and zones.
    status_rows = []
    work_summary = [
        ("Panel Cleaning", panel_pct, "tables"),
        ("Grass Cutting", grass_pct, "zones"),
    ]
    for label, pct_value, unit in work_summary:
        bar_width = max(2, min(100, float(pct_value)))
        bar = Table([[""]], colWidths=[bar_width * 0.75 * mm], rowHeights=[4 * mm])
        bar.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,-1), colors.HexColor("#18a66a" if pct_value >= 100 else "#e55353")),
            ("BOX", (0,0), (-1,-1), 0, colors.white),
        ]))
        done_count = panel_done if label == "Panel Cleaning" else grass_done
        total_count = panel_total if label == "Panel Cleaning" else grass_total
        cycles_per_year = 4 if label == "Panel Cleaning" else 6
        state = f"{cycles_per_year}/{cycles_per_year} cycles completed" if total_count > 0 and done_count >= total_count else f"0/{cycles_per_year} cycles completed"
        status_rows.append([Paragraph(label, small), bar, Paragraph(f"{pct_value:.1f}%", small), Paragraph(f"{done_count}/{total_count} {unit} • {state}", small)])
    status_table = Table(status_rows, colWidths=[27*mm, 55*mm, 17*mm, 42*mm])
    status_table.setStyle(TableStyle([("VALIGN", (0,0), (-1,-1), "MIDDLE"), ("ALIGN", (2,0), (2,-1), "RIGHT"), ("BOTTOMPADDING", (0,0), (-1,-1), 4)]))

    category_counts = df["Category"].value_counts().head(8).to_dict() if total else {}
    cat_rows = [[Paragraph("Category", small), Paragraph("Activities", small)]]
    for category, count in category_counts.items():
        cat_rows.append([Paragraph(str(category), small), Paragraph(str(int(count)), small)])
    if len(cat_rows) == 1:
        cat_rows.append([Paragraph("No activity", small), Paragraph("0", small)])
    cat_table = Table(cat_rows, colWidths=[62*mm, 22*mm], repeatRows=1)
    cat_table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#eaf2f8")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.HexColor("#123b63")),
        ("GRID", (0,0), (-1,-1), 0.35, colors.HexColor("#d7e0ea")),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("ALIGN", (1,1), (1,-1), "CENTER"),
    ]))

    summary_table = Table([[
        [Paragraph("Status Summary", h2), status_table],
        [Paragraph("Category Activity", h2), cat_table],
    ]], colWidths=[110*mm, 110*mm])
    # Instead of nested headings being awkward, build a two-column section manually.
    summary_table = Table([[status_table, cat_table]], colWidths=[145*mm, 75*mm])
    summary_table.setStyle(TableStyle([("VALIGN", (0,0), (-1,-1), "TOP")]))
    story += [Paragraph("Graphical Status Summary", h2), summary_table, Spacer(1, 2*mm)]

    story += [Paragraph("Work Progress", h2)]
    progress_data = [
        [Paragraph("Panel cleaning", small), Paragraph(f"{panel_pct:.1f}% — {'4/4 cycles completed' if panel_total > 0 and panel_done >= panel_total else '0/4 cycles completed'}", small)],
        [Paragraph("Grass cutting", small), Paragraph(f"{grass_pct:.1f}% — {'6/6 cycles completed' if grass_total > 0 and grass_done >= grass_total else '0/6 cycles completed'}", small)],
    ]
    progress_table = Table(progress_data, colWidths=[45*mm, 25*mm])
    progress_table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), colors.HexColor("#f4f7fb")),
        ("GRID", (0,0), (-1,-1), 0.35, colors.HexColor("#d7e0ea")),
        ("ALIGN", (1,0), (1,-1), "CENTER"),
    ]))
    story += [progress_table, Spacer(1, 2*mm)]

    story += [Paragraph("Current Activities", h2)]
    if total:
        columns = ["Date", "Category", "Asset", "Worker", "Status", "Approval", "Remarks"]
        activity_rows = [[Paragraph(f"<b>{c}</b>", small) for c in columns]]
        # Keep the PDF management summary to one landscape A4 page.
        # The complete activity dataset remains available in the Excel export.
        pdf_rows = df.head(7)
        for _, row in pdf_rows.iterrows():
            vals = [
                row.get("Date", ""), row.get("Category", ""), row.get("Asset", ""),
                row.get("Worker", ""), row.get("Status", ""), row.get("Approval", ""),
                row.get("Remarks", "") or "",
            ]
            formatted=[]
            for i,v in enumerate(vals):
                if pd.isna(v): v = ""
                if hasattr(v, "strftime"): v = v.strftime("%d %b %Y")
                formatted.append(Paragraph(str(v)[:120], small))
            activity_rows.append(formatted)
        activity_table = Table(activity_rows, colWidths=[18*mm, 29*mm, 18*mm, 25*mm, 25*mm, 22*mm, 70*mm], repeatRows=1)
        activity_table.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#d9ecfb")),
            ("TEXTCOLOR", (0,0), (-1,0), colors.HexColor("#123b63")),
            ("GRID", (0,0), (-1,-1), 0.3, colors.HexColor("#d7e0ea")),
            ("VALIGN", (0,0), (-1,-1), "TOP"),
            ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor("#f7fafc")]),
            ("LEFTPADDING", (0,0), (-1,-1), 3),
            ("RIGHTPADDING", (0,0), (-1,-1), 3),
            ("TOPPADDING", (0,0), (-1,-1), 3),
            ("BOTTOMPADDING", (0,0), (-1,-1), 3),
        ]))
        story.append(activity_table)
        if total > len(pdf_rows):
            story.append(Paragraph(
                f"Showing the latest {len(pdf_rows)} of {total} activities on this one-page PDF. Export to Excel for the complete report.",
                small,
            ))
    else:
        story.append(Paragraph("No activities found for the selected period.", small))

    issue_db = SessionLocal()
    try:
        open_issues = issue_db.query(RepairItem).filter(
            RepairItem.plant_id == plant.id, RepairItem.status != "Completed"
        ).order_by(RepairItem.created_at.desc()).limit(4).all()
    finally:
        issue_db.close()
    story += [Paragraph("Current Maintenance Inspection Issues", h2)]
    if open_issues:
        issue_rows = [[Paragraph("<b>Asset</b>", small), Paragraph("<b>Component / Issue</b>", small),
                       Paragraph("<b>Status</b>", small), Paragraph("<b>Reason / Remarks</b>", small)]]
        for issue in open_issues:
            issue_rows.append([Paragraph(str(issue.asset_id or ""), small),
                Paragraph(str(issue.component or ""), small), Paragraph(str(issue.status or "Open"), small),
                Paragraph(str(issue.issue or issue.reason_not_completed or "Not specified")[:180], small)])
        issue_table = Table(issue_rows, colWidths=[25*mm, 48*mm, 25*mm, 120*mm], repeatRows=1)
        issue_table.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#d9ecfb")),
            ("GRID",(0,0),(-1,-1),.3,colors.HexColor("#d7e0ea")),
            ("VALIGN",(0,0),(-1,-1),"TOP"),
            ("LEFTPADDING",(0,0),(-1,-1),3),("RIGHTPADDING",(0,0),(-1,-1),3),
            ("TOPPADDING",(0,0),(-1,-1),2),("BOTTOMPADDING",(0,0),(-1,-1),2)]))
        story.append(issue_table)
    else:
        story.append(Paragraph("No open maintenance issues recorded.", small))
    story += [Spacer(1, 1*mm), Paragraph("Prepared for operational management review. Maintenance activity information only.", small)]

    doc.build(story)
    return output.getvalue()

def reports_page(db,user,plant):
    header(plant)
    st.title("Management Reports")
    st.caption("Readable operational reports for panel cleaning, grass cutting, and overall maintenance.")
    c1,c2=st.columns(2)
    with c1: start=st.date_input("From",date.today()-timedelta(days=30),key="report_start")
    with c2: end=st.date_input("To",date.today(),key="report_end")
    if start>end:
        st.error("The start date cannot be after the end date.")
        return

    df=_records_dataframe(db,plant.id,start,end)
    total=len(df)
    completed=int((df["Status"]=="Completed").sum()) if total else 0
    failed=int((df["Status"]!="Completed").sum()) if total else 0
    # Approval KPIs refer only to inspection records, never panel-cleaning or grass-cutting work.
    inspection_filter = [
        MaintenanceRecord.plant_id == plant.id,
        MaintenanceRecord.category.in_(list(PROCESS_CATEGORIES.keys())),
        MaintenanceRecord.task_date >= start,
        MaintenanceRecord.task_date <= end,
    ]
    pending_approvals = db.query(MaintenanceRecord).filter(
        *inspection_filter, MaintenanceRecord.approval_status == "Pending"
    ).count()
    approved_approvals = db.query(MaintenanceRecord).filter(
        *inspection_filter, MaintenanceRecord.approval_status == "Approved"
    ).count()
    import plotly.express as px
    pc, panel_done, panel_total = panel_cleaning_percent(db,plant.id,end)
    gc, grass_done, grass_total = grass_cutting_percent(db,plant.id,end)
    c1,c2,c3,c4,c5,c6,c7=st.columns(7)
    c1.metric("Activities",total)
    c2.metric("Green - Completed",completed)
    c3.metric("Red - Not Completed",failed)
    # Compact approval KPI cards: keep each label within its own card on two lines.
    c4.markdown(
        f"<div style='border:1px solid #d7e0ea;border-radius:8px;padding:10px 8px;min-height:91px;background:#ffffff;overflow:hidden;text-align:center;'>"
        f"<div style='font-size:22px;font-weight:650;line-height:1.1;color:#262730;margin-bottom:7px;'>{pending_approvals}</div>"
        f"<div style='font-size:10px;line-height:1.25;white-space:normal;overflow-wrap:anywhere;'>Pending Inspection<br>Approvals</div></div>",
        unsafe_allow_html=True,
    )
    c5.markdown(
        f"<div style='border:1px solid #d7e0ea;border-radius:8px;padding:10px 8px;min-height:91px;background:#ffffff;overflow:hidden;text-align:center;'>"
        f"<div style='font-size:22px;font-weight:650;line-height:1.1;color:#262730;margin-bottom:7px;'>{approved_approvals}</div>"
        f"<div style='font-size:10px;line-height:1.25;white-space:normal;overflow-wrap:anywhere;'>Approved Inspection<br>Approvals</div></div>",
        unsafe_allow_html=True,
    )
    c6.metric("Panel Cleaning", f"{pc:.0f}%")
    c7.metric("Grass Cutting", f"{gc:.0f}%")
    panel_left=max(0,panel_total-panel_done)
    grass_left=max(0,grass_total-grass_done)
    st.markdown("### Panel Cleaning and Grass Cutting")
    pcol,gcol=st.columns(2)
    with pcol:
        st.markdown("**Panel cleaning status**")
        pie=px.pie(pd.DataFrame({"Status":["Completed","Left"],"Count":[panel_done,panel_left]}),
                   names="Status",values="Count",hole=.38,
                   color="Status",color_discrete_map={"Completed":"#16a66a","Left":"#e5484d"})
        pie.update_traces(textinfo="label+value+percent")
        pie.update_layout(height=310,margin=dict(l=5,r=5,t=15,b=5),legend_title_text="")
        st.plotly_chart(pie,use_container_width=True,config={"displayModeBar":False})
        st.markdown(f"<div style='font-size:15px;font-weight:600'>{panel_done} of {panel_total} tables completed; {panel_left} remaining ({pc:.1f}% complete) · {'4/4 cycles completed' if panel_total and panel_done >= panel_total else '0/4 cycles completed'}</div>", unsafe_allow_html=True)
    with gcol:
        st.markdown("**Grass cutting status**")
        pie=px.pie(pd.DataFrame({"Status":["Completed","Left"],"Count":[grass_done,grass_left]}),
                   names="Status",values="Count",hole=.38,
                   color="Status",color_discrete_map={"Completed":"#16a66a","Left":"#e5484d"})
        pie.update_traces(textinfo="label+value+percent")
        pie.update_layout(height=310,margin=dict(l=5,r=5,t=15,b=5),legend_title_text="")
        st.plotly_chart(pie,use_container_width=True,config={"displayModeBar":False})
        st.markdown(f"<div style='font-size:15px;font-weight:600;text-align:right'>{grass_done} of {grass_total} zones completed; {grass_left} remaining ({gc:.1f}% complete) · {'6/6 cycles completed' if grass_total and grass_done >= grass_total else '0/6 cycles completed'}</div>", unsafe_allow_html=True)

    st.markdown("### Overall Maintenance Summary")
    left,right=st.columns(2)
    if total:
        with left:
            d=df["Status"].value_counts().rename_axis("Status").reset_index(name="Count")
            fig=px.bar(d,x="Status",y="Count",title="Maintenance status",text="Count",
                       color="Status",color_discrete_map={"Completed":"#16a66a","Failed":"#e5484d","Pending":"#e5484d"})
            fig.update_layout(margin=dict(l=10,r=10,t=50,b=10),height=320)
            st.plotly_chart(fig,use_container_width=True,config={"displayModeBar":False})
        with right:
            d=df["Category"].value_counts().rename_axis("Category").reset_index(name="Count")
            fig=px.bar(d,x="Count",y="Category",orientation="h",title="Activities by category",text="Count")
            fig.update_layout(margin=dict(l=10,r=10,t=50,b=10),height=320)
            st.plotly_chart(fig,use_container_width=True,config={"displayModeBar":False})

    st.markdown("### Overall Activity Report")
    if total:
        report_df = df.copy()
        report_df["Status"] = report_df["Status"].replace({
            "Completed": "Green - Completed",
            "Failed": "Red - Not Completed",
            "Pending": "Red - Not Completed",
        })
        st.dataframe(report_df,use_container_width=True,hide_index=True)
    else:
        st.info("No activities found for the selected period.")

    st.markdown("### Separate Panel Cleaning and Grass Cutting Reports")
    for category, label, pct in [
        ("Panel Cleaning","Panel Cleaning",pc),
        ("Grass Cutting","Grass Cutting",gc),
    ]:
        # Dedicated work reports intentionally exclude all inspection/approval records.
        cat_df=df[df["Category"]==category].copy() if total else pd.DataFrame(columns=df.columns)
        with st.container(border=True):
            st.markdown(f"**{label} report** — {len(cat_df)} recorded activities in the selected period")
            x1,x2=st.columns(2)
            x1.download_button(
                f"⬇️ {label} Excel", _excel_bytes(cat_df, label),
                f"{plant.code.lower()}_{category.lower().replace(' ','_')}_report.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key=f"excel_{category}", use_container_width=True)
            x2.download_button(
                f"📄 {label} PDF",
                _pdf_report_bytes(cat_df,plant,start,end,pc,gc,report_type=category),
                f"{plant.code.lower()}_{category.lower().replace(' ','_')}_report.pdf",
                "application/pdf", key=f"pdf_{category}", use_container_width=True)

    st.markdown("### Overall Summary Downloads")
    x1,x2=st.columns(2)
    x1.download_button("⬇️ Export overall report to Excel",_excel_bytes(df,"Overall Summary"),
        f"{plant.code.lower()}_overall_maintenance_summary.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",use_container_width=True)
    x2.download_button("📄 Export overall summary to PDF",
        _pdf_report_bytes(df,plant,start,end,pc,gc),
        f"{plant.code.lower()}_overall_maintenance_summary.pdf","application/pdf",use_container_width=True)

def approvals_page(db,user,plant):
    header(plant)
    st.title("✅ Approvals & Sign-off")
    st.caption("Engineers and administrators can approve or decline maintenance inspections and record an electronic signature.")
    base_filter = [MaintenanceRecord.plant_id == plant.id, MaintenanceRecord.category.in_(list(PROCESS_CATEGORIES.keys()))]
    pending = db.query(MaintenanceRecord).filter(*base_filter, MaintenanceRecord.approval_status == "Pending").order_by(MaintenanceRecord.task_date.desc()).limit(100).all()
    reviewed = db.query(MaintenanceRecord).filter(*base_filter, MaintenanceRecord.approval_status.in_(["Approved", "Declined"])).order_by(MaintenanceRecord.approved_at.desc()).limit(100).all()
    approved_count = db.query(MaintenanceRecord).filter(*base_filter, MaintenanceRecord.approval_status == "Approved").count()
    declined_count = db.query(MaintenanceRecord).filter(*base_filter, MaintenanceRecord.approval_status == "Declined").count()
    c1,c2,c3=st.columns(3); c1.metric("Awaiting review",len(pending)); c2.metric("Approved",approved_count); c3.metric("Declined",declined_count)

    workers={u.id:u.full_name for u in db.query(User).all()}
    if pending:
        st.markdown("### 🔎 Awaiting review")
        for rec in pending:
            details=parse_details(rec); items=details.get("items",{}); red_items=[k for k,v in items.items() if not k.endswith("__reason") and v=="RED"]
            with st.container(border=True):
                st.markdown(f"### {rec.category} • {rec.asset_id}")
                st.write(f"**Inspection date:** {rec.task_date:%d %b %Y} • **Submitted by:** {workers.get(rec.worker_id,'Unassigned')} • **Status:** {rec.status}")
                if red_items: st.markdown("**Items requiring attention:** "+", ".join(red_items))
                if rec.remarks: st.write(f"**Remarks:** {rec.remarks}")
                signature=st.text_input("Electronic signature — type your full name",key=f"sig_{rec.id}",placeholder="Engineer / Administrator full name")
                comment=st.text_area("Approval comment",key=f"approval_comment_{rec.id}",placeholder="Optional for approval; required for decline")
                a,b=st.columns(2)
                if a.button("✅ Approve & Sign",key=f"approve_{rec.id}",type="primary",use_container_width=True):
                    if not signature.strip(): st.error("Enter your electronic signature first.")
                    else:
                        rec.approval_status="Approved"; rec.approved_by=user.id; rec.approved_at=datetime.utcnow(); rec.approval_signature=signature.strip(); rec.approval_comment=comment.strip(); db.commit(); st.success("Inspection approved and electronically signed."); st.rerun()
                if b.button("🔴 Decline",key=f"decline_{rec.id}",use_container_width=True):
                    if not signature.strip(): st.error("Enter your electronic signature first.")
                    elif not comment.strip(): st.error("Add a reason when declining an inspection.")
                    else:
                        rec.approval_status="Declined"; rec.approved_by=user.id; rec.approved_at=datetime.utcnow(); rec.approval_signature=signature.strip(); rec.approval_comment=comment.strip(); db.commit(); st.warning("Inspection declined and returned for correction."); st.rerun()
    else:
        st.success("No maintenance inspections are waiting for approval.")

    st.markdown("### ✏️ Reviewed inspections")
    if not reviewed:
        st.caption("No approved or declined inspection records yet.")
        return

    for rec in reviewed:
        state = "Approved" if rec.approval_status == "Approved" else "Declined"
        cls = "good" if state == "Approved" else "bad"
        with st.expander(f"{rec.task_date:%d %b %Y} • {rec.category} • {rec.asset_id} • {state}"):
            st.markdown(f"**Status:** <span class='{cls}'>{state}</span> &nbsp; • &nbsp; **Reviewed by:** {workers.get(rec.approved_by,'—')} &nbsp; • &nbsp; **Reviewed:** {rec.approved_at or '—'}", unsafe_allow_html=True)
            signature = st.text_input("Electronic signature", value=rec.approval_signature or "", key=f"review_sig_{rec.id}")
            comment = st.text_area("Approval comment", value=rec.approval_comment or "", key=f"review_comment_{rec.id}")
            new_state = st.selectbox("Decision", ["Approved", "Declined"], index=0 if state == "Approved" else 1, key=f"review_state_{rec.id}")
            a,b=st.columns(2)
            if a.button("💾 Save changes", key=f"review_save_{rec.id}", use_container_width=True):
                if not signature.strip(): st.error("Electronic signature is required.")
                elif new_state == "Declined" and not comment.strip(): st.error("A comment is required when declining.")
                else:
                    rec.approval_status=new_state; rec.approved_by=user.id; rec.approved_at=datetime.utcnow(); rec.approval_signature=signature.strip(); rec.approval_comment=comment.strip(); db.commit(); st.success("Review updated."); st.rerun()
            if b.button("🗑️ Delete inspection record", key=f"review_delete_{rec.id}", use_container_width=True):
                db.delete(rec); db.commit(); st.success("Inspection record deleted."); st.rerun()


# ============================================================
# Admin settings
# ============================================================

def admin_page(db):
    st.title("⚙️ Administration")
    pending_records=db.query(MaintenanceRecord).filter(MaintenanceRecord.category.in_(list(PROCESS_CATEGORIES.keys())),MaintenanceRecord.approval_status=="Pending").count()
    c1,c2,c3=st.columns(3); c1.metric("Users",db.query(User).count()); c2.metric("Plants",db.query(Plant).count()); c3.metric("Inspection approvals",pending_records)
    st.markdown("### 👥 User access"); st.write("Accounts are created active and can sign in immediately. Use **Users** to assign plants, change roles, or deactivate accounts.")
    st.markdown("### ✅ Maintenance sign-off"); st.write("Engineers and administrators can review inspections, approve or decline them, and record an electronic signature from **Approvals & Sign-off**.")
    st.markdown("### Management Reports"); st.write("Engineers and Administrators can use **Reports** for graphical completion percentages, inspection status, loss & repair summaries, Excel export and PDF management reports.")


# ============================================================
# Main
# ============================================================

def main():
    if "user" not in st.session_state:
        login_page()
        return

    css()
    db = db_session()

    user = db.query(User).filter(User.id == st.session_state.user.id).first()

    if not user or not user.active:
        st.session_state.clear()
        st.error("Your account is inactive or no longer available.")
        st.rerun()

    page, plant_id = top_nav(db, user)

    if not plant_id:
        st.warning("Ask an administrator to assign you to a plant.")
        db.close()
        return

    plant = db.query(Plant).filter(Plant.id == plant_id).first()

    if not plant:
        st.error("Selected plant not found.")
        db.close()
        return

    if page == "🏠 Overview":
        overview_page(db, user, plant)
    elif page == "🧹 Panel Cleaning":
        panel_cleaning_page(db, user, plant)
    elif page == "🌿 Grass Cutting":
        grass_cutting_page(db, user, plant)
    elif page == "🔧 Maintenance Inspections":
        inspection_page(db, user, plant)
    elif page == "⚠️ Repair & Maintenance":
        loss_repair_page(db, user, plant)
    elif page == "📋 Activity Log":
        activity_page(db, user, plant)
    elif page == "👥 Users" and user.role in ("admin", "engineer"):
        users_page(db, user, plant)
    elif page == "🏭 Plants" and user.role in ("admin", "engineer"):
        plants_page(db)
    elif page == "📊 Reports" and user.role in ("admin", "engineer"):
        reports_page(db, user, plant)
    elif page == "✅ Approvals & Sign-off" and user.role in ("admin", "engineer"):
        approvals_page(db, user, plant)
    elif page == "⏰ Reminders" and user.role in ("admin", "engineer"):
        reminders_page(db)
    elif page == "⚙️ Admin" and user.role == "admin":
        admin_page(db)

    db.close()


if __name__ == "__main__":
    main()









