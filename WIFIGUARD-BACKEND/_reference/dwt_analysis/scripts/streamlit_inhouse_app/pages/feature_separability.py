"""Page 3 - "F 에 따라 정말 다른가"에 수치로 답한다.

evaluate_feature_pair_domain_robustness.py 의 branch_encode 를 그대로 차용한다:
피처를 평탄화 -> StandardScaler -> PCA -> 2D 산점도 + F 예측 분류기.

⚠️ 분할은 반드시 레코딩 단위(GroupKFold)로 한다. 같은 레코딩의 윈도우들은
시간축이 겹치고 서브캐리어도 공유해 강하게 상관돼 있어, 윈도우 단위로 섞으면
누수로 정확도가 부풀려진다.
"""

from typing import Dict, Tuple

import numpy as np
import pandas as pd
import streamlit as st
from sklearn.decomposition import PCA
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import balanced_accuracy_score, silhouette_score
from sklearn.model_selection import GroupKFold, StratifiedKFold
from sklearn.preprocessing import StandardScaler

from lib.cache import extract_batch, get_file_index_cached
from lib.plotting import plot_embedding
from lib.state_defaults import feature_kwargs

st.title("🧭 Feature Separability")
st.caption(
    "두 피처가 F 조건을 얼마나 구분하는지 정량적으로 본다. "
    "구분이 잘 될수록 F 가 피처에 큰 영향을 준다는 뜻이다."
)

raw_root = st.session_state["raw_root"]
file_index = get_file_index_cached(raw_root)

if file_index.empty:
    st.error(f"라벨 파일을 찾지 못했습니다: {raw_root}")
    st.stop()

with st.sidebar:
    st.header("Grouping")
    group_mode = st.radio(
        "F 그룹화 단위",
        options=["a", "ab"],
        index=["a", "ab"].index(st.session_state["fc_group_mode"]),
        format_func=lambda m: "F{a} — 3그룹" if m == "a" else "F{a}-{b} — 9셀",
    )
    persons = st.multiselect(
        "참여자",
        sorted(file_index["person"].unique().tolist()),
        default=list(st.session_state["fc_persons"]),
    )

    st.divider()
    st.header("Model")
    pca_dim = st.slider("PCA 차원", 2, 50, int(st.session_state["sep_pca_dim"]))
    seed = st.number_input("Random seed", value=int(st.session_state["sep_seed"]), step=1)
    max_windows = st.slider("파일당 윈도우 수", 1, 12, int(st.session_state["ft_max_windows"]))

    st.divider()
    n_files = len(file_index[file_index["person"].isin(persons)]) if persons else 0
    st.caption(f"{n_files}개 파일 × {max_windows}윈도우 (Page 2 와 캐시 공유)")
    run_clicked = st.button("계산 실행", type="primary", width="stretch")

st.session_state.update(
    {
        "fc_group_mode": group_mode,
        "fc_persons": tuple(persons),
        "sep_pca_dim": pca_dim,
        "sep_seed": int(seed),
        "ft_max_windows": max_windows,
    }
)

if run_clicked:
    st.session_state["sep_has_run"] = True
if not persons:
    st.warning("참여자를 최소 한 명 선택하세요.")
    st.stop()
if not st.session_state["sep_has_run"]:
    st.info("사이드바에서 조건을 고른 뒤 **계산 실행** 을 누르세요.")
    st.stop()

selected_files = file_index[file_index["person"].isin(persons)]
progress = st.progress(0.0, text="피처 계산 준비 중...")
s3, acf, meta, errors = extract_batch(
    selected_files["filepath_str"].tolist(),
    group_mode=group_mode,
    progress_callback=lambda d, t, n: progress.progress(d / max(t, 1), text=f"[{d}/{t}] {n}"),
    **feature_kwargs(),
)
progress.empty()

if len(s3) == 0:
    st.error("윈도우가 하나도 나오지 않았습니다.")
    st.stop()

labels = meta["f_group"].to_numpy()
recordings = meta["name"].to_numpy()
unique_groups = sorted(set(labels))

if len(unique_groups) < 2:
    st.warning("F 그룹이 하나뿐이라 분리도를 볼 수 없습니다.")
    st.stop()


def branch_encode(x: np.ndarray, dim: int, seed: int) -> Tuple[np.ndarray, PCA]:
    """evaluate_feature_pair_domain_robustness.py:branch_encode 와 동일한 인코딩."""
    flat = x.reshape(len(x), -1)
    n_components = min(int(dim), flat.shape[0] - 1, flat.shape[1])
    scaled = StandardScaler().fit_transform(flat)
    pca = PCA(n_components=n_components, svd_solver="randomized", random_state=seed)
    return pca.fit_transform(scaled), pca


