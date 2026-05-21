"""
migration_page.py
-----------------
Standalone Streamlit page for Supabase → SQL Server migration.
Import and call render() from your main app:

    from migration_page import render
    render()

No DSN required — connection string is built purely from
what the client enters in the UI. Works on Streamlit Cloud.

Dependencies:
    pip install streamlit psycopg2-binary pyodbc python-dotenv
"""

import streamlit as st
import psycopg2
import pyodbc
from datetime import datetime

# ─────────────────────────────────────────────
# Migration constants
# ─────────────────────────────────────────────
PG_SCHEMA      = "aide_datamart"
TABLE_PATTERN  = "migr_sql_to_ws_%"
SOURCE_PREFIX  = "migr_sql_to_"
EXCLUDE_COLS   = {"system_id"}


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def source_to_target(name: str) -> str:
    """migr_sql_to_ws_obj_object → ws_obj_object"""
    if name.startswith(SOURCE_PREFIX):
        return name[len(SOURCE_PREFIX):]
    return name


def build_pg_connstr(cfg: dict) -> dict:
    """Returns kwargs dict for psycopg2.connect()"""
    return {
        "host":     cfg["host"],
        "port":     int(cfg["port"]),
        "dbname":   cfg["dbname"],
        "user":     cfg["user"],
        "password": cfg["password"],
        "sslmode":  "require",
        "connect_timeout": 10,
    }


def build_sql_connstr(cfg: dict) -> str:
    """
    Builds a pyodbc connection string purely from UI inputs.
    No DSN needed — works on any machine including Streamlit Cloud.

    Supports:
      - SQL Server Authentication  (UID + PWD)
      - Windows Authentication     (Trusted_Connection=yes)
    """
    driver  = cfg.get("driver", "ODBC Driver 17 for SQL Server")
    server  = cfg["server"].strip()
    database = cfg["database"].strip()

    base = f"DRIVER={{{driver}}};SERVER={server};DATABASE={database};"

    if cfg["auth"] == "Windows Authentication":
        return base + "Trusted_Connection=yes;"
    else:
        uid = cfg["uid"].strip()
        pwd = cfg["pwd"]
        return base + f"UID={uid};PWD={pwd};"


# ─────────────────────────────────────────────
# Connection verification
# ─────────────────────────────────────────────

def verify_pg(cfg: dict) -> tuple:
    """Returns (True, table_count) or (False, error_message)"""
    try:
        conn = psycopg2.connect(**build_pg_connstr(cfg))
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema=%s AND table_name LIKE %s;",
                (PG_SCHEMA, TABLE_PATTERN)
            )
            count = cur.fetchone()[0]
        conn.close()
        return True, count
    except Exception as e:
        return False, _friendly_pg_error(str(e))


def verify_sql(cfg: dict) -> tuple:
    """Returns (True, None) or (False, error_message)"""
    try:
        conn = pyodbc.connect(build_sql_connstr(cfg), timeout=10)
        conn.close()
        return True, None
    except Exception as e:
        return False, _friendly_sql_error(str(e), cfg)


def _friendly_pg_error(err: str) -> str:
    if "password authentication failed" in err:
        return "❌ Wrong Supabase password. Please check your credentials."
    if "could not connect" in err or "Connection refused" in err:
        return "❌ Cannot reach Supabase host. Check the host and port."
    if "SSL" in err:
        return "❌ SSL error. Make sure SSL mode is set to 'require' for Supabase."
    return f"❌ {err}"


def _friendly_sql_error(err: str, cfg: dict) -> str:
    if "Data source name not found" in err or "IM002" in err:
        return "❌ ODBC Driver not found on this machine. Try selecting a different driver."
    if "Login failed" in err:
        return f"❌ Login failed for user '{cfg.get('uid', '')}'. Check username and password."
    if "Cannot open database" in err:
        return f"❌ Database '{cfg.get('database', '')}' not found. Check the database name."
    if "server was not found" in err or "Could not open" in err:
        return f"❌ Server '{cfg.get('server', '')}' not reachable. Check server name and network."
    if "TCP Provider" in err or "10061" in err:
        return "❌ Connection refused. Make sure SQL Server is running and port 1433 is open."
    return f"❌ {err}"


