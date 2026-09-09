"""
브랜치 정의 · 고정 축약 · 누수 없는 파이프라인. streamlit 을 import 하지 않는다.

## 고정 사상과 적합 사상의 분리 (이 모듈의 존재 이유)

- **고정 사상** (블록 평균 풀링, `log1p`, marginal) 은 데이터에서 추정하는 파라미터가
  없다. CV 밖에서 한 번 적용해도 폴드 간 정보가 새지 않는다 -> `build_design_matrix`.
- **적합 사상** (센터링/표준화, PCA, `C` 선택) 은 반드시 학습 폴드에서만 적합해야 한다
  -> `make_pipeline` 안에 넣고 폴드마다 `clone()` 한다.

기존 `scripts/streamlit_inhouse_app/pages/feature_separability.py:106-112` 의
`branch_encode` 는 전체 데이터로 StandardScaler+PCA 를 적합한 뒤 CV 를 돈다. 도메인
실험에서 그 누수는 **비대칭**이라 특히 위험하다 — 전역 PCA 가 타깃 환경 데이터를 보게 되고,
그건 cross-domain arm 이 갖지 못해야 할 정보다. **cross 만 부풀려 격차를 줄이고
"강건하다" 쪽으로 편향시킨다.** 그래서 그 패턴을 복제하지 않는다.

## 이미지 브랜치는 센터링만, 스칼라 브랜치는 표준화

S3/ACF 맵은 픽셀 단위 표준화를 하면 거의 상수인 잡음 바닥 픽셀이 크게 증폭된다.
반대로 스칼라 피처(amfall q, FallDeFi)는 단위가 제각각이라 표준화가 필요하다.
"""

from dataclasses import dataclass, replace
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator
from sklearn.decomposition import PCA
from sklearn.feature_selection import VarianceThreshold
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

#: `C` 는 브랜치마다 학습 폴드 내부 CV 로 고른다. 고정하면 그 C 가 함의하는 사전분포에
#: 우연히 맞는 브랜치가 유리해져 차원이 3자릿수 다른 브랜치 비교가 불공정해진다.
DEFAULT_C_GRID: Tuple[float, ...] = (1e-3, 1e-2, 1e-1, 1.0, 10.0)


@dataclass(frozen=True)
class BranchSpec:
    name: str
    kind: str            # "map" | "scalar"
    source: str          # "s3" | "acf" | "s3+acf" | "columns"
    reducer: str = "pool"   # "pool" | "freq_marginal" | "time_marginal" | "lag_marginal" | "raw"
    pool_shape: Tuple[int, int] = (14, 14)
    log1p_scale: float = 0.0          # 0 = 끔
    columns: Tuple[str, ...] = ()
    marginal_bins: int = 32
    fuse_pca_dim: int = 16            # "s3+acf" 결합 시 블록별 PCA 차원


