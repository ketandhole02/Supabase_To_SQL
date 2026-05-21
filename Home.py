"""
Home.py  —  Main entry point for the Streamlit app.
Navigate to pages using the sidebar.
"""
import streamlit as st

st.set_page_config(
    page_title="Migration Tool",
    page_icon="🔄",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("🔄 Supabase → SQL Server Migration Tool")
st.markdown("""
Welcome! Use the sidebar to navigate to the **Migration** page.

**What this tool does:**
- Connects to your Supabase database
- Discovers all tables matching `migr_sql_to_ws_%`
- Truncates and reloads them into your local SQL Server
- Handles IDENTITY columns, column mismatches, and batch inserts automatically

**Before you start:**
1. Have your Supabase host, port, username and password ready
2. Have your SQL Server name, database name and credentials ready
3. Make sure ODBC Driver 17 (or 18) for SQL Server is installed on your machine
""")
