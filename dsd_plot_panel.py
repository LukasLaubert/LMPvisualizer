import os
import random
from pathlib import Path
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGridLayout, 
    QComboBox, QPushButton, QHeaderView, QTableWidgetItem,
    QSizePolicy, QSplitter, QFrame, QMessageBox, QFileDialog, QAbstractItemView,
    QMenu, QCheckBox, QTextEdit, QTableWidget
)
from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtGui import QColor, QIntValidator, QActionGroup, QAction, QFont, QFontMetrics
import pyqtgraph as pg
import numpy as np

from dsd_data_manager import DSDDataManager
from dsd_controller import DSDController
from dsd_widgets import DSDTableWidget, DSDAddDomainDialog
from trj_widgets import FilterBarWidget, PlayerControlWidget
from ui_components import ColorButton, NoNewLineDelegate, RightClickButton
from settings_manager import SettingsManager
from log_parser import LogParser
from popout_window import PopOutWindow

class DSDPlotPanel(QWidget):
    def __init__(self, main_window_ref):
        super().__init__()
        self.main_window = main_window_ref
        
        self.data_manager = DSDDataManager()
        self.controller = None 
        self.loaded_path = None
        self._updating_from_code = False
        
        self._init_ui()
        self.controller = DSDController(self.plot_widget, self.data_manager)
        self._connect_signals()
        
        self._update_ui_state(project_loaded=False)

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        
        # --- Top Controls ---
        top_container = QWidget()
        top_layout = QHBoxLayout(top_container)
        top_layout.setContentsMargins(5, 0, 5, 5)
        top_layout.setSpacing(10)

        # Mode Combo Placeholder
        self.mode_combo_placeholder = QWidget()
        self.mode_combo_placeholder.setFixedWidth(75)
        top_layout.addWidget(self.mode_combo_placeholder)

        # 1. Left Controls (Study/System/Axes)
        self.left_grid = QGridLayout()
        self.left_grid.setContentsMargins(0, 0, 0, 0)
        
        self.left_grid.addWidget(QLabel("Study"), 0, 0)
        self.study_combo = self._create_combo("Select Study")
        self.left_grid.addWidget(self.study_combo, 0, 1)

        self.left_grid.addWidget(QLabel("System"), 0, 2)
        self.system_combo = self._create_combo("Select System")
        self.left_grid.addWidget(self.system_combo, 0, 3)

        self.left_grid.addWidget(QLabel("Slice Ortho to"), 1, 0)
        self.slice_axis_combo = self._create_combo("Select Axis")
        self.left_grid.addWidget(self.slice_axis_combo, 1, 1)

        self.left_grid.addWidget(QLabel("Observe Axis"), 1, 2)
        self.observe_axis_combo = self._create_combo("Select Axis")
        self.left_grid.addWidget(self.observe_axis_combo, 1, 3)
        
        self.left_grid.setColumnStretch(1, 1)
        self.left_grid.setColumnStretch(3, 1)

        # Stretch left side to take priority
        top_layout.addLayout(self.left_grid, stretch=5)

        # 2. Visual Spacer Line
        line = QFrame()
        line.setFrameShape(QFrame.Shape.VLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        line.setLineWidth(1)
        line.setStyleSheet("background-color: #888; margin-top: 5px; margin-bottom: 5px;")
        top_layout.addWidget(line)

        # 3. Right Controls (Filter/Options)
        self.right_grid = QGridLayout()
        self.right_grid.setContentsMargins(0, 0, 0, 0)
        self.right_grid.setHorizontalSpacing(10)
        self.right_grid.setVerticalSpacing(5)

        # Column Widths
        middle_min_width = 180  # For Z-Filter and Options
        far_right_fixed_width = 150  # For Ref and Plot Type

        self.right_grid.addWidget(QLabel("Z-Filter"), 0, 0)
        self.zfilter_combo = self._create_combo("No Z-Filter")
        self.zfilter_combo.setMinimumWidth(middle_min_width)
        self.right_grid.addWidget(self.zfilter_combo, 0, 1)

        self.zfilter_ref_combo = self._create_combo("Initial", ["Initial", "Current", "Final"])
        self.zfilter_ref_combo.setFixedWidth(far_right_fixed_width)
        self.right_grid.addWidget(self.zfilter_ref_combo, 0, 2)

        self.right_grid.addWidget(QLabel("Type"), 1, 0)
        
        self.plot_type_combo = self._create_combo("Displacement plot", ["Displacement plot", "Strain Over Step", "Strain Over Strain"])
        self.plot_type_combo.setFixedWidth(middle_min_width)
        self.right_grid.addWidget(self.plot_type_combo, 1, 1)

        self.options_btn = QPushButton("Options")
        self.options_btn.setMinimumWidth(far_right_fixed_width)
        self.options_menu = QMenu(self)
        self.options_btn.setMenu(self.options_menu)
        self.right_grid.addWidget(self.options_btn, 1, 2)

        # Right side takes only what it needs (stretch=0)
        top_layout.addLayout(self.right_grid, stretch=0)
        
        self.add_btn = QPushButton("Add")
        self.add_btn.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
        self.add_btn.setMinimumWidth(60)
        self.add_btn.clicked.connect(self.add_new_domain)
        top_layout.addWidget(self.add_btn)

        main_layout.addWidget(top_container)

        # --- Main Splitter ---
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        
        # Left: Viz Area
        canvas_container = QWidget()
        canvas_layout = QVBoxLayout(canvas_container)
        canvas_layout.setContentsMargins(0,0,0,0)

        # Viz Layout
        viz_area = QWidget()
        viz_layout = QHBoxLayout(viz_area)
        viz_layout.setContentsMargins(0,0,0,0)
        
        self.filter_bar = FilterBarWidget()
        self.filter_bar.setVisible(False)
        viz_layout.addWidget(self.filter_bar)
        
        self.plot_widget = pg.PlotWidget()
        
        self.lock_axes_btn = RightClickButton(self.plot_widget)
        self.lock_axes_btn.setCheckable(True)
        self.lock_axes_btn.setText("🔓")
        self.lock_axes_btn.setToolTip("Left Click: Lock/Unlock View Limits\nRight Click: Resize Options")
        
        self.plot_widget.addLegend()
        self.lock_axes_btn.hide()
        
        original_resize = self.plot_widget.resizeEvent
        def custom_resize_event(event):
            self.lock_axes_btn.setGeometry(self.plot_widget.width() - 40, 0, 40, 30)
            if original_resize:
                original_resize(event)
        self.plot_widget.resizeEvent = custom_resize_event

        self.plot_widget.setBackground('w')
        self.plot_widget.setLabel('bottom', "Slice Position")
        self.plot_widget.setLabel('left', "Displacement / Strain")
        for axis in ['left', 'bottom', 'right', 'top']:
            self.plot_widget.getPlotItem().getAxis(axis).setPen('k')
            self.plot_widget.getPlotItem().getAxis(axis).setTextPen('k')
            
        viz_layout.addWidget(self.plot_widget, 1)
        canvas_layout.addWidget(viz_area, 1)
        
        self.player_controls = PlayerControlWidget()
        canvas_layout.addWidget(self.player_controls)
        
        self.main_splitter.addWidget(canvas_container)

        # Right: Config Panel (Table + Display)
        config_container = QWidget()
        config_layout = QVBoxLayout(config_container)
        
        # Table (Domain Management)
        self.plot_table = DSDTableWidget(self)
        self.plot_table.domainEdited.connect(self.update_plot)
        self.plot_table.itemChanged.connect(self._on_table_item_changed)
        self.plot_table.rowRemoved.connect(self._on_row_removed)

        config_layout.addWidget(self.plot_table, stretch=2)
        
        # Display Area (Text - RESTORED)
        display_container = QWidget()
        display_vbox = QVBoxLayout(display_container)
        display_vbox.setContentsMargins(0,0,0,0)
        
        self.display_text = QTextEdit()
        self.display_text.setReadOnly(True)
        self.display_text.setFrameStyle(QFrame.Shape.StyledPanel | QFrame.Shadow.Sunken)
        self.display_text.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap) # Prevent auto-wrap to detect overflow
        font = self.display_text.font()
        font.setFamily("Courier New") 
        font.setPointSize(9)
        self.display_text.setFont(font)
        self.display_text.setText("...")
        display_vbox.addWidget(self.display_text)
        
        config_layout.addWidget(display_container, stretch=1)
        
        # Bottom Buttons
        btns_layout = QHBoxLayout()
        self.popout_btn = QPushButton("Pop Out")
        self.export_btn = QPushButton("Quick Export")

        self.save_btn = QPushButton("Save")
        self.load_btn = QPushButton("Load")
        self.exit_btn = QPushButton("Exit")
        
        btns_layout.addWidget(self.popout_btn)
        btns_layout.addWidget(self.export_btn)
        btns_layout.addWidget(self.save_btn)
        btns_layout.addWidget(self.load_btn)
        btns_layout.addWidget(self.exit_btn)
        config_layout.addLayout(btns_layout)
        
        self.main_splitter.addWidget(config_container)
        self.main_splitter.setSizes([900, 300])
        
        main_layout.addWidget(self.main_splitter, 1)

        self._init_options_menu()
        self.plot_type_combo.currentTextChanged.connect(self._update_options_menu)


    # --- Helpers ---
    def _init_options_menu(self):
        self.opt_actions = {}
        
        # Options with mode assignment
        self.opts_config = [
            ('disp_std', "Show displacement standard deviation"),
            ('strain_std', "Strain standard deviation"),
            ('opt_line', "Show optimal line"),
            ('total_count', "Show total box particle numbers"),
            ('weighted_count', "Show weighted box particle numbers"),
        ]
        
        for key, text in self.opts_config:
            action = QAction(text, self)
            action.setCheckable(True)
            
            if key in ['disp_std', 'weighted_count']:
                action.setChecked(True)
                
            action.triggered.connect(lambda c, k=key: self._on_option_toggled(k))
            self.opt_actions[key] = action

        self.current_options = {key: self.opt_actions[key].isChecked() for key in self.opt_actions}
        self._update_options_menu()

    def _update_options_menu(self):
        self.options_menu.clear()
        plot_type = self.plot_type_combo.currentText()
        is_strain_plot = "Strain Over" in plot_type
        
        # 1. Displacement/Strain STD (mutually exclusive in menu display)
        if not is_strain_plot:
            self.options_menu.addAction(self.opt_actions['disp_std'])
        else:
            self.options_menu.addAction(self.opt_actions['strain_std'])
            
        # 2. Optimal Line
        self.options_menu.addAction(self.opt_actions['opt_line'])
        
        # 3. Particle Counts (only for Displacement plot)
        if not is_strain_plot:
            self.options_menu.addSeparator()
            self.options_menu.addAction(self.opt_actions['total_count'])
            self.options_menu.addAction(self.opt_actions['weighted_count'])
        else:
            # When switching to strain plot, these are "hidden" but we don't need to do 
            # anything special here as we just don't add them to the menu.
            # However, the controller logic should also respect this.
            pass
        
    def on_opt_line_deleted(self):
        # Called only when explicit user action disables it
        if 'opt_line' in self.opt_actions:
            self.opt_actions['opt_line'].setChecked(False)
            self.current_options['opt_line'] = False
            self.update_plot()

    def _on_option_toggled(self, key):
        if key == 'total_count' and self.opt_actions['total_count'].isChecked():
            self.opt_actions['weighted_count'].setChecked(False)
            self.current_options['weighted_count'] = False
            
        if key == 'weighted_count' and self.opt_actions['weighted_count'].isChecked():
            self.opt_actions['total_count'].setChecked(False)
            self.current_options['total_count'] = False

        self.current_options[key] = self.opt_actions[key].isChecked()

        if key == 'opt_line':
            if self.current_options['opt_line']:
                # If enabled, verify logic adds it in update_plot if needed
                pass
            else:
                # If disabled, remove row if exists
                if self.plot_table.opt_line_row != -1:
                    self.plot_table.removeRow(self.plot_table.opt_line_row)
                    self.plot_table.opt_line_row = -1
                    
        self.update_plot()

    def set_options(self, options):
        self.current_options.update(options)
        
        # Enforce mutual exclusivity for particle counts
        if self.current_options.get('total_count') and self.current_options.get('weighted_count'):
             # Priority to weighted if both unexpectedly True in saved state
             self.current_options['total_count'] = False
             
        for key, action in self.opt_actions.items():
            if key in self.current_options:
                action.setChecked(self.current_options.get(key, False))
        self.update_plot()

    def attach_mode_combo(self, combo_box):
        top_layout = self.layout().itemAt(0).widget().layout()
        self.mode_combo_placeholder.setParent(None)
        top_layout.insertWidget(0, combo_box)

    def _create_combo(self, placeholder, items=None):
        combo = QComboBox()
        combo.setItemDelegate(NoNewLineDelegate(combo))
        combo.addItem(placeholder)
        if items:
            for item in items:
                if item != placeholder:
                    combo.addItem(item)
        combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        return combo

    def _connect_signals(self):
        self.study_combo.currentTextChanged.connect(self.on_study_changed)
        self.system_combo.currentTextChanged.connect(self.on_system_changed)
        
        self.slice_axis_combo.currentTextChanged.connect(self.update_plot)
        self.observe_axis_combo.currentTextChanged.connect(self.update_plot)
        self.plot_type_combo.currentTextChanged.connect(self.update_plot)
        self.zfilter_combo.currentTextChanged.connect(self._on_zfilter_changed)
        self.zfilter_ref_combo.currentTextChanged.connect(self._on_zfilter_changed)
        self.controller.boundsChanged.connect(self._on_controller_bounds_changed)
        self.filter_bar.rangesChanged.connect(self.update_plot)
        
        # Connect to TextEdit with adaptive font sizing
        self.controller.errorUpdated.connect(self._on_error_updated)
        self.controller.frameChanged.connect(self._on_frame_changed)
        
        self.player_controls.stepChanged.connect(self._on_step_changed_by_user)
        self.player_controls.playToggled.connect(lambda p: self.controller.play() if p else self.controller.pause())
        self.player_controls.fpsChanged.connect(self.controller.set_fps)
        self.player_controls.autoReplayToggled.connect(self.controller.set_auto_replay)
        
        self.popout_btn.clicked.connect(self.launch_popout)
        self.export_btn.clicked.connect(self.quick_export)
        self.save_btn.clicked.connect(self.save_session)
        self.load_btn.clicked.connect(self.load_session)
        self.exit_btn.clicked.connect(self.main_window.close)
        
        self.lock_axes_btn.toggled.connect(self._on_lock_toggled)
        self.lock_axes_btn.rightClicked.connect(self._on_lock_right_clicked)
        
        # Trigger font size adjustment when the splitter divider is moved
        self.main_splitter.splitterMoved.connect(self._adjust_display_font_size)

    def _on_stats_updated(self, data):
        self.stats_table.setRowCount(0)
        
        if not data or not isinstance(data, list):
            return
            
        self.stats_table.setRowCount(len(data))
        
        for r, row_data in enumerate(data):
            # Name
            item_name = QTableWidgetItem(str(row_data.get('name', '')))
            item_name.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            self.stats_table.setItem(r, 0, item_name)
            
            # Strain
            strain = row_data.get('strain')
            s_str = f"{strain:.6e}" if strain is not None else "N/A"
            item_s = QTableWidgetItem(s_str)
            item_s.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.stats_table.setItem(r, 1, item_s)
            
            # Error
            err = row_data.get('error')
            e_str = f"{err:.4f}" if err is not None else "N/A"
            item_e = QTableWidgetItem(e_str)
            item_e.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.stats_table.setItem(r, 2, item_e)
            
            # Parts
            count = row_data.get('count')
            if isinstance(count, (int, float)):
                c_str = f"{int(count):,}"
            else:
                c_str = str(count)
            item_c = QTableWidgetItem(c_str)
            item_c.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.stats_table.setItem(r, 3, item_c)
            
            # Weights
            weight = row_data.get('weight')
            if isinstance(weight, (int, float)):
                w_str = f"{weight:,.2f}"
            else:
                w_str = str(weight)
            item_w = QTableWidgetItem(w_str)
            item_w.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.stats_table.setItem(r, 4, item_w)

    def _on_error_updated(self, text):
        self.display_text.setText(text)
        self._adjust_display_font_size()

    def _adjust_display_font_size(self):
        text = self.display_text.toPlainText()
        if not text: return
        
        # Determine max line width
        lines = text.split('\n')
        if not lines: return
        max_line = max(lines, key=len)
        
        target_width = self.display_text.viewport().width() - 10
        if target_width <= 0: return

        current_font = self.display_text.font()
        size = 9 # Start with default 9pt
        
        # Shrink to fit
        while size > 5:
            if hasattr(current_font, 'setPointSizeF'):
                current_font.setPointSizeF(float(size))
            else:
                current_font.setPointSize(int(size))
            
            metrics = QFontMetrics(current_font)
            if metrics.horizontalAdvance(max_line) <= target_width:
                break
            size -= 0.5 if hasattr(current_font, 'setPointSizeF') else 1
            
        if hasattr(current_font, 'setPointSizeF'):
            current_font.setPointSizeF(float(size))
        else:
            current_font.setPointSize(int(size))
            
        self.display_text.setFont(current_font)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._adjust_display_font_size()

    def _on_controller_bounds_changed(self, bounds):
        """
        Updates the Filter Bar labels dynamically during playback 
        while preserving relative handle positions.
        """
        if 'z_filter' in bounds:
            zmin, zmax = bounds['z_filter']
            # We block signals to prevent the bar from triggering 
            # a recursive update_plot during playback
            self.filter_bar.blockSignals(True)
            self.filter_bar.set_data_range(zmin, zmax)
            self.filter_bar.blockSignals(False)

    def load_project(self, root_path, keywords, force_reload=False, keep_table=False):
        if not force_reload and self.loaded_path == root_path:
            return

        studies, warnings, file_map = LogParser.discover_studies_systems(root_path, keywords)
        
        load_warns, _ = self.data_manager.load_project_data(studies, root_path, keywords, file_map)
        warnings.extend(load_warns)
        
        self.loaded_path = root_path
        self._update_ui_state(project_loaded=True, keep_table=keep_table)

    def _update_ui_state(self, project_loaded, keep_table=False):
        if not keep_table:
            self.plot_table.setRowCount(0)
            self.plot_widget.autoRange()
        
        if project_loaded:
            self.study_combo.setEnabled(True)
            self.system_combo.setEnabled(True)
            self.add_btn.setEnabled(True)
            
            studies = sorted(list(self.data_manager.parsers.keys()))
            self._populate_combo(self.study_combo, "Select Study", studies)
        else:
            self.add_btn.setEnabled(False)

    def _populate_combo(self, combo, placeholder, items, current=None):
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(placeholder)
        if items:
            combo.addItems(items)
        
        if current and current in items:
            combo.setCurrentText(current)
        elif len(items) == 1 and placeholder == "Select Study":
            combo.setCurrentIndex(1)
        else:
            combo.setCurrentIndex(0)
        combo.blockSignals(False)
            
    def on_study_changed(self, text):
        current_system = self.system_combo.currentText()
        if text != "Select Study" and text in self.data_manager.parsers:
            systems = sorted(list(self.data_manager.parsers[text].keys()))
        else:
            systems = []
        self._populate_combo(self.system_combo, "Select System", systems, current_system)
        self.on_system_changed(self.system_combo.currentText())

    def on_system_changed(self, text):
        if text != "Select System":
            study = self.study_combo.currentText()
            self.controller.set_active_system(study, text)
            self.player_controls.set_timesteps(self.controller.get_available_timesteps())
            
            # Capture currently selected axes before repopulating
            current_slice = self.slice_axis_combo.currentText()
            current_obs = self.observe_axis_combo.currentText()
            current_z = self.zfilter_combo.currentText()

            df, _ = self.data_manager.load_frame(study, text, self.controller.timesteps[0])
            if df is not None:
                cols = [c for c in df.columns if c not in ['id', 'type']]
                
                # Pass captured text as the 'current' argument to preserve selection
                self._populate_combo(self.slice_axis_combo, "Select Axis", cols, current_slice)
                self._populate_combo(self.observe_axis_combo, "Select Axis", cols, current_obs)
                self._populate_combo(self.zfilter_combo, "No Z-Filter", cols, current_z)
        
        self._sync_lock_visibility()
        self.update_plot()

    def _sync_lock_visibility(self):
        if self.plot_table.rowCount() > 0:
            self.lock_axes_btn.show()
        else:
            self.lock_axes_btn.hide()

    def add_new_domain(self):
        slice_ax = self.slice_axis_combo.currentText()
        
        types = []
        study = self.study_combo.currentText()
        system = self.system_combo.currentText()
        axis_bounds = None
        if self.controller.timesteps:
            df, _ = self.data_manager.load_frame(study, system, self.controller.timesteps[0])
            if df is not None:
                if 'type' in df.columns:
                    types = sorted(df['type'].unique().tolist())
                if slice_ax in df.columns:
                    axis_bounds = (df[slice_ax].min(), df[slice_ax].max())
        
        dlg = DSDAddDomainDialog(self, None, slice_ax, types, axis_bounds)
        if dlg.exec():
            data = dlg.get_data()
            self.plot_table.add_domain(data.get('name', "Domain " + str(self.plot_table.rowCount()+1)), data)
            self._sync_lock_visibility()
            self.update_plot()
            self.controller.auto_scale()

    def get_options(self):
        opts = self.current_options.copy()
        if hasattr(self, 'zfilter_combo'):
            opts['z_filter_col'] = self.zfilter_combo.currentText()
            opts['z_filter_ref'] = self.zfilter_ref_combo.currentText()
        return opts

    def update_plot(self, *args):
        domains = self.plot_table.get_domains()
        
        # --- Logic: Auto-restore Optimal Line if needed ---
        if self.current_options.get('opt_line'):
            if self.plot_table.opt_line_row == -1:
                real_domains = [d for d in domains if not d.get('is_optimal_line')]
                if real_domains:
                    self.plot_table.add_domain("Optimal Line", {'color': 'black', 'style': '--', 'size': '1'}, is_optimal_line=True)
                    domains = self.plot_table.get_domains() 
        else:
            if self.plot_table.opt_line_row != -1:
                self.plot_table.removeRow(self.plot_table.opt_line_row)
                self.plot_table.opt_line_row = -1
                domains = self.plot_table.get_domains() 
                
        opts = self.get_options()
        slice_ax = self.slice_axis_combo.currentText()
        obs_ax = self.observe_axis_combo.currentText()
        p_type = self.plot_type_combo.currentText()
        
        if "Select" in slice_ax or "Select" in obs_ax:
            return
            
        # Check the COMBO TEXT to ensures the filter is applied during session load
        z_col = self.zfilter_combo.currentText()
        z_ranges = self.filter_bar.current_ranges if z_col != "No Z-Filter" else None
        
        self.controller.update_config(domains, opts, slice_ax, obs_ax, p_type, z_ranges)

    def _update_table_stats(self, domains, opts, slice_ax, obs_ax, p_type, z_ranges):
        # Fetch data and calculate stats for table display
        study = self.study_combo.currentText()
        system = self.system_combo.currentText()
        
        # FIX: Use controller's timestep instead of player_controls
        timestep = self.controller.current_timestep if self.controller else None
        
        if not study or not system or timestep is None:
            return

        # Load Frames (assume fast if cached or handled by manager)
        df_curr, box_curr = self.data_manager.load_frame(study, system, timestep)
        df_init, box_init = self.data_manager.load_frame(study, system, self.controller.timesteps[0])
        
        if df_curr is None or df_init is None:
            return

        # Indices for new columns (#Parts, #Weights are last two)
        col_count = self.plot_table.columnCount()
        col_w = col_count - 1
        col_p = col_count - 2
        
        for r in range(self.plot_table.rowCount()):
            is_opt = (r == self.plot_table.opt_line_row)
            
            txt_p = "-"
            txt_w = "-"
            
            if not is_opt:
                if r < len(domains):
                    dom = domains[r]
                    if dom.get('active', True):
                        try:
                            res = self.data_manager.slice_disp_mean(
                                df_init, df_curr, dom, slice_ax, obs_ax, box_curr,
                                z_col=opts.get('z_filter_col'), z_ref=opts.get('z_filter_ref'),
                                z_ranges=z_ranges, df_final=None, box_init=box_init
                            )
                            if res is not None and not res.empty:
                                total_parts = res['count'].sum()
                                total_weight = res['sum_weights'].sum()
                                
                                txt_p = f"{int(total_parts):,}"
                                txt_w = f"{total_weight:,.2f}"
                        except Exception:
                            pass

            # Update Table Items
            item_p = self.plot_table.item(r, col_p)
            if not item_p:
                item_p = QTableWidgetItem()
                item_p.setFlags(item_p.flags() ^ Qt.ItemFlag.ItemIsEditable)
                item_p.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.plot_table.setItem(r, col_p, item_p)
            item_p.setText(txt_p)

            item_w = self.plot_table.item(r, col_w)
            if not item_w:
                item_w = QTableWidgetItem()
                item_w.setFlags(item_w.flags() ^ Qt.ItemFlag.ItemIsEditable)
                item_w.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.plot_table.setItem(r, col_w, item_w)
            item_w.setText(txt_w)

    def _on_zfilter_changed(self, text):
        col = self.zfilter_combo.currentText()
        ref = self.zfilter_ref_combo.currentText()
        
        study = self.study_combo.currentText()
        system = self.system_combo.currentText()

        if col != "No Z-Filter":
            self.filter_bar.setVisible(True)
            self.zfilter_ref_combo.setEnabled(True)
            
            # If we have a valid study/system, update the widget labels/range
            if study and system and "Select" not in study and "Select" not in system:
                 limits = self.controller.get_scope_min_max(study, system, col, ref)
                 if limits:
                     # This updates the UI widget range (labels at top/bottom)
                     self.filter_bar.set_data_range(limits[0], limits[1])
        else:
            self.filter_bar.setVisible(False)
            self.zfilter_ref_combo.setEnabled(False)
            
        self.update_plot()

    def _on_frame_changed(self, idx):
        self.player_controls.set_step_index(idx)

    def _on_step_changed_by_user(self, step):
        self.controller.set_timestep_index(step)
        
    def _on_table_item_changed(self, item):
        if item.column() == 0: 
            self.update_plot()

    def _on_row_removed(self, row):
        # Was the removed row the Optimal Line?
        was_optimal = (row == self.plot_table.opt_line_row)
        
        # If the user explicitly deleted the optimal line row, disable the option
        if was_optimal:
            if 'opt_line' in self.opt_actions:
                 # Only if it was removed by user action (checking signals context is hard, 
                 # but usually on_row_removed triggers after removal)
                 # Wait: if we removed it automatically in update_plot, this triggers too.
                 # We need to distinguish.
                 # However, update_plot sets opt_line_row to -1 BEFORE removing?
                 # If opt_line_row was not -1 and equals row, it's a deletion.
                 
                 # Check if option is currently ON. If ON, and row removed, check if it was auto-removal.
                 # Auto-removal happens if no real domains exist.
                 
                 real_domains = [d for d in self.plot_table.get_domains() if not d.get('is_optimal_line')]
                 
                 if not real_domains:
                     # This might be auto-removal (last real domain deleted -> opt line auto removed)
                     # In this case, we WANT to keep option ON.
                     pass
                 else:
                     # Real domains exist, but Opt Line row was removed -> User must have deleted it.
                     # Turn option OFF.
                     self.on_opt_line_deleted()
        
        # Adjust opt_line_row if a row above it was deleted
        if self.plot_table.opt_line_row != -1:
            if row < self.plot_table.opt_line_row:
                self.plot_table.opt_line_row -= 1
        
        # Trigger cleanup or update
        real_domains = [d for d in self.plot_table.get_domains() if not d.get('is_optimal_line')]
        if not real_domains and self.plot_table.opt_line_row != -1:
            # Auto-remove the opt line row visually, but KEEP option True
            self.plot_table.removeRow(self.plot_table.opt_line_row)
            self.plot_table.opt_line_row = -1
            
        self._sync_lock_visibility()
        self.update_plot()

    def _trigger_resize_and_lock(self, mode):
        self.controller.auto_scale(mode=mode)
        self.lock_axes_btn.setChecked(True)

    def _on_lock_toggled(self, checked):
        self.lock_axes_btn.setText("🔒" if checked else "🔓")
        self.controller.set_view_lock(checked)
        if not checked:
            self.plot_widget.plotItem.autoRange()

    def _on_lock_right_clicked(self):
        menu = QMenu(self)
        
        act_final = menu.addAction("Final Resize")
        act_final.triggered.connect(lambda: self._trigger_resize_and_lock('final'))
        
        act_max = menu.addAction("Maximum Resize")
        act_max.triggered.connect(lambda: self._trigger_resize_and_lock('max'))
        
        menu.exec(self.lock_axes_btn.mapToGlobal(QPoint(10, 10)))

    # --- Session Saving/Loading ---
    def save_session(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save DSD Session", "", "JSON Files (*.json)")
        if not path: return
        self.save_session_to_file(path)

    def load_session(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load DSD Session", "", "JSON Files (*.json)")
        if not path: return
        self.load_session_from_file(path)
        
    def save_session_to_file(self, path):
        p_path = self.main_window.path_edit.text()
        if not p_path: return False
        
        session_data = {
            'type': 'dsd_plot',
            'project_path': self.main_window.path_edit.text(),
            'keywords': self.main_window.chip_input.get_chips(),
            'domains': self.plot_table.get_domains(),
            'opt_line_row': self.plot_table.opt_line_row,
            'view_locked': self.lock_axes_btn.isChecked(),
            'view_limits': self.controller.view_limits,
            'global_options': {
                'study': self.study_combo.currentText(),
                'system': self.system_combo.currentText(),
                'slice_axis': self.slice_axis_combo.currentText(),
                'observe_axis': self.observe_axis_combo.currentText(),
                'plot_type': self.plot_type_combo.currentText(),
                'z_filter_col': self.zfilter_combo.currentText(),
                'z_filter_ref': self.zfilter_ref_combo.currentText(),
                'z_ranges': self.filter_bar.current_ranges if self.filter_bar.isVisible() else None,
                'options': self.get_options(),
                'current_step_index': self.player_controls.slider.value(),
                'last_target_strain': getattr(self.controller, 'last_target_strain', None)
            }
        }
        
        return SettingsManager.save_state(path, session_data)
        if success:
            print(f"[System] Saved session: {path} for mode DSD Mode")
        else:
            print(f"[System] Failed to save session: {path}")
        return success

    def load_session_from_file(self, path):
        if not path or not os.path.exists(path): return
        
        data = SettingsManager.load_state(path)
        if not data:
            QMessageBox.critical(self, "Error", "Failed to load DSD session file.")
            return
            
        # --- Mismatch Check ---
        if data.get('type') != 'dsd_plot':
            msg = QMessageBox(self.main_window)
            msg.setWindowTitle("Mode Mismatch")
            
            # Determine which mode it belongs to
            target_mode = "Trajectory Plot" if data.get('type') == 'trj_plot' else "Log Plot"
            target_idx = self.main_window.MODE_TRJ if data.get('type') == 'trj_plot' else self.main_window.MODE_LOG
            target_panel = self.main_window.trj_plot_panel if data.get('type') == 'trj_plot' else self.main_window.log_plot_panel

            msg.setText(f"Mode Mismatch. This is a {target_mode} session.")
            abort_btn = msg.addButton("Abort", QMessageBox.ButtonRole.RejectRole)
            switch_btn = msg.addButton(f"Switch to {target_mode}", QMessageBox.ButtonRole.AcceptRole)
            msg.exec()

            if msg.clickedButton() == switch_btn:
                # Save current (DSD) state
                self.main_window.save_session_for_mode(self.main_window.MODE_DSD)
                # Set flag to skip orchestration's autoload
                self.main_window._skip_next_orchestration = True
                # Switch to Target Mode
                self.main_window.switch_to_mode(target_idx)
                # Force load
                target_panel.load_session_from_file(path)
            return
        
        print(f"[System] Loading session: {path} for mode DSD Mode")

        self.main_window.path_edit.blockSignals(True)
        self.main_window.chip_input.blockSignals(True)

        try:
            # 1. Load Project Data
            project_path = data.get('project_path', '')
            keywords = data.get('keywords', [])
            self.main_window.path_edit.setText(project_path)
            self.main_window.chip_input.set_chips(keywords)
            self.load_project(Path(project_path), keywords, force_reload=True, keep_table=True)
            
            # 2. Establish System Selection
            g_opts = data.get('global_options', {})
            self.study_combo.setCurrentText(g_opts.get('study', 'Select Study'))
            self.on_study_changed(self.study_combo.currentText())
            self.system_combo.setCurrentText(g_opts.get('system', 'Select System'))
            self.on_system_changed(self.system_combo.currentText())
            
            # 3. Establish Axis Selections
            self.slice_axis_combo.setCurrentText(g_opts.get('slice_axis', 'Select Axis'))
            self.observe_axis_combo.setCurrentText(g_opts.get('observe_axis', 'Select Axis'))
            self.plot_type_combo.setCurrentText(g_opts.get('plot_type', 'Displacement plot'))

            # 4. Configure Filter (Now that axes are ready)
            z_col = g_opts.get('z_filter_col', 'No Z-Filter')
            z_ref = g_opts.get('z_filter_ref', 'Initial') 
            
            # Block signals temporarily to prevent multiple intermediate redraws
            self.zfilter_combo.blockSignals(True)
            self.zfilter_ref_combo.blockSignals(True)
            self.zfilter_combo.setCurrentText(z_col)
            self.zfilter_ref_combo.setCurrentText(z_ref)
            self.zfilter_combo.blockSignals(False)
            self.zfilter_ref_combo.blockSignals(False)
            
            # Manually trigger label update and visibility
            self._on_zfilter_changed(z_col)
            
            # Restore the specific handle positions (Red Bars)
            saved_ranges = g_opts.get('z_ranges')
            if saved_ranges and z_col != "No Z-Filter":
                self.filter_bar.set_current_ranges(saved_ranges)

            # 5. Restore Rest of the State
            step_idx = g_opts.get('current_step_index', 0)
            self.player_controls.set_step_index(step_idx)
            
            if 'options' in g_opts:
                self.set_options(g_opts['options'])
            
            self.plot_table.blockSignals(True)
            self.plot_table.setRowCount(0)
            self.plot_table.opt_line_row = -1
            for domain in data.get('domains', []):
                self.plot_table.add_domain(domain.get('name', 'Domain'), domain)
            self.plot_table.blockSignals(False)
            self._sync_lock_visibility()
            
            # 6. Restore View Limits
            stored_limits = data.get('view_limits', {})
            self.controller.view_limits = stored_limits
            current_type = g_opts.get('plot_type', 'Displacement plot')
            if current_type in stored_limits:
                ranges = stored_limits[current_type]
                self.plot_widget.setXRange(ranges[0][0], ranges[0][1], padding=0)
                self.plot_widget.setYRange(ranges[1][0], ranges[1][1], padding=0)

            locked = data.get('view_locked', False)
            self.lock_axes_btn.setChecked(locked)
            if locked:
                self.controller.set_view_lock(True)
                
            if 'last_target_strain' in g_opts:
                self.controller.last_target_strain = g_opts['last_target_strain']
                
            # 7. Final Trigger
            self.update_plot()
        finally:
            self.main_window.path_edit.blockSignals(False)
            self.main_window.chip_input.blockSignals(False)

    def launch_popout(self):
        state = self.controller.get_current_plot_state()
        if not state or not state.get('y_axes'):
            QMessageBox.information(self, "Info", "No valid data to pop out.")
            return

        dpi = self.logicalDpiX()
        w_in = self.plot_widget.width() / dpi
        h_in = self.plot_widget.height() / dpi

        win = PopOutWindow(state, self, figsize=(w_in, h_in))
        win.show()
        # Keep reference
        if not hasattr(self.main_window, 'dsd_popouts'):
            self.main_window.dsd_popouts = []
        self.main_window.dsd_popouts.append(win)
        win.destroyed.connect(lambda: self.main_window.dsd_popouts.remove(win) if win in self.main_window.dsd_popouts else None)

    def quick_export(self):
        filters = (
            "PNG Image (*.png);;"
            "JPEG Image (*.jpg *.jpeg);;"
            "TIFF Image (*.tif *.tiff);;"
            "WebP Image (*.webp);;"
            "Scalable Vector Graphics (*.svg *.svgz);;"
            "PDF Document (*.pdf);;"
            "Encapsulated PostScript (*.eps);;"
            "PostScript (*.ps);;"
            "PGF Code (*.pgf);;"
            "Raw Pixel Data (*.raw *.rgba);;"
            "CSV Data (*.csv);;"
            "TSV Data (*.tsv)"
        )
        path, _ = QFileDialog.getSaveFileName(self, "Export Plot", "", filters)
        
        if path:
            dpi = self.logicalDpiX()
            width_in = self.plot_widget.width() / dpi
            height_in = self.plot_widget.height() / dpi
            self.controller.export_plot(path, figsize=(width_in, height_in))
