"""
앱 전역 상수. 색상 토큰은 scripts/07_agc_distribution.py 와 동일하게 유지해
matplotlib 산출물과 이 앱의 plotly 산출물이 같은 팔레트를 쓰도록 한다.
"""

from typing import Dict, List, Tuple

from src.dwt_coef.falldefi_features import OF_KEYS as _FD_OF, SF_KEYS as _FD_SF

# --- 그룹 라벨 / 색상 -------------------------------------------------------

ENV_IDS: List[int] = [1, 2, 3]
ENV_LABELS: Dict[int, str] = {1: "E1", 2: "E2", 3: "E3"}
ENV_COLORS: Dict[int, str] = {1: "#2a78d6", 2: "#1baf7a", 3: "#eda100"}

FALL_LABELS: Dict[bool, str] = {True: "Fall", False: "Non-fall"}
FALL_COLORS: Dict[str, str] = {"Fall": "#d62728", "Non-fall": "#7f7f7f"}

ANTENNA_LABELS: Dict[int, str] = {0: "Ant1", 1: "Ant2", 2: "Ant3"}

GROUP_PALETTE: List[str] = [
    "#2a78d6", "#1baf7a", "#eda100", "#d62728", "#9467bd",
    "#8c564b", "#e377c2", "#7f7f7f", "#17becf", "#2ca02c",
    "#ff7f0e", "#1f77b4",
]


def group_color(index: int) -> str:
    return GROUP_PALETTE[index % len(GROUP_PALETTE)]


def env_color(env: int) -> str:
    return ENV_COLORS.get(int(env), "#7f7f7f")


# --- raw CSV -----------------------------------------------------------------

#: 값싼 메타데이터 pass 에서 읽는 컬럼. 90개 complex CSI 문자열 컬럼을 건너뛰는 것이
#: 12~25 ms/파일 을 만드는 핵심이다 (전체 파싱은 ~0.2~0.5 s/파일).
META_COLUMNS: List[str] = [
    "timestamp_low", "rssi_a", "rssi_b", "rssi_c", "noise", "agc", "rate", "Nrx", "Ntx",
]

#: Intel 5300 RSSI -> dBm 변환 상수 (Linux 802.11n CSI Tool 의 get_total_rss).
#:     total_rss_dbm = 10*log10(sum(10^(rssi_i/10))) - RSSI_OFFSET_DB - agc
RSSI_OFFSET_DB: float = 44.0

#: 데이터셋 전체에서 noise 는 -127 고정(파일당 unique 1개)이다. 컬럼을 조용히
#: 버리지 않고 noise_n_unique 로 화면에서 증명한 뒤 분석에서 제외한다.
DEAD_NOISE_VALUE: int = -127

NATIVE_FS_HZ: float = 320.0
NOMINAL_GAP_MS: float = 1000.0 / NATIVE_FS_HZ  # 3.125 ms
NOMINAL_DURATION_SEC: float = 4.0

FALL_ACTIVITIES: Tuple[int, ...] = (2, 5)

# --- 파이프라인 단계 ---------------------------------------------------------

STAGE_ORDER: List[str] = [
    "raw_amp", "resampled", "filtered",
    "stream_top1", "stream_selected_mean", "pc_max", "final",
]

STAGE_LABELS: Dict[str, str] = {
    "raw_amp": "S0 raw 진폭",
    "resampled": "S1 리샘플 320Hz",
    "filtered": "S2 대역통과",
    "stream_top1": "S3 스트림 top-1",
    "stream_selected_mean": "S3 선택 평균",
    "pc_max": "S4 PC max",
    "final": "S5 대표신호",
}

#: (단계, 함수, 지배 파라미터, 측정 대상, 무엇이 버려지는가) — P4 §4.0 파이프라인 지도
PIPELINE_MAP: List[Tuple[str, str, str, str, str]] = [
    ("S0 raw 진폭", "extract_amplitude", "—",
     "q over 90 streams", "복소 위상 전체"),
    ("S1 리샘플", "resample_signal", "fs_hz, tolerance_ms, max_interp_gap_steps",
     "q, interp/fallback/nonpositive 비율", "gap 값 자체, 리샘플 시간축"),
    ("S1b 고정길이", "preprocess_segment 내부", "window_sec",
     "truncated / padded 여부", "잘려나간 구간"),
    ("S2 대역통과", "bandpass_filter", "low_hz, high_hz, order",
     "q, 에너지비", "sos 계수"),
    ("S3 스트림 선택", "select_streams", "omega, n_streams, use_ant",
     "q_all, Q_top, 임계, 선택 origin", "**비선택 후보의 q 와 origin**"),
    ("S4 PC 선택", "select_pcs", "omega, eigenvalue_threshold, max_pcs",
     "고유값, q(p), Q(p), 임계", "**고유값·q(p)·Q(p)·임계 전부**"),
    ("S5 대표신호", "sum + z-score", "—",
     "final q, kurtosis", "정규화 전 스케일(mean/std)"),
]

