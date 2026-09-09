"""
FallDeFi (Palipana et al., IMWUT 2017) 의 스펙트로그램 기반 피처.

논문 Table 1 의 두 묶음을 그대로 구현한다:

- **Original Features (OF)** — 같은 환경에서 학습/시험할 때 정확도가 가장 높았던 피처 전체:
  extreme frequency (mean/std/max), torso frequency (mean/std/max),
  max extreme / max torso 비, spectral entropy (1-10 / 10-30 / 30-max Hz),
  fractal dimension, event duration, PBC 임계 위/아래 에너지 비
- **Selected Features (SF)** — 저자들이 **환경이 바뀌어도 강건하다**고 보고한 부분집합:
  event duration, spectral entropy (1-10 & 10-30 Hz), fractal dimension

SF 가 정말 환경에 강건한지는 이 모듈이 판단하지 않는다. 피처만 계산하고,
환경 격차 vs 활동 격차 비교는 Streamlit 앱(P6)이 데이터로 검증한다.

## Mendeley 데이터셋에 맞춘 불가피한 이탈 (전부 UI 에 표기된다)

1. **표본화율**: 논문은 1000 pkts/s, 5 GHz(나이퀴스트 500 Hz). Mendeley 는 320 Hz
   (나이퀴스트 **160 Hz**). STFT 창은 주파수 분해능 2 Hz 를 맞추도록 재계산한다
   (논문 512@1000Hz ≈ 2 Hz → 여기서는 160@320Hz = 2 Hz).
2. **잡음 추정 대역**: 논문은 ">250 Hz 는 이벤트가 없는 순수 잡음"으로 보고 거기서
   임계를 추정한다. 320 Hz 표본화에서는 스펙트럼 전체가 160 Hz 이하라 그런 대역이
   **존재하지 않는다**. 여기서는 비례 대응하는 상위 절반(> fs/4 = 80 Hz)을 쓰되,
   그 대역에도 실제 이벤트 에너지가 섞일 수 있음을 명시한다 — 임계가 과대추정되어
   약한 이벤트가 잘릴 수 있다.
3. **잡음 제거**: 논문은 wavelet denoising 후 PCA. 이 레포의 amfall 경로는 0.5-80 Hz
   대역통과인데, 그것을 쓰면 80 Hz 위가 비어 `spec_entropy_30_max` 와 잡음 추정 대역이
   모두 무의미해진다. 따라서 FallDeFi 피처는 **대역통과 이전(리샘플 직후)** 신호에서
   계산한다.
4. **PC 선택**: 논문은 분산 95% 를 담는 PC 들에 STFT 를 걸어 스펙트로그램을 평균한다.
   기본값(`pc_mode="variance95"`)은 그대로 따르고, amfall 이 q 로 고른 PC 를 쓰는
   `pc_mode="amfall_q"` 도 둬서 두 선택 규칙을 비교할 수 있게 했다.

5. **DC 근처 제외**: extreme/torso 주파수 곡선은 `min_freq_hz`(기본 1 Hz) 미만을 뺀다.
   CSI 진폭에는 사람 움직임과 무관한 큰 저주파 드리프트가 있어, 넣으면 두 곡선이
   전 파일에서 0~2 Hz 로 눌린다(실측 확인).
6. **fractal dimension 은 Higuchi 추정기**. 4초 파일의 스펙트로그램은 프레임이 20~30개뿐이라
   box-counting 이 점 개수에서 포화되어 곡선 모양과 무관하게 1.0 이 나온다(실측 확인).

이 파일은 신규 모듈이며 dvc.yaml 의 어떤 스테이지 dep 에도 없다 (DVC 영향 없음).
"""

from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy import signal as sp_signal

#: 논문 Table 1 의 Original Features (여기서 계산하는 키 이름)
OF_KEYS: Tuple[str, ...] = (
    "fd_ef_mean", "fd_ef_std", "fd_ef_max",
    "fd_tf_mean", "fd_tf_std", "fd_tf_max",
    "fd_ef_tf_max_ratio",
    "fd_entropy_1_10", "fd_entropy_10_30", "fd_entropy_30_max",
    "fd_fractal_dim",
    "fd_event_duration", "fd_pbc_energy_ratio",
)

