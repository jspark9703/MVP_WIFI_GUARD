# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

DWT (Discrete Wavelet Transform)-based fall detection analysis using the Mendeley CSI dataset (Alsaify et al. 2020 — Intel 5300 NIC, 1x3 MIMO, 30 subjects across 3 environments). The preprocessing pipeline follows the "amfall" paper's stream/PC selection methodology. Comments, docs, and commit messages mix Korean and English.

## Environment

- Conda env `wifisense` (`conda activate wifisense`); no `requirements.txt`/`environment.yml` exists in the repo — dependencies (numpy, scipy, pandas, pywt, matplotlib, tqdm, pyyaml, dvc) are assumed present in the env.
- Python 3.13.

## Commands

Run all commands from the repo root.

```bash
# Full DVC pipeline (only stages currently enabled: preprocess -> featurize)
dvc repro

# Individual stages
python scripts/preprocess.py --config config/preprocess.yaml [--max-files 5]
python scripts/featurization.py --config config/featurization.yaml [--max-files 10]

# Activity/fall segment estimation (standalone, not yet wired into dvc.yaml)
python scripts/true_fall_labelling.py --config config/fall_labelling.yaml
python scripts/true_activity_time.py --config config/fall_labelling.yaml

# Interactive moving-variance / subcarrier-selection explorer (Streamlit, 3 pages)
streamlit run scripts/streamlit_app/app.py

# Domain-shift explorer — per-environment raw statistics, q-value through the amfall stages, FallDeFi features (6 pages)
streamlit run scripts/streamlit_domain_shift_app/app.py

# Pull DVC-tracked data/results from remote
dvc pull                     # everything
dvc pull data/preprocessed   # just preprocessed signals
dvc pull data/features       # just DWT features
```

There is no test suite, lint config, or build step — this is a data-science analysis project. Validate changes by running the relevant pipeline stage with `--max-files N` on a small sample and inspecting `manifest.csv` / output `.npz` files, or via the notebooks.

## Architecture

### Data flow

```
Mendeley/Environment {1,2,3}/Subject N/E{e}_S{ss}_C{c}_A{aa}_T{tt}.csv   (raw CSI, DVC/gitignored — not committed)
        │  scripts/preprocess.py   (config/preprocess.yaml)
        ▼
data/preprocessed/*.npz   — per-segment 1D representative signals
        │  scripts/featurization.py   (config/featurization.yaml)
        ▼
data/features/*.npz   — per-segment DWT energy/entropy feature vectors
```

Every stage keys files by the same filename pattern `E{env}_S{subject:02d}_C{class}_A{activity:02d}_T{trial:02d}` and both writes a `manifest.csv` alongside its outputs summarizing per-file processing status/stats. `activity` in {2, 5} means a fall (`is_fall`); this mapping is duplicated in several places (`data_loader.parse_filename`, `featurization.py:parse_filename_metadata`, `ACTIVITY_MAP` in `true_activity_time.py`) — keep them in sync if it ever changes.

DVC (`dvc.yaml`/`dvc.lock`) tracks `data/preprocessed/` and `data/features/` as pipeline outputs, parameterized by the corresponding config YAML. `dvc.yaml` also contains commented-out stages (`fall_labelling`, `verify_raw_csi`, `verify_moving_variance`) for the newer fall/activity segment-detection scripts — these currently run standalone rather than through DVC.

### Core modules (`src/dwt_coef/`)

- **`data_loader.py`** — parses Mendeley filenames/CSVs into `{csi: (N,3,30) complex128, timestamps, env, subject, class_id, activity, trial, is_fall}`. `get_file_index()` builds a DataFrame index of the whole dataset without loading CSI data; `load_dataset()` filters + loads.
- **`preprocessing.py`** — the amfall-based per-segment pipeline, applied via `preprocess_segment()`:
  1. `extract_amplitude` — magnitude of complex CSI, shape (N, 3, 30)
  2. `resample_signal` — linear interpolation onto a regular grid (irregular timestamp gaps handled with a tolerance + max interpolation steps; falls back to nearest-sample copy outside tolerance)
  3. `bandpass_filter` — zero-phase Butterworth (`sosfiltfilt`)
  4. `select_streams` — global top-N stream selection across antennas using the amfall q(h) metric (`_compute_q` = max/mean of moving variance), then a normalized-Q threshold filter
  5. `select_pcs` — PCA over selected streams, then the same q-metric + Q-threshold logic selects PCs (capped at `max_pcs`)
  6. Sum selected PCs into one representative 1D signal, z-score normalize
  - `sliding_window_raw()` segments a recording on raw (irregular) timestamps before any of the above is applied per-segment.
