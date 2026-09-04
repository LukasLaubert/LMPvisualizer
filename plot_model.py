"""Single shared series model + matplotlib conversion helpers.

Canonical model unifying the three parallel render paths:
live pyqtgraph scene (controllers), PopOutWindow matplotlib editor
(popout_window.redraw_plot) and controller.export_plot image export.

Backward-compatible contract
----------------------------
get_current_plot_state() in each controller still returns the same dict
shape PopOutWindow has always consumed::

    {
      'title': str,
      'x_label': str,
      'x_limits': [min, max],
      'x_inverted': bool,            # only log sets this today
      'y_axes': {
        y_col: {
          'label': str,
          'color': QColor,
          'y_limits': [min, max],
          'y_inverted': bool,        # only log sets this today
          'series': [series_dict, ...]
        }
      }
    }

Each series_dict carries the canonical per-series keys (see make_series):
id, name, x, y, std, color, colors (optional per-point brushes for
heatmap scatter), linestyle_matlab, linestyle_qt (optional, log only),
width (line width), marker (mpl marker string), size (marker/scatter
size), mode ('line' | 'scatter'), layer_priority, row (table row for
legend order; fits/defaults use 1_000_000), show_legend (default True),
uid (stable table id, e.g. log plot_id; None when unknown).

Rendering helpers in this module implement the behaviours every caller
must preserve:

- legend = table-row order, selected rows at the end
  (layer_priority >= 100 is "selected", 200+ are fits after those).
- show_std (draw the fill) vs show_std_legend (list it) stay distinct:
  show_std_legend=False keeps the fill but drops its legend entry.
- log-scale safe clamping: no non-positive limits reach matplotlib.
- tick-direction-in and grid live in one place (callers keep their
  current grid alpha/style via parameters so no visual redesign).
- DSD FillBetween matching + BarGraph counts helpers.
- CSV/TSV helpers (format/subset/align) shared so text exports stay
  byte-identical while image exports converge.

matplotlib is intentionally NOT imported here (optional dependency):
helpers take an already-created Axes and draw on it.
"""

import numpy as np

try:
    from PyQt6.QtGui import QColor
except Exception:  # pragma: no cover - Qt always present in app/tests
    QColor = None

# ---------------------------------------------------------------------------
# Canonical constants (single source of truth for legend/layer rules)
# ---------------------------------------------------------------------------

SELECTED_PRIORITY_THRESHOLD = 100
FIT_PRIORITY_THRESHOLD = 200
DEFAULT_ROW = 1_000_000

STD_FILL_ALPHA = 0.25
AVG_FILL_ALPHA = 0.18  # aggregated log "avg std" band is paler

SMALL_POSITIVE_FALLBACK = 1e-9


# ---------------------------------------------------------------------------
# Series builder (single builder sourcing get_current_plot_state values)
# ---------------------------------------------------------------------------

def make_series(series_id, name, x, y, std=None, color=None,
                linestyle_matlab='-', linestyle_qt=None, width=1.5,
                marker='None', size=6, mode='line',
                layer_priority=0, row=DEFAULT_ROW,
                colors=None, show_legend=True, uid=None):
    """Build one canonical series dict.

    x/y/std are stored as numpy arrays (or None for std). color is a
    QColor; colors is an optional per-point QColor list for heatmap
    scatter (trj). marker is already an mpl marker string
    (use pg_symbol_to_mpl for pyqtgraph symbols first).
    """
    if x is not None and not isinstance(x, np.ndarray):
        try:
            x = np.asarray(x)
        except Exception:
            pass
    if y is not None and not isinstance(y, np.ndarray):
        try:
            y = np.asarray(y)
        except Exception:
            pass
    if std is not None and not isinstance(std, np.ndarray):
        try:
            std = np.asarray(std)
        except Exception:
            pass
    return {
        'id': series_id,
        'name': name,
        'x': x,
        'y': y,
        'std': std,
        'color': color,
        'colors': colors,
        'linestyle_matlab': linestyle_matlab,
        'linestyle_qt': linestyle_qt,
        'width': width,
        'marker': marker,
        'size': size,
        'mode': mode,
        'layer_priority': layer_priority,
        'row': row,
        'show_legend': show_legend,
        'uid': uid,
    }


