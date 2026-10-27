"""Terrain and building obstruction for the coverage model.

From each mast, rays every 0.5 degrees sample the 5 m LiDAR surface (ground + buildings + trees) every
20 m. Along a ray, the steepest climb seen so far is the horizon; a receiver below its horizon is
obstructed, and the loss is single knife-edge diffraction over that horizon (ITU-R P.526):

    v = h * sqrt(2 d / (lambda d1 d2)),   J(v) = 6.9 + 20 log10(sqrt((v - 0.1)^2 + 1) + v - 0.1)

h is the obstacle's height above the mast-receiver line, d1 and d2 its distances from either end. The
loss grows with frequency, so 3.5 GHz 5G drops behind obstacles that 800 MHz 4G passes. Obstacles within
CLEAR_M of the receiver are ignored: that is the receiver's own building, which the indoor threshold covers.
"""
import numpy as np
import rasterio
from scipy.ndimage import map_coordinates

from config import EXTRACTS

STEP_M = 20
R_MAX = 9000
N_AZ = 720
RX_M = 1.5          # receiver above ground
MAST_M = 25.0       # antenna above ground for a ground-based mast...
ROOF_M = 4.0        # ...or above the roof, if the mast stands on a taller building
CLEAR_M = 40.0
MAX_LOSS_DB = 40.0


class Surface:
    def __init__(self):
        with rasterio.open(EXTRACTS / "dsm_5m.tif") as s, rasterio.open(EXTRACTS / "dtm_5m.tif") as t:
            self.dsm, self.dtm, tf = s.read(1), t.read(1), s.transform
        self.x0, self.y0, self.cell = tf.c, tf.f, tf.a
        self.r = np.arange(1, R_MAX // STEP_M + 1) * STEP_M
        az = np.arange(N_AZ) * 2 * np.pi / N_AZ
        self.sin, self.cos = np.sin(az)[:, None], np.cos(az)[:, None]

    def sample(self, a, x, y):
        rows, cols = (self.y0 - np.asarray(y)) / self.cell - 0.5, (np.asarray(x) - self.x0) / self.cell - 0.5
        return map_coordinates(a, [rows, cols], order=1, mode="nearest")

    def mast_height(self, x, y):
        """Antenna height above sea level at an estimated mast position."""
        return float(max(self.sample(self.dtm, [x], [y])[0] + MAST_M, self.sample(self.dsm, [x], [y])[0] + ROOF_M))

    def horizon(self, x, y, h):
        """Steepest climb from (x, y, h) seen so far along each ray, and the distance where it occurs."""
        X, Y = x + self.r * self.sin, y + self.r * self.cos
        slope = (self.sample(self.dsm, X.ravel(), Y.ravel()).reshape(X.shape) - h) / self.r
        top = np.maximum.accumulate(slope, axis=1)
        at = np.where(slope >= top, np.arange(len(self.r)), 0)
        return top.astype(np.float32), self.r[np.maximum.accumulate(at, axis=1)].astype(np.float32)


def obstruction(hz, x, y, h, px, py, pz):
    """Knife-edge geometry G for receivers (px, py, pz): v = G * sqrt(f_MHz / 300). -inf when clear."""
    top, d1 = hz
    dx, dy = px - x, py - y
    r = np.hypot(dx, dy)
    a = np.round(np.mod(np.arctan2(dx, dy), 2 * np.pi) / (2 * np.pi / N_AZ)).astype(int) % N_AZ
    i = np.clip(np.floor((r - CLEAR_M) / STEP_M).astype(int) - 1, -1, top.shape[1] - 1)
    ok = i >= 0
    a, i = a[ok], i[ok]
    t, d = top[a, i], d1[a, i]
    rr = r[ok]
    hgt = d * (t - (pz[ok] - h) / rr)             # obstacle height above the mast-receiver line
    d2 = np.maximum(rr - d, 1.0)
    g = np.full(r.shape, -np.inf, np.float32)
    g[ok] = hgt * np.sqrt(2 * rr / (d * d2))
    return g


def knife_edge_db(g, f_mhz):
    v = g * np.sqrt(f_mhz / 300.0)
    loss = 6.9 + 20 * np.log10(np.sqrt((v - 0.1) ** 2 + 1) + v - 0.1)
    return np.where(v > -0.78, np.minimum(loss, MAX_LOSS_DB), 0.0)


def channel_mhz(ch, tech):
    """Carrier frequency from an LTE EARFCN or a 5G NR-ARFCN (downlink)."""
    ch = np.asarray(ch, dtype=float)
    if tech == "5G":
        return np.where(ch < 600000, ch * 0.005, 3000 + (ch - 600000) * 0.015)
    bands = [(0, 600, 2110, 0), (1200, 1950, 1805, 1200), (2750, 3450, 2620, 2750), (3450, 3800, 925, 3450),
             (6150, 6450, 791, 6150), (9210, 9660, 1452, 9210), (9870, 10360, 1452, 9870),
             (37750, 38250, 2570, 37750), (38650, 39650, 2300, 38650)]
    out = np.full(ch.shape, 2100.0)
    for lo, hi, f0, n0 in bands:
        m = (ch >= lo) & (ch < hi)
        out[m] = f0 + 0.1 * (ch[m] - n0)
    return out
