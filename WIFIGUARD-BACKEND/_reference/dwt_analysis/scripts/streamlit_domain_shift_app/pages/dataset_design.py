"""
P1. 데이터셋 & 표본 설계 — 연산 없음.

결론을 내기 전에 (a) 무엇을 묻고 있는지, (b) 이 데이터셋의 구조적 한계가 무엇인지,
(c) 어떤 표본으로 답할 것인지를 먼저 못박는다. 여기서 뽑은 표본을 P2~P6 가 공유한다.
"""

from pathlib import Path

import pandas as pd
import streamlit as st

from lib.constants import (
    AGC_MANIFEST_REL,
    AGC_SUMMARY_REL,
    COST_META_SEC_PER_FILE,
    COST_PROBE_SEC_PER_FILE,
    NOMINAL_DURATION_SEC,
    PREPROC_MANIFEST_REL,
)
from lib.cache import (
    get_file_index_cached,
    load_csv_artifact_cached,
    meta_cache_status,
    probe_cache_status,
    sample_cached,
)
from lib.disk_cache import list_cached_hashes
from lib.sampling import STRATA_CHOICES, confound_crosstab, sample_cost_estimate, sample_summary
from lib.state_defaults import (
    clear_run_flags,
    meta_fingerprint,
    params_fingerprint,
    sampling_kwargs,
)
from src.dwt_coef.session_builder import ACTIVITY_NAME_MAP

st.title("1. 데이터셋 & 표본 설계")
st.caption(
    "질문: **Mendeley CSI 낙상 데이터셋에서 환경(E1/E2/E3) 간 도메인 시프트가 실제로 존재하는가, "
    "그리고 amfall 전처리는 그것을 완화하는가 증폭하는가?**"
)

repo_root = Path(st.session_state["repo_root"])
mendeley_root = st.session_state["mendeley_root"]
cache_dir = st.session_state["cache_dir"]

try:
    file_index = get_file_index_cached(mendeley_root)
except FileNotFoundError:
    st.error(f"Mendeley 원본 CSV 를 찾을 수 없습니다: `{mendeley_root}`")
    st.stop()

# --- 사이드바 --------------------------------------------------------------

with st.sidebar:
    st.header("표본 설계")

    all_envs = sorted(file_index["env"].unique())
    envs = st.multiselect(
        "환경", all_envs,
        default=[e for e in st.session_state["ds_envs"] if e in all_envs] or all_envs,
        format_func=lambda e: f"E{e}",
    )

    all_acts = sorted(file_index["activity"].unique())
    cur_acts = st.session_state["ds_activities"]
    activities = st.multiselect(
        "활동 (비우면 전체)", all_acts,
        default=[a for a in (cur_acts or []) if a in all_acts],
        format_func=lambda a: f"A{a:02d} {ACTIVITY_NAME_MAP.get(a, '')}"
                              + (" ★낙상" if a in (2, 5) else ""),
    )

    strata_label = st.radio(
        "층화 기준", list(STRATA_CHOICES.keys()),
        index=list(STRATA_CHOICES.values()).index(tuple(st.session_state["ds_strata"]))
        if tuple(st.session_state["ds_strata"]) in STRATA_CHOICES.values() else 0,
        help="각 셀에서 동일한 수의 파일을 뽑는다. 기본값은 환경 × 낙상여부.",
    )
    strata = STRATA_CHOICES[strata_label]

    n_per_cell = st.slider("셀당 파일 수", 5, 100, int(st.session_state["ds_n_per_cell"]), step=5)
    seed = st.number_input("난수 seed", 0, 9999, int(st.session_state["ds_seed"]), step=1)
    balance_subjects = st.checkbox(
        "셀 안에서 피험자 균형 맞추기", value=bool(st.session_state["ds_balance_subjects"]),
        help="피험자를 라운드로빈으로 순회하며 뽑는다. env 와 subject 가 교락된 데이터셋에서 "
             "한 사람이 셀을 지배하면 '환경 효과'가 사실상 '그 사람 효과'가 된다.",
    )

prev = (tuple(st.session_state["ds_envs"]), st.session_state["ds_activities"],
        st.session_state["ds_n_per_cell"], st.session_state["ds_seed"],
        tuple(st.session_state["ds_strata"]), st.session_state["ds_balance_subjects"])

