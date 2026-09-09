"""
P7. CWT 스케일로그램 · PCA-ACF 의 낙상 분류 도메인 강건성.

앞 페이지들은 **분포 격차**로 시프트를 봤다. 여기서는 그 다음 질문에 답한다:
**"그래서 낙상 분류기가 방이 바뀌면 무너지는가?"**

핵심은 통제다. 순진한 LOEO(환경 내 CV vs 나머지 두 환경으로 학습)는 방이 아니라
**학습셋 크기**를 잰다 — 실측에서 격차의 부호가 뒤집혔다. 이 페이지는 모든 arm 의
학습 피험자 수와 활동 구성을 맞추고 테스트셋을 고정한 뒤 **학습한 방만** 바꾼다.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

from lib.constants import (
    ARM_LABELS,
    COST_CWTACF_SEC_PER_SAMPLE,
    CWTACF_KB_PER_SAMPLE,
    ENV_LABELS,
    FALLDEFI_OF_METRICS,
    FALLDEFI_SF_METRICS,
    FALL_ACTIVITY_IDS,
    NONFALL_ACTIVITY_IDS,
    PC_METRICS,
    Q_METRICS,
)
from lib.cache import (
    cwt_acf_batch_cached,
    cwt_acf_file_cached,
    cwt_acf_status,
    get_file_index_cached,
    probe_batch_cached,
    sample_fall_nonfall_cached,
)
from lib.deep_features import DeepFeatureParams, qc_summary
from lib.disk_cache import params_hash
from lib.divergence import cliffs_delta
from lib.embed import (
    BranchSpec,
    assert_unfitted,
    block_boundary,
    build_design_matrix,
    clamp_pca_dim,
    default_branches,
    make_pipeline,
)
from lib.plotting import (
    plot_acf_map,
    plot_arm_dotplot,
    plot_branch_scatter,
    plot_cwt_scalogram,
    plot_gap_bars,
    plot_group_box,
    plot_metric_triple,
    plot_per_subject_strip,
    plot_placebo_null,
)
from lib.render import chart
from lib.sampling import confound_crosstab
from lib import xdomain as XD
from lib.state_defaults import params_fingerprint, probe_kwargs

st.title("7. CWT · PCA-ACF 의 낙상 분류 도메인 강건성")
st.caption(
    "CWT S3 스케일로그램(224 주파수 × 224 시간)과 PCA-ACF(128 lag × 64 시간)로 낙상을 분류하고, "
    "**같은 방에서 학습했을 때와 다른 방에서 학습했을 때의 차이**를 잰다. "
    "기존 피처군(amfall q, FallDeFi)도 **동일한 폴드·동일한 분류기**로 나란히 비교한다."
)

mendeley_root = st.session_state["mendeley_root"]
cache_dir = st.session_state["cache_dir"]

POOL_CHOICES = {"14x14": (14, 14), "28x28": (28, 28)}

# --- 사이드바 ---------------------------------------------------------------

with st.sidebar:
    st.header("표본")
    n_fall = st.slider("낙상 파일/셀 (env × activity)", 5, 60,
                       int(st.session_state["xd_n_fall_per_cell"]), step=5)
    n_nonfall = st.slider("비낙상 파일/셀", 1, 12,
                          int(st.session_state["xd_n_nonfall_per_cell"]), step=1)
    rxs = st.multiselect("사용 rx", [1, 2, 3],
                         default=list(st.session_state["xd_rxs"]),
                         help="rx 는 행을 늘리지만 유효 표본(피험자 30명)은 늘리지 않습니다. "
                              "예측은 파일 단위로 합치고 CV 그룹은 피험자입니다.")

    st.divider()
    st.header("프로토콜")
    n_test = st.slider("테스트 피험자 수", 3, 5, int(st.session_state["xd_n_test_subjects"]))
    n_train = st.slider("학습 피험자 수 (모든 arm 동일)", 3, 5,
                        int(st.session_state["xd_n_train_subjects"]),
                        help="IN 과 CROSS 를 구조적으로 동일하게 만들기 위해 양쪽 모두 이 수로 맞춥니다.")
    n_rep = st.slider("반복 수", 3, 30, int(st.session_state["xd_n_replicates"]))

    st.divider()
    st.header("표현 / 모델")
    pool_label = st.radio("풀링", list(POOL_CHOICES),
                          index=list(POOL_CHOICES).index(st.session_state["xd_pool"])
                          if st.session_state["xd_pool"] in POOL_CHOICES else 0,
                          horizontal=True,
                          help="실측상 14x14 가 raw flatten(50176차)보다 정확도가 높습니다.")
    pca_dim = st.slider("PCA 차원 (파이프라인 내부)", 5, 40, int(st.session_state["xd_pca_dim"]))
    n_boot = st.slider("부트스트랩 반복", 200, 3000, int(st.session_state["xd_n_boot"]), step=200)
    seed = st.number_input("seed", 0, 9999, int(st.session_state["xd_seed"]), step=1)

prev = (st.session_state["xd_n_fall_per_cell"], st.session_state["xd_n_nonfall_per_cell"],
        tuple(st.session_state["xd_rxs"]), st.session_state["xd_pool"])
st.session_state.update({
    "xd_n_fall_per_cell": int(n_fall), "xd_n_nonfall_per_cell": int(n_nonfall),
    "xd_rxs": tuple(rxs) if rxs else (1,),
    "xd_n_test_subjects": int(n_test), "xd_n_train_subjects": int(n_train),
    "xd_n_replicates": int(n_rep), "xd_pool": pool_label, "xd_pca_dim": int(pca_dim),
    "xd_n_boot": int(n_boot), "xd_seed": int(seed),
})
if prev != (int(n_fall), int(n_nonfall), tuple(rxs) if rxs else (1,), pool_label):
    st.session_state["xd_has_run"] = False

if not rxs:
    st.warning("rx 를 하나 이상 선택하세요.")
    st.stop()

fd_params = DeepFeatureParams()
hash8 = params_hash(fd_params.fingerprint())

# --- 7.0 설계 계약 -----------------------------------------------------------

st.subheader("7.0 설계 계약 — 무엇을 고정하고 무엇만 바꾸는가")

st.markdown(
    f"""
