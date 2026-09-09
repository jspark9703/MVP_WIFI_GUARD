# Parameter Audit Complete - All Missing Parameters Fixed

## 🔍 Audit Results

### Missing Parameters Found & Fixed

#### 1. ✅ `max_pcs` Parameter
**Status:** ADDED to all locations
- **config/preprocess.yaml** - Added under `pca` section (default: 3)
- **scripts/preprocess.py** - Updated to read from config and pass to `preprocess_segment()`
- **debug_single_file.py** - Updated to display `max_pcs` info in debug output
- **dvc.yaml** - Added to parameter tracking list

#### 2. ✅ Stream Selection Parameters
**Status:** UPDATED with new algorithm
- **Old:** `stream_selection.n_per_antenna: 10` (per-antenna, 3×10=30 total)
- **New:** `stream_selection.n_streams: 30` (global pool selection)
- **New:** `stream_selection.use_ant: null` (optional antenna restriction, now set to [1,2])

#### 3. ✅ DVC Pipeline Parameters
**Status:** UPDATED to track new parameters
- Removed: `stream_selection.n_per_antenna`
- Added: `stream_selection.n_streams`
- Added: `stream_selection.use_ant`
- Added: `pca.max_pcs`

---

## 📋 Complete Parameter Configuration

### preprocess.yaml - Current State

```yaml
# === Stream Selection (Global + Q-Threshold) ===
stream_selection:
  omega: 160              # Moving variance window half-width
  n_streams: 30           # Top streams from global pool (90 total)
  use_ant: [1,2]          # Antennas to use (null = all 3)

# === PCA PC Selection ===
pca:
  eigenvalue_threshold: null  # Auto: max(eigenvalues) * 1e-3
  max_pcs: 3                  # Max PCs to select per AmFall spec
```

### featurization.yaml - Current State

```yaml
# === DWT Parameters ===
dwt:
  wavelet: "morse"        # Complex wavelet for transient detection
  level: 5                # Decomposition levels
  include_entropy: true   # Include Shannon entropy as feature
```

---

## 🔗 Parameter Flow (Complete Chain)

### Stage 1: Preprocessing

```
config/preprocess.yaml
    ↓
scripts/preprocess.py
    ├─ Reads: omega, n_streams, use_ant, max_pcs
    ↓
src/dwt_coef/preprocessing.py
    ├─ select_streams(omega, n_streams, use_ant)
    ├─ select_pcs(..., max_pcs)
    ├─ preprocess_segment(..., max_pcs)
    └─ Returns: normalized 1D signals
    ↓
data/preprocessed/*.npz
```

### Stage 2: Featurization

```
config/featurization.yaml
    ↓
scripts/featurization.py
    ├─ Reads: wavelet, level, include_entropy
    ↓
src/dwt_coef/dwt_features.py
    └─ compute_energy_entropy(wavelet, level)
    ↓
data/features/*.npz
```

---

## 🔄 DVC Pipeline Tracking

### Updated params in dvc.yaml

```yaml
preprocess:
  params:
    - config/preprocess.yaml:
        - stream_selection.omega
        - stream_selection.n_streams      # NEW
        - stream_selection.use_ant        # NEW
        - pca.eigenvalue_threshold
        - pca.max_pcs                     # NEW

featurize:
  params:
    - config/featurization.yaml:
        - dwt.wavelet                     # Changed to "morse"
        - dwt.level
        - dwt.include_entropy
```

---

## ✅ Files Updated

| File | Changes |
|------|---------|
| `config/preprocess.yaml` | Added `pca.max_pcs: 3` |
| `config/featurization.yaml` | Wavelet: `db4` → `morse` |
| `scripts/preprocess.py` | Added `max_pcs` parameter passing |
| `debug_single_file.py` | Added `max_pcs` to `select_pcs()` call & debug output |
| `dvc.yaml` | Updated parameter tracking for new algorithm |

---

## 🧪 Current Configuration

### Preprocessing
- ✅ Omega (ω): 160 (larger window for fall detection)
- ✅ Stream selection: Global n_streams=30 with optional antenna restriction
- ✅ PCA: Max 3 PCs (AmFall spec compliance)
- ✅ Normalization: Z-score after PC summation

### Featurization  
- ✅ Wavelet: Morse (complex-valued, better for transients)
- ✅ Decomposition: 5 levels
- ✅ Features: Energy (cA, cD1-cD5) + Entropy = 7 total

### DVC Pipeline
- ✅ All parameters tracked for reproducibility
- ✅ Clear dependency chain
- ✅ Ready for `dvc repro`

---

## 🚀 Ready to Run

The pipeline is now complete with:
1. ✅ All missing parameters added
2. ✅ All config files updated
3. ✅ All scripts synchronized
4. ✅ DVC pipeline properly configured
5. ✅ Parameter changes tracked

**Next step:** Run the pipeline with updated parameters
```bash
dvc gc --force              # Clear old cache
dvc repro data/features -R  # Regenerate with new algorithm
```

---

## Summary

**All parameters audited, all missing parameters added, all files synchronized. Pipeline is now complete and ready for execution.**

**Status: ✅ AUDIT COMPLETE**
