import numpy as np
import pandas as pd

__version__ = "0.1.0"

DAY = 86400
HOUR = 3600

REAL_TIME_MIN_SPAN = pd.Timedelta("2D")
GRID_MIN_STEP = 1e-4
GRID_MIN_FIT_SHARE = 0.98       # ยอมให้ค่าราว 2% หลุดกริด (เช่นค่าผิดปกติ) โดยยังนับว่าเป็นข้อมูลขั้นบันได
PERIOD_SEARCH_HALF_WINDOW = 12 * DAY
PERIOD_MIN_POINTS = 50
PERIOD_RESAMPLE_STEP = 1800.0   # FFT ต้องการข้อมูลระยะห่างเท่ากัน
PERIOD_MIN_PEAK_TO_MEDIAN = 3
PLOT_MIN_PAD = 15


class FinderMiss:
    def __init__(self, df, time_col="datetime", value_col="value", time=None,
                 long_gap_h=6.0, period_band_h=(6, 48)):
        self.df = df.reset_index(drop=True)
        self.time_col, self.value_col = time_col, value_col
        self.time = self.df[time_col] if time is None else pd.Series(time).reset_index(drop=True)
        if len(self.time) != len(self.df):
            raise ValueError(f"time ยาว {len(self.time)} แถว แต่ df ยาว {len(self.df)} แถว")
        self.long_gap_h = long_gap_h
        self.period_band_h = period_band_h
        self._fitted = False

    @classmethod
    def from_csv(cls, path, time_col="datetime", value_col="value", time=None, **kw):
        return cls(pd.read_csv(path), time_col, value_col, time=time, **kw)

    def __repr__(self):
        if not self._fitted:
            return f"FinderMiss({len(self.df)} rows, not fitted)"
        period = f"{self.period_h:.1f}h" if self.period_h else None
        return (f"FinderMiss({len(self.df)} rows, time={'real' if self.is_real_time else 'reconstructed'}, "
                f"grid={self.grid}, period={period}, gaps={len(self.gaps)}, missing={int((~self.known).sum())})")

    def fit(self):
        self.time_s, self.is_real_time = _build_time(self.time)
        self.y = self.df[self.value_col].to_numpy(float)
        self.known = ~np.isnan(self.y)
        self.gaps = _gap_runs(self.known)
        self.grid = _detect_grid(self.y)
        period_s = None
        # เวลาที่สร้างจาก mm:ss อาจเพี้ยนไปทีละชั่วโมง คาบที่หาได้จึงเชื่อไม่ได้
        if self.is_real_time and self.gaps:
            gaps_center = self.time_s[~self.known].mean()
            period_s = _detect_period(self.time_s, self.y, self.known, gaps_center, self.period_band_h)
        self.period_h = period_s / HOUR if period_s else None
        self._fitted = True
        return self

    def _ensure_fit(self):
        if not self._fitted:
            self.fit()

    @property
    def methods(self):
        self._ensure_fit()
        out = ["linear"]
        if self.grid is not None:
            out.append("linear+snap")
        if self.period_h:
            out.append("harmonic")
            if self.grid is not None:
                out.append("harmonic+snap")
        return out

    def fill(self, method="auto"):
        filled, labels = self._fill_values(method)
        out = self.df.copy()
        out[self.value_col] = filled
        out["status"] = labels
        return out

    def _fill_values(self, method):
        self._ensure_fit()
        if method != "auto" and method not in self.methods:
            raise ValueError(f"method '{method}' ใช้ไม่ได้กับข้อมูลนี้ — ใช้ได้: {['auto'] + self.methods}")
        filled = self.y.copy()
        labels = np.full(len(self.y), "real", dtype=object)
        if not self.gaps:
            return filled, labels
        linear = _linear(self.time_s, self.y, self.known)
        for i0, i1 in self.gaps:
            base, snap = self._gap_method(method, i0, i1)
            gap = slice(i0, i1 + 1)
            if base == "harmonic":
                filled[gap] = _harmonic(self.time_s, self.y, self.known, i0, i1, self.period_h * HOUR)
            else:
                filled[gap] = linear[gap]
            if snap:
                filled[gap] = _snap(filled[gap], self.grid)
            labels[gap] = f"imputed_{base}" + ("_snap" if snap else "")
        return filled, labels

    def _gap_method(self, method, i0, i1):
        if method != "auto":
            base, _, suffix = method.partition("+")
            return base, suffix == "snap"
        span_h = (self.time_s[i1] - self.time_s[i0]) / HOUR
        # รูสั้น เส้นตรงก็พอ รูยาวเส้นตรงจะตัดผ่านรอบคลื่นที่หายไปทั้งรอบ
        base = "harmonic" if (self.period_h and span_h > self.long_gap_h) else "linear"
        return base, self.grid is not None

    def evaluate(self, truth, methods=None):
        self._ensure_fit()
        actual = self._as_truth(truth)
        scored = ~self.known & np.isfinite(actual)
        if not scored.any():
            raise ValueError("ไม่มีจุดในรูที่ truth มีค่าให้เทียบ")
        actual = actual[scored]
        total_variance = np.sum((actual - actual.mean()) ** 2)
        rows = []
        for method in methods or ["auto"] + self.methods:
            predicted = self._fill_values(method)[0][scored]
            error = predicted - actual
            rows.append(dict(method=method, n=int(scored.sum()),
                             MAE=np.mean(np.abs(error)),
                             RMSE=np.sqrt(np.mean(error ** 2)),
                             R2=1 - np.sum(error ** 2) / total_variance if total_variance > 0 else np.nan,
                             exact=np.mean(np.isclose(predicted, actual)) * 100))
        return pd.DataFrame(rows)

    def _as_truth(self, truth):
        if isinstance(truth, pd.DataFrame):
            values = truth[self.value_col]
        elif isinstance(truth, str) or hasattr(truth, "__fspath__"):
            values = pd.read_csv(truth)[self.value_col]
        else:
            values = truth
        values = np.asarray(values, dtype=float)
        if len(values) != len(self.y):
            raise ValueError(f"truth ยาว {len(values)} แถว แต่ข้อมูลยาว {len(self.y)} แถว")
        return values

    def plot(self, truth=None, methods=None, save=None, show=True, title=None):
        import matplotlib.pyplot as plt

        self._ensure_fit()
        methods = methods or self.methods
        truth_values = None if truth is None else self._as_truth(truth)
        scores = None
        if truth_values is not None and self.gaps:
            scores = self.evaluate(truth_values, methods).set_index("method")

        ncol = 2 if len(methods) > 1 else 1
        nrow = int(np.ceil(len(methods) / ncol))
        fig, axes = plt.subplots(nrow, ncol, figsize=(7 * ncol, 4 * nrow),
                                 sharex=True, sharey=True, squeeze=False)
        window = self._plot_window()
        for ax, method in zip(axes.flat, methods):
            score = None if scores is None else scores.loc[method]
            self._draw_method(ax, method, truth_values, window, score)
        for ax in axes.flat[len(methods):]:
            ax.axis("off")
        for ax in axes[-1]:
            ax.set_xlabel("record #")
        if title:
            fig.suptitle(title, fontsize=12)
        fig.tight_layout()
        if save:
            fig.savefig(save, dpi=120)
        if show:
            plt.show()
        else:
            plt.close(fig)
        return fig

    def _plot_window(self):
        missing = np.flatnonzero(~self.known)
        if not len(missing):
            return slice(None)
        pad = max(len(missing), PLOT_MIN_PAD)
        return slice(max(0, missing[0] - pad), min(len(self.y), missing[-1] + pad + 1))

    def _draw_method(self, ax, method, truth, window, score):
        filled, _ = self._fill_values(method)
        idx = np.arange(len(self.y))
        observed = self.known[window]
        if truth is not None:
            ax.plot(idx[window], truth[window], "-", color="0.6", lw=1, label="truth")
        ax.plot(idx[window][observed], self.y[window][observed], ".", color="tab:blue", ms=3, label="observed")
        # วาดทีละรู ไม่อย่างนั้นเส้นจะลากข้ามช่วงที่มีข้อมูลจริงระหว่างรู
        for k, (i0, i1) in enumerate(self.gaps):
            gap = slice(i0, i1 + 1)
            label = method if k == 0 else None
            ax.axvspan(i0, i1, color="orange", alpha=0.07)
            if method.endswith("snap"):
                ax.step(idx[gap], filled[gap], where="mid", color="tab:red", lw=1.8, label=label)
            else:
                ax.plot(idx[gap], filled[gap], "-", color="tab:red", lw=1.8, label=label)
        heading = method
        if score is not None:
            heading += f"\nMAE={score.MAE:.4f}  RMSE={score.RMSE:.4f}  R2={score.R2:.3f}"
        ax.set_title(heading, fontsize=10)
        ax.legend(fontsize=7)


