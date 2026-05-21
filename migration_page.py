import streamlit as st
import psycopg2
import pyodbc
from datetime import datetime
import requests
import socket

# ─────────────────────────────────────────────
# DEBUG / NETWORK CHECKS
# ─────────────────────────────────────────────

# try:
#     ip = requests.get("https://api.ipify.org").text
#     st.success(f"🌍 Streamlit Public IP: {ip}")
# except Exception as e:
#     st.error(f"IP Error: {e}")

# try:
#     host = "aide-aws-sqlserver.cva2cqyqop46.eu-north-1.rds.amazonaws.com"
#     port = 1433

#     socket.create_connection((host, port), timeout=10)

#     st.success("✅ Port 1433 reachable")

# except Exception as e:
#     st.error(f"❌ Connection failed: {e}")

# ─────────────────────────────────────────────
# MIGRATION CONSTANTS
# ─────────────────────────────────────────────

PG_SCHEMA = "aide_datamart"
TABLE_PATTERN = "migr_sql_to_ws_%"
SOURCE_PREFIX = "migr_sql_to_"
EXCLUDE_COLS = {"system_id"}

# ─────────────────────────────────────────────
# SUPABASE CONFIG
# ─────────────────────────────────────────────

_PG_CONFIG = {
    "host": "aws-0-ap-south-1.pooler.supabase.com",
    "port": 6543,
    "dbname": "postgres",
    "user": "postgres.smvcwjoefalywezaftrw",
    "password": "SupaBaseDE@2026",
    "sslmode": "require",
    "connect_timeout": 10,
}

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def source_to_target(name: str) -> str:
    """
    migr_sql_to_ws_table → ws_table
    """
    if name.startswith(SOURCE_PREFIX):
        return name[len(SOURCE_PREFIX):]

    return name

# ─────────────────────────────────────────────
# CONNECTION HELPERS
# ─────────────────────────────────────────────

def _get_pg_conn():
    """
    Supabase connection
    """
    return psycopg2.connect(**_PG_CONFIG)


def _get_sql_conn(cfg: dict, autocommit=False):
    """
    SQL Server connection using pyodbc
    Driver is hardcoded in backend only
    """

    conn_str = (
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER={cfg['server']};"
        f"DATABASE={cfg['database']};"
        f"UID={cfg['uid']};"
        f"PWD={cfg['pwd']};"
        "TrustServerCertificate=yes;"
    )

    conn = pyodbc.connect(conn_str, timeout=30)

    conn.autocommit = autocommit

    return conn

# ─────────────────────────────────────────────
# CONNECTION VALIDATION
# ─────────────────────────────────────────────

def verify_pg():

    try:

        conn = _get_pg_conn()

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT COUNT(*)
                FROM information_schema.tables
                WHERE table_schema=%s
                AND table_name LIKE %s
                """,
                (PG_SCHEMA, TABLE_PATTERN)
            )

            count = cur.fetchone()[0]

        conn.close()

        return True, count

    except Exception as e:

        return False, str(e)


def verify_sql(cfg: dict):

    try:

        conn = _get_sql_conn(cfg, autocommit=True)

        conn.close()

        return True, None

    except Exception as e:

        return False, str(e)

# ─────────────────────────────────────────────
# POSTGRES HELPERS
# ─────────────────────────────────────────────

def get_pg_tables(pg_conn):

    with pg_conn.cursor() as cur:

        cur.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema=%s
            AND table_name LIKE %s
            ORDER BY table_name
            """,
            (PG_SCHEMA, TABLE_PATTERN)
        )

        return [r[0] for r in cur.fetchall()]