def block_pool(x: np.ndarray, out_shape: Tuple[int, int]) -> np.ndarray:
    """
    (N,H,W) -> (N,h,w) 블록 평균. **고정 선형 사상**이라 CV 밖에서 적용해도 안전하다.
    H,W 가 h,w 로 나누어떨어지지 않으면 가장자리를 잘라낸다.
    """
    n, h, w = x.shape
    oh, ow = out_shape
    kh, kw = max(1, h // oh), max(1, w // ow)
    x = x[:, : oh * kh, : ow * kw]
    return x.reshape(n, oh, kh, ow, kw).mean(axis=(2, 4))


def _resample_1d(v: np.ndarray, out_bins: int) -> np.ndarray:
    """(N, L) -> (N, out_bins) 선형 보간. 고정 사상."""
    if v.shape[1] == out_bins:
        return v
    src = np.linspace(0.0, 1.0, v.shape[1])
    dst = np.linspace(0.0, 1.0, out_bins)
    return np.stack([np.interp(dst, src, row) for row in v])


def build_design_matrix(spec: BranchSpec, s3: np.ndarray, acf: np.ndarray,
                        rows: pd.DataFrame) -> np.ndarray:
    """
    고정 사상만 적용해 (n, d) 설계행렬을 만든다. 파라미터를 추정하는 단계는
    여기에 두지 않는다 — 전부 `make_pipeline` 안으로 간다.
    """
    if spec.kind == "scalar":
        cols = [c for c in spec.columns if c in rows.columns]
        if not cols:
            return np.zeros((len(rows), 0), dtype=np.float64)
        return rows[cols].to_numpy(dtype=np.float64)

    def _map(arr: np.ndarray, which: str) -> np.ndarray:
        a = arr.astype(np.float32)
        # log1p 는 **S3 에만** 적용한다. S3 는 [0,1] 의 heavy-tailed 맵이라 선형
        # 스케일에서는 가장 밝은 능선 하나가 선형모형을 지배하고, denoise 로 다수
        # 픽셀이 정확히 0 이라 log1p(0)=0 으로 희소성도 보존된다.
        # ACF 는 [-1,1] 부호 있는 값이라 clip(0) 을 걸면 음의 상관이 통째로 사라진다.
        if spec.log1p_scale > 0 and which == "s3":
            a = np.log1p(spec.log1p_scale * np.clip(a, 0.0, None))
        if spec.reducer == "pool":
            return block_pool(a, spec.pool_shape).reshape(len(a), -1)
        if spec.reducer == "freq_marginal":      # 시간 평균 -> 주파수 프로파일
            return _resample_1d(a.mean(axis=2), spec.marginal_bins)
        if spec.reducer == "time_marginal":      # 주파수 평균 -> 시간 프로파일
            return _resample_1d(a.mean(axis=1), spec.marginal_bins)
        if spec.reducer == "lag_marginal":       # 시간 평균 -> lag 프로파일
            return _resample_1d(a.mean(axis=2), spec.marginal_bins)
        if spec.reducer == "raw":
            return a.reshape(len(a), -1)
        raise ValueError(f"unknown reducer {spec.reducer!r}")

    if spec.source == "s3":
        return _map(s3, "s3").astype(np.float64)
    if spec.source == "acf":
        return _map(acf, "acf").astype(np.float64)
    if spec.source == "s3+acf":
        # 결합은 파이프라인 안에서 블록별 PCA 로 차원을 맞춘 뒤 붙인다.
        # 여기서는 두 블록을 이어붙이고 경계 위치만 기억시킨다.
        a = _map(s3, "s3").astype(np.float64)
        b = _map(acf, "acf").astype(np.float64)
        return np.concatenate([a, b], axis=1)
    raise ValueError(f"unknown source {spec.source!r}")


def block_boundary(spec: BranchSpec, s3: np.ndarray, acf: np.ndarray,
                   rows: pd.DataFrame) -> Optional[int]:
    """"s3+acf" 브랜치에서 두 블록의 경계 열 인덱스. 그 외에는 None."""
    if spec.source != "s3+acf":
        return None
    single = replace(spec, source="s3")
    return int(build_design_matrix(single, s3, acf, rows).shape[1])


class BlockPCA(BaseEstimator):
    """
    두 블록을 각각 PCA 로 같은 차원으로 줄인 뒤 이어붙인다.

    단순 concat 은 큰 블록(S3 196차)이 작은 블록(ACF 512차)과 L2 페널티를 불공정하게
    나눠 갖는다. 블록별 동일 차원 축약이 그 모호함을 없앤다. `fit` 은 학습 폴드에서만
    호출되므로 누수가 없다.
    """

    def __init__(self, boundary: int, n_components: int = 16, random_state: int = 0):
        self.boundary = boundary
        self.n_components = n_components
        self.random_state = random_state

    def fit(self, X, y=None):  # noqa: N803
        b = self.boundary
        n = X.shape[0]
        k_a = max(1, min(self.n_components, b, n - 1))
        k_b = max(1, min(self.n_components, X.shape[1] - b, n - 1))
        self.pca_a_ = PCA(n_components=k_a, svd_solver="full",
                          random_state=self.random_state).fit(X[:, :b])
        self.pca_b_ = PCA(n_components=k_b, svd_solver="full",
                          random_state=self.random_state).fit(X[:, b:])
        return self

    def transform(self, X):  # noqa: N803
        return np.concatenate(
            [self.pca_a_.transform(X[:, : self.boundary]),
             self.pca_b_.transform(X[:, self.boundary:])], axis=1)


def make_pipeline(spec: BranchSpec, seed: int = 0, pca_dim: Optional[int] = None,
                  boundary: Optional[int] = None) -> Pipeline:
    """
    적합이 필요한 모든 단계를 담은 파이프라인. 폴드마다 `clone()` 해서 쓴다.

    - map:    VarianceThreshold -> 센터링만 -> [PCA / BlockPCA] -> L2 로지스틱
    - scalar: 중앙값 대치 -> 표준화(단위가 제각각) -> [PCA] -> L2 로지스틱
    """
    steps: List[Tuple[str, Any]] = []

    if spec.kind == "scalar":
        steps.append(("impute", SimpleImputer(strategy="median")))
        steps.append(("var", VarianceThreshold(0.0)))
        steps.append(("scale", StandardScaler()))
    else:
        steps.append(("var", VarianceThreshold(0.0)))
        # 이미지: 센터링만. per-pixel 표준화는 거의 상수인 잡음 바닥 픽셀을 증폭한다.
        steps.append(("scale", StandardScaler(with_mean=True, with_std=False)))

    if spec.source == "s3+acf" and boundary is not None:
        steps.append(("blockpca", BlockPCA(boundary=boundary,
                                           n_components=spec.fuse_pca_dim,
                                           random_state=seed)))
    elif pca_dim is not None and pca_dim > 0:
        steps.append(("pca", PCA(n_components=pca_dim, svd_solver="full",
                                 random_state=seed)))

    steps.append(("clf", LogisticRegression(
        penalty="l2", C=1.0, max_iter=5000, class_weight="balanced",
        random_state=seed, solver="lbfgs",
    )))
    return Pipeline(steps)


def clamp_pca_dim(pipe: Pipeline, n_train: int, n_features: int) -> Pipeline:
    """PCA 성분 수를 학습 폴드 크기에 맞게 줄인다 (n_components <= min(n-1, d))."""
    for name, step in pipe.steps:
        if name == "pca" and isinstance(step, PCA) and step.n_components is not None:
            step.n_components = max(1, min(int(step.n_components), n_train - 1, n_features))
        elif name == "blockpca" and isinstance(step, BlockPCA):
            step.n_components = max(1, min(int(step.n_components), n_train - 1))
    return pipe


def assert_unfitted(pipe: Pipeline) -> bool:
    """
    `.fit` 직전 canary — 변환기에 적합 상태가 남아 있으면 `clone()` 을 빠뜨린 것이다.
    누수를 소리 없이 통과시키는 대표적인 실수라 명시적으로 잡는다.
    """
    for _, step in pipe.steps:
        for attr in ("mean_", "components_", "scale_", "variances_", "statistics_"):
            if hasattr(step, attr):
                return False
    return True


def default_branches(pool_shape: Tuple[int, int] = (14, 14),
                     acf_pool_shape: Tuple[int, int] = (32, 16),
                     q_columns: Sequence[str] = (),
                     fd_of_columns: Sequence[str] = (),
                     fd_sf_columns: Sequence[str] = ()) -> List[BranchSpec]:
    """
    기본 브랜치 묶음. 스칼라 브랜치(amfall q, FallDeFi)는 컬럼 목록을 주입받아
    **동일한 폴드·동일한 분류기**로 이미지 브랜치와 나란히 비교된다.
    """
    branches = [
        BranchSpec("CWT S3", "map", "s3", "pool", pool_shape, log1p_scale=100.0),
        BranchSpec("PCA-ACF", "map", "acf", "pool", acf_pool_shape),
        BranchSpec("S3+ACF", "map", "s3+acf", "pool", pool_shape, log1p_scale=100.0),
        BranchSpec("S3 freq-marginal", "map", "s3", "freq_marginal", log1p_scale=100.0),
        BranchSpec("S3 time-marginal", "map", "s3", "time_marginal", log1p_scale=100.0),
        BranchSpec("ACF lag-marginal", "map", "acf", "lag_marginal"),
    ]
    if q_columns:
        branches.append(BranchSpec("amfall q", "scalar", "columns", columns=tuple(q_columns)))
    if fd_of_columns:
        branches.append(BranchSpec("FallDeFi OF", "scalar", "columns", columns=tuple(fd_of_columns)))
    if fd_sf_columns:
        branches.append(BranchSpec("FallDeFi SF", "scalar", "columns", columns=tuple(fd_sf_columns)))
    return branches
