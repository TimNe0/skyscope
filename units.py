"""Unit conversion and label formatting.

The providers speak aviation units (feet, knots). Nothing converts on the way
in -- conversion happens here, at render time only, so switching the units
setting never needs a refetch.
"""

from .geo import KM_PER_NM, KMH_PER_KT, MS_PER_KT, M_PER_FT

AVIATION = "aviation"
METRIC = "metric"
MIXED = "mixed"

CHOICES = (AVIATION, METRIC, MIXED)

LABELS = {
    AVIATION: "Aviation (ft, kt, nm)",
    METRIC: "Metric (m, m/s, km)",
    MIXED: "Mixed (m, km/h, km)",
}


def dist_unit(units):
    return "nm" if units == AVIATION else "km"


def dist_value(km, units):
    return km / KM_PER_NM if units == AVIATION else km


def fmt_dist(km, units, decimal=False):
    # MicroPython's % formatting has no "*" precision, so this stays a plain
    # two-way choice rather than a decimals count.
    value = dist_value(km, units)
    if decimal:
        return "%.1f%s" % (value, dist_unit(units))
    return "%d%s" % (round(value), dist_unit(units))


def fmt_radius(km, units):
    """Range-ring label. Whole numbers only -- ring labels get no decimals."""
    return "%d%s" % (round(dist_value(km, units)), dist_unit(units))


def fmt_alt(alt_ft, units, on_ground=False):
    if on_ground:
        return "GND"
    if alt_ft is None:
        return "---"
    if units == AVIATION:
        return "%dft" % round(alt_ft)
    return "%dm" % round(alt_ft * M_PER_FT)


def fmt_speed(gs_kt, units):
    if gs_kt is None:
        return "---"
    if units == AVIATION:
        return "%dkt" % round(gs_kt)
    if units == MIXED:
        return "%dkm/h" % round(gs_kt * KMH_PER_KT)
    return "%dm/s" % round(gs_kt * MS_PER_KT)


def fmt_rate(baro_rate, units):
    """Vertical rate, with an explicit sign so a glance tells climb from descent."""
    if baro_rate is None:
        return "---"
    if units == AVIATION:
        return "%+dfpm" % round(baro_rate)
    return "%+.1fm/s" % (baro_rate * M_PER_FT / 60.0)


def fmt_bearing(bearing):
    return "%03d" % (round(bearing) % 360)
