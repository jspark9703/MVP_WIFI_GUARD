# DVC Pipeline Configuration

**Project**: CSI-Based Fall Detection (DWT Coefficient Analysis)  
**Pipeline Status**: ✅ Complete and Validated

---

## 📊 Pipeline Overview

```
Mendeley Data
     ↓
[preprocess] → Windowing, Resampling, Filtering, PCA
     ↓
[featurize] → DWT Features
     ↓
[fall_labelling] ← Full-Signal Moving Variance (CSI Raw)
     ↓
├─ [verify_raw_csi]
└─ [verify_moving_variance]
```

---

## 🔧 Pipeline Stages

### Stage 1: `preprocess`
**Purpose**: Preprocess raw CSI data

```bash
cmd: python scripts/preprocess.py --config config/preprocess.yaml
```

**Dependencies**:
- `scripts/preprocess.py`
- `src/dwt_coef/data_loader.py`, `preprocessing.py`
- `config/preprocess.yaml`

**Outputs**: `data/preprocessed/`

**Key Parameters** (from `config/preprocess.yaml`):
- Window: 2.5s, 0.25s stride
- Resample: 320 Hz
- Filter: [2 Hz - 100 Hz], order 6
- PCA: eigenvalue threshold

---

### Stage 2: `featurize`
**Purpose**: Extract DWT features

```bash
cmd: python scripts/featurization.py --config config/featurization.yaml
```

**Dependencies**:
- `scripts/featurization.py`
- `src/dwt_coef/dwt_features.py`
- `config/featurization.yaml`
- `data/preprocessed/`

**Outputs**: `data/features/`

**Key Parameters** (from `config/featurization.yaml`):
- Wavelet: `db1`
- DWT Level: 3
- Entropy: included

---

### Stage 3: `fall_labelling` ⭐
**Purpose**: Extract and label true fall segments

```bash
cmd: python scripts/true_fall_labelling.py --config config/fall_labelling.yaml
```

**Dependencies**:
- `scripts/true_fall_labelling.py`
- `src/dwt_coef/data_loader.py`, `preprocessing.py`
- `config/fall_labelling.yaml`
- `Mendeley/` (raw CSI dataset)

**Outputs**:
- `results/analysis/03_fall_segments_v2.csv` (42 segments from 50 sample files)
- `results/analysis/03_fall_labelling_metadata_v2.json` (metadata + stats)

**Key Parameters** (from `config/fall_labelling.yaml`):

| Parameter | Value | Description |
|-----------|-------|-------------|
| **omega** | 160 | Moving variance window half-width (2W+1 = 129 samples) |
| **n_streams** | 10 | Top streams to average for normalized MV |
| **norm_threshold** | 0.2 | Normalized MV threshold [0, 1] for fall detection |
| **min_duration** | 0.5s | Minimum fall segment duration |
| **merge_threshold** | 0.25s | Gap to merge adjacent segments |
| **max_duration** | 2.0s | Hard cap on segment duration |

**Method**: Full-signal moving variance (per-file min-max normalization)
- No sliding windows
- Full recording MV computation
- Adaptive normalization per file

---

### Stage 4: `verify_raw_csi`
**Purpose**: Visualize raw CSI with fall segment overlays

```bash
cmd: python scripts/05_verify_fall_segments.py --max-files 50
```

**Dependencies**:
- `scripts/05_verify_fall_segments.py`
- `src/dwt_coef/data_loader.py`
- `results/analysis/03_fall_labelling_metadata_v2.json`
- `Mendeley/`

**Outputs**: 
- `results/analysis/fall_segment_verification/` (50 PNG files)
  - Each: Raw CSI (3 antennas) + segment overlays

---

### Stage 5: `verify_moving_variance`
**Purpose**: Visualize moving variance with fall segments

```bash
cmd: python scripts/06_moving_variance_verification.py --max-files 50
```

**Dependencies**:
- `scripts/06_moving_variance_verification.py`
- `src/dwt_coef/data_loader.py`, `preprocessing.py`
- `results/analysis/03_fall_labelling_metadata_v2.json`
- `Mendeley/`

**Outputs**: 
- `results/analysis/fall_moving_variance_verification/` (50 PNG files)
  - Each: Moving variance (top 3 streams) + segment overlays

---

## 🚀 Usage

### Run Full Pipeline
```bash
# Run all stages
dvc repro

# Or with options
dvc repro --force           # Force re-run all stages
dvc repro --force -P       # Force re-run all pipelines
```

### Run Specific Stages

