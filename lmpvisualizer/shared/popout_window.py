# lmp_visualizer/popout_window.py

import os
import sys
import copy
import datetime
import json
import shutil
import numpy as np
from PyQt6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
                             QDockWidget, QScrollArea, QFormLayout, QLabel, 
                             QLineEdit, QCheckBox, QComboBox, QSpinBox, 
                             QDoubleSpinBox, QGroupBox, QPushButton, QColorDialog,
                             QFrame, QSizePolicy, QMessageBox, QToolBar,
                             QDialog, QDialogButtonBox, QFileDialog, QGridLayout)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics

import matplotlib
matplotlib.use('qtagg')
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
import matplotlib.pyplot as plt

from lmpvisualizer.shared.ui_components import ColorButton
from lmpvisualizer.shared import plot_model
from lmpvisualizer.shared.logger_setup import get_logger

logger = get_logger(__name__)

BERLIN_TZ_NAME = "Europe/Berlin"
PRESET_NAME_FMT = "%Y-%m-%d_%H:%M:%S"


def berlin_now_str(now_utc=None):
    """Current time as Berlin wall-clock 'YYYY-MM-DD_HH:MM:SS' (no tz suffix).

    Falls back to system local time when the zoneinfo DB is missing.
    Never raises.
    """
    try:
        if now_utc is None:
            now_utc = datetime.datetime.now(datetime.timezone.utc)
        elif now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=datetime.timezone.utc)
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(BERLIN_TZ_NAME)
            local = now_utc.astimezone(tz)
        except Exception:
            try:
                local = now_utc.astimezone()
            except Exception:
                local = datetime.datetime.now()
        return local.strftime(PRESET_NAME_FMT)
    except Exception:
        try:
            return datetime.datetime.now().strftime(PRESET_NAME_FMT)
        except Exception:
            return "preset"


def mint_preset_name(existing_names, now_utc=None):
    """Mint a collision-free preset name against existing names."""
    try:
        existing = set(existing_names or [])
    except Exception:
        existing = set()
    base = berlin_now_str(now_utc)
    if base not in existing:
        return base
    i = 2
    while f"{base}_{i}" in existing:
        i += 1
    return f"{base}_{i}"


def _qcolor_to_hex(c):
    try:
        if isinstance(c, QColor):
            if not c.isValid():
                return "#000000"
            try:
                if c.alpha() != 255:
                    return c.name(QColor.NameFormat.HexArgb)
            except Exception:
                pass
            return c.name()
        if isinstance(c, str):
            return c
    except Exception:
        pass
    return "#000000"


def _hex_to_qcolor(v, fallback="black"):
    try:
        if isinstance(v, QColor):
            return v if v.isValid() else QColor(fallback)
        if isinstance(v, str) and v:
            c = QColor(v)
            if c.isValid():
                return c
    except Exception:
        pass
    try:
        return QColor(fallback)
    except Exception:
        return QColor("black")


def _plain_series_props(props):
    """Copy get_properties() output with QColors as hex (JSON-safe)."""
    try:
        out = {}
        for k, v in dict(props or {}).items():
            try:
                if isinstance(v, QColor):
                    out[k] = _qcolor_to_hex(v)
                else:
                    out[k] = copy.deepcopy(v)
            except Exception:
                try:
                    out[k] = v
                except Exception:
                    pass
        return out
    except Exception:
        return {}

def _should_restore_label(saved_text, saved_default, exact_hit):
    """Custom-only text rule for preset apply.

    Legend/title texts embed data names, so a saved text is restored only
    if the user customized it (it differs from the default captured at
    save time). Untouched defaults always follow the current data, which
    keeps data renames (mean suffixes, columns, study/system) from
    mislabeling curves. Legacy presets without a stored default keep the
    old always-apply behaviour, but only on exact sid hits.
    """
    try:
        if not isinstance(saved_text, str):
            return False
        if isinstance(saved_default, str):
            return saved_text != saved_default
        return bool(exact_hit)
    except Exception:
        return False

class LinePropertiesWidget(QGroupBox):
    """Widget to control properties of a single line or scatter series."""
    propertiesChanged = pyqtSignal()

    def __init__(self, series_id, initial_props, parent=None):
        # Elide the title text to prevent the dock from expanding too much
        # We target approx 250px width for the title
        full_name = initial_props['name']
        fm = QFontMetrics(QFont())
        elided_name = fm.elidedText(full_name, Qt.TextElideMode.ElideMiddle, 250)
        
        super().__init__(elided_name, parent)
        self.setToolTip(full_name)
        self._series_default = full_name
        
        self.series_id = series_id
        self.is_scatter = initial_props.get('mode') == 'scatter'
        self.has_std = initial_props.get('has_std', False)
        
        self.setCheckable(True)
        self.setChecked(initial_props.get('visible', True))
        self.toggled.connect(self.propertiesChanged)

        # Grid layout: two label/control pairs per row, so the group needs roughly
        # half the vertical space of the previous one-control-per-row form.
        layout = QGridLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setHorizontalSpacing(4)
        layout.setVerticalSpacing(5)
        layout.setColumnStretch(1, 1)

        # Row 0: Legend text + colour swatch (no separate "Color:" row/label)
        self.label_edit = QLineEdit(initial_props['name'])
        self.label_edit.editingFinished.connect(self.propertiesChanged)
        self.label_edit.setMaximumWidth(200)

        is_heatmap = self.is_scatter and 'colors' in initial_props and initial_props['colors'] is not None
        self.color_btn = ColorButton(initial_props['color'])
        self.color_btn.colorChanged.connect(lambda: self.propertiesChanged.emit())

        layout.addWidget(QLabel("Legend:"), 0, 0)
        layout.addWidget(self.label_edit, 0, 1)
        if not is_heatmap:
            # Square swatch, sized to the line edit's height
            side = self.label_edit.sizeHint().height()
            self.color_btn.setFixedSize(side, side)
            self.color_btn.setToolTip("Series colour")
            layout.addWidget(self.color_btn, 0, 2, 1, 2)
        # (heatmap series intentionally keep the button unparented / hidden)

        def _spin(lo, hi, val, step=None):
            sp = QDoubleSpinBox()
            sp.setRange(lo, hi)
            if step is not None:
                sp.setSingleStep(step)
            sp.setValue(float(val))
            sp.valueChanged.connect(self.propertiesChanged)
            sp.setMaximumWidth(90)
            return sp

        if self.is_scatter:
            self.size_spin = _spin(1.0, 200.0, initial_props.get('size', 10))

            self.marker_combo = QComboBox()
            self.marker_combo.addItems(['o', 'x', '+', 'v', '^', '<', '>', 's', 'p', '*', 'h', 'H', 'D', 'd'])
            self.marker_combo.setCurrentText(initial_props.get('marker', 'o'))
            self.marker_combo.currentTextChanged.connect(self.propertiesChanged)
            self.marker_combo.setMaximumWidth(90)

            # Row 1: Size | Marker
            layout.addWidget(QLabel("Size:"), 1, 0)
            layout.addWidget(self.size_spin, 1, 1)
            layout.addWidget(QLabel("Marker:"), 1, 2)
            layout.addWidget(self.marker_combo, 1, 3)

            # Placeholders for scatter (line width/style do not apply)
            self.style_combo = QComboBox()
            self.width_spin = QDoubleSpinBox()
            next_row = 2
        else:
            self.width_spin = _spin(0.1, 20.0, initial_props.get('linewidth', 1.5), step=0.5)

            self.style_combo = QComboBox()
            self.style_combo.addItems(['None', '-', '--', ':', '-.'])
            cur_ls = initial_props.get('linestyle', '-')
            if cur_ls not in ['None', '-', '--', ':', '-.']:
                cur_ls = '-'
            self.style_combo.setCurrentText(cur_ls)
            self.style_combo.currentTextChanged.connect(self.propertiesChanged)
            self.style_combo.setMaximumWidth(90)

            self.marker_combo = QComboBox()
            self.marker_combo.addItems(['None', 'o', 'x', '+', 'v', '^', '<', '>', 's', 'p', '*', 'h', 'H', 'D', 'd'])
            current_marker = initial_props.get('marker', 'None')
            if current_marker is None: current_marker = 'None'
            self.marker_combo.setCurrentText(current_marker)
            self.marker_combo.currentTextChanged.connect(self.propertiesChanged)
            self.marker_combo.setMaximumWidth(90)

            # Marker size is now adjustable for line series too - it used to be an
            # orphan widget with a hardcoded markersize of 6 in get_properties().
            self.size_spin = _spin(1.0, 200.0, initial_props.get('size', 6))

            # Row 1: Width | Style     Row 2: Marker | Size
            layout.addWidget(QLabel("Width:"), 1, 0)
            layout.addWidget(self.width_spin, 1, 1)
            layout.addWidget(QLabel("Style:"), 1, 2)
            layout.addWidget(self.style_combo, 1, 3)

            layout.addWidget(QLabel("Marker:"), 2, 0)
            layout.addWidget(self.marker_combo, 2, 1)
            layout.addWidget(QLabel("Size:"), 2, 2)
            layout.addWidget(self.size_spin, 2, 3)
            next_row = 3

        # Shared Error Band Control (Available for both line and scatter if data exists)
        self.error_check = QCheckBox("Show Error Band")
        self.error_legend_check = QCheckBox("Std in Legend")
        self.error_legend_check.setToolTip("Show error band in legend while keeping the band")
        if self.has_std:
            self.error_check.setChecked(initial_props.get('show_std', True))
            self.error_check.toggled.connect(self.propertiesChanged)
            self.error_check.toggled.connect(lambda c: self.error_legend_check.setEnabled(c))
            layout.addWidget(self.error_check, next_row, 0, 1, 2)
            self.error_legend_check.setChecked(initial_props.get('show_std_legend', True))
            self.error_legend_check.setEnabled(self.error_check.isChecked())
            self.error_legend_check.toggled.connect(self.propertiesChanged)
            layout.addWidget(self.error_legend_check, next_row, 2, 1, 2)
        else:
            self.error_check.setVisible(False)
            self.error_legend_check.setVisible(False)

    def get_properties(self):
        props = {
            'visible': self.isChecked(),
            'label': self.label_edit.text(),
            'color': self.color_btn.color(),
            'marker': self.marker_combo.currentText()
        }
        
        if self.is_scatter:
            props['size'] = self.size_spin.value()
            props['linestyle'] = 'None' 
            props['linewidth'] = 0
        else:
            m = self.marker_combo.currentText()
            if m == 'None': m = None
            props['marker'] = m
            props['linestyle'] = self.style_combo.currentText()
            props['linewidth'] = self.width_spin.value()
            props['size'] = self.size_spin.value()  # marker size, now user-adjustable
            
        props['show_std'] = self.error_check.isChecked() if self.has_std else False
        props['show_std_legend'] = self.error_legend_check.isChecked() if self.has_std else False
            
        return props