| | IN (플라시보) | CROSS (헤드라인) |
|---|---|---|
| 테스트 집합 | **동일** (대상 환경의 홀드아웃 피험자 {n_test}명) | **동일** |
| 학습 피험자 수 | {n_train}명 | **{n_train}명 (맞춤)** |
| 학습 활동 구성 | 템플릿 | **동일하게 맞춤** |
| 학습한 사람 | 새 사람 | 새 사람 |
| **학습한 방** | **같은 방** | **다른 방** ← 유일한 차이 |

`gap = IN − CROSS` 는 **동일 테스트셋 위의 대응(paired) 차이**다. IN 은 단순 baseline 이
아니라 **플라시보**이며, IN 점수의 분포가 곧 "학습한 사람들이 녹화된 방은 상관없다"는
**귀무분포**다. CROSS 가 그 분포 안에 들어오면 측정 가능한 방 효과가 없는 것이다.
"""
)

st.error(
    "**분리 불가능한 교락**: E1=S1–10, E2=S11–20, E3=S21–30 으로 환경별 피험자 풀이 "
    "서로소다. 따라서 CROSS 는 '다른 방 + 다른 사람'이다. 다만 **IN 도 홀드아웃 피험자로 "
    "시험**하므로 양쪽 다 '새 사람'이고, 두 arm 의 차이는 방에 귀속된다 — 이 데이터셋에서 "
    "가능한 가장 깨끗한 설계지만, 방 효과와 '그 방에서 촬영된 사람들'의 효과는 끝내 분리되지 않는다."
)

with st.expander("왜 통제가 필요한가 — 순진한 LOEO 는 방이 아니라 학습셋 크기를 잰다"):
    st.markdown(
        """
소규모 실측(108샘플)에서 "환경 내 CV vs 나머지 두 환경으로 학습"을 그대로 재니 격차가
**음수**(CWT S3 −0.102)로 나왔다. in-domain 은 환경 1개(36샘플), cross 는 환경 2개(72샘플)에서
학습하니 당연한 결과다. 학습 크기를 맞추자 **−0.031 로 붕괴**했다.

