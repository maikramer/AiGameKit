import os
import random
import json
import numpy as np
import rasterio
from pyfastnoiselite.pyfastnoiselite import FastNoiseLite, NoiseType, FractalType
import torch
from terrain3d.vendor.inference.perlin_transform import build_quantiles, transform_perlin

_VENDOR_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA_DIR = os.path.join(_VENDOR_DIR, "data", "global")

WC_FILES = [
    os.path.join(_DATA_DIR, "wc2.1_10m_bio_1.tif"),
    os.path.join(_DATA_DIR, "wc2.1_10m_bio_4.tif"),
    os.path.join(_DATA_DIR, "wc2.1_10m_bio_12.tif"),
    os.path.join(_DATA_DIR, "wc2.1_10m_bio_15.tif"),
]
ETOPO_FILE = os.path.join(_DATA_DIR, "etopo_10m.tif")
WC_URL = "https://geodata.ucdavis.edu/climate/worldclim/2_1/base/wc2.1_10m_bio.zip"
ETOPO_URL = "https://raw.githubusercontent.com/xandergos/terrain-diffusion/main/data/global/etopo_10m.tif"
STATS_CACHE_PATH = os.path.join(_DATA_DIR, "synthetic_map_stats.json")


def _download_file(url: str, dest: str) -> None:
    import urllib.request

    print(f"  Downloading {url}...")
    urllib.request.urlretrieve(url, dest)


def _ensure_data_files() -> None:
    wc_missing = [f for f in WC_FILES if not os.path.exists(f)]
    etopo_missing = not os.path.exists(ETOPO_FILE)
    if not wc_missing and not etopo_missing:
        return

    import zipfile

    os.makedirs(_DATA_DIR, exist_ok=True)

    if wc_missing:
        print(f"Downloading WorldClim bioclim rasters ({len(wc_missing)} missing)...")
        zip_path = os.path.join(_DATA_DIR, "wc2.1_10m_bio.zip")
        _download_file(WC_URL, zip_path)
        print("  Extracting...")
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(_DATA_DIR)
        os.remove(zip_path)
        print("  WorldClim data ready.")

    if etopo_missing:
        print("Downloading ETOPO elevation raster...")
        _download_file(ETOPO_URL, ETOPO_FILE)
        print("  ETOPO data ready.")

def _compute_map_stats(frequency_mult, drop_water_pct):
    """Compute quantile-matching stats from global raster data and Perlin noise.

    Uses fixed deterministic seeds for noise generation since the noise
    distribution is seed-independent — only the frequency/octave parameters
    matter for quantile computation.
    """
    _ensure_data_files()

    elev_img = rasterio.open(ETOPO_FILE).read(1)
    temp_img = rasterio.open(WC_FILES[0]).read(1)
    temp_std_img = rasterio.open(WC_FILES[1]).read(1)
    precip_img = rasterio.open(WC_FILES[2]).read(1)
    precip_std_img = rasterio.open(WC_FILES[3]).read(1)

    def process_image(img):
        h = img.shape[0]
        crop_start = h // 6
        crop_end = h - h // 6
        img = img[crop_start:crop_end, :]
        img[img < -30000] = np.nan
        return img

    elev_img = process_image(elev_img)
    temp_img = process_image(temp_img)
    temp_std_img = process_image(temp_std_img)
    precip_img = process_image(precip_img)
    precip_std_img = process_image(precip_std_img)

    valid_mask = ~np.isnan(temp_img)
    temp_flat = temp_img[valid_mask]
    temp_std_flat = temp_std_img[valid_mask]

    coeffs = np.polyfit(temp_flat, temp_std_flat, 1)
    a_temp_std, b_temp_std = coeffs[0], coeffs[1]

    temp_std_img = temp_std_img - (a_temp_std * temp_img + b_temp_std)

    lapse = (-6.5 + 0.0015 * precip_img).clip(-9.8, -4.0) / 1000
    temp_img = temp_img - lapse * np.maximum(0, elev_img)

    temp_std_p1 = np.percentile(temp_std_img[valid_mask], 0.1)
    temp_std_p99 = np.percentile(temp_std_img[valid_mask], 99.9)

    def compute_quantiles(base_image, frequency, octaves, lacunarity, gain, seed, base_image_mask=None):
        noise = FastNoiseLite(seed=seed)
        noise.noise_type = NoiseType.NoiseType_Perlin
        noise.frequency = frequency
        noise.fractal_type = FractalType.FractalType_FBm
        noise.fractal_octaves = octaves
        noise.fractal_lacunarity = lacunarity
        noise.fractal_gain = gain

        size = 32 * 1024
        x = np.arange(0, size, 32, dtype=np.float32)
        y = np.arange(0, size, 32, dtype=np.float32)
        xx, yy = np.meshgrid(x, y)
        coords = np.array([xx.flatten(), yy.flatten()], dtype=np.float32)

        noise_q = build_quantiles(noise.gen_from_coords(coords).flatten(), n_quantiles=64, eps=1e-4)
        if base_image_mask is not None:
            base_q = build_quantiles(base_image[base_image_mask].flatten(), n_quantiles=64, eps=1e-4)
        else:
            base_q = build_quantiles(base_image.flatten(), n_quantiles=64, eps=1e-4)
        return noise_q, base_q

    fixed_seeds = [1, 2, 3, 4, 5]
    rng = np.random.default_rng(0)
    hist_mask = np.logical_or(rng.random((elev_img.shape[0], elev_img.shape[1])) > drop_water_pct, elev_img >= 0)
    maps = [
        (elev_img,        0.05 * frequency_mult[0], 4, 2.0, 0.5, fixed_seeds[0], hist_mask),
        (temp_img,        0.05 * frequency_mult[1], 2, 2.0, 0.5, fixed_seeds[1], None),
        (temp_std_img,    0.05 * frequency_mult[2], 4, 2.0, 0.5, fixed_seeds[2], None),
        (precip_img,      0.05 * frequency_mult[3], 4, 2.0, 0.5, fixed_seeds[3], None),
        (precip_std_img,  0.05 * frequency_mult[4], 4, 2.0, 0.5, fixed_seeds[4], None),
    ]
    all_noise_q, all_base_q = zip(*[compute_quantiles(*args) for args in maps])

    stats = {
        'a_temp_std': np.float64(a_temp_std),
        'b_temp_std': np.float64(b_temp_std),
        'temp_std_p1': np.float64(temp_std_p1),
        'temp_std_p99': np.float64(temp_std_p99),
    }
    for i, (nq, bq) in enumerate(zip(all_noise_q, all_base_q)):
        stats[f'noise_quantiles_{i}'] = nq
        stats[f'base_image_quantiles_{i}'] = bq
    return stats