class AggregatedLogPropertiesWidget(QGroupBox):
    """One box per table row: main (orig) line + avg line + smooth/avg/sys std bands as subboxes."""
    propertiesChanged = pyqtSignal()

    def __init__(self, base_id, base_name, main_props, std_props=None, inter_props=None, mean_props=None, sys_props=None, parent=None):
        fm = QFontMetrics(QFont())
        elided = fm.elidedText(base_name, Qt.TextElideMode.ElideMiddle, 250)
        super().__init__(elided, parent)
        self.setToolTip(base_name)
        self.series_id = base_id
        self.base_name = base_name
        self._series_default = main_props.get('name', base_name)
        self.has_std = std_props is not None
        self.has_inter = inter_props is not None
        self.has_mean = mean_props is not None
        self.has_sys = sys_props is not None
        self.setCheckable(True)
        self.setChecked(main_props.get('visible', True))
        self.toggled.connect(self.propertiesChanged)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 8, 6, 6)
        layout.setSpacing(6)

        # Legend row: independent QHBox so color right-aligned regardless of Width/Style columns
        legend_row = QHBoxLayout()
        legend_row.setContentsMargins(0, 0, 0, 0)
        legend_row.setSpacing(6)
        self.main_legend_check = QCheckBox("Legend:")
        self.main_legend_check.setChecked(main_props.get('show_legend', True))
        self.main_legend_check.setToolTip("Show in legend")
        self.main_legend_check.toggled.connect(self.propertiesChanged)
        legend_row.addWidget(self.main_legend_check)
        self.main_label = QLineEdit(main_props.get('name', base_name))
        self.main_label.editingFinished.connect(self.propertiesChanged)
        legend_row.addWidget(self.main_label, 1)
        self.main_color = ColorButton(main_props.get('color', QColor("red")))
        self.main_color.colorChanged.connect(lambda: self.propertiesChanged.emit())
        side = self.main_label.sizeHint().height()
        self.main_color.setFixedSize(side, side)
        legend_row.addWidget(self.main_color)
        layout.addLayout(legend_row)
        # main grid: Width|Style Marker|Size (independent of Legend row)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(5)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        # Width | Style (one line)
        def _spin(lo, hi, val, step=None):
            sp = QDoubleSpinBox(); sp.setRange(lo, hi)
            if step is not None: sp.setSingleStep(step)
            sp.setValue(float(val)); sp.valueChanged.connect(self.propertiesChanged)
            sp.setMaximumWidth(90); return sp
        self.width_spin = _spin(0.1, 20.0, main_props.get('linewidth', 1.5), step=0.5)
        self.style_combo = QComboBox()
        self.style_combo.addItems(['None', '-', '--', ':', '-.'])
        cur = main_props.get('linestyle', '-')
        if cur not in ['None', '-', '--', ':', '-.']: cur = '-'
        self.style_combo.setCurrentText(cur)
        self.style_combo.currentTextChanged.connect(self.propertiesChanged)
        self.style_combo.setMaximumWidth(90)
        grid.addWidget(QLabel("Width:"), 0, 0)
        grid.addWidget(self.width_spin, 0, 1)
        grid.addWidget(QLabel("Style:"), 0, 2)
        grid.addWidget(self.style_combo, 0, 3)
        # Marker | Size (one line)
        self.marker_combo = QComboBox()
        self.marker_combo.addItems(['None', 'o', 'x', '+', 'v', '^', '<', '>', 's', 'p', '*', 'h', 'H', 'D', 'd'])
        cur_m = main_props.get('marker', 'None')
        if cur_m is None: cur_m = 'None'
        self.marker_combo.setCurrentText(cur_m)
        self.marker_combo.currentTextChanged.connect(self.propertiesChanged)
        self.marker_combo.setMaximumWidth(90)
        self.size_spin = _spin(1.0, 200.0, main_props.get('size', 6))
        grid.addWidget(QLabel("Marker:"), 1, 0)
        grid.addWidget(self.marker_combo, 1, 1)
        grid.addWidget(QLabel("Size:"), 1, 2)
        grid.addWidget(self.size_spin, 1, 3)
        self.sync_btn = QPushButton("⇄")
        self.sync_btn.setCheckable(True)
        self.sync_btn.setToolTip("Sync Width/Style/Marker/Size across all main lines")
        self.sync_btn.setFixedWidth(22)
        self.sync_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.sync_btn.setStyleSheet("QPushButton:checked { background-color: #4a90e2; color: white; }")
        grid.addWidget(self.sync_btn, 0, 4, 2, 1)
        layout.addLayout(grid)
        # bands
        self.std_group = None
        self.inter_group = None
        self.mean_group = None
        self.sys_group = None
        if self.has_std:
            self.std_group = self._make_band("smooth std", std_props, layout)
        if self.has_inter:
            self.inter_group = self._make_band("avg std", inter_props, layout)
        if self.has_mean:
            # Mean curve beside the orig main line: own visibility,
            # legend label and color; width/style/marker/size are shared
            # with the main controls (one style per table row, as live).
            self.mean_group = self._make_band("avg", mean_props, layout)
        if self.has_sys:
            # Raw system-selection std riding on the main (orig) curve
            # (average & std system with smooth-before off): same controls.
            # Legend stays off by default, as live (band without entry).
            self.sys_group = self._make_band("sys std", sys_props, layout)

    def _make_band(self, title, props, parent_layout):
        box = QGroupBox(title, self)
        box.setCheckable(True)
        box.setChecked(props.get('show_std', True))
        box.toggled.connect(self.propertiesChanged)
        bl = QGridLayout(box)
        bl.setContentsMargins(6, 6, 6, 6)
        bl.setHorizontalSpacing(4)
        bl.setVerticalSpacing(4)
        bl.setColumnStretch(1, 1)
        # Legend row: check left of Legend, field extends to swatch at very right
        lc = QCheckBox("Legend:")
        lc.setChecked(props.get('show_legend', True))
        lc.setToolTip("Show in legend")
        lc.toggled.connect(self.propertiesChanged)
        bl.addWidget(lc, 0, 0)
        le = QLineEdit(props.get('name', title))
        le.editingFinished.connect(self.propertiesChanged)
        bl.addWidget(le, 0, 1, 1, 2)
        cb = ColorButton(props.get('color', QColor("red")))
        cb.colorChanged.connect(lambda: self.propertiesChanged.emit())
        side = le.sizeHint().height()
        cb.setFixedSize(side, side)
        bl.addWidget(cb, 0, 3)
        # keep refs
        box._le = le
        box._lc = lc
        box._cb = cb
        box._default_label = le.text()
        parent_layout.addWidget(box)
        return box

    def get_properties(self):
        # main
        m_marker = self.marker_combo.currentText()
        if m_marker == 'None': m_marker = None
        props = {
            'visible': self.isChecked(),
            'label': self.main_label.text(),
            'show_legend': self.main_legend_check.isChecked(),
            'color': self.main_color.color(),
            'linestyle': self.style_combo.currentText(),
            'linewidth': self.width_spin.value(),
            'marker': m_marker,
            'size': self.size_spin.value(),
        }
        if self.has_std and self.std_group:
            props['std_visible'] = self.std_group.isChecked()
            props['std_label'] = self.std_group._le.text()
            props['std_show_legend'] = self.std_group._lc.isChecked()
            props['std_color'] = self.std_group._cb.color()
            props['std_show'] = self.std_group.isChecked()
        if self.has_inter and self.inter_group:
            props['inter_visible'] = self.inter_group.isChecked()
            props['inter_label'] = self.inter_group._le.text()
            props['inter_show_legend'] = self.inter_group._lc.isChecked()
            props['inter_color'] = self.inter_group._cb.color()
            props['inter_show'] = self.inter_group.isChecked()
        if self.has_mean and self.mean_group:
            props['mean_visible'] = self.mean_group.isChecked()
            props['mean_label'] = self.mean_group._le.text()
            props['mean_show_legend'] = self.mean_group._lc.isChecked()
            props['mean_color'] = self.mean_group._cb.color()
            props['mean_show'] = self.mean_group.isChecked()
        if self.has_sys and self.sys_group:
            props['sys_visible'] = self.sys_group.isChecked()
            props['sys_label'] = self.sys_group._le.text()
            props['sys_show_legend'] = self.sys_group._lc.isChecked()
            props['sys_color'] = self.sys_group._cb.color()
            props['sys_show'] = self.sys_group.isChecked()
        return props