st.session_state.update({
    "ds_envs": tuple(envs), "ds_activities": tuple(activities) if activities else None,
    "ds_n_per_cell": int(n_per_cell), "ds_seed": int(seed),
    "ds_strata": tuple(strata), "ds_balance_subjects": bool(balance_subjects),
})

new = (tuple(envs), tuple(activities) if activities else None, int(n_per_cell),
       int(seed), tuple(strata), bool(balance_subjects))
if prev != new:
    clear_run_flags()

if not envs:
    st.warning("환경을 하나 이상 선택하세요.")
    st.stop()

# --- 1. 데이터셋 구조 -------------------------------------------------------

st.subheader("1.1 데이터셋 구조")

c1, c2, c3, c4 = st.columns(4)
c1.metric("전체 파일", f"{len(file_index):,}")
c2.metric("환경", f"{file_index['env'].nunique()}")
c3.metric("피험자", f"{file_index['subject'].nunique()}")
c4.metric("파일당 길이", f"{NOMINAL_DURATION_SEC:.2f} s")
st.caption(
    "파일 1개 = 활동 1 phase, 정확히 4.00초, 네이티브 ~320 Hz "
    "(패킷 간격 중앙값 3.125 ms = 1/320). 즉 `fs_hz=320` 은 업샘플링이 아니라 원 레이트다."
)

col_a, col_b = st.columns(2)
with col_a:
    st.markdown("**환경 × 실험 조건(class)**")
    st.dataframe(pd.crosstab(file_index["env"], file_index["class_id"]),
                 use_container_width=True)
with col_b:
    st.markdown("**환경 × 활동**")
    st.dataframe(pd.crosstab(file_index["env"], file_index["activity"]),
                 use_container_width=True)

# --- 2. 교락 경고 -----------------------------------------------------------

st.subheader("1.2 ⚠️ 구조적 교락 — 이 앱의 모든 결론에 붙는 단서")

st.error(
    "**환경과 피험자를 분리할 수 없습니다.** Mendeley 프로토콜상 환경별 피험자 풀이 "
    "서로소입니다 (E1=S1–10, E2=S11–20, E3=S21–30). 따라서 이 앱이 측정하는 모든 "
    "'환경 차이'는 실제로는 **(방 효과 + 그 방에서 촬영된 특정 인물들의 효과)** 이며, "
    "어떤 통계 기법으로도 둘을 분리할 수 없습니다.\n\n"
    "피험자 단위 LeaveOneGroupOut 도 이 교락을 풀지 **못합니다** — S03 을 빼도 E1 은 "
    "S01, S02, S04–S10 으로 남습니다. P6 §6.5 의 '같은 환경 안에서 사람 맞히기' 통제가 "
    "이 앱에서 할 수 있는 가장 정직한 대응입니다."
)

ct = confound_crosstab(file_index)
st.markdown("**환경 × 피험자 교차표** — 0 이 아닌 칸이 블록 대각으로만 나타나는 것이 교락의 증거입니다.")
st.dataframe(ct.style.background_gradient(cmap="Greys", axis=None), use_container_width=True)

# --- 3. 분석 계획 -----------------------------------------------------------

st.subheader("1.3 분석 계획")
st.markdown(
    """
| 페이지 | 답하는 질문 | 비용 |
|---|---|---|
| **1. 데이터셋 & 표본 설계** | 무엇을 묻고 있고, 어떤 한계가 있고, 어떤 표본으로 답하는가 | 없음 |
| **2. 수집 단계 시프트** | CSI 값을 하나도 보기 전에, 수집 자체가 환경별로 다른가 (패킷 타이밍 / AGC / RSSI) | 값쌈 (~25 ms/파일) |
| **3. CSI 진폭 프로파일** | 진폭 통계와 서브캐리어 지문이 환경별로 다른가, 그게 단순 이득 차이인가 | 비쌈 (~0.6 s/파일) |
| **4. amfall 단계별 q-value** | 파이프라인 각 단계에서 q 가 어떻게 변하고, 환경 격차가 커지는가 줄어드는가 | P3 캐시 공유 |
| **5. FallDeFi 피처 격차** | FallDeFi 논문이 '환경 강건'하다고 지목한 피처가 정말 그런가 | P3 캐시 공유 |
| **6. 도메인 시프트 종합** | 환경 격차가 활동(낙상) 격차보다 큰가 — 크로스 도메인 일반화가 가능한가 | 값쌈 |

P3·P4·P5 는 **동일한 per-file 결과를 공유**합니다. 하나를 실행하면 나머지는 거의 즉시 열립니다.
"""
)

