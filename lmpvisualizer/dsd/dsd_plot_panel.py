import os
import copy
import random
from pathlib import Path
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGridLayout, 
    QComboBox, QPushButton, QHeaderView, QTableWidgetItem,
    QSizePolicy, QSplitter, QFrame, QMessageBox, QFileDialog, QAbstractItemView,
    QMenu, QCheckBox, QTextEdit, QTableWidget, QWidgetAction, QToolButton
)
from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtGui import QColor, QIntValidator, QActionGroup, QAction, QFont, QFontMetrics
import pyqtgraph as pg
import numpy as np

from lmpvisualizer.dsd.dsd_data_manager import DSDDataManager
from lmpvisualizer.dsd.dsd_controller import DSDController
from lmpvisualizer.dsd.dsd_widgets import DSDTableWidget, DSDAddDomainDialog
from lmpvisualizer.trj.trj_widgets import FilterBarWidget, PlayerControlWidget
from lmpvisualizer.shared.ui_components import ColorButton, NoNewLineDelegate, RightClickButton, MissingPathResolver
from lmpvisualizer.shared.settings_manager import SettingsManager
from lmpvisualizer.log.log_parser import LogParser
from lmpvisualizer.shared.popout_window import PopOutWindow, mint_preset_name
from lmpvisualizer.shared.auto_index_dialog import AutoIndexDialog
from lmpvisualizer.shared.video_export_dialog import VideoExportDialog
from lmpvisualizer.shared.logger_setup import get_logger

logger = get_logger(__name__)