class PopOutWindow(QMainWindow):
    # Static class variable to store LaTeX state across different popout instances
    # within the same application session. Resets to False when app restarts.
    _session_latex_enabled = False

    def __init__(self, plot_state_data, parent=None, figsize=None,
                 source_name=None, on_close=None):
        super().__init__(parent)
        self.setWindowTitle("Plot Inspector")
        self.resize(1100, 650) 
        
        self.plot_data = copy.deepcopy(plot_state_data)
        self.line_widgets = {} 
        self._main_sync_enabled = False
        self._in_sync = False
        self.initial_figsize = figsize 
        # Preset lifecycle: fresh window -> source_name None (close mints new);
        # preset-opened window -> source_name set (close overwrites entry).
        self.source_name = source_name
        self._preset_on_close = on_close
        
        self._init_ui()
        
        self.canvas.mpl_connect('resize_event', self.on_canvas_resize)
        
        if self.initial_figsize:
            self._apply_initial_figsize(self.initial_figsize)
        else:
            self.redraw_plot()

    def _init_ui(self):
        self.figure = Figure(dpi=100) 
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCentralWidget(self.canvas)

        self.toolbar = NavigationToolbar2QT(self.canvas, self)
        self.addToolBar(self.toolbar)

        self.dock = QDockWidget("Plot Settings", self)
        self.dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self.dock_widget = QWidget()
        self.dock_layout = QVBoxLayout(self.dock_widget)
        self.dock_layout.setContentsMargins(0, 0, 0, 0)
        
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll_content = QWidget()
        self.form_layout = QVBoxLayout(self.scroll_content)
        
        # UI Setup
        self._init_global_settings()
        self._init_font_settings()
        self._init_axis_settings()
        self._init_legend_settings()

        self.lines_group = QGroupBox("Series Properties")
        self.lines_layout = QVBoxLayout(self.lines_group)
        self.form_layout.addWidget(self.lines_group)
        
        self._populate_line_widgets()

        self.form_layout.addStretch()
        if getattr(self, '_preset_on_close', None) is not None:
            # Intermediate snapshot with close semantics; window, settings
            # and lifecycle continue untouched (NO reset).
            self.save_snapshot_btn = QPushButton("Save Snapshot")
            self.save_snapshot_btn.setToolTip(
                "Store the current settings as a preset entry and keep working; "
                "closing still saves again")
            self.save_snapshot_btn.clicked.connect(self._on_save_snapshot_clicked)
            self.form_layout.addWidget(self.save_snapshot_btn)
        self.scroll.setWidget(self.scroll_content)

        # The two-column groups are wider than the old single-column form, so give the
        # dock enough room to show both columns instead of hiding one behind a
        # horizontal scrollbar. The preferred width is capped so a long series name
        # cannot bloat the dock, but never below what the layout actually needs.
        sb_w = self.scroll.verticalScrollBar().sizeHint().width() + 8
        preferred = self.scroll_content.sizeHint().width() + sb_w
        floor = self.scroll_content.minimumSizeHint().width() + sb_w
        self.scroll.setMinimumWidth(max(floor, min(preferred, 560)))

        self.dock_layout.addWidget(self.scroll)
        self.dock.setWidget(self.dock_widget)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock)

        # Connect + apply session state LAST, after all widgets (like font_title_spin)
        # exist. on_latex_toggled triggers a full redraw that touches them.
        self.latex_check.toggled.connect(self.on_latex_toggled)
        self.latex_check.setChecked(PopOutWindow._session_latex_enabled)

    def _init_global_settings(self):
        group = QGroupBox("Global Settings")
        layout = QFormLayout(group)

        # Size Controls
        size_layout = QHBoxLayout()
        size_layout.setContentsMargins(0, 0, 0, 0)
        size_layout.setSpacing(2)
        
        self.width_spin = QDoubleSpinBox()
        self.width_spin.setPrefix("W: ")
        self.width_spin.setRange(10, 10000)
        self.width_spin.setDecimals(1)
        self.width_spin.editingFinished.connect(self.apply_canvas_size)
        self.width_spin.setFixedWidth(90)
        
        self.height_spin = QDoubleSpinBox()
        self.height_spin.setPrefix("H: ")
        self.height_spin.setRange(10, 10000)
        self.height_spin.setDecimals(1)
        self.height_spin.editingFinished.connect(self.apply_canvas_size)
        self.height_spin.setFixedWidth(90)
        
        self.unit_combo = QComboBox()
        self.unit_combo.addItems(['px', 'in', 'mm', 'cm'])
        self.unit_combo.setCurrentText('px')
        self.unit_combo.currentTextChanged.connect(self.update_size_display)
        self.unit_combo.setFixedWidth(60)
        
        set_size_btn = QPushButton("Set")
        set_size_btn.setFixedWidth(40)
        set_size_btn.clicked.connect(self.apply_canvas_size)
        
        size_layout.addWidget(self.width_spin)
        size_layout.addWidget(self.height_spin)
        size_layout.addWidget(self.unit_combo)
        size_layout.addWidget(set_size_btn)
        
        layout.addRow("Size:", size_layout)

        # Text props
        self.title_edit = QLineEdit(self.plot_data.get('title', ''))
        self.title_edit.editingFinished.connect(self.redraw_plot)
        self.title_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout.addRow("Title:", self.title_edit)

        # Show Grid and Use LaTeX share one row (LaTeX on the right).
        # NOTE: latex_check.toggled is still connected last, in _init_ui, because its
        # handler triggers a full redraw that touches font_title_spin.
        self.latex_check = QCheckBox("Use LaTeX (slow...)")

        self.grid_check = QCheckBox("Show Grid")
        self.grid_check.setChecked(True)
        self.grid_check.toggled.connect(self.redraw_plot)

        checks_row = QHBoxLayout()
        checks_row.setContentsMargins(0, 0, 0, 0)
        checks_row.setSpacing(8)
        checks_row.addWidget(self.grid_check)
        checks_row.addWidget(self.latex_check)
        checks_row.addStretch(1)
        layout.addRow(checks_row)

        self.form_layout.addWidget(group)

    def _init_font_settings(self):
        group = QGroupBox("Font Sizes")
        # 2x2 grid instead of four single-column rows - same widgets, same signals,
        # half the vertical space.
        layout = QGridLayout(group)
        layout.setHorizontalSpacing(6)
        layout.setVerticalSpacing(5)

        def _font_spin(value):
            sp = QSpinBox()
            sp.setRange(6, 72)
            sp.setValue(value)
            sp.valueChanged.connect(self.redraw_plot)
            sp.setFixedWidth(60)
            return sp

        self.font_title_spin = _font_spin(12)
        self.font_label_spin = _font_spin(11)
        self.font_tick_spin = _font_spin(12)
        self.font_legend_spin = _font_spin(12)

        for row, col, text, spin in (
            (0, 0, "Title:", self.font_title_spin),
            (0, 2, "Axis Labels:", self.font_label_spin),
            (1, 0, "Tick Labels:", self.font_tick_spin),
            (1, 2, "Legend:", self.font_legend_spin),
        ):
            layout.addWidget(QLabel(text), row, col)
            layout.addWidget(spin, row, col + 1)

        layout.setColumnStretch(1, 1)
        layout.setColumnStretch(3, 1)

        self.form_layout.addWidget(group)

    def _init_axis_settings(self):
        group = QGroupBox("Axes")
        layout = QVBoxLayout(group)
        layout.setSpacing(6)
        layout.setContentsMargins(6, 8, 6, 6)
        # X-Axis header with Log on same line
        default_x = self.plot_data.get('x_label', '')
        x_header = QHBoxLayout()
        x_header.setContentsMargins(0, 0, 0, 0)
        x_header.addWidget(QLabel(f"<b>X-Axis: {default_x}</b>"))
        x_header.addStretch(1)
        self.x_log_check = QCheckBox("Log")
        self.x_log_check.setToolTip("Log scale X")
        self.x_log_check.toggled.connect(self.redraw_plot)
        x_header.addWidget(self.x_log_check)
        layout.addLayout(x_header)
        x_row = QHBoxLayout()
        x_row.setContentsMargins(0, 0, 0, 0)
        x_row.addWidget(QLabel("Label:"))
        self.x_label_edit = QLineEdit(default_x)
        self.x_label_edit.editingFinished.connect(self.redraw_plot)
        x_row.addWidget(self.x_label_edit, 1)
        layout.addLayout(x_row)

        self.y_configs = {}
        for y_col, axis_data in self.plot_data['y_axes'].items():
            y_header = QHBoxLayout()
            y_header.setContentsMargins(0, 6, 0, 0)
            y_header.addWidget(QLabel(f"<b>Y-Axis: {y_col}</b>"))
            y_header.addStretch(1)
            log_check = QCheckBox("Log")
            log_check.setToolTip("Log scale Y")
            log_check.toggled.connect(self.redraw_plot)
            y_header.addWidget(log_check)
            layout.addLayout(y_header)
            y_row = QHBoxLayout()
            y_row.setContentsMargins(0, 0, 0, 0)
            y_row.addWidget(QLabel("Label:"))
            default_y = axis_data.get('label', y_col)
            label_edit = QLineEdit(default_y)
            label_edit.editingFinished.connect(self.redraw_plot)
            y_row.addWidget(label_edit, 1)
            layout.addLayout(y_row)
            self.y_configs[y_col] = {'label_edit': label_edit, 'log_check': log_check, 'header': y_header}

        self.form_layout.addWidget(group)

    def _init_legend_settings(self):
        group = QGroupBox("Legend")
        layout = QGridLayout(group)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(5)
        layout.setColumnStretch(0, 1)
        layout.setColumnStretch(1, 1)
        self.show_legend_check = QCheckBox("Show Legend")
        self.show_legend_check.setChecked(True)
        self.show_legend_check.toggled.connect(self.redraw_plot)
        layout.addWidget(self.show_legend_check, 0, 0)
        loc_box = QHBoxLayout()
        loc_box.setContentsMargins(0, 0, 0, 0)
        loc_box.setSpacing(4)
        loc_box.addWidget(QLabel("Location:"))
        self.legend_loc = QComboBox()
        self.legend_loc.addItems(['best', 'upper right', 'upper left', 'lower left', 'lower right', 'center left', 'center right', 'upper center', 'lower center'])
        self.legend_loc.currentTextChanged.connect(self.redraw_plot)
        self.legend_loc.setMaximumWidth(140)
        loc_box.addWidget(self.legend_loc)
        loc_box.addStretch(1)
        loc_w = QWidget()
        loc_w.setLayout(loc_box)
        layout.addWidget(loc_w, 0, 1)
        self.legend_frame = QCheckBox("Frame")
        self.legend_frame.setChecked(True)
        self.legend_frame.toggled.connect(self.redraw_plot)
        layout.addWidget(self.legend_frame, 1, 0)
        self.legend_draggable = QCheckBox("Draggable")
        self.legend_draggable.setChecked(True)
        self.legend_draggable.toggled.connect(self.redraw_plot)
        layout.addWidget(self.legend_draggable, 1, 1)
        self.form_layout.addWidget(group)

    def _populate_line_widgets(self):
        for i in reversed(range(self.lines_layout.count())): 
            self.lines_layout.itemAt(i).widget().setParent(None)
        self.line_widgets.clear()
        self._aggregated_bases = {}
        # collect all entries first so widget order = table row order (row asc) even with multi-y
        pending = []  # (row, kind, payload)
        for y_col, axis_data in self.plot_data['y_axes'].items():
            series_list = axis_data['series']
            has_log_suffix = any(s['name'].endswith(('_running_mean_std_inter','_running_mean_std','_running_mean')) for s in series_list)
            if has_log_suffix:
                base_map = {}
                def _base_layer(name):
                    if name.endswith('_running_mean_std_inter'): return name[:-len('_running_mean_std_inter')], 'inter'
                    if name.endswith('_running_mean_std'): return name[:-len('_running_mean_std')], 'std'
                    if name.endswith('_running_mean'): return name[:-len('_running_mean')], 'mean'
                    return name, 'orig'
                for s in series_list:
                    base, layer = _base_layer(s['name'])
                    key = (y_col, base)
                    if key not in base_map:
                        base_map[key] = {'y_col': y_col, 'base': base, 'orig': None, 'mean': None, 'std': None, 'inter': None}
                    base_map[key][layer] = s
                for (y_col_key, base), layers in base_map.items():
                    main = layers['mean'] if layers['mean'] is not None else layers['orig']
                    if main is None:
                        continue
                    row = main.get('row', 1_000_000)
                    pending.append((row, 'agg', (y_col_key, base, layers)))
            else:
                for series in axis_data['series']:
                    row = series.get('row', 1_000_000)
                    pending.append((row, 'simple', series))
        # sort by table row so left panel matches table sequence (not y_col grouping)
        pending.sort(key=lambda x: x[0])
        for row, kind, payload in pending:
            if kind == 'agg':
                y_col_key, base, layers = payload
                has_orig = layers['orig'] is not None
                # Main section always drives the Orig curve (the table row);
                # the mean gets its own subbox. Mean-only rows keep the (avg)
                # main label.
                main = layers['orig'] if has_orig else layers['mean']
                is_mean_main = not has_orig
                std_s = layers['std']
                inter_s = layers['inter']
                # Section presence follows the table intent (entry exists),
                # not the band array content (constant data has no error bars).
                has_std = std_s is not None
                has_inter = inter_s is not None
                sid = f"{y_col_key}_{base}"
                main_color = main['color']
                if main.get('colors'):
                    try: main_color = main['colors'][0]
                    except: pass
                # The original keeps the plain table name; a mean shown as
                # main (mean-only row) carries the (avg) suffix.
                main_props = {
                    'name': base + ' (avg)' if is_mean_main else base,
                    'visible': True,
                    'color': main_color,
                    'linestyle': main.get('linestyle_matlab', '-'),
                    'linewidth': main.get('width', 1.5),
                    'marker': main.get('marker', 'None'),
                    'size': main.get('size', 10),
                }
                std_props = None
                if has_std:
                    c = std_s['color']
                    if std_s.get('colors'):
                        try: c = std_s['colors'][0]
                        except: pass
                    std_props = {'name': base + ' (smooth std)', 'visible': True, 'color': c, 'show_std': True, 'show_legend': True}
                inter_props = None
                if has_inter:
                    c = inter_s['color']
                    if inter_s.get('colors'):
                        try: c = inter_s['colors'][0]
                        except: pass
                    inter_props = {'name': base + ' (avg std)', 'visible': True, 'color': c, 'show_std': True, 'show_legend': True}
                mean_props = None
                mean_s = layers['mean']
                if has_orig and mean_s is not None:
                    mc = mean_s['color']
                    if mean_s.get('colors'):
                        try: mc = mean_s['colors'][0]
                        except: pass
                    mean_props = {'name': base + ' (avg)', 'visible': True, 'color': mc, 'show_std': True, 'show_legend': True}
                sys_props = None
                if main.get('std') is not None:
                    # Raw system-selection std on the main curve (average &
                    # std system, smooth-before off): own subbox so it can be
                    # toggled and labeled like every other band. Defaults keep
                    # the live look: band shown, no legend entry.
                    sys_props = {'name': base + ' (sys std)', 'visible': True, 'color': main_color, 'show_std': True, 'show_legend': False}
                widget = AggregatedLogPropertiesWidget(sid, base, main_props, std_props, inter_props, mean_props, sys_props)
                widget.propertiesChanged.connect(self.redraw_plot)
                widget.sync_btn.toggled.connect(lambda checked, w=widget: self._on_main_sync_toggled(checked, w))
                widget.width_spin.valueChanged.connect(lambda v, w=widget: self._on_main_style_changed(w))
                widget.style_combo.currentTextChanged.connect(lambda t, w=widget: self._on_main_style_changed(w))
                widget.marker_combo.currentTextChanged.connect(lambda t, w=widget: self._on_main_style_changed(w))
                widget.size_spin.valueChanged.connect(lambda v, w=widget: self._on_main_style_changed(w))
                widget.sync_btn.blockSignals(True)
                widget.sync_btn.setChecked(self._main_sync_enabled)
                widget.sync_btn.blockSignals(False)
                self.lines_layout.addWidget(widget)
                self.line_widgets[sid] = widget
                self._aggregated_bases[(y_col_key, base)] = layers
                widget._uid = main.get('uid')
            else:
                series = payload
                sid = series['id']
                is_scatter = series.get('mode') == 'scatter'
                initial_color = series['color']
                if series.get('colors'):
                     try:
                         initial_color = series['colors'][0]
                     except IndexError:
                         pass
                initial_props = {
                    'name': series['name'],
                    'visible': True,
                    'color': initial_color,
                    'linestyle': series.get('linestyle_matlab', '-'),
                    'linewidth': series.get('width', 1.5),
                    'marker': series.get('marker', 'None'),
                    'has_std': series['std'] is not None,
                    'show_std': True,
                    'show_std_legend': True,
                    'mode': series.get('mode', 'line'),
                    'size': series.get('size', 20) if is_scatter else 10,
                    'colors': series.get('colors')
                }
                widget = LinePropertiesWidget(sid, initial_props)
                widget.propertiesChanged.connect(self.redraw_plot)
                self.lines_layout.addWidget(widget)
                self.line_widgets[sid] = widget
                widget._uid = series.get('uid')

    def _on_main_sync_toggled(self, checked, source):
        if self._in_sync:
            return
        self._main_sync_enabled = checked
        for w in self.line_widgets.values():
            if not hasattr(w, 'sync_btn'):
                continue
            w.sync_btn.blockSignals(True)
            w.sync_btn.setChecked(checked)
            w.sync_btn.blockSignals(False)
        if checked and source is not None:
            self._sync_main_styles_from(source)

    def _on_main_style_changed(self, source):
        if not self._main_sync_enabled or self._in_sync:
            return
        # only propagate if source is aggregated main
        if not hasattr(source, 'sync_btn'):
            return
        self._sync_main_styles_from(source)

    def _sync_main_styles_from(self, source):
        if self._in_sync:
            return
        self._in_sync = True
        try:
            src = source.get_properties()
            for sid, w in self.line_widgets.items():
                if w is source or not hasattr(w, 'sync_btn'):
                    continue
                w.width_spin.blockSignals(True)
                w.style_combo.blockSignals(True)
                w.marker_combo.blockSignals(True)
                w.size_spin.blockSignals(True)
                try:
                    w.width_spin.setValue(src['linewidth'])
                    idx = w.style_combo.findText(src['linestyle'])
                    if idx != -1:
                        w.style_combo.setCurrentIndex(idx)
                    m = src['marker']
                    if m is None:
                        m = 'None'
                    idx = w.marker_combo.findText(m)
                    if idx != -1:
                        w.marker_combo.setCurrentIndex(idx)
                    w.size_spin.setValue(src['size'])
                finally:
                    w.width_spin.blockSignals(False)
                    w.style_combo.blockSignals(False)
                    w.marker_combo.blockSignals(False)
                    w.size_spin.blockSignals(False)
            self.redraw_plot()
        finally:
            self._in_sync = False

    def _apply_initial_figsize(self, figsize):
        """
        Resize the window so the matplotlib CANVAS matches figsize (in inches),
        accounting for the dock, toolbar, and window borders (overhead).
        """
        w_in, h_in = figsize
        dpi = self.figure.get_dpi()
        
        target_canvas_w = int(w_in * dpi)
        target_canvas_h = int(h_in * dpi)
        
        # 1. Force a show and layout update to ensure widgets report correct sizes
        self.show() 
        
        # Process events to ensures Qt has calculated layout geometries
        from PyQt6.QtWidgets import QApplication
        QApplication.processEvents()
        
        # 2. Calculate the UI Overhead (Window Size - Canvas Size)
        current_win_size = self.size()
        current_canvas_size = self.canvas.size()
        
        w_overhead = current_win_size.width() - current_canvas_size.width()
        h_overhead = current_win_size.height() - current_canvas_size.height()
        
        # 3. Apply new size
        new_total_w = target_canvas_w + w_overhead
        new_total_h = target_canvas_h + h_overhead
        
        self.resize(new_total_w, new_total_h)
        
        # 4. FIX: Explicitly redraw the plot content so it isn't white
        self.redraw_plot()

    def apply_canvas_size(self):
        target_w = self.width_spin.value()
        target_h = self.height_spin.value()
        unit = self.unit_combo.currentText()
        dpi = self.figure.get_dpi()
        
        target_w_px = 0
        target_h_px = 0
        
        if unit == 'px':
            target_w_px = int(target_w)
            target_h_px = int(target_h)
        elif unit == 'in':
            target_w_px = int(target_w * dpi)
            target_h_px = int(target_h * dpi)
        elif unit == 'mm':
            target_w_px = int((target_w / 25.4) * dpi)
            target_h_px = int((target_h / 25.4) * dpi)
        elif unit == 'cm':
            target_w_px = int((target_w / 2.54) * dpi)
            target_h_px = int((target_h / 2.54) * dpi)
            
        current_win_w = self.width()
        current_win_h = self.height()
        current_canvas_w = self.canvas.width()
        current_canvas_h = self.canvas.height()
        
        overhead_w = current_win_w - current_canvas_w
        overhead_h = current_win_h - current_canvas_h
        
        new_total_w = target_w_px + overhead_w
        new_total_h = target_h_px + overhead_h
        
        self.resize(new_total_w, new_total_h)
        
        # Force matplotlib update
        self.figure.tight_layout()
        self.canvas.draw()

    def on_canvas_resize(self, event):
        if self.figure:
            self.figure.tight_layout()
            self.canvas.draw_idle()
            self.update_size_display()

    def _get_global_config_path(self):
        """Returns path to the global configuration JSON."""
        config_dir = os.path.join(os.path.expanduser('~'), '.LMPvisualizer')
        os.makedirs(config_dir, exist_ok=True)
        return os.path.join(config_dir, 'global_config.json')

    def _load_latex_paths(self):
        """Loads latex paths from global config."""
        path = self._get_global_config_path()
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    return data.get('tex_path', ''), data.get('gs_path', '')
            except:
                pass
        return "", ""

    def _save_latex_paths(self, tex_path, gs_path):
        """Saves latex paths to global config."""
        config_path = self._get_global_config_path()
        data = {}
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            except:
                pass
        
        data['tex_path'] = tex_path
        data['gs_path'] = gs_path
        
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4)

    def _update_system_path(self, tex_path, gs_path):
        """Temporarily updates os.environ['PATH'] for this process."""
        current_path = os.environ['PATH']
        paths_to_add = []
        
        if tex_path and tex_path not in current_path:
            paths_to_add.append(tex_path)
        if gs_path and gs_path not in current_path:
            paths_to_add.append(gs_path)
            
        if paths_to_add:
            # Prepend to ensure our custom paths take precedence
            os.environ['PATH'] = os.pathsep.join(paths_to_add + [current_path])

    def check_requirements(self):
        """Checks if latex, dvipng, and gs are available."""
        # Note: We check specifically for what matplotlib typically needs
        # On Windows, gs might be gswin64c, but shutil.which('gs') often fails if not aliased.
        # However, Matplotlib handles the internal name check if the DIR is in path.
        reqs = ['latex', 'dvipng', 'gs']
        
        # If on windows, ghostscript might be strictly named gswin64c or gswin32c
        if sys.platform.startswith('win'):
            # This is a loose check. If the GS folder is in path, we assume it's good.
            # Matplotlib internals are complex here, but checking for latex is the main gatekeeper.
            if shutil.which('gswin64c') or shutil.which('gswin32c'):
                reqs.remove('gs')
                
        missing = [tool for tool in reqs if shutil.which(tool) is None]
        return missing

    def on_latex_toggled(self, checked):
        # Update session state for future windows
        PopOutWindow._session_latex_enabled = checked

        if not checked:
            # Disable LaTeX and revert to standard Matplotlib sans-serif font
            plt.rcParams['text.usetex'] = False
            plt.rcParams['font.family'] = 'sans-serif'
            plt.rcParams['font.serif'] = ['DejaVu Serif']
            plt.rcParams['font.sans-serif'] = ['DejaVu Sans']
            self.redraw_plot()
            return

        # 1. Load saved paths and apply them to environment
        saved_tex, saved_gs = self._load_latex_paths()
        self._update_system_path(saved_tex, saved_gs)

        # 2. Check availability
        missing = self.check_requirements()

        # 3. If missing, prompt user
        if missing:
            dialog = LatexConfigDialog(self, saved_tex, saved_gs)
            if dialog.exec():
                new_tex, new_gs = dialog.get_paths()
                
                # Update Environment immediately
                self._update_system_path(new_tex, new_gs)
                
                # Save for future
                self._save_latex_paths(new_tex, new_gs)
                
                # Check again
                missing_retry = self.check_requirements()
                if missing_retry:
                    logger.warning("Still missing LaTeX requirements: %s", ", ".join(missing_retry))
                    QMessageBox.warning(self, "Still Missing Requirements", 
                        f"Could not find: {', '.join(missing_retry)}\n"
                        "Please ensure the directories point to the folder containing the executables.")
                    self.latex_check.blockSignals(True)
                    self.latex_check.setChecked(False)
                    self.latex_check.blockSignals(False)
                    PopOutWindow._session_latex_enabled = False
                    return
            else:
                # User cancelled dialog
                self.latex_check.blockSignals(True)
                self.latex_check.setChecked(False)
                self.latex_check.blockSignals(False)
                PopOutWindow._session_latex_enabled = False
                return

        # 4. Try enabling Matplotlib LaTeX with Computer Modern Fonts
        try:
            plt.rcParams['text.usetex'] = True
            
            # FORCE FONT TO SERIF (Computer Modern Roman)
            plt.rcParams['font.family'] = 'serif'
            plt.rcParams['font.serif'] = ['Computer Modern Roman']
            
            # Optional: If you want sans-serif to also look 'Latexy' (Computer Modern Sans)
            # plt.rcParams['font.sans-serif'] = ['Computer Modern Sans serif']
            
            self.redraw_plot()
        except Exception as e:
            logger.warning("Error enabling LaTeX: %s", e)
            QMessageBox.warning(self, "LaTeX Error", f"Error enabling LaTeX:\n{e}\n\nMake sure Ghostscript and MikTeX/TeXLive are installed correctly.")
            self.latex_check.blockSignals(True)
            self.latex_check.setChecked(False)
            self.latex_check.blockSignals(False)
            
            # Revert settings on error
            PopOutWindow._session_latex_enabled = False
            plt.rcParams['text.usetex'] = False
            plt.rcParams['font.family'] = 'sans-serif'
            self.redraw_plot()

    @staticmethod
    def _oriented_limits(limits, inverted):
        return plot_model.oriented_limits(limits, inverted)

    def redraw_plot(self):
        # 1. Capture current view limits to prevent auto-rescaling on property updates
        saved_xlim = None
        saved_ylim_prim = None
        saved_ylims_sec = {} # {y_col: (min, max)}
        
        if self.figure.axes:
            # Assuming ax_primary is always axes[0]
            ax_prim = self.figure.axes[0]
            saved_xlim = ax_prim.get_xlim()
            saved_ylim_prim = ax_prim.get_ylim()
            
            # Identify secondary axes (twinx)
            # We map them back to y_cols based on the stored configs or simple index order
            # The recreation loop sorts y_cols by priority. We must match that.
            # However, simpler approach: we stored 'axes_map' implicitly? No.
            # We can rely on the fact that we rebuild them in the EXACT same order.
            # Primary is 0. Secondaries are 1..N.
            pass 
            
        self.figure.clear()
        
        # Get separate font sizes
        font_title = self.font_title_spin.value()
        font_label = self.font_label_spin.value()
        font_tick = self.font_tick_spin.value()
        font_legend = self.font_legend_spin.value()
        
        ax_primary = self.figure.add_subplot(111)
        ax_primary.set_title(self.title_edit.text(), fontsize=font_title)
        ax_primary.set_xlabel(self.x_label_edit.text(), fontsize=font_label)

        ax_primary.tick_params(axis='both', labelsize=font_tick, direction='in')

        # Apply X Limits: Saved > Initial Data (clamp for log via shared helper)
        def _clamp_log(lim, is_log):
            if not is_log:
                return lim
            try:
                data_arrays = []
                for yc in self.plot_data.get('y_axes', {}).values():
                    for s in yc.get('series', []):
                        data_arrays.append(s.get('x', []))
                return plot_model.clamp_log_limits(lim, True, data_arrays)
            except Exception:
                return lim
        x_is_log = self.x_log_check.isChecked()
        # when linear, prefer initial limits to restore 0 after log; when log, prefer saved then initial
        if x_is_log:
            if saved_xlim:
                saved_xlim = _clamp_log(saved_xlim, True)
                ax_primary.set_xlim(saved_xlim)
            elif 'x_limits' in self.plot_data:
                lim = self._oriented_limits(self.plot_data['x_limits'], self.plot_data.get('x_inverted', False))
                lim = _clamp_log(lim, True)
                ax_primary.set_xlim(lim)
        else:
            if 'x_limits' in self.plot_data:
                lim = self._oriented_limits(self.plot_data['x_limits'], self.plot_data.get('x_inverted', False))
                ax_primary.set_xlim(lim)
            elif saved_xlim:
                ax_primary.set_xlim(saved_xlim)
        
        if x_is_log:
            try:
                ax_primary.set_xscale('log')
            except Exception:
                pass
            # keep x alignment if needed (tight)
            try:
                self.figure.tight_layout()
            except Exception:
                pass
        
        if self.grid_check.isChecked():
            ax_primary.grid(True, which='both', linestyle='--', linewidth=0.5, alpha=0.7)

        y_cols = list(self.plot_data['y_axes'].keys())
        if not y_cols:
            self.canvas.draw()
            return
            
        # Calculate max priority for each axis to determine draw order (z-order)
        axis_priorities = {}
        for y_col in y_cols:
            max_prio = 0
            for series in self.plot_data['y_axes'][y_col]['series']:
                max_prio = max(max_prio, series.get('layer_priority', 0))
            axis_priorities[y_col] = max_prio
            
        # Sort y_cols so axes with higher priority plots are drawn later (on top)
        y_cols.sort(key=lambda y: axis_priorities[y], reverse=False)

        axes_map = {y_cols[0]: ax_primary}
        
        # Configure Primary Y-Axis
        config_prim = self.y_configs[y_cols[0]]
        ax_primary.set_ylabel(config_prim['label_edit'].text(), fontsize=font_label)
        # Apply Y Limits: clamp for log, prefer initial when linear to restore 0 after log
        y_is_log = config_prim['log_check'].isChecked()
        y_lim = None
        if y_is_log:
            if saved_ylim_prim:
                y_lim = saved_ylim_prim
            elif 'y_limits' in self.plot_data['y_axes'][y_cols[0]]:
                y_axis_data = self.plot_data['y_axes'][y_cols[0]]
                y_lim = self._oriented_limits(y_axis_data['y_limits'], y_axis_data.get('y_inverted', False))
        else:
            if 'y_limits' in self.plot_data['y_axes'][y_cols[0]]:
                y_axis_data = self.plot_data['y_axes'][y_cols[0]]
                y_lim = self._oriented_limits(y_axis_data['y_limits'], y_axis_data.get('y_inverted', False))
            elif saved_ylim_prim:
                y_lim = saved_ylim_prim
        if y_lim is not None:
            if y_is_log:
                try:
                    y_arrays = [s.get('y', []) for s in self.plot_data['y_axes'][y_cols[0]]['series']]
                    y_lim = plot_model.clamp_log_limits(y_lim, True, y_arrays)
                except Exception:
                    pass
            ax_primary.set_ylim(y_lim)
            # when log deselected, re-apply view alignment if needed
            if not y_is_log:
                try:
                    self.figure.tight_layout()
                except Exception:
                    pass
        if y_is_log:
            try:
                ax_primary.set_yscale('log')
            except Exception:
                pass
            
        # Apply Axis Colors (Primary) via shared helper (same rgb as exports).
        y_data_prim = self.plot_data['y_axes'][y_cols[0]]
        if 'color' in y_data_prim:
            col = y_data_prim['color']
            rgb = plot_model.qcolor_to_rgb(col, default=(0.0, 0.0, 0.0))
            ax_primary.yaxis.label.set_color(rgb)
            ax_primary.tick_params(axis='y', colors=rgb, direction='in')
            # Primary left spine
            ax_primary.spines['left'].set_color(rgb)
            
            # X-axis color is usually black, but we can keep it standard
            ax_primary.xaxis.label.set_color('black')
            ax_primary.tick_params(axis='x', colors='black', direction='in')
            ax_primary.spines['bottom'].set_color('black')
            ax_primary.spines['top'].set_visible(False)
            ax_primary.spines['right'].set_visible(False)

        # Configure Secondary Y-Axes
        # We need to retrieve saved limits for secondaries. 
        # Since we just cleared self.figure.axes, we can't access them by index anymore.
        # But we can assume the index order matches y_cols order because sorting is deterministic.
        
        for i, y_col in enumerate(y_cols[1:], start=1):
            ax_new = ax_primary.twinx()
            ax_new.spines['top'].set_visible(False)
            ax_new.spines['left'].set_visible(False)
            
            if i > 1:
                ax_new.spines['right'].set_position(('outward', 60 * (i - 1)))
            
            config = self.y_configs[y_col]
            ax_new.set_ylabel(config['label_edit'].text(), fontsize=font_label)
            ax_new.tick_params(axis='y', labelsize=font_tick, direction='in')
            
            # Apply Y Limits (Secondary) with log clamp, then set scale
            y_is_log = config['log_check'].isChecked()
            y_lim = None
            if 'y_limits' in self.plot_data['y_axes'][y_col]:
                 y_axis_data = self.plot_data['y_axes'][y_col]
                 y_lim = self._oriented_limits(y_axis_data['y_limits'], y_axis_data.get('y_inverted', False))
            if y_lim is not None:
                if y_is_log:
                    try:
                        y_arrays = [s.get('y', []) for s in self.plot_data['y_axes'][y_col]['series']]
                        y_lim = plot_model.clamp_log_limits(y_lim, True, y_arrays)
                    except Exception:
                        pass
                ax_new.set_ylim(y_lim)
            if y_is_log:
                try:
                    ax_new.set_yscale('log')
                except Exception:
                    pass
            
            # Apply Axis Colors (Secondary) via shared helper.
            y_data_sec = self.plot_data['y_axes'][y_col]
            if 'color' in y_data_sec:
                col = y_data_sec['color']
                rgb = plot_model.qcolor_to_rgb(col, default=(0.0, 0.0, 0.0))
                ax_new.yaxis.label.set_color(rgb)
                ax_new.tick_params(axis='y', colors=rgb, direction='in')
                ax_new.spines['right'].set_color(rgb)
            
            axes_map[y_col] = ax_new

        # legend = table row order, selected rows at bottom (most top plot last)
        legend_entries = []  # (is_selected, row, sub_prio, handle, label)
        
        # Iterate axes for drawing (z-order); collect legend_entries separately sorted by row
        for y_col, axis_data in self.plot_data['y_axes'].items():
            ax = axes_map[y_col]
            agg_for_col = []
            if hasattr(self, '_aggregated_bases'):
                agg_for_col = [(b, l) for (yc, b), l in self._aggregated_bases.items() if yc == y_col]
            if agg_for_col:
                def _agg_prio(item):
                    _, layers = item
                    m = layers.get('mean') if layers.get('mean') is not None else layers.get('orig')
                    return m.get('layer_priority', 0) if m else 0
                agg_for_col.sort(key=_agg_prio)
                for base, layers in agg_for_col:
                    sid = f"{y_col}_{base}"
                    if sid not in self.line_widgets: continue
                    props = self.line_widgets[sid].get_properties()
                    if not props.get('visible', True):
                        continue
                    main = layers.get('orig') if layers.get('orig') is not None else layers.get('mean')
                    if main is None:
                        continue
                    all_x = main['x']
                    all_y = main['y']
                    prio = main.get('layer_priority', 0)
                    is_sel = 1 if plot_model.is_selected_priority(prio) else 0  # selected row has +100 z_offset -> bottom of legend
                    row = main.get('row', plot_model.DEFAULT_ROW)
                    z_val = plot_model.zorder_for_priority(prio)
                    if props.get('show_legend', True):
                        main_label = props.get('label')
                    else:
                        main_label = None
                    c_main = props.get('color', main['color'])
                    color_tuple_main = plot_model.qcolor_to_rgba(c_main)
                    main_std = main.get('std')
                    if main_std is not None and getattr(self.line_widgets[sid], 'has_sys', False) \
                            and props.get('sys_visible', True) and props.get('sys_show', True):
                        # Raw system-selection std on the main curve: own
                        # toggle, legend entry and color via the sys std
                        # subbox (defaults: shown, unlabeled, main color).
                        try:
                            s_legend_on = props.get('sys_show_legend', False)
                            s_label = props.get('sys_label') if s_legend_on else None
                            s_c = plot_model.qcolor_to_rgba(props.get('sys_color', c_main))
                            s_fill = plot_model.draw_std_fill(ax, all_x, all_y, main_std, s_c, label=s_label, zorder=z_val - 0.15, alpha=plot_model.AVG_FILL_ALPHA)
                            if s_label:
                                legend_entries.append((is_sel, row, 0, s_fill, s_label))
                        except Exception:
                            pass
                    std_s = layers.get('std')
                    inter_s = layers.get('inter')
                    all_std = std_s.get('std') if std_s else None
                    all_inter = inter_s.get('std') if inter_s else None
                    if props.get('std_visible', False) and props.get('std_show', True) and all_std is not None:
                        try:
                            sd_c = props.get('std_color', c_main)
                            fill_label = props.get('std_label') if props.get('std_show_legend', True) else None
                            if fill_label == "": fill_label = None
                            fill = plot_model.draw_std_fill(ax, all_x, all_y, all_std, plot_model.qcolor_to_rgba(sd_c), label=fill_label, zorder=z_val - 0.05, alpha=plot_model.STD_FILL_ALPHA)
                            if fill_label:
                                # sub_prio 0 = avg std pale, 1 = smooth std, keep interleaving per row
                                legend_entries.append((is_sel, row, 1, fill, fill_label))
                        except Exception:
                            pass
                    if props.get('inter_visible', False) and props.get('inter_show', True) and all_inter is not None:
                        try:
                            ic = props.get('inter_color', c_main)
                            fill_label = props.get('inter_label') if props.get('inter_show_legend', True) else None
                            if fill_label == "": fill_label = None
                            fill = plot_model.draw_std_fill(ax, all_x, all_y, all_inter, plot_model.qcolor_to_rgba(ic), label=fill_label, zorder=z_val - 0.1, alpha=plot_model.AVG_FILL_ALPHA)
                            if fill_label:
                                legend_entries.append((is_sel, row, 0, fill, fill_label))
                        except Exception:
                            pass
                    mode = main.get('mode', 'line')
                    if mode == 'scatter':
                        if main.get('colors') is not None:
                            try:
                                colors_to_use = [plot_model.qcolor_to_rgba(c) for c in main['colors']]
                            except Exception:
                                colors_to_use = None
                            scatter_h = plot_model.draw_scatter(ax, all_x, all_y, per_point_colors=colors_to_use, size=props.get('size', 10)**2, marker=props.get('marker', 'o') if props.get('marker') else 'o', label=main_label, zorder=z_val)
                        else:
                            scatter_h = plot_model.draw_scatter(ax, all_x, all_y, color_rgba=color_tuple_main, size=props.get('size', 10)**2, marker=props.get('marker', 'None') if props.get('marker') not in (None, 'None') else 'o', label=main_label, zorder=z_val)
                        if main_label:
                            legend_entries.append((is_sel, row, 2, scatter_h, main_label))
                    else:
                        lstyle = props.get('linestyle', '-')
                        marker = props.get('marker', 'None')
                        line = plot_model.draw_line(ax, all_x, all_y, color_tuple_main, linewidth=props.get('linewidth', 1.5), linestyle=lstyle, marker=marker, markersize=props.get('size', 6), label=main_label, zorder=z_val)
                        if line is not None and main_label:
                            legend_entries.append((is_sel, row, 2, line, main_label))
                    mean_s = layers.get('mean')
                    if layers.get('orig') is not None and mean_s is not None \
                            and getattr(self.line_widgets[sid], 'has_mean', False) \
                            and props.get('mean_visible', True) and props.get('mean_show', True):
                        # Mean curve beside the orig main line: own color and
                        # legend label, width/style shared with main (one
                        # style per table row, as live).
                        try:
                            m_legend_on = props.get('mean_show_legend', True)
                            m_label = props.get('mean_label') if m_legend_on else None
                            m_c = plot_model.qcolor_to_rgba(props.get('mean_color', mean_s.get('color')))
                            _, _, m_entries = plot_model.render_series(
                                ax, mean_s, color=m_c, label=m_label,
                                linestyle=props.get('linestyle', '-'),
                                linewidth=props.get('linewidth', 1.5),
                                marker=props.get('marker', 'None'),
                                markersize=props.get('size', 6),
                                show_std=False, show_legend=m_legend_on,
                            )
                            # render_series derives sub_prio from layer
                            # priority (mean band prio -> 0); the avg line
                            # belongs last per row: bands, orig, avg.
                            m_entries = [(e[0], e[1], 3, e[3], e[4]) for e in m_entries]
                            legend_entries.extend(m_entries)
                        except Exception:
                            pass
                continue
            sorted_series = sorted(axis_data['series'], key=lambda s: s.get('layer_priority', 0))
            for series in sorted_series:
                sid = series['id']
                if sid not in self.line_widgets: continue
                props = self.line_widgets[sid].get_properties()
                if not props['visible']: continue
                # Consume the shared series renderer (same helper the
                # exports use): fill + line/scatter, show_std vs
                # show_std_legend stay distinct.
                color_tuple = plot_model.qcolor_to_rgba(props['color'])
                try:
                    scatter_area = float(props.get('size', 6)) ** 2
                except Exception:
                    scatter_area = 36.0
                _, _, entries = plot_model.render_series(
                    ax, series, color=color_tuple,
                    label=props.get('label'),
                    linestyle=props.get('linestyle', '-'),
                    linewidth=props.get('linewidth', 1.5),
                    marker=props.get('marker', 'None'),
                    markersize=props.get('size', 6),
                    show_std=props.get('show_std', True),
                    show_std_legend=props.get('show_std_legend', True),
                    show_legend=True,
                    scatter_size=scatter_area,
                )
                legend_entries.extend(entries)
        # sort legend exactly as table rows: non-selected in row order, selected at bottom (most top plot last)
        legend_entries = plot_model.sort_legend_entries(legend_entries)
        all_handles = [h for _,_,_,h,_ in legend_entries]
        all_labels = [l for _,_,_,_,l in legend_entries]
        if self.show_legend_check.isChecked() and all_handles:
            loc = self.legend_loc.currentText()
            frame = self.legend_frame.isChecked()
            draggable = self.legend_draggable.isChecked()
            
            target_ax = axes_map[y_cols[-1]]
            leg = target_ax.legend(all_handles, all_labels, loc=loc, frameon=frame, fontsize=font_legend)
            leg.set_zorder(10000) # Force on top
            leg.set_in_layout(False)
            if draggable:
                leg.set_draggable(True)
        
        self.figure.tight_layout()
        self.canvas.draw()

    def update_size_display(self):
        if not self.figure: return
        
        size = self.canvas.size()
        w_px = size.width()
        h_px = size.height()
        
        unit = self.unit_combo.currentText()
        dpi = self.figure.get_dpi()
        
        w_val, h_val = 0.0, 0.0
        
        if unit == 'px':
            w_val, h_val = float(w_px), float(h_px)
            self.width_spin.setDecimals(0)
            self.height_spin.setDecimals(0)
        elif unit == 'in':
            w_val, h_val = w_px / dpi, h_px / dpi
            self.width_spin.setDecimals(2)
            self.height_spin.setDecimals(2)
        elif unit == 'mm':
            w_val = (w_px / dpi) * 25.4
            h_val = (h_px / dpi) * 25.4
            self.width_spin.setDecimals(1)
            self.height_spin.setDecimals(1)
        elif unit == 'cm':
            w_val = (w_px / dpi) * 2.54
            h_val = (h_px / dpi) * 2.54
            self.width_spin.setDecimals(2)
            self.height_spin.setDecimals(2)

        self.width_spin.blockSignals(True)
        self.height_spin.blockSignals(True)
        self.width_spin.setValue(w_val)
        self.height_spin.setValue(h_val)
        self.width_spin.blockSignals(False)
        self.height_spin.blockSignals(False)

    # --- Popout presets (settings snapshot only, never x/y arrays) ---

    def collect_all(self):
        """Snapshot the entire settings dock + canvas size as plain values."""
        try:
            name = self.source_name or berlin_now_str()
        except Exception:
            name = "preset"
        glob = {}
        try:
            glob['title'] = self.title_edit.text()
        except Exception:
            glob['title'] = ""
        try:
            glob['grid'] = bool(self.grid_check.isChecked())
        except Exception:
            glob['grid'] = True
        try:
            glob['latex'] = bool(self.latex_check.isChecked())
        except Exception:
            glob['latex'] = False
        try:
            glob['fonts'] = {
                'title': int(self.font_title_spin.value()),
                'label': int(self.font_label_spin.value()),
                'tick': int(self.font_tick_spin.value()),
                'legend': int(self.font_legend_spin.value()),
            }
        except Exception:
            glob['fonts'] = {'title': 12, 'label': 11, 'tick': 12, 'legend': 12}
        try:
            glob['x_label'] = self.x_label_edit.text()
        except Exception:
            glob['x_label'] = ""
        try:
            glob['x_log'] = bool(self.x_log_check.isChecked())
        except Exception:
            glob['x_log'] = False
        try:
            y_labels, y_logs = {}, {}
            for y_col, cfg in dict(getattr(self, 'y_configs', {}) or {}).items():
                try:
                    y_labels[y_col] = cfg['label_edit'].text()
                except Exception:
                    pass
                try:
                    y_logs[y_col] = bool(cfg['log_check'].isChecked())
                except Exception:
                    pass
            glob['y_labels'] = y_labels
            glob['y_logs'] = y_logs
        except Exception:
            glob.setdefault('y_labels', {})
            glob.setdefault('y_logs', {})
        try:
            glob['legend'] = {
                'show': bool(self.show_legend_check.isChecked()),
                'loc': self.legend_loc.currentText(),
                'frame': bool(self.legend_frame.isChecked()),
                'draggable': bool(self.legend_draggable.isChecked()),
            }
        except Exception:
            glob['legend'] = {'show': True, 'loc': 'best', 'frame': True, 'draggable': True}
        try:
            # Save-time text defaults so apply can tell customized text apart
            # from untouched defaults (which must follow current data).
            _pd = getattr(self, 'plot_data', {}) or {}
            if isinstance(_pd, dict):
                _t = _pd.get('title', '')
                if isinstance(_t, str):
                    glob['title_default'] = _t
                _x = _pd.get('x_label', '')
                if isinstance(_x, str):
                    glob['x_default'] = _x
                _yd = {}
                for _yc, _ad in dict(_pd.get('y_axes', {}) or {}).items():
                    try:
                        _l = _ad.get('label', _yc) if isinstance(_ad, dict) else _yc
                        _yd[_yc] = _l if isinstance(_l, str) else str(_yc)
                    except Exception:
                        pass
                glob['y_defaults'] = _yd
        except Exception:
            pass
        try:
            dpi = self.figure.get_dpi() if getattr(self, 'figure', None) is not None else 100
            cw, ch = self.canvas.width(), self.canvas.height()
            glob['figsize'] = [float(cw) / float(dpi), float(ch) / float(dpi)]
        except Exception:
            try:
                glob['figsize'] = [float(self.initial_figsize[0]), float(self.initial_figsize[1])]
            except Exception:
                glob['figsize'] = [11.0, 6.5]
        try:
            glob['width'] = float(self.width_spin.value())
            glob['height'] = float(self.height_spin.value())
            glob['unit'] = self.unit_combo.currentText()
        except Exception:
            pass
        series = {}
        try:
            for sid, w in dict(getattr(self, 'line_widgets', {}) or {}).items():
                try:
                    props = _plain_series_props(w.get_properties())
                    try:
                        _u = getattr(w, '_uid', None)
                    except Exception:
                        _u = None
                    # JSON-safe stable id only; anything exotic degrades to sid matching.
                    if _u is not None and isinstance(_u, (int, str)) and not isinstance(_u, bool):
                        props['uid'] = _u
                    # Save-time defaults for the custom-only text rule.
                    try:
                        _sd = getattr(w, '_series_default', None)
                    except Exception:
                        _sd = None
                    if isinstance(_sd, str):
                        props['label_default'] = _sd
                    try:
                        _sg = getattr(w, 'std_group', None)
                        if _sg is not None:
                            _bd = getattr(_sg, '_default_label', None)
                            if isinstance(_bd, str):
                                props['std_label_default'] = _bd
                        _ig = getattr(w, 'inter_group', None)
                        if _ig is not None:
                            _bd = getattr(_ig, '_default_label', None)
                            if isinstance(_bd, str):
                                props['inter_label_default'] = _bd
                        _og = getattr(w, 'mean_group', None)
                        if _og is not None:
                            _bd = getattr(_og, '_default_label', None)
                            if isinstance(_bd, str):
                                props['mean_label_default'] = _bd
                        _yg = getattr(w, 'sys_group', None)
                        if _yg is not None:
                            _bd = getattr(_yg, '_default_label', None)
                            if isinstance(_bd, str):
                                props['sys_label_default'] = _bd
                    except Exception:
                        pass
                    series[sid] = props
                except Exception:
                    continue
        except Exception:
            series = {}
        try:
            return copy.deepcopy({'name': name, 'global': glob, 'series': series})
        except Exception:
            return {'name': name, 'global': glob, 'series': series}

    def apply_all(self, preset):
        """Restore every dock control + canvas size; unknown sids ignored."""
        try:
            data = dict(preset or {})
        except Exception:
            return
        try:
            pname = data.get('name')
            if isinstance(pname, str) and pname:
                self.source_name = pname
        except Exception:
            pass
        glob = data.get('global', {})
        if not isinstance(glob, dict):
            glob = {}
        series_saved = data.get('series', {})
        if not isinstance(series_saved, dict):
            series_saved = {}

        def _set_checked(widget, val):
            widget.blockSignals(True)
            try:
                widget.setChecked(bool(val))
            finally:
                widget.blockSignals(False)

        def _set_text(widget, val):
            widget.blockSignals(True)
            try:
                widget.setText(str(val))
            finally:
                widget.blockSignals(False)

        # -- globals --
        try:
            _t = glob.get('title')
            if isinstance(_t, str):
                _td = glob.get('title_default')
                if _should_restore_label(_t, _td if isinstance(_td, str) else None, True):
                    _set_text(self.title_edit, _t)
        except Exception:
            pass
        try:
            if isinstance(glob.get('grid'), bool):
                _set_checked(self.grid_check, glob['grid'])
        except Exception:
            pass
        try:
            fonts = glob.get('fonts', {})
            if isinstance(fonts, dict):
                for key, spin in (('title', self.font_title_spin),
                                  ('label', self.font_label_spin),
                                  ('tick', self.font_tick_spin),
                                  ('legend', self.font_legend_spin)):
                    try:
                        v = fonts.get(key)
                        if isinstance(v, (int, float)):
                            spin.blockSignals(True)
                            try:
                                spin.setValue(int(v))
                            finally:
                                spin.blockSignals(False)
                    except Exception:
                        pass
        except Exception:
            pass
        try:
            _x = glob.get('x_label')
            if isinstance(_x, str):
                _xd = glob.get('x_default')
                if _should_restore_label(_x, _xd if isinstance(_xd, str) else None, True):
                    _set_text(self.x_label_edit, _x)
        except Exception:
            pass
        try:
            if isinstance(glob.get('x_log'), bool):
                _set_checked(self.x_log_check, glob['x_log'])
        except Exception:
            pass
        try:
            y_labels = glob.get('y_labels', {})
            y_logs = glob.get('y_logs', {})
            y_defaults = glob.get('y_defaults', {})
            if not isinstance(y_labels, dict):
                y_labels = {}
            if not isinstance(y_defaults, dict):
                y_defaults = {}
            if not isinstance(y_logs, dict):
                y_logs = {}
            for y_col, cfg in dict(getattr(self, 'y_configs', {}) or {}).items():
                try:
                    if y_col in y_labels and isinstance(y_labels[y_col], str):
                        _yd = y_defaults.get(y_col) if isinstance(y_defaults, dict) else None
                        if _should_restore_label(y_labels[y_col], _yd if isinstance(_yd, str) else None, True):
                            _set_text(cfg['label_edit'], y_labels[y_col])
                except Exception:
                    pass
                try:
                    if y_col in y_logs and isinstance(y_logs[y_col], bool):
                        _set_checked(cfg['log_check'], y_logs[y_col])
                except Exception:
                    pass
        except Exception:
            pass
        try:
            leg = glob.get('legend', {})
            if isinstance(leg, dict):
                if isinstance(leg.get('show'), bool):
                    _set_checked(self.show_legend_check, leg['show'])
                if isinstance(leg.get('loc'), str):
                    try:
                        idx = self.legend_loc.findText(leg['loc'])
                        if idx != -1:
                            self.legend_loc.blockSignals(True)
                            try:
                                self.legend_loc.setCurrentIndex(idx)
                            finally:
                                self.legend_loc.blockSignals(False)
                    except Exception:
                        pass
                if isinstance(leg.get('frame'), bool):
                    _set_checked(self.legend_frame, leg['frame'])
                if isinstance(leg.get('draggable'), bool):
                    _set_checked(self.legend_draggable, leg['draggable'])
        except Exception:
            pass
        # LaTeX without modal dialogs: never prompt inside apply.
        try:
            if isinstance(glob.get('latex'), bool):
                want = bool(glob['latex'])
                self.latex_check.blockSignals(True)
                try:
                    if want:
                        try:
                            missing = self.check_requirements()
                        except Exception:
                            missing = ['latex']
                        if missing:
                            self.latex_check.setChecked(False)
                            PopOutWindow._session_latex_enabled = False
                            try:
                                plt.rcParams['text.usetex'] = False
                                plt.rcParams['font.family'] = 'sans-serif'
                            except Exception:
                                pass
                        else:
                            self.latex_check.setChecked(True)
                            PopOutWindow._session_latex_enabled = True
                            try:
                                plt.rcParams['text.usetex'] = True
                                plt.rcParams['font.family'] = 'serif'
                                plt.rcParams['font.serif'] = ['Computer Modern Roman']
                            except Exception:
                                pass
                    else:
                        self.latex_check.setChecked(False)
                        PopOutWindow._session_latex_enabled = False
                        try:
                            plt.rcParams['text.usetex'] = False
                            plt.rcParams['font.family'] = 'sans-serif'
                        except Exception:
                            pass
                finally:
                    self.latex_check.blockSignals(False)
        except Exception:
            pass

        # -- series: exact sid first, stable uid (log plot_id) as fallback --
        try:
            widgets = self.line_widgets or {}
            applied = set()
            uid_index = {}
            try:
                for _w in widgets.values():
                    try:
                        _u = getattr(_w, '_uid', None)
                    except Exception:
                        _u = None
                    if _u is not None and isinstance(_u, (int, str)) and not isinstance(_u, bool):
                        uid_index.setdefault(_u, []).append(_w)
            except Exception:
                uid_index = {}
            for sid, saved in series_saved.items():
                try:
                    if not isinstance(saved, dict):
                        continue
                    w = widgets.get(sid)
                    if w is None:
                        try:
                            _u = saved.get('uid')
                        except Exception:
                            _u = None
                        if _u is not None and isinstance(_u, (int, str)) and not isinstance(_u, bool):
                            for _c in uid_index.get(_u, []):
                                if id(_c) not in applied:
                                    w = _c
                                    break
                    if w is None:
                        continue
                    applied.add(id(w))
                    try:
                        try:
                            _exact = (w is widgets.get(sid))
                        except Exception:
                            _exact = False
                        self._apply_series_widget(w, saved, _exact)
                    except Exception:
                        continue
                except Exception:
                    continue
        except Exception:
            pass

        # -- canvas size (spins + unit win; figsize is the fallback) --
        try:
            width = glob.get('width')
            height = glob.get('height')
            unit = glob.get('unit')
            if isinstance(width, (int, float)) and isinstance(height, (int, float)) \
                    and isinstance(unit, str) and unit in ('px', 'in', 'mm', 'cm'):
                self.unit_combo.blockSignals(True)
                self.width_spin.blockSignals(True)
                self.height_spin.blockSignals(True)
                try:
                    self.unit_combo.setCurrentText(unit)
                    self.width_spin.setValue(float(width))
                    self.height_spin.setValue(float(height))
                finally:
                    self.unit_combo.blockSignals(False)
                    self.width_spin.blockSignals(False)
                    self.height_spin.blockSignals(False)
                try:
                    self.apply_canvas_size()
                except Exception:
                    pass
            elif isinstance(glob.get('figsize'), (list, tuple)) and len(glob['figsize']) == 2:
                try:
                    fw, fh = float(glob['figsize'][0]), float(glob['figsize'][1])
                    self.unit_combo.blockSignals(True)
                    self.width_spin.blockSignals(True)
                    self.height_spin.blockSignals(True)
                    try:
                        self.unit_combo.setCurrentText('in')
                        self.width_spin.setValue(fw)
                        self.height_spin.setValue(fh)
                    finally:
                        self.unit_combo.blockSignals(False)
                        self.width_spin.blockSignals(False)
                        self.height_spin.blockSignals(False)
                    try:
                        self.apply_canvas_size()
                    except Exception:
                        pass
                except Exception:
                    pass
        except Exception:
            pass

        try:
            self.redraw_plot()
        except Exception:
            pass

    def _apply_series_widget(self, w, saved, exact_hit=True):
        w.blockSignals(True)
        try:
            if isinstance(saved.get('visible'), bool):
                try:
                    w.setChecked(bool(saved['visible']))
                except Exception:
                    pass
            is_agg = hasattr(w, 'main_label')
            if is_agg:
                try:
                    if _should_restore_label(saved.get('label'), saved.get('label_default'), exact_hit):
                        w.main_label.setText(saved['label'])
                except Exception:
                    pass
                try:
                    if isinstance(saved.get('show_legend'), bool):
                        w.main_legend_check.blockSignals(True)
                        try:
                            w.main_legend_check.setChecked(bool(saved['show_legend']))
                        finally:
                            w.main_legend_check.blockSignals(False)
                except Exception:
                    pass
                try:
                    if 'color' in saved:
                        w.main_color.blockSignals(True)
                        try:
                            w.main_color.set_color(_hex_to_qcolor(saved['color']))
                        finally:
                            w.main_color.blockSignals(False)
                except Exception:
                    pass
                for attr, key in (('width_spin', 'linewidth'), ('size_spin', 'size')):
                    try:
                        if isinstance(saved.get(key), (int, float)):
                            sp = getattr(w, attr, None)
                            if sp is not None:
                                sp.blockSignals(True)
                                try:
                                    sp.setValue(float(saved[key]))
                                finally:
                                    sp.blockSignals(False)
                    except Exception:
                        pass
                for attr, key in (('style_combo', 'linestyle'), ('marker_combo', 'marker')):
                    try:
                        v = saved.get(key)
                        if v is None and key == 'marker':
                            v = 'None'
                        if isinstance(v, str):
                            cb = getattr(w, attr, None)
                            if cb is not None:
                                idx = cb.findText(v)
                                if idx != -1:
                                    cb.blockSignals(True)
                                    try:
                                        cb.setCurrentIndex(idx)
                                    finally:
                                        cb.blockSignals(False)
                    except Exception:
                        pass
                for prefix, group_attr, has_attr in (
                        ('std', 'std_group', 'has_std'),
                        ('inter', 'inter_group', 'has_inter'),
                        ('mean', 'mean_group', 'has_mean'),
                        ('sys', 'sys_group', 'has_sys')):
                    try:
                        if not getattr(w, has_attr, False):
                            continue
                        grp = getattr(w, group_attr, None)
                        if grp is None:
                            continue
                        vis = saved.get(prefix + '_visible', saved.get(prefix + '_show'))
                        if isinstance(vis, bool):
                            grp.blockSignals(True)
                            try:
                                grp.setChecked(bool(vis))
                            finally:
                                grp.blockSignals(False)
                        lbl = saved.get(prefix + '_label')
                        if _should_restore_label(lbl, saved.get(prefix + '_label_default'), exact_hit):
                            try:
                                grp._le.setText(lbl)
                            except Exception:
                                pass
                        sl = saved.get(prefix + '_show_legend')
                        if isinstance(sl, bool):
                            try:
                                grp._lc.blockSignals(True)
                                try:
                                    grp._lc.setChecked(bool(sl))
                                finally:
                                    grp._lc.blockSignals(False)
                            except Exception:
                                pass
                        if (prefix + '_color') in saved:
                            try:
                                grp._cb.blockSignals(True)
                                try:
                                    grp._cb.set_color(_hex_to_qcolor(saved[prefix + '_color']))
                                finally:
                                    grp._cb.blockSignals(False)
                            except Exception:
                                pass
                    except Exception:
                        pass
            else:
                try:
                    if _should_restore_label(saved.get('label'), saved.get('label_default'), exact_hit):
                        w.label_edit.setText(saved['label'])
                except Exception:
                    pass
                try:
                    if 'color' in saved:
                        w.color_btn.blockSignals(True)
                        try:
                            w.color_btn.set_color(_hex_to_qcolor(saved['color']))
                        finally:
                            w.color_btn.blockSignals(False)
                except Exception:
                    pass
                try:
                    if isinstance(saved.get('size'), (int, float)):
                        w.size_spin.blockSignals(True)
                        try:
                            w.size_spin.setValue(float(saved['size']))
                        finally:
                            w.size_spin.blockSignals(False)
                except Exception:
                    pass
                try:
                    if isinstance(saved.get('linewidth'), (int, float)) and hasattr(w, 'width_spin'):
                        w.width_spin.blockSignals(True)
                        try:
                            w.width_spin.setValue(float(saved['linewidth']))
                        finally:
                            w.width_spin.blockSignals(False)
                except Exception:
                    pass
                try:
                    v = saved.get('linestyle')
                    if isinstance(v, str) and hasattr(w, 'style_combo'):
                        idx = w.style_combo.findText(v)
                        if idx != -1:
                            w.style_combo.blockSignals(True)
                            try:
                                w.style_combo.setCurrentIndex(idx)
                            finally:
                                w.style_combo.blockSignals(False)
                except Exception:
                    pass
                try:
                    if 'marker' in saved and hasattr(w, 'marker_combo'):
                        v = saved.get('marker')
                        if v is None:
                            v = 'None'
                        if isinstance(v, str):
                            idx = w.marker_combo.findText(v)
                            if idx != -1:
                                w.marker_combo.blockSignals(True)
                                try:
                                    w.marker_combo.setCurrentIndex(idx)
                                finally:
                                    w.marker_combo.blockSignals(False)
                except Exception:
                    pass
                try:
                    if isinstance(saved.get('show_std'), bool) and hasattr(w, 'error_check'):
                        w.error_check.blockSignals(True)
                        try:
                            w.error_check.setChecked(bool(saved['show_std']))
                        finally:
                            w.error_check.blockSignals(False)
                except Exception:
                    pass
                try:
                    if isinstance(saved.get('show_std_legend'), bool) and hasattr(w, 'error_legend_check'):
                        w.error_legend_check.blockSignals(True)
                        try:
                            w.error_legend_check.setChecked(bool(saved['show_std_legend']))
                        finally:
                            w.error_legend_check.blockSignals(False)
                except Exception:
                    pass
        finally:
            try:
                w.blockSignals(False)
            except Exception:
                pass

    def _on_save_snapshot_clicked(self):
        """Store current settings via the close-save callback, change nothing else."""
        try:
            cb = getattr(self, '_preset_on_close', None)
            if cb is None:
                return
            try:
                preset = self.collect_all()
            except Exception:
                preset = None
            if preset is not None:
                try:
                    cb(self, preset, "snapshot")
                except Exception as e:
                    logger.warning("Popout preset snapshot save failed: %s", e)
        except Exception:
            pass

    def closeEvent(self, event):
        try:
            cb = getattr(self, '_preset_on_close', None)
            if cb is not None:
                try:
                    preset = self.collect_all()
                except Exception:
                    preset = None
                if preset is not None:
                    try:
                        cb(self, preset)
                    except Exception as e:
                        logger.warning("Popout preset close-save failed: %s", e)
        except Exception:
            pass
        event.accept()

