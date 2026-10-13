# findermiss

Fill missing values in time series. For each gap, `findermiss` picks between straight-line
interpolation and a fitted daily-cycle wave, and can snap the result to the step size of
stepped (quantised) sensor data.

## Why this exists

`findermiss` started with a plain problem: we wanted to analyse readings from monitoring
stations, but some stretches of data were missing, and the analysis needed a complete series.

Short gaps were easy, a straight line between the neighbouring readings was good enough.
Long gaps were not. One station lost about 38 hours of a signal that rises and falls every
day, and linear interpolation drew a flat line straight through about one and a half cycles
(R² = −0.74 against the real values, worse than just using the average). Fitting the
station's own daily wave from the data around the gap and joining it to both edges brought
that up to R² = 0.72.

Other stations reported in fixed steps (e.g. 0.01), so filled values that land between
steps looked fake. Rounding them to the step size made the fill match the real readings
exactly at most points.

`findermiss` puts those three ideas together and decides gap by gap which one to use.

## Install

```
pip install findermiss          # numpy, pandas
pip install findermiss[plot]    # + matplotlib, for FinderMiss.plot()
```

Requires pandas 2.0 or newer.

## Quick start

```python
from findermiss import FinderMiss

fm = FinderMiss.from_csv("station_missing.csv", time_col="datetime", value_col="value")
filled = fm.fill()                       # "auto": chooses a method for each gap
filled.to_csv("station_filled.csv", index=False)
```

Missing values must be empty / `NaN` in the value column. `fill()` returns a copy of your
data with the gaps filled and a `status` column telling where each value came from:

| status | meaning |
|---|---|
| `real` | original value, untouched |
| `imputed_linear` | straight line between the values on both sides of the gap |
| `imputed_harmonic` | fitted wave (dominant period + its half) with trend, joined smoothly to both sides |
| `..._snap` suffix | rounded to the data's step size |

## Methods

```python
fm.fit()          # optional; runs automatically on first use
print(fm)         # FinderMiss(6455 rows, time=real, grid=0.009999999999999787, period=25.0h, gaps=1, missing=287)
fm.methods        # methods usable on this data, e.g. ['linear', 'linear+snap', 'harmonic', 'harmonic+snap']
fm.fill("harmonic+snap")
```

- **linear** is always available.
- **harmonic** is available when the timestamps are real dates covering more than 2 days
  and a clear cycle between 6 and 48 hours is found near the gaps.
- **+snap** is available when nearly all values are multiples of one step size (stepped data).
- **auto** (default) uses harmonic for gaps longer than `long_gap_h` (6 hours) when a cycle
  was found, linear otherwise, and snaps whenever a step size was found.

Timestamps can be full dates, or `mm:ss` only — in that case time is rebuilt by counting
each time the minutes wrap back to zero as one hour.

## Measuring accuracy

If you have the complete data (e.g. you removed values on purpose to test), compare every
method on the gap points only:

```python
fm.evaluate("station_complete.csv")   # path, DataFrame or array, same row order
#           method    n     MAE    RMSE      R2   exact
#             auto  287  0.1430  0.1902  0.7193  2.4390
#           linear  287  0.3934  0.4741 -0.7441  0.0000
#   ...
```

`exact` is the percentage of gap points filled with the right value (within floating-point
tolerance, `numpy.isclose`).

## Plot

```python
fm.plot(truth="station_complete.csv", save="compare.png", show=False)
```

One panel per method, zoomed in around the gaps. Needs `findermiss[plot]`.

## Options

```python
FinderMiss(df, time_col="datetime", value_col="value",
           time=None,             # use these timestamps instead of df[time_col]
           long_gap_h=6.0,        # auto: gaps longer than this (hours) use harmonic
           period_band_h=(6, 48)) # cycle lengths (hours) to look for
```

## Known limitations

- Fully duplicated rows are not merged; they are filled and scored as separate points.
- Timestamps that cannot be parsed become `NaN` and are not reported.
- A gap at the very start or end of the data cannot be joined to both sides, so linear
  repeats the nearest value and harmonic is not pulled back towards the data.

## License

MIT
