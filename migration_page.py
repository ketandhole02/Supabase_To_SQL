import streamlit as st
import psycopg2
import pymssql
from datetime import datetime

# ─────────────────────────────────────────────
# Migration constants
# ─────────────────────────────────────────────
PG_SCHEMA      = "aide_datamart"
TABLE_PATTERN  = "migr_sql_to_ws_%"
SOURCE_PREFIX  = "migr_sql_to_"
EXCLUDE_COLS   = {"system_id"}

# ─────────────────────────────────────────────
# Supabase credentials — BACKEND ONLY
# ─────────────────────────────────────────────
_PG_CONFIG = {
    "host":            "aws-0-ap-south-1.pooler.supabase.com",
    "port":            6543,
    "dbname":          "postgres",
    "user":            "postgres.smvcwjoefalywezaftrw",
    "password":        "SupaBaseDE@2026",
    "sslmode":         "require",
    "connect_timeout": 10,
}

# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def source_to_target(name: str) -> str:
    """migr_sql_to_ws_obj_object → ws_obj_object"""
    if name.startswith(SOURCE_PREFIX):
        return name[len(SOURCE_PREFIX):]
    return name

# ─────────────────────────────────────────────
# Connection helpers
# ─────────────────────────────────────────────

def _get_pg_conn():
    """Internal — uses hardcoded backend credentials."""
    return psycopg2.connect(**_PG_CONFIG)


def _get_sql_conn(cfg: dict, autocommit=False):

    conn = pymssql.connect(
        server=cfg["server"],
        port=1433,
        user=cfg["uid"],
        password=cfg["pwd"],
        database=cfg["database"],
        timeout=30,
        autocommit=autocommit
    )

    return conn
# ─────────────────────────────────────────────
# Connection verification
# ─────────────────────────────────────────────

def verify_pg() -> tuple:
    try:
        conn = _get_pg_conn()

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
    try:
        conn = _get_sql_conn(cfg, autocommit=True)
        conn.close()

        return True, None

    except Exception as e:
        return False, _friendly_sql_error(str(e), cfg)


def _friendly_pg_error(err: str) -> str:

    if "password authentication failed" in err:
        return "❌ Supabase authentication failed."

    if "could not connect" in err:
        return "❌ Cannot reach Supabase."

    return f"❌ Supabase error: {err}"


def _friendly_sql_error(err: str, cfg: dict) -> str:

    if "Login failed" in err:
        return f"❌ Login failed for user '{cfg.get('uid', '')}'."

    if "Cannot open database" in err:
        return f"❌ Database '{cfg.get('database', '')}' not found."

    if "timeout" in err.lower():
        return "❌ Connection timed out."

    if "server" in err.lower():
        return "❌ SQL Server not reachable."

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
            "WHERE table_schema=%s AND table_name=%s "
            "ORDER BY ordinal_position;",
            (PG_SCHEMA, table)
        )

        return [
            r[0]
            for r in cur.fetchall()
            if r[0].lower() not in EXCLUDE_COLS
        ]


def get_sql_columns(sql_conn, table: str) -> set:

    cur = sql_conn.cursor()

    cur.execute(
        "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA='dbo' AND TABLE_NAME=%s",
        (table,)
    )

    return {r[0].lower() for r in cur.fetchall()}


def get_identity_columns(sql_conn, table: str) -> set:

    cur = sql_conn.cursor()

    cur.execute("""
        SELECT COLUMN_NAME
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA='dbo'
        AND TABLE_NAME=%s
    """, (table,))

    return {r[0].lower() for r in cur.fetchall()}


def run_migration(sql_cfg: dict, tables: list, emit) -> dict:

    pg_conn  = _get_pg_conn()
    sql_conn = _get_sql_conn(sql_cfg, autocommit=False)

    success = 0
    failed  = 0
    total   = len(tables)

    try:

        for idx, src_table in enumerate(tables, 1):

            tgt_table = source_to_target(src_table)

            emit(f"[{idx}/{total}] {src_table}", "info")

            try:

                pg_cols = get_pg_columns(pg_conn, src_table)

                col_sel = ", ".join(f'"{c}"' for c in pg_cols)

                with pg_conn.cursor() as cur:

                    cur.execute(
                        f'SELECT {col_sel} '
                        f'FROM "{PG_SCHEMA}"."{src_table}";'
                    )

                    rows = cur.fetchall()

                emit(f"Fetched {len(rows)} rows.", "info")

                cur = sql_conn.cursor()

                cur.execute(f"TRUNCATE TABLE dbo.{tgt_table}")

                sql_conn.commit()

                ins_cols = ", ".join(f"[{c}]" for c in pg_cols)

                ph = ", ".join(["%s"] * len(pg_cols))

                insert_sql = (
                    f"INSERT INTO dbo.{tgt_table} "
                    f"({ins_cols}) VALUES ({ph})"
                )

                for row in rows:
                    cur.execute(insert_sql, tuple(row))

                sql_conn.commit()

                emit(f"OK {tgt_table}", "ok")

                success += 1

            except Exception as e:

                failed += 1

                emit(f"ERR {src_table}: {e}", "err")

                sql_conn.rollback()

    finally:

        pg_conn.close()
        sql_conn.close()

    return {
        "total": total,
        "success": success,
        "failed": failed
    }

# ─────────────────────────────────────────────
# Streamlit App
# ─────────────────────────────────────────────

def render():

    st.title("🔄 Supabase → SQL Server Migration")

    st.markdown("---")

    col1, col2 = st.columns(2)

    # ────────────────────────────────────────
    # Supabase
    # ────────────────────────────────────────

    with col1:

        st.subheader("Supabase")

        if st.button("Verify Supabase"):

            ok, result = verify_pg()

            if ok:
                st.success(f"Connected! Found {result} tables.")
            else:
                st.error(result)

    # ────────────────────────────────────────
    # SQL Server
    # ────────────────────────────────────────

    with col2:

        st.subheader("SQL Server")

        sql_server = st.text_input(
            "SQL Server",
            placeholder="e.g. hostname only"
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

        if sql_server and sql_db:

            preview = (
                f"SERVER={sql_server};"
                f"DATABASE={sql_db};"
                f"UID={sql_uid};"
                f"PWD=***;"
            )

            st.caption(preview)

        if st.button("Verify SQL Server"):

            if not sql_server:
                st.warning("Enter SQL Server")

            elif not sql_db:
                st.warning("Enter Database")

            else:

                ok, err = verify_sql(sql_cfg)

                if ok:
                    st.success("Connected successfully!")
                    st.session_state.sql_cfg = sql_cfg
                else:
                    st.error(err)

    st.markdown("---")

    if st.button("🚀 Run Migration"):

        if "sql_cfg" not in st.session_state:
            st.warning("Verify SQL Server first")
            return

        pg_conn = _get_pg_conn()

        tables = get_pg_tables(pg_conn)

        pg_conn.close()

        logs = []

        def emit(msg, kind="info"):
            logs.append(msg)
            st.write(msg)

        result = run_migration(
            st.session_state.sql_cfg,
            tables,
            emit
        )

        st.success(
            f"Migration Complete | "
            f"Success: {result['success']} | "
            f"Failed: {result['failed']}"
        )

# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

if __name__ == "__main__":

    st.set_page_config(
        page_title="Migration",
        page_icon="🔄",
        layout="wide"
    )

    render()
