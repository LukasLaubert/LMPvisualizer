import sys
import copy
import shutil
import numpy as np
from PyQt6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
                             QDockWidget, QScrollArea, QFormLayout, QLabel, 
                             QLineEdit, QCheckBox, QComboBox, QSpinBox, 
                             QDoubleSpinBox, QGroupBox, QPushButton, QColorDialog, 
                             QFrame, QSizePolicy, QMessageBox, QToolBar)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QColor, QAction, QIcon

import matplotlib
# Ensure we use the Qt6 backend
matplotlib.use('qtagg')
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
import matplotlib.pyplot as plt

from ui_components import ColorButton

class LinePropertiesWidget(QGroupBox):
    """Widget to control properties of a single line series."""
    propertiesChanged = pyqtSignal()

    def __init__(self, series_id, initial_props, parent=None):
        super().__init__(initial_props['name'], parent)
        self.series_id = series_id
        self.setCheckable(True)
        self.setChecked(initial_props.get('visible', True))
        self.toggled.connect(self.propertiesChanged)

        layout = QFormLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)

        # Label
        self.label_edit = QLineEdit(initial_props['name'])
        self.label_edit.editingFinished.connect(self.propertiesChanged)
        layout.addRow("Legend:", self.label_edit)

        # Color
        self.color_btn = ColorButton(initial_props['color'])
        self.color_btn.colorChanged.connect(lambda: self.propertiesChanged.emit())
        layout.addRow("Color:", self.color_btn)

        # Style
        self.style_combo = QComboBox()
        self.style_combo.addItems(['-', '--', ':', '-.'])
        self.style_combo.setCurrentText(initial_props.get('linestyle', '-'))
        self.style_combo.currentTextChanged.connect(self.propertiesChanged)
        layout.addRow("Style:", self.style_combo)

        # Width
        self.width_spin = QDoubleSpinBox()
        self.width_spin.setRange(0.1, 20.0)
        self.width_spin.setSingleStep(0.5)
        self.width_spin.setValue(initial_props.get('linewidth', 1.5))
        self.width_spin.valueChanged.connect(self.propertiesChanged)
        layout.addRow("Width:", self.width_spin)

        # Marker
        self.marker_combo = QComboBox()
        self.marker_combo.addItems(['None', 'o', 's', '^', 'v', 'D', 'x', '+'])
        current_marker = initial_props.get('marker', 'None')
        if current_marker is None: current_marker = 'None'
        self.marker_combo.setCurrentText(current_marker)
        self.marker_combo.currentTextChanged.connect(self.propertiesChanged)
        layout.addRow("Marker:", self.marker_combo)
        
        # Error Band Toggle (if data available)
        self.has_std = initial_props.get('has_std', False)
        self.error_check = QCheckBox("Show Error Band")
        if self.has_std:
            self.error_check.setChecked(initial_props.get('show_std', True))
            self.error_check.toggled.connect(self.propertiesChanged)
            layout.addRow(self.error_check)

    def get_properties(self):
        marker = self.marker_combo.currentText()
        if marker == 'None': marker = None
        
        return {
            'visible': self.isChecked(),
            'label': self.label_edit.text(),
            'color': self.color_btn.color(),
            'linestyle': self.style_combo.currentText(),
            'linewidth': self.width_spin.value(),
            'marker': marker,
            'show_std': self.error_check.isChecked() if self.has_std else False
        }

