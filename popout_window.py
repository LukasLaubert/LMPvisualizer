# lmp_visualizer/popout_window.py

import sys
import copy
import shutil
import numpy as np
from PyQt6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
                             QDockWidget, QScrollArea, QFormLayout, QLabel, 
                             QLineEdit, QCheckBox, QComboBox, QSpinBox, 
                             QDoubleSpinBox, QGroupBox, QPushButton, QColorDialog, 
                             QFrame, QSizePolicy, QMessageBox, QToolBar)
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
            
            self.style_combo = QComboBox() 
            self.width_spin = QDoubleSpinBox() 
            self.error_check = QCheckBox() 
            
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
            self.marker_combo.addItems(['None', 'o', 's', '^', 'v', 'D', 'x', '+'])
            current_marker = initial_props.get('marker', 'None')
            if current_marker is None: current_marker = 'None'
            self.marker_combo.setCurrentText(current_marker)
            self.marker_combo.currentTextChanged.connect(self.propertiesChanged)
            self.marker_combo.setMaximumWidth(100)
            layout.addRow("Marker:", self.marker_combo)
            
            self.has_std = initial_props.get('has_std', False)
            self.error_check = QCheckBox("Show Error Band")
            if self.has_std:
                self.error_check.setChecked(initial_props.get('show_std', True))
                self.error_check.toggled.connect(self.propertiesChanged)
                layout.addRow(self.error_check)
            
            self.size_spin = QDoubleSpinBox()

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
            props['show_std'] = False
        else:
            m = self.marker_combo.currentText()
            if m == 'None': m = None
            props['marker'] = m
            props['linestyle'] = self.style_combo.currentText()
            props['linewidth'] = self.width_spin.value()
            props['show_std'] = self.error_check.isChecked() if self.has_std else False
            
        return props

class PopOutWindow(QMainWindow):
    def __init__(self, plot_state_data, parent=None, figsize=None):
        super().__init__(parent)
        self.setWindowTitle("Plot Inspector")
        self.resize(1100, 650) # Slightly wider default
        
        self.plot_data = copy.deepcopy(plot_state_data)
        self.line_widgets = {} 
        self.initial_figsize = figsize # Tuple (width_in, height_in)
        
        self._init_ui()
        
        self.canvas.mpl_connect('resize_event', self.on_canvas_resize)
        
        # If initial size provided, apply it immediately
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

        self.latex_check = QCheckBox("Use LaTeX")
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
        self.font_label_spin.setValue(10)
        self.font_label_spin.valueChanged.connect(self.redraw_plot)
        self.font_label_spin.setFixedWidth(70)
        layout.addRow("Axis Labels:", self.font_label_spin)

        self.font_tick_spin = QSpinBox()
        self.font_tick_spin.setRange(6, 72)
        self.font_tick_spin.setValue(10)
        self.font_tick_spin.valueChanged.connect(self.redraw_plot)
        self.font_tick_spin.setFixedWidth(70)
        layout.addRow("Tick Labels:", self.font_tick_spin)

        self.font_legend_spin = QSpinBox()
        self.font_legend_spin.setRange(6, 72)
        self.font_legend_spin.setValue(10)
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

    def on_latex_toggled(self, checked):
        if checked:
            reqs = ['latex', 'dvipng', 'gs']
            missing = [tool for tool in reqs if shutil.which(tool) is None]
            if missing:
                QMessageBox.warning(self, "LaTeX Requirements Missing", 
                    f"Missing: {', '.join(missing)}\nInstall TeX Live/MiKTeX and Ghostscript.")
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
        
        # Get separate font sizes
        font_title = self.font_title_spin.value()
        font_label = self.font_label_spin.value()
        font_tick = self.font_tick_spin.value()
        font_legend = self.font_legend_spin.value()
        
        ax_primary = self.figure.add_subplot(111)
        ax_primary.set_title(self.title_edit.text(), fontsize=font_title)
        ax_primary.set_xlabel(self.x_label_edit.text(), fontsize=font_label)
        
        ax_primary.tick_params(axis='both', labelsize=font_tick, direction='in')
        
        if 'x_limits' in self.plot_data:
            ax_primary.set_xlim(self.plot_data['x_limits'])
        
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
        if 'y_limits' in self.plot_data['y_axes'][y_cols[0]]:
            ax_primary.set_ylim(self.plot_data['y_axes'][y_cols[0]]['y_limits'])
            
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
            if 'y_limits' in self.plot_data['y_axes'][y_col]:
                ax_new.set_ylim(self.plot_data['y_axes'][y_col]['y_limits'])
            
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

                if series.get('mode') == 'scatter':
                    colors_to_use = None
                    if series.get('colors') is not None:
                        colors_to_use = []
                        for c in series['colors']:
                            colors_to_use.append((c.redF(), c.greenF(), c.blueF(), c.alphaF()))
                    else:
                        c = props['color']
                        colors_to_use = (c.redF(), c.greenF(), c.blueF(), c.alphaF())

                    scatter_h = ax.scatter(
                        series['x'], series['y'],
                        label=props['label'],
                        c=colors_to_use, 
                        s=props['size']**2,
                        marker=props['marker'],
                        edgecolors='none',
                        zorder=z_val
                    )
                    
                    if props['label']: # Only add to legend if label is not empty
                        all_handles.append(scatter_h)
                        all_labels.append(props['label'])

                else:
                    c = props['color']
                    color_tuple = (c.redF(), c.greenF(), c.blueF(), c.alphaF())
                    
                    # Check if this series has std data and should show error band
                    has_error_band = props['show_std'] and series['std'] is not None
                    
                    if has_error_band:
                        # ONLY plot the error band. This prevents the "extra line" inside the band for Std plots.				   
                        try:
                            lower = series['y'] - series['std']
                            upper = series['y'] + series['std']
                            fill = ax.fill_between(series['x'], lower, upper, color=color_tuple, 
                                                 alpha=0.25, linewidth=0, label=props['label'], zorder=z_val)
                            
                            if props['label']:
                                all_handles.append(fill)
                                all_labels.append(props['label'])
                        except Exception:
                            pass
                    else:
                        # Plot normal line (Orig or Mean)
                        line, = ax.plot(
                            series['x'], series['y'], 
                            label=props['label'],
                            color=color_tuple,
                            linestyle=props['linestyle'],
                            linewidth=props['linewidth'],
                            marker=props['marker'],
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