def evaluate_branch(
    embedding: np.ndarray, labels: np.ndarray, groups: np.ndarray, seed: int
) -> Dict[str, float]:
    """F 라벨을 얼마나 맞히는지. 레코딩 단위 group split 으로 누수를 막는다."""
    n_groups = len(set(groups))
    n_classes = len(set(labels))
    result: Dict[str, float] = {"n": len(labels), "n_recordings": n_groups, "n_classes": n_classes}

    result["silhouette"] = (
        float(silhouette_score(embedding, labels)) if n_classes > 1 and len(labels) > n_classes else float("nan")
    )
    result["chance"] = 1.0 / n_classes

    n_splits = min(5, n_groups, min(pd.Series(labels).value_counts()))
    if n_splits < 2:
        result["balanced_accuracy_grouped"] = float("nan")
        result["balanced_accuracy_naive"] = float("nan")
        return result

    def _cv(splitter, split_args) -> float:
        preds = np.empty(len(labels), dtype=object)
        for train_idx, test_idx in splitter.split(*split_args):
            if len(set(labels[train_idx])) < 2:
                preds[test_idx] = labels[train_idx][0]
                continue
            model = LogisticRegression(max_iter=3000, class_weight="balanced", random_state=seed)
            model.fit(embedding[train_idx], labels[train_idx])
            preds[test_idx] = model.predict(embedding[test_idx])
        return float(balanced_accuracy_score(labels, preds.astype(str)))

    result["balanced_accuracy_grouped"] = _cv(
        GroupKFold(n_splits=n_splits), (embedding, labels, groups)
    )
    result["balanced_accuracy_naive"] = _cv(
        StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=seed), (embedding, labels)
    )
    return result


st.subheader(f"{len(s3)}개 윈도우 / {len(set(recordings))}개 레코딩 / {len(unique_groups)}개 F 그룹")

branches = {"CWT S3": s3, "PCA-ACF": acf}
embeddings: Dict[str, np.ndarray] = {}
metrics: Dict[str, Dict[str, float]] = {}

with st.spinner("PCA 인코딩 + 교차검증 중..."):
    for branch_name, array in branches.items():
        embedding, pca = branch_encode(array, pca_dim, int(seed))
        embeddings[branch_name] = embedding
        metrics[branch_name] = evaluate_branch(embedding, labels, recordings, int(seed))
        metrics[branch_name]["explained_variance"] = float(pca.explained_variance_ratio_.sum())

st.markdown("#### F 조건 예측 성능")
st.caption(
    "**grouped** 가 정직한 값이다 (레코딩 단위 분할). **naive** 는 윈도우를 랜덤 분할한 것으로, "
    "같은 레코딩이 train/test 양쪽에 들어가 누수로 부풀려진다 — 둘의 격차가 그 누수의 크기다."
)

summary = pd.DataFrame(metrics).T
summary.index.name = "branch"
display = summary[
    [
        "n",
        "n_recordings",
        "n_classes",
        "chance",
        "balanced_accuracy_grouped",
        "balanced_accuracy_naive",
        "silhouette",
        "explained_variance",
    ]
].round(4)
st.dataframe(display, width="stretch")

for branch_name, result in metrics.items():
    grouped = result.get("balanced_accuracy_grouped", float("nan"))
    chance = result["chance"]
    if np.isnan(grouped):
        st.caption(f"**{branch_name}** — 그룹/클래스 수가 부족해 교차검증 불가")
    elif grouped > chance + 0.15:
        st.caption(
            f"**{branch_name}** — grouped {grouped:.3f} vs 우연 {chance:.3f}: "
            f"F 조건이 피처에 뚜렷하게 반영돼 있다."
        )
    else:
        st.caption(
            f"**{branch_name}** — grouped {grouped:.3f} vs 우연 {chance:.3f}: "
            f"F 조건을 거의 구분하지 못한다 (이 표본 크기에서는)."
        )

st.markdown("---")
st.markdown("#### PCA 임베딩 (PC1 vs PC2)")
st.caption("F 그룹으로 색칠. 점은 윈도우 하나, 마우스를 올리면 레코딩 이름이 보인다.")

cols = st.columns(2)
for i, (branch_name, embedding) in enumerate(embeddings.items()):
    with cols[i]:
        st.plotly_chart(
            plot_embedding(
                embedding,
                labels,
                title=f"{branch_name}",
                symbols=[f"{n} #{w}" for n, w in zip(recordings, meta['window_index'])],
            ),
            width="stretch",
        )

st.warning(
    f"표본이 작다: F 그룹당 레코딩 {len(set(recordings)) / len(unique_groups):.1f}개. "
    "임베딩이 갈라져 보이더라도 F 가 아니라 개별 레코딩이나 참여자를 구분하고 있을 수 있다 — "
    "Page 2 의 F × 참여자 분포표에서 교락 여부를 먼저 확인할 것."
)

with st.expander("윈도우별 임베딩 좌표"):
    coords = pd.DataFrame(
        {
            "name": recordings,
            "f_group": labels,
            "person": meta["person"],
            **{f"s3_pc{j + 1}": embeddings["CWT S3"][:, j] for j in range(min(3, embeddings["CWT S3"].shape[1]))},
            **{f"acf_pc{j + 1}": embeddings["PCA-ACF"][:, j] for j in range(min(3, embeddings["PCA-ACF"].shape[1]))},
        }
    )
    st.dataframe(coords, width="stretch")