def _load_stats_cache():
    """Load synthetic map stats from JSON cache."""
    if not os.path.exists(STATS_CACHE_PATH):
        return None
    try:
        with open(STATS_CACHE_PATH, "r", encoding="utf-8") as cache_file:
            data = json.load(cache_file)
        noise_quantile_tables = data["noise_quantile_tables"]
        data_quantile_tables = data["data_quantile_tables"]
        stats = {
            "a_temp_std": float(data["a_temp_std"]),
            "b_temp_std": float(data["b_temp_std"]),
            "temp_std_p1": float(data["temp_std_p1"]),
            "temp_std_p99": float(data["temp_std_p99"]),
        }
        for index, quantile_table in enumerate(noise_quantile_tables):
            stats[f"noise_quantiles_{index}"] = np.asarray(quantile_table, dtype=np.float64)
        for index, quantile_table in enumerate(data_quantile_tables):
            stats[f"base_image_quantiles_{index}"] = np.asarray(quantile_table, dtype=np.float64)
        print("Synthetic map stats cache hit.")
        return stats
    except Exception:
        print("Synthetic map stats cache unreadable. Recomputing.")
    return None

def _save_stats_cache(stats):
    """Save synthetic map stats cache as plain JSON."""
    os.makedirs(_DATA_DIR, exist_ok=True)
    noise_quantile_tables = []
    data_quantile_tables = []
    quantile_index = 0
    while f"noise_quantiles_{quantile_index}" in stats and f"base_image_quantiles_{quantile_index}" in stats:
        noise_quantile_tables.append(np.asarray(stats[f"noise_quantiles_{quantile_index}"], dtype=np.float64).tolist())
        data_quantile_tables.append(np.asarray(stats[f"base_image_quantiles_{quantile_index}"], dtype=np.float64).tolist())
        quantile_index += 1

    payload = {
        "n_quantiles": int(len(noise_quantile_tables[0])) if noise_quantile_tables else 0,
        "noise_quantile_tables": noise_quantile_tables,
        "data_quantile_tables": data_quantile_tables,
        "a_temp_std": float(stats["a_temp_std"]),
        "b_temp_std": float(stats["b_temp_std"]),
        "temp_std_p1": float(stats["temp_std_p1"]),
        "temp_std_p99": float(stats["temp_std_p99"]),
    }
    with open(STATS_CACHE_PATH, "w", encoding="utf-8") as cache_file:
        json.dump(payload, cache_file)

