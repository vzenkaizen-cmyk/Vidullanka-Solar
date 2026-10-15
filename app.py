
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
    page_title="Solar Plant Maintenance System",
    page_icon="☀️",
    layout="wide",
    initial_sidebar_state="expanded",
)

APP_TITLE = "Solar Power Plant Maintenance System"
DEFAULT_DB = "sqlite:///solar_maintenance.db"

# Management target times used on the compact Overview cards.
# These are deliberately kept in one place so management can change them later.
PANEL_CLEANING_TARGET_DAYS = 7
GRASS_CUTTING_TARGET_DAYS = 30
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