# ---------------------------------------------------------------------------
# Style translation (previously triplicated per controller)
# ---------------------------------------------------------------------------

def qt_pen_style_to_mpl(qt_style):
    """Map Qt PenStyle enum/int to a matplotlib linestyle.

    0 NoPen -> 'None', 1 Solid -> '-', 2 Dash -> '--', 3 Dot -> ':',
    4 DashDot -> '-.', 5 DashDotDot -> mpl dash tuple. Unknown -> '-'.
    """
    try:
        if hasattr(qt_style, 'value'):
            style_int = qt_style.value
        else:
            style_int = int(qt_style)
    except Exception:
        return '-'
    mapping = {
        0: 'None',
        1: '-',
        2: '--',
        3: ':',
        4: '-.',
        5: (0, (3, 1, 1, 1, 1, 1)),
    }
    return mapping.get(style_int, '-')


_PG_SYMBOL_TO_MPL = {
    'o': 'o',
    's': 's',
    't': 'v',   # pg triangle-down
    't1': '^',
    't2': '>',
    't3': '<',
    'd': 'D',
    '+': '+',
    'x': 'x',
    'p': 'p',
    'h': 'h',
    'star': '*',
}

# Markers that are already valid matplotlib markers pass through.
_MPL_PASSTHROUGH = {'o', 's', 'v', '^', '>', '<', 'D', 'd', '+', 'x',
                    'p', 'h', 'H', '*', '.', ',', '|', '_', 'None', None}


def pg_symbol_to_mpl(pg_symbol):
    """Map a pyqtgraph scatter symbol to a matplotlib marker string."""
    if pg_symbol is None or pg_symbol == 'None':
        return 'None'
    if pg_symbol in _PG_SYMBOL_TO_MPL:
        return _PG_SYMBOL_TO_MPL[pg_symbol]
    if pg_symbol in _MPL_PASSTHROUGH:
        return pg_symbol
    return 'o'


def qcolor_to_rgb(qcolor, default=(0.0, 0.0, 0.0)):
    """QColor -> (r, g, b) floats. Falls back to default on garbage."""
    try:
        return (qcolor.redF(), qcolor.greenF(), qcolor.blueF())
    except Exception:
        return default


def qcolor_to_rgba(qcolor, default=(0.0, 0.0, 0.0, 1.0)):
    """QColor -> (r, g, b, a) floats."""
    try:
        return (qcolor.redF(), qcolor.greenF(), qcolor.blueF(), qcolor.alphaF())
    except Exception:
        return default


# ---------------------------------------------------------------------------
# Limits: orientation (inverted axes) + log-safe clamping
# ---------------------------------------------------------------------------

def oriented_limits(limits, inverted):
    """Return limits honouring a pyqtgraph inversion flag.

    Mirrors LogController._viewbox_axis_inverted/_oriented_range and
    PopOutWindow._oriented_limits: inverted swaps the two ends.
    """
    if not limits or len(limits) != 2:
        return limits
    try:
        return [limits[1], limits[0]] if inverted else list(limits)
    except Exception:
        return limits


def _smallest_positive(arrays):
    best = None
    for arr in arrays or []:
        try:
            vals = np.asarray(arr, dtype=float).ravel()
        except Exception:
            continue
        pos = vals[np.isfinite(vals) & (vals > 0)]
        if pos.size:
            cand = float(np.min(pos))
            best = cand if best is None else min(best, cand)
    return best