class DSDPlotPanel(QWidget):
    def __init__(self, main_window_ref):
        super().__init__()
        self.main_window = main_window_ref
        
        self.data_manager = DSDDataManager()
        self.controller = None 
        self.loaded_path = None
        # study key -> {'path': <project path>, 'study': <raw folder name>}
        self.study_origins = {}
        self._pending_study_key = None
        self._updating_from_code = False
        self._loading_session = False
        # Set while load_project() rebuilds the data manager, so on_system_changed()
        # forces the controller to re-derive its timestep lists.
        self._force_system_reload = False
        self.popout_presets = []

        self._init_ui()
        self.controller = DSDController(self.plot_widget, self.data_manager)
        self._connect_signals()
        
        self._update_ui_state(project_loaded=False)
        try:
            self._refresh_popout_button()
        except Exception:
            pass

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

        # 2. Add / Auto Preload - placed LEFT of the divider so they belong to the
        #    plot-side block, and so the divider can line up with the splitter handle.
        self.add_btn = QPushButton("Add")
        self.add_btn.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
        self.add_btn.setMinimumWidth(60)
        self.add_btn.clicked.connect(self.add_new_domain)
        top_layout.addWidget(self.add_btn)

        self.idx_btn = QPushButton("Auto\nPreload")
        self.idx_btn.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
        self.idx_btn.setMinimumWidth(60)
        self.idx_btn.clicked.connect(self._on_auto_index_clicked)
        top_layout.addWidget(self.idx_btn)

        # 3. Visual Spacer Line
        self.header_divider = QFrame()
        self.header_divider.setFrameShape(QFrame.Shape.VLine)
        self.header_divider.setFrameShadow(QFrame.Shadow.Sunken)
        self.header_divider.setLineWidth(1)
        self.header_divider.setStyleSheet("background-color: #888; margin-top: 5px; margin-bottom: 5px;")
        top_layout.addWidget(self.header_divider)

        # 4. Right Controls (Filter/Options) - inside a container so its width can be
        #    pinned to the table pane, which keeps the divider above the splitter handle.
        self.right_container = QWidget()
        self.right_grid = QGridLayout(self.right_container)
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

        self.zfilter_ref_combo = self._create_combo("Initial", ["Initial", "Current", "Final", "Set step"])
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
        top_layout.addWidget(self.right_container, stretch=0)

        self.top_layout = top_layout
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
        self.export_btn = QPushButton("Quick Export ▾")
        
        # Configure Dropdown Menu for Quick Export
        self.export_menu = QMenu(self)
        self.export_image_action = self.export_menu.addAction("Image...")
        self.export_data_action = self.export_menu.addAction("Raw Data...")
        self.export_video_action = self.export_menu.addAction("Video / GIF...")
        
        self.export_image_action.triggered.connect(self.export_image)
        self.export_data_action.triggered.connect(self.export_raw_data)
        self.export_video_action.triggered.connect(self.export_video)
        self.export_btn.setMenu(self.export_menu)

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


    # --- Helpers ---
    def _on_plot_type_changed(self, text):
        self._update_options_menu()
        
        # Repopulate systems to show/hide "Strain average" based on plot type
        self._repopulate_systems()
        
        context = 'displacement' if text == "Displacement plot" else 'strain'
        if hasattr(self, 'plot_table'):
            self.plot_table.set_context(context)
            
        self.update_plot()

        # RESTORE LIMITS: If view is locked, explicitly apply the saved limits for the NEW type
        if self.controller.view_locked:
            stored_limits = self.controller.view_limits.get(text)
            if stored_limits:
                self.plot_widget.setXRange(stored_limits[0][0], stored_limits[0][1], padding=0)
                self.plot_widget.setYRange(stored_limits[1][0], stored_limits[1][1], padding=0)

    def _repopulate_systems(self):
        """Helper to populate the system combo based on study and plot type."""
        study = self.study_combo.currentText()
        plot_type = self.plot_type_combo.currentText()
        current_selection = self.system_combo.currentText()
        
        if study != "Select Study" and study in self.data_manager.parsers:
            systems = sorted(list(self.data_manager.parsers[study].keys()))
        else:
            systems = []
            
        self.system_combo.blockSignals(True)
        self.system_combo.clear()
        self.system_combo.addItem("Select System")
        
        # Add systems first
        if systems:
            self.system_combo.addItems(systems)
            
        # Add "Strain average" LAST, only for strain plots and if > 1 system
        if len(systems) > 1 and "Strain Over" in plot_type:
            self.system_combo.addItem("Strain average")
            
        # Restore selection if it still exists
        index = self.system_combo.findText(current_selection)
        if index != -1:
            self.system_combo.setCurrentIndex(index)
        elif len(systems) == 1:
            self.system_combo.setCurrentIndex(1)
        else:
            self.system_combo.setCurrentIndex(0)
            
        self.system_combo.blockSignals(False)
        self.on_system_changed(self.system_combo.currentText())

    def _init_options_menu(self):
        self.opt_actions = {}
        
        # Options with mode assignment
        self.opts_config = [
            ('disp_std', "Show displacement standard deviation"),
            ('strain_std', "Strain standard deviation"),
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
            
        # 2. Particle Counts (only for Displacement plot)
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
        # Deprecated: Optimal line visibility is now controlled via table checkbox
        self.update_plot()

    def _on_option_toggled(self, key):
        if key == 'total_count' and self.opt_actions['total_count'].isChecked():
            self.opt_actions['weighted_count'].setChecked(False)
            self.current_options['weighted_count'] = False
            
        if key == 'weighted_count' and self.opt_actions['weighted_count'].isChecked():
            self.opt_actions['total_count'].setChecked(False)
            self.current_options['total_count'] = False

        self.current_options[key] = self.opt_actions[key].isChecked()
        self.update_plot()

    def set_options(self, options):
        self.current_options.update(options)
        
        # Enforce mutual exclusivity for particle counts
        if self.current_options.get('total_count') and self.current_options.get('weighted_count'):
             # Priority to weighted if both unexpectedly True in saved state
             self.current_options['total_count'] = False
             
        for key, action in self.opt_actions.items():
            if key in self.current_options and key in self.opt_actions:
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
        self.plot_type_combo.currentTextChanged.connect(self._on_plot_type_changed)
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
        self.player_controls.rangeRequested.connect(self._on_range_requested)
        self.player_controls.jumpToStepRequested.connect(self._on_jump_to_step)
        
        self.popout_btn.clicked.connect(self._on_popout_button_clicked)
        # self.export_btn click is handled by its dropdown menu
        self.save_btn.clicked.connect(self.save_session)
        self.load_btn.clicked.connect(self.load_session)
        self.exit_btn.clicked.connect(self.main_window.close)
        
        self.lock_axes_btn.toggled.connect(self._on_lock_toggled)
        self.lock_axes_btn.rightClicked.connect(self._on_lock_right_clicked)
        
        # Trigger font size adjustment when the splitter divider is moved
        self.main_splitter.splitterMoved.connect(self._adjust_display_font_size)
        # ...and keep the header divider above the splitter handle
        self.main_splitter.splitterMoved.connect(lambda *_: self._sync_header_divider())
        
        # Table Signals
        self.plot_table.rowMoved.connect(self.update_plot)

    def _on_jump_to_step(self, val):
        if not self.controller.timesteps: return
        # Find closest step
        arr = np.array(self.controller.timesteps)
        idx = (np.abs(arr - val)).argmin()
        
        # Set in controller and player controls
        self.controller.set_timestep_index(idx)
        self.player_controls.set_step_index(idx)

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
        self._sync_header_divider()

    def showEvent(self, event):
        # The panel lives in a QStackedWidget, so it may never get a resize event
        # between construction and first display - sync the divider explicitly.
        super().showEvent(event)
        self._sync_header_divider()

    def _sync_header_divider(self):
        """Keeps the header's vertical divider above the splitter handle.

        The header and the splitter are siblings, so nothing links them by default.
        Pinning the right block's width to the table pane makes the divider track the
        handle as the user drags it.
        """
        if not hasattr(self, 'right_container') or not hasattr(self, 'main_splitter'):
            return

        sizes = self.main_splitter.sizes()
        if len(sizes) < 2 or sizes[0] <= 0:
            return

        margins = self.top_layout.contentsMargins()
        spacing = self.top_layout.spacing()
        line_w = self.header_divider.sizeHint().width()
        handle_w = self.main_splitter.handleWidth()

        # divider centre == handle centre  ->  solve for the right block's width
        width = (self.width() - margins.right() - spacing - line_w / 2.0
                 - sizes[0] - handle_w / 2.0)
        width = int(round(width))

        # Never shrink below what the controls actually need.
        min_w = self.right_container.sizeHint().width()
        self.right_container.setFixedWidth(max(width, min_w))

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

    def load_project(self, root_path, keywords, force_reload=False, keep_table=False, target_system=None):
        paths = LogParser.normalize_paths(root_path)
        if not force_reload and self.loaded_path == paths:
            return

        # A study key gains or loses its path prefix as chips come and go, so note
        # which folder the current selection points at before the keys are rebuilt.
        previous = (self.study_origins or {}).get(self.study_combo.currentText())

        studies, warnings, file_map, origins = LogParser.discover_multi(paths, keywords)
        self.study_origins = origins
        self._pending_study_key = (
            LogParser.qualify_study(origins, previous['path'], previous['study'])
            if previous else None)

        load_warns, _ = self.data_manager.load_project_data(studies, paths, keywords, file_map)
        warnings.extend(load_warns)

        self.loaded_path = paths
        # The data manager was just rebuilt, so the controller's cached timestep lists
        # are stale even if the study/system names happen to be identical.
        self._force_system_reload = True
        try:
            self._update_ui_state(project_loaded=True, keep_table=keep_table)
        finally:
            self._force_system_reload = False

        # The chip that owned the selected study was removed AND nothing took its
        # place: blank the canvas rather than keep showing a project that is no longer
        # loaded. The domains stay. Deliberately narrow - when the same study name is
        # still available from another path the selection survives and must not be cut.
        # The comparison is path-identity based (same spelling-insensitive chip
        # matching as the row tables), so a same-named study from another project
        # never passes for the removed one.
        if (previous and not LogParser.path_is_loaded(previous['path'], paths)
                and self.study_combo.currentText() == "Select Study"):
            self.controller.clear_scene()
            self.player_controls.set_timesteps([])

        # Handle Auto-Selection if target_system provided
        if target_system:
            # We assume target_system is in the "." study (Current Directory)
            # because the user selected a file in the 'root_path' passed to us.
            if "." in studies:
                self.study_combo.setCurrentText(".")
                # Trigger update to populate systems
                # Then select system
                index = self.system_combo.findText(target_system)
                if index != -1:
                    self.system_combo.setCurrentIndex(index)

    def on_path_cleared(self):
        """Clearing the path empties the canvas but keeps the configured domains.

        A domain is a configuration, not data - re-entering a path should bring the
        same rows straight back instead of forcing the user to define them again.
        """
        self.loaded_path = None
        self.controller.clear_scene()
        self.player_controls.set_timesteps([])
        self._update_ui_state(project_loaded=False, keep_table=True)

    def _update_ui_state(self, project_loaded, keep_table=False):
        if not keep_table:
            self.plot_table.setRowCount(0)
            # Clearing the rows also removes the optimal-line row; keep the tracked
            # index in sync or add_domain() would insert at an out-of-range position
            # and every following setCellWidget() would be a silent no-op.
            self.plot_table.opt_line_row = -1
            self.plot_widget.autoRange()

        if project_loaded:
            self.study_combo.setEnabled(True)
            self.system_combo.setEnabled(True)
            self.add_btn.setEnabled(True)

            try:
                from lmpvisualizer.log.log_parser import LogParser as _LP
                studies = sorted(list(self.data_manager.parsers.keys()),
                                 key=lambda k: (_LP._is_virtual_study_key(k), k))
            except Exception:
                studies = sorted(list(self.data_manager.parsers.keys()))
            # Preserve the current selection across a reload/refresh. Without this the
            # combo falls back to the "Select Study" placeholder, which cascades into an
            # empty system combo and leaves Add without atom types or axis bounds.
            # _pending_study_key follows the selection through a re-qualified key.
            self._populate_combo(self.study_combo, "Select Study", studies,
                                 self._pending_study_key or self.study_combo.currentText())
            self._style_study_combo(self.study_combo)
            self._pending_study_key = None

            # Force trigger because _populate_combo blocks signals
            self.on_study_changed(self.study_combo.currentText())
        else:
            self.add_btn.setEnabled(False)
            # No project loaded: the combos must not keep offering the studies and
            # systems of a project that is no longer there. _populate_combo blocks
            # signals, so this resets the labels without triggering a re-plot.
            self._populate_combo(self.study_combo, "Select Study", [])
            self._populate_combo(self.system_combo, "Select System", [])

    def _style_study_combo(self, combo):
        """Bold the virtual wildcard studies (Study/System pattern with *)."""
        try:
            model = combo.model()
            for i in range(combo.count()):
                text = combo.itemText(i)
                is_virtual = LogParser._is_virtual_study_key(text)
                font = QFont(combo.font())
                font.setBold(is_virtual)
                model.setData(model.index(i, 0), font, Qt.ItemDataRole.FontRole)
        except Exception:
            pass

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
        if placeholder == "Select Study":
            try:
                model = combo.model()
                for i in range(combo.count()):
                    text = combo.itemText(i)
                    is_virtual = LogParser._is_virtual_study_key(text)
                    font = QFont(combo.font())
                    font.setBold(is_virtual)
                    model.setData(model.index(i, 0), font, Qt.ItemDataRole.FontRole)
            except Exception:
                pass
        combo.blockSignals(False)
            
    def on_study_changed(self, text):
        self._repopulate_systems()

    def on_system_changed(self, text):
        # Update label of Strain standard deviation if average is selected
        if text == "Strain average":
            self.opt_actions['strain_std'].setText("Mean strain avg standard deviation")
        else:
            self.opt_actions['strain_std'].setText("Strain standard deviation")

        if text != "Select System":
            study = self.study_combo.currentText()
            
            # For "Strain average", we use the first system as reference for axes
            ref_system = text
            if text == "Strain average":
                ref_system = next(iter(self.data_manager.parsers[study].keys()))

            self.controller.set_active_system(study, text, force=self._force_system_reload)
            self.player_controls.set_timesteps(self.controller.get_available_timesteps())
            
            # Capture currently selected axes before repopulating
            current_slice = self.slice_axis_combo.currentText()
            current_obs = self.observe_axis_combo.currentText()
            current_z = self.zfilter_combo.currentText()

            df, _ = self.data_manager.load_frame(study, ref_system, self.controller.timesteps[0])
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
        obs_ax = self.observe_axis_combo.currentText()

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
        
        dlg = DSDAddDomainDialog(self, None, slice_ax, types, axis_bounds, obs_ax)
        if dlg.exec():
            data = dlg.get_data()
            self.plot_table.add_domain(data.get('name', "Domain " + str(self.plot_table.rowCount()+1)), data)
            self._sync_lock_visibility()
            self.update_plot()
            self.controller.auto_scale()

    def _on_auto_index_clicked(self):
        if not self.data_manager.parsers:
            QMessageBox.warning(self, "No project loaded", "Please load a project path first.")
            return
        
        # Pack the current environment for the Preload Tool
        config = {
            'hashes': [],
            'domains': [d for d in self.plot_table.get_domains() if not d.get('is_optimal_line')],
            'timesteps': self.controller.timesteps,
            'slice_axis': self.slice_axis_combo.currentText(),
            'observe_axis': self.observe_axis_combo.currentText(),
            'options': self.get_options()
        }
        
        # Calculate hashes using helper
        if config['timesteps'] and "Select" not in config['slice_axis'] and "Select" not in config['observe_axis']:
            config['hashes'] = self.data_manager.get_required_hashes(
                config['domains'], config['timesteps'], 
                config['slice_axis'], config['observe_axis'], config['options']
            )

        dlg = AutoIndexDialog(self, self.data_manager.parsers, dsd_config=config)
        dlg.exec()

    def get_options(self):
        opts = self.current_options.copy()
        if hasattr(self, 'zfilter_combo'):
            opts['z_filter_col'] = self.zfilter_combo.currentText()
            opts['z_filter_ref'] = self.zfilter_ref_combo.currentText()
        return opts

    def update_plot(self, *args):
        if self._loading_session:
            return
            
        domains = self.plot_table.get_domains()
        
        # --- Logic: Auto-restore Optimal Line if needed ---
        if self.current_options.get('opt_line'):
            if self.plot_table.opt_line_row == -1:
                real_domains = [d for d in domains if not d.get('is_optimal_line')]
                if real_domains:
                    self.plot_table.add_domain("End-to-end", {'color': 'black', 'style': '--', 'size': '1'}, is_optimal_line=True)
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
        if self._updating_from_code: return
        
        col = self.zfilter_combo.currentText()
        ref = self.zfilter_ref_combo.currentText()
        
        # Handle "Set step" trigger
        if ref == "Set step":
            curr_step = self.controller.current_timestep
            new_label = f"Step {curr_step}"
            
            self._updating_from_code = True
            # Remove any existing "Step ..." entry
            for i in range(self.zfilter_ref_combo.count()):
                if self.zfilter_ref_combo.itemText(i).startswith("Step "):
                    self.zfilter_ref_combo.removeItem(i)
                    break
            
            # Add the new step and select it
            current_count = self.zfilter_ref_combo.count()
            self.zfilter_ref_combo.insertItem(current_count - 1, new_label) # Insert before "Set step"
            self.zfilter_ref_combo.setCurrentText(new_label)
            self._updating_from_code = False
            ref = new_label

        study = self.study_combo.currentText()
        system = self.system_combo.currentText()

        if col != "No Z-Filter":
            self.filter_bar.setVisible(True)
            self.zfilter_ref_combo.setEnabled(True)
            
            # If we have a valid study/system, update the widget labels/range
            if study and system and "Select" not in study and "Select" not in system:
                 limits = self.controller.get_scope_min_max(study, system, col, ref)
                 if limits:
                     self.filter_bar.set_data_range(limits[0], limits[1])
        else:
            self.filter_bar.setVisible(False)
            self.zfilter_ref_combo.setEnabled(False)
            
        self.update_plot()

    def _on_frame_changed(self, idx):
        self.player_controls.set_step_index(idx)

    def _on_step_changed_by_user(self, step):
        self.controller.set_timestep_index(step)

    def _on_range_requested(self, min_val, max_val):
        new_timesteps = self.controller.set_timestep_range(min_val, max_val)
        if new_timesteps:
            self.player_controls.set_timesteps(new_timesteps)
            
            # Sync slider index to the controller's current timestep
            # (Indices shift if the start of the range changes)
            curr_ts = self.controller.current_timestep
            try:
                new_idx = new_timesteps.index(curr_ts)
                self.player_controls.set_step_index(new_idx)
            except ValueError:
                self.player_controls.set_step_index(0)
            
            # Refresh Filter Limits if active (Initial/Final refs might have changed)
            z_col = self.zfilter_combo.currentText()
            if z_col != "No Z-Filter":
                ref = self.zfilter_ref_combo.currentText()
                study = self.study_combo.currentText()
                system = self.system_combo.currentText()
                
                # Check study/system validity to avoid errors
                if study and system and "Select" not in study and "Select" not in system:
                    limits = self.controller.get_scope_min_max(study, system, z_col, ref)
                    if limits:
                        self.filter_bar.set_data_range(limits[0], limits[1])

            # Update plot to reflect new range (e.g. Initial Frame might change)
            self.update_plot()
        
    def _on_table_item_changed(self, item):
        if item.column() == 0: 
            self.update_plot()

    def _on_row_removed(self, row):
        # Note: DSDTableWidget handles opt_line_row adjustments and on_opt_line_deleted triggers internally.
        # We only need to check for auto-removal of the optimal line if it's the last thing standing.
        
        # Trigger cleanup or update
        real_domains = [d for d in self.plot_table.get_domains() if not d.get('is_optimal_line')]
        if not real_domains and self.plot_table.opt_line_row != -1:
            # Auto-remove the opt line row visually, but KEEP option True (so next add brings it back)
            # We must be careful not to trigger recursive deletion loops if we are not careful,
            # but removeRow triggers rowRemoved which calls this again.
            # However, opt_line_row will be updated by widget before we get here?
            # Actually, we should just remove it.
            self.plot_table.removeRow(self.plot_table.opt_line_row)
            # Widget will handle opt_line_row update to -1 via its internal logic or we force it?
            # removeRow triggers _delete_row in widget logic? No, removeRow is QTableWidget method.
            # We rely on widget's rowRemoved signal? 
            # DSDTableWidget overrides nothing relevant to removeRow call itself, 
            # but _delete_row CALLS removeRow.
            # Calling removeRow directly from here will NOT trigger _delete_row logic (which updates opt_line).
            # So we must manually update opt_line_row or call a safe delete method.
            
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
        path, _ = QFileDialog.getSaveFileName(
            self, "Save DSD Session", self.main_window.get_session_dialog_dir(), "JSON Files (*.json)")
        if not path: return
        self.main_window.remember_dialog_dir("session", path)
        if self.save_session_to_file(path, trigger="manual save"):
            self.main_window.set_loaded_session(path)

    def dominant_project_path(self):
        """DSD has one global selection instead of per-row studies."""
        try:
            origin = (self.study_origins or {}).get(self.study_combo.currentText(), {})
            return origin.get('path') or ""
        except Exception:
            return ""

    def load_session(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load DSD Session", self.main_window.get_session_dialog_dir(), "JSON Files (*.json)")
        if not path: return
        self.main_window.remember_dialog_dir("session", path)
        self.load_session_from_file(path)
        
    def save_session_to_file(self, path, trigger=None):
        # trigger accepted for funnel uniformity; dsd stays silent on save.
        project_paths = self.main_window.get_project_paths()
        if not project_paths: return False
        
        # Calculate percentages for robust restoration (Hybrid Mode)
        final_pct = 1.0
        current_pct = 0.0
        
        if self.controller and self.controller.full_timesteps and self.controller.timesteps:
            try:
                # Percentage of remaining trajectory used
                full_arr = np.array(self.controller.full_timesteps)
                start_val = self.controller.timesteps[0]
                end_val = self.controller.timesteps[-1]
                
                start_idx = (np.abs(full_arr - start_val)).argmin()
                end_idx = (np.abs(full_arr - end_val)).argmin()
                
                total_remaining = len(self.controller.full_timesteps) - 1 - start_idx
                if total_remaining > 0:
                    final_pct = (end_idx - start_idx) / total_remaining
            except: pass
            
        if self.controller and len(self.controller.timesteps) > 1:
             curr_idx = self.player_controls.slider.value()
             current_pct = curr_idx / (len(self.controller.timesteps) - 1)

        study_origin = (self.study_origins or {}).get(self.study_combo.currentText(), {})
        session_data = {
            'type': 'dsd',
            # 'project_path' stays for older builds; 'project_paths' is authoritative.
            'project_path': project_paths[0],
            'project_paths': project_paths,
            'keywords': self.main_window.chip_input.get_chips(),
            'domains': self.plot_table.get_domains(),
            'opt_line_row': self.plot_table.opt_line_row,
            'view_locked': self.lock_axes_btn.isChecked(),
            'view_limits': self.controller.view_limits,
            'global_options': {
                'study': self.study_combo.currentText(),
                # Stable identity of the selection, independent of prefixing.
                'study_path': study_origin.get('path'),
                'study_raw': study_origin.get('study'),
                'system': self.system_combo.currentText(),
                'slice_axis': self.slice_axis_combo.currentText(),
                'observe_axis': self.observe_axis_combo.currentText(),
                'plot_type': self.plot_type_combo.currentText(),
                'z_filter_col': self.zfilter_combo.currentText(),
                'z_filter_ref': self.zfilter_ref_combo.currentText(),
                'z_ranges': self.filter_bar.current_ranges if self.filter_bar.isVisible() else None,
                'options': self.get_options(),
                'initial_step': self.controller.timesteps[0] if self.controller.timesteps else None,
                'final_step': self.controller.timesteps[-1] if self.controller.timesteps else None,
                'final_step_pct': final_pct,
                'current_step_index': self.player_controls.slider.value(),
                'current_step_pct': current_pct,
                'last_target_strain': getattr(self.controller, 'last_target_strain', None)
            },
            'popout_presets': copy.deepcopy(getattr(self, 'popout_presets', []) or [])
        }
        
        return SettingsManager.save_state(path, session_data)

    def load_session_from_file(self, path):
        if not path or not os.path.exists(path): return
        
        data = SettingsManager.load_state(path)
        if not data:
            logger.warning("Failed to load DSD session file: %s", path)
            QMessageBox.critical(self, "Error", "Failed to load DSD session file.")
            return
            
        # --- Mismatch Check ---
        file_type = data.get('type')
        if file_type not in ['dsd', 'dsd_plot']:
            # Check if it is a known OTHER type
            is_trj = file_type in ['trj', 'trj_plot']
            is_log = file_type in ['log', 'log_plot']
            
            if is_trj or is_log:
                # It IS a known other mode -> Offer Switch
                target_mode = "Trajectory Plot" if is_trj else "Log Plot"
                target_idx = self.main_window.MODE_TRJ if is_trj else self.main_window.MODE_LOG
                target_panel = self.main_window.trj_plot_panel if is_trj else self.main_window.log_plot_panel

                msg = QMessageBox(self.main_window)
                msg.setWindowTitle("Mode Mismatch")
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
            else:
                # Unknown Type -> Error only, NO Switch
                logger.warning("Invalid session file '%s' with unknown type: '%s'", path, file_type)
                QMessageBox.warning(self.main_window, "Invalid Session File", 
                                    f"The file has an unknown or invalid type: '{file_type}'.\n"
                                    "Cannot load this session.")
                return
        
        logger.info("[System] Loading session: %s for mode DSD Mode", path)

        self.main_window.path_input.blockSignals(True)
        self.main_window.chip_input.blockSignals(True)
        self._loading_session = True

        try:
            try:
                self.popout_presets = self._sanitize_popout_presets(data.get('popout_presets', []))
            except Exception:
                self.popout_presets = []
            try:
                self._refresh_popout_button()
            except Exception:
                pass
            # 1. Load Project Data
            project_paths = self.main_window.session_paths(data)
            keywords = data.get('keywords', [])

            project_paths, relocated, action = self.main_window.resolve_session_paths(
                self, project_paths)
            if action in ('cancel', 'reset', 'change'):
                return

            self.main_window.set_project_paths(project_paths)
            self.main_window.chip_input.set_chips(keywords)
            self.load_project(project_paths, keywords, force_reload=True, keep_table=True)
            
            # If relocated and project was found, auto-update the session file
            if relocated and self.data_manager.parsers:
                if self.save_session_to_file(path):
                    logger.info("[System] Relocation successful. Session file updated: %s", path)
            
            # 2. Establish Base Configuration
            g_opts = data.get('global_options', {})
            
            # Restore Plot Type FIRST to unlock "Strain average" in the systems dropdown
            self.plot_type_combo.setCurrentText(g_opts.get('plot_type', 'Displacement plot'))
            
            self.study_combo.blockSignals(True)
            self.system_combo.blockSignals(True)
            
            # The session stores the study key it saw; with several paths loaded that
            # key may now be prefixed, so prefer the stable (path, folder) pair.
            saved_study = g_opts.get('study', 'Select Study')
            if g_opts.get('study_path'):
                saved_study = LogParser.repoint_study_key(
                    self.study_origins, g_opts['study_path'],
                    g_opts.get('study_raw', saved_study)) or saved_study

            self.study_combo.setCurrentText(saved_study)
            self.on_study_changed(self.study_combo.currentText())
            
            # Now "Strain average" is guaranteed to be in the list if the study has > 1 system
            self.system_combo.setCurrentText(g_opts.get('system', 'Select System'))
            self.on_system_changed(self.system_combo.currentText())
            
            self.study_combo.blockSignals(False)
            self.system_combo.blockSignals(False)
            
            # --- Restore Range (Hybrid Logic) ---
            init_s = g_opts.get('initial_step')
            final_s = g_opts.get('final_step')
            final_pct = g_opts.get('final_step_pct')

            if init_s is not None:
                # If we have a percentage and full timesteps, use the Hybrid calculation
                if final_pct is not None and self.controller.full_timesteps:
                     try:
                         full_arr = np.array(self.controller.full_timesteps)
                         start_idx = (np.abs(full_arr - init_s)).argmin()
                         total_remaining = len(self.controller.full_timesteps) - 1 - start_idx
                         
                         if total_remaining > 0:
                             window_len = int(total_remaining * final_pct)
                             end_idx = min(start_idx + window_len, len(self.controller.full_timesteps) - 1)
                             final_s = self.controller.full_timesteps[end_idx]
                     except: pass
                
                # Apply range (using either the calculated final_s or the absolute fallback)
                if final_s is not None:
                    new_steps = self.controller.set_timestep_range(init_s, final_s)
                    if new_steps:
                        self.player_controls.set_timesteps(new_steps)

            # --- Restore Time Step (Percentage of Window) ---
            step_idx = g_opts.get('current_step_index', 0)
            curr_pct = g_opts.get('current_step_pct')
            
            if curr_pct is not None and self.controller.timesteps:
                step_idx = int(curr_pct * (len(self.controller.timesteps) - 1))
            
            self.controller.set_timestep_index(step_idx)
            self.player_controls.set_step_index(step_idx)
            
            # 3. Establish Axis Selections
            self.slice_axis_combo.setCurrentText(g_opts.get('slice_axis', 'Select Axis'))
            self.observe_axis_combo.setCurrentText(g_opts.get('observe_axis', 'Select Axis'))

            # 4. Configure Filter (Now that axes are ready)
            z_col = g_opts.get('z_filter_col', 'No Z-Filter')
            z_ref = g_opts.get('z_filter_ref', 'Initial') 
            
            # Block signals temporarily to prevent multiple intermediate redraws
            self.zfilter_combo.blockSignals(True)
            self.zfilter_ref_combo.blockSignals(True)
            
            # Restore custom "Step ..." entry if needed
            if z_ref.startswith("Step "):
                # Remove any existing step first
                for i in range(self.zfilter_ref_combo.count()):
                    if self.zfilter_ref_combo.itemText(i).startswith("Step "):
                        self.zfilter_ref_combo.removeItem(i)
                        break
                # Insert before "Set step"
                self.zfilter_ref_combo.insertItem(self.zfilter_ref_combo.count() - 1, z_ref)

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
            self._loading_session = False
            self.update_plot()
        finally:
            self._loading_session = False
            self.main_window.path_input.blockSignals(False)
            self.main_window.chip_input.blockSignals(False)

        try:
            self.main_window.set_loaded_session(path)
        except Exception:
            pass

    def launch_popout(self):
        self._open_fresh_popout()

    def _on_popout_button_clicked(self):
        if getattr(self, 'popout_presets', None):
            try:
                self.popout_btn.showMenu()
            except Exception:
                pass
            return
        self._open_fresh_popout()

    def _sanitize_popout_presets(self, raw):
        out = []
        try:
            if not isinstance(raw, list):
                return []
            for p in raw:
                if not isinstance(p, dict):
                    continue
                name = p.get('name')
                if not isinstance(name, str) or not name:
                    continue
                if not isinstance(p.get('global'), dict) or not isinstance(p.get('series'), dict):
                    continue
                try:
                    out.append(copy.deepcopy(p))
                except Exception:
                    out.append({'name': name, 'global': dict(p.get('global')),
                                'series': dict(p.get('series'))})
        except Exception:
            return []
        return out

    def _write_popout_autosave(self, trigger=None):
        try:
            self.main_window.save_session_for_mode(self.main_window.MODE_DSD, trigger=trigger)
        except Exception as e:
            logger.warning("[System] Popout preset autosave failed: %s", e)

    def _refresh_popout_button(self):
        presets = getattr(self, 'popout_presets', []) or []
        if not presets:
            try:
                self.popout_btn.setMenu(None)
            except Exception:
                pass
            try:
                self.popout_btn.setText("Pop Out")
            except Exception:
                pass
            return
        try:
            self.popout_btn.setText("Pop Out")
        except Exception:
            pass
        try:
            menu = QMenu(self.popout_btn)
            self._fill_popout_menu(menu)
            self.popout_btn.setMenu(menu)
        except Exception as e:
            logger.warning("[System] Popout menu rebuild failed: %s", e)

    def _fill_popout_menu(self, menu):
        """(Re)build the button menu rows into an existing menu (stays open)."""
        try:
            menu.clear()
        except Exception:
            pass
        try:
            presets = getattr(self, 'popout_presets', []) or []
            new_act = menu.addAction("New Pop Out")
            new_act.triggered.connect(lambda _c=False: self._open_fresh_popout())
            menu.addSeparator()
            for preset in list(presets):
                try:
                    name = preset.get('name', 'preset')
                except Exception:
                    continue
                wa = QWidgetAction(menu)
                row = QWidget()
                hl = QHBoxLayout(row)
                hl.setContentsMargins(4, 2, 4, 2)
                hl.setSpacing(6)
                open_btn = QPushButton(name)
                open_btn.setFlat(True)
                open_btn.setStyleSheet("text-align: left;")
                open_btn.setMinimumWidth(160)
                trash = QToolButton()
                trash.setText("\U0001f5d1")
                trash.setToolTip("Delete preset")
                trash.setAutoRaise(True)
                open_btn.clicked.connect(
                    lambda _c=False, n=name, m=menu: (m.close(), self._open_preset_popout(n)))
                # No menu close here: the menu rebuilds in place so several
                # entries can be deleted in a row; clicking away still closes.
                trash.clicked.connect(
                    lambda _c=False, n=name, m=menu: self._delete_popout_preset(n, m))
                hl.addWidget(open_btn, 1)
                hl.addWidget(trash)
                wa.setDefaultWidget(row)
                menu.addAction(wa)
        except Exception as e:
            logger.warning("[System] Popout menu rebuild failed: %s", e)

    def _delete_popout_preset(self, name, menu=None):
        try:
            self.popout_presets = [p for p in (getattr(self, 'popout_presets', []) or [])
                                   if p.get('name') != name]
        except Exception:
            self.popout_presets = []
        try:
            if menu is not None and (getattr(self, 'popout_presets', []) or []):
                self._fill_popout_menu(menu)
            else:
                if menu is not None:
                    try:
                        menu.close()
                    except Exception:
                        pass
                self._refresh_popout_button()
        except Exception:
            pass
        self._write_popout_autosave("preset deleted")

    def _on_popout_closed(self, window, preset, trigger=None):
        try:
            src = getattr(window, 'source_name', None)
            existing = [p.get('name') for p in (getattr(self, 'popout_presets', []) or [])]
            if src:
                replaced = False
                for i, p in enumerate(self.popout_presets):
                    if p.get('name') == src:
                        try:
                            preset['name'] = src
                        except Exception:
                            pass
                        self.popout_presets[i] = copy.deepcopy(preset)
                        replaced = True
                        break
                if not replaced:
                    try:
                        preset['name'] = mint_preset_name(existing)
                    except Exception:
                        pass
                    self.popout_presets.append(copy.deepcopy(preset))
            else:
                try:
                    preset['name'] = mint_preset_name(existing)
                except Exception:
                    pass
                self.popout_presets.append(copy.deepcopy(preset))
        except Exception as e:
            logger.warning("[System] Popout preset save failed: %s", e)
            return
        try:
            self._refresh_popout_button()
        except Exception:
            pass
        self._write_popout_autosave(trigger or "popout closed")

    def _track_popout(self, win):
        if not hasattr(self.main_window, 'dsd_popouts'):
            self.main_window.dsd_popouts = []
        self.main_window.dsd_popouts.append(win)
        win.destroyed.connect(lambda: self.main_window.dsd_popouts.remove(win) if win in self.main_window.dsd_popouts else None)

    def _open_fresh_popout(self):
        state = self.controller.get_current_plot_state()
        if not state or not state.get('y_axes'):
            QMessageBox.information(self, "Info", "No valid data to pop out.")
            return

        dpi = self.logicalDpiX()
        w_in = self.plot_widget.width() / dpi
        h_in = self.plot_widget.height() / dpi

        win = PopOutWindow(state, self, figsize=(w_in, h_in),
                           source_name=None, on_close=self._on_popout_closed)
        win.show()
        # Keep reference
        self._track_popout(win)

    def _open_preset_popout(self, name):
        try:
            preset = next((p for p in (getattr(self, 'popout_presets', []) or [])
                           if p.get('name') == name), None)
        except Exception:
            preset = None
        if preset is None:
            return
        try:
            state = self.controller.get_current_plot_state()
        except Exception:
            return
        if not state or not state.get('y_axes'):
            QMessageBox.information(self, "Info", "No valid data to pop out.")
            return
        try:
            dpi = self.logicalDpiX()
            w_in = self.plot_widget.width() / dpi
            h_in = self.plot_widget.height() / dpi
        except Exception:
            w_in, h_in = 11.0, 6.5
        win = PopOutWindow(state, self, figsize=(w_in, h_in),
                           source_name=name, on_close=self._on_popout_closed)
        try:
            win.apply_all(copy.deepcopy(preset))
        except Exception as e:
            logger.warning("[System] Popout preset apply failed: %s", e)
        win.show()
        self._track_popout(win)

    def export_image(self):
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
            "Raw Pixel Data (*.raw *.rgba)"
        )
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Image", self.main_window.get_last_dialog_dir("export_image"), filters)
        
        if path:
            self.main_window.remember_dialog_dir("export_image", path)
            dpi = self.logicalDpiX()
            width_in = self.plot_widget.width() / dpi
            height_in = self.plot_widget.height() / dpi
            self.controller.export_plot(path, figsize=(width_in, height_in))

    def export_raw_data(self):
        filters = (
            "CSV Data (*.csv);;"
            "TSV Data (*.tsv)"
        )
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Raw Data", self.main_window.get_last_dialog_dir("export_data"), filters)
        
        if path:
            self.main_window.remember_dialog_dir("export_data", path)
            dpi = self.logicalDpiX()
            width_in = self.plot_widget.width() / dpi
            height_in = self.plot_widget.height() / dpi
            self.controller.export_plot(path, figsize=(width_in, height_in))

    def export_video(self):
        from lmpvisualizer.shared.video_export_dialog import VideoExportDialog
        dlg = VideoExportDialog(self)
        dlg.exec()
