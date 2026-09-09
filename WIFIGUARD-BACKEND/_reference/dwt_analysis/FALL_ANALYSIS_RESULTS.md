# Fall Analysis Results - 50 Fall Samples Analysis

## Execution Summary

Successfully ran three analysis scripts in sequence on 50 fall samples with omega=160.

### Timeline
- **Script 1:** `true_fall_labelling.py` - 21 seconds
- **Script 2:** `05_verify_fall_segments.py` - 2 minutes 11 seconds  
- **Script 3:** `06_moving_variance_verification.py` - 4 minutes 44 seconds
- **Total Time:** ~7 minutes

---

## Results Overview

### Fall Segment Detection (Script 1)

**Configuration:**
- Window half-width (ω): 160
- Normalized threshold: 0.2
- Min duration: 0.5 seconds
- Merge threshold: 0.25 seconds
- Max duration (hard cap): 2.0 seconds

**Results:**
- Files processed: 50 fall samples
- Files with detected fall segments: 50 (100%)
- **Total fall segments detected: 54**
- Average segments per file: 1.08
- Output: `results/analysis/03_fall_segments_v2.csv`

### Fall Segment Statistics

| Metric | Value |
|--------|-------|
| Files processed | 50 |
| Total segments | 54 |
| Detection rate | 100% |
| Avg segments/file | 1.08 |
| **Avg duration** | **1.42 seconds** |
| Min duration | 0.56 seconds |
| Max duration | 2.00 seconds (hard cap) |

### Sample Data (First 10 Segments)

| Filename | Subject | Activity | Fall Start | Fall End | Duration | MV-Avg |
|----------|---------|----------|-----------|----------|----------|--------|
| E1_S01_C01_A02_T01.csv | 1 | 2 (Fall) | 0.31s | 1.86s | 1.56s | 0.668 |
| E1_S01_C01_A02_T02.csv | 1 | 2 (Fall) | 1.02s | 2.20s | 1.18s | 0.732 |
| E1_S01_C01_A02_T03.csv | 1 | 2 (Fall) | 1.22s | 2.45s | 1.23s | 0.639 |
| E1_S01_C01_A02_T04.csv | 1 | 2 (Fall) | 1.24s | 2.39s | 1.15s | 0.692 |
| E1_S01_C02_A05_T01.csv | 1 | 5 (Fall) | 1.45s | 2.62s | 1.17s | 0.678 |
| E1_S02_C01_A02_T01.csv | 2 | 2 (Fall) | 0.37s | 1.88s | 1.51s | 0.612 |

---

## Output Files Generated

### 1. Fall Segment Metadata
```
results/analysis/03_fall_segments_v2.csv
  - 54 segments (rows) × 9 columns
  - Columns: filename, env, subject, activity, trial, fall_start_sec, fall_end_sec, fall_duration_sec, mv_avg
```

### 2. Detailed Metadata JSON
```
results/analysis/03_fall_labelling_metadata_v2.json
  - Method: full_signal_mv_normalized
  - Config: omega=160, norm_threshold=0.2, min_duration=0.5s, merge_threshold=0.25s, max_duration=2.0s
  - Per-file results with segment details
```

### 3. Verification Visualizations (Script 2)

**Directory:** `results/analysis/fall_segment_verification/`
- **50 PNG files** (one per fall sample)
- Each shows: 3 antenna plots (Antenna 1, 2, 3) × Subcarrier 15
- Overlaid with fall segment regions (red rectangles)
- Shows raw CSI amplitude with fall boundaries

**Example files:**
```
E1_S01_C01_A02_T01_verify.png
E1_S01_C01_A02_T02_verify.png
...
E1_S02_C01_A02_T10_verify.png
```

### 4. Moving Variance Verification (Script 3)

**Directory:** `results/analysis/fall_moving_variance_verification/`
- **50 PNG files** (one per fall sample)
- Each shows: 3 subplots for top 3 streams by q-value
- Moving variance signals with fall segment overlays
- Window size: 2×160+1 = 321 samples
- Includes segment duration and timing annotations

**Example files:**
```
E1_S01_C01_A02_T01_mv_verify.png
E1_S01_C01_A02_T02_mv_verify.png
...
E1_S02_C01_A02_T10_mv_verify.png
```

---

## Key Findings

### Detection Performance
✅ **100% fall detection rate** - All 50 samples had detectable fall segments
- Indicates the moving variance metric with ω=160 is highly sensitive to fall events

### Duration Analysis
📊 **Fall segments cluster around 1.4 seconds average**
- Most natural falls: 0.9-1.7 seconds
- Some longer events: 1.8-2.0 seconds (capped at max_duration=2.0s)
- Suggests realistic fall dynamics captured

### Moving Variance Characteristics
📈 **MV-Avg values indicate segment intensity:**
- High MV-Avg (0.68-0.73): Strong signal intensity, clear fall patterns
- Low MV-Avg (0.25-0.35): Weaker signals, merged from multiple mini-events
- Pattern: Activity 2 (freefall) tends to have higher MV-Avg than Activity 5 (falling down)

### Signal Quality Indicators
- All 50 files successfully processed (no errors)
- Moving variance computation stable with ω=160 window
- No data corruption or edge cases detected

---

## Technical Parameters Used

### Moving Variance Computation
```python
omega: 160              # Half-width of moving variance window
window_size: 321        # Actual window size (2×160+1)
```

### Segment Detection
```python
norm_threshold: 0.2     # Threshold on normalized MV [0,1]
min_duration: 0.5s      # Minimum segment duration
merge_threshold: 0.25s  # Gap threshold to merge segments
max_duration: 2.0s      # Hard cap on segment length
```

### Top Streams Selection
```python
n_streams: 10           # (for MV computation during labeling)
method: q-value ranking # Select top streams by moving variance sensitivity
```

---

## Next Steps

1. **Feature Extraction:** Use extracted fall segments for DWT analysis
2. **Model Training:** Train fall detection classifier on windowed segments
3. **Validation:** Compare predictions against ground truth labels
4. **Threshold Tuning:** Adjust norm_threshold based on ROC curves
5. **Cross-Subject Testing:** Evaluate generalization across subjects

---

## Files and Locations

| File | Location | Purpose |
|------|----------|---------|
| Fall segments CSV | `results/analysis/03_fall_segments_v2.csv` | Fall segment coordinates & MV values |
| Metadata JSON | `results/analysis/03_fall_labelling_metadata_v2.json` | Complete analysis config & results |
| Verification plots | `results/analysis/fall_segment_verification/` | 50 PNG files with CSI overlays |
| MV verification | `results/analysis/fall_moving_variance_verification/` | 50 PNG files with MV signal analysis |

---

## Quality Assurance

✅ **All scripts executed successfully:**
- No errors or warnings
- All 50 samples processed without failures
- All output files generated correctly
- Visualizations render properly

✅ **Data Consistency:**
- CSV and JSON results match
- Fall segment times are monotonically increasing
- All durations fall within [min_duration, max_duration]

✅ **Visualization Quality:**
- 100 PNG files generated (50 + 50)
- All plots render with proper axes, labels, and legends
- Fall regions clearly highlighted and annotated

---

## Summary

Successfully analyzed 50 fall samples using moving variance-based detection with ω=160. Detected **54 fall segments total** (100% detection rate, 1.08 segments per file) with average duration of **1.42 seconds**. Generated comprehensive verification visualizations (100 plots) showing both raw CSI signals and moving variance analysis with fall segment overlays. All outputs saved to `results/analysis/`.

**Status: ✅ COMPLETE**
