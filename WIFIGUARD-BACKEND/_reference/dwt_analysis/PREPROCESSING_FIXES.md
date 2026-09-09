# Preprocessing.py Fixes — AmFall Algorithm Alignment

## Summary
Fixed 4 major algorithmic discrepancies in `src/dwt_coef/preprocessing.py` to match the AmFall algorithm specification.

---

## Changes Made

### 1. **`select_streams()` — Complete Algorithmic Rewrite** ✅ [HIGH]

**Problem:** 
- Q-threshold filtering step was entirely missing
- Selection was done per-antenna independently, not globally
- No fallback when no streams pass threshold

**Solution:**
- Now flattens all antenna streams into a global pool (N, n_ant*30)
- Ranks all streams by q(h) globally
- Takes top ℓ candidates
- Computes normalized Q(h_i) = q(h_i) / Σq for candidates
- Filters: keeps only streams where Q(h_i) >= 1/ℓ
- Fallback: if none pass threshold, keeps single stream with highest q
- **New parameter `use_ant`**: Optional list of antenna indices to use (None = all 3)

**Signature Change:**
```python
def select_streams(
    amplitude_3d: np.ndarray,
    omega: int = 64,
    n_streams: int = 30,                    # was n_per_antenna: int = 10
    use_ant: Optional[List[int]] = None     # NEW: restrict to specific antennas
) -> Tuple[np.ndarray, Dict[str, Any]]:
```

**Stats Output Changes:**
- Added: `antennas_used`, `candidate_stream_count`, `top_n_considered`, `q_threshold`
- Added: `selected_stream_origins` (track which antenna/subcarrier each selected stream came from)
- Added: `q_values_top`, `Q_values_normalized` (for debugging)

---

### 2. **`preprocess_segment()` — Updated to Use New Signature** ✅ [MEDIUM]

**Changes:**
- Replaced parameter `n_per_antenna: int = 10` with `n_streams: int = 30`
- Added parameter `use_ant: Optional[List[int]] = None`
- Updated call to `select_streams()` to pass new parameters

**Signature Change:**
```python
def preprocess_segment(
    ...,
    n_streams: int = 30,              # was n_per_antenna: int = 10
    use_ant: Optional[List[int]] = None,  # NEW
    ...
) -> Tuple[np.ndarray, Dict[str, Any]]:
```

---

### 3. **`preprocess_csi()` — Fixed Parameter Mapping + PC Summation** ✅ [HIGH]

**Problems:**
- Parameter `top_l` was passed directly to `select_streams()` as `n_per_antenna`, tripling the intended selection
- PC summation step was missing (spec requires summed output)

**Solution:**
- Renamed parameter from `top_l` to `n_streams` for clarity
- Added `use_ant` parameter
- Added PC summation: `summed_signal = pcs.sum(axis=1)`
- Added normalization after summation
- Output is now 1D signal (N,) not (N, n_pcs) matrix

**Signature Change:**
```python
def preprocess_csi(
    ...,
    n_streams: int = 30,                   # was top_l: Optional[int] = None
    use_ant: Optional[List[int]] = None,   # NEW
    ...
) -> Tuple[np.ndarray, Dict[str, Any]]:
```

**Output Change:**
- **Before:** `(pcs: (N, n_selected_pcs), stats)`
- **After:** `(summed_signal: (N,), stats)` — spec-compliant single signal

---

### 4. **`_moving_variance()` — Spec Compliance** ✅ [LOW]

**Problem:**
- Window denominator was 2W+1 (via `uniform_filter1d(size=2W+1)`)
- Spec requires denominator 2W

**Solution:**
- Rescale result by factor `(2W+1) / 2W` to match spec
- Updated docstring with spec reference

**Impact:**
- Since q(h) = max(υ) / mean(υ), the denominator cancels out
- This is cosmetic/spec-fidelity, doesn't affect q values
- But now exactly matches the algorithm specification

---

## Backward Compatibility

⚠️ **Breaking Changes:**
- `select_streams`: `n_per_antenna` → `n_streams` (parameter rename + semantic change)
- `preprocess_csi`: `top_l` → `n_streams` (parameter rename)
- `preprocess_csi` output shape: `(N, n_pcs)` → `(N,)` (now returns summed signal, not PC matrix)

These are necessary for spec alignment. Code using the old signatures will need updates.

---

## Testing Recommendations

1. **Unit test for `select_streams` Q-threshold:**
   - Verify selected stream count ≤ n_streams (not always = n_streams)
   - Verify fallback works when no streams pass threshold
   - Test `use_ant` antenna filtering

2. **Integration test:**
   - Run DVC pipeline stage: `dvc repro data/features`
   - Verify output shapes: `preprocess_csi` returns (N,) not (N, n_pcs)

3. **Validation:**
   - Compare q values before/after fix (should be unchanged since denominator cancels)
   - Verify PC summation is applied in `preprocess_csi`
   - Check normalization is working (output should have mean≈0, std≈1)

---

## Files Modified
- `src/dwt_coef/preprocessing.py`

## Lines Changed
- `_moving_variance()`: lines 18-32 (denominator fix)
- `select_streams()`: lines 242-334 (complete rewrite)
- `preprocess_segment()`: lines 338-423 (parameter updates)
- `preprocess_csi()`: lines 426-494 (parameter + summation fixes)