# ─────────────────────────────────────────────
# Core migration logic
# ─────────────────────────────────────────────

def get_pg_tables(pg_conn) -> list:
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema=%s AND table_name LIKE %s ORDER BY table_name;",
            (PG_SCHEMA, TABLE_PATTERN)
        )
        return [r[0] for r in cur.fetchall()]


def get_pg_columns(pg_conn, table: str) -> list:
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position;",
            (PG_SCHEMA, table)
        )
        return [r[0] for r in cur.fetchall() if r[0].lower() not in EXCLUDE_COLS]


def get_sql_columns(sql_conn, table: str) -> set:
    cur = sql_conn.cursor()
    cur.execute(
        "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA='dbo' AND TABLE_NAME=?", table
    )
    return {r[0].lower() for r in cur.fetchall()}


def get_identity_columns(sql_conn, table: str) -> set:
    cur = sql_conn.cursor()
    cur.execute("""
        SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA='dbo' AND TABLE_NAME=?
          AND COLUMNPROPERTY(
                OBJECT_ID(TABLE_SCHEMA+'.'+TABLE_NAME),
                COLUMN_NAME, 'IsIdentity') = 1
    """, table)
    return {r[0].lower() for r in cur.fetchall()}


def pg_to_sql_type(pg_type: str, max_len) -> str:
    return {
        "integer": "INT", "bigint": "BIGINT", "smallint": "SMALLINT",
        "numeric": "DECIMAL(18,4)", "real": "FLOAT", "double precision": "FLOAT",
        "boolean": "BIT", "date": "DATE",
        "timestamp without time zone": "DATETIME2",
        "timestamp with time zone":    "DATETIMEOFFSET",
        "uuid": "UNIQUEIDENTIFIER", "text": "NVARCHAR(MAX)",
        "character varying": f"NVARCHAR({max_len or 255})",
        "character":         f"NCHAR({max_len or 1})",
        "json": "NVARCHAR(MAX)", "jsonb": "NVARCHAR(MAX)",
    }.get(pg_type.lower(), "NVARCHAR(MAX)")


def ensure_table_exists(sql_conn, pg_conn, src_table: str, tgt_table: str, columns: list, emit):
    cur = sql_conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES "
        "WHERE TABLE_SCHEMA='dbo' AND TABLE_NAME=?", tgt_table
    )
    if cur.fetchone()[0] > 0:
        emit(f"INFO  [dbo].[{tgt_table}] already exists.", "info")
        return
    emit(f"INFO  Creating [dbo].[{tgt_table}]...", "info")
    with pg_conn.cursor() as c:
        c.execute(
            "SELECT column_name, data_type, character_maximum_length "
            "FROM information_schema.columns "
            "WHERE table_schema=%s AND table_name=%s AND column_name=ANY(%s) "
            "ORDER BY ordinal_position;",
            (PG_SCHEMA, src_table, list(columns))
        )
        pg_cols = c.fetchall()
    col_defs = ",\n    ".join(
        f"[{col}] {pg_to_sql_type(dtype, ml)}" for col, dtype, ml in pg_cols
    )
    cur.execute(f"CREATE TABLE [dbo].[{tgt_table}] (\n    {col_defs}\n);")
    sql_conn.commit()
    emit(f"OK    [dbo].[{tgt_table}] created successfully.", "ok")