- **`dwt_features.py`** — `compute_energy_entropy()` runs `pywt.wavedec` and returns per-band energy (`E_cA`, `E_cD1..level`) plus Shannon entropy over the normalized energy distribution. `extract_scalogram()` produces a visualization-only multi-resolution image.
- **`q_diagnostics.py`** — "full-visibility" restatement of `select_streams`/`select_pcs` for analysis. The production functions discard exactly what a domain-shift study needs: q for *non-selected* candidates, origins for the whole candidate pool, the PCA eigen-spectrum, `q_pc_values`/`Q_normalized_pc`, and the PC threshold `1/(n_c-1)`. `stream_q_landscape()` / `pc_q_landscape()` return all of it; the q kernel (`_compute_q`) is **imported, not copied**, so only selection bookkeeping is restated. `verify_against_reference()` runs both implementations side by side and is surfaced in the domain-shift app (§4.6) so the duplication is auditable. **Do not "fix" this by editing `preprocessing.py`** — that file is a listed `deps:` of the `preprocess` DVC stage, so any edit marks `preprocess` → `featurize` stale (4500-file re-run for byte-identical output). A new module has no DVC impact.
  - Invariant worth knowing: the Q-threshold is applied to a **q-descending-sorted** array, so the selected set is always a **prefix** of `q_values_top`/`Q_values_normalized`. Test membership with `i < selected_stream_count`; `selected_stream_indices` are indices into the full 60/90 candidate pool and are *not* usable to index those lists. (`selection_stats.py` documents this correctly; `scripts/streamlit_app/lib/plotting.py:plot_q_value_bar` gets it wrong.)
- **`falldefi_features.py`** — the spectrogram features of FallDeFi (Palipana et al., IMWUT 2017) Table 1: extreme/torso frequency curve stats, their max ratio, spectral entropy in 1–10 / 10–30 / 30–max Hz, fractal dimension, PBC event duration, and the PBC above/below energy ratio. `OF_KEYS` is the full Original-Feature set; `SF_KEYS` is the Selected-Feature subset the authors report as **robust to environment change** — the domain-shift app's page 5 tests that claim on Mendeley. Six documented deviations were forced by the dataset, all surfaced in the UI; the two that matter most: (a) the paper estimates its noise threshold above 250 Hz, which **does not exist** at Mendeley's 320 Hz sampling (160 Hz Nyquist), so the proportional top half (> fs/4) is used and the threshold may be overestimated; (b) fractal dimension uses the **Higuchi** estimator, because box-counting saturates at the point count on a 20–70-frame curve and returns a constant 1.0 regardless of shape. Features are computed on the **pre-bandpass** resampled signal — amfall's 0.5–80 Hz would empty the 30–max Hz entropy band and the noise-estimation band alike.
- **`session_builder.py`** — reconstructs a continuous multi-phase recording from the Mendeley `class_id` ("C") field, which is a scripted multi-phase protocol (see `Mendeley/Manuscript.pdf` Table 1): files sharing `(env, subject, class_id, trial)` are sequential activity phases of one physical session, split into separate CSVs. `CLASS_ACTIVITY_ORDER` gives the correct concatenation order per class (e.g. C1 = sit still → fall → lie down); `build_session()` resamples each phase independently then concatenates, synthesizing a continuous time axis (phase transitions are treated as instantaneous). No other code in the repo does this concatenation.

### Scripts (`scripts/`)

- `preprocess.py`, `featurization.py` — the two active DVC-pipeline stages (config-driven, see Commands above).
- `true_fall_labelling.py` / `true_activity_time.py` — newer, standalone moving-variance-based segment detectors that estimate actual activity/fall time ranges directly from raw CSI (independent of the preprocess/featurize pipeline), driven by `config/fall_labelling.yaml`. `true_activity_time.py` generalizes `true_fall_labelling.py` to arbitrary activities via `ACTIVITY_MAP`.
- `04_advanced_analysis.py` is superseded by the split `04a_raw_csi_visualization.py` (raw CSI plots across activities/environments) and `04b_moving_variance_analysis.py` (q-value/moving-variance fall segment estimation).
- `05_verify_fall_segments.py`, `06_moving_variance_verification.py` — visualize detected segments overlaid on raw CSI / moving-variance signals, for validating `true_fall_labelling.py` output.
- `run_activity_analysis.py` — orchestrates 04/05/06 per activity, writing results under `results/analysis/{activity_name}/`.
- `streamlit_app/` — interactive 3-page app for exploring moving-variance/subcarrier-selection parameters live (Page 1: tune resample/filter/omega/n_streams/antennas against a reconstructed multi-phase session; Page 2: replay that session as dummy real-time data; Page 3: batch-run selection across a sample of sessions to check whether antenna/environment/experiment-protocol systematically affect which subcarriers get selected, via `src/dwt_coef/selection_stats.py` + scipy.stats tests). Run with `streamlit run scripts/streamlit_app/app.py`. Uses `src/dwt_coef/session_builder.py` to reconstruct a continuous session from the per-activity-phase CSVs sharing one `(env, subject, class_id, trial)`.

