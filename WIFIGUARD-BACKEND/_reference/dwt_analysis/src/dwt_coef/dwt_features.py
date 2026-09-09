"""
DWT (Discrete Wavelet Transform) Feature Extraction

Extracts multi-resolution energy features from preprocessed PC signals.
Uses Daubechies-4 (db4) wavelet with 5 decomposition levels.

Pipeline:
1. DWT decomposition (multi-resolution analysis)
2. Energy coefficient extraction (per-level energy)
3. Feature aggregation (scalogram, feature vector, etc.)
"""

from typing import Dict, List, Optional, Tuple

import numpy as np
import pywt


def dwt_decompose(
    signal: np.ndarray,
    wavelet: str = "db4",
    level: int = 5
) -> Dict[str, any]:
    """
    Perform multi-resolution DWT decomposition.

    Args:
        signal: 1D signal, shape (N,)
        wavelet: Wavelet type (default: 'db4' = Daubechies-4)
        level: Decomposition levels (default: 5)

    Returns:
        dict with keys:
        - 'approx': Approximation coefficients at max level
        - 'details': List of detail coefficients [cD1, cD2, ..., cD_level]
        - 'coefficients': Dict with all coefficients {cA, cD1, cD2, ...}
    """
    signal = np.asarray(signal, dtype=np.float32)

    coeffs = pywt.wavedec(signal, wavelet, level=level)

    cA = coeffs[0]
    details = coeffs[1:]

    coefficients_dict = {"cA": cA}
    for i, cD in enumerate(details, start=1):
        coefficients_dict[f"cD{i}"] = cD

    return {
        "approx": cA,
        "details": details,
        "coefficients": coefficients_dict,
        "wavelet": wavelet,
        "level": level,
    }


def compute_energy(coefficients_dict: Dict[str, np.ndarray]) -> Dict[str, float]:
    """
    Compute energy (L2-norm squared) of each coefficient.

    Energy: E(c) = Σ c_i²

    Args:
        coefficients_dict: Dict from dwt_decompose()['coefficients']

    Returns:
        dict with keys {'cA', 'cD1', 'cD2', ..., 'total'}
    """
    energy = {}
    total_energy = 0.0

    for key, coeff in coefficients_dict.items():
        coeff = np.asarray(coeff, dtype=np.float32)
        e = float(np.sum(coeff ** 2))
        energy[key] = e
        total_energy += e

    energy["total"] = total_energy

    return energy


def compute_energy_entropy(
    signal: np.ndarray,
    wavelet: str = "db4",
    level: int = 5
) -> Dict[str, any]:
    """
    Compute energy and Shannon entropy of DWT coefficients.

    Energy distribution can indicate signal activity level.
    Entropy measures uniformity of energy across frequency bands.

    Args:
        signal: 1D signal, shape (N,)
        wavelet: Wavelet type (default: 'db4')
        level: Decomposition levels (default: 5)

    Returns:
        dict with keys:
        - 'energy': Dict of energies {cA, cD1, cD2, ..., total}
        - 'normalized_energy': Energy divided by total (probabilities)
        - 'entropy': Shannon entropy H = -Σ p_i * log2(p_i)
    """
    decomp = dwt_decompose(signal, wavelet, level)
    energy_dict = compute_energy(decomp["coefficients"])

    total_energy = energy_dict["total"]
    if total_energy < 1e-10:
        normalized = {key: 0.0 for key in energy_dict}
        entropy = 0.0
    else:
        normalized = {}
        entropy = 0.0
        for key, e in energy_dict.items():
            if key == "total":
                continue
            p = e / total_energy
            normalized[key] = p
            if p > 1e-10:
                entropy -= p * np.log2(p)

    normalized["total"] = 1.0

    return {
        "energy": energy_dict,
        "normalized_energy": normalized,
        "entropy": float(entropy),
    }