def run_migration(pg_cfg: dict, sql_cfg: dict, tables: list, emit) -> dict:
    """
    Runs full migration: for each table →
      1. Get Supabase columns
      2. Intersect with SQL Server columns
      3. TRUNCATE target
      4. IDENTITY_INSERT ON → bulk INSERT → IDENTITY_INSERT OFF
    """
    pg_conn  = psycopg2.connect(**build_pg_connstr(pg_cfg))
    sql_conn = pyodbc.connect(build_sql_connstr(sql_cfg), autocommit=False)
    success = failed = 0
    total   = len(tables)

    try:
        for idx, src_table in enumerate(tables, 1):
            tgt_table = source_to_target(src_table)
            emit(f"\n{'─'*50}", "info")
            emit(f"[{idx}/{total}]  {PG_SCHEMA}.{src_table}", "info")
            emit(f"       →  [dbo].[{tgt_table}]", "info")
            emit(f"{'─'*50}", "info")

            try:
                # Step A — Supabase columns (source of truth)
                pg_cols = get_pg_columns(pg_conn, src_table)
                if not pg_cols:
                    emit(f"WARN  No eligible columns in Supabase. Skipping.", "warn")
                    failed += 1
                    continue
                emit(f"INFO  Supabase columns ({len(pg_cols)}): {pg_cols}", "info")

                # Step B — Ensure target table exists in SQL Server
                ensure_table_exists(sql_conn, pg_conn, src_table, tgt_table, pg_cols, emit)

                # Step C — Intersect: only insert columns that exist on BOTH sides
                sql_col_set = get_sql_columns(sql_conn, tgt_table)
                columns     = [c for c in pg_cols if c.lower() in sql_col_set]
                skipped     = set(pg_cols) - set(columns)
                if skipped:
                    emit(f"WARN  Skipped (missing in SQL Server): {sorted(skipped)}", "warn")
                emit(f"INFO  Inserting {len(columns)} column(s): {columns}", "info")

                if not columns:
                    emit(f"WARN  No matching columns. Skipping.", "warn")
                    failed += 1
                    continue

                # Step D — Fetch rows from Supabase
                col_sel = ", ".join(f'"{c}"' for c in columns)
                with pg_conn.cursor() as cur:
                    cur.execute(f'SELECT {col_sel} FROM "{PG_SCHEMA}"."{src_table}";')
                    rows = cur.fetchall()
                emit(f"INFO  Fetched {len(rows)} row(s) from Supabase.", "info")

                # Step E — Identity columns
                identity_cols = get_identity_columns(sql_conn, tgt_table)
                if identity_cols:
                    emit(f"INFO  IDENTITY columns: {identity_cols}", "info")

                # Step F — TRUNCATE (clean reload)
                emit(f"INFO  Truncating [dbo].[{tgt_table}]...", "info")
                sql_conn.cursor().execute(f"TRUNCATE TABLE [dbo].[{tgt_table}];")
                sql_conn.commit()
                emit(f"OK    Table truncated.", "ok")

                # Step G — IDENTITY_INSERT ON → INSERT → OFF
                has_identity = bool(identity_cols & {c.lower() for c in columns})
                cur = sql_conn.cursor()
                if has_identity:
                    cur.execute(f"SET IDENTITY_INSERT [dbo].[{tgt_table}] ON")

                ins_cols   = ", ".join(f"[{c}]" for c in columns)
                ph         = ", ".join("?" * len(columns))
                insert_sql = f"INSERT INTO [dbo].[{tgt_table}] ({ins_cols}) VALUES ({ph});"
                BATCH      = 500
                errors     = 0
                total_rows = len(rows)

                try:
                    for i in range(0, total_rows, BATCH):
                        batch = rows[i: i + BATCH]
                        for row in batch:
                            try:
                                cur.execute(insert_sql, list(row))
                            except Exception as e:
                                errors += 1
                                emit(f"ERR   Row error: {e}", "err")
                        sql_conn.commit()
                        done = min(i + BATCH, total_rows)
                        emit(f"INFO  Batch {i//BATCH+1}: {len(batch)} rows ({done}/{total_rows})", "info")
                finally:
                    if has_identity:
                        cur.execute(f"SET IDENTITY_INSERT [dbo].[{tgt_table}] OFF")
                        sql_conn.commit()

                if errors == 0:
                    emit(f"OK    [{tgt_table}] — {total_rows}/{total_rows} rows inserted. Errors: 0", "ok")
                else:
                    emit(f"WARN  [{tgt_table}] — {total_rows - errors}/{total_rows} rows. Errors: {errors}", "warn")
                success += 1

            except Exception as e:
                failed += 1
                emit(f"ERR   Failed [{src_table}]: {e}", "err")
                try:
                    sql_conn.rollback()
                except Exception:
                    pass
    finally:
        pg_conn.close()
        sql_conn.close()

    emit(f"\n{'='*50}", "info")
    emit(f"OK    Migration complete.", "ok")
    emit(f"INFO  Total: {total}  |  Success: {success}  |  Failed: {failed}", "info")
    emit(f"{'='*50}", "info")
    return {"total": total, "success": success, "failed": failed}


