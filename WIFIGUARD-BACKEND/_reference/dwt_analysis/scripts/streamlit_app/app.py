#!/usr/bin/env python3
"""
Moving-Variance / Subcarrier-Selection Visualization Research App

Entry point for the 3-page Streamlit app:
    Page 1: Preprocess Tuning  - tune resample/filter/moving-variance/subcarrier-selection
            parameters against a reconstructed multi-phase Mendeley session.
    Page 2: Realtime Monitor   - replay a session as dummy real-time data using the
            parameters chosen on Page 1.
    Page 3: Subcarrier Stats   - run selection across a sample of sessions to check
            whether antenna/environment/protocol affect which subcarriers get selected.

Usage:
    conda activate wifisense
    streamlit run scripts/streamlit_app/app.py
"""

import sys
from pathlib import Path

# scripts/streamlit_app/app.py -> repo root is two levels up
APP_DIR = Path(__file__).resolve().parent
REPO_ROOT = APP_DIR.parent.parent
sys.path.insert(0, str(REPO_ROOT))  # for `src.dwt_coef.*`
sys.path.insert(0, str(APP_DIR))    # for `lib.*`

MENDELEY_ROOT = str(REPO_ROOT / "Mendeley")

import streamlit as st

from lib.state_defaults import init_session_state

st.set_page_config(page_title="CSI Moving-Variance Explorer", layout="wide")

init_session_state()
st.session_state["mendeley_root"] = MENDELEY_ROOT

pages_dir = Path(__file__).parent / "pages"

pg = st.navigation([
    st.Page(str(pages_dir / "preprocess_tuning.py"), title="1. Preprocess Tuning", icon="🎛️"),
    st.Page(str(pages_dir / "realtime_monitor.py"), title="2. Realtime Monitor", icon="📡"),
    st.Page(str(pages_dir / "subcarrier_stats.py"), title="3. Subcarrier Stats", icon="📊"),
])
pg.run()