```bash
# Run fall labelling only
dvc repro fall_labelling

# Run verification stages only
dvc repro verify_raw_csi verify_moving_variance
```

### Run with Configuration Override

The fall_labelling script supports CLI args to override config:

```bash
# Override specific parameters
python scripts/true_fall_labelling.py \
  --config config/fall_labelling.yaml \
  --norm-threshold 0.15 \
  --max-files 100

# All parameters (omitted → use config defaults)
python scripts/true_fall_labelling.py \
  --config config/fall_labelling.yaml \
  --omega 64 \
  --norm-threshold 0.2 \
  --min-duration 0.5 \
  --merge-threshold 0.25 \
  --max-duration 2.0
```

### View Pipeline Status
```bash
# Show DAG
dvc dag

# Show pipeline status
dvc status

# Show metric values
dvc metrics show

# Show parameter values
dvc params show
```

---

## 📁 Configuration Files

### `config/fall_labelling.yaml`
Main configuration for fall segment extraction:

```yaml
# Moving variance computation
moving_variance:
  omega: 64                    # Window half-width
  n_streams: 10                # Top streams to average

# Fall segment detection
detection:
  norm_threshold: 0.2          # [0, 1] threshold
  min_duration: 0.5            # seconds
  merge_threshold: 0.25        # seconds
  max_duration: 2.0            # seconds

# Processing options
processing:
  visualize: false             # Generate plots
```

### `config/preprocess.yaml`
Preprocessing configuration (unchanged)

### `config/featurization.yaml`
DWT feature extraction (unchanged)

---

## 📊 Output Structure

```
results/analysis/
├── 03_fall_segments_v2.csv
│   └── Columns: filename, env, subject, activity, trial,
│                fall_start_sec, fall_end_sec, fall_duration_sec, mv_avg
│
├── 03_fall_labelling_metadata_v2.json
│   └── method, config, summary, per-file results
│
├── fall_segment_verification/
│   └── E1_S01_C01_A02_T01_verify.png (×50)
│
└── fall_moving_variance_verification/
    └── E1_S01_C01_A02_T01_mv_verify.png (×50)
```

---

## 📈 Test Results (50 Sample Files)

| Metric | Value |
|--------|-------|
| Files Processed | 50/50 (100%) |
| Detection Rate | 80% (40/50 files) |
| Total Segments | 42 |
| Avg Duration | 0.81s |
| Duration Range | 0.55s ~ 1.77s |
| Errors | 0 |

---

## 🔄 Parameter Tuning

To adjust fall detection sensitivity:

1. **Increase Detection (Lower Threshold)**:
   ```yaml
   detection:
     norm_threshold: 0.15  # from 0.2 → more sensitive
   ```

2. **Decrease False Positives (Higher Threshold)**:
   ```yaml
   detection:
     norm_threshold: 0.25  # from 0.2 → more selective
   ```

3. **Merge Nearby Segments**:
   ```yaml
   detection:
     merge_threshold: 0.5  # from 0.25 → more aggressive merging
   ```

4. **Filter Short Segments**:
   ```yaml
   detection:
     min_duration: 0.8     # from 0.5 → remove short events
   ```

After changing config, run:
```bash
dvc repro fall_labelling --force
```

---

## 🎯 Next Steps

### Phase 1: Full Dataset Processing
```bash
# Process all 9000 files
dvc repro fall_labelling

# Expected: ~9000-12000 fall segments
# Estimated time: ~1 hour
```

### Phase 2: Model Training (Recommended)
- Use `03_fall_segments_v2.csv` as fall labels
- Input: Raw CSI data + DWT features
- Target: Binary classification (fall / non-fall)

### Phase 3: Real-Time Deployment
- Adapt for streaming CSI input
- Implement low-latency moving variance
- Handle variable sampling rates

---

## 🔗 Related Files

- [Final Analysis Summary](results/analysis/FINAL_ANALYSIS_SUMMARY.md)
- [Verification Report](results/analysis/VERIFICATION_REPORT.md)
- [Optimization Report](results/analysis/OPTIMIZATION_REPORT_v3.md)

---

## 💡 Key Features

✅ **Version Control**: Full DVC tracking of inputs/outputs/configs  
✅ **Reproducibility**: Deterministic with fixed random seeds  
✅ **Scalability**: Ready for 9000+ files  
✅ **Modularity**: Independent stages can be re-run  
✅ **Traceability**: Complete metadata in JSON  

---

**Status**: 🟢 Ready for Production  
**Last Updated**: 2026-06-29
