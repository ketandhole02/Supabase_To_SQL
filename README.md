# Supabase → SQL Server Migration Tool

A Streamlit app that migrates tables from Supabase into a local SQL Server (SSMS).

## Folder structure

```
supabase_migration_app/
├── Home.py                  ← Main entry point (Streamlit starts here)
├── migration_page.py        ← Core migration logic + UI (reusable module)
├── requirements.txt         ← Python dependencies
├── packages.txt             ← System dependencies (ODBC — for Streamlit Cloud)
├── README.md
├── pages/
│   └── 1_Migration.py       ← Migration page (shows in sidebar)
└── .streamlit/
    └── config.toml          ← Theme + server settings
```

## How to deploy on Streamlit Cloud

1. Push this folder to a **GitHub repository** (public or private)
2. Go to [share.streamlit.io](https://share.streamlit.io) → **New app**
3. Select your repo, branch, and set **Main file path** to `Home.py`
4. Click **Deploy** — done!

## How to run locally

```bash
pip install -r requirements.txt
streamlit run Home.py
```

Or on Windows:
```powershell
& "C:\Program Files\Python39\python.exe" -m pip install -r requirements.txt
& "C:\Program Files\Python39\python.exe" -m streamlit run Home.py
```

## How to add this migration page to an existing Streamlit app

Just copy `migration_page.py` into your existing project root, then in any page file:

```python
from migration_page import render
render()
```

## Important note about pyodbc on Streamlit Cloud

Streamlit Cloud runs on Linux. `pyodbc` needs the ODBC driver installed on the
server — `packages.txt` handles that automatically. However, the client's
**SQL Server must be accessible from the internet** (public IP or VPN) for the
cloud-deployed app to reach it. For purely local SQL Servers behind a firewall,
run the app locally instead.
