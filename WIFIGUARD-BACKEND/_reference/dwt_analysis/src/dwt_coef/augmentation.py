"""Band-aware Subcarrier Grouping (대역 분할 앙상블 추출).

AmFall 의 데이터 증강 아이디어 - 하나의 CSI 관측치 H_raw 를 여러 개의 스트림
부분집합으로 쪼개어 각각을 독립적인 학습 샘플로 쓴다 - 를 자체 수집 데이터
(단일 안테나 x 243 서브캐리어) 에 맞게 구현한 것.

원 논문은 스트림 풀을 단순히 등분하지만, 단일 안테나 수집 데이터에서는 인접
서브캐리어 간 상관이 매우 높아 무작위 추출 시 한쪽 주파수 영역에 치우친 패치가
나올 수 있다. 이를 막기 위해 서브캐리어를 저/중/고 3개 대역으로 나눈 뒤 각
대역에서 균등하게 뽑아 하나의 패치를 구성한다.

    243개 유효 서브캐리어 -> 81 / 81 / 81 로 3등분
    각 대역에서 30개 무작위 추출 -> 패치당 90개 서브캐리어

주의: 패치는 서로 배타적(disjoint)이지 않다. 15개 패치 x 90개 = 1350 개로
243개 풀보다 훨씬 크기 때문에 필연적으로 중복된다. 각 패치 내부에서만 중복이
없으며, 패치들은 서로 다른 조합을 갖는다 (조합 수가 천문학적이라 실질적으로
동일 패치가 나올 확률은 0). 따라서 이 증강은 완전히 독립적인 샘플을 만드는
것이 아니라 상관된 뷰(view)를 늘리는 것이며, train/test 분할은 반드시 패치가
아니라 원본 레코딩 단위로 해야 데이터 누수가 없다.
"""

from typing import Any, Dict, List, Optional, Sequence

import numpy as np

BAND_NAMES = ("low", "mid", "high")

DEFAULT_N_PATCHES = 15
DEFAULT_N_BANDS = 3
DEFAULT_PER_BAND = 30


def split_bands(n_positions: int, n_bands: int = DEFAULT_N_BANDS) -> List[np.ndarray]:
    """서브캐리어 위치 0..n_positions-1 을 연속된 n_bands 개 대역으로 균등 분할한다.

    분할은 서브캐리어 인덱스 순서(= 주파수 순서) 기준이므로 저/중/고 대역이 된다.
    n_positions 가 n_bands 로 나누어떨어지지 않으면 앞쪽 대역이 하나씩 더 갖는다.

    Args:
        n_positions: 유효 서브캐리어 개수 (기본 데이터에서는 243)
        n_bands: 대역 수

    Returns:
        각 대역의 위치 인덱스 배열 리스트 (길이 n_bands)

    Raises:
        ValueError: n_bands 가 1 미만이거나 n_positions 보다 큰 경우
    """
    if n_bands < 1:
        raise ValueError(f"n_bands must be >= 1, got {n_bands}")
    if n_bands > n_positions:
        raise ValueError(f"n_bands ({n_bands}) cannot exceed n_positions ({n_positions})")
    return [b for b in np.array_split(np.arange(n_positions), n_bands)]


def sample_patch_positions(
    bands: Sequence[np.ndarray],
    per_band: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """각 대역에서 per_band 개씩 비복원 추출하여 하나의 패치 위치 집합을 만든다.

    Args:
        bands: split_bands() 결과
        per_band: 대역당 추출 개수
        rng: numpy Generator

    Returns:
        오름차순 정렬된 (n_bands * per_band,) int32 위치 배열

    Raises:
        ValueError: per_band 가 어떤 대역의 크기보다 큰 경우
    """
    smallest = min(len(b) for b in bands)
    if per_band > smallest:
        raise ValueError(f"per_band ({per_band}) exceeds smallest band size ({smallest})")
    picks = [rng.choice(band, size=per_band, replace=False) for band in bands]
    return np.sort(np.concatenate(picks)).astype(np.int32)


def band_ids_for(positions: np.ndarray, bands: Sequence[np.ndarray]) -> np.ndarray:
    """각 위치가 몇 번째 대역에 속하는지 반환한다.

    Args:
        positions: 위치 인덱스 배열
        bands: split_bands() 결과

    Returns:
        positions 와 같은 길이의 int8 대역 ID 배열 (0 = low, 1 = mid, ...)
    """
    boundaries = np.cumsum([len(b) for b in bands[:-1]])
    return np.searchsorted(boundaries, positions, side="right").astype(np.int8)


def generate_patches(
    amplitude: np.ndarray,
    usable_idx: Optional[np.ndarray] = None,
    n_patches: int = DEFAULT_N_PATCHES,
    n_bands: int = DEFAULT_N_BANDS,
    per_band: int = DEFAULT_PER_BAND,
    seed: Optional[int] = None,
    rng: Optional[np.random.Generator] = None,
) -> List[Dict[str, Any]]:
    """하나의 레코딩에서 대역 균등 추출 패치 n_patches 개를 만든다.

    진폭 슬라이싱 외의 연산(리샘플/필터/PCA)은 하지 않는다 - 논문이 강조한
    "단순 리쉐이핑" 수준의 증강이므로 전처리는 다운스트림에 맡긴다.

    Args:
        amplitude: (N, n_slots) 진폭 배열
        usable_idx: 사용할 슬롯 인덱스. None 이면 amplitude 의 모든 열 사용
        n_patches: 생성할 패치 수
        n_bands: 대역 수
        per_band: 대역당 서브캐리어 수
        seed: rng 가 None 일 때 사용할 시드
        rng: 미리 만든 Generator (seed 보다 우선)

    Returns:
        길이 n_patches 의 dict 리스트. 각 dict:
            patch_id       : int
            amplitude      : (N, n_bands * per_band) float32
            subcarrier_idx : (n_bands * per_band,) int32 - 원본 슬롯 인덱스
            band_id        : (n_bands * per_band,) int8

    Raises:
        ValueError: n_patches 가 1 미만이거나 대역/추출 수가 유효하지 않은 경우
    """
    if n_patches < 1:
        raise ValueError(f"n_patches must be >= 1, got {n_patches}")

    if usable_idx is None:
        usable_idx = np.arange(amplitude.shape[1], dtype=np.int32)
    usable_idx = np.asarray(usable_idx, dtype=np.int32)

    if rng is None:
        rng = np.random.default_rng(seed)

    bands = split_bands(len(usable_idx), n_bands=n_bands)

    patches: List[Dict[str, Any]] = []
    for patch_id in range(n_patches):
        positions = sample_patch_positions(bands, per_band, rng)
        slots = usable_idx[positions]
        patches.append(
            {
                "patch_id": patch_id,
                "amplitude": np.ascontiguousarray(amplitude[:, slots], dtype=np.float32),
                "subcarrier_idx": slots,
                "band_id": band_ids_for(positions, bands),
            }
        )
    return patches


def make_file_rng(seed: int, file_ordinal: int) -> np.random.Generator:
    """파일별로 독립적이면서 재현 가능한 Generator 를 만든다.

    (seed, file_ordinal) 엔트로피 쌍을 쓰기 때문에 처리 순서나 --max-files 값이
    바뀌어도 같은 파일은 항상 같은 패치를 얻는다.

    Args:
        seed: 전역 시드
        file_ordinal: 파일 고유 정수 (파일 인덱스 상의 안정적인 순번)

    Returns:
        np.random.Generator
    """
    return np.random.default_rng([seed, file_ordinal])