def get_pg_columns(pg_conn, table):

    with pg_conn.cursor() as cur:

        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema=%s
            AND table_name=%s
            ORDER BY ordinal_position
            """,
            (PG_SCHEMA, table)
        )

        return [
            r[0]
            for r in cur.fetchall()
            if r[0].lower() not in EXCLUDE_COLS
        ]

# ─────────────────────────────────────────────
# SQL SERVER HELPERS
# ─────────────────────────────────────────────

def get_sql_columns(sql_conn, table):

    cur = sql_conn.cursor()

    cur.execute(
        """
        SELECT COLUMN_NAME
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA='dbo'
        AND TABLE_NAME=?
        """,
        table
    )

    return {r[0].lower() for r in cur.fetchall()}

# ─────────────────────────────────────────────
# MIGRATION LOGIC
# ─────────────────────────────────────────────

def run_migration(sql_cfg, tables, emit):

    pg_conn = _get_pg_conn()

    sql_conn = _get_sql_conn(sql_cfg, autocommit=False)

    success = 0
    failed = 0

    try:

        for idx, src_table in enumerate(tables, 1):

            tgt_table = source_to_target(src_table)

            emit(f"🚀 [{idx}/{len(tables)}] {src_table}")

            try:

                pg_cols = get_pg_columns(pg_conn, src_table)

                sql_cols = get_sql_columns(sql_conn, tgt_table)

                columns = [
                    c for c in pg_cols
                    if c.lower() in sql_cols
                ]

                if not columns:
                    emit(f"⚠️ No matching columns for {tgt_table}")
                    failed += 1
                    continue

                col_sel = ", ".join(f'"{c}"' for c in columns)

                with pg_conn.cursor() as cur:

                    cur.execute(
                        f'''
                        SELECT {col_sel}
                        FROM "{PG_SCHEMA}"."{src_table}"
                        '''
                    )

                    rows = cur.fetchall()

                emit(f"📥 Rows fetched: {len(rows)}")

                cur = sql_conn.cursor()

                cur.execute(f"TRUNCATE TABLE dbo.{tgt_table}")

                sql_conn.commit()

                insert_cols = ", ".join(f"[{c}]" for c in columns)

                placeholders = ", ".join("?" * len(columns))

                insert_sql = f"""
                    INSERT INTO dbo.{tgt_table}
                    ({insert_cols})
                    VALUES ({placeholders})
                """

                cur.fast_executemany = True

                cur.executemany(insert_sql, rows)

                sql_conn.commit()

                emit(f"✅ Completed: {tgt_table}")

                success += 1

            except Exception as e:

                sql_conn.rollback()

                emit(f"❌ Failed {src_table}: {e}")

                failed += 1

    finally:

        pg_conn.close()

        sql_conn.close()

    return {
        "success": success,
        "failed": failed,
        "total": len(tables)
    }

# ─────────────────────────────────────────────
# STREAMLIT UI
# ─────────────────────────────────────────────

def render():

    st.title("🔄 Supabase → SQL Server Migration")

    st.markdown("---")

    col1, col2 = st.columns(2)

    # ────────────────────────────────────────
    # SUPABASE
    # ────────────────────────────────────────

    with col1:

        st.subheader("🐘 Supabase")

        if st.button("Verify Supabase"):

            ok, result = verify_pg()

            if ok:
                st.success(f"Connected! Tables Found: {result}")
            else:
                st.error(result)

    # ────────────────────────────────────────
    # SQL SERVER
    # ────────────────────────────────────────

    with col2:

        st.subheader("🗄️ SQL Server")

        sql_server = st.text_input(
            "SQL Server",
            placeholder="hostname only"
        )

        sql_db = st.text_input(
            "Database"
        )

        sql_uid = st.text_input(
            "Username"
        )

        sql_pwd = st.text_input(
            "Password",
            type="password"
        )

        sql_cfg = {
            "server": sql_server,
            "database": sql_db,
            "uid": sql_uid,
            "pwd": sql_pwd,
        }

        if st.button("Verify SQL Server"):

            if not sql_server:
                st.warning("Enter SQL Server")

            elif not sql_db:
                st.warning("Enter Database")

            else:

                ok, err = verify_sql(sql_cfg)

                if ok:

                    st.success("✅ SQL Server Connected")

                    st.session_state.sql_cfg = sql_cfg

                else:

                    st.error(err)

    st.markdown("---")

    # ────────────────────────────────────────
    # RUN MIGRATION
    # ────────────────────────────────────────

    if st.button("🚀 Run Migration"):

        if "sql_cfg" not in st.session_state:

            st.warning("Verify SQL Server first")

            return

        pg_conn = _get_pg_conn()

        tables = get_pg_tables(pg_conn)

        pg_conn.close()

        st.info(f"Tables Found: {len(tables)}")

        logs = st.empty()

        log_lines = []

        def emit(msg):

            log_lines.append(msg)

            logs.code("\n".join(log_lines))

        result = run_migration(
            st.session_state.sql_cfg,
            tables,
            emit
        )

        st.success(
            f"""
            Migration Completed

            Total   : {result['total']}
            Success : {result['success']}
            Failed  : {result['failed']}
            """
        )

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

if __name__ == "__main__":

    st.set_page_config(
        page_title="Migration",
        page_icon="🔄",
        layout="wide"
    )

    render()