# --- 지표 블록 (P5 발산 행렬의 행 그룹) --------------------------------------

ACQUISITION_METRICS: List[str] = [
    "median_gap_ms", "gap_cv", "frac_nonpositive", "frac_gap_gt_2x",
    "agc_mean", "agc_std", "rss_dbm_mean", "rssi_spread",
]

TIMING_METRICS: List[str] = [
    "interp_ratio", "fallback_ratio", "nonpositive_ratio", "length_ratio",
]

AMPLITUDE_METRICS: List[str] = [
    "amp_mean", "amp_std", "amp_cv", "amp_ant_ratio_21", "amp_sub_std_mean",
]

Q_METRICS: List[str] = [
    "q_raw_max", "q_filt_max", "q_filt_mean", "q_gain_max",
    "q_top1", "q_top5_mean", "q_entropy_norm", "q_gini",
    "selected_stream_count", "final_q",
]

PC_METRICS: List[str] = [
    "pc_candidate_count", "pc_selected_count", "eig_top1_ratio",
    "eig_effective_rank", "q_pc_max", "q_pc_over_q_stream",
]

#: FallDeFi (Palipana et al. 2017) Table 1. 목록은 원 모듈에서 가져와 중복 정의를 피한다.
FALLDEFI_OF_METRICS: List[str] = list(_FD_OF)
#: 저자들이 "환경 변화에 강건" 하다고 보고한 부분집합 — P6 이 그 주장을 검증한다.
FALLDEFI_SF_METRICS: List[str] = list(_FD_SF)
#: OF 중 SF 에 들지 않은 것 = 저자들이 환경 변화에 약하다고 본 피처
FALLDEFI_OF_ONLY_METRICS: List[str] = [m for m in _FD_OF if m not in _FD_SF]

METRIC_BLOCKS: Dict[str, List[str]] = {
    "수집(acquisition)": ACQUISITION_METRICS,
    "타이밍(timing)": TIMING_METRICS,
    "진폭(amplitude)": AMPLITUDE_METRICS,
    "q-value": Q_METRICS,
    "PCA": PC_METRICS,
    "FallDeFi OF": FALLDEFI_OF_METRICS,
}

#: 사람이 읽을 수 있는 지표 설명 (표/툴팁용)
METRIC_DESCRIPTIONS: Dict[str, str] = {
    "median_gap_ms": "패킷 간격 중앙값 (정상 3.125 ms)",
    "gap_cv": "패킷 간격 변동계수",
    "frac_nonpositive": "비단조(≤0) 타임스탬프 gap 비율",
    "frac_gap_gt_2x": "정상 간격의 2배를 넘는 gap 비율",
    "agc_mean": "AGC 평균 (클수록 수신 약함)",
    "agc_std": "AGC 표준편차",
    "rss_dbm_mean": "실제 수신전력 dBm = pow2db(Σ10^(rssi/10)) − 44 − agc",
    "rssi_spread": "안테나 3개 RSSI 평균의 최대−최소",
    "interp_ratio": "리샘플러가 만들어낸 보간 샘플 비율",
    "fallback_ratio": "리샘플러 폴백(보간 포기) 비율",
    "nonpositive_ratio": "비단조 gap 비율 (리샘플 기준)",
    "length_ratio": "리샘플 후/전 샘플 수 비",
    "amp_mean": "CSI 진폭 평균",
    "amp_std": "CSI 진폭 표준편차",
    "amp_cv": "CSI 진폭 변동계수",
    "amp_ant_ratio_21": "안테나2/안테나1 평균 진폭 비",
    "amp_sub_std_mean": "서브캐리어 간 진폭 산포",
    "q_raw_max": "리샘플 직후(필터 전) q 최대",
    "q_filt_max": "대역통과 후 q 최대 (90 스트림 전체)",
    "q_filt_mean": "대역통과 후 q 평균",
    "q_gain_max": "q_filt_max / q_raw_max — 필터가 q 를 얼마나 올렸나",
    "q_top1": "후보 풀 내 q 최대 (use_ant 제한 반영)",
    "q_top5_mean": "상위 5개 q 평균",
    "q_entropy_norm": "Q_top 분포의 정규화 섀넌 엔트로피 (landscape 평탄도)",
    "q_gini": "Q_top 분포의 지니계수 (landscape 집중도)",
    "selected_stream_count": "Q-임계를 통과한 스트림 수",
    "final_q": "최종 z-score 대표신호의 q",
    "pc_candidate_count": "고유값 임계를 통과한 후보 PC 수",
    "pc_selected_count": "최종 선택된 PC 수",
    "eig_top1_ratio": "최대 고유값의 분산 설명 비율",
    "eig_effective_rank": "고유값 분포의 유효 랭크 (exp of entropy)",
    "q_pc_max": "후보 PC 중 q 최대",
    "q_pc_over_q_stream": "q_pc_max / q_filt_max — PCA 가 활동신호를 살렸나 죽였나",
    # --- FallDeFi Table 1 ---
    "fd_ef_mean": "극단 주파수 곡선 평균 (Hz)",
    "fd_ef_std": "극단 주파수 곡선 표준편차",
    "fd_ef_max": "극단 주파수 최대 (Hz)",
    "fd_tf_mean": "몸통 주파수 곡선 평균 (Hz, percentile 법)",
    "fd_tf_std": "몸통 주파수 곡선 표준편차",
    "fd_tf_max": "몸통 주파수 최대 (Hz)",
    "fd_ef_tf_max_ratio": "max 극단 / max 몸통 주파수 비",
    "fd_entropy_1_10": "스펙트럼 엔트로피 1-10 Hz [SF]",
    "fd_entropy_10_30": "스펙트럼 엔트로피 10-30 Hz [SF]",
    "fd_entropy_30_max": "스펙트럼 엔트로피 30 Hz-나이퀴스트",
    "fd_fractal_dim": "극단 주파수 곡선의 프랙탈 차원 (Higuchi) [SF]",
    "fd_event_duration": "PBC 로 검출한 이벤트 지속시간 (s) [SF]",
    "fd_pbc_energy_ratio": "PBC 임계 위/아래 에너지 비",
}