def make_synthetic_map_factory(frequency_mult=[1.0, 1.0, 1.0, 1.0, 1.0], seed=None, drop_water_pct=0.0):
    actual_seeds = [((seed or random.randint(0, 2**30)) + i + 1) & 0x7FFFFFFF for i in range(5)]

    stats = _load_stats_cache()
    if stats is None:
        stats = _compute_map_stats(frequency_mult, drop_water_pct)
        _save_stats_cache(stats)

    a_temp_std = float(stats['a_temp_std'])
    b_temp_std = float(stats['b_temp_std'])
    temp_std_p1 = float(stats['temp_std_p1'])
    temp_std_p99 = float(stats['temp_std_p99'])

    def build_synthetic_map(frequency, octaves, lacunarity, gain, seed, noise_quantiles, base_image_quantiles):
        noise = FastNoiseLite(seed=seed)
        noise.noise_type = NoiseType.NoiseType_Perlin
        noise.frequency = frequency
        noise.fractal_type = FractalType.FractalType_FBm
        noise.fractal_octaves = octaves
        noise.fractal_lacunarity = lacunarity
        noise.fractal_gain = gain
        transform_fn = lambda x: transform_perlin(x, noise_quantiles, base_image_quantiles)
        return noise, transform_fn

    def sample_synthetic_map(noise, transform_fn, i1, j1, i2, j2):
        x = np.arange(i1, i2, dtype=np.float32)
        y = np.arange(j1, j2, dtype=np.float32)
        xx, yy = np.meshgrid(x, y)

        Xs = xx.flatten()
        Ys = yy.flatten()
        coords = np.array([Xs, Ys], dtype=np.float32)

        noise_values = noise.gen_from_coords(coords)
        transformed_values = transform_fn(noise_values)
        return transformed_values.reshape(i2 - i1, j2 - j1)

    map_configs = [
        (0.05 * frequency_mult[0], 4, 2.0, 0.5),
        (0.05 * frequency_mult[1], 2, 2.0, 0.5),
        (0.05 * frequency_mult[2], 4, 2.0, 0.5),
        (0.05 * frequency_mult[3], 4, 2.0, 0.5),
        (0.05 * frequency_mult[4], 4, 2.0, 0.5),
    ]
    synthetic_params = [
        build_synthetic_map(*cfg, actual_seeds[i], stats[f'noise_quantiles_{i}'], stats[f'base_image_quantiles_{i}'])
        for i, cfg in enumerate(map_configs)
    ]
    synthetic_elev_params, synthetic_temp_params, synthetic_temp_std_params, synthetic_precip_params, synthetic_precip_std_params = synthetic_params

    def finalize_synthetic_map(raw_map):
        synthetic_elev = np.asarray(raw_map[0], dtype=np.float32)
        synthetic_temp = np.asarray(raw_map[1], dtype=np.float32)
        synthetic_temp_std = np.asarray(raw_map[2], dtype=np.float32)
        synthetic_precip = np.asarray(raw_map[3], dtype=np.float32)
        synthetic_precip_std = np.asarray(raw_map[4], dtype=np.float32)

        lapse_rate = (-6.5 + 0.0015 * synthetic_precip).clip(-9.8, -4.0) / 1000
        synthetic_temp = synthetic_temp + lapse_rate * np.maximum(0, synthetic_elev)
        synthetic_temp = np.clip(synthetic_temp, -10, 40)
        # Stretch sub-20 °C values (empirical tweak): spreads cold vs warm mass in the map
        # so desert and tropical regions read more distinctly. Above 20 °C unchanged;
        # below, affine expand around the 20 °C pivot by 1.25×.
        synthetic_temp = np.where(synthetic_temp > 20, synthetic_temp, (synthetic_temp - 20) * 1.25 + 20)

        t = (synthetic_temp_std - temp_std_p1) / (temp_std_p99 - temp_std_p1)
        baseline = np.maximum(temp_std_p1, -(a_temp_std * synthetic_temp + b_temp_std))
        synthetic_temp_std = t * (temp_std_p99 - baseline) + baseline
        synthetic_temp_std = synthetic_temp_std + (a_temp_std * synthetic_temp + b_temp_std)
        synthetic_temp_std = np.maximum(synthetic_temp_std, 20)

        synthetic_precip_std = synthetic_precip_std * np.maximum(0, (185 - 0.04111 * synthetic_precip) / 185)
        return np.stack([synthetic_elev, synthetic_temp, synthetic_temp_std, synthetic_precip, synthetic_precip_std], axis=0)

    def sample_raw_synthetic_map(i1, j1, i2, j2):
        synthetic_elev = sample_synthetic_map(*synthetic_elev_params, i1, j1, i2, j2)
        synthetic_temp = sample_synthetic_map(*synthetic_temp_params, i1, j1, i2, j2)
        synthetic_temp_std = sample_synthetic_map(*synthetic_temp_std_params, i1, j1, i2, j2)
        synthetic_precip = sample_synthetic_map(*synthetic_precip_params, i1, j1, i2, j2)
        synthetic_precip_std = sample_synthetic_map(*synthetic_precip_std_params, i1, j1, i2, j2)
        return np.stack([synthetic_elev, synthetic_temp, synthetic_temp_std, synthetic_precip, synthetic_precip_std], axis=0)

    def sample_full_synthetic_map(i1, j1, i2, j2):
        synthetic_map = finalize_synthetic_map(sample_raw_synthetic_map(i1, j1, i2, j2))
        synthetic_map[0] = np.sign(synthetic_map[0]) * np.sqrt(np.abs(synthetic_map[0]))
        return torch.from_numpy(synthetic_map).float()

    sample_full_synthetic_map.sample_raw = sample_raw_synthetic_map
    sample_full_synthetic_map.finalize = finalize_synthetic_map
    return sample_full_synthetic_map