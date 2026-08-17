"""Streamlit SQL explorer for the scraped Coles/Woolworths data in `scraper_data.duckdb`.

Run with:
    python -m streamlit run dashboard.py

Read-only: opens its own DuckDB connection (`read_only=True`) rather than
reusing `ProductStore`'s write-path connection, so this can run safely
alongside (or independently of) a live `main.py` scrape. `read_only=True`
also means any accidental INSERT/UPDATE/DDL in a typed query fails outright
rather than mutating the scraped data.
"""

from __future__ import annotations

import os

import duckdb
import streamlit as st

DB_PATH = "scraper_data.duckdb"
DEFAULT_QUERY = "SELECT * FROM current_prices ORDER BY scraped_at DESC LIMIT 100"
SCHEMA_TABLES = ("stores", "products", "price_history", "current_prices")

st.set_page_config(page_title="SQL Explorer", layout="wide", page_icon="🔍")


@st.cache_resource
def get_connection() -> duckdb.DuckDBPyConnection | None:
    if not os.path.exists(DB_PATH):
        return None
    return duckdb.connect(DB_PATH, read_only=True)


st.title("🔍 SQL Explorer")
st.caption(f"Read-only SQL access to `{DB_PATH}`.")

conn = get_connection()
if conn is None:
    st.warning(f"No data found in `{DB_PATH}`. Run `python main.py` first to scrape a store.")
    st.stop()

with st.expander("Table schema — stores / products / price_history / current_prices"):
    for table in SCHEMA_TABLES:
        st.caption(table)
        st.dataframe(conn.execute(f"DESCRIBE {table}").fetchdf(), hide_index=True, use_container_width=True)

query = st.text_area("SQL query", value=DEFAULT_QUERY, height=150)
run = st.button("Run query", type="primary")

if run:
    try:
        result_df = conn.execute(query).fetchdf()
    except Exception as exc:
        st.error(f"Query failed: {exc}")
    else:
        st.caption(f"{len(result_df):,} rows")
        st.dataframe(result_df, use_container_width=True, hide_index=True)
        st.download_button(
            "Download as CSV",
            result_df.to_csv(index=False).encode("utf-8"),
            file_name="query_result.csv",
            mime="text/csv",
        )
