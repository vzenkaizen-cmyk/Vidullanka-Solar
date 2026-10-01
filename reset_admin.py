from pathlib import Path
import tomllib, hashlib, secrets
from sqlalchemy import create_engine, text

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 180_000)
    return f"pbkdf2_sha256$180000${salt.hex()}${digest.hex()}"

path=Path('.streamlit/secrets.toml')
if not path.exists(): raise SystemExit('ERROR: .streamlit/secrets.toml not found.')
with path.open('rb') as f: cfg=tomllib.load(f)
url=cfg.get('DATABASE_URL','').strip()
email=cfg.get('ADMIN_EMAIL','samodas@vidullanka.com').strip().lower()
pw=cfg.get('ADMIN_PASSWORD','').strip()
if not url: raise SystemExit('ERROR: DATABASE_URL is missing.')
if not pw: raise SystemExit('ERROR: ADMIN_PASSWORD is missing.')
if url.startswith('postgres://'): url='postgresql+psycopg2://'+url[11:]
elif url.startswith('postgresql://'): url='postgresql+psycopg2://'+url[13:]
engine=create_engine(url,pool_pre_ping=True)
with engine.begin() as c:
    row=c.execute(text('SELECT id FROM users WHERE lower(email)=:email'),{'email':email}).first()
    if row:
        c.execute(text('''UPDATE users SET full_name=:name,password_hash=:ph,role='admin',approved=TRUE,active=TRUE WHERE id=:id'''),{'id':row[0],'name':'System Administrator','ph':hash_password(pw)})
        action='Existing account promoted/reset.'
    else:
        c.execute(text('''INSERT INTO users(full_name,email,password_hash,role,approved,active) VALUES(:name,:email,:ph,'admin',TRUE,TRUE)'''),{'name':'System Administrator','email':email,'ph':hash_password(pw)})
        action='New administrator account created.'
print('ADMIN READY')
print('Email:',email)
print('Role: admin | Approved: TRUE | Active: TRUE')
print(action)
