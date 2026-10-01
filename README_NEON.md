# Solar Maintenance System - Neon PostgreSQL Edition

## 1. Create a Neon database

Create a PostgreSQL project/database in Neon.

In Neon Console:
1. Open the project.
2. Click **Connect**.
3. Select the database/branch.
4. Copy the PostgreSQL connection string.
5. Keep `sslmode=require`.

Example format:

```toml
DATABASE_URL = "postgresql://USER:PASSWORD@HOST/DBNAME?sslmode=require"
```

## 2. Local setup

Open PowerShell in this project folder:

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Create:

```text
.streamlit/secrets.toml
```

Copy `.streamlit/secrets.toml.example` and replace the placeholders with your real Neon credentials.

Then run:

```powershell
streamlit run app.py
```

The application uses SQLAlchemy + psycopg2 and automatically creates the required tables in Neon on first startup.

## 3. Initial administrator

The first run creates an admin using:

```toml
ADMIN_EMAIL = "admin@yourcompany.com"
ADMIN_PASSWORD = "YOUR_STRONG_PASSWORD"
```

Change these values before production.

## 4. Email alerts

For email alerts:

```toml
ENABLE_EMAIL = "true"
SMTP_HOST = "smtp.office365.com"
SMTP_PORT = "587"
SMTP_USERNAME = "maintenance@yourcompany.com"
SMTP_PASSWORD = "..."
SMTP_FROM = "maintenance@yourcompany.com"
SMTP_USE_TLS = "true"
```

If email is not configured, RED alerts still appear in the application's notification center and are stored in Neon.

## 5. Power BI

Run `powerbi_views.sql` against the same Neon database.

Recommended Power BI connection:

Power BI Desktop -> Get Data -> PostgreSQL database

Use the Neon host/database/user from the Connect dialog.

Load:

- `vw_powerbi_maintenance`
- `vw_powerbi_repairs`
- `vw_powerbi_notifications`
- `vw_powerbi_reminders`
- `vw_powerbi_dashboard_summary`

Recommended slicers:
- Date
- Plant
- Worker
- Category
- Status
- Asset

## 6. Monthly reminders

The included GitHub Action runs on the first day of every month.

Add these GitHub repository secrets:

- DATABASE_URL
- SMTP_HOST
- SMTP_PORT
- SMTP_USERNAME
- SMTP_PASSWORD
- SMTP_FROM
- SMTP_USE_TLS

The Streamlit application also sends due reminders when an authorized user opens a plant.

## 7. Important

Do not put `secrets.toml` in GitHub.

Use `.streamlit/secrets.toml` locally and Streamlit Cloud Secrets in deployment.
