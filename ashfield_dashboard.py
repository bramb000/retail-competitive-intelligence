"""Ashfield gold DuckDB viewer.

    .venv/bin/python -m streamlit run ashfield_dashboard.py
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import streamlit as st

DB = Path(__file__).resolve().parent / "lake" / "gold" / "ashfield_compare.duckdb"
TABLES = (
    "banner_compare",
    "sku_matches",
    "category_crosswalk",
    "category_pricing",
    "category_space",
    "category_venn",
    "sku_facts",
)

st.set_page_config(page_title="Ashfield gold", layout="wide")
st.title("Ashfield gold")

if not DB.exists():
    st.error(f"Missing {DB} — run: .venv/bin/python scrape_ashfield_deep.py --phase etl")
    st.stop()

conn = duckdb.connect(str(DB), read_only=True)
table = st.selectbox("Table", TABLES)
df = conn.execute(f"SELECT * FROM gold.{table}").df()
st.caption(f"{len(df):,} rows · {DB}")
st.dataframe(df, use_container_width=True, hide_index=True)
