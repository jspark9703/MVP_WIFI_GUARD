"""
Mendeley 도메인 시프트 탐색기.

raw 데이터의 환경별 통계와 amfall 전처리 단계별 q-value 변화를 함께 보여, 이 데이터셋에
도메인 시프트가 실제로 존재하는지 확인한다. 페이지 순서 자체가 분석 과정이다:
표본 설계 -> 수집 단계 -> CSI 진폭 -> q 파이프라인 -> 종합.

실행: streamlit run scripts/streamlit_domain_shift_app/app.py
"""

import sys
from pathlib import Path

# scripts/streamlit_domain_shift_app/app.py -> 레포 루트는 두 단계 위
APP_DIR = Path(__file__).resolve().parent
REPO_ROOT = APP_DIR.parent.parent
sys.path.insert(0, str(REPO_ROOT))  # for `src.dwt_coef.*`
sys.path.insert(0, str(APP_DIR))    # for `lib.*`

import streamlit as st  # noqa: E402

from lib.constants import CACHE_DIR_REL  # noqa: E402
from lib.state_defaults import init_session_state  # noqa: E402

st.set_page_config(page_title="Mendeley Domain Shift Explorer", layout="wide")

init_session_state()
st.session_state["repo_root"] = str(REPO_ROOT)
st.session_state["mendeley_root"] = str(REPO_ROOT / "Mendeley")
st.session_state["cache_dir"] = str(REPO_ROOT / CACHE_DIR_REL)

pages_dir = APP_DIR / "pages"

pg = st.navigation([
    st.Page(str(pages_dir / "dataset_design.py"), title="1. 데이터셋 & 표본 설계", icon="🗂️"),
    st.Page(str(pages_dir / "acquisition_shift.py"), title="2. 수집 단계 시프트", icon="📶"),
    st.Page(str(pages_dir / "csi_profile.py"), title="3. CSI 진폭 프로파일", icon="📈"),
    st.Page(str(pages_dir / "q_evolution.py"), title="4. amfall 단계별 q-value", icon="🔬"),
    st.Page(str(pages_dir / "falldefi_features.py"), title="5. FallDeFi 피처 격차", icon="📐"),
    st.Page(str(pages_dir / "shift_summary.py"), title="6. 도메인 시프트 종합", icon="🧭"),
    st.Page(str(pages_dir / "cross_domain_fall.py"), title="7. CWT·ACF 교차 도메인", icon="🎯"),
])
pg.run()
