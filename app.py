
import os
import io
import json
import base64
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
    page_title="Solar Plant Maintenance System",
    page_icon="☀️",
    layout="wide",
    initial_sidebar_state="expanded",
)

APP_TITLE = "Solar Power Plant Maintenance System"
DEFAULT_DB = "sqlite:///solar_maintenance.db"
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
    return True


initialize_database(engine)


# ============================================================
# Password helpers
# ============================================================

def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 180_000)
    return f"pbkdf2_sha256$180000${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt_hex, digest_hex = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(iterations),
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except Exception:
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
            "Lightning Arresters OK",
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
            "Front Glass Damage",
            "Back Glass Damage",
            "Diode Damage",
        ],
    },
    "DC Cable Inspection": {
        "frequency": "Monthly",
        "asset_type": "Table",
        "items": [
            "DC Cable Condition OK",
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
            "Multi-Function Meter Readings OK",
            "Fans / Ventilation Duct Is Clean",
            "Thermo Works",
            "Enclosure OK",
            "Surge Protection Active",
            "Fuse - All Works",
            "Meter Panel Battery Status Functions",
            "Protection Module OK",
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
            "OVCB Tripping Works",
            "OVCB Tripping Count Recorded",
            "OVCB Auto / Manual Works",
            "DDLO OK",
            "Fence OK",
            "Warning Signs Visible and OK",
        ],
    },
    "AC Inspection": {
        "frequency": "Every 4 Months",
        "asset_type": "AC Unit",
        "items": [
            "Outdoor - No Noise/Vibration of Compressor",
            "Outdoor - No Noise/Vibration of Condenser Fan",
            "Outdoor - Gas Tubeline Insulation OK",
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
            ("HOF", "HOF"),
            ("MTR-I", "Matara I Solar Power Plant"),
            ("MTR-II", "Matara II Solar Power Plant"),
            ("HS1", "Horana I Solar Power Plant"),
            ("HS2", "Horana II Solar Power Plant"),
            ("MDP", "MDP Solar Power Plant"),
            ("ORK", "ORK Solar Power Plant"),
        ]
        for site_code, site_name in configured_sites:
            if not db.query(Plant).filter(Plant.code == site_code).first():
                db.add(Plant(code=site_code, name=site_name, capacity_mw=0,
                             inverter_count=10, table_count=80, zone_count=10, active=True))
        db.commit()

        # HRN has been renamed to HS1 in the application. If an older Neon database
        # still contains HRN, migrate it to HS1 without deleting the plant data.
        old_hrn = db.query(Plant).filter(Plant.code == "HRN").first()
        hs1_plant = db.query(Plant).filter(Plant.code == "HS1").first()
        if old_hrn and not hs1_plant:
            old_hrn.code = "HS1"
            old_hrn.name = "HS1 Solar Power Plant"
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
            hs1_plant.name = "HS1 Solar Power Plant"
            if hs1_plant.capacity_mw in (None, 0):
                hs1_plant.capacity_mw = 2

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
            # Always synchronize the configured administrator account.
            # This fixes existing Neon accounts whose password, role, or
            # approval state does not match the current admin configuration.
            admin.full_name = "System Administrator"
            admin.role = "admin"
            admin.approved = True
            admin.active = True
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
            "Action required: inspect the Loss & Repair section and update the repair status."
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
                "Please review Loss & Repair."
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
        [data-testid="stSidebar"]{display:block!important;min-width:250px!important}
        [data-testid="stSidebar"]>div{background:var(--surface);border-right:1px solid var(--border)}
        .sidebar-brand{font-size:20px;font-weight:850;color:var(--text);padding:8px 0 2px}
        .sidebar-sub{font-size:12px;color:var(--muted);margin-bottom:14px}
        .top-title{font-size:30px;font-weight:800;color:var(--text);line-height:1.15}.top-subtitle{color:var(--muted);font-size:14px}
        .topbar{background:var(--surface);border:1px solid var(--border);border-radius:16px;padding:12px 16px;margin:0 0 10px;box-shadow:var(--shadow)}
        .nav-wrap{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:4px 8px;margin:0 0 18px;box-shadow:var(--shadow);position:sticky;top:0;z-index:50}
        .nav-wrap [role="radiogroup"]{gap:4px;flex-wrap:wrap}.nav-wrap label{border-radius:9px;padding:5px 10px!important;border:1px solid transparent;min-height:34px}.nav-wrap label:hover{background:var(--surface2);border-color:var(--border)}
        .card,.dashboard-card,.status-tile{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:18px;margin-bottom:12px;box-shadow:var(--shadow)}
        .dashboard-card{height:158px;min-height:158px;display:flex;flex-direction:column;justify-content:flex-start;gap:8px;padding:14px 14px 12px;box-sizing:border-box;overflow:hidden}.dashboard-card h4{margin:0;color:var(--muted);font-size:13px;line-height:1.25;min-height:17px}.dashboard-value{color:var(--text);font-size:28px;font-weight:800;line-height:1.1;margin-top:2px}.dashboard-label,.small-muted{color:var(--muted);font-size:11px;line-height:1.25;overflow-wrap:anywhere}.dashboard-label{margin-top:auto;min-height:29px}.pending-inline{display:block;margin-top:2px;white-space:normal;line-height:1.2}.good{color:var(--green);font-weight:800}.bad{color:var(--red);font-weight:800}.warn{color:var(--yellow);font-weight:800}.section-title{font-size:20px;font-weight:800;color:var(--text)}
        div[data-testid="stMetric"]{background:var(--surface);border:1px solid var(--border);padding:12px;border-radius:12px;box-shadow:var(--shadow);min-height:108px;box-sizing:border-box;min-width:0}
        div[data-testid="stMetric"] label{white-space:normal!important;overflow-wrap:anywhere;line-height:1.25}
        /* Keep Streamlit's image/element toolbar compact when it appears. */
        [data-testid="stElementToolbar"]{transform:scale(.72);transform-origin:top right;right:3px!important;top:3px!important}
        .modebar-container{transform:scale(.72);transform-origin:top right}
        div[data-baseweb="select"]>div,div[data-baseweb="input"]>div,div[data-baseweb="textarea"]>div{background:var(--surface)}
        .status-tile{padding:13px 14px;min-height:78px;box-sizing:border-box}.status-dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:8px}.status-green{background:var(--green)}.status-red{background:var(--red)}.status-pending{background:var(--yellow)}
        .group-card{background:var(--surface2);border:1px solid var(--border);border-radius:14px;padding:18px 20px;margin:8px 0 20px;width:100%;box-sizing:border-box}.group-title{font-size:16px;font-weight:800;color:var(--text);margin:12px 0 8px}.check-item{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:16px 18px;margin:6px 0;min-height:58px;width:100%;display:flex;align-items:center;box-sizing:border-box}.group-card [data-testid="stHorizontalBlock"]{gap:1.1rem;align-items:center}.group-card [data-testid="stSelectbox"]{min-width:180px}.group-card [data-testid="stTextInput"]{min-width:220px}
        .layout-wrap{background:var(--surface);border:1px solid var(--border);border-radius:16px;padding:12px;box-shadow:var(--shadow)}
        .solar-map{position:relative;width:100%;min-height:900px;aspect-ratio:1280/1000;overflow:hidden;border-radius:14px;background:radial-gradient(ellipse at 50% 45%,#173d4b 0%,#09233b 55%,#06162a 100%);border:1px solid #244765;box-shadow:inset 0 0 0 8px rgba(35,93,125,.12)}
        .solar-map-title{position:absolute;left:14px;top:12px;background:rgba(4,24,45,.90);color:#fff;padding:8px 12px;border-radius:8px;font-weight:800;z-index:5}.solar-map-legend{position:absolute;right:12px;bottom:12px;background:rgba(4,24,45,.92);color:#fff;padding:8px 12px;border-radius:8px;z-index:5;font-size:12px}.legend-dot{display:inline-block;width:12px;height:8px;border-radius:2px;margin-right:5px}.legend-green{background:#16e879}.legend-red{background:#ff4e59}
        .map-zone{position:absolute;padding:7px;background:rgba(6,24,42,.66);border:2px solid rgba(255,78,89,.9);border-radius:10px;box-sizing:border-box;pointer-events:none;box-shadow:0 4px 16px rgba(0,0,0,.2)}.map-zone-title{display:inline-block;color:#fff;padding:5px 8px;border-radius:5px;font-size:12px;font-weight:800;margin-bottom:6px;box-shadow:0 1px 3px rgba(0,0,0,.4)}.map-tables{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:4px}.map-table{height:12px;border-radius:3px;opacity:1;box-shadow:0 0 0 1px rgba(0,0,0,.28)}.map-green{background:#16e879}.map-red{background:#ff4e59}.map-controls{position:absolute;right:10px;top:10px;width:108px;max-width:18%;background:rgba(4,24,45,.88);color:#fff;padding:5px 7px;border-radius:7px;z-index:5;font-size:9px;line-height:1.35;box-sizing:border-box;overflow-wrap:anywhere}.map-controls b{display:block;margin-bottom:2px}
        .mini-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}.mini-item{background:var(--surface);border:1px solid var(--border);border-radius:9px;padding:8px 7px;min-height:50px;box-sizing:border-box}.mini-item .name{font-weight:800;font-size:12px}.mini-item .state{font-size:10px;margin-top:3px}.loss-tile{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:10px;min-height:76px;box-sizing:border-box}.loss-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}
        .map-section-title{font-size:18px;font-weight:800;color:var(--text);margin:2px 0 10px}.top-user{font-size:12px;color:var(--muted);text-align:right;padding-top:4px}
        .overview-notification{background:var(--surface);border:1px solid var(--border);border-left:4px solid var(--red);border-radius:12px;padding:9px 11px;min-height:68px;box-shadow:var(--shadow);box-sizing:border-box}.overview-notification .small-muted{display:block;margin:6px 0;overflow-wrap:anywhere}.overview-empty{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:14px;color:var(--muted);box-shadow:var(--shadow)}
        .plant-detail-card{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:14px;min-height:105px;margin-bottom:12px;box-shadow:var(--shadow);box-sizing:border-box;overflow:hidden}.plant-detail-label{color:var(--muted);font-size:12px;margin-bottom:7px}.plant-detail-value{color:var(--text);font-size:19px;font-weight:800;line-height:1.2;overflow-wrap:anywhere;word-break:break-word}.plant-detail-hint{color:var(--muted);font-size:10px;margin-top:6px}.staff-summary-card{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:16px;min-height:122px;box-shadow:var(--shadow);box-sizing:border-box;overflow:hidden}.staff-summary-label{color:var(--muted);font-size:12px;margin-bottom:10px}.staff-summary-value{color:var(--text);font-size:29px;font-weight:800;line-height:1.15;overflow-wrap:anywhere;word-break:break-word;white-space:normal}.staff-summary-text{font-size:22px}

        @media(max-width:900px){.top-title{font-size:23px}.mini-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.loss-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.solar-map{min-height:980px;aspect-ratio:1/1.15}.map-zone-title{font-size:10px;padding:4px 5px}.map-controls{width:92px;font-size:8px;max-width:24%}}
        </style>""",unsafe_allow_html=True)

def header(plant):
    st.markdown(
        f"""
        <div class="top-title">☀️ {plant.name} <span style="color:#16a66a;">Maintenance Dashboard</span></div>
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

def login_page():
    css()

    st.markdown(
        """
        <div style="text-align:center; margin-top:20px;">
            <div style="font-size:55px;">☀️</div>
            <div class="top-subtitle">Maintenance automation • inspections • repair tracking • notifications</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    db = db_session()
    plants = db.query(Plant).filter(Plant.active == True).order_by(Plant.name).all()

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

    st.markdown("### Plant overview")
    cols = st.columns(min(4, max(1, len(plants))))
    for i, p in enumerate(plants):
        with cols[i % len(cols)]:
            st.markdown(
                f"""
                <div class="card">
                    <div class="section-title">☀️ {p.name}</div>
                    <div class="small-muted">{p.code}</div>
                    <hr>
                    <b>{p.capacity_mw:g} MW</b> capacity<br>
                    {p.inverter_count} inverters • {p.table_count} tables • {p.zone_count} zones
                </div>
                """,
                unsafe_allow_html=True,
            )
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
    plants = allowed_plants(db, user)
    if not plants:
        st.sidebar.markdown('<div class="sidebar-brand">☀️ Solar Maintenance</div>', unsafe_allow_html=True)
        st.sidebar.error("No plant is assigned to this account. Ask an administrator to assign a plant.")
        if st.sidebar.button("🚪 Sign out", key="sidebar_signout_unassigned"):
            st.session_state.clear(); st.rerun()
        return None, None

    plant_options = {p.name: p.id for p in plants}
    current_id = st.session_state.get("selected_plant_id") or (user.plant_id if user.role not in ("admin", "engineer") else plants[0].id)
    current_name = next((n for n, pid in plant_options.items() if pid == current_id), plants[0].name)

    # Keep the signed-in identity in the main area; sign-out lives at the bottom of the sidebar navigation.
    st.markdown(f'<div class="top-user" style="text-align:left">{user.full_name} • {user.role.title()}</div>', unsafe_allow_html=True)

    with st.sidebar:
        st.markdown('<div class="sidebar-brand">☀️ Solar Maintenance</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="sidebar-sub">{user.full_name} • {user.role.title()}</div>', unsafe_allow_html=True)
        selected_name = st.selectbox("Plant", list(plant_options.keys()), index=list(plant_options.keys()).index(current_name), key="top_plant")
        st.session_state.selected_plant_id = plant_options[selected_name]
        st.divider()
        notification_center(db, user)
        st.caption("🟢 System Online")

    if user.role == "supervisor":
        pages = ["🏠 Overview", "👷 Operating Staff & Site Contacts", "🧹 Panel Cleaning", "🌿 Grass Cutting"]
    else:
        pages = ["🏠 Overview", "👷 Operating Staff & Site Contacts", "🧹 Panel Cleaning", "🌿 Grass Cutting", "🔧 Maintenance Inspections", "⚠️ Loss & Repair", "📋 Activity Log"]
    if user.role in ("admin", "engineer"):
        pages += ["📊 Reports", "✅ Approvals & Sign-off"]
    if user.role == "admin":
        pages += ["👥 Users", "🏭 Plants", "⏰ Reminders", "⚙️ Admin"]
    # Keep navigation in the left sidebar, as requested.
    with st.sidebar:
        st.divider()
        page = st.radio("Navigation", pages, key="top_navigation_v2")
        st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)
        if st.button("🚪 Sign out", use_container_width=True, key="sidebar_signout_bottom"):
            st.session_state.clear()
            st.rerun()

    reminder_key = f"_reminders_checked_{st.session_state.selected_plant_id}_{date.today().isoformat()}"
    if user.role == "admin" and not st.session_state.get(reminder_key):
        send_due_reminders(db, st.session_state.selected_plant_id)
        st.session_state[reminder_key] = True

    return page, st.session_state.selected_plant_id

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
        '<div class="map-controls"><b>Live Status</b><span style="color:#16e879">● GREEN = Completed</span><br><span style="color:#ff4e59">● RED = Pending</span><br>Table = panel cleaning<br>Border = grass cutting</div>',
        '<div class="solar-map-legend"><span class="legend-dot legend-green"></span>Completed &nbsp;&nbsp; <span class="legend-dot legend-red"></span>Pending / Pending</div>'
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

def satellite_table_map_page(db, user, plant, embedded=False):
    """Show the real satellite photo with each solar-table row coloured by saved status."""
    import plotly.graph_objects as go
    from PIL import Image

    if not embedded:
        header(plant)
        st.markdown("## 🛰️ Solar Plant Map")
        st.caption("Each solar-table strip is coloured by panel-cleaning status. Zone outlines show grass-cutting status.")

    task_date = st.date_input("Satellite status date", value=date.today(), key=f"satellite_map_date_{plant.id}")
    image_candidates = [
        os.path.join(os.path.dirname(__file__), "assets", "HR1.jpeg"),
        os.path.join(os.path.dirname(__file__), "assets", "HR1.jpg"),
        os.path.join(os.path.dirname(__file__), "HR1.jpeg"),
    ]
    image_path = next((p for p in image_candidates if os.path.exists(p)), None)
    if not image_path:
        st.error("Satellite image not found. Add the supplied site photo to the repository as assets/HR1.jpeg.")
        return

    with Image.open(image_path) as im:
        img_width, img_height = im.size
    with open(image_path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("ascii")
    image_uri = f"data:image/jpeg;base64,{encoded}"

    assets = asset_list(plant, "Table")
    panel_records = status_map(db, plant.id, "Panel Cleaning", task_date)
    completed = {a for a, r in panel_records.items() if r.status == "Completed"}
    grass_records = status_map(db, plant.id, "Grass Cutting", task_date)
    zone_assets = asset_list(plant, "Zone")
    completed_grass = {a for a, r in grass_records.items() if r.status == "Completed"}
    pending_count = max(0, len(assets) - len(completed))

    # These quadrilaterals follow the real solar-array blocks in assets/HR1.jpeg.
    # Coordinates use the 1280x720 reference photo and scale with the actual image.
    # Each block is filled with separate long row-shaped polygons, not floating dots.
    blocks = [
        # top-left array
        ((250, 8), (438, 0), (300, 325), (465, 320)),
        # upper middle-left array
        ((466, 145), (653, 132), (493, 350), (672, 348)),
        # upper middle-right array
        ((650, 78), (833, 58), (690, 348), (850, 340)),
        # upper-right array
        ((833, 15), (1045, 0), (870, 315), (1072, 310)),
        # lower-left array
        ((305, 390), (492, 377), (340, 700), (515, 710)),
        # lower middle-left array
        ((485, 390), (665, 370), (520, 710), (690, 710)),
        # lower middle-right array
        ((666, 350), (855, 328), (705, 704), (875, 695)),
        # lower-right array
        ((850, 325), (1074, 310), (885, 685), (1095, 670)),
    ]
    # Allocate table records over the physical blocks in proportion to their visible size.
    weights = [10, 8, 9, 10, 10, 10, 11, 12]
    if assets:
        total_weight = sum(weights)
        counts = [len(assets) * w // total_weight for w in weights]
        for i in range(len(assets) - sum(counts)):
            counts[i % len(counts)] += 1
    else:
        counts = [0] * len(blocks)

    scale_x, scale_y = img_width / 1280.0, img_height / 720.0
    fig = go.Figure()
    fig.add_layout_image(dict(source=image_uri, xref="x", yref="y", x=0, y=img_height,
                              sizex=img_width, sizey=img_height, sizing="stretch", layer="below"))

    # Draw grass-cutting status around each physical array block. Zone status is
    # selected from the site's configured zones and is saved independently from cleaning.
    zone_count = max(1, len(zone_assets))
    for block_idx, block in enumerate(blocks):
        if not zone_assets:
            break
        zone_idx = min(zone_count - 1, round(block_idx * (zone_count - 1) / max(1, len(blocks) - 1)))
        zone_name = zone_assets[zone_idx]
        grass_done = zone_name in completed_grass
        points = [block[0], block[1], block[3], block[2], block[0]]
        fig.add_trace(go.Scatter(
            x=[x * scale_x for x, y in points],
            y=[img_height - y * scale_y for x, y in points],
            mode="lines", line=dict(color="#13c982" if grass_done else "#ff4e59", width=3),
            hoverinfo="text", text=[f"{zone_name} — Grass cutting: {'Completed' if grass_done else 'Pending'}"] * len(points),
            showlegend=False,
        ))

    # A table is represented by a narrow polygon matching one real panel row.
    asset_index = 0
    for block_idx, (block, count) in enumerate(zip(blocks, counts)):
        if count <= 0:
            continue
        top_left, top_right, bottom_left, bottom_right = block
        # Slight inset avoids painting the paths and gaps between solar tables.
        inset = 0.018
        for row_idx in range(count):
            if asset_index >= len(assets):
                break
            asset = assets[asset_index]
            asset_index += 1
            f0 = row_idx / count + inset / count
            f1 = (row_idx + 1) / count - inset / count

            def interp(a, b, f):
                return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)

            # Interpolate both ends along the slanted sides of the array block.
            tl = interp(top_left, bottom_left, f0)
            tr = interp(top_right, bottom_right, f0)
            br = interp(top_right, bottom_right, f1)
            bl = interp(top_left, bottom_left, f1)
            polygon = [tl, tr, br, bl, tl]
            is_done = asset in completed
            zone_idx = min(zone_count - 1, int(asset_index * zone_count / max(1, len(assets)))) if zone_assets else 0
            zone_name = zone_assets[zone_idx] if zone_assets else f"Zone {zone_idx + 1}"
            grass_done = zone_name in completed_grass
            fig.add_trace(go.Scatter(
                x=[x * scale_x for x, y in polygon],
                y=[img_height - y * scale_y for x, y in polygon],
                mode="lines", fill="toself",
                fillcolor="#16c982" if is_done else "#ff4e59",
                line=dict(color="#062337", width=1),
                hoverinfo="text",
                text=[f"{asset}<br>Panel cleaning: {'Completed' if is_done else 'Pending'}<br>{zone_name} grass cutting: {'Completed' if grass_done else 'Pending'}"] * len(polygon),
                showlegend=False,
            ))

    fig.update_xaxes(range=[0, img_width], visible=False, fixedrange=True, constrain="domain")
    fig.update_yaxes(range=[0, img_height], visible=False, fixedrange=True, scaleanchor="x", scaleratio=1)
    fig.update_layout(
        height=440 if embedded else 700, margin=dict(l=0, r=0, t=0, b=0),
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
    )
    green_col, red_col, grass_col = st.columns([1, 1, 1.45], gap="small")
    green_col.metric("Panel cleaning completed", len(completed))
    red_col.metric("Panel cleaning pending", pending_count)
    grass_col.metric("Grass-cutting zones completed", len(completed_grass))
    if not embedded:
        st.caption("Save progress in Panel Cleaning and Grass Cutting. The satellite table colours and zone borders update from the saved records for the selected date.")


def operations_staff_section(db, user, plant):
    """Show/edit per-site operating headcount and contacts; values persist in Neon."""
    profile = db.query(PlantOperationsProfile).filter(PlantOperationsProfile.plant_id == plant.id).first()
    if profile is None:
        defaults = {
            "HOF": ("", "Mr. Mahela Wanigasooriya", "Mr. Mahela Wanigasooriya"),
            "HS1": ("HS1 In-Charge", "Mr. Prasanna Kottege", "Mr. Mahela Wanigasooriya"),
            "MTR": ("MTR In-Charge", "Mr. Prasanna Kottege", "Mr. Mahela Wanigasooriya"),
            "MDP": ("MDP In-Charge", "Mr. Prasanna Kottege", "Mr. Mahela Wanigasooriya"),
            "ORK": ("ORK In-Charge", "Mr. Prasanna Kottege", "Mr. Mahela Wanigasooriya"),
            "MTR I": ("MTR I In-Charge", "Mr. Prasanna Kottege", "Mr. Mahela Wanigasooriya"),
            "MTR II": ("MTR II In-Charge", "Mr. Prasanna Kottege", "Mr. Mahela Wanigasooriya"),
            "HS1": ("HS1 In-Charge", "Mr. Prasanna Kottege", "Mr. Mahela Wanigasooriya"),
            "HS2": ("HS2 In-Charge", "Mr. Prasanna Kottege", "Mr. Mahela Wanigasooriya"),
        }
        sup, eng, hof = defaults.get(plant.code.upper(), ("", "", "Mr. Mahela Wanigasooriya"))
        profile = PlantOperationsProfile(plant_id=plant.id, operating_staff_count=0,
                                         supervisor_name=sup, engineer_name=eng, hof_name=hof)
        db.add(profile)
        db.commit()
        db.refresh(profile)

    st.markdown("### 👷 Operating Staff & Site Contacts")
    st.caption("Headcount and contact details are site-specific. An authorised supervisor, engineer, or administrator can update these values later.")
    # All sites share the published operations leadership roster; the site profile below is editable.
    leadership = [
        ("Mr. Roshan Siriwardana", "Director – Operations"),
        ("Mr. Manchanayeke Upul", "General Manager – Operations"),
        ("Mr. Sampath Raweendra", "Chief Electrical Engineer"),
        ("Mr. Damitha Bandulawansha", "Assistant Manager – Operations"),
        ("Mr. Prasanna Kottege", "Electrical Engineer"),
        ("Mr. Mahela Wanigasooriya", "Mechanical Engineer / HOF staff"),
    ]
    with st.expander("Operations leadership roster", expanded=False):
        st.dataframe(pd.DataFrame(leadership, columns=["Name", "Position"]), use_container_width=True, hide_index=True)

    editable = user.role in ("admin", "engineer", "supervisor")
    with st.form(f"site_staff_profile_{plant.id}"):
        # Give the contact fields more room so full engineer/supervisor names remain visible.
        c1, c2 = st.columns([0.9, 1.35], gap="large")
        with c1:
            count = st.number_input("No. of operating staff members", min_value=0, max_value=10000,
                                    value=int(profile.operating_staff_count or 0), step=1, disabled=not editable)
            supervisor = st.text_input("Site supervisor / in-charge", value=profile.supervisor_name or "",
                                       placeholder="Enter supervisor name", disabled=not editable)
        with c2:
            engineer = st.text_input("Relevant engineer", value=profile.engineer_name or "",
                                     placeholder="Enter responsible engineer", disabled=not editable)
            hof = st.text_input("HOF / mechanical contact", value=profile.hof_name or "Mr. Mahela Wanigasooriya",
                                disabled=not editable)
        submitted = st.form_submit_button("Save site staff details", use_container_width=True, disabled=not editable)
        if submitted:
            profile.operating_staff_count = int(count)
            profile.supervisor_name = supervisor.strip()
            profile.engineer_name = engineer.strip()
            profile.hof_name = hof.strip()
            profile.updated_at = datetime.utcnow()
            db.commit()
            st.success(f"Operating staff and contacts saved for {plant.name}.")
            st.rerun()
    c1, c2, c3 = st.columns([0.9, 1.15, 1.55], gap="large")
    c1.markdown(f'<div class="staff-summary-card"><div class="staff-summary-label">Operating staff</div><div class="staff-summary-value">{int(profile.operating_staff_count or 0)}</div></div>', unsafe_allow_html=True)
    c2.markdown(f'<div class="staff-summary-card"><div class="staff-summary-label">Supervisor / In-charge</div><div class="staff-summary-value staff-summary-text">{profile.supervisor_name or "Not set"}</div></div>', unsafe_allow_html=True)
    c3.markdown(f'<div class="staff-summary-card"><div class="staff-summary-label">Engineer</div><div class="staff-summary-value staff-summary-text">{profile.engineer_name or "Not set"}</div></div>', unsafe_allow_html=True)


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
        "MTR": "Sudheera", "MTR-I": "Sudheera", "MTR-II": "Sudheera",
        "MDP": "Sansala", "HOF": "Not provided",
    }
    supervisor = (profile.supervisor_name.strip() if profile and profile.supervisor_name else "") or supervisors.get(plant.code, "Not provided")
    engineers = {
        "HS1": "Mr. Prasanna Kottege", "HS2": "Mr. Prasanna Kottege",
        "MTR": "Mr. Mahela Wanigasooriya", "MTR-I": "Mr. Mahela Wanigasooriya",
        "MTR-II": "Mr. Mahela Wanigasooriya", "MDP": "Mr. Mahela Wanigasooriya",
        "ORK": "Not provided", "HOF": "Mr. Mahela Wanigasooriya",
    }
    engineer = (profile.engineer_name.strip() if profile and profile.engineer_name else "") or engineers.get(plant.code, "Not provided")

    st.markdown("## 🏭 Power Plant Details")
    detail_rows = [
        ("Plant", plant.name), ("Site code", plant.code),
        ("AC Capacity", f"{plant.capacity_mw:g} MW"),
        ("Inverters", f"{plant.inverter_count}"), ("Solar tables", f"{plant.table_count}"),
        ("Zones", f"{plant.zone_count}"), ("Operating staff", str(staff_count)),
        ("Plant In-Charge / Supervisor", supervisor), ("Relevant Engineer", engineer),
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
        "ORK": {"Power Plant": "ORK (Monaragala)", "AC Capacity": "0.88 MW", "Panels": "535 Wp × 1,870 Nos", "Panel Size": "2279 × 1134 mm", "Cell Type": "Mono", "Inverters": "100 kW × 8 Nos", "Panel Connection for Inverter": "18 panels × 13 strings", "Table Arrangement": "34 panels × 1 table + 36 panels × 51 tables", "Main Transformer": "1000 kVA, 33 kV/400 V", "Auxiliary Transformer": "NA", "Plant factor": "18%", "Project Company": "Orik Corporation (Pvt) Ltd", "Ownership": "87%", "Land Area": "6.67 acres"},
        "HS1": {"Power Plant": "HRN 1 (Horana)", "AC Capacity": "2 MW", "Panels": "535 Wp × 4,480 Nos", "Panel Size": "2285 × 1134 mm", "Cell Type": "Mono", "Inverters": "200 kW × 10 Nos", "Panel Connection for Inverter": "28 panels × 28 strings", "Table Arrangement": "56 panels × 80 tables", "Main Transformer": "2200 kVA, 33 kV/800 V", "Auxiliary Transformer": "10 kVA, 230 V/800 V", "Plant factor": "21%", "Project Company": "Vidulsolar (Pvt) Ltd", "Ownership": "100%", "Land Area": "8.99 acres"},
        "HS2": {"Power Plant": "HRN 2 (Horana)", "AC Capacity": "2 MW", "Panels": "615 Wp × 4,060 Nos", "Panel Size": "2382 × 1134 mm", "Cell Type": "N-Type TOPCon", "Inverters": "300 kW × 7 Nos", "Panel Connection for Inverter": "29 panels × 20 strings", "Table Arrangement": "58 panels × 70 tables", "Main Transformer": "2250 kVA, 33 kV/800 V", "Auxiliary Transformer": "5 kVA, 230 V/800 V", "Plant factor": "20%", "Project Company": "Vidulsolar (Pvt) Ltd", "Ownership": "100%", "Land Area": "6.5 acres"},
        "MTR-I": {"Power Plant": "MTR (Matara)", "AC Capacity": "3 MW", "Panels": "615 Wp × 6,160 Nos", "Panel Size": "2382 × 1134 mm", "Cell Type": "N-Type TOPCon", "Inverters": "300 kW × 10 Nos", "Panel Connection for Inverter": "28 panels × 22 strings", "Table Arrangement": "56 panels × 110 tables", "Main Transformer": "3300 kVA, 33 kV/800 V", "Auxiliary Transformer": "5 kVA, 230 V/800 V", "Plant factor": "21.04%", "Project Company": "Vidul Matara Solar Power (Pvt) Ltd", "Ownership": "100%", "Land Area": "10.5 acres"},
        "MDP": {"Power Plant": "MDP (Madampe)", "AC Capacity": "6 MW", "Panels": "615 Wp × 12,320 Nos", "Panel Size": "2382 × 1134 mm", "Cell Type": "N-Type TOPCon", "Inverters": "300 kW × 20 Nos", "Panel Connection for Inverter": "28 panels × 22 strings", "Table Arrangement": "56 panels × 220 tables", "Main Transformer": "6000 kVA, 33 kV/800 V", "Auxiliary Transformer": "50 kVA, 230 V/800 V", "Plant factor": "21.00%", "Project Company": "Vidulsolar (Pvt) Ltd", "Ownership": "100%", "Land Area": "20 acres"},
    }
    spec = specs.get(plant.code) or specs.get("MTR-I" if plant.code == "MTR" else plant.code, {})
    st.markdown("## ⚙️ Technical Specifications")
    if spec:
        st.dataframe(pd.DataFrame([{"Specification": k, "Value": v} for k, v in spec.items()]),
                     use_container_width=True, hide_index=True)
    else:
        st.info("Technical specifications have not yet been provided for this site. Add them to the site specification configuration when available.")


def overview_page(db, user, plant):
    header(plant)
    overview_context(db, user, plant)
    st.markdown("### 🔎 Dashboard Filters")
    f1, f2, f3, f4 = st.columns([1.1, 1.1, 1.4, 1.3])
    with f1:
        start_date = st.date_input("From", date.today() - timedelta(days=30), key="dash_start")
    with f2:
        end_date = st.date_input("To", date.today(), key="dash_end")

    workers = db.query(User).filter(
        User.active == True,
        or_(User.plant_id == plant.id, User.role.in_(["admin", "engineer"]))
    ).order_by(User.full_name).all()
    worker_options = {"All": None, "Supervisor": "__role_supervisor__", "Engineer and Admin": "__role_management__"}
    with f3:
        worker_label = st.selectbox("All / Supervisor / Engineer and Admin", list(worker_options.keys()), key="dash_worker")
    with f4:
        category = st.selectbox("Category", DASHBOARD_CATEGORIES, key="dash_category")

    if start_date > end_date:
        st.error("The start date cannot be after the end date.")
        return

    worker_id = worker_options[worker_label]
    records = filtered_records(db, plant.id, start_date, end_date, None if isinstance(worker_id, str) else worker_id, category)
    if worker_id == "__role_supervisor__":
        role_ids = {u.id for u in workers if u.role == "supervisor"}
        records = [r for r in records if r.worker_id in role_ids]
    elif worker_id == "__role_management__":
        role_ids = {u.id for u in workers if u.role in ("engineer", "admin")}
        records = [r for r in records if r.worker_id in role_ids]
    metrics = dashboard_metrics(db, plant, records, start_date, end_date)
    repair_rows = db.query(RepairItem.asset_id, func.count(RepairItem.id)).filter(
        RepairItem.plant_id == plant.id, RepairItem.status != "Completed"
    ).group_by(RepairItem.asset_id).all()
    repair_counts = {a: int(c) for a, c in repair_rows}
    worker_map = {u.id: u.full_name for u in workers}

    st.caption(f"Live dashboard • {start_date:%d %b %Y} → {end_date:%d %b %Y} • {len(records)} filtered records • Updated {datetime.now():%d %b %Y • %H:%M}")

    cards = [
        ("Plant Capacity", f"{plant.capacity_mw:g} MW", "Installed capacity", ""),
        ("Total Inverters", str(plant.inverter_count), "Operational units", ""),
        ("Total Tables", str(plant.table_count), "Panel tables", ""),
        ("Panel Cleaning", f"{metrics['panel_pct']:.0f}%", f"{metrics['panel_done']} completed<br><span class='pending-inline'>{metrics['panel_total']-metrics['panel_done']} pending</span>", "good"),
        ("Grass Cutting", f"{metrics['grass_pct']:.0f}%", f"{metrics['grass_done']} completed<br><span class='pending-inline'>{metrics['grass_total']-metrics['grass_done']} pending</span>", "good"),
        ("Open Loss & Repair", str(metrics['open_repairs']), "Active repair items", "bad" if metrics['open_repairs'] else "good"),
    ]
    for col, (title, val, label, cls) in zip(st.columns(6), cards):
        col.markdown(f'<div class="dashboard-card"><h4>{title}</h4><div class="dashboard-value {cls}">{val}</div><div class="dashboard-label">{label}</div></div>', unsafe_allow_html=True)

    # Keep the real satellite image on Overview, with live table markers and
    # grass-cutting zone outlines. The image keeps its aspect ratio.
    st.markdown("### 🛰️ Solar Plant Map")
    satellite_table_map_page(db, user, plant, embedded=True)

    panel_col, grass_col = st.columns(2, gap="large")
    with panel_col:
        st.markdown('<div class="map-section-title">🧹 Panel Cleaning Status</div>', unsafe_allow_html=True)
        donut_chart(metrics["panel_pct"], "Completed", "overview_panel_donut")
        st.progress(metrics["panel_pct"] / 100, text=f"{metrics['panel_pct']:.0f}% completed")
        st.write(f"🟢 Completed **{metrics['panel_done']} / {metrics['panel_total']}**")
        st.write(f"🔴 Pending **{metrics['panel_total'] - metrics['panel_done']}**")
    with grass_col:
        st.markdown('<div class="map-section-title">🌿 Grass Cutting Status</div>', unsafe_allow_html=True)
        donut_chart(metrics["grass_pct"], "Completed", "overview_grass_donut")
        st.progress(metrics["grass_pct"] / 100, text=f"{metrics['grass_pct']:.0f}% completed")
        st.write(f"🟢 Completed **{metrics['grass_done']} / {metrics['grass_total']}**")
        st.write(f"🔴 Pending **{metrics['grass_total'] - metrics['grass_done']}**")
        st.write(f"🔴 Pending **{metrics['grass_total'] - metrics['grass_done']}**")
        st.markdown('<div class="map-section-title">⚠️ Loss & Repair</div>', unsafe_allow_html=True)
        if metrics["open_repairs"]:
            st.error(f"{metrics['open_repairs']} active repair item(s)")
        else:
            st.success("No open repair items")

    # Bottom panels mirror the reference dashboard and keep all cards equal height.
    st.markdown("### 📋 Current Status Details")
    panel_records = status_map(db, plant.id, "Panel Cleaning", end_date)
    grass_records = status_map(db, plant.id, "Grass Cutting", end_date)
    c_panel, c_grass, c_loss = st.columns([1.15, 1.0, 1.15], gap="medium")

    with c_panel:
        st.markdown('<div class="map-section-title">🧹 Panel Cleaning Details</div>', unsafe_allow_html=True)
        shown = asset_list(plant, "Table")[:15]
        items = []
        for a in shown:
            done = panel_records.get(a) and panel_records[a].status == "Completed"
            items.append(f'<div class="mini-item"><div class="name">{a}</div><div class="state {"good" if done else "bad"}">● {"Completed" if done else "Pending"}</div></div>')
        st.markdown('<div class="mini-grid">'+''.join(items)+'</div>', unsafe_allow_html=True)
        st.progress(metrics["panel_pct"] / 100, text=f"Cleaning progress • {metrics['panel_pct']:.0f}%")
        st.caption(f"Showing first {min(15, plant.table_count)} tables • {metrics['panel_done']} of {metrics['panel_total']} completed")

    with c_grass:
        st.markdown('<div class="map-section-title">🌿 Grass Cutting Details</div>', unsafe_allow_html=True)
        items = []
        for z in asset_list(plant, "Zone"):
            done = grass_records.get(z) and grass_records[z].status == "Completed"
            items.append(f'<div class="mini-item"><div class="name">{z}</div><div class="state {"good" if done else "bad"}">● {"Completed" if done else "Pending"}</div></div>')
        st.markdown('<div class="mini-grid">'+''.join(items)+'</div>', unsafe_allow_html=True)
        st.progress(metrics["grass_pct"] / 100, text=f"Grass cutting progress • {metrics['grass_pct']:.0f}%")
        st.caption(f"{metrics['grass_done']} of {metrics['grass_total']} zones completed")

    with c_loss:
        st.markdown('<div class="map-section-title">🔧 Loss & Repair (Inverter-wise)</div>', unsafe_allow_html=True)
        items = []
        for a in asset_list(plant, "Inverter"):
            count = repair_counts.get(a, 0)
            cls = "bad" if count else "good"
            text_state = f"{count} open" if count else "No issue"
            items.append(f'<div class="loss-tile"><b>{a}</b><br><span class="{cls}">● {text_state}</span></div>')
        st.markdown('<div class="loss-grid">'+''.join(items)+'</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="dashboard-card" style="height:auto;min-height:78px;margin-top:8px"><div class="dashboard-value {"bad" if metrics["open_repairs"] else "good"}">{metrics["open_repairs"]}</div><div class="dashboard-label">Total active repair items</div></div>', unsafe_allow_html=True)

    st.markdown("### 🕒 Recent Maintenance Activity")
    if records:
        for r in records[:8]:
            cls, label = ("good", "GREEN") if r.status == "Completed" else (("bad", "RED") if r.status == "Failed" else ("warn", "PENDING"))
            st.markdown(f'<div class="status-tile"><div style="display:flex;justify-content:space-between;gap:12px"><div><b>{r.category}</b> • {r.asset_id}<br><span class="small-muted">{r.task_date:%d %b %Y} • {worker_map.get(r.worker_id,"Unassigned")}</span></div><div class="{cls}">● {label}</div></div></div>', unsafe_allow_html=True)
    else:
        st.info("No maintenance records match the selected filters.")

    # Keep technical/site information below the operational dashboard, as requested.
    st.divider()
    render_plant_details_and_specs(db, plant)

# ============================================================
# Panel Cleaning
# ============================================================

def panel_cleaning_page(db, user, plant):
    header(plant)
    st.markdown("## 🧹 Panel Cleaning")

    task_date = st.date_input("Work date", value=date.today(), key="panel_date")
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

        with cols[i % 5]:
            st.markdown(f"**{asset}**  \n{'🟢 Completed' if done else '🔴 Pending'}")
            if st.button(
                "Mark Pending" if done else "Mark Completed",
                key=f"panel_{asset}_{task_date}",
                use_container_width=True,
            ):
                upsert_record(
                    db, plant.id, "Panel Cleaning", asset, task_date, user.id,
                    "Pending" if done else "Completed",
                    {"items": {"Panel/Table Cleaning": "Not Done" if done else "Done"}},
                )
                st.rerun()


# ============================================================
# Grass Cutting
# ============================================================

def grass_cutting_page(db, user, plant):
    header(plant)
    st.markdown("## 🌿 Grass Cutting")

    task_date = st.date_input("Work date", value=date.today(), key="grass_date")
    grass_records = status_map(db, plant.id, "Grass Cutting", task_date)
    completed = sum(1 for r in grass_records.values() if r.status == "Completed")
    pct = round(completed / plant.zone_count * 100, 1) if plant.zone_count else 0

    st.progress(pct / 100, text=f"Grass cutting progress: {pct:.1f}%")

    cols = st.columns(5)
    for i, zone in enumerate(asset_list(plant, "Zone")):
        r = grass_records.get(zone)
        done = bool(r and r.status == "Completed")

        with cols[i % 5]:
            st.markdown(f"**{zone}**  \n{'🟢 Completed' if done else '🔴 Pending'}")
            if st.button(
                "Mark Pending" if done else "Mark Completed",
                key=f"grass_{zone}_{task_date}",
                use_container_width=True,
            ):
                upsert_record(
                    db, plant.id, "Grass Cutting", zone, task_date, user.id,
                    "Pending" if done else "Completed",
                    {"items": {"Grass Cutting": "Not Done" if done else "Done"}},
                )
                st.rerun()


# ============================================================
# Maintenance inspections
# ============================================================

def inspection_page(db, user, plant):
    header(plant)
    st.markdown("## 🔧 Maintenance Inspections")
    st.caption(
        "Daily Inspection → Inverter Inspection → Panel Inspection → "
        "DC Cable Inspection → MDB Inspection → Switch Yard Inspection → AC Inspection"
    )

    category = st.selectbox("Select maintenance process", list(PROCESS_CATEGORIES.keys()))
    cfg = PROCESS_CATEGORIES[category]

    c1, c2, c3 = st.columns(3)
    c1.markdown(
        f'<div class="dashboard-card"><h4>Frequency</h4>'
        f'<div style="font-size:20px;font-weight:800;color:#14213d;">{cfg["frequency"]}</div></div>',
        unsafe_allow_html=True,
    )
    c2.markdown(
        f'<div class="dashboard-card"><h4>Asset Type</h4>'
        f'<div style="font-size:20px;font-weight:800;color:#14213d;">{cfg["asset_type"]}</div></div>',
        unsafe_allow_html=True,
    )
    c3.markdown(
        f'<div class="dashboard-card"><h4>Checklist Items</h4>'
        f'<div style="font-size:20px;font-weight:800;color:#14213d;">{len(cfg["items"])}</div></div>',
        unsafe_allow_html=True,
    )

    task_date = st.date_input("Inspection date", value=date.today())
    assets = asset_list(plant, cfg["asset_type"])
    asset = st.selectbox(f"Select {cfg['asset_type']}", assets)

    old = record_status(db, plant.id, category, asset, task_date)
    old_details = parse_details(old) if old else {}
    old_items = old_details.get("items", {})

    if old and getattr(old, "approval_status", None) in ("Approved", "Declined"):
        approval_text = "APPROVED" if old.approval_status == "Approved" else "DECLINED"
        approval_cls = "good" if old.approval_status == "Approved" else "bad"
        st.markdown(f'<div class="dashboard-card"><b>Engineer / Admin Review: <span class="{approval_cls}">{approval_text}</span></b><br><span class="small-muted">Signature: {getattr(old, "approval_signature", "") or "—"} • Reviewed: {getattr(old, "approved_at", None) or "—"}</span></div>', unsafe_allow_html=True)

    st.markdown(f"### {asset} — {category}")

    # Daily Inspection is intentionally split into the two groups requested:
    # 1-3 = inverter checks, 4-6 = site safety/security checks.
    if category == "Daily Inspection":
        groups = [
            ("Inverter Health Checks", cfg["items"][:3], 0),
            ("Site Safety & Security Checks", cfg["items"][3:6], 3),
        ]
    else:
        groups = [("Inspection Checklist", cfg["items"], 0)]

    values = {}

    for group_title, items, offset in groups:
        st.markdown(f'<div class="group-title">{group_title}</div>', unsafe_allow_html=True)
        st.markdown('<div class="group-card">', unsafe_allow_html=True)

        for local_index, item in enumerate(items):
            i = offset + local_index
            current = old_items.get(item, "Pending")
            # Support records created by the previous version.
            if current == "OK / Green":
                current = "GREEN"
            elif current == "Not OK / Red":
                current = "RED"
            options = ["Pending", "GREEN", "RED"]
            default_index = options.index(current) if current in options else 0

            cols = st.columns([4.0, 2.2, 3.8])
            cols[0].markdown(
                f'<div class="check-item"><b>{i + 1}. {item}</b></div>',
                unsafe_allow_html=True,
            )
            values[item] = cols[1].selectbox(
                "Status",
                options,
                index=default_index,
                format_func=lambda value: {"Pending": "PENDING", "GREEN": "Completed", "RED": "Pending"}.get(value, value),
                key=f"{category}_{asset}_{task_date}_{i}",
                label_visibility="collapsed",
            )

            if values[item] == "RED":
                values[f"{item}__reason"] = cols[2].text_input(
                    "Reason / issue",
                    value=old_items.get(f"{item}__reason", ""),
                    key=f"reason_{category}_{asset}_{task_date}_{i}",
                    placeholder="Reason / issue found",
                )
            else:
                values[f"{item}__reason"] = ""

        st.markdown('</div>', unsafe_allow_html=True)

    remarks = st.text_area(
        "Overall remarks",
        value=old.remarks if old else "",
        placeholder="Additional maintenance remarks...",
    )

    if st.button("💾 Save Inspection", type="primary", use_container_width=True):
        statuses = [v for k, v in values.items() if not k.endswith("__reason")]

        if all(v == "GREEN" for v in statuses):
            overall = "Completed"
        elif any(v == "RED" for v in statuses):
            overall = "Failed"
        else:
            overall = "Pending"

        upsert_record(
            db,
            plant.id,
            category,
            asset,
            task_date,
            user.id,
            overall,
            {"items": values},
            remarks,
        )

        for item in cfg["items"]:
            if values[item] == "RED":
                reason = values.get(f"{item}__reason", "").strip()
                ensure_repair_from_failed_item(
                    db, plant.id, category, asset, item, reason, user.id
                )

        st.success(f"{category} for {asset} saved as {overall}.")
        if overall == "Failed":
            st.warning("🔴 Staff/supervisors/admins assigned to this plant have been notified.")
        st.rerun()


# ============================================================
# Loss & Repair
# ============================================================

def loss_repair_page(db, user, plant):
    header(plant)
    st.markdown("## ⚠️ Loss & Repair / Maintenance")

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
    st.markdown("## 📋 Activity Log")

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

def users_page(db, user):
    st.title("👥 User Management")
    st.caption("Assign plants, manage roles, and manage Supervisor, Engineer, Admin and Staff access.")

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
                # Account approval is no longer used; keep legacy field enabled for existing schemas.
                u.approved = True
                u.active = active
                db.commit()
                st.success("User updated.")
                st.rerun()


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
            capacity = c1.number_input("Capacity MW", 0.0, 1000.0, float(p.capacity_mw), 0.1, key=f"pcap_{p.id}")

            inv = c2.number_input("Inverters", 1, 1000, int(p.inverter_count), key=f"pinv_{p.id}")
            tables = c2.number_input("Tables", 1, 10000, int(p.table_count), key=f"ptab_{p.id}")
            zones = c2.number_input("Zones", 1, 1000, int(p.zone_count), key=f"pzone_{p.id}")
            active = c2.checkbox("Active", p.active, key=f"pactive_{p.id}")

            if st.button("Save Plant", key=f"save_plant_{p.id}"):
                p.name = name.strip()
                p.code = code.strip().upper()
                p.capacity_mw = capacity
                p.inverter_count = inv
                p.table_count = tables
                p.zone_count = zones
                p.active = active
                db.commit()
                st.success("Plant updated.")
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
        elif db.query(Plant).filter(Plant.code == new_code.strip().upper()).first():
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

def reminders_page(db):
    st.title("⏰ Automatic Maintenance Reminders")

    plants = db.query(Plant).filter(Plant.active == True).order_by(Plant.name).all()
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
                "Remarks": r.remarks,
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
    return pd.DataFrame([{"Date":r.task_date,"Category":r.category,"Asset":r.asset_id,"Worker":users.get(r.worker_id,"Unassigned"),"Status":r.status,"Approval":getattr(r,"approval_status","Pending") or "Pending","Remarks":r.remarks or "","Completed At":r.completed_at,"Approved At":getattr(r,"approved_at",None),"Approved By":users.get(getattr(r,"approved_by",None),"")} for r in rows])

def _excel_bytes(df,sheet_name="Maintenance Report"):
    output=io.BytesIO()
    with pd.ExcelWriter(output,engine="openpyxl") as writer: df.to_excel(writer,index=False,sheet_name=sheet_name[:31])
    return output.getvalue()


def _pdf_report_bytes(df, plant, start, end, panel_pct, grass_pct):
    """Create a management-friendly PDF report with KPIs, charts and activity details."""
    output = io.BytesIO()
    doc = SimpleDocTemplate(
        output,
        pagesize=landscape(A4),
        rightMargin=12 * mm,
        leftMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
        title=f"{plant.name} - Solar Maintenance Report",
        author="Solar Plant Maintenance System",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ReportTitle", parent=styles["Title"], alignment=TA_CENTER,
        fontSize=20, leading=24, spaceAfter=6
    )
    subtitle_style = ParagraphStyle(
        "ReportSubtitle", parent=styles["Normal"], alignment=TA_CENTER,
        fontSize=9, textColor=colors.HexColor("#5b6573"), spaceAfter=12
    )
    h2 = ParagraphStyle(
        "ReportH2", parent=styles["Heading2"], fontSize=13, leading=16,
        textColor=colors.HexColor("#123b63"), spaceBefore=8, spaceAfter=6
    )
    small = ParagraphStyle(
        "ReportSmall", parent=styles["Normal"], fontSize=8, leading=10
    )

    total = len(df)
    green = int((df["Status"] == "Completed").sum()) if total else 0
    red = int((df["Status"] == "Failed").sum()) if total else 0
    pending = int((df["Status"] == "Pending").sum()) if total else 0
    approved = int((df["Approval"] == "Approved").sum()) if total else 0

    story = [
        Paragraph("Solar Plant Maintenance Management Report", title_style),
        Paragraph(
            f"<b>{plant.name}</b> &nbsp; | &nbsp; Period: {start:%d %b %Y} - {end:%d %b %Y} &nbsp; | &nbsp; Generated: {datetime.now():%d %b %Y %H:%M}",
            subtitle_style,
        ),
    ]

    # KPI cards represented as a compact table in the PDF.
    kpi_data = [[
        Paragraph(f"<b>{total}</b><br/><font size='8'>Activities</font>", small),
        Paragraph(f"<b>{green}</b><br/><font size='8'>GREEN</font>", small),
        Paragraph(f"<b>{red}</b><br/><font size='8'>RED</font>", small),
        Paragraph(f"<b>{pending}</b><br/><font size='8'>Pending</font>", small),
        Paragraph(f"<b>{approved}</b><br/><font size='8'>Approved</font>", small),
        Paragraph(f"<b>{panel_pct:.0f}%</b><br/><font size='8'>Panel Cleaning</font>", small),
        Paragraph(f"<b>{grass_pct:.0f}%</b><br/><font size='8'>Grass Cutting</font>", small),
    ]]
    kpi_table = Table(kpi_data, colWidths=[35*mm]*7, rowHeights=[18*mm])
    kpi_table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), colors.HexColor("#f4f7fb")),
        ("BOX", (0,0), (-1,-1), 0.6, colors.HexColor("#d7e0ea")),
        ("INNERGRID", (0,0), (-1,-1), 0.4, colors.HexColor("#e2e8f0")),
        ("ALIGN", (0,0), (-1,-1), "CENTER"),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
    ]))
    story += [kpi_table, Spacer(1, 7*mm)]

    # Simple visual summary charts using tables (no image dependencies).
    status_counts = [("GREEN", green), ("RED", red), ("Pending", pending), ("Approved", approved)]
    max_status = max([v for _, v in status_counts] or [1])
    status_rows = []
    for label, value in status_counts:
        width = 100 if max_status == 0 else max(2, int(value / max_status * 100))
        bar = Table([[""]], colWidths=[max(4, width)*0.75*mm], rowHeights=[4*mm])
        bar.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,-1), colors.HexColor("#18a66a" if label == "GREEN" else "#e55353" if label == "RED" else "#94a3b8" if label == "Pending" else "#3b82f6")),
            ("BOX", (0,0), (-1,-1), 0, colors.white),
        ]))
        status_rows.append([Paragraph(label, small), bar, Paragraph(str(value), small)])
    status_table = Table(status_rows, colWidths=[25*mm, 78*mm, 18*mm])
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
    summary_table = Table([[status_table, cat_table]], colWidths=[120*mm, 100*mm])
    summary_table.setStyle(TableStyle([("VALIGN", (0,0), (-1,-1), "TOP")]))
    story += [Paragraph("Graphical Status Summary", h2), summary_table, Spacer(1, 6*mm)]

    story += [Paragraph("Work Progress", h2)]
    progress_data = [
        [Paragraph("Panel cleaning", small), Paragraph(f"{panel_pct:.0f}%", small)],
        [Paragraph("Grass cutting", small), Paragraph(f"{grass_pct:.0f}%", small)],
    ]
    progress_table = Table(progress_data, colWidths=[45*mm, 25*mm])
    progress_table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), colors.HexColor("#f4f7fb")),
        ("GRID", (0,0), (-1,-1), 0.35, colors.HexColor("#d7e0ea")),
        ("ALIGN", (1,0), (1,-1), "CENTER"),
    ]))
    story += [progress_table, Spacer(1, 6*mm)]

    story += [Paragraph("Current Activities", h2)]
    if total:
        columns = ["Date", "Category", "Asset", "Worker", "Status", "Approval", "Remarks"]
        activity_rows = [[Paragraph(f"<b>{c}</b>", small) for c in columns]]
        for _, row in df.iterrows():
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
        activity_table = Table(activity_rows, colWidths=[22*mm, 32*mm, 23*mm, 28*mm, 22*mm, 25*mm, 78*mm], repeatRows=1)
        activity_table.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#123b63")),
            ("TEXTCOLOR", (0,0), (-1,0), colors.white),
            ("GRID", (0,0), (-1,-1), 0.3, colors.HexColor("#d7e0ea")),
            ("VALIGN", (0,0), (-1,-1), "TOP"),
            ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor("#f7fafc")]),
            ("LEFTPADDING", (0,0), (-1,-1), 3),
            ("RIGHTPADDING", (0,0), (-1,-1), 3),
            ("TOPPADDING", (0,0), (-1,-1), 3),
            ("BOTTOMPADDING", (0,0), (-1,-1), 3),
        ]))
        story.append(activity_table)
    else:
        story.append(Paragraph("No activities found for the selected period.", small))

    story += [Spacer(1, 8*mm), Paragraph("Prepared for operational management review. This report contains maintenance activity information only; database and software configuration details are intentionally excluded.", small)]

    doc.build(story)
    return output.getvalue()

def reports_page(db,user,plant):
    header(plant); st.title("📊 Management Reports"); st.caption("Management reports are available to Engineers and Administrators. No technical database or software configuration is shown.")
    c1,c2=st.columns(2)
    with c1: start=st.date_input("From",date.today()-timedelta(days=30),key="report_start")
    with c2: end=st.date_input("To",date.today(),key="report_end")
    if start>end: st.error("The start date cannot be after the end date."); return
    df=_records_dataframe(db,plant.id,start,end); total=len(df); completed=int((df["Status"]=="Completed").sum()) if total else 0; failed=int((df["Status"]=="Failed").sum()) if total else 0; pending=int((df["Status"]=="Pending").sum()) if total else 0; approved=int((df["Approval"]=="Approved").sum()) if total else 0
    c1,c2,c3,c4,c5=st.columns(5); c1.metric("Activities",total); c2.metric("GREEN",completed); c3.metric("RED",failed); c4.metric("Pending",pending); c5.metric("Approved",approved)
    import plotly.express as px
    left,right=st.columns(2)
    if total:
        with left:
            d=df["Status"].value_counts().rename_axis("Status").reset_index(name="Count"); fig=px.bar(d,x="Status",y="Count",title="Current maintenance status",text="Count"); fig.update_layout(margin=dict(l=10,r=10,t=50,b=10),height=330); st.plotly_chart(fig,use_container_width=True,config={"displayModeBar":False})
        with right:
            d=df["Category"].value_counts().rename_axis("Category").reset_index(name="Count"); fig=px.bar(d,x="Count",y="Category",orientation="h",title="Activities by category",text="Count"); fig.update_layout(margin=dict(l=10,r=10,t=50,b=10),height=330); st.plotly_chart(fig,use_container_width=True,config={"displayModeBar":False})
    st.markdown("### 🧹🌿 Current work progress"); pc,_,_=panel_cleaning_percent(db,plant.id,end); gc,_,_=grass_cutting_percent(db,plant.id,end); a,b=st.columns(2); a.metric("Panel cleaning",f"{pc:.0f}%"); b.metric("Grass cutting",f"{gc:.0f}%"); st.progress(pc/100,text=f"Panel cleaning • {pc:.0f}%"); st.progress(gc/100,text=f"Grass cutting • {gc:.0f}%")
    st.markdown("### 📋 Current activity report")
    if total:
        st.dataframe(df,use_container_width=True,hide_index=True)
        b1,b2=st.columns(2)
        with b1:
            st.download_button("⬇️ Export current activities to Excel",_excel_bytes(df),"solar_maintenance_current_activities.xlsx","application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",use_container_width=True)
        with b2:
            pdf_bytes=_pdf_report_bytes(df,plant,start,end,pc,gc)
            st.download_button("📄 Export management report to PDF",pdf_bytes,"solar_maintenance_management_report.pdf","application/pdf",use_container_width=True)
    else:
        st.info("No activities found for the selected period.")

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
    st.markdown("### 📊 Management reports"); st.write("Engineers and Administrators can use **Reports** for graphical completion percentages, inspection status, loss & repair summaries, Excel export and PDF management reports.")


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
    elif page == "👷 Operating Staff & Site Contacts":
        header(plant)
        operations_staff_section(db, user, plant)
    elif page == "🧹 Panel Cleaning":
        panel_cleaning_page(db, user, plant)
    elif page == "🌿 Grass Cutting":
        grass_cutting_page(db, user, plant)
    elif page == "🔧 Maintenance Inspections":
        inspection_page(db, user, plant)
    elif page == "⚠️ Loss & Repair":
        loss_repair_page(db, user, plant)
    elif page == "📋 Activity Log":
        activity_page(db, user, plant)
    elif page == "👥 Users" and user.role == "admin":
        users_page(db, user)
    elif page == "🏭 Plants" and user.role == "admin":
        plants_page(db)
    elif page == "📊 Reports" and user.role in ("admin", "engineer"):
        reports_page(db, user, plant)
    elif page == "✅ Approvals & Sign-off" and user.role in ("admin", "engineer"):
        approvals_page(db, user, plant)
    elif page == "⏰ Reminders" and user.role == "admin":
        reminders_page(db)
    elif page == "⚙️ Admin" and user.role == "admin":
        admin_page(db)

    db.close()


if __name__ == "__main__":
    main()