def clamp_log_limits(limits, is_log, data_arrays=None):
    """Clamp limits so no non-positive value reaches a log-scale axis.

    Preserves orientation (an inverted [hi, lo] pair stays inverted,
    just both-positive). Non-log or malformed input passes through
    untouched. Any end <= 0 is replaced with the smallest positive
    data value, else SMALL_POSITIVE_FALLBACK.
    """
    if not is_log or limits is None or len(limits) != 2:
        return limits
    try:
        lo, hi = float(limits[0]), float(limits[1])
    except Exception:
        return limits
    if lo > 0 and hi > 0:
        return limits
    fallback = _smallest_positive(data_arrays)
    if fallback is None or fallback <= 0:
        fallback = SMALL_POSITIVE_FALLBACK
    # Preserve order: replace each non-positive end independently.
    new_lo = lo if lo > 0 else fallback
    new_hi = hi if hi > 0 else fallback
    # Guard degenerate equal ends after clamping.
    try:
        if new_lo == new_hi:
            new_hi = new_lo * 10.0 if new_lo > 0 else fallback
    except Exception:
        pass
    # Keep original orientation: limits may be [hi, lo] when inverted.
    if limits[0] > limits[1]:
        return [max(new_lo, new_hi), min(new_lo, new_hi)]
    return [new_lo, new_hi]


# ---------------------------------------------------------------------------
# Legend ordering: table-row order with selected last
# ---------------------------------------------------------------------------

def is_selected_priority(prio):
    try:
        return int(prio) >= SELECTED_PRIORITY_THRESHOLD
    except Exception:
        return False


def sub_priority(prio):
    """Within-row ordering: fits (200+) sort after every table row."""
    try:
        p = int(prio)
    except Exception:
        return 0
    if p >= FIT_PRIORITY_THRESHOLD:
        return FIT_PRIORITY_THRESHOLD
    return p % SELECTED_PRIORITY_THRESHOLD


def legend_sort_key(entry):
    """Sort key for (is_sel, row, sub_prio, handle, label) tuples."""
    return (entry[0], entry[1], entry[2])


def sort_legend_entries(entries):
    """Sort legend entries: non-selected in row order, selected last."""
    return sorted(entries, key=legend_sort_key)


def zorder_for_priority(prio, base=2.0):
    try:
        return base + float(prio)
    except Exception:
        return base


# ---------------------------------------------------------------------------
# Matplotlib drawing (single implementation for popout + all exports)
# ---------------------------------------------------------------------------

def draw_std_fill(ax, x, y, std, color_rgba, label=None, zorder=1.9,
                  alpha=STD_FILL_ALPHA):
    """Draw an error-band fill. Returns the PolyCollection handle."""
    lower = np.asarray(y) - np.asarray(std)
    upper = np.asarray(y) + np.asarray(std)
    rgb = tuple(color_rgba[:3]) if len(color_rgba) >= 3 else color_rgba
    return ax.fill_between(np.asarray(x), lower, upper, color=rgb,
                           alpha=alpha, linewidth=0, label=label,
                           zorder=zorder)


def draw_line(ax, x, y, color_rgba, linewidth=1.5, linestyle='-',
              marker='None', markersize=6, label=None, zorder=2.0):
    """Draw one line series. Returns the Line2D handle."""
    if linestyle == 'None' and (marker is None or marker == 'None'):
        return None
    ls = linestyle if linestyle != 'None' else 'None'
    mk = marker if marker not in (None, 'None') else 'None'
    line, = ax.plot(np.asarray(x), np.asarray(y), color=color_rgba,
                    linewidth=linewidth, linestyle=ls, marker=mk,
                    markersize=markersize, label=label, zorder=zorder)
    return line


def draw_scatter(ax, x, y, color_rgba=None, per_point_colors=None,
                 size=25, marker='o', label=None, zorder=2.0):
    """Draw one scatter series. size is mpl area (points**2 upstream)."""
    mk = marker if marker not in (None, 'None') else 'o'
    if per_point_colors is not None:
        return ax.scatter(np.asarray(x), np.asarray(y), c=per_point_colors,
                          s=size, marker=mk, edgecolors='none',
                          label=label, zorder=zorder)
    return ax.scatter(np.asarray(x), np.asarray(y), color=color_rgba,
                      s=size, marker=mk, edgecolors='none',
                      label=label, zorder=zorder)