# --- 4. 표본 -----------------------------------------------------------------

st.subheader("1.4 표본")

sample = sample_cached(mendeley_root, **sampling_kwargs())
if sample.empty:
    st.warning("선택 조건에 해당하는 파일이 없습니다.")
    st.stop()

st.session_state["sample_filepaths"] = tuple(sample["filepath_str"].tolist())

summary = sample_summary(sample)
st.dataframe(summary, use_container_width=True)
st.caption(
    f"층화 기준 **{strata_label}**, 셀당 최대 **{n_per_cell}** 개, seed **{seed}**, "
    f"피험자 균형 **{'켜짐' if balance_subjects else '꺼짐'}** → 총 **{len(sample)}** 개 파일. "
    "같은 seed 는 항상 같은 표본을 만듭니다. `max_subject_share` 가 1/피험자수 에 가까울수록 균형이 좋습니다."
)

m1, m2 = st.columns(2)
m1.metric("P2 메타데이터 pass 예상", f"{len(sample) * COST_META_SEC_PER_FILE:.0f} 초")
m2.metric("P3~P5 CSI pass 예상", f"{len(sample) * COST_PROBE_SEC_PER_FILE:.0f} 초")
st.caption(sample_cost_estimate(len(sample), COST_PROBE_SEC_PER_FILE))

# --- 5. 이미 계산된 산출물 / 캐시 상태 ---------------------------------------

st.subheader("1.5 이미 계산된 것 / 캐시 상태")
st.caption("클릭하기 전에 무엇이 공짜이고 무엇이 비싼지 확인하는 자리입니다.")

paths = sample["filepath_str"].tolist()
meta_st = meta_cache_status(cache_dir, meta_fingerprint(), paths)
probe_st = probe_cache_status(cache_dir, params_fingerprint(), paths)

s1, s2 = st.columns(2)
s1.metric("메타데이터 캐시", f"{meta_st['cached']} / {meta_st['total']}",
          help=f"params_hash={meta_st['params_hash']}")
s2.metric("CSI probe 캐시", f"{probe_st['cached']} / {probe_st['total']}",
          help=f"params_hash={probe_st['params_hash']}")

artifacts = []
for label, rel, note in (
    ("AGC manifest (전체 9000 파일)", AGC_MANIFEST_REL, "P2 에서 표본 검증용 정답으로 사용"),
    ("AGC 요약 통계", AGC_SUMMARY_REL, "환경별 전체 평균"),
    ("전처리 manifest (DVC)", PREPROC_MANIFEST_REL, "mean_qh 교차 확인용, 없을 수 있음"),
):
    df = load_csv_artifact_cached(str(repo_root / rel))
    artifacts.append({"산출물": label, "경로": rel,
                      "상태": f"{len(df):,} 행" if not df.empty else "없음", "용도": note})
st.dataframe(pd.DataFrame(artifacts), use_container_width=True)

cached_files = list_cached_hashes(cache_dir)
if not cached_files.empty:
    with st.expander(f"디스크 캐시 파일 {len(cached_files)}개"):
        st.dataframe(cached_files, use_container_width=True)
        st.caption(
            "캐시는 `(filepath, params_hash)` 단위 행으로 쌓입니다. 셀당 파일 수를 "
            "20 → 30 으로 올려도 새로 계산하는 건 10개뿐입니다. 파이프라인 파라미터를 바꾸면 "
            "`params_hash` 가 바뀌어 **새 파일**이 생깁니다 (조용한 재사용은 정확성 버그이므로)."
        )

with st.expander(f"표본 파일 전체 목록 ({len(sample)}개)"):
    st.dataframe(
        sample[["name", "env", "subject", "activity", "activity_name", "trial",
                "fall_label", "cell"]],
        use_container_width=True,
    )
