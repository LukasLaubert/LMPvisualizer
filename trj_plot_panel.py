# lmp_visualizer/trj_plot_panel.py

import os
import copy
import random
from pathlib import Path
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGridLayout, 
    QComboBox, QPushButton, QTableWidget, QHeaderView, QTableWidgetItem,
    QSizePolicy, QCheckBox, QSplitter, QFrame, QMenu, QLineEdit,
    QMessageBox, QFileDialog, QAbstractItemView, QWidgetAction, QToolButton
)
from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtGui import QColor, QIntValidator, QFont
import pyqtgraph as pg
import numpy as np

from trj_data_manager import TrjDataManager
from trj_controller import TrjController
from trj_widgets import FilterBarWidget, HeatmapBarWidget, PlayerControlWidget
from ui_components import ColorButton, NoNewLineDelegate, RightClickButton, MissingPathResolver
from settings_manager import SettingsManager
from log_parser import LogParser
from global_label_editor_dialog import GlobalLabelEditorDialog
from auto_index_dialog import AutoIndexDialog
from logger_setup import get_logger

logger = get_logger(__name__)


def _system_pattern_tokenize(name: str):
    """Collapse each maximal digit run into a single None token."""
    tokens = []
    i = 0
    n = len(name)
    while i < n:
        ch = name[i]
        if ch.isdigit():
            tokens.append(None)
            i += 1
            while i < n and name[i].isdigit():
                i += 1
        else:
            tokens.append(ch)
            i += 1
    return tokens


def _find_pattern_matched_system(prev: str, candidates: list[str]):
    """First candidate with identical non-digit chars, digit groups counted as one."""
    if not prev or prev == "Select System":
        return None
    prev_toks = _system_pattern_tokenize(prev)
    for cand in candidates:
        if cand == "Select System":
            continue
        if _system_pattern_tokenize(cand) == prev_toks:
            return cand
    return None


