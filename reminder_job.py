
import os
import smtplib
from datetime import date, datetime
from email.message import EmailMessage

from sqlalchemy import create_engine, Column, Integer, String, Boolean, Date, DateTime, Text, ForeignKey, Float, or_
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.environ["DATABASE_URL"]
SMTP_HOST = os.environ["SMTP_HOST"]
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.environ["SMTP_USERNAME"]
SMTP_PASSWORD = os.environ["SMTP_PASSWORD"]
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USERNAME)
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").lower() == "true"

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

class Plant(Base):
    __tablename__ = "plants"
    id = Column(Integer, primary_key=True)
    name = Column(String(200))
    active = Column(Boolean)

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    full_name = Column(String(200))
    email = Column(String(200))
    role = Column(String(30))
    plant_id = Column(Integer, ForeignKey("plants.id"))
    active = Column(Boolean)
    approved = Column(Boolean)

class MaintenanceReminder(Base):
    __tablename__ = "maintenance_reminders"
    id = Column(Integer, primary_key=True)
    plant_id = Column(Integer, ForeignKey("plants.id"))
    category = Column(String(100))
    frequency = Column(String(50))
    due_date = Column(Date)
    status = Column(String(30))
    sent_at = Column(DateTime)

class Notification(Base):
    __tablename__ = "notifications"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    plant_id = Column(Integer)
    notification_type = Column(String(50))
    title = Column(String(250))
    message = Column(Text)
    category = Column(String(100))
    asset_id = Column(String(100))
    severity = Column(String(30))
    is_read = Column(Boolean)
    email_sent = Column(Boolean)
    created_at = Column(DateTime)

Base.metadata.create_all(engine)

MONTHLY_CATEGORIES = [
    ("Inverter Inspection", "Monthly"),
    ("Panel Inspection", "Monthly"),
    ("DC Cable Inspection", "Monthly"),
    ("MDB Inspection", "Monthly"),
    ("Switch Yard Inspection", "Monthly"),
    ("AC Inspection", "Every 4 Months"),
]

def send_email(to_email, subject, body):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM
    msg["To"] = to_email
    msg.set_content(body)
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
        if SMTP_USE_TLS:
            server.starttls()
        server.login(SMTP_USERNAME, SMTP_PASSWORD)
        server.send_message(msg)

def main():
    db = SessionLocal()
    today = date.today()
    sent = 0

    plants = db.query(Plant).filter(Plant.active == True).all()

    for plant in plants:
        recipients = db.query(User).filter(
            User.active == True,
            User.approved == True,
            or_(User.plant_id == plant.id, User.role == "admin"),
        ).all()

        for category, frequency in MONTHLY_CATEGORIES:
            # Create a reminder for the current month if none exists.
            existing = db.query(MaintenanceReminder).filter(
                MaintenanceReminder.plant_id == plant.id,
                MaintenanceReminder.category == category,
                MaintenanceReminder.due_date <= today,
                MaintenanceReminder.status != "Completed",
            ).first()

            if not existing:
                # For the automated monthly worker, the due date is today.
                existing = MaintenanceReminder(
                    plant_id=plant.id,
                    category=category,
                    frequency=frequency,
                    due_date=today,
                    status="Pending",
                )
                db.add(existing)
                db.commit()

            if existing.sent_at:
                continue

            title = f"Monthly maintenance reminder: {category}"
            message = (
                f"Plant: {plant.name}\n"
                f"Maintenance: {category}\n"
                f"Frequency: {frequency}\n"
                f"Due date: {existing.due_date}\n\n"
                "Please complete the maintenance checklist in the Solar Maintenance System."
            )

            for recipient in recipients:
                try:
                    send_email(recipient.email, f"[Solar Maintenance] {title}", message)
                    email_sent = True
                except Exception as exc:
                    print(f"Email failed for {recipient.email}: {exc}")
                    email_sent = False

                db.add(
                    Notification(
                        user_id=recipient.id,
                        plant_id=plant.id,
                        notification_type="MONTHLY_REMINDER",
                        title=title,
                        message=message,
                        category=category,
                        asset_id=None,
                        severity="Medium",
                        is_read=False,
                        email_sent=email_sent,
                        created_at=datetime.utcnow(),
                    )
                )

            existing.sent_at = datetime.utcnow()
            db.commit()
            sent += 1

    db.close()
    print(f"Monthly reminder job complete. Sent {sent} reminder batches.")

if __name__ == "__main__":
    main()
