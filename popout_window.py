# lmp_visualizer/popout_window.py

import os
import sys
import copy
import json
import shutil
import numpy as np
from PyQt6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
                             QDockWidget, QScrollArea, QFormLayout, QLabel, 
                             QLineEdit, QCheckBox, QComboBox, QSpinBox, 
                             QDoubleSpinBox, QGroupBox, QPushButton, QColorDialog, 
                             QFrame, QSizePolicy, QMessageBox, QToolBar,
                             QDialog, QDialogButtonBox, QFileDialog)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics

import matplotlib
matplotlib.use('qtagg')
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
import matplotlib.pyplot as plt

from ui_components import ColorButton

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
        
        self.series_id = series_id
        self.is_scatter = initial_props.get('mode') == 'scatter'
        self.has_std = initial_props.get('has_std', False)
        
        self.setCheckable(True)
        self.setChecked(initial_props.get('visible', True))
        self.toggled.connect(self.propertiesChanged)

        layout = QFormLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)

        # Label
        self.label_edit = QLineEdit(initial_props['name'])
        self.label_edit.editingFinished.connect(self.propertiesChanged)
        self.label_edit.setMaximumWidth(200)
        layout.addRow("Legend:", self.label_edit)

        # Color
        is_heatmap = self.is_scatter and 'colors' in initial_props and initial_props['colors'] is not None
        
        if not is_heatmap:
            self.color_btn = ColorButton(initial_props['color'])
            self.color_btn.colorChanged.connect(lambda: self.propertiesChanged.emit())
            layout.addRow("Color:", self.color_btn)
        else:
            self.color_btn = ColorButton(initial_props['color'])

        if self.is_scatter:
            self.size_spin = QDoubleSpinBox()
            self.size_spin.setRange(1.0, 200.0)
            self.size_spin.setValue(float(initial_props.get('size', 10)))
            self.size_spin.valueChanged.connect(self.propertiesChanged)
            self.size_spin.setFixedWidth(100)
            layout.addRow("Size:", self.size_spin)
            
            self.marker_combo = QComboBox()
            self.marker_combo.addItems(['o', 'x', '+', 'v', '^', '<', '>', 's', 'p', '*', 'h', 'H', 'D', 'd'])
            self.marker_combo.setCurrentText(initial_props.get('marker', 'o'))
            self.marker_combo.currentTextChanged.connect(self.propertiesChanged)
            self.marker_combo.setMaximumWidth(100)
            layout.addRow("Marker:", self.marker_combo)
            
            # Placeholders for scatter
            self.style_combo = QComboBox()
            self.width_spin = QDoubleSpinBox()
        else:
            self.style_combo = QComboBox()
            self.style_combo.addItems(['-', '--', ':', '-.'])
            self.style_combo.setCurrentText(initial_props.get('linestyle', '-'))
            self.style_combo.currentTextChanged.connect(self.propertiesChanged)
            self.style_combo.setMaximumWidth(100)
            layout.addRow("Style:", self.style_combo)

            self.width_spin = QDoubleSpinBox()
            self.width_spin.setRange(0.1, 20.0)
            self.width_spin.setSingleStep(0.5)
            self.width_spin.setValue(initial_props.get('linewidth', 1.5))
            self.width_spin.valueChanged.connect(self.propertiesChanged)
            self.width_spin.setFixedWidth(100)
            layout.addRow("Width:", self.width_spin)

            self.marker_combo = QComboBox()
            self.marker_combo.addItems(['None', 'o', 'x', '+', 'v', '^', '<', '>', 's', 'p', '*', 'h', 'H', 'D', 'd'])
            current_marker = initial_props.get('marker', 'None')
            if current_marker is None: current_marker = 'None'
            self.marker_combo.setCurrentText(current_marker)
            self.marker_combo.currentTextChanged.connect(self.propertiesChanged)
            self.marker_combo.setMaximumWidth(100)
            layout.addRow("Marker:", self.marker_combo)
            
            # Placeholder for line
            self.size_spin = QDoubleSpinBox()

        # Shared Error Band Control (Available for both line and scatter if data exists)
        self.error_check = QCheckBox("Show Error Band")
        if self.has_std:
            self.error_check.setChecked(initial_props.get('show_std', True))
            self.error_check.toggled.connect(self.propertiesChanged)
            layout.addRow(self.error_check)

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
            props['size'] = 6 # Default for line markers
            
        props['show_std'] = self.error_check.isChecked() if self.has_std else False
            
        return props