# ─────────────────────────────────────────────
# CSS (scoped — safe to call inside any page)
# ─────────────────────────────────────────────
def _inject_css():
    st.markdown("""
    <style>
    .mig-card {
        background: #161b27; border: 1px solid #2d3748;
        border-radius: 12px; padding: 22px 24px; margin-bottom: 16px;
    }
    .mig-card h4 {
        color: #63b3ed; font-size: 12px; font-weight: 600;
        letter-spacing: .1em; text-transform: uppercase; margin: 0 0 14px 0;
    }
    .mig-badge-ok   { display:inline-block; padding:3px 12px; border-radius:20px; font-size:12px; font-weight:600; background:#1a3a2a; color:#68d391; border:1px solid #276749; }
    .mig-badge-fail { display:inline-block; padding:3px 12px; border-radius:20px; font-size:12px; font-weight:600; background:#3a1a1a; color:#fc8181; border:1px solid #742a2a; }
    .mig-badge-idle { display:inline-block; padding:3px 12px; border-radius:20px; font-size:12px; font-weight:600; background:#1a1f2e; color:#718096; border:1px solid #2d3748; }
    .mig-terminal {
        background:#0a0d14; border:1px solid #2d3748; border-radius:10px;
        padding:16px 20px; font-family:'JetBrains Mono',monospace; font-size:12px;
        line-height:1.8; max-height:420px; overflow-y:auto;
        color:#a0aec0; white-space:pre-wrap; word-break:break-all;
    }
    .mig-line-ok   { color:#68d391; }
    .mig-line-warn { color:#f6e05e; }
    .mig-line-err  { color:#fc8181; }
    .mig-line-info { color:#90cdf4; }
    .mig-metric { background:#161b27; border:1px solid #2d3748; border-radius:10px; padding:16px; text-align:center; }
    .mig-metric .val { font-size:26px; font-weight:700; color:#63b3ed; font-family:monospace; }
    .mig-metric .lbl { font-size:11px; color:#718096; text-transform:uppercase; letter-spacing:.08em; margin-top:4px; }
    .mig-chip { display:inline-block; background:#1a2535; border:1px solid #2d3748; border-radius:6px; padding:3px 10px; margin:3px; font-family:monospace; font-size:11px; color:#a0aec0; }
    </style>
    """, unsafe_allow_html=True)


def _render_log(lines: list):
    html = ""
    for line in lines:
        text, kind = line
        cls = {"ok": "mig-line-ok", "warn": "mig-line-warn",
               "err": "mig-line-err"}.get(kind, "mig-line-info")
        escaped = text.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
        html += f'<div class="{cls}">{escaped}</div>'
    st.markdown(f'<div class="mig-terminal">{html}</div>', unsafe_allow_html=True)