def extract_scalogram(
    signal: np.ndarray,
    wavelet: str = "db4",
    level: int = 5
) -> np.ndarray:
    """
    Extract multi-resolution scalogram for visualization.

    Scalogram: (level+1, max_len) matrix where rows are decomposition levels.
    Rows ordered: [cA (bottom, low freq), cD_level, ..., cD1 (top, high freq)]

    Args:
        signal: 1D signal, shape (N,)
        wavelet: Wavelet type (default: 'db4')
        level: Decomposition levels (default: 5)

    Returns:
        Normalized scalogram, shape (level+1, N_max)
        Each row: normalized magnitude of one coefficient
    """
    decomp = dwt_decompose(signal, wavelet, level)
    coeffs_dict = decomp["coefficients"]

    max_len = len(signal)

    rows = []
    coeff_order = ["cA"] + [f"cD{i}" for i in range(level, 0, -1)]

    for key in coeff_order:
        coeff = np.asarray(coeffs_dict[key], dtype=np.float32)

        coeff_normalized = (coeff - np.min(coeff)) / (
            np.max(coeff) - np.min(coeff) + 1e-10
        )

        if len(coeff) != max_len:
            indices = np.linspace(0, len(coeff) - 1, max_len)
            coeff_interp = np.interp(indices, np.arange(len(coeff)), coeff_normalized)
        else:
            coeff_interp = coeff_normalized

        rows.append(coeff_interp)

    scalogram = np.stack(rows, axis=0).astype(np.float32)

    return scalogram


def extract_all_pc_features(
    pcs: np.ndarray,
    wavelet: str = "db4",
    level: int = 5,
    include_entropy: bool = True
) -> np.ndarray:
    """
    Extract DWT energy features from multiple PCs.

    For each PC, compute energy of cA and all cD levels, optionally add entropy.

    Args:
        pcs: Array of PC signals, shape (N_samples, n_pcs)
        wavelet: Wavelet type (default: 'db4')
        level: Decomposition levels (default: 5)
        include_entropy: If True, add Shannon entropy as last column

    Returns:
        Feature matrix, shape (n_pcs, n_features)
        where n_features = (level+1) if not entropy, else (level+2)
        Columns: E_cA, E_cD1, E_cD2, ..., E_cDlevel, [entropy]
    """
    pcs = np.asarray(pcs, dtype=np.float32)

    if pcs.ndim == 1:
        pcs = pcs.reshape(-1, 1)

    n_pcs = pcs.shape[1]

    n_features = (level + 1)
    if include_entropy:
        n_features += 1

    features = np.zeros((n_pcs, n_features), dtype=np.float32)

    for pc_idx in range(n_pcs):
        pc = pcs[:, pc_idx]

        result = compute_energy_entropy(pc, wavelet, level)
        energy_dict = result["energy"]
        entropy = result["entropy"]

        col_idx = 0

        features[pc_idx, col_idx] = energy_dict["cA"]
        col_idx += 1

        for i in range(1, level + 1):
            key = f"cD{i}"
            features[pc_idx, col_idx] = energy_dict[key]
            col_idx += 1

        if include_entropy:
            features[pc_idx, col_idx] = entropy

    return features


def dwt_reconstruct(
    coefficients_dict: Dict[str, np.ndarray],
    wavelet: str = "db4"
) -> np.ndarray:
    """
    Reconstruct signal from DWT coefficients.

    Args:
        coefficients_dict: Dict from dwt_decompose()['coefficients']
        wavelet: Wavelet type (must match decomposition)

    Returns:
        Reconstructed signal, shape (N,)
    """
    level = max([int(key[2:]) for key in coefficients_dict if key.startswith("cD")])

    coeffs_list = [coefficients_dict["cA"]]
    for i in range(1, level + 1):
        coeffs_list.append(coefficients_dict[f"cD{i}"])

    reconstructed = pywt.waverec(coeffs_list, wavelet)

    return reconstructed.astype(np.float32)


def compute_reconstruction_error(
    signal: np.ndarray,
    reconstructed: np.ndarray
) -> Dict[str, float]:
    """
    Compute error metrics between original and reconstructed signals.

    Useful for validating DWT decomposition/reconstruction.

    Args:
        signal: Original signal
        reconstructed: Reconstructed signal

    Returns:
        dict with keys:
        - 'mse': Mean Squared Error
        - 'rmse': Root Mean Squared Error
        - 'mae': Mean Absolute Error
        - 'snr_db': Signal-to-Noise Ratio in dB
    """
    signal = np.asarray(signal, dtype=np.float32)
    reconstructed = np.asarray(reconstructed, dtype=np.float32)

    if len(signal) != len(reconstructed):
        reconstructed = reconstructed[: len(signal)]

    error = signal - reconstructed

    mse = float(np.mean(error ** 2))
    rmse = float(np.sqrt(mse))
    mae = float(np.mean(np.abs(error)))

    signal_power = float(np.mean(signal ** 2))
    noise_power = mse
    snr_db = 10 * np.log10(signal_power / (noise_power + 1e-10))

    return {
        "mse": mse,
        "rmse": rmse,
        "mae": mae,
        "snr_db": snr_db,
    }