def render_series(ax, series, color=None, label=None, linestyle=None,
                  linewidth=None, marker=None, markersize=None,
                  show_std=True, show_std_legend=True, show_legend=True,
                  std_alpha=STD_FILL_ALPHA, per_point_colors=None,
                  scatter_size=None, zorder=None):
    """Render one canonical series + optional std band on ax.

    Returns (main_handle, fill_handle, legend_entries) where
    legend_entries is a list of (is_sel, row, sub_prio, handle, label)
    tuples ready for sort_legend_entries. show_std=False skips the
    fill; show_std_legend=False keeps the fill but drops its entry
    (the preserved show_std vs show_std_legend distinction).
    """
    x = series.get('x')
    y = series.get('y')
    std = series.get('std')
    if x is None or y is None or len(np.asarray(x)) == 0:
        return None, None, []
    prio = series.get('layer_priority', 0)
    row = series.get('row', DEFAULT_ROW)
    is_sel = 1 if is_selected_priority(prio) else 0
    sub_prio = sub_priority(prio)
    z = zorder if zorder is not None else zorder_for_priority(prio)

    if color is None:
        color = qcolor_to_rgba(series.get('color')) if series.get('color') is not None else (0, 0, 0, 1)
    if label is None:
        label = series.get('name') if show_legend and series.get('show_legend', True) else None
    if linestyle is None:
        linestyle = series.get('linestyle_matlab', '-')
    if linewidth is None:
        linewidth = series.get('width', 1.5)
    if marker is None:
        marker = series.get('marker', 'None')
    if markersize is None:
        markersize = series.get('size', 6)
    mode = series.get('mode', 'line')

    legend_entries = []
    fill_handle = None
    if show_std and std is not None:
        try:
            std_arr = np.asarray(std)
            if std_arr is not None and np.any(np.asarray(std_arr) != 0):
                fill_label = None
                if show_std_legend and label:
                    fill_label = f"{label} (Std)"
                fill_handle = draw_std_fill(ax, x, y, std_arr, color,
                                            label=fill_label, zorder=z - 0.1,
                                            alpha=std_alpha)
                if fill_label:
                    legend_entries.append((is_sel, row, sub_prio - 0.5,
                                           fill_handle, fill_label))
        except Exception:
            fill_handle = None

    main_handle = None
    try:
        if mode == 'scatter':
            colors = per_point_colors
            if colors is None:
                colors = series.get('colors')
            if colors is not None:
                try:
                    colors = [(c.redF(), c.greenF(), c.blueF(), c.alphaF())
                              for c in colors]
                except Exception:
                    colors = None
            sz = scatter_size
            if sz is None:
                try:
                    sz = float(series.get('size', 5)) ** 2
                except Exception:
                    sz = 25
            mk = marker if marker not in (None, 'None') else 'o'
            main_handle = draw_scatter(ax, x, y, color_rgba=color,
                                       per_point_colors=colors, size=sz,
                                       marker=mk, label=label, zorder=z)
        else:
            main_handle = draw_line(ax, x, y, color, linewidth=linewidth,
                                    linestyle=linestyle, marker=marker,
                                    markersize=markersize, label=label,
                                    zorder=z)
    except Exception:
        main_handle = None

    if main_handle is not None and label:
        legend_entries.append((is_sel, row, sub_prio, main_handle, label))
    return main_handle, fill_handle, legend_entries


def apply_axis_style(ax, xlabel=None, ylabel=None, axis_color=None,
                     fontsize_label=11, fontsize_tick=10, grid=True,
                     grid_alpha=0.3, grid_linestyle=None, grid_linewidth=None,
                     is_primary=True):
    """Shared tick-direction-in + label/grid styling for one mpl axis."""
    if xlabel is not None:
        ax.set_xlabel(xlabel, fontsize=fontsize_label)
    if ylabel is not None:
        ax.set_ylabel(ylabel, fontsize=fontsize_label)
    if axis_color is not None:
        try:
            ax.yaxis.label.set_color(axis_color)
        except Exception:
            pass
    # Ticks point in on every path (live/export/popout convergence).
    try:
        if axis_color is not None:
            ax.tick_params(axis='y', colors=axis_color, direction='in',
                           labelsize=fontsize_tick)
            ax.tick_params(axis='x', direction='in', labelsize=fontsize_tick)
        else:
            ax.tick_params(axis='both', direction='in', labelsize=fontsize_tick)
    except Exception:
        try:
            ax.tick_params(direction='in')
        except Exception:
            pass
    if grid:
        try:
            if grid_linestyle is not None:
                ax.grid(True, alpha=grid_alpha, linestyle=grid_linestyle,
                        linewidth=grid_linewidth if grid_linewidth else 0.5)
            else:
                ax.grid(True, alpha=grid_alpha)
        except Exception:
            pass


