import os
from pathlib import Path
import tomllib
from sqlalchemy import create_engine, text

secrets_path = Path(".streamlit/secrets.toml")
if not secrets_path.exists():
    raise SystemExit("Create .streamlit/secrets.toml first.")

with secrets_path.open("rb") as f:
    secrets = tomllib.load(f)

url = secrets.get("DATABASE_URL")
if not url:
    raise SystemExit("DATABASE_URL is missing in .streamlit/secrets.toml")

if url.startswith("postgres://"):
    url = "postgresql+psycopg2://" + url[len("postgres://"):]
elif url.startswith("postgresql://"):
    url = "postgresql+psycopg2://" + url[len("postgresql://"):]

engine = create_engine(url, pool_pre_ping=True)

with engine.connect() as conn:
    result = conn.execute(text("SELECT version()"))
    print("Neon connection successful.")
    print(result.scalar())