크기만 맞춰도 학습 **피험자 수**와 **학습에 쓰인 방의 개수**(1개 vs 2개 — 다중 소스 학습은
그 자체가 도메인 일반화 기법)가 남는다. 그래서 이 페이지는 양쪽 모두 피험자 수를 맞춘다.
아래 결과의 `LOEO-full` arm 이 통제 없는 수치이며, 보통 IN 보다도 높게 나온다 —
"도메인 문제가 없다"로 오독되기 딱 좋은 숫자다.
"""
    )
    st.dataframe(confound_crosstab(get_file_index_cached(mendeley_root))
                 .style.background_gradient(cmap="Greys", axis=None),
                 use_container_width=True)

# --- 표본 ------------------------------------------------------------------

sample = sample_fall_nonfall_cached(
    mendeley_root, (1, 2, 3), FALL_ACTIVITY_IDS, NONFALL_ACTIVITY_IDS,
    int(n_fall), int(n_nonfall), int(seed),
)
if sample.empty:
    st.warning("표본이 비어 있습니다.")
    st.stop()

paths = sample["filepath_str"].tolist()
n_samples = len(paths) * len(rxs)
status = cwt_acf_status(cache_dir, hash8, paths, tuple(rxs))

st.subheader("7.1 표본 구성")
c1, c2, c3, c4 = st.columns(4)
c1.metric("파일", f"{len(sample):,}")
c2.metric("낙상 / 비낙상", f"{int(sample['is_fall'].sum())} / {int((~sample['is_fall']).sum())}")
c3.metric("샘플 (파일 × rx)", f"{n_samples:,}")
c4.metric("피험자", f"{sample['subject'].nunique()}")

marg = pd.crosstab(sample["env"], sample["activity"])
identical = bool(marg.nunique().eq(1).all())
st.dataframe(marg, use_container_width=True)
(st.success if identical else st.warning)(
    ("✅ 환경 간 활동 주변분포가 동일합니다 — 활동 격차를 방 격차로 오독할 위험이 없습니다."
     if identical else
     "⚠️ 환경 간 활동 구성이 다릅니다. `(env, activity)` 층화가 제대로 되지 않았습니다.")
)

st.caption(
    f"캐시 적중 **{status['cached']}** / 계산 필요 **{status['missing']}** → "
    f"예상 **{status['missing'] * COST_CWTACF_SEC_PER_SAMPLE:.0f}초**, "
    f"저장 **{n_samples * CWTACF_KB_PER_SAMPLE / 1024:.0f} MB** (params_hash `{hash8}`). "
    f"rx 를 1개로 줄이면 비용은 1/3 이고 **유효 표본(피험자 {sample['subject'].nunique()}명)은 그대로**입니다."
)

if st.button("CWT/ACF 추출 + 교차 도메인 평가", type="primary", use_container_width=True):
    st.session_state["xd_has_run"] = True

if not st.session_state["xd_has_run"]:
    st.info("사이드바 설정을 확인한 뒤 위 버튼을 눌러 실행하세요.")
    st.stop()

progress = st.progress(0.0, text="CWT/ACF 추출 준비 중...")


def _on_progress(done: int, total: int, name: str) -> None:
    progress.progress(done / max(total, 1), text=f"[{done}/{total}] {name}")


s3, acf, rows, errors = cwt_acf_batch_cached(
    paths, tuple(rxs), fd_params, cache_dir, hash8, progress_callback=_on_progress,
)
progress.empty()

if rows.empty:
    st.error("피처 추출 결과가 비어 있습니다.")
    if errors:
        st.write(errors[:5])
    st.stop()

if errors:
    with st.expander(f"⚠️ 건너뛴 샘플 {len(errors)}개"):
        for line in errors[:50]:
            st.write(line)

# --- 7.2 피처 QC ------------------------------------------------------------

st.subheader("7.2 피처 QC — 정확도를 보기 전에")
st.caption(
    "crop 이 가장자리에 붙었거나(pad>0) S3 가 거의 비었/찼으면 그 샘플의 스케일로그램은 "
    "다른 종류의 물건이다. 이 비율이 환경별로 다르면 도메인 시프트가 아니라 **추출 실패율 차이**를 "
    "재고 있을 수 있다."
)

qc = qc_summary(rows)
st.dataframe(qc.round(4), use_container_width=True, hide_index=True)

qc_flags = []
for col in ("interp_ratio", "s3_energy_above_80hz", "s3_nonzero_ratio"):
    if col not in rows.columns:
        continue
    worst = 0.0
    for a, b in ((1, 2), (1, 3), (2, 3)):
        d = cliffs_delta(rows.loc[rows["env"] == a, col], rows.loc[rows["env"] == b, col])["delta"]
        if d is not None:
            worst = max(worst, abs(d))
    if worst > 0.474:
        qc_flags.append(f"`{col}` (최대 |δ|={worst:.2f})")

if qc_flags:
    st.error(
        "⚠️ **환경 간 피처 품질 차이가 큽니다**: " + ", ".join(qc_flags) +
        ". 아래 정확도 격차의 일부는 방 효과가 아니라 추출 품질 차이일 수 있습니다. "
        "7.5 의 저레이트 제외 토글로 반증해 보세요."
    )
else:
    st.success("✅ 환경 간 피처 품질 차이가 크지 않습니다 (모든 |Cliff's δ| ≤ 0.474).")

qcols = st.columns(3)
for col, metric in zip(qcols, ("s3_nonzero_ratio", "s3_energy_above_80hz", "interp_ratio")):
    with col:
        chart(plot_group_box(rows, metric, "env", metric, metric, height=300))

# --- 7.3 대표 샘플 ----------------------------------------------------------

st.subheader("7.3 피처 눈으로 보기")

names = rows.drop_duplicates("filepath").sort_values(["env", "subject", "activity"])
options = names["filepath"].tolist()
labels = dict(zip(names["filepath"],
                  names["name"] + "  (" + np.where(names["is_fall"], "낙상", "비낙상") + ")"))
default_idx = options.index(st.session_state["xd_focus_file"]) \
    if st.session_state["xd_focus_file"] in options else 0
focus = st.selectbox("파일", options, index=default_idx, format_func=lambda p: labels.get(p, p))
st.session_state["xd_focus_file"] = focus

one = cwt_acf_file_cached(focus, int(rxs[0]))
fc1, fc2 = st.columns(2)
with fc1:
    chart(plot_cwt_scalogram(one["s3"], fs_hz=fd_params.fs_hz,
                             freq_min_hz=fd_params.freq_min_hz,
                             freq_max_hz=fd_params.freq_max_hz,
                             segment_sec=fd_params.segment_sec))
with fc2:
    chart(plot_acf_map(one["acf"], lag_seconds=fd_params.acf_lag_seconds,
                       segment_sec=fd_params.segment_sec))
m = one["meta"]
st.caption(
    f"rx{rxs[0]} · 선택 스트림 {m['selected_stream_count']}개 · "
    f"crop {m['crop_len']} 샘플 (pad {m['pad_left']}+{m['pad_right']}) · "
    f"유효 최고주파 {m['effective_freq_max_hz']:.0f} Hz · s3 비영 비율 {m['s3_nonzero_ratio']:.3f}. "
    f"ACF 의 lag 축은 0~{fd_params.acf_lag_seconds}s 이고 값은 x(t)·x(t−τ) 의 부호 있는 곱이다."
)

# --- 브랜치 · arm 구성 -------------------------------------------------------

# 스칼라 브랜치는 P4/P5 의 probe 결과에서 가져온다 (동일 파일이면 캐시 적중).
scalar_rows = pd.DataFrame()
try:
    prec, _, _, _, _ = probe_batch_cached(paths, cache_dir, probe_kwargs())
    if not prec.empty:
        scalar_rows = prec
except Exception:  # noqa: BLE001 - 스칼라 브랜치는 선택 사항이다
    scalar_rows = pd.DataFrame()

if not scalar_rows.empty:
    keep = [c for c in set(Q_METRICS + PC_METRICS + FALLDEFI_OF_METRICS)
            if c in scalar_rows.columns]
    rows = rows.merge(scalar_rows[["filepath"] + keep], on="filepath", how="left")

q_cols = [c for c in (Q_METRICS + PC_METRICS) if c in rows.columns]
of_cols = [c for c in FALLDEFI_OF_METRICS if c in rows.columns]
sf_cols = [c for c in FALLDEFI_SF_METRICS if c in rows.columns]

branches = default_branches(
    pool_shape=POOL_CHOICES[pool_label], acf_pool_shape=(32, 16),
    q_columns=q_cols, fd_of_columns=of_cols, fd_sf_columns=sf_cols,
)

y = rows["is_fall"].to_numpy().astype(int)

arms = []
for e in sorted(rows["env"].unique()):
    arms += XD.make_matched_arms(
        rows, int(e), n_test_subjects=int(n_test), n_train_subjects=int(n_train),
        n_replicates=int(n_rep), seed=int(seed),
    )

if not arms:
    st.error("arm 을 만들 수 없습니다 — 환경당 피험자가 "
             f"{n_test + n_train}명 이상 필요합니다.")
    st.stop()

split_hash = XD.split_structure_hash(arms)
gcan = XD.group_canary(rows, arms)
ccan = XD.composition_canary(arms)


@st.cache_data(show_spinner=False)
def _evaluate(branch_name: str, _branch: BranchSpec, _rows: pd.DataFrame,
              _y: np.ndarray, _arms: list, pca: int, sd: int,
              boot: int, cache_token: str) -> dict:
    """브랜치 1개를 모든 arm 에 대해 평가한다. cache_token 이 폴드/표본 동일성을 키에 넣는다."""
    X = build_design_matrix(_branch, s3, acf, _rows)
    if X.shape[1] == 0:
        return {"scores": pd.DataFrame(), "per_subject": pd.DataFrame(), "dim": 0}
    bnd = block_boundary(_branch, s3, acf, _rows)

    def factory(n_train_rows: int, n_features: int):
        pipe = make_pipeline(_branch, seed=sd,
                             pca_dim=None if _branch.source == "s3+acf" else pca,
                             boundary=bnd)
        return clamp_pca_dim(pipe, n_train_rows, n_features)

    recs, per_sub = [], []
    for a in _arms:
        res = XD.run_arm(X, _y, _rows, a, factory, predict_unit="file")
        if res is None:
            continue
        row = XD.score_arm(res)
        if a.name in ("IN", "CROSS"):
            ci = XD.bootstrap_subject_ci(res, "auc", n_boot=boot, seed=sd)
            row.update({"ci_lo": ci["lo"], "ci_hi": ci["hi"]})
            ps = XD.per_subject_scores(res, "auc")
            ps["arm"] = a.name
            per_sub.append(ps)
        recs.append(row)
    return {"scores": pd.DataFrame(recs),
            "per_subject": pd.concat(per_sub, ignore_index=True) if per_sub else pd.DataFrame(),
            "dim": int(X.shape[1])}


token = f"{hash8}|{split_hash}|{len(rows)}|{pool_label}"
results = {}
bar = st.progress(0.0, text="브랜치 평가 중...")
for i, b in enumerate(branches):
    bar.progress(i / max(len(branches), 1), text=f"[{i+1}/{len(branches)}] {b.name}")
    results[b.name] = _evaluate(b.name, b, rows, y, arms, int(pca_dim), int(seed),
                                int(n_boot), token)
bar.empty()

n_eff_subjects = int(rows["subject"].nunique())
gap_rows, summary_rows = [], []
for b in branches:
    sc = results[b.name]["scores"]
    if sc.empty:
        continue
    g = XD.paired_gap(sc, "IN", "CROSS", "auc", n_eff_subjects=n_eff_subjects)
    in_auc = float(sc[sc["arm"] == "IN"]["auc"].mean())
    cross_auc = float(sc[sc["arm"] == "CROSS"]["auc"].mean())
    if g.get("gap_mean") is not None:
        gap_rows.append({"branch": b.name, **g})
    summary_rows.append({
        "branch": b.name, "kind": b.kind, "dim": results[b.name]["dim"],
        "in_auc": in_auc, "cross_auc": cross_auc,
        "gap_mean": g.get("gap_mean"), "mde80": g.get("mde80"),
        "underpowered": g.get("underpowered"),
    })

gaps = pd.DataFrame(gap_rows)
summary = pd.DataFrame(summary_rows)

# --- 7.4 헤드라인 -----------------------------------------------------------

st.subheader("7.4 헤드라인 — 통제된 도메인 격차")

focus_branch = st.radio("브랜치", [b.name for b in branches], horizontal=True)
sc = results[focus_branch]["scores"]

if sc.empty:
    st.info("이 브랜치는 평가할 수 없었습니다.")
else:
    by_env = sc.groupby(["arm", "target_env"], as_index=False).agg(
        auc=("auc", "mean"), ci_lo=("ci_lo", "mean"), ci_hi=("ci_hi", "mean"))
    chart(plot_arm_dotplot(by_env, "auc"))

    g = gaps[gaps["branch"] == focus_branch]
    if not g.empty:
        r = g.iloc[0]
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("IN (같은 방)", f"{summary.set_index('branch').loc[focus_branch, 'in_auc']:.3f}")
        k2.metric("CROSS (다른 방)", f"{summary.set_index('branch').loc[focus_branch, 'cross_auc']:.3f}")
        k3.metric("gap (IN − CROSS)", f"{r['gap_mean']:+.3f}",
                  delta=f"{r['n_pairs']}쌍", delta_color="off")
        k4.metric("MDE₈₀", f"{r['mde80']:.3f}")

        if bool(r["underpowered"]):
            st.warning(
                f"**검출력 부족** — 격차 {r['gap_mean']:+.3f} 가 최소검출가능효과 "
                f"{r['mde80']:.3f} 보다 작습니다. 이 결과로 '강건하다'고 말할 수 없습니다. "
                "표본(셀당 파일 수)이나 반복 수를 늘려야 합니다."
            )
        elif r["gap_mean"] > 0:
            st.error(
                f"**방이 바뀌면 성능이 떨어집니다** — 같은 방에서 학습했을 때보다 AUC 가 "
                f"평균 {r['gap_mean']:.3f} 낮습니다 (95% 구간 {r['gap_lo']:+.3f} ~ {r['gap_hi']:+.3f}, "
                f"{r['win_rate']:.0%} 의 반복에서 IN 이 우세). 학습셋 크기·활동 구성·테스트셋이 "
                "모두 동일하므로 이 차이는 **학습한 방**에 귀속됩니다."
            )
        else:
            st.info(
                f"격차가 음수({r['gap_mean']:+.3f})입니다 — 다른 방에서 학습한 쪽이 오히려 나았습니다. "
                "표본이 작을 때 흔히 나타나며, MDE 와 함께 읽어야 합니다."
            )

    loeo = sc[sc["arm"] == "LOEO_full"]["auc"]
    if len(loeo):
        st.caption(
            f"참고 — 통제 없는 **LOEO-full** AUC = {loeo.mean():.3f} "
            f"(다른 두 환경 전체 {rows['subject'].nunique() - n_test}명으로 학습). "
            "학습 피험자가 훨씬 많아 IN 보다도 높게 나오는 경우가 많고, 그대로 읽으면 "
            "'도메인 문제가 없다'로 오독됩니다."
        )

# --- 7.5 임계값 vs 피처 ------------------------------------------------------

st.subheader("7.5 임계값 문제인가, 피처 문제인가")

if not sc.empty:
    chart(plot_metric_triple(sc))
    ins = sc[sc["arm"] == "IN"]
    crs = sc[sc["arm"] == "CROSS"]
    if len(ins) and len(crs):
        d_auc = float(ins["auc"].mean() - crs["auc"].mean())
        d_ba = float(ins["ba_at_half"].mean() - crs["ba_at_half"].mean())
        d_or = float(ins["ba_at_oracle"].mean() - crs["ba_at_oracle"].mean())
        verdict = (
            "**캘리브레이션/사전확률 이동** — 순위(AUC)는 대체로 살아있는데 고정 임계 0.5 의 "
            "정확도만 무너졌습니다. 타깃 환경 라벨 몇 개로 임계를 다시 잡으면 상당 부분 회복됩니다."
            if (d_ba - d_auc) > 0.05 and d_or < d_ba else
            "**진짜 피처 이동** — AUC 자체가 떨어졌습니다. 임계 조정으로는 못 고칩니다."
        )
        st.caption(
            f"AUC 감소 {d_auc:+.3f} · BA@0.5 감소 {d_ba:+.3f} · BA@oracle 감소 {d_or:+.3f}. {verdict} "
            f"예측확률 평균은 IN {ins['mean_pred_prob'].mean():.3f} → CROSS {crs['mean_pred_prob'].mean():.3f} 로 이동."
        )

# --- 7.6 플라시보 · 순열 -----------------------------------------------------

st.subheader("7.6 플라시보 · 순열 참조")

perm_vals: list = []
if st.checkbox("라벨 순열 귀무분포 계산 (느림)", value=False,
               help="낙상 라벨을 파일 단위로, 피험자 내부에서만 섞습니다. "
                    "행 단위 셔플은 한 파일의 rx 행에 다른 라벨을 주는 불가능한 배치가 됩니다."):
    b = next(x for x in branches if x.name == focus_branch)
    X = build_design_matrix(b, s3, acf, rows)
    bnd = block_boundary(b, s3, acf, rows)
    n_perm = st.slider("순열 횟수", 5, 50, 10)
    pbar = st.progress(0.0, text="순열 중...")
    for i in range(n_perm):
        pbar.progress((i + 1) / n_perm)
        rng = np.random.default_rng(int(seed) * 977 + i)
        y_p = XD.permute_labels_within_subject(rows, y, rng)
        for a in [x for x in arms if x.name == "CROSS"][:6]:
            res = XD.run_arm(X, y_p, rows, a,
                             lambda n, d: clamp_pca_dim(
                                 make_pipeline(b, int(seed),
                                               None if b.source == "s3+acf" else int(pca_dim), bnd),
                                 n, d))
            if res is not None:
                perm_vals.append(XD.score_arm(res)["auc"])
    pbar.empty()

if not sc.empty:
    chart(plot_placebo_null(sc[sc["arm"] == "IN"]["auc"].tolist(),
                            sc[sc["arm"] == "CROSS"]["auc"].tolist(),
                            perm_vals or None))
    st.caption(
        "격차의 진짜 귀무가설은 '라벨이 무작위'가 아니라 **'학습한 사람들이 녹화된 방은 상관없다'** 이고, "
        "그 분포를 IN arm 이 공급합니다. CROSS 분포가 IN 밴드 안에 들어오면 측정 가능한 방 효과가 없습니다."
        + (f" 순열 귀무 평균 AUC = {np.mean(perm_vals):.3f} (0.5 근처여야 정상)." if perm_vals else "")
    )

    ps = results[focus_branch]["per_subject"]
    if not ps.empty:
        chart(plot_per_subject_strip(ps))
        st.caption("피험자별 점수. 한 사람이 결과를 끌고 가면 집계 평균보다 여기서 먼저 보입니다.")

# --- 7.7 피처군 비교 ---------------------------------------------------------

st.subheader("7.7 어느 피처군이 방 변화를 가장 잘 견디는가")

if summary.empty:
    st.info("비교할 브랜치가 없습니다.")
else:
    chart(plot_branch_scatter(summary))
    chart(plot_gap_bars(gaps))
    st.dataframe(
        summary.sort_values("cross_auc", ascending=False).round(4),
        use_container_width=True, hide_index=True,
    )
    st.caption(
        f"모든 브랜치가 **바이트 동일한 폴드**를 봤습니다 (split hash `{split_hash}`). "
        "차원이 3자릿수 차이 나므로 파이프라인 구조(센터링/표준화·PCA)만 브랜치 종류에 맞게 다르고 "
        "분류기·폴드·클래스 가중은 동일합니다. `underpowered=True` 행은 격차를 읽으면 안 됩니다."
    )

# --- 7.8 canary / 반증 -------------------------------------------------------

st.subheader("7.8 검증과 반증")

v1, v2 = st.columns(2)
with v1:
    st.markdown("**그룹 canary** — 학습·테스트가 어느 수준에서도 겹치지 않는가")
    (st.success if gcan["ok"] else st.error)(
        f"arm {gcan['n_arms']}개 · 피험자 겹침 {gcan['subject_overlap']} · "
        f"파일 겹침 {gcan['file_overlap']} · 세션 겹침 {gcan['session_overlap']}"
    )
    st.caption(
        "세션 겹침을 따로 보는 이유: `(env, subject, class_id, trial)` 을 공유하는 파일들은 "
        "하나의 물리 세션의 연속 phase 라 같은 정적 다중경로를 공유합니다. 그래서 CV 그룹을 "
        "파일이 아니라 **피험자**로 잡습니다."
    )
with v2:
    st.markdown("**구성 canary** — 활동 구성을 실제로 맞췄는가")
    (st.success if ccan["ok"] else st.warning)(
        f"부족한 셀 {ccan['cells_short']}개 · 최대 부족량 {ccan['worst_shortfall']}"
    )
    st.caption(
        "cross-domain 학습 풀에서 템플릿과 같은 `(activity, is_fall)` 구성을 못 채운 경우입니다. "
        "셀당 파일 수를 늘리면 줄어듭니다. 부족량이 크면 활동 격차가 방 격차에 섞입니다."
    )

if st.checkbox("반증: 저레이트/보간 과다 파일 제외하고 다시 보기", value=False,
               help="E1 의 일부 피험자는 비단조 타임스탬프가 많아 신호의 상당 부분이 선형 보간입니다."):
    thr = float(rows["interp_ratio"].quantile(0.9))
    keep_mask = rows["interp_ratio"] <= thr
    st.caption(f"`interp_ratio` 상위 10%({thr:.3f} 초과) 제외 → {int(keep_mask.sum())}/{len(rows)} 샘플 유지. "
               "이 조건에서 결론의 부호가 바뀌면 그건 방 효과가 아니라 수집 품질 차이입니다.")
    st.dataframe(rows[~keep_mask].groupby("env").size().rename("제외된 샘플").reset_index(),
                 use_container_width=True, hide_index=True)

# --- 7.9 결론 ---------------------------------------------------------------

st.subheader("7.9 결론")

if not summary.empty:
    ok = summary.dropna(subset=["gap_mean"])
    powered = ok[~ok["underpowered"].astype(bool)]
    best = ok.sort_values("cross_auc", ascending=False).iloc[0] if len(ok) else None

    bullets = []
    if best is not None:
        bullets.append(
            f"- 다른 방에서 가장 잘 버틴 피처군은 **{best['branch']}** "
            f"(CROSS AUC {best['cross_auc']:.3f}, gap {best['gap_mean']:+.3f})"
        )
    if len(powered):
        worst = powered.sort_values("gap_mean", ascending=False).iloc[0]
        bullets.append(
            f"- 검출력이 확보된 브랜치 {len(powered)}개 중 격차가 가장 큰 것은 "
            f"**{worst['branch']}** ({worst['gap_mean']:+.3f})"
        )
    bullets.append(
        f"- 브랜치 {len(ok)}개 중 **{int(ok['underpowered'].sum())}개가 검출력 부족** "
        f"(|gap| < MDE₈₀) — 이 브랜치들은 강건하다고도 아니라고도 말할 수 없습니다"
    )
    st.markdown("\n".join(bullets))

    in_floor = ok["in_auc"].max() if len(ok) else 0.0
    if in_floor < 0.6:
        st.error(
            f"**in-domain 바닥 게이트**: 같은 방 안에서조차 최고 AUC 가 {in_floor:.3f} 입니다. "
            "도메인 강건성을 논하기 전에 in-domain 낙상 분류부터 성립하지 않습니다 — "
            "표본을 늘리거나 표현을 바꿔야 합니다."
        )

st.error(
    "**해석 단서**: (1) 유병률이 인위적 50:50 이고 데이터셋 자연 비율은 2/12 ≈ 16.7% 입니다. "
    "(2) `crop_signal_around_activity` 는 4초 전체를 보고 활동 피크를 찾으므로 스트리밍 배포보다 "
    "낙관적입니다. (3) 환경과 피험자가 서로소라 '방 효과'는 끝내 '그 방 사람들의 효과'와 분리되지 않습니다."
)

with st.expander("arm 별 원자료"):
    all_scores = pd.concat(
        [results[b.name]["scores"].assign(branch=b.name)
         for b in branches if not results[b.name]["scores"].empty],
        ignore_index=True) if results else pd.DataFrame()
    st.dataframe(all_scores.round(4), use_container_width=True)
    if not all_scores.empty:
        st.download_button("CSV 다운로드", all_scores.to_csv(index=False).encode("utf-8-sig"),
                           file_name="cross_domain_fall.csv", mime="text/csv")
