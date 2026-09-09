# Configuration and Script Updates

## Summary
Updated configuration files and scripts to align with the fixed preprocessing algorithm and DWT parameter changes.

---

## 1. **config/preprocess.yaml** — Stream Selection Parameters ✅

### Changes:
```yaml
# OLD:
stream_selection:
  omega: 64
  n_per_antenna: 10   # Select 10 per antenna → 30 total (per-antenna logic)

# NEW:
stream_selection:
  omega: 64
  n_streams: 30       # Target 30 streams from global pool (global logic)
  use_ant: null       # null = all 3 antennas; specify: [0], [0,2] to restrict
```

### Reasoning:
- `n_per_antenna` → `n_streams`: Changed from per-antenna count to global target count
- `use_ant`: New parameter to optionally restrict stream selection to specific antennas (e.g., `[0, 1]` for antennas 0 and 1 only)

---

## 2. **config/featurization.yaml** — Wavelet Selection ✅

### Changes:
```yaml
# OLD:
dwt:
  wavelet: "db4"        # Daubechies-4

# NEW:
dwt:
  wavelet: "morse"      # Morse wavelet (complex-valued, better for transient detection)
```

### Reasoning:
- **Morse wavelet** is more suited for detecting transient signals and movement changes
- Better captures sharp edges and discontinuities in fall detection
- Complex-valued wavelet provides phase information for richer feature extraction

---

## 3. **scripts/preprocess.py** — Updated to Use New Parameters ✅

### Changes:

#### 3.1 Updated Docstring
```python
# OLD:
# - Select streams per antenna (30 → n_per_antenna)

# NEW:
# - Select streams globally (90 → n_streams, with Q-threshold filtering)
# - Select PCs by q(p) metric (max 3 PCs)
# - Sum selected PCs → normalized 1D representative signal
```

#### 3.2 Updated Function Call in `process_file()`
```python
# OLD:
H_S, stream_stats = select_streams(amp_f, omega, n_per_antenna)

# NEW:
H_S, stream_stats = select_streams(
    amp_f, 
    omega, 
    n_streams=stream_cfg["n_streams"],
    use_ant=stream_cfg.get("use_ant")
)
```

#### 3.3 Updated Stats Collection
```python
# OLD:
q_values_per_antenna = s.get("q_values_per_antenna", [])
if q_values_per_antenna:
    for q_list in q_values_per_antenna:
        qh_list.extend(q_list)

# NEW:
q_values_top = s.get("q_values_top", [])
if q_values_top:
    qh_list.extend(q_values_top)
```

---

## 4. **debug_single_file.py** — Updated Debugging Script ✅

### Changes:

#### 4.1 Updated Stream Selection Debug Section
```python
# OLD:
print(f"  Parameters: omega=64, n_per_antenna=10")
print(f"  Logic: for each antenna, select top 10/30 subcarriers by q(h)")

for a in range(3):
    amp_a = amp_f[:, a, :]
    # ... per-antenna q computation ...
    
H_S, stream_stats = select_streams(amp_f, omega=64, n_per_antenna=10)

# NEW:
print(f"  Parameters: omega=64, n_streams=30, use_ant=None")
print(f"  Logic: flatten all antennas (90 streams), rank globally, apply Q-threshold")

H_S, stream_stats = select_streams(amp_f, omega=64, n_streams=30, use_ant=None)
```

#### 4.2 Updated Output Display
```python
# Now shows:
# - Antennas used
# - Candidate pool size
# - Top-N considered
# - Q-threshold value
# - Selected stream count
# - Selected stream origins (which antenna/subcarrier each came from)
```

---

## Files Updated

| File | Type | Changes |
|------|------|---------|
| `config/preprocess.yaml` | Config | `n_per_antenna` → `n_streams`; added `use_ant` parameter |
| `config/featurization.yaml` | Config | `wavelet: "db4"` → `wavelet: "morse"` |
| `scripts/preprocess.py` | Script | Updated parameter passing, stats collection, docstring |
| `debug_single_file.py` | Script | Updated stream selection debug output |

---

## Backward Compatibility

⚠️ **Breaking Changes:**
- Any existing `.npz` files from old preprocessing pipeline need to be regenerated
- DVC cache needs to be cleared: `dvc gc --force` before re-running pipeline

✅ **What Stays the Same:**
- Output file format/naming
- Window size/stride parameters
- Resampling parameters
- Bandpass filter parameters

---

## Testing the Updates

### Quick Test:
```bash
# Test that config loads correctly
python -c "
import yaml
with open('config/preprocess.yaml') as f:
    cfg = yaml.safe_load(f)
    print('preprocess.yaml:', cfg['stream_selection'])

with open('config/featurization.yaml') as f:
    cfg = yaml.safe_load(f)
    print('featurization.yaml:', cfg['dwt'])
"
```

### Full Pipeline Test:
```bash
# Test with single file
python debug_single_file.py

# Run preprocess on small subset
python scripts/preprocess.py --config config/preprocess.yaml --max-files 1
```

---

## Next Steps

1. Clear DVC cache: `dvc gc --force`
2. Run preprocessing: `dvc repro data/features -R`
3. Verify output shapes and features
4. Compare feature distributions with old pipeline