# ─────────────────────────────────────────────
# Session state init (namespaced to avoid
# collisions with your existing app's state)
# ─────────────────────────────────────────────
def _init_state():
    defaults = {
        "mig_pg_ok":      False,
        "mig_sql_ok":     False,
        "mig_pg_cfg":     {},
        "mig_sql_cfg":    {},
        "mig_tables":     [],
        "mig_log":        [],
        "mig_running":    False,
        "mig_done":       False,
        "mig_metrics":    {},
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


# ─────────────────────────────────────────────
# Main render function — call this from your app
# ─────────────────────────────────────────────
def render():
    _inject_css()
    _init_state()

    # ── Page header ───────────────────────────
    st.markdown("## 🔄 Supabase → SQL Server Migration")
    st.markdown(
        f"Migrates tables matching `{TABLE_PATTERN}` from "
        f"`{PG_SCHEMA}` → SQL Server `dbo` schema. "
        f"Column `system_id` is always excluded."
    )
    st.markdown("---")

    # ── Two-column layout: Supabase | SQL Server ──
    col_left, col_right = st.columns(2, gap="large")

    # ────────────────────────────
    # LEFT — Supabase config
    # ────────────────────────────
    with col_left:
        st.markdown('<div class="mig-card"><h4>🐘 Supabase Connection</h4>', unsafe_allow_html=True)

        pg_host = st.text_input("Host",     value="aws-0-ap-south-1.pooler.supabase.com", key="mig_pg_host")
        pg_port = st.text_input("Port",     value="6543",     key="mig_pg_port")
        pg_db   = st.text_input("Database", value="postgres", key="mig_pg_db")
        pg_user = st.text_input("Username", value="",         key="mig_pg_user")
        pg_pass = st.text_input("Password", value="", type="password", key="mig_pg_pass")

        pg_cfg = {
            "host": pg_host, "port": pg_port,
            "dbname": pg_db, "user": pg_user, "password": pg_pass,
        }

        btn_col, badge_col = st.columns([1, 1])
        with btn_col:
            if st.button("Verify Connection", key="mig_btn_pg", use_container_width=True):
                with st.spinner("Connecting to Supabase..."):
                    ok, result = verify_pg(pg_cfg)
                    if ok:
                        st.session_state.mig_pg_ok  = True
                        st.session_state.mig_pg_cfg  = pg_cfg
                        st.session_state.mig_tables  = []  # reset until both verified
                        st.success(f"Connected! Found {result} matching table(s).")
                    else:
                        st.session_state.mig_pg_ok = False
                        st.error(result)
        with badge_col:
            if st.session_state.mig_pg_ok:
                st.markdown('<span class="mig-badge-ok">✓ Connected</span>', unsafe_allow_html=True)
            else:
                st.markdown('<span class="mig-badge-idle">Not verified</span>', unsafe_allow_html=True)

        st.markdown('</div>', unsafe_allow_html=True)

    # ────────────────────────────
    # RIGHT — SQL Server config
    # ────────────────────────────
    with col_right:
        st.markdown('<div class="mig-card"><h4>🗄️ SQL Server Connection</h4>', unsafe_allow_html=True)

        sql_server = st.text_input(
            "Server",
            value="",
            placeholder="e.g. DESKTOP-ABC, 192.168.1.10, myserver\\SQLEXPRESS",
            key="mig_sql_server",
            help="Server name or IP address of the SQL Server instance"
        )
        sql_db = st.text_input(
            "Database",
            value="WS_MIGRATION",
            key="mig_sql_db"
        )
        sql_driver = st.selectbox(
            "ODBC Driver",
            ["ODBC Driver 17 for SQL Server",
             "ODBC Driver 18 for SQL Server",
             "ODBC Driver 13 for SQL Server",
             "SQL Server"],
            key="mig_sql_driver",
            help="Must be installed on the machine running this app"
        )
        sql_auth = st.selectbox(
            "Authentication",
            ["SQL Server Authentication", "Windows Authentication"],
            key="mig_sql_auth"
        )

        sql_uid = sql_pwd = ""
        if sql_auth == "SQL Server Authentication":
            sql_uid = st.text_input("Username", value="", key="mig_sql_uid")
            sql_pwd = st.text_input("Password", value="", type="password", key="mig_sql_pwd")

        # Live connection string preview
        sql_cfg = {
            "server": sql_server, "database": sql_db,
            "driver": sql_driver, "auth": sql_auth,
            "uid": sql_uid, "pwd": sql_pwd,
        }
        if sql_server and sql_db:
            preview = build_sql_connstr({**sql_cfg, "pwd": "***"})
            st.caption(f"🔗 Connection string: `{preview}`")

        btn_col2, badge_col2 = st.columns([1, 1])
        with btn_col2:
            if st.button("Verify Connection", key="mig_btn_sql", use_container_width=True):
                if not sql_server:
                    st.warning("Please enter a server name.")
                else:
                    with st.spinner("Connecting to SQL Server..."):
                        ok, err = verify_sql(sql_cfg)
                        if ok:
                            st.session_state.mig_sql_ok  = True
                            st.session_state.mig_sql_cfg = sql_cfg
                            st.success("Connected successfully!")
                        else:
                            st.session_state.mig_sql_ok = False
                            st.error(err)
        with badge_col2:
            if st.session_state.mig_sql_ok:
                st.markdown('<span class="mig-badge-ok">✓ Connected</span>', unsafe_allow_html=True)
            else:
                st.markdown('<span class="mig-badge-idle">Not verified</span>', unsafe_allow_html=True)

        st.markdown('</div>', unsafe_allow_html=True)

    # ── Load table preview once both verified ──
    if st.session_state.mig_pg_ok and st.session_state.mig_sql_ok:
        if not st.session_state.mig_tables:
            try:
                pg_conn = psycopg2.connect(**build_pg_connstr(st.session_state.mig_pg_cfg))
                st.session_state.mig_tables = get_pg_tables(pg_conn)
                pg_conn.close()
            except Exception as e:
                st.error(f"Could not load tables: {e}")

    # ── Table mapping preview ──────────────────
    tables = st.session_state.mig_tables
    if tables:
        st.markdown("---")
        with st.expander(f"📋 Tables to migrate ({len(tables)} found)", expanded=True):
            chips = "".join(
                f'<span class="mig-chip">'
                f'<b>{PG_SCHEMA}.{t}</b> → <b>dbo.{source_to_target(t)}</b>'
                f'</span>'
                for t in tables
            )
            st.markdown(chips, unsafe_allow_html=True)

    # ── Run / Clear buttons ────────────────────
    st.markdown("---")
    both_ok = st.session_state.mig_pg_ok and st.session_state.mig_sql_ok

    if not both_ok:
        st.info("👆 Verify both connections above to enable migration.")
    elif not tables:
        st.warning(f"No tables found matching `{TABLE_PATTERN}` in `{PG_SCHEMA}`.")
    else:
        run_col, clear_col = st.columns([3, 1])
        with run_col:
            run_clicked = st.button(
                f"🚀 Run Migration  ({len(tables)} tables)",
                disabled=st.session_state.mig_running,
                use_container_width=True,
                type="primary",
            )
        with clear_col:
            if st.button("🗑️ Clear Log", use_container_width=True):
                st.session_state.mig_log     = []
                st.session_state.mig_done    = False
                st.session_state.mig_metrics = {}
                st.rerun()

        if run_clicked and not st.session_state.mig_running:
            st.session_state.mig_running = True
            st.session_state.mig_done    = False
            st.session_state.mig_log     = []

            log_box = st.empty()

            def emit(msg: str, kind: str = "info"):
                st.session_state.mig_log.append((msg, kind))
                _render_log(st.session_state.mig_log)

            emit(f"{'='*50}", "info")
            emit(f"Migration started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", "info")
            emit(f"Source  : {PG_SCHEMA}.{TABLE_PATTERN}", "info")
            emit(f"Target  : [{st.session_state.mig_sql_cfg['database']}].[dbo].[ws_*]", "info")
            emit(f"Tables  : {len(tables)}", "info")
            emit(f"{'='*50}", "info")

            try:
                result = run_migration(
                    st.session_state.mig_pg_cfg,
                    st.session_state.mig_sql_cfg,
                    tables,
                    emit
                )
                st.session_state.mig_metrics = result
            except Exception as e:
                emit(f"ERR   Fatal: {e}", "err")
            finally:
                st.session_state.mig_running = False
                st.session_state.mig_done    = True

            st.rerun()

    # ── Log display ────────────────────────────
    if st.session_state.mig_log:
        st.markdown("#### 📟 Migration Log")
        _render_log(st.session_state.mig_log)

    # ── Results summary ────────────────────────
    if st.session_state.mig_done and st.session_state.mig_metrics:
        m = st.session_state.mig_metrics
        st.markdown("#### 📊 Results")
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown(f'<div class="mig-metric"><div class="val">{m["total"]}</div><div class="lbl">Tables Processed</div></div>', unsafe_allow_html=True)
        with c2:
            color = "#68d391" if m["success"] == m["total"] else "#f6e05e"
            st.markdown(f'<div class="mig-metric"><div class="val" style="color:{color}">{m["success"]}</div><div class="lbl">Succeeded</div></div>', unsafe_allow_html=True)
        with c3:
            color = "#fc8181" if m["failed"] > 0 else "#68d391"
            st.markdown(f'<div class="mig-metric"><div class="val" style="color:{color}">{m["failed"]}</div><div class="lbl">Failed</div></div>', unsafe_allow_html=True)

        if m["failed"] == 0:
            st.success(f"✅ All {m['total']} table(s) migrated successfully!")
        else:
            st.warning(f"⚠️ {m['failed']} table(s) failed. Check the log above.")


# ── Allow running standalone for testing ──────
if __name__ == "__main__":
    st.set_page_config(page_title="Migration", page_icon="🔄", layout="wide")
    render()