def _build_time(datetime_col):
    text = datetime_col.astype(str)
    t = pd.to_datetime(text, format="ISO8601", errors="coerce")
    if t.notna().mean() > 0.5:
        if t.isna().any():
            t[t.isna()] = pd.to_datetime(text[t.isna()], format="mixed", dayfirst=True, errors="coerce")
        if (t.max() - t.min()) > REAL_TIME_MIN_SPAN:
            return (t - t.min()).dt.total_seconds().to_numpy(), True
    mmss = pd.to_datetime(text, format="%M:%S.%f", errors="coerce")
    # ไฟล์เก็บแค่นาที:วินาที ค่าที่ลดลงแปลว่าข้ามไปชั่วโมงถัดไป
    hours_passed = (mmss.diff() < pd.Timedelta(0)).cumsum()
    mmss = mmss + pd.to_timedelta(hours_passed, unit="h")
    return (mmss - mmss.min()).dt.total_seconds().to_numpy(), False


def _detect_grid(vals):
    v = vals[np.isfinite(vals)]
    distinct = np.unique(v)
    if len(distinct) < 3:
        return None
    steps = np.diff(distinct)
    step = np.min(steps[steps > 1e-9])
    multiples = v / step
    on_grid = np.abs(multiples - np.round(multiples)) < 1e-6
    if step >= GRID_MIN_STEP and np.mean(on_grid) > GRID_MIN_FIT_SHARE:
        return float(step)
    return None


