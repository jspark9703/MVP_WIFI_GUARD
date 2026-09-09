"""In-house CSI 피처 탐색 앱 (CWT S3 vs PCA-ACF, 프레넬 조건별).

data/raw/ 의 자체 수집 ESP32-C5 CSI 중 낙상 라벨이 포함된 3초 세그먼트에서
학습 파이프라인과 동일한 두 피처를 뽑아 F(프레넬) 조건별로 비교한다.

Mendeley 전용인 scripts/streamlit_app/ 과는 데이터소스가 달라 별도 앱으로 분리했다.

Usage:
    streamlit run scripts/streamlit_inhouse_app/app.py
"""

import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
REPO_ROOT = APP_DIR.parent.parent
sys.path.insert(0, str(REPO_ROOT))  # for `src.dwt_coef.*`
sys.path.insert(0, str(APP_DIR))  # for `lib.*`

RAW_ROOT = str(REPO_ROOT / "data" / "raw")

import streamlit as st  # noqa: E402

from lib.state_defaults import init_session_state  # noqa: E402

st.set_page_config(page_title="In-house CSI Feature Explorer", layout="wide")

init_session_state()
st.session_state["raw_root"] = RAW_ROOT

pages_dir = APP_DIR / "pages"
pg = st.navigation(
    [
        st.Page(str(pages_dir / "segment_inspector.py"), title="1. Segment Inspector", icon="🔍"),
        st.Page(str(pages_dir / "fresnel_compare.py"), title="2. Fresnel Compare", icon="📐"),
        st.Page(str(pages_dir / "feature_separability.py"), title="3. Separability", icon="🧭"),
        st.Page(str(pages_dir / "recording_sweep.py"), title="4. Recording Sweep", icon="🎞️"),
    ]
)
pg.run()