class PopOutWindow(QMainWindow):
    def __init__(self, plot_state_data, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Plot Inspector")
        
        # Initial Window Size (includes dock and toolbar)
        # We start a bit larger to ensure controls are visible, user can click "Set Size" to enforce 400x300 canvas
        self.resize(900, 600)
        
        # Data storage
        self.plot_data = copy.deepcopy(plot_state_data)
        self.line_widgets = {} 
        
        self._init_ui()
        
        # Hook up resize event for refreshing layout
        self.canvas.mpl_connect('resize_event', self.on_canvas_resize)
        
        self.redraw_plot()

    def _init_ui(self):
        # --- Central Widget: Matplotlib Canvas ---
        # Default dpi is usually 100
        self.figure = Figure(dpi=100) 
        self.canvas = FigureCanvasQTAgg(self.figure)
        
        # Set Policy to Expanding to fill available space
        self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.canvas.updateGeometry()
        
        self.setCentralWidget(self.canvas)

        # --- Toolbar ---
        self.toolbar = NavigationToolbar2QT(self.canvas, self)
        self.addToolBar(self.toolbar)

        # --- Dock Widget: Inspector ---
        self.dock = QDockWidget("Plot Settings", self)
        self.dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self.dock_widget = QWidget()
        self.dock_layout = QVBoxLayout(self.dock_widget)
        self.dock_layout.setContentsMargins(0, 0, 0, 0)
        
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll_content = QWidget()
        self.form_layout = QVBoxLayout(self.scroll_content)
        
        # 1. Global Settings
        self._init_global_settings()
        
        # 2. Axis Settings
        self._init_axis_settings()

        # 3. Legend Settings
        self._init_legend_settings()

        # 4. Line List
        self.lines_group = QGroupBox("Series Properties")
        self.lines_layout = QVBoxLayout(self.lines_group)
        self.form_layout.addWidget(self.lines_group)
        
        # Populate lines
        self._populate_line_widgets()

        self.form_layout.addStretch()
        self.scroll.setWidget(self.scroll_content)
        self.dock_layout.addWidget(self.scroll)
        
        self.dock.setWidget(self.dock_widget)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock)

    def _init_global_settings(self):
        group = QGroupBox("Global Settings")
        layout = QFormLayout(group)

        # --- Canvas Size Control ---
        size_layout = QHBoxLayout()
        size_layout.setContentsMargins(0, 0, 0, 0)
        size_layout.setSpacing(2) # Very tight spacing
        
        self.width_spin = QDoubleSpinBox()
        self.width_spin.setPrefix("W: ") # Put label inside to save space
        self.width_spin.setRange(10, 10000)
        self.width_spin.setDecimals(1)
        self.width_spin.setToolTip("Canvas Width")
        self.width_spin.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.UpDownArrows)
        # Removed custom stylesheet to allow native OS vertical buttons to render correctly
        
        self.height_spin = QDoubleSpinBox()
        self.height_spin.setPrefix("H: ") # Put label inside
        self.height_spin.setRange(10, 10000)
        self.height_spin.setDecimals(1)
        self.height_spin.setToolTip("Canvas Height")
        self.height_spin.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.UpDownArrows)
        
        self.unit_combo = QComboBox()
        self.unit_combo.addItems(['px', 'in', 'mm', 'cm'])
        self.unit_combo.setCurrentText('px')
        self.unit_combo.currentTextChanged.connect(self.update_size_display)
        self.unit_combo.setFixedWidth(45) # Force narrow width
        
        set_size_btn = QPushButton("Set") # Shortened text
        set_size_btn.setFixedWidth(40)    # Force narrow width
        set_size_btn.clicked.connect(self.apply_canvas_size)
        
        size_layout.addWidget(self.width_spin)
        size_layout.addWidget(self.height_spin)
        size_layout.addWidget(self.unit_combo)
        size_layout.addWidget(set_size_btn)
        
        layout.addRow("Size:", size_layout)

        # --- Title ---
        self.title_edit = QLineEdit(self.plot_data.get('title', ''))
        self.title_edit.setPlaceholderText("Plot Title")
        self.title_edit.editingFinished.connect(self.redraw_plot)
        layout.addRow("Title:", self.title_edit)

        # --- LaTeX ---
        self.latex_check = QCheckBox("Use LaTeX")
        self.latex_check.setToolTip("Requires 'latex', 'dvipng', and 'ghostscript' in system PATH.")
        self.latex_check.toggled.connect(self.on_latex_toggled)
        layout.addRow(self.latex_check)

        # --- Font Size ---
        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(6, 48)
        self.font_size_spin.setValue(10)
        self.font_size_spin.valueChanged.connect(self.redraw_plot)
        layout.addRow("Font Size:", self.font_size_spin)

        # --- Grid ---
        self.grid_check = QCheckBox("Show Grid")
        self.grid_check.setChecked(True)
        self.grid_check.toggled.connect(self.redraw_plot)
        layout.addRow(self.grid_check)

        self.form_layout.addWidget(group)

    def _init_axis_settings(self):
        group = QGroupBox("Axes")
        layout = QFormLayout(group)

        self.x_label_edit = QLineEdit(self.plot_data.get('x_label', ''))
        self.x_label_edit.editingFinished.connect(self.redraw_plot)
        layout.addRow("X Label:", self.x_label_edit)
        
        self.x_log_check = QCheckBox("Log Scale X")
        self.x_log_check.toggled.connect(self.redraw_plot)
        layout.addRow(self.x_log_check)

        # Y Axes Configs
        self.y_configs = {}
        for y_col, axis_data in self.plot_data['y_axes'].items():
            lbl = QLabel(f"<b>Y-Axis: {y_col}</b>")
            layout.addRow(lbl)
            
            label_edit = QLineEdit(axis_data.get('label', y_col))
            label_edit.editingFinished.connect(self.redraw_plot)
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
        layout.addRow("Location:", self.legend_loc)

        self.legend_frame = QCheckBox("Frame")
        self.legend_frame.setChecked(True)
        self.legend_frame.toggled.connect(self.redraw_plot)
        layout.addRow(self.legend_frame)
        
        self.legend_draggable = QCheckBox("Draggable (Disable Zoom First!)")
        self.legend_draggable.setChecked(True)
        self.legend_draggable.toggled.connect(self.redraw_plot)
        layout.addRow(self.legend_draggable)

        self.form_layout.addWidget(group)

    def _populate_line_widgets(self):
        # Clear existing
        for i in reversed(range(self.lines_layout.count())): 
            self.lines_layout.itemAt(i).widget().setParent(None)
        self.line_widgets.clear()

        for y_col, axis_data in self.plot_data['y_axes'].items():
            for series in axis_data['series']:
                sid = series['id']
                
                initial_props = {
                    'name': series['name'],
                    'visible': True,
                    'color': series['color'],
                    'linestyle': series['linestyle_matlab'],
                    'linewidth': series['width'],
                    'marker': 'None',
                    'has_std': series['std'] is not None,
                    'show_std': True
                }
                
                widget = LinePropertiesWidget(sid, initial_props)
                widget.propertiesChanged.connect(self.redraw_plot)
                self.lines_layout.addWidget(widget)
                self.line_widgets[sid] = widget

    def apply_canvas_size(self):
        """Resizes the main window so the canvas area matches the requested dimensions in the selected unit."""
        target_w = self.width_spin.value()
        target_h = self.height_spin.value()
        unit = self.unit_combo.currentText()
        dpi = self.figure.get_dpi()
        
        # Convert everything to pixels for the window resize
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
            
        # --- Exact Sizing Logic ---
        # Instead of guessing margins, calculate the current 'overhead' 
        # (Toolbar + Dock + OS Borders) by comparing Window size vs Canvas size.
        current_win_w = self.width()
        current_win_h = self.height()
        current_canvas_w = self.canvas.width()
        current_canvas_h = self.canvas.height()
        
        overhead_w = current_win_w - current_canvas_w
        overhead_h = current_win_h - current_canvas_h
        
        # Calculate required total window size
        new_total_w = target_w_px + overhead_w
        new_total_h = target_h_px + overhead_h
        
        self.resize(new_total_w, new_total_h)
        
        # Force Matplotlib update
        w_in = target_w_px / dpi
        h_in = target_h_px / dpi
        self.figure.set_size_inches(w_in, h_in)
        self.figure.tight_layout()
        self.canvas.draw()

    def on_canvas_resize(self, event):
        """Ensures plot is re-laid out and UI numbers are updated when window is resized."""
        if self.figure:
            self.figure.tight_layout()
            self.canvas.draw_idle()
            # Update the text boxes to show new dimensions
            self.update_size_display()

    def on_latex_toggled(self, checked):
        if checked:
            # Pre-check for requirements to prevent freeze
            reqs = ['latex', 'dvipng', 'gs'] # gs is ghostscript
            missing = [tool for tool in reqs if shutil.which(tool) is None]
            
            if missing:
                QMessageBox.warning(self, "LaTeX Requirements Missing", 
                    f"Cannot enable LaTeX mode.\n\nMissing executables: {', '.join(missing)}\n\n"
                    "To fix this:\n"
                    "1. Install MiKTeX (Win) or TeX Live (Linux/Mac).\n"
                    "2. Install Ghostscript.\n"
                    "3. Add their bin/ folders to your System PATH.\n"
                    "4. Restart this application.")
                
                # Reset checkbox safely
                self.latex_check.blockSignals(True)
                self.latex_check.setChecked(False)
                self.latex_check.blockSignals(False)
                return

        try:
            plt.rcParams['text.usetex'] = checked
            self.redraw_plot()
        except Exception as e:
            QMessageBox.warning(self, "LaTeX Error", f"Error enabling LaTeX:\n{e}")
            self.latex_check.blockSignals(True)
            self.latex_check.setChecked(False)
            self.latex_check.blockSignals(False)
            plt.rcParams['text.usetex'] = False
            self.redraw_plot()

    def redraw_plot(self):
        self.figure.clear()
        
        # Global Font Settings
        font_size = self.font_size_spin.value()
        plt.rcParams.update({'font.size': font_size})
        
        # Setup Axes
        ax_primary = self.figure.add_subplot(111)
        ax_primary.set_title(self.title_edit.text())
        ax_primary.set_xlabel(self.x_label_edit.text())
        
        if self.x_log_check.isChecked():
            ax_primary.set_xscale('log')
        
        if self.grid_check.isChecked():
            ax_primary.grid(True, which='both', linestyle='--', linewidth=0.5, alpha=0.7)

        y_cols = list(self.plot_data['y_axes'].keys())
        if not y_cols:
            self.canvas.draw()
            return

        axes_map = {y_cols[0]: ax_primary}
        
        # Configure Primary Axis
        config_prim = self.y_configs[y_cols[0]]
        ax_primary.set_ylabel(config_prim['label_edit'].text())
        if config_prim['log_check'].isChecked():
            ax_primary.set_yscale('log')
        
        # Configure Secondary Axes
        for i, y_col in enumerate(y_cols[1:], start=1):
            ax_new = ax_primary.twinx()
            if i > 1:
                ax_new.spines['right'].set_position(('outward', 60 * (i - 1)))
            
            config = self.y_configs[y_col]
            ax_new.set_ylabel(config['label_edit'].text())
            if config['log_check'].isChecked():
                ax_new.set_yscale('log')
            axes_map[y_col] = ax_new

        all_handles = []
        all_labels = []
        
        for y_col, axis_data in self.plot_data['y_axes'].items():
            ax = axes_map[y_col]
            for series in axis_data['series']:
                sid = series['id']
                if sid not in self.line_widgets: continue
                
                props = self.line_widgets[sid].get_properties()
                if not props['visible']: continue

                c = props['color']
                color_tuple = (c.redF(), c.greenF(), c.blueF(), c.alphaF())

                line, = ax.plot(series['x'], series['y'], 
                                label=props['label'],
                                color=color_tuple,
                                linestyle=props['linestyle'],
                                linewidth=props['linewidth'],
                                marker=props['marker'])
                
                all_handles.append(line)
                all_labels.append(props['label'])

                if props['show_std'] and series['std'] is not None:
                    try:
                        lower = series['y'] - series['std']
                        upper = series['y'] + series['std']
                        ax.fill_between(series['x'], lower, upper, color=color_tuple, alpha=0.25, linewidth=0)
                    except Exception as e:
                        print(f"Error plotting std dev: {e}")

        # Legend
        if self.show_legend_check.isChecked() and all_handles:
            loc = self.legend_loc.currentText()
            frame = self.legend_frame.isChecked()
            draggable = self.legend_draggable.isChecked()
            
            leg = ax_primary.legend(all_handles, all_labels, loc=loc, frameon=frame)
            
            # CRITICAL: Exclude legend from layout calculations to prevent plot resizing
            leg.set_in_layout(False)
            
            if draggable:
                leg.set_draggable(True)

        # Force tight layout to calculate axes based on labels/titles, IGNORING the legend
        self.figure.tight_layout()
        self.canvas.draw()

    def update_size_display(self):
        """Updates the spinboxes to reflect the current actual canvas size in the selected unit."""
        if not self.figure: return
        
        # Get current size in pixels directly from the canvas widget
        # We use the widget size because figure.bbox might lag slightly during resize events
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

        # Block signals to prevent feedback loops or unintended triggers
        self.width_spin.blockSignals(True)
        self.height_spin.blockSignals(True)
        self.width_spin.setValue(w_val)
        self.height_spin.setValue(h_val)
        self.width_spin.blockSignals(False)
        self.height_spin.blockSignals(False)