def setup_multi_y_axes(ax_primary, y_order):
    """Create twinx axes for y_order[1:] mirroring log/popout layout.

    Primary keeps left spine; secondaries hide top/left, colour their
    right spine later via apply_axis_style. Outward offset (60pt per
    extra axis) matches the existing log/popout behaviour.
    """
    axes_map = {y_order[0]: ax_primary}
    try:
        ax_primary.spines['top'].set_visible(False)
        ax_primary.spines['right'].set_visible(False)
    except Exception:
        pass
    for i, y_col in enumerate(y_order[1:], start=1):
        try:
            ax_new = ax_primary.twinx()
            ax_new.spines['top'].set_visible(False)
            ax_new.spines['left'].set_visible(False)
            if i > 1:
                ax_new.spines['right'].set_position(('outward', 60 * (i - 1)))
            axes_map[y_col] = ax_new
        except Exception:
            continue
    return axes_map


# ---------------------------------------------------------------------------
# DSD helpers: FillBetween matching + bar width (previously duplicated
# across get_current_plot_state / export_plot / _export_text_data)
# ---------------------------------------------------------------------------

def match_std_band(x, y, bands, atol=1e-8, exact_x=False):
    """Find the std array whose band midpoint matches (x, y).

    bands: iterable of (band_x, lower, upper). Returns std or None.
    exact_x=True requires identical x (CSV path); otherwise allclose.
    """
    try:
        x = np.asarray(x)
        y = np.asarray(y)
    except Exception:
        return None
    for band in bands:
        try:
            bx, lower, upper = band
            bx = np.asarray(bx)
            lower = np.asarray(lower)
            upper = np.asarray(upper)
        except Exception:
            continue
        try:
            if bx is None or lower is None or upper is None:
                continue
            if len(bx) != len(x):
                continue
            if exact_x:
                if not np.array_equal(bx, x):
                    continue
            elif not np.allclose(bx, x, atol=atol):
                continue
            mid = (np.asarray(lower) + np.asarray(upper)) / 2.0
            if np.allclose(mid, y, atol=atol):
                return np.abs(np.asarray(upper) - np.asarray(lower)) / 2.0
        except Exception:
            continue
    return None


def compute_bar_width(x, default=1.0, fraction=0.8):
    """Bar width for DSD counts: min positive x-gap * fraction."""
    try:
        xs = np.sort(np.asarray(x, dtype=float))
        if len(xs) < 2:
            return default
        diffs = np.diff(xs)
        pos = diffs[diffs > 0]
        if len(pos) == 0:
            return default
        return float(np.min(pos)) * fraction
    except Exception:
        return default


# ---------------------------------------------------------------------------
# Text-export helpers (shared; per-mode grouping/formatting preserved so
# CSV/TSV output stays byte-identical)
# ---------------------------------------------------------------------------

def format_export_value(value):
    """Log-style cell formatting: None/NaN -> '' else str(value)."""
    if value is None:
        return ''
    try:
        if np.isnan(value):
            return ''
    except TypeError:
        pass
    return str(value)


def subset_positions(base_x, sub_x):
    """Find order-preserving positions of sub_x inside base_x (log logic)."""
    base = np.asarray(base_x, dtype=float)
    sub = np.asarray(sub_x, dtype=float)
    if len(sub) > len(base):
        return None
    positions = []
    search_start = 0
    for value in sub:
        if search_start >= len(base):
            return None
        matches = np.where(np.isclose(base[search_start:], value,
                                      rtol=1e-9, atol=1e-12))[0]
        if len(matches) == 0:
            return None
        pos = search_start + int(matches[0])
        positions.append(pos)
        search_start = pos + 1
    return positions


def align_values(length, positions, values):
    """Scatter y/std values into a base-x-length column (None-filled)."""
    aligned = [None] * length
    for pos, value in zip(positions, values):
        aligned[pos] = value
    return aligned