#: 논문 Table 1 의 Selected Features — "환경 변화에 강건" 하다고 보고된 부분집합
SF_KEYS: Tuple[str, ...] = (
    "fd_event_duration", "fd_entropy_1_10", "fd_entropy_10_30", "fd_fractal_dim",
)

#: OF 중 SF 에 들지 않은 것 = 저자들이 환경 변화에 약하다고 본 피처
OF_ONLY_KEYS: Tuple[str, ...] = tuple(k for k in OF_KEYS if k not in SF_KEYS)

#: PBC 대역 (논문 §5.3: 몸통 반사가 집중되는 5-25 Hz)
PBC_LOW_HZ: float = 5.0
PBC_HIGH_HZ: float = 25.0

#: spectral entropy 대역 (논문 §6.1 (iv))
ENTROPY_BANDS: Tuple[Tuple[str, float, Optional[float]], ...] = (
    ("1_10", 1.0, 10.0),
    ("10_30", 10.0, 30.0),
    ("30_max", 30.0, None),
)

PC_MODES: Tuple[str, ...] = ("variance95", "amfall_q")


def stft_spectrogram(
    x: np.ndarray,
    fs_hz: float,
    freq_res_hz: float = 2.0,
    overlap_ratio: float = 0.90,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    단일 신호의 STFT 진폭 스펙트로그램.

    창 길이는 논문의 주파수 분해능(≈2 Hz)을 맞추도록 표본화율에서 역산한다
    (nperseg = fs / freq_res). 논문은 50% 겹침(해밍 최적)을 썼지만 Mendeley 파일은
    4초뿐이라 프레임 수가 부족해지므로 기본 90% 로 올린다 — 주파수 분해능은 그대로이고
    시간 분해능만 0.05 s 로 좋아진다. 겹침이 낮으면 검출된 낙상 구간이 10프레임 미만이 되어
    fractal dimension 적합이 불안정해진다(실측 확인).

    Returns:
        (freqs (F,), times (T,), magnitude (F, T))
    """
    x = np.asarray(x, dtype=np.float64)
    nperseg = max(16, int(round(fs_hz / max(freq_res_hz, 1e-6))))
    if x.size:
        nperseg = min(nperseg, x.size)
    noverlap = int(round(nperseg * float(np.clip(overlap_ratio, 0.0, 0.95))))

    freqs, times, Z = sp_signal.stft(
        x, fs=fs_hz, window="hamming", nperseg=nperseg, noverlap=noverlap,
        boundary=None, padded=False,
    )
    return freqs, times, np.abs(Z)


def averaged_spectrogram(
    pcs: np.ndarray,
    fs_hz: float,
    freq_res_hz: float = 2.0,
    overlap_ratio: float = 0.90,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    논문 §5.1: 선택된 주성분 각각에 STFT 를 건 뒤 스펙트로그램을 **평균**한다.
    PC 들은 서로 직교해 각기 다른 주파수 성분을 담으므로 평균하면 정보량이 늘어난다.
    """
    pcs = np.asarray(pcs)
    if pcs.ndim == 1:
        pcs = pcs[:, None]

    acc = None
    freqs = times = None
    for i in range(pcs.shape[1]):
        f, t, mag = stft_spectrogram(pcs[:, i], fs_hz, freq_res_hz, overlap_ratio)
        acc = mag if acc is None else acc + mag
        freqs, times = f, t
    return freqs, times, acc / max(pcs.shape[1], 1)


def noise_threshold(
    freqs: np.ndarray,
    mag: np.ndarray,
    fs_hz: float,
    k: float = 3.0,
    noise_band_lo_hz: Optional[float] = None,
) -> Tuple[float, float]:
    """
    논문 식 (3): N_th = mu + k*sigma — 고주파(순수 잡음) 대역의 가우시안 근사에서 추정.

    논문은 >250 Hz 를 썼지만 320 Hz 표본화에서는 나이퀴스트가 160 Hz 라 그 대역이 없다.
    비례 대응하는 상위 절반(> fs/4)을 기본값으로 쓴다.

    Returns:
        (N_th, 실제 사용한 하한 주파수)
    """
    lo = float(fs_hz / 4.0) if noise_band_lo_hz is None else float(noise_band_lo_hz)
    band = mag[freqs >= lo, :]
    if band.size == 0:  # 대역이 비면 상위 25% 빈으로 후퇴
        n = max(1, mag.shape[0] // 4)
        band = mag[-n:, :]
        lo = float(freqs[-n]) if freqs.size else lo
    return float(np.mean(band) + k * np.std(band)), lo


def extreme_frequency_curve(freqs: np.ndarray, mag: np.ndarray, thr: float,
                            min_freq_hz: float = 1.0) -> np.ndarray:
    """
    프레임별 '임계를 넘는 가장 높은 주파수'. 논문 §6.1 (i).

    `min_freq_hz` 미만(DC 근처)은 제외한다 — CSI 진폭에는 사람 움직임과 무관한
    거대한 저주파 드리프트가 있어서, 포함하면 곡선이 DC 에 눌려버린다.
    """
    sel = freqs >= min_freq_hz
    f_sel, m_sel = freqs[sel], mag[sel, :]
    out = np.zeros(mag.shape[1], dtype=np.float64)
    if f_sel.size == 0:
        return out
    above = m_sel >= thr
    for t in range(m_sel.shape[1]):
        idx = np.flatnonzero(above[:, t])
        out[t] = float(f_sel[idx[-1]]) if idx.size else 0.0
    return out


def torso_frequency_curve(freqs: np.ndarray, mag: np.ndarray,
                          percentile: float = 50.0,
                          min_freq_hz: float = 1.0) -> np.ndarray:
    """
    프레임별 누적 에너지가 `percentile`% 에 도달하는 주파수 — 논문 §6.1 (ii) 의 percentile 법.
    잡음과 사지 움직임의 영향을 가장 적게 받는 몸통 속도 추정치로 쓰인다.

    extreme 곡선과 같은 이유로 `min_freq_hz` 미만은 제외한다. 이걸 빼먹으면
    중앙 주파수가 저주파 드리프트에 눌려 전 파일이 0~2 Hz 로 붙어버린다(실측 확인).
    """
    p = float(np.clip(percentile, 0.0, 100.0)) / 100.0
    sel = freqs >= min_freq_hz
    f_sel, m_sel = freqs[sel], mag[sel, :]
    out = np.zeros(mag.shape[1], dtype=np.float64)
    if f_sel.size == 0:
        return out

    power = m_sel ** 2
    total = power.sum(axis=0)
    for t in range(m_sel.shape[1]):
        if total[t] <= 0:
            continue
        c = np.cumsum(power[:, t]) / total[t]
        out[t] = float(f_sel[min(int(np.searchsorted(c, p)), f_sel.size - 1)])
    return out


def band_spectral_entropy(freqs: np.ndarray, mag: np.ndarray,
                          lo_hz: float, hi_hz: Optional[float]) -> float:
    """
    논문 §6.1 (iv): 정규화 PSD 의 섀넌 엔트로피 H = -sum(p ln p), 지정 대역에서.
    프레임별로 계산한 뒤 평균한다 (프레임 축 집계 방식은 논문에 명시되어 있지 않다).
    낙상은 진폭 요동이 커서 엔트로피가 높다.
    """
    if freqs.size == 0:
        return float("nan")
    hi = float(hi_hz) if hi_hz is not None else float(freqs[-1])
    sel = (freqs >= lo_hz) & (freqs <= hi)
    if sel.sum() < 2:
        return float("nan")

    power = mag[sel, :] ** 2
    total = power.sum(axis=0)
    valid = total > 0
    if not np.any(valid):
        return float("nan")

    p = power[:, valid] / total[valid]
    with np.errstate(divide="ignore", invalid="ignore"):
        h = -np.sum(np.where(p > 0, p * np.log(p), 0.0), axis=0)
    return float(np.mean(h))


def fractal_dimension(curve: np.ndarray, kmax: Optional[int] = None) -> float:
    """
    극단 주파수 곡선의 거칠기 — 논문 §6.1 (v) 의 Hausdorff 차원.

    **Higuchi 추정기로 구현한다.** 교과서적인 box-counting 은 여기서 쓸 수 없다:
    Mendeley 4초 파일의 스펙트로그램은 프레임이 20~30개뿐이라, 상자를 잘게 쪼개면
    모든 표본점이 각자 다른 상자를 차지해 N(eps) 가 점 개수에서 포화되고 기울기가
    곡선 모양과 무관하게 1.0 으로 붙어버린다(실측 확인). Higuchi 는 이산 표본
    시계열을 위해 설계된 추정기라 짧은 구간에서도 동작한다.

    반환값은 [1, 2] 로 자른다: 1 에 가까울수록 매끄럽고 2 에 가까울수록 불규칙하다.
    논문은 이 피처가 **정규화된 값이라 환경 변화에 강건**하다고 본다.
    """
    y = np.asarray(curve, dtype=np.float64)
    n = y.size
    if n < 6 or not np.isfinite(y).all():
        return float("nan")
    if float(np.ptp(y)) <= 0:
        return 1.0  # 완전한 평탄선

    # 검출된 이벤트 구간은 프레임이 10개 안팎일 수 있다. n//4 로 잡으면 적합점이
    # 2개뿐이라 기울기를 못 구하고 NaN 이 된다(실측 확인) — 최소 3점을 보장한다.
    if kmax is None:
        kmax = int(np.clip(n // 3, 3, 10))

    logL, logk = [], []
    for k in range(1, kmax + 1):
        lengths = []
        for m in range(k):
            idx = np.arange(1, (n - m - 1) // k + 1)
            if idx.size == 0:
                continue
            dist = np.abs(y[m + idx * k] - y[m + (idx - 1) * k]).sum()
            lengths.append(dist * (n - 1) / (idx.size * k) / k)
        if lengths:
            mean_len = float(np.mean(lengths))
            if mean_len > 0:
                logL.append(np.log(mean_len))
                logk.append(np.log(1.0 / k))

    if len(logL) < 3:
        return float("nan")
    slope = float(np.polyfit(logk, logL, 1)[0])
    return float(np.clip(slope, 1.0, 2.0))


def power_burst_curve(freqs: np.ndarray, mag: np.ndarray, n_th: float,
                      lo_hz: float = PBC_LOW_HZ,
                      hi_hz: float = PBC_HIGH_HZ) -> Tuple[np.ndarray, float]:
    """
    논문 식 (5)(6): PBC(n) = sum_{k=kl..ku} |S(n,k)|, 임계 PBCth = sum_{k=kl..ku} N_th.
    5-25 Hz 는 몸통처럼 큰 질량의 반사가 집중되는 대역이다.

    Returns:
        (PBC (T,), PBCth)
    """
    sel = (freqs >= lo_hz) & (freqs <= hi_hz)
    if sel.sum() == 0:
        return np.zeros(mag.shape[1]), 0.0
    return mag[sel, :].sum(axis=0), float(n_th * sel.sum())


def _longest_run(mask: np.ndarray) -> Tuple[int, int]:
    """마스크에서 가장 긴 연속 True 구간 (start, end_exclusive). 없으면 (0, 0)."""
    best = cur = best_end = 0
    for i, v in enumerate(mask):
        cur = cur + 1 if v else 0
        if cur > best:
            best, best_end = cur, i + 1
    return (best_end - best, best_end) if best else (0, 0)


def variance95_pcs(amplitude_2d: np.ndarray, var_ratio: float = 0.95,
                   max_pcs: int = 10) -> Tuple[np.ndarray, Dict[str, Any]]:
    """
    논문 §5.1 의 PC 선택: 전체 분산의 `var_ratio` 를 담는 최소 개수의 주성분.
    amfall 의 q 기반 PC 선택과는 다른 규칙이라, 두 규칙을 비교할 수 있게 분리해 둔다.

    Args:
        amplitude_2d: (N, n_stream) — 서브캐리어/스트림 전체

    Returns:
        (pcs (N, n_sel), stats)
    """
    x = np.asarray(amplitude_2d, dtype=np.float64)
    if x.ndim != 2 or x.shape[1] < 1:
        return x.reshape(len(x), -1).astype(np.float32), {
            "n_pcs": 0, "var_explained": float("nan"), "evr": np.zeros(0)}

    x = x - x.mean(axis=0, keepdims=True)
    cov = np.cov(x.T)
    if cov.ndim == 0:
        cov = cov.reshape(1, 1)
    eigvals, eigvecs = np.linalg.eigh(cov)

    order = np.argsort(-eigvals)
    eigvals, eigvecs = eigvals[order], eigvecs[:, order]
    evr = eigvals / (eigvals.sum() + 1e-12)

    n = int(np.searchsorted(np.cumsum(evr), var_ratio) + 1)
    n = int(np.clip(n, 1, min(max_pcs, eigvecs.shape[1])))

    return (x @ eigvecs[:, :n]).astype(np.float32), {
        "n_pcs": n, "var_explained": float(np.sum(evr[:n])), "evr": evr,
    }


def extract_falldefi_features(
    pcs: np.ndarray,
    fs_hz: float,
    freq_res_hz: float = 2.0,
    overlap_ratio: float = 0.90,
    noise_k: float = 3.0,
    noise_band_lo_hz: Optional[float] = None,
    torso_percentile: float = 50.0,
    min_freq_hz: float = 1.0,
    pbc_low_hz: float = PBC_LOW_HZ,
    pbc_high_hz: float = PBC_HIGH_HZ,
    return_arrays: bool = False,
) -> Dict[str, Any]:
    """
    주성분 묶음에서 FallDeFi Table 1 의 피처를 계산한다.

    Args:
        pcs: (N, n_pc) 주성분 시계열. 논문대로 각 PC 의 스펙트로그램을 평균해서 쓴다.
        return_arrays: True 면 스펙트로그램/곡선/PBC 배열도 함께 반환 (시각화용).

    Returns:
        fd_* 스칼라 피처 dict. return_arrays=True 면 "arrays" 키가 추가된다.
    """
    diag = {"fd_event_detected": False, "fd_n_frames": 0,
            "fd_noise_threshold": float("nan"), "fd_noise_band_lo_hz": float("nan"),
            "fd_pbc_threshold": float("nan"), "fd_n_pcs_used": 0}

    pcs = np.asarray(pcs) if pcs is not None else np.zeros((0, 0))
    if pcs.size == 0 or pcs.shape[0] < 16:
        out = {**{k: float("nan") for k in OF_KEYS}, **diag}
        if return_arrays:
            out["arrays"] = None
        return out

    freqs, times, mag = averaged_spectrogram(pcs, fs_hz, freq_res_hz, overlap_ratio)
    n_th, noise_lo = noise_threshold(freqs, mag, fs_hz, noise_k, noise_band_lo_hz)

    # 논문 식 (4): 임계 미만은 0 으로 (스펙트로그램 분할)
    mag_seg = np.where(mag >= n_th, mag, 0.0)

    pbc, pbc_th = power_burst_curve(freqs, mag, n_th, pbc_low_hz, pbc_high_hz)
    above = pbc > pbc_th
    start, end = _longest_run(above)
    detected = end > start

    dt = float(times[1] - times[0]) if times.size > 1 else 0.0
    event_duration = float((end - start) * dt) if detected else 0.0

    # 검출된 이벤트 구간으로 좁혀 스펙트럼 피처를 뽑는다 (논문의 추출 방식)
    sl = slice(start, end) if detected else slice(0, mag.shape[1])
    mag_ev = mag_seg[:, sl]
    mag_raw_ev = mag[:, sl]
    if mag_ev.shape[1] < 2:
        mag_ev, mag_raw_ev = mag_seg, mag

    # 곡선은 **분할된** 스펙트로그램에서 (극단/몸통 주파수는 임계가 정의의 일부),
    # 엔트로피는 **분할 전** 스펙트로그램에서 계산한다. 임계를 적용하면 대역 하나가
    # 통째로 0 이 되어 -sum(p ln p) 가 정의되지 않고 NaN 이 퍼진다(실측 확인).
    # 논문의 엔트로피는 "스펙트로그램 에너지 분포의 무작위성"이므로 분할 전이 맞다.
    ef = extreme_frequency_curve(freqs, mag_ev, n_th, min_freq_hz)
    tf = torso_frequency_curve(freqs, mag_ev, torso_percentile, min_freq_hz)

    # fractal dimension 은 표본이 너무 적으면 추정이 불가능하다. 이벤트 구간이
    # 6프레임 미만이면 전체 곡선으로 후퇴하고 그 사실을 기록한다.
    ef_full = extreme_frequency_curve(freqs, mag_seg, n_th, min_freq_hz)
    fd_curve, fd_on_full = (ef, False) if ef.size >= 6 else (ef_full, True)
    ef_max = float(np.max(ef)) if ef.size else 0.0
    tf_max = float(np.max(tf)) if tf.size else 0.0

    energy_above = float(pbc[above].sum() - pbc_th * above.sum()) if above.any() else 0.0
    energy_below = float(pbc[~above].sum()) if (~above).any() else 0.0

    feats: Dict[str, Any] = {
        "fd_ef_mean": float(np.mean(ef)) if ef.size else float("nan"),
        "fd_ef_std": float(np.std(ef)) if ef.size else float("nan"),
        "fd_ef_max": ef_max,
        "fd_tf_mean": float(np.mean(tf)) if tf.size else float("nan"),
        "fd_tf_std": float(np.std(tf)) if tf.size else float("nan"),
        "fd_tf_max": tf_max,
        "fd_ef_tf_max_ratio": float(ef_max / tf_max) if tf_max > 0 else float("nan"),
        "fd_fractal_dim": fractal_dimension(fd_curve),
        "fd_fractal_on_full_curve": bool(fd_on_full),
        "fd_event_duration": event_duration,
        "fd_pbc_energy_ratio": (float(energy_above / energy_below)
                                if energy_below > 0 else float("nan")),
        "fd_event_detected": bool(detected),
        "fd_n_frames": int(mag.shape[1]),
        "fd_noise_threshold": float(n_th),
        "fd_noise_band_lo_hz": float(noise_lo),
        "fd_pbc_threshold": float(pbc_th),
        "fd_n_pcs_used": int(pcs.shape[1]),
    }
    for name, lo, hi in ENTROPY_BANDS:
        feats[f"fd_entropy_{name}"] = band_spectral_entropy(freqs, mag_raw_ev, lo, hi)

    if return_arrays:
        feats["arrays"] = {
            "freqs": freqs, "times": times, "mag": mag, "mag_seg": mag_seg,
            "extreme_curve": ef, "torso_curve": tf,
            "pbc": pbc, "pbc_threshold": pbc_th,
            "event_start_idx": int(start), "event_end_idx": int(end),
            "event_start_sec": float(times[start]) if detected and times.size else 0.0,
            "event_end_sec": (float(times[min(end, times.size - 1)])
                              if detected and times.size else 0.0),
            "event_slice_start": int(sl.start), "event_slice_stop": int(sl.stop),
            "noise_threshold": n_th, "noise_band_lo_hz": noise_lo,
        }
    return feats