class TrjPlotPanel(QWidget):
    def __init__(self, main_window_ref):
        super().__init__()
        self.main_window = main_window_ref
        
        # Backend & Logic
        self.data_manager = TrjDataManager()
        self.controller = None 
        self.loaded_path = None
        # study key -> {'path': <project path>, 'study': <raw folder name>}
        self.study_origins = {}
        self._pending_study_key = None
        # Armed when the loaded project set changed under the current selection, so the
        # next study/system binding widens the step window instead of conserving it.
        self._reset_range_on_next_bind = False
        self.global_label_map = {}
        self._updating_from_code = False
        # Set while load_project() rebuilds the data manager, so on_system_changed()
        # forces the controller to re-derive its timestep lists and reset the range.
        self._force_system_reload = False
        # Explicit system sequence memory (real systems only, 0-based), per-mode global.
        self._explicit_system_seq = None
        self._prev_study = None
        self._suppress_seq_update = False

        # Track last selected row to save state before switching
        self.last_selected_row = -1
        self.popout_presets = []
        self.popout_windows = []
        
        # UI Setup
        self._init_ui()
        self.controller = TrjController(self.plot_widget, self.data_manager)
        self._connect_signals()
        
        self._update_ui_state(project_loaded=False)
        try:
            self._refresh_popout_button()
        except Exception:
            pass

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        
        # --- Top Controls Layout ---
        top_container = QWidget()
        top_layout = QHBoxLayout(top_container)
        top_layout.setContentsMargins(5, 0, 5, 5)
        top_layout.setSpacing(10)

        # 1. Mode Combo Placeholder
        self.mode_combo_placeholder = QWidget()
        self.mode_combo_placeholder.setFixedWidth(75)
        top_layout.addWidget(self.mode_combo_placeholder)

        # 2. Left Controls
        self.left_grid = QGridLayout()
        self.left_grid.setContentsMargins(0, 0, 0, 0)
        self.left_grid.setHorizontalSpacing(10)
        self.left_grid.setVerticalSpacing(5)

        self.left_grid.addWidget(QLabel("Study"), 0, 0)
        self.study_combo = self._create_combo("Select Study")
        self.left_grid.addWidget(self.study_combo, 0, 1)

        self.left_grid.addWidget(QLabel("System"), 0, 2)
        self.system_combo = self._create_combo("Select System")
        self.left_grid.addWidget(self.system_combo, 0, 3)

        self.left_grid.addWidget(QLabel("X-Axis"), 1, 0)
        self.xaxis_combo = self._create_combo("Select X-Axis")
        self.left_grid.addWidget(self.xaxis_combo, 1, 1)

        self.left_grid.addWidget(QLabel("Y-Axis"), 1, 2)
        self.yaxis_combo = self._create_combo("Select Y-Axis")
        self.left_grid.addWidget(self.yaxis_combo, 1, 3)
        
        self.left_grid.setColumnStretch(1, 1)
        self.left_grid.setColumnStretch(3, 1)

        # Stretch left side to take priority (matches dsd)
        top_layout.addLayout(self.left_grid, stretch=5)

        # 3. Add / Auto Preload - placed LEFT of the divider so they belong to the
        #    plot-side block, and so the divider can line up with the splitter handle.
        self.add_btn = QPushButton("Add")
        self.add_btn.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
        self.add_btn.setMinimumWidth(60)
        # clicked is wired in _connect_signals(); connecting it here as well made every
        # click add two rows.
        top_layout.addWidget(self.add_btn)

        self.idx_btn = QPushButton("Auto\nPreload")
        self.idx_btn.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
        self.idx_btn.setMinimumWidth(60)
        self.idx_btn.clicked.connect(self._on_auto_index_clicked)
        top_layout.addWidget(self.idx_btn)

        # 4. Visual Divider
        self.header_divider = QFrame()
        self.header_divider.setFrameShape(QFrame.Shape.VLine)
        self.header_divider.setFrameShadow(QFrame.Shadow.Sunken)
        self.header_divider.setLineWidth(1)
        self.header_divider.setStyleSheet("background-color: #888; margin-top: 5px; margin-bottom: 5px;")
        top_layout.addWidget(self.header_divider)

        # 5. Right Controls - inside a container so its width can be pinned to the
        #    table pane, which keeps the divider above the splitter handle.
        self.right_container = QWidget()
        self.right_grid = QGridLayout(self.right_container)
        self.right_grid.setContentsMargins(0, 0, 0, 0)
        self.right_grid.setHorizontalSpacing(10)
        self.right_grid.setVerticalSpacing(5)

        # Column Widths (kept identical to dsd so both headers line up)
        middle_min_width = 180   # For Z-Filter and Heatmap
        far_right_fixed_width = 150  # For the two Ref combos

        self.right_grid.addWidget(QLabel("Z-Filter"), 0, 0)
        self.zfilter_combo = self._create_combo("No Z-Filter")
        self.zfilter_combo.setMinimumWidth(middle_min_width)
        self.right_grid.addWidget(self.zfilter_combo, 0, 1)

        ref_options = ["Initial", "Final"]
        # self.zfilter_ref_combo = self._create_combo("Current", ref_options) # Replaced for order control
        
        self.zfilter_ref_combo = QComboBox()
        self.zfilter_ref_combo.setItemDelegate(NoNewLineDelegate(self.zfilter_ref_combo))
        self.zfilter_ref_combo.addItems(["Initial", "Current", "Final", "Set step"])
        self.zfilter_ref_combo.setCurrentText("Current")
        self.zfilter_ref_combo.setFixedWidth(far_right_fixed_width)
        self.zfilter_ref_combo.setEnabled(False)
        self.right_grid.addWidget(self.zfilter_ref_combo, 0, 2)

        self.right_grid.addWidget(QLabel("Heatmap"), 1, 0)
        self.heatmap_combo = self._create_combo("No Heatmap")
        self.heatmap_combo.setMinimumWidth(middle_min_width)
        self.right_grid.addWidget(self.heatmap_combo, 1, 1)

        self.heatmap_ref_combo = QComboBox()
        self.heatmap_ref_combo.setItemDelegate(NoNewLineDelegate(self.heatmap_ref_combo))
        self.heatmap_ref_combo.addItems(["Initial", "Current", "Final", "Set step"])
        self.heatmap_ref_combo.setCurrentText("Current")
        self.heatmap_ref_combo.setFixedWidth(far_right_fixed_width)
        self.heatmap_ref_combo.setEnabled(False)
        self.right_grid.addWidget(self.heatmap_ref_combo, 1, 2)

        # Right side takes only what it needs (stretch=0), matching dsd: the columns
        # are pinned, so all extra window width goes to the left block instead of
        # widening these combos.
        top_layout.addWidget(self.right_container, stretch=0)

        self.top_layout = top_layout
        main_layout.addWidget(top_container)

        # --- Main Splitter ---
        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter = main_splitter

        # Canvas
        canvas_container = QWidget()
        canvas_layout = QVBoxLayout(canvas_container)
        canvas_layout.setContentsMargins(0,0,0,0)
        canvas_layout.setSpacing(0)
        
        # Viz Area
        viz_area = QWidget()
        viz_layout = QHBoxLayout(viz_area)
        viz_layout.setContentsMargins(0,0,0,0)
        viz_layout.setSpacing(0)
        
        self.filter_bar = FilterBarWidget()
        self.filter_bar.setVisible(False)
        viz_layout.addWidget(self.filter_bar)
        
        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setBackground('w')
        # Set axis colors to black
        for axis in ['left', 'bottom', 'right', 'top']:
            self.plot_widget.getPlotItem().getAxis(axis).setPen('k')
            self.plot_widget.getPlotItem().getAxis(axis).setTextPen('k')
            
        self.plot_widget.setDownsampling(auto=True, ds=1, mode='subsample') 
        self.plot_widget.setClipToView(True)
        
        # Lock Button Overlay
        self.lock_axes_btn = RightClickButton()
        self.lock_axes_btn.setCheckable(True)
        self.lock_axes_btn.setText("🔓")
        self.lock_axes_btn.setToolTip("Left Click: Lock/Unlock View Limits\nRight Click: Toggle Sync Aspect Ratio")
        self.lock_axes_btn.setParent(self.plot_widget)
        self.lock_axes_btn.hide()
        
        original_resize = self.plot_widget.resizeEvent
        def custom_resize_event(event):
            self.lock_axes_btn.setGeometry(self.plot_widget.width() - 40, 0, 40, 30)
            if original_resize:
                original_resize(event)
        self.plot_widget.resizeEvent = custom_resize_event
        
        viz_layout.addWidget(self.plot_widget, 1)
        
        self.heatmap_bar = HeatmapBarWidget()
        self.heatmap_bar.setVisible(False)
        viz_layout.addWidget(self.heatmap_bar)
        
        canvas_layout.addWidget(viz_area, 1)
        
        self.player_controls = PlayerControlWidget()
        canvas_layout.addWidget(self.player_controls)
        
        main_splitter.addWidget(canvas_container)
        
        # --- Table ---
        table_container = QWidget()
        table_layout = QVBoxLayout(table_container)
        table_layout.setContentsMargins(0,0,0,0)
        
        self.plot_table = QTableWidget()
        self.plot_table.setColumnCount(6)
        self.plot_table.setHorizontalHeaderLabels(["↕", "Plot", "Color", "Style", "Size", "Del"])
        
        header = self.plot_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        
        self.plot_table.setColumnWidth(0, 25)  # Arrows
        self.plot_table.setColumnWidth(2, 50)  # Color
        self.plot_table.setColumnWidth(3, 36)  # Style
        self.plot_table.setColumnWidth(4, 28)  # Size
        self.plot_table.setColumnWidth(5, 30)  # Del
        
        self.plot_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.plot_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.plot_table.verticalHeader().hide()
        
        self.plot_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.plot_table.customContextMenuRequested.connect(self.open_label_context_menu)

        table_layout.addWidget(self.plot_table)
        
        # Bottom Buttons
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
        
        btns_layout = QHBoxLayout()
        btns_layout.addWidget(self.popout_btn)
        btns_layout.addWidget(self.export_btn)
        btns_layout.addWidget(self.save_btn)
        btns_layout.addWidget(self.load_btn)
        btns_layout.addWidget(self.exit_btn)
        table_layout.addLayout(btns_layout)
        
        main_splitter.addWidget(table_container)
        main_splitter.setSizes([900, 300])
        
        main_layout.addWidget(main_splitter, 1)

    # --- Header divider alignment ---

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

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_header_divider()

    def showEvent(self, event):
        # The panel lives in a QStackedWidget, so it may never get a resize event
        # between construction and first display - sync the divider explicitly.
        super().showEvent(event)
        self._sync_header_divider()

    # --- Helpers & Logic ---

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
        
        self.xaxis_combo.currentTextChanged.connect(self.on_axis_changed)
        self.yaxis_combo.currentTextChanged.connect(self.on_axis_changed)
        
        self.zfilter_combo.currentTextChanged.connect(self.sync_dropdowns_to_row)
        self.zfilter_ref_combo.currentTextChanged.connect(self.sync_dropdowns_to_row)
        self.heatmap_combo.currentTextChanged.connect(self.sync_dropdowns_to_row)
        self.heatmap_ref_combo.currentTextChanged.connect(self.sync_dropdowns_to_row)

        self.add_btn.clicked.connect(self.add_new_row)
        
        self.plot_table.itemSelectionChanged.connect(self.on_table_selection_changed)
        self.plot_table.cellDoubleClicked.connect(self.on_cell_double_clicked)
        
        self.controller.frameChanged.connect(self._on_frame_changed)
        self.controller.boundsChanged.connect(self._on_controller_bounds_changed)
        self.controller.playbackStopped.connect(lambda: self.player_controls.btn_play.setChecked(False))
        
        self.player_controls.stepChanged.connect(self._on_step_changed_by_user)
        self.player_controls.playToggled.connect(lambda p: self.controller.play() if p else self.controller.pause())
        self.player_controls.fpsChanged.connect(self.controller.set_fps)
        self.player_controls.autoReplayToggled.connect(self.controller.set_auto_replay)
        self.player_controls.rangeRequested.connect(self._on_range_requested)
        self.player_controls.jumpToStepRequested.connect(self._on_jump_to_step)
        
        # Updated connection
        self.filter_bar.rangesChanged.connect(self._on_filter_bar_changed)

        self.heatmap_bar.boundsEdited.connect(self._on_heatmap_bounds_edited)
        self.heatmap_bar.boundsReset.connect(self._on_heatmap_bounds_reset)


        self.popout_btn.clicked.connect(self._on_popout_button_clicked)
        # self.export_btn click is handled by its dropdown menu
        self.save_btn.clicked.connect(self.save_session)
        self.load_btn.clicked.connect(self.load_session)
        self.exit_btn.clicked.connect(self.main_window.close)
        
        self.lock_axes_btn.toggled.connect(self._on_view_lock_toggled)
        self.lock_axes_btn.rightClicked.connect(self._on_view_sync_toggled)

        # pyqtgraph's own auto-range button re-arms the ViewBox's auto-range flag, which
        # silently defeats our lock (the view keeps refitting on every redraw). PlotItem
        # connected its handler first, so ours runs after the fit has been applied.
        try:
            self.plot_widget.getPlotItem().autoBtn.clicked.connect(self._on_auto_range_clicked)
        except Exception as e:
            logger.warning("[System] Could not hook the auto-range button: %s", e)

        # Keep the header divider above the splitter handle while it is dragged
        self.main_splitter.splitterMoved.connect(lambda *_: self._sync_header_divider())

    def _on_jump_to_step(self, val):
        if not self.controller.timesteps: return
        arr = np.array(self.controller.timesteps)
        idx = (np.abs(arr - val)).argmin()
        
        self.controller.set_timestep_index(idx)
        self.player_controls.set_step_index(idx)
        self._save_step_to_current_row(idx)

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
        warnings_load, _ = self.data_manager.load_project_data(studies, paths, keywords, file_map)
        warnings.extend(warnings_load)

        self.loaded_path = paths
        # Rows remember (path, raw study); re-point them at the study keys this load
        # produced and drop the ones whose path chip is gone.
        self._resync_rows_to_paths(paths)
        # The data manager was just rebuilt, so the controller's cached timestep lists
        # are stale even if the study/system names happen to be identical, and the step
        # window must widen back to the new trajectory's full extent.
        self._force_system_reload = True
        try:
            self._update_ui_state(project_loaded=True, keep_table=keep_table)
        finally:
            self._force_system_reload = False

        if previous and not LogParser.path_is_loaded(previous['path'], paths):
            # The project the selection belonged to is gone. A different project can
            # carry the very same study/system folder names, so drop the controller's
            # identity - otherwise set_active_system() would short-circuit on the
            # matching names and keep serving the old trajectory's timesteps.
            self.controller.current_study = None
            self.controller.current_system = None
            # Conserving the old project's step window across a project swap is
            # meaningless, but the user may only pick the study later - so arm the
            # reset for whenever that binding happens.
            self._reset_range_on_next_bind = True

            # Nothing took the selection's place: blank the scene rather than keep
            # showing a project that is no longer loaded. Deliberately narrow, so a
            # plain reload cannot disturb a restored step range.
            if self.study_combo.currentText() == "Select Study":
                self.player_controls.set_timesteps([])
                self.controller.update_view_config({'active': False})

        if target_system:
            if "." in studies:
                self.study_combo.setCurrentText(".")
                # on_study_changed triggers system populating
                index = self.system_combo.findText(target_system)
                if index != -1:
                    self.system_combo.setCurrentIndex(index)

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

    def _study_origin_fields(self, study_key):
        """The (path, raw study) a study key came from, for storing in a row."""
        origin = (getattr(self, 'study_origins', None) or {}).get(study_key)
        if not origin:
            return {}
        return {'source_path': origin['path'], 'study_raw': origin['study']}

    def _resync_rows_to_paths(self, paths):
        """Re-points rows at the current study keys and drops orphaned ones.

        Stable identity is (canonical source path, raw study folder) from the
        row's state dict - the stored study key is not stable, it gains or
        loses its path prefix as chips come and go. Rows are updated in place
        here (setData/setText); only rows whose chip is gone are removed, and
        only then, by _drop_rows_of_unloaded_paths below. Single-path rows
        without a stored path adopt the origin of their study key, so old
        sessions load unchanged. Virtual wildcard studies (containing *) are
        kept as-is if still present.
        """
        origins = getattr(self, 'study_origins', None) or {}

        for row in range(self.plot_table.rowCount()):
            item = self.plot_table.item(row, 1)
            state = item.data(Qt.ItemDataRole.UserRole) if item else None
            if not state:
                continue

            study = state.get('study', '')
            if LogParser._is_virtual_study_key(study):
                if study not in self.data_manager.parsers:
                    continue
                continue
            source = state.get('source_path')
            if source is None:
                # Row from a single-path session: adopt the origin of its study key.
                origin = origins.get(state.get('study'))
                if origin:
                    state['source_path'] = origin['path']
                    state['study_raw'] = origin['study']
                    item.setData(Qt.ItemDataRole.UserRole, state)
                continue

            new_key = LogParser.repoint_study_key(origins, source, state.get('study_raw'))
            if new_key and new_key != state.get('study'):
                state['study'] = new_key
                item.setData(Qt.ItemDataRole.UserRole, state)
                item.setText(f"{new_key} | {state.get('system', 'N/A')}")

        self._drop_rows_of_unloaded_paths(paths)

    def _drop_rows_of_unloaded_paths(self, paths):
        """Removes the rows of paths whose chip is gone, then rebuilds the survivors.

        The rebuild only runs when at least one row was actually dropped -
        otherwise the in-place re-pointing above is the whole update. The
        rebuild itself cannot be avoided: every per-row cell widget captures
        its row index in its signal handlers (the sync_row_visuals(row)
        lambdas wired in _populate_row_data), so a bare removeRow() would
        leave each row below it wired to the wrong index.
        """
        survivors, dropped = [], 0
        for row in range(self.plot_table.rowCount()):
            item = self.plot_table.item(row, 1)
            state = item.data(Qt.ItemDataRole.UserRole) if item else None
            source = (state or {}).get('source_path')
            study = (state or {}).get('study', '')
            if LogParser._is_virtual_study_key(study):
                if study not in self.data_manager.parsers:
                    dropped += 1
                    continue
                survivors.append(self._extract_row_data(row))
                continue
            if LogParser.path_is_loaded(source, paths):
                survivors.append(self._extract_row_data(row))
            else:
                dropped += 1

        if not dropped:
            return

        selected = self.plot_table.currentRow()
        self.plot_table.blockSignals(True)
        try:
            self.plot_table.setRowCount(0)
            for data in survivors:
                row = self.plot_table.rowCount()
                self.plot_table.insertRow(row)
                self._populate_row_data(row, data)
        finally:
            self.plot_table.blockSignals(False)

        self.last_selected_row = -1
        self._update_move_buttons_visibility()

        if self.plot_table.rowCount() == 0:
            self._reset_controls_to_defaults()
            self.add_new_row()
        else:
            self.plot_table.selectRow(min(max(selected, 0), self.plot_table.rowCount() - 1))

    def _update_ui_state(self, project_loaded, keep_table=False):
        if not keep_table:
            self.plot_table.setRowCount(0)
            self.controller.update_view_config({'active': False})
            self.plot_widget.autoRange()
        
        if project_loaded:
            self.study_combo.setEnabled(True)
            self.system_combo.setEnabled(True)
            self.add_btn.setEnabled(True)
            
            studies = self.data_manager.get_study_names()
            # Preserve the current selection across a reload, and force the cascade:
            # _populate_combo blocks signals, so without this the system combo would
            # still list the PREVIOUS project's systems and the controller would keep
            # its stale timestep lists.
            # _pending_study_key follows the selection through a re-qualified key.
            self._populate_combo(self.study_combo, "Select Study", studies,
                                 self._pending_study_key or self.study_combo.currentText())
            self._style_study_combo(self.study_combo)
            self._pending_study_key = None
            self.on_study_changed(self.study_combo.currentText())

            if self.plot_table.rowCount() == 0:
                self.add_new_row()
        else:
            self._reset_controls_to_defaults()
            self.add_btn.setEnabled(False)

    def _populate_combo(self, combo, placeholder, items, current=None):
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(placeholder)
        if items:
            combo.addItems(items)
        
        if current and current in items:
            combo.setCurrentText(current)
        elif len(items) == 1 and placeholder in ("Select Study", "Select System"):
            combo.setCurrentIndex(1)
        elif placeholder == "Select System" and current and current != "Select System" and len(items) > 1:
            matched = _find_pattern_matched_system(current, items)
            if matched is not None:
                combo.setCurrentText(matched)
            else:
                combo.setCurrentIndex(0)
        else:
            combo.setCurrentIndex(0)
        # Bold virtual studies in the study combo
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
        # Study switch: remember previous study for derived fallback when no explicit global.
        prev_study = getattr(self, '_prev_study', None)
        current_system = self.system_combo.currentText()
        systems = self.data_manager.get_system_names(text) if text != "Select Study" else []
        # Derived target when no explicit global yet.
        prev_seq_target = None
        if getattr(self, '_explicit_system_seq', None) is None and prev_study and current_system and current_system != "Select System":
            try:
                prev_systems = self.data_manager.get_system_names(prev_study) if prev_study != "Select Study" else []
                if current_system in prev_systems:
                    prev_seq_target = prev_systems.index(current_system)
            except Exception:
                prev_seq_target = None
        self._suppress_seq_update = True
        try:
            self._populate_combo(self.system_combo, "Select System", systems, current_system)
            # Sequence fallback: only if still placeholder and previous checks failed (single/exact/pattern already tried).
            if self.system_combo.currentText() == "Select System" and len(systems) > 0:
                target_idx = None
                if getattr(self, '_explicit_system_seq', None) is not None:
                    target_idx = self._explicit_system_seq
                elif prev_seq_target is not None:
                    target_idx = prev_seq_target
                if target_idx is not None:
                    clamped = max(0, min(int(target_idx), len(systems) - 1))
                    final = systems[clamped]
                    self.system_combo.blockSignals(True)
                    self.system_combo.setCurrentText(final)
                    self.system_combo.blockSignals(False)
            self.on_system_changed(self.system_combo.currentText())
        finally:
            self._suppress_seq_update = False
            self._prev_study = text if text != "Select Study" else None

    def on_system_changed(self, text):
        # Manual system pick locks the sequence number (count only real systems).
        if text and text != "Select System" and not self._updating_from_code and not getattr(self, '_suppress_seq_update', False):
            study_tmp = self.study_combo.currentText()
            if study_tmp and study_tmp != "Select Study":
                try:
                    _systems_tmp = self.data_manager.get_system_names(study_tmp)
                    if text in _systems_tmp:
                        self._explicit_system_seq = _systems_tmp.index(text)
                except Exception:
                    pass
        if text != "Select System":
            study = self.study_combo.currentText()
            
            # Check if study changed BEFORE updating controller
            is_study_change = (self.controller.current_study != study)
            
            parser = self.data_manager.get_parser(study, text)
            if parser:
                cols = parser.get_column_names()
                self.controller.set_active_system(study, text, force=self._force_system_reload)
                if self._force_system_reload or self._reset_range_on_next_bind:
                    # New project: show the whole trajectory instead of conserving the
                    # previous project's (possibly much shorter) min/max window.
                    self.controller.reset_range()
                    self._reset_range_on_next_bind = False
                self.player_controls.set_timesteps(self.controller.get_available_timesteps())
                
                # Sync player controls to the controller's preserved timestep
                current_ts = self.controller.current_timestep
                if current_ts in self.controller.timesteps:
                    idx = self.controller.timesteps.index(current_ts)
                    self.player_controls.set_step_index(idx)
                else:
                    self.player_controls.set_step_index(0)
                
                if cols:
                    # Capture currently selected axes before repopulating
                    current_x = self.xaxis_combo.currentText()
                    current_y = self.yaxis_combo.currentText()
                    
                    combo_map = {
                        self.xaxis_combo: ("Select X-Axis", current_x),
                        self.yaxis_combo: ("Select Y-Axis", current_y),
                        self.zfilter_combo: ("No Z-Filter", None),
                        self.heatmap_combo: ("No Heatmap", None)
                    }

                    for combo, (placeholder, retention) in combo_map.items():
                        target = retention if retention and retention in cols else combo.currentText()
                        self._populate_combo(combo, placeholder, cols, target)
                        combo.setEnabled(True)

        # Only sync if we are NOT in the middle of a programmatic update
        if not self._updating_from_code:
            self.sync_dropdowns_to_row()

    def on_axis_changed(self, text):
        self.sync_dropdowns_to_row()
        self.plot_widget.getPlotItem().autoRange()

    def _get_distinct_color(self, existing_colors):
        candidates = []
        for _ in range(20):
            hue = random.random()
            candidates.append(QColor.fromHsvF(hue, 1.0, 1.0, 1.0))

        if not existing_colors:
            return candidates[0]

        best_candidate = None
        max_min_dist = -1

        for candidate in candidates:
            r1, g1, b1, _ = candidate.getRgb()
            min_dist_sq = float('inf')
            for existing in existing_colors:
                r2, g2, b2, _ = existing.getRgb()
                dist_sq = (r1 - r2)**2 + (g1 - g2)**2 + (b1 - b2)**2
                if dist_sq < min_dist_sq:
                    min_dist_sq = dist_sq
            
            if min_dist_sq > max_min_dist:
                max_min_dist = min_dist_sq
                best_candidate = candidate

        return best_candidate

    # --- Row Management ---

    def add_new_row(self):
        row = self.plot_table.rowCount()
        self.plot_table.insertRow(row)
        
        # Defaults
        state = {
            'study': "Select Study", 'system': "Select System", 
            'x_col': "Select X-Axis", 'y_col': "Select Y-Axis",
            'z_col': "No Z-Filter", 'z_ref': "Initial",
            'h_col': "No Heatmap", 'h_ref': "Initial",
            'view_lock': True, 'view_sync': True, 'view_range': None,
            'current_step_index': 0,
            'z_ranges': [(float('-inf'), float('inf'))] # Default single full range
        }
        row_data = {
            'state': state, 'is_heatmap': False, 'color': "#000000",
            'heatmap_grad': None, 'style': 'o', 'size': '5'
        }

        # Clone from current selection if valid
        current_row = self.plot_table.currentRow()
        if current_row >= 0:
            try:
                extracted = self._extract_row_data(current_row)
                row_data = extracted.copy()
                row_data['state'] = extracted['state'].copy()
            except Exception:
                pass
        
        if not row_data['is_heatmap']:
            existing_colors = []
            for r in range(row):
                cw = self.plot_table.cellWidget(r, 2)
                if isinstance(cw, ColorButton):
                    existing_colors.append(cw.color())
            new_col = self._get_distinct_color(existing_colors)
            row_data['color'] = new_col.name()
        
        self._populate_row_data(row, row_data)
        self.plot_table.selectRow(row)
        self.on_table_selection_changed()
        self._update_move_buttons_visibility()

    def _populate_row_data(self, row, data):
        state = data['state']
        is_heatmap = data.get('is_heatmap', False)

        # 1. Move Buttons (Col 0) - Centered
        move_container = QWidget()
        move_layout = QVBoxLayout(move_container)
        move_layout.setContentsMargins(2, 2, 2, 2)
        move_layout.setSpacing(2)
        move_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        btn_up = QPushButton("▲")
        btn_up.setObjectName("up_button")
        btn_up.setFixedSize(20, 12)
        btn_up.clicked.connect(self.move_row_up)
        
        btn_down = QPushButton("▼")
        btn_down.setObjectName("down_button")
        btn_down.setFixedSize(20, 12)
        btn_down.clicked.connect(self.move_row_down)
        
        move_layout.addWidget(btn_up)
        move_layout.addWidget(btn_down)
        self.plot_table.setCellWidget(row, 0, move_container)

        # 2. Name Item (Col 1)
        s_study = state.get('study', 'N/A')
        s_system = state.get('system', 'N/A')
        if "Select" in s_study: s_study = "N/A"
        if "Select" in s_system: s_system = "N/A"
        
        disp_study = LogParser.display_study_for_virtual(s_study, s_system)
        item = QTableWidgetItem(f"{disp_study} | {s_system}")
        item.setData(Qt.ItemDataRole.UserRole, state)
        self.plot_table.setItem(row, 1, item)

        # 3. Color/Heatmap (Col 2)
        if is_heatmap:
            combo = QComboBox()
            combo.addItems(["Rainbow", "Viridis", "Hot", "Blue-Red", "Plasma", "Magma"])
            combo.view().setMinimumWidth(120)
            
            if data.get('heatmap_grad'):
                combo.setCurrentText(data.get('heatmap_grad'))
            combo.currentTextChanged.connect(lambda: self.sync_row_visuals(row))
            self.plot_table.setCellWidget(row, 2, combo)
        else:
            btn = ColorButton(QColor(data.get('color', '#000000')))
            btn.colorChanged.connect(lambda: self.sync_row_visuals(row))
            self.plot_table.setCellWidget(row, 2, btn)

        # 4. Style (Col 3)
        style_combo = QComboBox()
        style_combo.addItems(["o", "x", "+", "d", "s", "t", "p", "h", "star"])
        style_combo.setCurrentText(data.get('style', 'o'))
        style_combo.currentTextChanged.connect(lambda: self.sync_row_visuals(row))
        self.plot_table.setCellWidget(row, 3, style_combo)

        # 5. Size (Col 4)
        size_edit = QLineEdit(str(data.get('size', '5')))
        size_edit.setValidator(QIntValidator(1, 100))
        size_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        size_edit.textChanged.connect(lambda: self.sync_row_visuals(row))
        self.plot_table.setCellWidget(row, 4, size_edit)

        # 6. Delete (Col 5)
        del_btn = QPushButton("X")
        del_btn.setStyleSheet("color: red; font-weight: bold;") 
        del_btn.clicked.connect(self.delete_row)
        
        del_container = QWidget()
        del_layout = QHBoxLayout(del_container)
        del_layout.setContentsMargins(0,0,0,0)
        del_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        del_layout.addWidget(del_btn)
        self.plot_table.setCellWidget(row, 5, del_container)

    def _extract_row_data(self, row):
        item = self.plot_table.item(row, 1)
        state = item.data(Qt.ItemDataRole.UserRole)
        
        color_widget = self.plot_table.cellWidget(row, 2)
        style_widget = self.plot_table.cellWidget(row, 3)
        size_widget = self.plot_table.cellWidget(row, 4)

        is_heatmap = isinstance(color_widget, QComboBox)
        color_val = "#000000"
        grad_val = None
        
        if is_heatmap:
            grad_val = color_widget.currentText()
        elif isinstance(color_widget, ColorButton):
            color_val = color_widget.color().name()

        style_val = style_widget.currentText()
        size_val = size_widget.text()

        return {
            'state': state,
            'is_heatmap': is_heatmap,
            'color': color_val,
            'heatmap_grad': grad_val,
            'style': style_val,
            'size': size_val
        }

    def move_row_up(self):
        btn = self.sender()
        pos = btn.parentWidget().mapTo(self.plot_table.viewport(), QPoint(0,0))
        row = self.plot_table.indexAt(pos).row()
        if row > 0:
            self.swap_rows(row, row - 1)
            self.plot_table.selectRow(row - 1)

    def move_row_down(self):
        btn = self.sender()
        pos = btn.parentWidget().mapTo(self.plot_table.viewport(), QPoint(0,0))
        row = self.plot_table.indexAt(pos).row()
        if row < self.plot_table.rowCount() - 1:
            self.swap_rows(row, row + 1)
            self.plot_table.selectRow(row + 1)

    def swap_rows(self, r1, r2):
        self.plot_table.blockSignals(True)
        d1 = self._extract_row_data(r1)
        d2 = self._extract_row_data(r2)
        self._populate_row_data(r1, d2)
        self._populate_row_data(r2, d1)
        self.plot_table.blockSignals(False)
        self._update_move_buttons_visibility()

    def _update_move_buttons_visibility(self):
        count = self.plot_table.rowCount()
        for r in range(count):
            widget = self.plot_table.cellWidget(r, 0)
            if widget:
                up = widget.findChild(QPushButton, "up_button")
                down = widget.findChild(QPushButton, "down_button")
                if up: up.setEnabled(r > 0)
                if down: down.setEnabled(r < count - 1)

    def delete_row(self):
        btn = self.sender()
        if not btn: return
        pos = btn.parentWidget().mapTo(self.plot_table.viewport(), QPoint(0,0))
        index = self.plot_table.indexAt(pos)
        
        if index.isValid():
            row_to_del = index.row()
            if row_to_del == self.last_selected_row:
                self.last_selected_row = -1 
            
            self.plot_table.removeRow(row_to_del)
            self._update_move_buttons_visibility()
            
            if self.plot_table.rowCount() == 0:
                self._reset_controls_to_defaults()
                self.add_new_row()
            else:
                new_sel = min(row_to_del, self.plot_table.rowCount() - 1)
                self.plot_table.selectRow(new_sel)

    def _reset_controls_to_defaults(self):
        self._updating_from_code = True
        try:
            if self.study_combo.count() > 0:
                self.study_combo.setCurrentIndex(0)
            self.on_study_changed("Select Study")
            self.on_system_changed("Select System")
            self.zfilter_combo.setCurrentIndex(0)
            self.heatmap_combo.setCurrentIndex(0)
            self.zfilter_ref_combo.setEnabled(False)
            self.heatmap_ref_combo.setEnabled(False)
        finally:
            self._updating_from_code = False

    # --- Per-Row State Saving ---

    def _save_current_row_state(self):
        if self.last_selected_row >= 0 and self.last_selected_row < self.plot_table.rowCount():
            item = self.plot_table.item(self.last_selected_row, 1)
            if item:
                state = item.data(Qt.ItemDataRole.UserRole)
                if state:
                    vb = self.plot_widget.getPlotItem().getViewBox()
                    state['view_range'] = vb.viewRange()
                    item.setData(Qt.ItemDataRole.UserRole, state)

    def _on_frame_changed(self, frame_idx):
        self.player_controls.set_step_index(frame_idx)
        self._save_step_to_current_row(frame_idx)

    def _on_step_changed_by_user(self, step):
        self.controller.set_timestep_index(step)
        self._save_step_to_current_row(step)

    def _on_range_requested(self, min_val, max_val):
        new_timesteps = self.controller.set_timestep_range(min_val, max_val)
        if new_timesteps:
            self.player_controls.set_timesteps(new_timesteps)
            
            # Sync slider index to the controller's current timestep
            curr_ts = self.controller.current_timestep
            try:
                new_idx = new_timesteps.index(curr_ts)
                self.player_controls.set_step_index(new_idx)
            except ValueError:
                self.player_controls.set_step_index(0)
            
            # Refresh Limits if active (Initial/Final refs might have changed)
            # Need to capture current state to know which cols are active
            row = self.plot_table.currentRow()
            if row >= 0:
                item = self.plot_table.item(row, 1)
                state = item.data(Qt.ItemDataRole.UserRole)
                if state:
                    study = state.get('study')
                    system = state.get('system')
                    
                    # 1. Z-Filter
                    z_col = state.get('z_col')
                    if z_col and z_col != "No Z-Filter":
                        z_ref = state.get('z_ref', 'Current')
                        dmin, dmax = self.controller.get_scope_min_max(study, system, z_col, z_ref)
                        self.filter_bar.set_data_range(dmin, dmax)
                        
            self.update_plot_from_selection()

    def _save_step_to_current_row(self, step):
        row = self.plot_table.currentRow()
        if row >= 0:
            item = self.plot_table.item(row, 1)
            if item:
                state = item.data(Qt.ItemDataRole.UserRole)
                if state:
                    state['current_step_index'] = step
                    item.setData(Qt.ItemDataRole.UserRole, state)

    def _on_filter_bar_changed(self, ranges):
        self.update_plot_from_selection()
        row = self.plot_table.currentRow()
        if row >= 0:
            item = self.plot_table.item(row, 1)
            if item:
                state = item.data(Qt.ItemDataRole.UserRole)
                if state:
                    state['z_ranges'] = ranges
                    item.setData(Qt.ItemDataRole.UserRole, state)

    def _on_controller_bounds_changed(self, bounds):
        if 'heatmap' in bounds:
            payload = bounds['heatmap']
            hmin, hmax = payload[0], payload[1]
            is_override = payload[2] if len(payload) > 2 else False
            self.heatmap_bar.set_range(hmin, hmax, is_override)

        if 'z_filter' in bounds:
            zmin, zmax = bounds['z_filter']
            self.filter_bar.set_data_range(zmin, zmax)

    # --- Heatmap colour-scale overrides (per reference type, persisted in the row) ---

    def _current_row_state(self):
        row = self.plot_table.currentRow()
        if row < 0:
            return None, None
        item = self.plot_table.item(row, 1)
        if not item:
            return None, None
        return item, item.data(Qt.ItemDataRole.UserRole)

    def _on_heatmap_bounds_edited(self, lo, hi):
        item, state = self._current_row_state()
        if not state:
            return
        key = self.controller.bounds_key(state.get('h_ref', 'Current'))
        bounds = dict(state.get('h_bounds') or {})
        bounds[key] = [lo, hi]
        state['h_bounds'] = bounds
        item.setData(Qt.ItemDataRole.UserRole, state)
        self.update_plot_from_selection()

    def _on_heatmap_bounds_reset(self):
        item, state = self._current_row_state()
        if not state:
            return
        key = self.controller.bounds_key(state.get('h_ref', 'Current'))
        bounds = dict(state.get('h_bounds') or {})
        bounds.pop(key, None)
        state['h_bounds'] = bounds
        item.setData(Qt.ItemDataRole.UserRole, state)
        self.update_plot_from_selection()

    # --- Logic ---

    def on_cell_double_clicked(self, row, col):
        if col == 1:
            self.open_global_label_editor()

    def open_global_label_editor(self):
        props = set()
        for i in range(self.plot_table.rowCount()):
            state = self.plot_table.item(i, 1).data(Qt.ItemDataRole.UserRole)
            if state:
                props.add(state.get('x_col'))
                props.add(state.get('y_col'))
        
        valid_props = [p for p in props if p and "Select" not in p]
        if not valid_props: return

        dlg = GlobalLabelEditorDialog(sorted(list(valid_props)), self.global_label_map, self)
        if dlg.exec():
            self.global_label_map = dlg.get_updated_map()
            self.update_plot_from_selection()

    def open_label_context_menu(self, pos):
        pass

    def _capture_dropdown_state(self):
        current_row = self.plot_table.currentRow()
        old_state = {}
        if current_row >= 0:
            item = self.plot_table.item(current_row, 1)
            if item:
                old_state = item.data(Qt.ItemDataRole.UserRole) or {}

        state = {
            'study': self.study_combo.currentText(),
            'system': self.system_combo.currentText(),
            'x_col': self.xaxis_combo.currentText(),
            'y_col': self.yaxis_combo.currentText(),
            'z_col': self.zfilter_combo.currentText(),
            'z_ref': self.zfilter_ref_combo.currentText(),
            'h_col': self.heatmap_combo.currentText(),
            'h_ref': self.heatmap_ref_combo.currentText(),
            'view_lock': old_state.get('view_lock', False),
            'view_sync': old_state.get('view_sync', False),
            'view_range': old_state.get('view_range', None),
            'current_step_index': old_state.get('current_step_index', 0),
            'z_ranges': old_state.get('z_ranges', self.filter_bar.current_ranges),
            # Carried over explicitly: this dict is rebuilt from the combos on every
            # sync, so anything not listed here would be silently dropped.
            'h_bounds': old_state.get('h_bounds', {})
        }
        state.update(self._study_origin_fields(state['study']))

        if state['view_lock']:
            vb = self.plot_widget.getPlotItem().getViewBox()
            state['view_range'] = vb.viewRange()
            
        return state

    def _apply_state_to_dropdowns(self, state):
        # Set flag to tell sync_dropdowns_to_row to ignore signals
        self._updating_from_code = True
        try:
            # 1. Set Study
            self.study_combo.blockSignals(True)
            self.study_combo.setCurrentText(state.get('study', 'Select Study'))
            self.study_combo.blockSignals(False)
            
            # Manually trigger change logic, but flag remains True
            self.on_study_changed(self.study_combo.currentText())
            
            # 2. Set System
            self.system_combo.blockSignals(True)
            self.system_combo.setCurrentText(state.get('system', 'Select System'))
            self.system_combo.blockSignals(False)
            
            # Manually trigger change logic
            self.on_system_changed(self.system_combo.currentText())
            
            # 3. Set Dependent Combos (X, Y, Z, Heatmap)
            # We use a helper to find the text or default to index 0
            def safe_set(combo, val):
                combo.blockSignals(True)
                if val:
                    # Dynamically restore "Step X" entry if it exists in saved state but not in combo
                    if val.startswith("Step ") and combo.findText(val) == -1:
                        # Insert before "Set step"
                        combo.insertItem(combo.count() - 1, val)
                    
                    if combo.findText(val) >= 0:
                        combo.setCurrentText(val)
                    else:
                        combo.setCurrentIndex(0)
                else:
                    combo.setCurrentIndex(0)
                combo.blockSignals(False)
                
            safe_set(self.xaxis_combo, state.get('x_col'))
            safe_set(self.yaxis_combo, state.get('y_col'))
            safe_set(self.zfilter_combo, state.get('z_col'))
            safe_set(self.zfilter_ref_combo, state.get('z_ref'))
            safe_set(self.heatmap_combo, state.get('h_col'))
            safe_set(self.heatmap_ref_combo, state.get('h_ref'))
            
        finally:
            # Always ensure flag is lowered
            self._updating_from_code = False

    def on_table_selection_changed(self):
        # 1. Save state of the PREVIOUS row before switching
        self._save_current_row_state()
        
        row = self.plot_table.currentRow()
        self.last_selected_row = row 
        
        if row < 0: return
        
        item = self.plot_table.item(row, 1)
        if not item: return
        state = item.data(Qt.ItemDataRole.UserRole)
        
        if state:
            # 2. Apply dropdown state (Study, System, Axes)
            # This function handles its own flag (_updating_from_code) to prevent
            # dropdown signals from triggering saves.
            self._apply_state_to_dropdowns(state)
            
            # 3. Handle Visual Widgets (Color/Heatmap) in the table
            is_heatmap = state.get('h_col') != "No Heatmap"
            curr_widget = self.plot_table.cellWidget(row, 2)
            is_combo = isinstance(curr_widget, QComboBox)
            
            if (is_heatmap and not is_combo) or (not is_heatmap and is_combo):
                data = self._extract_row_data(row)
                data['is_heatmap'] = is_heatmap
                self._populate_row_data(row, data)

            # 4. Handle Visibility
            has_filter = state.get('z_col') != "No Z-Filter"
            self.filter_bar.setVisible(has_filter)
            self.zfilter_ref_combo.setEnabled(has_filter)

            self.heatmap_bar.setVisible(is_heatmap)
            self.heatmap_ref_combo.setEnabled(is_heatmap)

            # 5. Restore Filter Bar State
            # CRITICAL: Block signals here to prevent the bar from telling the table to "save" while we are just trying to "load".
            if has_filter:
                self.filter_bar.blockSignals(True)
                try:
                    dmin, dmax = self.controller.get_scope_min_max(state['study'], state['system'], state['z_col'], state.get('z_ref', 'Current'))
                    self.filter_bar.set_data_range(dmin, dmax)
                    
                    if 'z_ranges' in state:
                        self.filter_bar.set_current_ranges(state['z_ranges'])
                    elif 'z_range' in state: # Legacy support
                        self.filter_bar.set_current_ranges([state['z_range']])
                finally:
                    self.filter_bar.blockSignals(False)
            
            # 6. Restore Time Step
            step_idx = state.get('current_step_index', 0)
            self.controller.set_timestep_index(step_idx)
            self.player_controls.set_step_index(step_idx)

            # 7. Restore View Locks
            is_locked = state.get('view_lock', True)
            is_synced = state.get('view_sync', True)
            
            self.lock_axes_btn.setVisible(True)
            self.lock_axes_btn.blockSignals(True) # Block to prevent toggle signal
            self.lock_axes_btn.setChecked(is_locked)
            self.lock_axes_btn.blockSignals(False)
            self._update_lock_button_visuals(is_locked, is_synced)
            
            # 8. Finally, update the plot
            self.update_plot_from_selection()
            
            # Restore specific view range if locked
            vb = self.plot_widget.getPlotItem().getViewBox()
            if is_locked and state.get('view_range'):
                vr = state['view_range']
                vb.setRange(xRange=vr[0], yRange=vr[1], padding=0)

    def sync_dropdowns_to_row(self):
        if self._updating_from_code: return
        
        row = self.plot_table.currentRow()
        if row < 0: return
        
        state = self._capture_dropdown_state()
        
        # --- Handle "Set step" Triggers ---
        re_sync_state = False
        for combo, key in [(self.zfilter_ref_combo, 'z_ref'), (self.heatmap_ref_combo, 'h_ref')]:
            if state.get(key) == "Set step":
                curr_step = self.controller.current_timestep
                new_label = f"Step {curr_step}"
                
                self._updating_from_code = True
                # Remove any existing step
                for i in range(combo.count()):
                    if combo.itemText(i).startswith("Step "):
                        combo.removeItem(i)
                        break
                # Insert before last item ("Set step")
                combo.insertItem(combo.count() - 1, new_label)
                combo.setCurrentText(new_label)
                self._updating_from_code = False
                
                state[key] = new_label
                re_sync_state = True
        
        # If we updated the dropdowns, we should update the 'state' stored in item
        # but let's continue with the rest of the logic using the modified 'state'
        
        if state['z_col'] != "No Z-Filter":
            item = self.plot_table.item(row, 1)
            prev_state = item.data(Qt.ItemDataRole.UserRole)
            
            is_new_filter_activation = (not prev_state) or (prev_state.get('z_col') == "No Z-Filter")
            is_prop_changed = prev_state and (prev_state.get('z_col') != state['z_col'] or prev_state.get('z_ref') != state['z_ref'])
            
            if is_new_filter_activation or is_prop_changed:
                dmin, dmax = self.controller.get_scope_min_max(
                    state['study'], state['system'], state['z_col'], state['z_ref']
                )
                self.filter_bar.set_data_range(dmin, dmax)
                
                if is_new_filter_activation:
                    self.filter_bar.set_current_ranges([(dmin, dmax)])
                    state['z_ranges'] = [(dmin, dmax)]
                else:
                    state['z_ranges'] = self.filter_bar.current_ranges

        item = self.plot_table.item(row, 1)
        item.setData(Qt.ItemDataRole.UserRole, state)
        
        s_study = state.get('study', 'N/A')
        s_system = state.get('system', 'N/A')
        if "Select" in s_study: s_study = "N/A"
        if "Select" in s_system: s_system = "N/A"
        disp_study = LogParser.display_study_for_virtual(s_study, s_system)
        item.setText(f"{disp_study} | {s_system}")
        
        is_heatmap = state['h_col'] != "No Heatmap"
        curr_widget = self.plot_table.cellWidget(row, 2)
        is_combo = isinstance(curr_widget, QComboBox)
        
        if (is_heatmap and not is_combo) or (not is_heatmap and is_combo):
            data = self._extract_row_data(row)
            data['is_heatmap'] = is_heatmap
            self._populate_row_data(row, data)
            
        has_filter = state['z_col'] != "No Z-Filter"
        self.filter_bar.setVisible(has_filter)
        self.zfilter_ref_combo.setEnabled(has_filter)
        
        self.heatmap_bar.setVisible(is_heatmap)
        self.heatmap_ref_combo.setEnabled(is_heatmap)
        
        self.update_plot_from_selection()

    def sync_row_visuals(self, row):
        if row == self.plot_table.currentRow():
            self.update_plot_from_selection()

    def update_plot_from_selection(self):
        row = self.plot_table.currentRow()
        if row < 0: 
            self.controller.update_view_config({'active': False})
            return
            
        item = self.plot_table.item(row, 1)
        if not item: return
        
        state = item.data(Qt.ItemDataRole.UserRole)
        if not state: return
        
        if "Select" in state.get('study', '') or "Select" in state.get('system', ''):
            self.controller.update_view_config({'active': False})
            return
        if "Select" in state.get('x_col', '') or "Select" in state.get('y_col', ''):
            self.controller.update_view_config({'active': False})
            return

        color_widget = self.plot_table.cellWidget(row, 2)
        style_widget = self.plot_table.cellWidget(row, 3)
        size_widget = self.plot_table.cellWidget(row, 4)
        
        if not all([color_widget, style_widget, size_widget]): return
        
        color = QColor('black')
        gradient = None
        if isinstance(color_widget, ColorButton):
            color = color_widget.color()
        elif isinstance(color_widget, QComboBox):
            gradient = color_widget.currentText()
            # Update the Heatmap Bar visual to match selected gradient
            self.heatmap_bar.set_gradient_name(gradient)
            
        size_txt = size_widget.text()
        size = int(size_txt) if size_txt.isdigit() else 5
        
        z_col = state.get('z_col') if state.get('z_col') != "No Z-Filter" else None
        h_col = state.get('h_col') if state.get('h_col') != "No Heatmap" else None

        x_ax = state.get('x_col')
        y_ax = state.get('y_col')
        x_label = self.global_label_map.get(x_ax, x_ax)
        y_label = self.global_label_map.get(y_ax, y_ax)
        self.plot_widget.setLabel('bottom', x_label)
        self.plot_widget.setLabel('left', y_label)

        # Calculate relative z-filter handles for persistence
        bar_min = self.filter_bar.data_min
        bar_max = self.filter_bar.data_max
        range_span = bar_max - bar_min
        if range_span == 0: range_span = 1.0
        
        rel_ranges = []
        for cmin, cmax in self.filter_bar.current_ranges:
            rel_min = (cmin - bar_min) / range_span
            rel_max = (cmax - bar_min) / range_span
            rel_min = max(0.0, min(1.0, rel_min))
            rel_max = max(0.0, min(1.0, rel_max))
            rel_ranges.append((rel_min, rel_max))

        config = {
            'active': True,
            'study': state['study'],
            'system': state['system'],
            'x_col': x_ax,
            'y_col': y_ax,
            'x_label': x_label,
            'y_label': y_label,
            'z_filter_col': z_col,
            'z_filter_ref': state.get('z_ref', 'Initial'),
            'z_ranges': self.filter_bar.current_ranges,
            'z_ranges_rel': rel_ranges,
            'heatmap_col': h_col,
            'heatmap_ref': state.get('h_ref', 'Initial'),
            # Per-reference-type colour-scale overrides; empty dict = follow the data.
            'h_bounds': state.get('h_bounds') or {},
            'heatmap_gradient': gradient,
            'color': color,
            'symbol': style_widget.currentText(),
            'size': size,
            'view_sync': state.get('view_sync', True),
            'view_lock': state.get('view_lock', True),
            'plot_name': item.text()
        }
        
        self.controller.update_view_config(config)

    def _on_view_lock_toggled(self, checked):
        row = self.plot_table.currentRow()
        if row < 0: return
        
        item = self.plot_table.item(row, 1)
        state = item.data(Qt.ItemDataRole.UserRole)
        
        state['view_lock'] = checked
        if checked:
            state['view_range'] = self.plot_widget.getPlotItem().getViewBox().viewRange()
        else:
            state['view_range'] = None
            
        item.setData(Qt.ItemDataRole.UserRole, state)
        self._update_lock_button_visuals(checked, state.get('view_sync', False))
        
        self.update_plot_from_selection()

    def _on_auto_range_clicked(self):
        """Re-asserts the view lock after pyqtgraph's auto-range button.

        The button enables the ViewBox's own auto-range, so without this the view would
        keep refitting on every frame even though the lock reads as closed. Freeze the
        range the button just produced and adopt it as the locked range.
        """
        row = self.plot_table.currentRow()
        if row < 0: return

        item = self.plot_table.item(row, 1)
        if not item: return

        state = item.data(Qt.ItemDataRole.UserRole)
        if not state or not state.get('view_lock'): return

        vb = self.plot_widget.getPlotItem().getViewBox()
        vb.disableAutoRange()
        state['view_range'] = vb.viewRange()
        item.setData(Qt.ItemDataRole.UserRole, state)

    def _on_view_sync_toggled(self):
        row = self.plot_table.currentRow()
        if row < 0: return
        
        item = self.plot_table.item(row, 1)
        state = item.data(Qt.ItemDataRole.UserRole)
        
        current_sync = state.get('view_sync', False)
        state['view_sync'] = not current_sync
        
        item.setData(Qt.ItemDataRole.UserRole, state)
        self._update_lock_button_visuals(state.get('view_lock', False), state['view_sync'])
        self.update_plot_from_selection()

    def _update_lock_button_visuals(self, locked, synced):
        text = "🔒" if locked else "🔓"
        if synced:
            text += " ↕"
        self.lock_axes_btn.setText(text)

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

    def _mint_preset_name(self, existing):
        try:
            from popout_window import mint_preset_name as _mint
            return _mint(existing)
        except Exception:
            pass
        try:
            import datetime
            try:
                from zoneinfo import ZoneInfo
                now = datetime.datetime.now(datetime.timezone.utc).astimezone(ZoneInfo("Europe/Berlin"))
            except Exception:
                now = datetime.datetime.now().astimezone()
            base = now.strftime("%Y-%m-%d_%H:%M:%S")
        except Exception:
            base = "preset"
        if base not in (existing or []):
            return base
        i = 2
        while f"{base}_{i}" in (existing or []):
            i += 1
        return f"{base}_{i}"

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
            self.main_window.save_session_for_mode(self.main_window.MODE_TRJ, trigger=trigger)
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
                        preset['name'] = self._mint_preset_name(existing)
                    except Exception:
                        pass
                    self.popout_presets.append(copy.deepcopy(preset))
            else:
                try:
                    preset['name'] = self._mint_preset_name(existing)
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
        try:
            self.popout_windows.append(win)
        except Exception:
            pass
        try:
            self.pop_win = win
        except Exception:
            pass
        try:
            win.destroyed.connect(
                lambda: self.popout_windows.remove(win) if win in self.popout_windows else None)
        except Exception:
            pass

    def _open_fresh_popout(self):
        from popout_window import PopOutWindow
        state = self.controller.get_current_plot_state()
        if not state or not state.get('y_axes'):
            QMessageBox.information(self, "Info", "No valid data to pop out.")
            return

        dpi = self.logicalDpiX()
        w_in = self.plot_widget.width() / dpi
        h_in = self.plot_widget.height() / dpi

        self.pop_win = PopOutWindow(state, figsize=(w_in, h_in),
                                    source_name=None, on_close=self._on_popout_closed)
        self.pop_win.show()
        self._track_popout(self.pop_win)

    def _open_preset_popout(self, name):
        try:
            preset = next((p for p in (getattr(self, 'popout_presets', []) or [])
                           if p.get('name') == name), None)
        except Exception:
            preset = None
        if preset is None:
            return
        try:
            from popout_window import PopOutWindow
        except ImportError:
            logger.warning("Matplotlib is required for the Pop Out feature.")
            QMessageBox.critical(self, "Error", "Matplotlib is required for the Pop Out feature.\nPlease install it via pip: pip install matplotlib")
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
        win = PopOutWindow(state, figsize=(w_in, h_in),
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
        path, _ = QFileDialog.getSaveFileName(self, "Export Image", "", filters)
        
        if path:
            dpi = self.logicalDpiX()
            width_in = self.plot_widget.width() / dpi
            height_in = self.plot_widget.height() / dpi
            self.controller.export_plot(path, figsize=(width_in, height_in))

    def export_raw_data(self):
        filters = (
            "CSV Data (*.csv);;"
            "TSV Data (*.tsv)"
        )
        path, _ = QFileDialog.getSaveFileName(self, "Export Raw Data", "", filters)
        
        if path:
            dpi = self.logicalDpiX()
            width_in = self.plot_widget.width() / dpi
            height_in = self.plot_widget.height() / dpi
            self.controller.export_plot(path, figsize=(width_in, height_in))

    def export_video(self):
        from video_export_dialog import VideoExportDialog
        dlg = VideoExportDialog(self)
        dlg.exec()

    def _on_auto_index_clicked(self):
        if not self.data_manager.parsers:
            QMessageBox.warning(self, "No project loaded", "Please load a project path first.")
            return
            
        dlg = AutoIndexDialog(self, self.data_manager.parsers)
        dlg.exec()

    def _is_session_valid(self):
        """Checks if the current session contains meaningful data to save."""
        if not self.main_window.get_project_paths():
            return False

        if self.plot_table.rowCount() == 0:
            return False

        # Check for at least one valid row
        has_valid_row = False
        for row in range(self.plot_table.rowCount()):
            item = self.plot_table.item(row, 1)
            if item:
                state = item.data(Qt.ItemDataRole.UserRole)
                if state:
                    # Check if the row is still in "Select..." state
                    s_study = state.get('study', '')
                    s_system = state.get('system', '')
                    if "Select" not in s_study and "Select" not in s_system:
                        has_valid_row = True
                        break
        return has_valid_row

    def save_session_to_file(self, path, trigger=None):
        """Saves session to a specific file path."""
        # Update current row state before saving to capture latest view changes
        self._save_current_row_state()
        
        # Robustness check: Do not save if the state is empty/default
        if not self._is_session_valid():
             logger.info("[System] Save aborted: Session contains no valid data.")
             return False
        
        paths = self.main_window.get_project_paths()
        session_data = {
            'type': 'trj',
            # 'project_path' stays for older builds; 'project_paths' is authoritative.
            'project_path': paths[0] if paths else '',
            'project_paths': paths,
            'keywords': self.main_window.chip_input.get_chips(),
            'initial_step': self.controller.timesteps[0] if self.controller.timesteps else None,
            'final_step': self.controller.timesteps[-1] if self.controller.timesteps else None,
            'rows': [],
            'global_label_map': self.global_label_map,
            'popout_presets': copy.deepcopy(getattr(self, 'popout_presets', []) or [])
        }
        
        for row in range(self.plot_table.rowCount()):
            data = self._extract_row_data(row)
            row_data = {
                'state': data['state'],
                'visuals': {
                    'color': data['color'], 
                    'gradient': data['heatmap_grad'],
                    'symbol': data['style'], 
                    'size': data['size']
                }
            }
            session_data['rows'].append(row_data)
            
        tag = f" ({trigger})" if trigger else ""
        try:
            success = SettingsManager.save_state(path, session_data)
            if success:
                logger.info("[System] Saved session: %s for mode Trajectory Plot%s", path, tag)
            else:
                logger.warning("[System] Failed to save session: %s%s", path, tag)
            return success
        except Exception as e:
            logger.warning("[System] Error saving session%s: %s", tag, e)
            return False

    def save_session(self):
        """Opens file dialog to save session manually."""
        path, _ = QFileDialog.getSaveFileName(self, "Save Trj Session", "", "JSON Files (*.json)")
        if not path: return
        self.save_session_to_file(path, trigger="manual save")

    def load_session(self):
        """Opens file dialog to load session manually."""
        path, _ = QFileDialog.getOpenFileName(self, "Load Trj Session", "", "JSON Files (*.json)")
        if not path: return
        self.load_session_from_file(path)

    def load_session_from_file(self, path: str):
        """Loads a session from a file, handling mode mismatches and preventing UI reload triggers."""
        if not path or not os.path.exists(path):
            return
            
        data = SettingsManager.load_state(path)
        if not data:
            logger.warning("Failed to load trj plot mode session file: %s", path)
            QMessageBox.critical(self, "Error", "Failed to load trj plot mode session file.")
            return
        
        # --- Mismatch Check ---
        file_type = data.get('type')
        if file_type not in ['trj', 'trj_plot']:
            # Check for known types
            is_dsd = file_type in ['dsd', 'dsd_plot']
            is_log = file_type in ['log', 'log_plot']
            
            if is_dsd or is_log:
                target_mode = "DSD Mode" if is_dsd else "Log Plot"
                target_idx = self.main_window.MODE_DSD if is_dsd else self.main_window.MODE_LOG
                target_panel = self.main_window.dsd_plot_panel if is_dsd else self.main_window.log_plot_panel

                msg = QMessageBox(self.main_window)
                msg.setWindowTitle("Mode Mismatch")
                msg.setText(f"Mode Mismatch. This is a {target_mode} session.")
                abort_btn = msg.addButton("Abort", QMessageBox.ButtonRole.RejectRole)
                switch_btn = msg.addButton(f"Switch to {target_mode}", QMessageBox.ButtonRole.AcceptRole)
                msg.exec()
                
                if msg.clickedButton() == switch_btn:
                    # Save current (Trj) state
                    self.main_window.save_session_for_mode(self.main_window.MODE_TRJ)
                    # Set flag to skip orchestration's autoload
                    self.main_window._skip_next_orchestration = True
                    # Switch to Target Mode
                    self.main_window.switch_to_mode(target_idx)
                    # Force load
                    target_panel.load_session_from_file(path)
                return
            else:
                 # Unknown Type -> Error only
                logger.warning("Invalid session file '%s' with unknown type: '%s'", path, file_type)
                QMessageBox.warning(self.main_window, "Invalid Session File", 
                                    f"The file has an unknown or invalid type: '{file_type}'.\n"
                                    "Cannot load this session.")
                return

        logger.info("[System] Loading session: %s for mode Trajectory Plot", path)

        # --- Load Logic ---
        self.global_label_map = data.get('global_label_map', {})
        try:
            self.popout_presets = self._sanitize_popout_presets(data.get('popout_presets', []))
        except Exception:
            self.popout_presets = []
        try:
            self._refresh_popout_button()
        except Exception:
            pass
        project_paths = self.main_window.session_paths(data)
        keywords = data.get('keywords', [])

        # Prevent global widgets from triggering a reload during update
        self.main_window.path_input.blockSignals(True)
        self.main_window.chip_input.blockSignals(True)

        try:
            if project_paths:
                project_paths, relocated, action = self.main_window.resolve_session_paths(
                    self, project_paths)
                if action in ('cancel', 'reset', 'change'):
                    return

                self.main_window.set_project_paths(project_paths)
                self.main_window.chip_input.set_chips(keywords)

                # Load project data (Force reload to ensure consistency)
                self.load_project(project_paths, keywords, force_reload=True, keep_table=False)
                # A session carries its own step window; never let the project-swap
                # reset fire while restoring it.
                self._reset_range_on_next_bind = False

                # Restore Range
                init_s = data.get('initial_step')
                final_s = data.get('final_step')
                if init_s is not None and final_s is not None:
                    new_steps = self.controller.set_timestep_range(init_s, final_s)
                    if new_steps:
                        self.player_controls.set_timesteps(new_steps)

                # If relocated and project was found, auto-update the session file
                if relocated and self.data_manager.parsers:
                    if self.save_session_to_file(path):
                        logger.info("[System] Relocation successful. Session file updated: %s", path)
            
            self.plot_table.setRowCount(0)
            for entry in data.get('rows', []):
                state = entry.get('state')
                visuals = entry.get('visuals', {})
                
                if 'view_lock' not in state: state['view_lock'] = True
                if 'view_sync' not in state: state['view_sync'] = True
                
                is_heatmap = state.get('h_col') != "No Heatmap"
                
                row_data = {
                    'state': state,
                    'is_heatmap': is_heatmap,
                    'color': visuals.get('color', '#000000'),
                    'heatmap_grad': visuals.get('gradient'),
                    'style': visuals.get('symbol', 'o'),
                    'size': str(visuals.get('size', 5))
                }
                
                row = self.plot_table.rowCount()
                self.plot_table.insertRow(row)
                self._populate_row_data(row, row_data)

            # Rows are restored after load_project ran, so bind them to their paths here.
            self._resync_rows_to_paths(self.main_window.get_project_paths())

            if self.plot_table.rowCount() > 0:
                self.plot_table.selectRow(0)

        finally:
            self.main_window.path_input.blockSignals(False)
            self.main_window.chip_input.blockSignals(False)