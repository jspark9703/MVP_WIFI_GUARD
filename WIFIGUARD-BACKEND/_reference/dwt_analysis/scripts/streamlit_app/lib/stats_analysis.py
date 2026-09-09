"""
scipy.stats wrappers for Page 3's subcarrier-selection statistics.

Each function returns a small dict of {statistic, p_value, ...} rather than raising,
so degenerate inputs (e.g. only one group present after filtering) produce a
None-valued result the page can render as "not enough data" instead of crashing.
"""

from typing import Any, Dict, List

import numpy as np
import pandas as pd
from scipy import stats


def categorical_chisq(df: pd.DataFrame, col_a: str, col_b: str) -> Dict[str, Any]:
    """
    Chi-square test of independence between two categorical columns (e.g. "antenna"
    vs. "env", or "subcarrier" vs. "class_id").

    Returns: {table, chi2, p_value, dof, cramers_v} — chi2/p_value/dof/cramers_v are
    None if the contingency table is degenerate (fewer than 2 rows or columns).
    """
    table = pd.crosstab(df[col_a], df[col_b])

    if table.shape[0] < 2 or table.shape[1] < 2:
        return {"table": table, "chi2": None, "p_value": None, "dof": None, "cramers_v": None}

    chi2, p_value, dof, _expected = stats.chi2_contingency(table)
    n = table.values.sum()
    r, k = table.shape
    cramers_v = float(np.sqrt((chi2 / n) / min(r - 1, k - 1)))

    return {
        "table": table, "chi2": float(chi2), "p_value": float(p_value),
        "dof": int(dof), "cramers_v": cramers_v,
    }


def cramers_v_matrix(df: pd.DataFrame, columns: List[str]) -> pd.DataFrame:
    """
    Symmetric matrix of pairwise Cramér's V association strength among `columns`
    (all treated as categorical) — a correlation-matrix analogue for nominal data.
    Diagonal is 1.0; a cell is NaN where that pair's contingency table is degenerate.
    """
    matrix = pd.DataFrame(index=columns, columns=columns, dtype=float)
    for i, col_a in enumerate(columns):
        matrix.loc[col_a, col_a] = 1.0
        for col_b in columns[i + 1:]:
            v = categorical_chisq(df, col_a, col_b)["cramers_v"]
            matrix.loc[col_a, col_b] = v
            matrix.loc[col_b, col_a] = v
    return matrix


def q_value_kruskal(df_free: pd.DataFrame, group_col: str) -> Dict[str, Any]:
    """
    Kruskal-Wallis test: does the q(h) distribution (from "free" rows) differ across
    groups of `group_col` (e.g. "antenna", "env", "class_id")?

    Returns: {statistic, p_value, n_groups} — statistic/p_value are None if fewer
    than 2 non-empty groups are present.
    """
    groups = [g["q_value"].to_numpy() for _, g in df_free.groupby(group_col) if len(g) > 0]

    if len(groups) < 2:
        return {"statistic": None, "p_value": None, "n_groups": len(groups)}

    statistic, p_value = stats.kruskal(*groups)
    return {"statistic": float(statistic), "p_value": float(p_value), "n_groups": len(groups)}


def forced_antenna_friedman(df_forced: pd.DataFrame) -> Dict[str, Any]:
    """
    Friedman test (nonparametric repeated-measures): does per-session mean q(h) differ
    across the 3 forced-single-antenna conditions, paired by session?

    Returns: {statistic, p_value, n_sessions, mean_by_mode} — statistic/p_value are
    None if fewer than 2 sessions have data for all 3 forced modes.
    """
    modes = ["ant0_only", "ant1_only", "ant2_only"]
    session_cols = ["env", "subject", "class_id", "trial"]

    forced = df_forced[df_forced["selection_mode"].isin(modes)]
    per_session = forced.groupby(session_cols + ["selection_mode"])["q_value"].mean().reset_index()
    pivot = per_session.pivot(index=session_cols, columns="selection_mode", values="q_value")
    pivot = pivot.reindex(columns=modes).dropna()

    if len(pivot) < 2:
        return {"statistic": None, "p_value": None, "n_sessions": len(pivot), "mean_by_mode": {}}

    statistic, p_value = stats.friedmanchisquare(*[pivot[m] for m in modes])
    return {
        "statistic": float(statistic), "p_value": float(p_value), "n_sessions": len(pivot),
        "mean_by_mode": pivot.mean().to_dict(),
    }