- `streamlit_domain_shift_app/` — 5-page domain-shift explorer for the environment (E1/E2/E3) × activity (fall/non-fall) axes. Page order *is* the analysis narrative: (1) dataset structure + the irreducible env↔subject confound + stratified sampling shared by all later pages; (2) acquisition-layer shift — packet timing, AGC, RSSI→dBm — a cheap `usecols` pass (~25 ms/file, no complex-CSI parse); (3) CSI amplitude profiles per environment; (4) **q-value through each amfall stage** (raw → resampled → filtered → stream selection → PCA → representative signal), single-file trace plus batch; (5) **FallDeFi Table 1 features** — spectrogram + power-burst-curve trace for one file, then env-gap vs class-gap for the Original vs Selected feature sets, testing the paper's environment-robustness claim; (6) divergence matrix, env-gap vs class-gap across all metric blocks, and an env-classifier probe. Pages 3–5 share one `probe_file` result per file, so running any one fills the cache for all three. Run with `streamlit run scripts/streamlit_domain_shift_app/app.py`.
  - Caching is a **file-level parquet** under `results/analysis/domain_shift/cache/` keyed by `(filepath, params_hash)`, *not* `@st.cache_data(persist="disk")` — the latter keys on the whole file list, so nudging the sample-size slider would discard all prior work. `params_*.json` (one dir up) decodes each hash and is git-tracked; the parquet cache is gitignored.
  - Chart titles are **not** drawn inside the figure — a plotly `layout.title` sits in the same band as the top horizontal legend and covers the legend labels. Builders pass the title through `layout.meta["title"]` and `lib/render.py:chart()` renders it as markdown above the chart (plus an optional caption below). `plotting.py` therefore stays a pure `go.Figure` module; `cache.py` and `render.py` are the only two `lib` modules that import streamlit.
  - Uses `use_container_width=True`, **not** the `width="stretch"` idiom of the other two apps: the installed Streamlit is 1.44.1, where `st.button(width=...)` raises `TypeError` and `st.plotly_chart(width=...)` is silently swallowed into `**kwargs`. (This means `scripts/streamlit_app/pages/subcarrier_stats.py`'s run button currently crashes on this environment.)

### Config files (`config/`)

Each pipeline/script reads its own YAML (passed via `--config`); DVC params in `dvc.yaml` mirror these keys exactly, so if you add/rename a config key used as a `dvc.yaml` param, update both.

- `preprocess.yaml` — data selection (env/subjects/activities/fall_only), windowing, resampling, bandpass filter, stream/PC selection thresholds.
- `featurization.yaml` — DWT wavelet/level/entropy toggle, plus an independent post-hoc filter on which preprocessed files to featurize.
- `fall_labelling.yaml` — moving-variance window (`omega`, `n_streams`) and segment-detection thresholds (`norm_threshold`, `min_duration`, `merge_threshold`, `max_duration`) used by `true_fall_labelling.py` / `true_activity_time.py`.

### Notebooks (`notebooks/`)

Meant to be run in order against the pipeline outputs: `01_window.ipynb` (raw data exploration) → `02_preprocess.ipynb` (filtering/stream-selection/PCA/q-value analysis) → `03_feature.ipynb` (DWT energy distribution comparisons across activity/env/subject). `notebooks/lagacy/` holds superseded versions.

### Notes

- `Mendeley/` raw data and `data/preprocessed/`, `data/features/` are gitignored; they're pulled via `dvc pull` and regenerated via `dvc repro`, not committed to git.
- Several top-level `*_COMPLETE.md` / `*_CHECKLIST.md` files are point-in-time status write-ups from earlier work sessions — treat them as historical notes, not living documentation; prefer reading the current code/config over these when they might conflict.