class PopOutWindow(QMainWindow):
    # Static class variable to store LaTeX state across different popout instances
    # within the same application session. Resets to False when app restarts.
    _session_latex_enabled = False

    def __init__(self, plot_state_data, parent=None, figsize=None):
        super().__init__(parent)
        self.setWindowTitle("Plot Inspector")
        self.resize(1100, 650) 
        
        self.plot_data = copy.deepcopy(plot_state_data)
        self.line_widgets = {} 
        self.initial_figsize = figsize 
        
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
        self.scroll.setWidget(self.scroll_content)
        self.dock_layout.addWidget(self.scroll)
        self.dock.setWidget(self.dock_widget)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock)

        # Apply session state LAST, after all widgets (like font_title_spin) exist.
        # This triggers on_latex_toggled -> redraw_plot safely.
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
        self.title_edit.setMaximumWidth(160)
        layout.addRow("Title:", self.title_edit)

        self.latex_check = QCheckBox("Use LaTeX (slow...)")
        self.latex_check.toggled.connect(self.on_latex_toggled)
        layout.addRow(self.latex_check)

        self.grid_check = QCheckBox("Show Grid")
        self.grid_check.setChecked(True)
        self.grid_check.toggled.connect(self.redraw_plot)
        layout.addRow(self.grid_check)

        self.form_layout.addWidget(group)

    def _init_font_settings(self):
        group = QGroupBox("Font Sizes")
        layout = QFormLayout(group)

        self.font_title_spin = QSpinBox()
        self.font_title_spin.setRange(6, 72)
        self.font_title_spin.setValue(12)
        self.font_title_spin.valueChanged.connect(self.redraw_plot)
        self.font_title_spin.setFixedWidth(70)
        layout.addRow("Title:", self.font_title_spin)

        self.font_label_spin = QSpinBox()
        self.font_label_spin.setRange(6, 72)
        self.font_label_spin.setValue(11)
        self.font_label_spin.valueChanged.connect(self.redraw_plot)
        self.font_label_spin.setFixedWidth(70)
        layout.addRow("Axis Labels:", self.font_label_spin)

        self.font_tick_spin = QSpinBox()
        self.font_tick_spin.setRange(6, 72)
        self.font_tick_spin.setValue(12)
        self.font_tick_spin.valueChanged.connect(self.redraw_plot)
        self.font_tick_spin.setFixedWidth(70)
        layout.addRow("Tick Labels:", self.font_tick_spin)

        self.font_legend_spin = QSpinBox()
        self.font_legend_spin.setRange(6, 72)
        self.font_legend_spin.setValue(12)
        self.font_legend_spin.valueChanged.connect(self.redraw_plot)
        self.font_legend_spin.setFixedWidth(70)
        layout.addRow("Legend:", self.font_legend_spin)

        self.form_layout.addWidget(group)

    def _init_axis_settings(self):
        group = QGroupBox("Axes")
        layout = QFormLayout(group)

        # Use the custom global label passed from controller if available
        default_x = self.plot_data.get('x_label', '')
        self.x_label_edit = QLineEdit(default_x)
        self.x_label_edit.editingFinished.connect(self.redraw_plot)
        self.x_label_edit.setMaximumWidth(200)
        layout.addRow("X Label:", self.x_label_edit)
        
        self.x_log_check = QCheckBox("Log Scale X")
        self.x_log_check.toggled.connect(self.redraw_plot)
        layout.addRow(self.x_log_check)

        self.y_configs = {}
        for y_col, axis_data in self.plot_data['y_axes'].items():
            lbl = QLabel(f"<b>Y-Axis: {y_col}</b>")
            layout.addRow(lbl)
            
            # Use the custom global label passed from controller if available
            default_y = axis_data.get('label', y_col)
            label_edit = QLineEdit(default_y)
            label_edit.editingFinished.connect(self.redraw_plot)
            label_edit.setMaximumWidth(200)
            layout.addRow("Label:", label_edit)
            
            log_check = QCheckBox("Log Scale Y")
            log_check.toggled.connect(self.redraw_plot)
            layout.addRow(log_check)
            
            self.y_configs[y_col] = {'label_edit': label_edit, 'log_check': log_check}

        self.form_layout.addWidget(group)

    def _init_legend_settings(self):
        group = QGroupBox("Legend")
        layout = QFormLayout(group)
        
        self.show_legend_check = QCheckBox("Show Legend")
        self.show_legend_check.setChecked(True)
        self.show_legend_check.toggled.connect(self.redraw_plot)
        layout.addRow(self.show_legend_check)

        self.legend_loc = QComboBox()
        self.legend_loc.addItems(['best', 'upper right', 'upper left', 'lower left', 'lower right', 'center left', 'center right', 'upper center', 'lower center'])
        self.legend_loc.currentTextChanged.connect(self.redraw_plot)
        self.legend_loc.setMaximumWidth(140)
        layout.addRow("Location:", self.legend_loc)

        self.legend_frame = QCheckBox("Frame")
        self.legend_frame.setChecked(True)
        self.legend_frame.toggled.connect(self.redraw_plot)
        layout.addRow(self.legend_frame)
        
        self.legend_draggable = QCheckBox("Draggable")
        self.legend_draggable.setChecked(True)
        self.legend_draggable.toggled.connect(self.redraw_plot)
        layout.addRow(self.legend_draggable)

        self.form_layout.addWidget(group)

    def _populate_line_widgets(self):
        for i in reversed(range(self.lines_layout.count())): 
            self.lines_layout.itemAt(i).widget().setParent(None)
        self.line_widgets.clear()

        for y_col, axis_data in self.plot_data['y_axes'].items():
            for series in axis_data['series']:
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
                    'mode': series.get('mode', 'line'),
                    'size': series.get('size', 20) if is_scatter else 10,
                    'colors': series.get('colors')
                }
                
                widget = LinePropertiesWidget(sid, initial_props)
                widget.propertiesChanged.connect(self.redraw_plot)
                self.lines_layout.addWidget(widget)
                self.line_widgets[sid] = widget

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
                with open(path, 'r') as f:
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
                with open(config_path, 'r') as f:
                    data = json.load(f)
            except:
                pass
        
        data['tex_path'] = tex_path
        data['gs_path'] = gs_path
        
        with open(config_path, 'w') as f:
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
        if not limits or len(limits) != 2:
            return limits
        return [limits[1], limits[0]] if inverted else limits

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
        
        # Apply X Limits: Saved > Initial Data
        if saved_xlim:
            ax_primary.set_xlim(saved_xlim)
        elif 'x_limits' in self.plot_data:
            ax_primary.set_xlim(self._oriented_limits(self.plot_data['x_limits'], self.plot_data.get('x_inverted', False)))
        
        if self.x_log_check.isChecked():
            ax_primary.set_xscale('log')
        
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
        if config_prim['log_check'].isChecked():
            ax_primary.set_yscale('log')
            
        # Apply Y Limits: Saved > Initial Data
        if saved_ylim_prim:
            ax_primary.set_ylim(saved_ylim_prim)
        elif 'y_limits' in self.plot_data['y_axes'][y_cols[0]]:
            y_axis_data = self.plot_data['y_axes'][y_cols[0]]
            ax_primary.set_ylim(self._oriented_limits(y_axis_data['y_limits'], y_axis_data.get('y_inverted', False)))
            
        # Apply Axis Colors (Primary)
        y_data_prim = self.plot_data['y_axes'][y_cols[0]]
        if 'color' in y_data_prim:
            col = y_data_prim['color']
            rgb = (col.redF(), col.greenF(), col.blueF())
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
            
            if config['log_check'].isChecked():
                ax_new.set_yscale('log')
                
            # Apply Y Limits (Secondary)
            # We need to have saved them. Since we didn't implement complex mapping above, 
            # let's fallback to initial limits for secondaries OR try to map by index if feasible.
            # Better strategy: We can't easily map back without robust ID tracking.
            # But we can try: 
            if 'y_limits' in self.plot_data['y_axes'][y_col]:
                 y_axis_data = self.plot_data['y_axes'][y_col]
                 ax_new.set_ylim(self._oriented_limits(y_axis_data['y_limits'], y_axis_data.get('y_inverted', False)))
            
            # Apply Axis Colors (Secondary)
            y_data_sec = self.plot_data['y_axes'][y_col]
            if 'color' in y_data_sec:
                col = y_data_sec['color']
                rgb = (col.redF(), col.greenF(), col.blueF())
                ax_new.yaxis.label.set_color(rgb)
                ax_new.tick_params(axis='y', colors=rgb, direction='in')
                ax_new.spines['right'].set_color(rgb)
            
            axes_map[y_col] = ax_new

        all_handles = []
        all_labels = []
        
        # Iterate axes
        for y_col, axis_data in self.plot_data['y_axes'].items():
            ax = axes_map[y_col]
            
            # Explicitly sort by layer_priority to match the desired Z-order logic
            sorted_series = sorted(axis_data['series'], key=lambda s: s.get('layer_priority', 0))
            
            for series in sorted_series:
                sid = series['id']
                if sid not in self.line_widgets: continue
                
                props = self.line_widgets[sid].get_properties()
                if not props['visible']: continue

                # Calculate explicit z-order: Orig(~2) < Std(~3) < Mean(~4)	 
                z_val = 2.0 + series.get('layer_priority', 0)

                all_x = series['x']
                all_y = series['y']
                all_std = series.get('std')
                
                c = props['color']
                color_tuple = (c.redF(), c.greenF(), c.blueF(), c.alphaF())
                
                # Plot the error band if present and enabled
                if props.get('show_std', True) and all_std is not None:
                    try:
                        lower = all_y - all_std
                        upper = all_y + all_std
                        fill_label = f"{props['label']} (Std)" if props['label'] else None
                        fill = ax.fill_between(all_x, lower, upper, color=color_tuple, 
                                             alpha=0.25, linewidth=0, label=fill_label, zorder=z_val - 0.1)
                        if fill_label:
                            all_handles.append(fill)
                            all_labels.append(fill_label)
                    except Exception:
                        pass

                # Plot the data (scatter or line)
                if series.get('mode') == 'scatter':
                    # Explicit Scatter rendering (colors, discrete points)
                    if series.get('colors') is not None:
                        # Use 'c' for array of colors
                        colors_to_use = []
                        for c_item in series['colors']:
                            colors_to_use.append((c_item.redF(), c_item.greenF(), c_item.blueF(), c_item.alphaF()))
                        
                        scatter_h = ax.scatter(
                            all_x, all_y,
                            label=props['label'],
                            c=colors_to_use,
                            s=props['size']**2,
                            marker=props['marker'],
                            edgecolors='none',
                            zorder=z_val
                        )
                    else:
                        # Use 'color' for single color to avoid ambiguity with value mapping
                        scatter_h = ax.scatter(
                            all_x, all_y,
                            label=props['label'],
                            color=color_tuple,
                            s=props['size']**2,
                            marker=props['marker'],
                            edgecolors='none',
                            zorder=z_val
                        )
                    
                    if props['label']:
                        all_handles.append(scatter_h)
                        all_labels.append(props['label'])
                else:
                    # Standard Line rendering (with optional markers)
                    lstyle = props.get('linestyle', '-')
                    if lstyle == 'None':
                        lstyle = 'None'

                    line, = ax.plot(
                        all_x, all_y, 
                        label=props['label'],
                        color=color_tuple,
                        linestyle=lstyle,
                        linewidth=props.get('linewidth', 1.5),
                        marker=props.get('marker', 'None'),
                        markersize=props.get('size', 6),
                        zorder=z_val
                    )
                    
                    if props['label']:
                        all_handles.append(line)
                        all_labels.append(props['label'])

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