def _detect_period(x, y, known, center, band_h):
    near = known & (np.abs(x - center) < PERIOD_SEARCH_HALF_WINDOW)
    if near.sum() < PERIOD_MIN_POINTS:
        return None
    xm, ym = x[near], y[near]
    grid_x = np.arange(xm.min(), xm.max(), PERIOD_RESAMPLE_STEP)
    grid_y = np.interp(grid_x, xm, ym)
    grid_y = grid_y - grid_y.mean()
    spectrum = np.abs(np.fft.rfft(grid_y * np.hanning(len(grid_y))))[1:]
    period_h = 1 / np.fft.rfftfreq(len(grid_x), PERIOD_RESAMPLE_STEP)[1:] / HOUR
    in_band = (period_h > band_h[0]) & (period_h < band_h[1])
    if not in_band.any():
        return None
    band_spectrum = spectrum[in_band]
    if band_spectrum.max() < PERIOD_MIN_PEAK_TO_MEDIAN * np.median(spectrum):
        return None
    return period_h[in_band][np.argmax(band_spectrum)] * HOUR


def _gap_runs(known):
    edges = np.diff(np.concatenate(([0], (~known).astype(int), [0])))
    starts = np.flatnonzero(edges == 1)
    ends = np.flatnonzero(edges == -1) - 1
    return list(zip(starts.tolist(), ends.tolist()))


def _linear(x, y, known):
    filled = y.copy()
    filled[~known] = np.interp(x[~known], x[known], y[known])
    return filled


def _harmonic(x, y, known, i0, i1, period, nharm=2, win_days=5, sigma=4):
    xg = x[i0:i1 + 1]
    center = xg.mean()

    def design(xx):
        cols = [np.ones_like(xx), (xx - center) / DAY]
        for h in range(1, nharm + 1):
            w = 2 * np.pi * xx / (period / h)
            cols += [np.sin(w), np.cos(w)]
        return np.column_stack(cols)

    # หน้าต่างกว้างกว่า win_days อีกหนึ่งคาบ+1 วัน เพื่อให้มีข้อมูลครบรอบคลื่นทั้งสองฝั่งของรู
    win = known & (np.abs(x - center) < (win_days + period / DAY + 1) * DAY)
    weight = np.exp(-((x[win] - center) / (sigma * DAY)) ** 2)
    coef, *_ = np.linalg.lstsq(design(x[win]) * weight[:, None], y[win] * weight, rcond=None)
    pred = design(xg) @ coef
    iL, iR = i0 - 1, i1 + 1
    # ไซน์ที่ฟิตได้ไม่ผ่านค่าจริงที่ขอบรูพอดี เลยเกลี่ยส่วนต่างที่สองขอบเป็นเส้นตรงให้ปลายต่อกันสนิท
    if iL >= 0 and iR < len(x):
        resL = y[iL] - (design(x[iL:iL + 1]) @ coef)[0]
        resR = y[iR] - (design(x[iR:iR + 1]) @ coef)[0]
        f = (xg - x[iL]) / (x[iR] - x[iL])
        pred = pred + resL * (1 - f) + resR * f
    return pred


def _snap(v, grid):
    return np.round(v / grid) * grid