class LatexConfigDialog(QDialog):
    def __init__(self, parent=None, current_tex="", current_gs=""):
        super().__init__(parent)
        self.setWindowTitle("LaTeX Configuration")
        self.resize(500, 200)
        
        layout = QVBoxLayout(self)
        
        info_label = QLabel(
            "LaTeX or Ghostscript binaries were not found in your PATH.\n"
            "Please specify the <b>directories</b> containing the executables."
        )
        layout.addWidget(info_label)
        
        # Form Layout for inputs
        form_layout = QFormLayout()
        
        # LaTeX Path
        self.tex_edit = QLineEdit(current_tex)
        self.tex_btn = QPushButton("Browse")
        self.tex_btn.clicked.connect(lambda: self.browse_folder(self.tex_edit))
        tex_box = QHBoxLayout()
        tex_box.addWidget(self.tex_edit)
        tex_box.addWidget(self.tex_btn)
        form_layout.addRow("TeX/LaTeX Bin Folder:", tex_box)
        
        # Ghostscript Path
        self.gs_edit = QLineEdit(current_gs)
        self.gs_btn = QPushButton("Browse")
        self.gs_btn.clicked.connect(lambda: self.browse_folder(self.gs_edit))
        gs_box = QHBoxLayout()
        gs_box.addWidget(self.gs_edit)
        gs_box.addWidget(self.gs_btn)
        form_layout.addRow("Ghostscript Bin Folder:", gs_box)
        
        layout.addLayout(form_layout)
        
        # Note
        note = QLabel("<i>Note: Point to the folder containing 'latex.exe' or 'gswin64c.exe'.</i>")
        note.setStyleSheet("color: #666;")
        layout.addWidget(note)
        
        # Buttons
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def browse_folder(self, line_edit):
        folder = QFileDialog.getExistingDirectory(self, "Select Binary Directory", line_edit.text())
        if folder:
            line_edit.setText(folder)

    def get_paths(self):
        return self.tex_edit.text().strip(), self.gs_edit.text().strip()