# --- 교차 도메인 분류 (P7) ---------------------------------------------------

#: arm 표시 순서와 색. IN 은 baseline 이 아니라 **플라시보**(같은 방, 다른 사람)다.
ARM_LABELS: Dict[str, str] = {
    "IN": "IN (같은 방, 새 사람)",
    "CROSS": "CROSS (다른 방, 새 사람)",
    "CROSS_E1": "CROSS←E1", "CROSS_E2": "CROSS←E2", "CROSS_E3": "CROSS←E3",
    "LOEO_full": "LOEO-full (통제 없음)",
}
ARM_COLORS: Dict[str, str] = {
    "IN": "#2a78d6", "CROSS": "#d62728",
    "CROSS_E1": "#9ecae1", "CROSS_E2": "#9ecae1", "CROSS_E3": "#9ecae1",
    "LOEO_full": "#898781",
}

#: 낙상 / 비낙상 활동 (표본 층화를 (env, activity) 로 하기 위해 분리해 둔다)
FALL_ACTIVITY_IDS: Tuple[int, ...] = (2, 5)
NONFALL_ACTIVITY_IDS: Tuple[int, ...] = (1, 3, 4, 6, 7, 8, 9, 10, 11, 12)

#: 비낙상 중 낙상과 혼동되기 쉬운 활동 — 여기서 교차 도메인 성능이 무너지면 구체적 발견이다
HARD_NEGATIVE_IDS: Tuple[int, ...] = (3, 11)   # 눕기, 앉기

#: CWT/ACF 추출 비용 (실측 0.28 s/(파일,rx))
COST_CWTACF_SEC_PER_SAMPLE: float = 0.28
#: 샘플당 저장 (float16, S3 224x224 + ACF 128x64)
CWTACF_KB_PER_SAMPLE: float = 114.0

# --- 경로 -------------------------------------------------------------------

AGC_MANIFEST_REL = "results/analysis/agc_distribution/agc_manifest.csv"
AGC_SUMMARY_REL = "results/analysis/agc_distribution/agc_summary_stats.csv"
PREPROC_MANIFEST_REL = "data/preprocessed/manifest.csv"
OUTPUT_DIR_REL = "results/analysis/domain_shift"
CACHE_DIR_REL = "results/analysis/domain_shift/cache"

# --- 비용 모델 (실측값) ------------------------------------------------------

#: 실측 프로파일: probe_file 비용의 대부분은 90개 complex 문자열 컬럼 파싱(load_csi_file)이고
#: q landscape 3개는 합쳐 ~11%, FallDeFi STFT+피처는 ~12 ms 다. 즉 계산을 아껴도 의미가 없다.
COST_META_SEC_PER_FILE: float = 0.025
COST_PROBE_SEC_PER_FILE: float = 0.60

# --- 효과크기 해석 -----------------------------------------------------------

#: Cliff's delta 관례적 구간 (Romano et al. 2006)
CLIFF_BANDS: List[Tuple[float, str]] = [
    (0.147, "negligible"),
    (0.330, "small"),
    (0.474, "medium"),
    (1.001, "large"),
]


def cliff_magnitude(delta: float) -> str:
    a = abs(float(delta))
    for bound, name in CLIFF_BANDS:
        if a < bound:
            return name
    return "large"
