import copy
import os
from pathlib import Path
import re
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton,
    QComboBox, QFrame, QTableWidget, QHeaderView, QTableWidgetItem,
    QMessageBox, QCheckBox, QLabel, QSplitter, QGridLayout, QSizePolicy, 
    QMenu, QFileDialog, QDialog, QSpinBox
)
from PyQt6.QtCore import Qt, QPoint, QTimer
from PyQt6.QtGui import QColor, QIntValidator, QAction, QActionGroup, QFont
import pyqtgraph as pg
import random
import numpy as np
import pandas as pd
import os

from log_parser import LogParser
from log_data_manager import LogDataManager
from log_controller import LogController, ColoredAxis
from settings_manager import SettingsManager
from ui_components import ColorButton, InconsistentDataDialog, RightClickButton, NoNewLineDelegate, MissingPathResolver
from global_label_editor_dialog import GlobalLabelEditorDialog
from custom_property_dialog import CustomPropertyDialog
from popout_window import PopOutWindow
from fit_dialog import FitFunctionDialog
from header_selection_dialog import HeaderSelectionDialog


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


class QuantizeDialog(QDialog):
    """Minimalist dialog for entering quantization points."""
    def __init__(self, current_points, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Quantize Plot Data")
        self.setMinimumWidth(300)
        
        layout = QVBoxLayout(self)
        
        self.label = QLabel("Enter number of data points for the average curve:")
        layout.addWidget(self.label)
        
        self.spin = QSpinBox()
        self.spin.setRange(2, 1000000)
        self.spin.setValue(current_points if current_points else 100)
        layout.addWidget(self.spin)
        
        btn_layout = QHBoxLayout()
        
        self.set_btn = QPushButton("Set Quantization")
        self.set_btn.clicked.connect(self.accept)
        btn_layout.addWidget(self.set_btn)
        
        self.remove_btn = QPushButton("Remove Quantization")
        self.remove_btn.clicked.connect(self._remove)
        btn_layout.addWidget(self.remove_btn)
        
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self.cancel_btn)
        
        layout.addLayout(btn_layout)
        
        self.result_value = None

    def _remove(self):
        self.result_value = "REMOVE"
        self.accept()

    def get_value(self):
        if self.result_value == "REMOVE":
            return None
        return self.spin.value()

class LogPlotPanel(QWidget):
    def __init__(self, main_window_ref):
        super().__init__()
        self.main_window = main_window_ref
        
        self.data_manager = LogDataManager()
        self.plot_controller = None
        self.sync_mean_enabled = False
        self.running_mean_setting = "symmetric_window"
        self.synchronized_columns = set()
        self.average_user_choices = {}
        self.popout_windows = []
        self.current_x_axis = None
        self.global_label_map = {}
        self.custom_properties = {} # Name -> Formula
        self.scale_lock_enabled = False
        self.single_axis_view_lock_enabled = False
        self.single_axis_view_ranges = None
        self._applying_single_axis_lock = False
        self._suppress_single_axis_range_capture = False
        self._update_views_pending = False
        
        # New Data Processing Properties
        self.enforce_zero_start = False
        self.quantize_points = None # None means deactivated, integer > 1 means active
        # When True, Mean window is applied per-system then averaged; otherwise average then smooth
        self.smooth_before_averaging = False
        
        # Fit Feature Properties
        self.fit_dialogs = {} # row_id -> FitFunctionDialog instance
        self.fit_results = {} # row_id -> result dict (x, y, error, params)
        self.fit_table_visible = False
        self.next_fit_id = 0 # Unique ID for fit rows to track dialogs reliably
        self.next_plot_id = 0 # Unique ID for plot rows
        # 'plot' | 'fit' | None - which table the user clicked last. Decides whether
        # "Add Fit" clones a fit row or starts a fresh fit on the selected plot row.
        self._last_clicked_table = None
        # plot_id -> last fetched {'x', 'y', ...}, filled by update_plots
        self._plot_data_cache = {}

        # Track loaded path to prevent clearing data on mode switch
        self.loaded_path = None
        # study key -> {'path': <project path>, 'study': <raw folder name>}
        self.study_origins = {}
        self._is_internal_update = False
        # Explicit system sequence memory (real systems only, 0-based). Updated only
        # on manual system picks, never on auto fallback. Used as final fallback
        # when single/exact/pattern all fail.
        self._explicit_system_seq = None
        self._prev_study = None
        # Header selection: None = all headers, else set of header tuples
        self.allowed_headers = None
        self._header_groups_cache = None

        self._init_ui()
        self._connect_signals()
        self._update_ui_state(project_loaded=False)

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(10)
        # Flush with the shared top bar, matching trj/dsd (which both use 0 margins
        # and rely on the inner (5, 0, 5, 5) of the control row).
        main_layout.setContentsMargins(0, 0, 0, 0)

        # --- Top Controls (Specific to Log Plot) ---
        # Make controls_layout an attribute so attach_mode_combo can access it
        self.controls_layout = QGridLayout()
        self.controls_layout.setContentsMargins(5, 0, 5, 5)
        self.controls_layout.setHorizontalSpacing(10)
        
        # Row 0/1, Col 0 is reserved for Mode Combo (Rowspan 2) -> Handled by attach_mode_combo
        
        # 2x2 grid for main selectors (Shifted to Col 1+)
        self.study_label_widget = QLabel("Study")
        self.controls_layout.addWidget(self.study_label_widget, 0, 1)
        self.study_combo = self._create_combo("Select Study")
        self.controls_layout.addWidget(self.study_combo, 0, 2)

        self.system_label_widget = QLabel("System")
        self.controls_layout.addWidget(self.system_label_widget, 0, 3)
        self.system_combo = self._create_combo("Select System")
        self.controls_layout.addWidget(self.system_combo, 0, 4)
        
        self.controls_layout.addWidget(QLabel("X-Axis"), 1, 1)
        self.xaxis_combo = self._create_combo("Select X-Axis")
        self.controls_layout.addWidget(self.xaxis_combo, 1, 2)

        self.controls_layout.addWidget(QLabel("Y-Axis"), 1, 3)
        self.yaxis_combo = self._create_combo("Select Y-Axis")
        self.controls_layout.addWidget(self.yaxis_combo, 1, 4)

        # Add button (spanning 2 rows) at Column 5
        self.add_btn = QPushButton("Add")
        self.add_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.controls_layout.addWidget(self.add_btn, 0, 5, 2, 1)

        # Column stretching (Dropdowns stretch)
        self.controls_layout.setColumnStretch(2, 1)
        self.controls_layout.setColumnStretch(4, 1)
        
        main_layout.addLayout(self.controls_layout)

        # --- Main Splitter (Plot and Table) ---
        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        
        # --- Left Panel (Plot) ---
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0,0,0,0)

        self.plot_widget = pg.PlotWidget(axisItems={'left': ColoredAxis('left'), 'bottom': ColoredAxis('bottom')})
        self.plot_widget.setBackground('w')
        self.plot_controller = LogController(self.plot_widget)
        self.plot_widget.getPlotItem().getViewBox().sigRangeChanged.connect(self._on_single_axis_view_range_changed)

        self.lock_axes_btn = RightClickButton()
        self.lock_axes_btn.setCheckable(True)
        self.lock_axes_btn.setText("🔓")
        self.lock_axes_btn.setToolTip("Left Click: Lock/Unlock 0-alignment\nRight Click: Lock/Unlock Scaling")
        self.lock_axes_btn.hide()
        self.lock_axes_btn.setParent(self.plot_widget)
        self.lock_axes_btn.setGeometry(self.plot_widget.width() - 40, 00, 40, 30)

        self.view_lock_btn = RightClickButton()
        self.view_lock_btn.setCheckable(True)
        self.view_lock_btn.setText("\U0001f513")
        self.view_lock_btn.setToolTip("Left Click: Lock/Unlock View Limits")
        self.view_lock_btn.hide()
        self.view_lock_btn.setParent(self.plot_widget)
        self.view_lock_btn.setGeometry(self.plot_widget.width() - 40, 0, 40, 30)
        original_resize = self.plot_widget.resizeEvent
        def custom_resize_event(event):
            if hasattr(self, 'lock_axes_btn'):
                self._update_lock_button_position()
            if original_resize:
                original_resize(event)
        self.plot_widget.resizeEvent = custom_resize_event

        left_layout.addWidget(self.plot_widget)

        self.popout_btn = QPushButton("Pop Out")
        self.export_btn = QPushButton("Quick Export ▾")
        
        # Configure Dropdown Menu for Quick Export
        self.export_menu = QMenu(self)
        self.export_image_action = self.export_menu.addAction("Image...")
        self.export_data_action = self.export_menu.addAction("Raw Data...")
        self.export_image_action.triggered.connect(self.export_image)
        self.export_data_action.triggered.connect(self.export_raw_data)
        self.export_btn.setMenu(self.export_menu)
        
        # Pop Out / Quick Export are added to the table button row below (like trj/dsd),
        # so all five buttons sit together under the table instead of being stretched
        # across the full window width. Wiring is unchanged - it is all by signal.

        # --- Right Panel (Table) ---
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0,0,0,0)
        
        # We need a vertical splitter for Plot Table (top) and Fit Table (bottom)
        self.right_splitter = QSplitter(Qt.Orientation.Vertical)
        
        # --- Top Table Container ---
        top_table_container = QWidget()
        top_table_layout = QVBoxLayout(top_table_container)
        top_table_layout.setContentsMargins(0,0,0,0)
        
        self.plot_table = QTableWidget()
        self.plot_table.setColumnCount(9)
        self.plot_table.setHorizontalHeaderLabels(["↕", "Plot", "Orig", "Mean", "Std", "", "Style", "thk", "Del"])
        header = self.plot_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(8, QHeaderView.ResizeMode.Fixed)
        self.plot_table.setColumnWidth(0, 10)
        self.plot_table.setColumnWidth(2, 35)
        self.plot_table.setColumnWidth(3, 45)
        self.plot_table.setColumnWidth(4, 35)
        self.plot_table.setColumnWidth(5, 10)
        self.plot_table.setColumnWidth(6, 35)
        self.plot_table.setColumnWidth(7, 25)
        self.plot_table.setColumnWidth(8, 25)
        self.plot_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.plot_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.plot_table.verticalHeader().hide()

        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(self._show_mean_header_context_menu)
        header.sectionClicked.connect(self._on_header_clicked)
        
        top_table_layout.addWidget(self.plot_table)

        # --- Fit Control Bar (Middle) - Defined BEFORE adding to layout ---
        self.fit_control_widget = QWidget()
        fit_control_layout = QHBoxLayout(self.fit_control_widget)
        fit_control_layout.setContentsMargins(0, 0, 0, 0)
        
        self.show_fit_btn = QPushButton("Start Curve Fitting")
        self.show_fit_btn.clicked.connect(self._toggle_fit_ui)
        
        self.add_fit_btn = QPushButton("Add Fit")
        self.add_fit_btn.clicked.connect(self.add_fit_row)
        self.hide_fit_btn = QPushButton("Hide Fitting Table")
        self.hide_fit_btn.clicked.connect(self._toggle_fit_ui)
        
        self.add_fit_btn.hide()
        self.hide_fit_btn.hide()
        
        fit_control_layout.addWidget(self.show_fit_btn)
        fit_control_layout.addWidget(self.add_fit_btn)
        fit_control_layout.addWidget(self.hide_fit_btn)
        
        top_table_layout.addWidget(self.fit_control_widget)

        # --- Table Buttons (Pop Out/Quick Export/Save/Load/Exit) ---
        self.save_btn = QPushButton("Save")
        self.load_btn = QPushButton("Load")
        self.exit_btn = QPushButton("Exit")
        table_buttons_layout = QHBoxLayout()
        table_buttons_layout.addWidget(self.popout_btn)
        table_buttons_layout.addWidget(self.export_btn)
        table_buttons_layout.addWidget(self.save_btn)
        table_buttons_layout.addWidget(self.load_btn)
        table_buttons_layout.addWidget(self.exit_btn)
        
        self.right_splitter.addWidget(top_table_container)

        # --- Fit Table (Bottom) ---
        self.fit_table_container = QWidget()
        fit_table_layout = QVBoxLayout(self.fit_table_container)
        fit_table_layout.setContentsMargins(0,0,0,0)
        
        self.fit_table = QTableWidget()
        self.fit_table.setColumnCount(9)
        self.fit_table.setHorizontalHeaderLabels(["↕", "Plot", "Type", "Fit Fun", "Err", "", "Style", "thk", "Del"])
        f_header = self.fit_table.horizontalHeader()
        f_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        f_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch) # Plot name
        f_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed) # Type (Previously 3)
        f_header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch) # Fit Fun (Previously 2)
        f_header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        f_header.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        f_header.setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
        f_header.setSectionResizeMode(7, QHeaderView.ResizeMode.Fixed)
        f_header.setSectionResizeMode(8, QHeaderView.ResizeMode.Fixed)
        
        self.fit_table.setColumnWidth(0, 10)
        self.fit_table.setColumnWidth(2, 55) # Width for Type
        self.fit_table.setColumnWidth(4, 50)
        self.fit_table.setColumnWidth(5, 10)
        self.fit_table.setColumnWidth(6, 35)
        self.fit_table.setColumnWidth(7, 25)
        self.fit_table.setColumnWidth(8, 25)
        self.fit_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.fit_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.fit_table.verticalHeader().hide()
        
        fit_table_layout.addWidget(self.fit_table)
        
        self.fit_table_container.hide()
        self.right_splitter.addWidget(self.fit_table_container)
        
        right_layout.addWidget(self.right_splitter)
        right_layout.addLayout(table_buttons_layout)

        main_splitter.addWidget(left_panel)
        main_splitter.addWidget(right_panel)
        main_splitter.setSizes([900, 500])
        main_layout.addWidget(main_splitter, 1)

    def _connect_signals(self):
        self.add_btn.clicked.connect(self.add_new_plot_row)
        self.load_btn.clicked.connect(self.load_session)
        # self.export_btn click is handled by its dropdown menu
        
        # Exit closes the whole application window
        self.exit_btn.clicked.connect(self.main_window.close)
        
        self.lock_axes_btn.toggled.connect(self._on_lock_axes_toggled)
        self.lock_axes_btn.rightClicked.connect(self._on_scale_lock_toggled)
        self.view_lock_btn.toggled.connect(self._on_view_lock_toggled)
        
        self.study_combo.currentTextChanged.connect(self.on_study_selected)
        self.system_combo.currentTextChanged.connect(self._on_system_combo_changed)
        # Use _handle_axis_change for axis combos to intercept "Custom" selection
        self.xaxis_combo.currentTextChanged.connect(lambda text: self._handle_axis_change(self.xaxis_combo, 'x_axis', text))
        self.yaxis_combo.currentTextChanged.connect(lambda text: self._handle_axis_change(self.yaxis_combo, 'y_axis', text))
        
        self.plot_table.itemSelectionChanged.connect(self.on_table_selection_changed)
        self.plot_table.cellDoubleClicked.connect(self._on_table_double_click)
        
        self.popout_btn.clicked.connect(self.launch_popout_window)

    def _on_system_combo_changed(self, text: str):
        # Manual pick → lock sequence number (real systems only, 0-based).
        if not self._is_internal_update and text and text != "Select System":
            study = self.study_combo.currentText()
            if study and study != "Select Study":
                try:
                    systems = self.data_manager.get_system_names(study)
                    if text in systems:
                        self._explicit_system_seq = systems.index(text)
                except Exception:
                    pass
        self._update_selected_row_name_component('system', text)

    def _toggle_fit_ui(self):
        self.fit_table_visible = not self.fit_table_visible
        
        if self.fit_table_visible:
            self.show_fit_btn.hide()
            self.add_fit_btn.show()
            self.hide_fit_btn.show()
            self.fit_table_container.show()
            
            if self.fit_table.rowCount() == 0:
                self.add_fit_row()

            # Update colors when showing fit table
            self._update_fit_row_colors()
        else:
            self.show_fit_btn.show()
            self.add_fit_btn.hide()
            self.hide_fit_btn.hide()
            self.fit_table_container.hide()
            
        self.update_plots()

    def _extract_fit_clone_data(self):
        """Reads the fit row a new one should be cloned from (selected, else last)."""
        clone_data = {}
        source_row_idx = -1

        selected_rows = self.fit_table.selectionModel().selectedRows()
        if selected_rows:
            source_row_idx = selected_rows[-1].row()
        elif self.fit_table.rowCount() > 0:
            source_row_idx = self.fit_table.rowCount() - 1

        if source_row_idx == -1:
            return clone_data

        try:
            w_source = self.fit_table.cellWidget(source_row_idx, 1)
            w_type = self.fit_table.cellWidget(source_row_idx, 2)
            # no w_color extraction to prevent copying manual colors
            w_style = self.fit_table.cellWidget(source_row_idx, 6)
            w_thk = self.fit_table.cellWidget(source_row_idx, 7)

            if w_source and w_type and w_style and w_thk:
                clone_data['source_text'] = w_source.currentText()
                # The source is bound by plot id; the text is only a fallback.
                clone_data['source_id'] = w_source.currentData()
                clone_data['type_text'] = w_type.currentText()
                clone_data['style'] = w_style.currentText()
                clone_data['thk'] = w_thk.findChild(QLineEdit).text()

                item_id = self.fit_table.item(source_row_idx, 0)
                if item_id:
                    src_fit_id = item_id.data(Qt.ItemDataRole.UserRole)
                    if src_fit_id in self.fit_dialogs:
                        clone_data['dialog_state'] = self.fit_dialogs[src_fit_id].get_state()
        except Exception as e:
            print(f"Clone extraction failed: {e}")

        return clone_data

    def add_fit_row(self, target_fit_id=None):
        # QPushButton.clicked emits 'False'. We must ignore this bool.
        if isinstance(target_fit_id, bool):
            target_fit_id = None

        # A target id only ever comes from the session loader, which sets every
        # field itself afterwards - neither cloning nor seeding applies there.
        is_restoring = target_fit_id is not None

        # --- 1. DECIDE WHAT THE NEW ROW STARTS FROM ---
        # Whichever table the user clicked last wins: a fit row gets cloned, a plot
        # row becomes the new fit's source (with its color and its x-range).
        clone_data = {}
        source_plot_row = -1

        if not is_restoring:
            origin = self._last_clicked_table
            if origin is None:
                # Nothing clicked yet: clone if there is a fit to clone, else adopt
                # the selected plot row (the case when the fit table is first shown).
                origin = 'fit' if self.fit_table.rowCount() > 0 else 'plot'

            if origin == 'plot':
                source_plot_row = self.plot_table.currentRow()

            if source_plot_row == -1:
                clone_data = self._extract_fit_clone_data()
        else:
            clone_data = self._extract_fit_clone_data()

        # --- 2. CREATE NEW ROW ---
        row = self.fit_table.rowCount()
        self.fit_table.insertRow(row)
        
        if target_fit_id is not None:
            fit_id = target_fit_id
        else:
            fit_id = self.next_fit_id
            self.next_fit_id += 1
        
        id_item = QTableWidgetItem()
        id_item.setData(Qt.ItemDataRole.UserRole, fit_id)
        self.fit_table.setItem(row, 0, id_item)
        
        self.fit_table.setCellWidget(row, 0, self._create_fit_move_widget())
        
        plot_combo = QComboBox()
        # Connect source change to auto-color logic
        plot_combo.currentIndexChanged.connect(self._on_fit_source_changed)
        plot_combo.currentIndexChanged.connect(self._on_fit_type_changed)
        self.fit_table.setCellWidget(row, 1, plot_combo)
        
        type_combo = QComboBox()
        type_combo.addItems(["Off", "Orig"])
        type_combo.setProperty("fit_id", fit_id)
        type_combo.currentIndexChanged.connect(self._on_fit_type_changed)
        self.fit_table.setCellWidget(row, 2, type_combo)

        fun_btn = QPushButton("a*x + b")
        fun_btn.setProperty("fit_id", fit_id)
        fun_btn.clicked.connect(self._on_fit_fun_clicked)
        self.fit_table.setCellWidget(row, 3, fun_btn)
        
        err_label = QLabel("N/A")
        err_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        err_label.setProperty("fit_id", fit_id)
        self.fit_table.setCellWidget(row, 4, err_label)
        
        # Color Button Logic
        color_btn = ColorButton(QColor("gray"))
        # Mark as NOT manually set initially (Auto-Coloring enabled)
        color_btn.setProperty("manually_set", False) 
        color_btn.colorChanged.connect(self._on_fit_color_changed_manually)
        self.fit_table.setCellWidget(row, 5, color_btn)
        
        style_combo = self._create_style_combo()
        style_combo.setCurrentText("--")
        style_combo.currentTextChanged.connect(self.update_plots)
        self.fit_table.setCellWidget(row, 6, style_combo)
        
        thk_edit = QLineEdit("1")
        thk_edit.setValidator(QIntValidator(1, 10))
        thk_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thk_edit.textChanged.connect(self.update_plots)
        self.fit_table.setCellWidget(row, 7, self._create_centered_widget(thk_edit))
        
        del_btn = QPushButton("X")
        del_btn.setStyleSheet("color: red; font-weight: bold;")
        del_btn.setProperty("fit_id", fit_id)
        del_btn.clicked.connect(self.delete_fit_row)
        self.fit_table.setCellWidget(row, 8, self._create_centered_widget(del_btn))
        
        dialog = FitFunctionDialog(self)
        dialog.fitUpdated.connect(lambda res, fid=fit_id: self._on_fit_updated(fid, res))
        self.fit_dialogs[fit_id] = dialog
        
        self._update_fit_source_combos()
        self._update_fit_type_combos()
        
        # --- 3. APPLY CLONED DATA ---
        if clone_data:
            plot_combo.blockSignals(True)
            type_combo.blockSignals(True)
            style_combo.blockSignals(True)
            thk_edit.blockSignals(True)
            
            try:
                if clone_data.get('source_id') is not None:
                    idx = plot_combo.findData(clone_data['source_id'])
                    if idx != -1:
                        plot_combo.setCurrentIndex(idx)
                elif 'source_text' in clone_data:
                    idx = plot_combo.findText(clone_data['source_text'])
                    if idx != -1:
                        plot_combo.setCurrentIndex(idx)

                if 'type_text' in clone_data:
                    idx = type_combo.findText(clone_data['type_text'])
                    if idx != -1:
                        type_combo.setCurrentIndex(idx)
                    else:
                        type_combo.setCurrentIndex(0)

                style_combo.setCurrentText(clone_data['style'])
                thk_edit.setText(clone_data['thk'])
                
                if 'dialog_state' in clone_data:
                    dialog.set_state(clone_data['dialog_state'])
                    f_text = dialog.func_input.text()
                    fun_btn.setText((f_text[:12] + "...") if len(f_text) > 12 else f_text)
            
            finally:
                plot_combo.blockSignals(False)
                type_combo.blockSignals(False)
                style_combo.blockSignals(False)
                thk_edit.blockSignals(False)
        else:
            type_combo.setCurrentIndex(0)

        # --- 3b. OR START FROM THE PLOT ROW THE USER CLICKED LAST ---
        if source_plot_row != -1:
            self._bind_fit_to_plot_row(plot_combo, source_plot_row, dialog)

        # The source is only final now, so the Type list ("Mean" offered or not)
        # has to be rebuilt against the row this fit actually points at.
        self._update_fit_type_combos()

        self.fit_table.clearSelection()
        self.fit_table.selectRow(row)
        
        # --- 4. EXPLICITLY REFRESH COLOR ---
        # Manually call helper to set color based on (cloned or default) source
        self._on_fit_source_changed_explicit(plot_combo)
        
        # Also ensure stylesheet is applied for the text color immediately
        self._update_fit_row_colors()

    def _restore_fit_source(self, combo, source_id, source_text):
        """Re-binds a loaded fit to its plot row: by id, else by the saved label."""
        if combo is None:
            return

        index = combo.findData(source_id) if source_id is not None else -1
        if index == -1 and source_text:
            index = combo.findText(source_text)

        if index == -1:
            # The plot row this fit was made for is not in the session any more.
            # Blank rather than silently adopting an unrelated row.
            combo.blockSignals(True)
            combo.setCurrentIndex(-1)
            combo.blockSignals(False)
            combo.setProperty("detached", True)
            return

        combo.setCurrentIndex(index)
        combo.setProperty("detached", False)

    def _get_plot_row_id(self, row: int):
        """The stable id of a plot row - what a fit's Plot column actually holds."""
        item = self.plot_table.item(row, 1)
        return item.data(Qt.ItemDataRole.UserRole + 1) if item else None

    def _get_plot_row_color(self, plot_id):
        """Colour of the plot row with this id, or None if it is gone."""
        if plot_id is None:
            return None
        for r in range(self.plot_table.rowCount()):
            if self._get_plot_row_id(r) == plot_id:
                color_btn = self.plot_table.cellWidget(r, 5)
                return color_btn.color() if color_btn else None
        return None

    def _get_plot_row_x_range(self, plot_id):
        """(min, max) of the x data last fetched for a plot row, or None.

        Only rows that were actually fetched are in the cache - a row that is
        hidden and feeds no fit has no data, and the fit dialog then keeps its
        own defaults and auto-ranges once data arrives.
        """
        cached = (self._plot_data_cache or {}).get(plot_id)
        if not cached:
            return None

        x = cached.get('x')
        if x is None or len(x) == 0:
            return None

        try:
            x_min = float(np.nanmin(x))
            x_max = float(np.nanmax(x))
        except (TypeError, ValueError):
            return None

        if not (np.isfinite(x_min) and np.isfinite(x_max)):
            return None
        return x_min, x_max

    def _bind_fit_to_plot_row(self, plot_combo, plot_row: int, dialog=None):
        """Points a fit row at a plot row and seeds the fit range from its data."""
        plot_id = self._get_plot_row_id(plot_row)
        index = plot_combo.findData(plot_id) if plot_id is not None else -1
        if index == -1:
            return

        plot_combo.blockSignals(True)
        plot_combo.setCurrentIndex(index)
        plot_combo.blockSignals(False)
        plot_combo.setProperty("detached", False)

        if dialog is not None:
            x_range = self._get_plot_row_x_range(plot_id)
            if x_range:
                dialog.min_spin.setValue(x_range[0])
                dialog.max_spin.setValue(x_range[1])

    def _on_fit_type_changed(self):
        """Handle changes in the Fit Type dropdown (Off/Orig/Mean)."""
        sender = self.sender()
        if not isinstance(sender, QComboBox): return
        
        fit_id = sender.property("fit_id")
        text = sender.currentText()
        
        # If turned OFF, reset the error label to "N/A"
        if text == "Off":
            for r in range(self.fit_table.rowCount()):
                item_id = self.fit_table.item(r, 0)
                if item_id and item_id.data(Qt.ItemDataRole.UserRole) == fit_id:
                    err_lbl = self.fit_table.cellWidget(r, 4)
                    if err_lbl:
                        err_lbl.setText("N/A")
                    break

        self.update_plots()

    def _on_fit_source_changed(self):
        """
        Called when the Source Plot combo changes.
        Updates the fit color to be 40% darker than the source plot's color,
        UNLESS the user has manually set the color previously.
        """
        sender = self.sender()
        if not isinstance(sender, QComboBox): return

        # A user-driven pick re-attaches a fit that was detached by a deletion.
        sender.setProperty("detached", False)

        # The redraw comes from _on_fit_type_changed, wired to the same signal.
        self._on_fit_source_changed_explicit(sender)

    def _on_fit_source_changed_explicit(self, combo):
        """Recolors the fit row from its source plot row (40% darker rule)."""
        # Find row for this combo. A cell widget is parented to the viewport, so
        # mapping its position would resolve to the top row, never to its own.
        row = -1
        for r in range(self.fit_table.rowCount()):
            if self.fit_table.cellWidget(r, 1) == combo:
                row = r
                break

        if row == -1: return

        # Check Manual flag
        color_btn = self.fit_table.cellWidget(row, 5)
        if color_btn.property("manually_set") is True: return

        source_color = self._get_plot_row_color(combo.currentData())

        if source_color:
            new_color = QColor(source_color)
            h, s, v, a = new_color.getHsv()
            new_color.setHsv(h, s, int(v * 0.6), a)
            
            color_btn.blockSignals(True)
            color_btn.set_color(new_color)
            color_btn.blockSignals(False)

    def _on_fit_color_changed_manually(self):
        """Marks the row's color as manually set."""
        sender = self.sender()
        if sender:
            sender.setProperty("manually_set", True)
            self._update_fit_row_colors() # Update dropdown text colors
            self.update_plots()
        
    def delete_fit_row(self):
        btn = self.sender()
        if not btn: return
        
        # Identify by the ID attached to the specific button clicked
        fit_id = btn.property("fit_id")
        
        # Find the row that visually holds this ID
        row_to_del = -1
        for r in range(self.fit_table.rowCount()):
            item = self.fit_table.item(r, 0)
            if item and item.data(Qt.ItemDataRole.UserRole) == fit_id:
                row_to_del = r
                break
        
        if row_to_del != -1:
            self.fit_table.removeRow(row_to_del)
        
        # Clean up memory
        if fit_id in self.fit_dialogs:
            self.fit_dialogs[fit_id].close()
            del self.fit_dialogs[fit_id]
        if fit_id in self.fit_results:
            del self.fit_results[fit_id]
            
        self.update_plots()

    def _create_fit_move_widget(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        up_btn = QPushButton("▲")
        down_btn = QPushButton("▼")
        
        # Connect buttons to movement slots
        up_btn.clicked.connect(self.move_fit_row_up)
        down_btn.clicked.connect(self.move_fit_row_down)
        
        layout.addWidget(up_btn)
        layout.addWidget(down_btn)
        return widget

    def move_fit_row_up(self):
        button = self.sender()
        if button:
            parent_widget = button.parentWidget()
            pos = parent_widget.mapTo(self.fit_table.viewport(), QPoint(0,0))
            row = self.fit_table.indexAt(pos).row()
            if row > 0:
                self.swap_fit_rows(row, row - 1)

    def move_fit_row_down(self):
        button = self.sender()
        if button:
            parent_widget = button.parentWidget()
            pos = parent_widget.mapTo(self.fit_table.viewport(), QPoint(0,0))
            row = self.fit_table.indexAt(pos).row()
            if row < self.fit_table.rowCount() - 1:
                self.swap_fit_rows(row, row + 1)

    def swap_fit_rows(self, r1, r2):
        # 1. Block Table Signals generally
        self.fit_table.blockSignals(True)
        
        # 2. Swap ID Items (The Data Source of Truth)
        item1 = self.fit_table.item(r1, 0)
        item2 = self.fit_table.item(r2, 0)
        id1 = item1.data(Qt.ItemDataRole.UserRole)
        id2 = item2.data(Qt.ItemDataRole.UserRole)
        item1.setData(Qt.ItemDataRole.UserRole, id2)
        item2.setData(Qt.ItemDataRole.UserRole, id1)

        # 3. Update Widget Properties (The Tags used for signal routing)
        # We must re-tag the widgets so they point to their new location's ID
        for r, fid in [(r1, id2), (r2, id1)]:
            # Type Combo
            w_type = self.fit_table.cellWidget(r, 2)
            if w_type: w_type.setProperty("fit_id", fid)
            
            # Fun Btn
            w_fun = self.fit_table.cellWidget(r, 3)
            if w_fun: w_fun.setProperty("fit_id", fid)
            
            # Err Lbl
            w_err = self.fit_table.cellWidget(r, 4)
            if w_err: w_err.setProperty("fit_id", fid)
            
            # Del Btn (inside container)
            container = self.fit_table.cellWidget(r, 8)
            if container:
                btn = container.findChild(QPushButton)
                if btn: btn.setProperty("fit_id", fid)

        # 4. Swap Visual Values with Signal Blocking
        def safe_swap_combo(col_idx):
            w1 = self.fit_table.cellWidget(r1, col_idx)
            w2 = self.fit_table.cellWidget(r2, col_idx)
            if not w1 or not w2: return
            
            t1, t2 = w1.currentText(), w2.currentText()
            
            w1.blockSignals(True)
            w2.blockSignals(True)
            w1.setCurrentText(t2)
            w2.setCurrentText(t1)
            w1.blockSignals(False)
            w2.blockSignals(False)

        # Plot Combo (Source): swapped by index, not by text. Both combos hold the
        # same option list, while a text swap would land on the wrong plot as soon
        # as two rows share a name - and would wipe a detached (blank) source.
        src1 = self.fit_table.cellWidget(r1, 1)
        src2 = self.fit_table.cellWidget(r2, 1)
        if src1 and src2:
            i1, i2 = src1.currentIndex(), src2.currentIndex()
            det1 = bool(src1.property("detached"))
            det2 = bool(src2.property("detached"))

            src1.blockSignals(True)
            src2.blockSignals(True)
            src1.setCurrentIndex(i2)
            src2.setCurrentIndex(i1)
            src1.blockSignals(False)
            src2.blockSignals(False)

            src1.setProperty("detached", det2)
            src2.setProperty("detached", det1)

        # Type Combo Swap with Safety Check
        w_type1 = self.fit_table.cellWidget(r1, 2)
        w_type2 = self.fit_table.cellWidget(r2, 2)
        if w_type1 and w_type2:
            t1, t2 = w_type1.currentText(), w_type2.currentText()
            
            w_type1.blockSignals(True)
            w_type2.blockSignals(True)
            
            # Ensure target combos HAVE the item we are about to set.
            # If we swap "Mean" into a combo that only has ["Off", "Orig"], setCurrentText fails.
            # We add it temporarily; update_plots will clean it up immediately after.
            if w_type1.findText(t2) == -1: w_type1.addItem(t2)
            if w_type2.findText(t1) == -1: w_type2.addItem(t1)
            
            w_type1.setCurrentText(t2)
            w_type2.setCurrentText(t1)
            
            w_type1.blockSignals(False)
            w_type2.blockSignals(False)

        safe_swap_combo(6) # Style Combo
        
        # Swap Button Text
        btn1 = self.fit_table.cellWidget(r1, 3)
        btn2 = self.fit_table.cellWidget(r2, 3)
        if btn1 and btn2:
            txt1, txt2 = btn1.text(), btn2.text()
            btn1.setText(txt2)
            btn2.setText(txt1)
        
        # Swap Error Label Text
        lbl1 = self.fit_table.cellWidget(r1, 4)
        lbl2 = self.fit_table.cellWidget(r2, 4)
        if lbl1 and lbl2:
            txt1, txt2 = lbl1.text(), lbl2.text()
            lbl1.setText(txt2)
            lbl2.setText(txt1)
        
        # Swap Colors (with the manual flag - otherwise a manual color lands on a
        # row that still auto-colours itself and is overwritten on the next refresh)
        col1 = self.fit_table.cellWidget(r1, 5)
        col2 = self.fit_table.cellWidget(r2, 5)
        if col1 and col2:
            c1, c2 = col1.color(), col2.color()
            m1 = bool(col1.property("manually_set"))
            m2 = bool(col2.property("manually_set"))
            col1.blockSignals(True)
            col2.blockSignals(True)
            col1.set_color(c2)
            col2.set_color(c1)
            col1.blockSignals(False)
            col2.blockSignals(False)
            col1.setProperty("manually_set", m2)
            col2.setProperty("manually_set", m1)
        
        # Swap Thickness
        thk1_w = self.fit_table.cellWidget(r1, 7)
        thk2_w = self.fit_table.cellWidget(r2, 7)
        if thk1_w and thk2_w:
            le1 = thk1_w.findChild(QLineEdit)
            le2 = thk2_w.findChild(QLineEdit)
            if le1 and le2:
                v1, v2 = le1.text(), le2.text()
                le1.blockSignals(True)
                le2.blockSignals(True)
                le1.setText(v2)
                le2.setText(v1)
                le1.blockSignals(False)
                le2.blockSignals(False)

        self.fit_table.blockSignals(False)
        self.update_plots()

    def _update_fit_source_combos(self):
        # 1. Gather all plot row options (Text + ID)
        options = []
        for r in range(self.plot_table.rowCount()):
            text = self._get_row_display_name(r)
            pid = self._get_plot_row_id(r)
            options.append((text, pid))

        for r in range(self.fit_table.rowCount()):
            combo = self.fit_table.cellWidget(r, 1)
            # Apply delegate if needed
            if not isinstance(combo.view().itemDelegate(), NoNewLineDelegate):
                combo.setItemDelegate(NoNewLineDelegate(combo))

            # Save current selection
            current_id = combo.currentData()
            current_text = combo.currentText()
            was_detached = bool(combo.property("detached"))

            combo.blockSignals(True)
            combo.clear()

            # Repopulate
            target_index = 0
            found_id = False

            for i, (text, pid) in enumerate(options):
                combo.addItem(text, pid) # pid stored in UserRole

                # Check match
                # Priority 1: Match by ID (robust to renaming and to duplicate names)
                if current_id is not None and pid == current_id:
                    target_index = i
                    found_id = True

            # Priority 2: Match by Text (fallback for loaded sessions or fresh rows)
            if not found_id and current_text:
                index = combo.findText(current_text)
                if index != -1:
                    target_index = index
                    found_id = True

            if was_detached and not found_id:
                # Source row is gone. Leave the cell blank rather than re-binding
                # the fit to whatever row now happens to sit first.
                combo.setCurrentIndex(-1)
            elif combo.count() > 0:
                combo.setCurrentIndex(target_index)
                combo.setProperty("detached", False)

            combo.blockSignals(False)
        
        # Update colors after repopulating
        self._update_fit_row_colors()

    def _on_fit_fun_clicked(self):
        btn = self.sender()
        fit_id = btn.property("fit_id")
        if fit_id in self.fit_dialogs:
            dialog = self.fit_dialogs[fit_id]
            dialog.show()
            dialog.raise_()
            dialog.activateWindow()

    def _on_fit_updated(self, fit_id, result):
        self.fit_results[fit_id] = result
        
        for r in range(self.fit_table.rowCount()):
            item_id = self.fit_table.item(r, 0)
            if not item_id: continue
            
            # Match purely by Integer ID
            if item_id.data(Qt.ItemDataRole.UserRole) == fit_id:
                # Handle Error Display
                # If status is Preview, show that instead of a number/fail
                if result.get('status') == 'Preview':
                    self.fit_table.cellWidget(r, 4).setText("Preview")
                else:
                    val = result.get('final_error', float('inf'))
                    err_lbl = self.fit_table.cellWidget(r, 4)
                    if err_lbl:
                        if val > 1e15:
                            err_lbl.setText("Fail")
                        else:
                            err_lbl.setText(f"{val:.2e}")
                
                # Update Button Text with formula
                func_str = "Fit..."
                if fit_id in self.fit_dialogs:
                    func_str = self.fit_dialogs[fit_id].func_input.text()
                
                btn = self.fit_table.cellWidget(r, 3)
                if btn:
                    display_text = (func_str[:12] + "...") if len(func_str) > 12 else func_str
                    btn.setText(display_text)
                break
        
        # Call update_plots with auto_trigger_fits=False to prevent recursive calculations
        # This will redraw the plot, picking up the new 'Preview' data from fit_results
        self.update_plots(auto_trigger_fits=False)

    # --- Public Interface for MainWindow ---
    
    def attach_mode_combo(self, combo_box):
        """Takes the floating Mode Combo and places it into the specific layout slot."""
        # We add it to the grid at 0,0 spanning 2 rows
        self.controls_layout.addWidget(combo_box, 0, 0, 2, 1)

    def _handle_axis_change(self, combo, component, text):
        """Handles changes in axis comboboxes, intercepting 'Custom' selection."""
        if text.strip().lower() == "custom":
            # Reset combo to previous valid text or placeholder to avoid loop if canceled
            # But we don't easily know previous text. 
            # The dialog handling will set the new text if saved.
            self._open_custom_dialog(combo)
            return
        
        self._update_selected_row_name_component(component, text)

    def _open_custom_dialog(self, triggering_combo):
        """Opens the custom property dialog."""
        # Temporarily block signals to prevent unwanted updates
        triggering_combo.blockSignals(True)
        
        try:
            dialog = CustomPropertyDialog(
                self.data_manager.get_all_column_names(),
                self.custom_properties,
                self,
                index_validator=self.data_manager.validate_formula_indices
            )
            if dialog.exec():
                # Update local properties
                self.custom_properties = dialog.get_properties()
                # Update data manager
                self.data_manager.set_custom_properties(self.custom_properties)
                
                # Refresh all axis combos with new properties
                self._refresh_axis_combos()
                
                # Check if user selected a property to use
                selected_prop = dialog.get_selected_property()
                if selected_prop:
                    triggering_combo.setCurrentText(selected_prop)
                    
                    # Safe way: Update the internal state manually
                    component = 'x_axis' if triggering_combo == self.xaxis_combo else 'y_axis'
                    self._update_selected_row_name_component(component, selected_prop)
                else:
                    # Revert to previous value from table to avoid stuck on "Custom"
                    self._revert_combo_selection(triggering_combo)
            else:
                # Revert to previous value from table
                self._revert_combo_selection(triggering_combo)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"An error occurred in the Custom Property Dialog:\n{e}")
            triggering_combo.setCurrentIndex(0)
        finally:
            triggering_combo.blockSignals(False)

    def _revert_combo_selection(self, combo):
        """Reverts the combo box selection to match the current table state."""
        if self.plot_table.currentRow() == -1:
            combo.setCurrentIndex(0)
            return

        selected_row = self.plot_table.currentRow()
        item = self.plot_table.item(selected_row, 1)
        if not item:
            combo.setCurrentIndex(0)
            return

        plot_name = item.data(Qt.ItemDataRole.UserRole) or item.text()
        _, _, x_ax, y_ax = self._parse_plot_name(plot_name)
        
        target_val = x_ax if combo == self.xaxis_combo else y_ax
        
        # Try to set text, fallback to index 0
        index = combo.findText(target_val)
        if index != -1:
            combo.setCurrentIndex(index)
        else:
            combo.setCurrentIndex(0)

    def _refresh_axis_combos(self):
        """Refreshes both axis combos with current columns and custom properties."""
        # Helper to preserve selection if possible
        def refresh(combo):
            current = combo.currentText()
            self._populate_axis_combo(combo, self.data_manager.get_all_column_names())
            if combo.findText(current) != -1:
                combo.setCurrentText(current)
            else:
                combo.setCurrentIndex(0)

        # We need to temporarily disconnect signals or block them to avoid triggering updates during refresh
        # IMPORTANT: Use the return value of blockSignals to restore the PREVIOUS state.
        # This prevents unblocking a combo that was blocked by _open_custom_dialog.
        
        was_blocked_x = self.xaxis_combo.blockSignals(True)
        was_blocked_y = self.yaxis_combo.blockSignals(True)
        
        try:
            refresh(self.xaxis_combo)
            refresh(self.yaxis_combo)
        finally:
            self.xaxis_combo.blockSignals(was_blocked_x)
            self.yaxis_combo.blockSignals(was_blocked_y)

    def _populate_axis_combo(self, combo: QComboBox, items: list):
        """Populates axis combo with standard items, 'Custom', and custom properties."""
        # Save previous state
        was_blocked = combo.blockSignals(True)
        try:
            combo.clear()
            
            # Standard placeholder
            placeholder = "Select Axis"
            combo.addItem(placeholder)
            
            # Standard Items
            combo.addItems(items)
            
            # Separator
            combo.insertSeparator(combo.count())
            
            # Custom Option (Bold)
            combo.addItem("Custom")
            custom_idx = combo.count() - 1
            model = combo.model()
            model.setData(model.index(custom_idx, 0), QFont(combo.font().family(), -1, QFont.Weight.Bold), Qt.ItemDataRole.FontRole)
            
            # Custom Properties (Italic)
            for prop_name in self.custom_properties:
                combo.addItem(prop_name)
                idx = combo.count() - 1
                font = QFont()
                font.setItalic(True)
                model.setData(model.index(idx, 0), font, Qt.ItemDataRole.FontRole)
        finally:
            # Restore previous state
            combo.blockSignals(was_blocked)

    def _revert_combo_selection(self, combo):
        """Reverts the combo box selection to match the current table state."""
        if self.plot_table.currentRow() == -1:
            combo.setCurrentIndex(0)
            return

        selected_row = self.plot_table.currentRow()
        item = self.plot_table.item(selected_row, 1)
        if not item:
            combo.setCurrentIndex(0)
            return

        plot_name = item.data(Qt.ItemDataRole.UserRole) or item.text()
        _, _, x_ax, y_ax = self._parse_plot_name(plot_name)
        
        target_val = x_ax if combo == self.xaxis_combo else y_ax
        
        # Try to set text, fallback to index 0
        index = combo.findText(target_val)
        if index != -1:
            combo.setCurrentIndex(index)
        else:
            combo.setCurrentIndex(0)

    def on_keywords_changed(self, keywords):
        # Just reload if path exists
        paths = [p for p in self.main_window.get_project_paths() if os.path.exists(p)]
        if paths:
            self.load_project(paths, keywords)

    def save_state_for_exit(self):
        # Called by MainWindow closeEvent
        self._save_state_on_exit()

    def _create_move_widget(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        up_button = QPushButton("▲")
        up_button.setObjectName("up_button")
        down_button = QPushButton("▼")
        down_button.setObjectName("down_button")
        
        up_button.clicked.connect(self.move_row_up)
        down_button.clicked.connect(self.move_row_down)

        layout.addWidget(up_button)
        layout.addWidget(down_button)
        return widget

    def move_row_up(self):
        button = self.sender()
        if button:
            parent_widget = button.parentWidget()
            pos = parent_widget.mapTo(self.plot_table.viewport(), QPoint(0,0))
            row = self.plot_table.indexAt(pos).row()
            if row > 0:
                self.swap_rows(row, row - 1)
                self.plot_table.selectRow(row - 1)

    def move_row_down(self):
        button = self.sender()
        if button:
            parent_widget = button.parentWidget()
            pos = parent_widget.mapTo(self.plot_table.viewport(), QPoint(0,0))
            row = self.plot_table.indexAt(pos).row()
            if row < self.plot_table.rowCount() - 1:
                self.swap_rows(row, row + 1)
                self.plot_table.selectRow(row + 1)

    def _update_move_buttons_visibility(self):
        rowCount = self.plot_table.rowCount()
        for row in range(rowCount):
            move_widget = self.plot_table.cellWidget(row, 0)
            if move_widget:
                up_button = move_widget.findChild(QPushButton, "up_button")
                down_button = move_widget.findChild(QPushButton, "down_button")

                if up_button and down_button:
                    up_button.setEnabled(row > 0)
                    down_button.setEnabled(row < rowCount - 1)

    @staticmethod
    def _shorten_labels(labels: list[str]) -> list[str]:
        if not labels or len(labels) < 2:
            return labels

        # Filter out 'average' labels from the logic but keep track of them
        labels_to_process = [lbl for lbl in labels if lbl != 'average']
        if len(labels_to_process) < 2:
            return labels # Not enough labels to find commonalities

        split_pattern = r"([-_ ])"
        split_labels_to_process = [re.split(split_pattern, label) for label in labels_to_process]

        if not all(split_labels_to_process):
            return labels

        min_len = min(len(s) for s in split_labels_to_process)
        common_parts_indices = []
        for i in range(min_len):
            if i % 2 == 0:  # Only check non-delimiter parts
                first_part = split_labels_to_process[0][i]
                if all(s[i] == first_part for s in split_labels_to_process):
                    common_parts_indices.append(i)

        if not common_parts_indices:
            return labels

        if len(split_labels_to_process[0]) == 1 and len(split_labels_to_process[0][0]) > 5 and 0 in common_parts_indices:
            part = split_labels_to_process[0][0]
            # Create a map for shortened names
            shortened_map = {lbl: f"{part[:3]}..." for lbl in labels_to_process}
            return [shortened_map.get(lbl, lbl) for lbl in labels]

        # Apply "..." to a copy of the lists
        processed_splits = [list(s) for s in split_labels_to_process]
        for i in common_parts_indices:
            for s in processed_splits:
                if i < len(s):
                    s[i] = "..."
        
        # Reconstruct the shortened labels
        shortened_labels = []
        for s in processed_splits:
            result = []
            i = 0
            while i < len(s):
                part = s[i]
                if part == "...":
                    result.append("...")
                    while i + 2 < len(s) and s[i+1] in "-_ " and s[i+2] == "...":
                        i += 2
                else:
                    result.append(part)
                i += 1
            shortened_labels.append("".join(result))
        
        # Create a mapping from original to shortened
        shortened_map = dict(zip(labels_to_process, shortened_labels))
        
        # Return the final list, preserving 'average' labels
        return [shortened_map.get(lbl, lbl) for lbl in labels]


    def _update_plot_labels(self):
        rowCount = self.plot_table.rowCount()
        if rowCount == 0:
            return

        # Restore original names if only one row
        if rowCount < 2:
            for row in range(rowCount):
                item = self.plot_table.item(row, 1)
                if item and item.data(Qt.ItemDataRole.UserRole):
                    item.setText(item.data(Qt.ItemDataRole.UserRole))
            return

        full_names = []
        for row in range(rowCount):
            item = self.plot_table.item(row, 1)
            if item:
                full_name = item.data(Qt.ItemDataRole.UserRole) or item.text()
                full_names.append(full_name)
        
        if not full_names: return

        studies = [name.split(' | ')[0] for name in full_names]
        systems = [name.split(' | ')[1] for name in full_names]
        
        shortened_studies = LogPlotPanel._shorten_labels(studies)
        shortened_systems = LogPlotPanel._shorten_labels(systems)

        for row in range(rowCount):
            item = self.plot_table.item(row, 1)
            if item and row < len(full_names):
                parts = full_names[row].split(' | ')
                if len(parts) == 4:
                    short_study = shortened_studies[row] if row < len(shortened_studies) else parts[0]
                    
                    # Get the shortened system name, and handle 'average' -> 'ave'
                    system_name = parts[1]
                    if system_name == 'average':
                        short_system = 'ave'
                    elif system_name == 'average & std':
                        short_system = 'a&sd'
                    else:
                        short_system = shortened_systems[row] if row < len(shortened_systems) else system_name

                    new_name = f"{short_study} | {short_system} | {parts[2]} | {parts[3]}"
                    item.setText(new_name)

        # Update fit colors after label changes
        self._update_fit_row_colors()

    def _extract_row_data(self, row_index):
        data = {}
        item = self.plot_table.item(row_index, 1)
        # Column 1: Plot name (QTableWidgetItem)
        data['plot_name'] = item.data(Qt.ItemDataRole.UserRole) or item.text() if item else "N/A | N/A | N/A | N/A"
        # Extract ID (UserRole + 1) and the path this row's study came from (UserRole + 2)
        if item:
            data['plot_id'] = item.data(Qt.ItemDataRole.UserRole + 1)
            data['origin'] = item.data(Qt.ItemDataRole.UserRole + 2)

        # Column 2: Show (QCheckBox)
        show_widget = self.plot_table.cellWidget(row_index, 2)
        data['show'] = show_widget.findChild(QCheckBox).isChecked() if show_widget else True
        
        # Column 3: Mean (QLineEdit)
        mean_edit = self.plot_table.cellWidget(row_index, 3)
        data['mean'] = mean_edit.findChild(QLineEdit).text() if mean_edit else "0"

        # Column 4: Std (QCheckBox)
        data['std'] = self.plot_table.cellWidget(row_index, 4).findChild(QCheckBox).isChecked()

        # Column 5: Color (ColorButton)
        color_btn = self.plot_table.cellWidget(row_index, 5)
        data['color'] = color_btn.color().name() if color_btn else QColor("black").name()
        
        # Column 6: Style (QComboBox)
        style_combo = self.plot_table.cellWidget(row_index, 6)
        data['style'] = style_combo.currentText() if style_combo else "Solid"
        
        # Column 7: Thickness (QLineEdit)
        thk_edit = self.plot_table.cellWidget(row_index, 7)
        data['thickness'] = thk_edit.findChild(QLineEdit).text() if thk_edit else "1"

        return data

    def _populate_row_data(self, row_index, data):
        # Column 0: Move buttons
        move_widget = self._create_move_widget()
        self.plot_table.setCellWidget(row_index, 0, move_widget)

        # Column 1: Plot name
        name_item = QTableWidgetItem(data['plot_name'])
        name_item.setData(Qt.ItemDataRole.UserRole, data['plot_name'])
        # Every row needs an id: the fit table binds its source to it, not to the
        # displayed name (names repeat as soon as a row is duplicated).
        plot_id = data.get('plot_id')
        if plot_id is None:
            plot_id = self.next_plot_id
            self.next_plot_id += 1
        name_item.setData(Qt.ItemDataRole.UserRole + 1, plot_id)
        if data.get('origin'):
            name_item.setData(Qt.ItemDataRole.UserRole + 2, data['origin'])
        self.plot_table.setItem(row_index, 1, name_item)

        # Column 2: Show
        show_check = QCheckBox()
        show_check.setChecked(data.get('show', True))
        show_check.stateChanged.connect(lambda state, row=row_index, col=2: self._handle_table_widget_change(row, col))
        self.plot_table.setCellWidget(row_index, 2, self._create_centered_widget(show_check))

        # Column 3: Mean
        mean_edit = QLineEdit(data.get('mean', "0"))
        mean_edit.setValidator(QIntValidator(0, 999999))
        mean_edit.textChanged.connect(lambda text, row=row_index, col=3: self._handle_table_widget_change(row, col))
        self.plot_table.setCellWidget(row_index, 3, self._create_centered_widget(mean_edit))

        # Column 4: Std
        std_check = QCheckBox()
        std_check.setChecked(data.get('std', False))
        std_check.stateChanged.connect(lambda state, row=row_index, col=4: self._handle_table_widget_change(row, col))
        self.plot_table.setCellWidget(row_index, 4, self._create_centered_widget(std_check))

        # Column 5: Color
        color_btn = ColorButton(QColor(data['color']))
        color_btn.colorChanged.connect(lambda color, row=row_index, col=5: self._handle_table_widget_change(row, col))
        self.plot_table.setCellWidget(row_index, 5, color_btn)

        # Column 6: Style
        style_combo = self._create_style_combo()
        style_combo.setCurrentText(data['style'])
        style_combo.currentTextChanged.connect(lambda text, row=row_index, col=6: self._handle_table_widget_change(row, col))
        self.plot_table.setCellWidget(row_index, 6, style_combo)

        # Column 7: Thickness
        thk_edit = QLineEdit(data['thickness'])
        thk_edit.textChanged.connect(lambda text, row=row_index, col=7: self._handle_table_widget_change(row, col))
        self.plot_table.setCellWidget(row_index, 7, self._create_centered_widget(thk_edit))

        # Column 8: Delete button
        del_btn = QPushButton("X")
        del_btn.setStyleSheet("color: red; font-weight: bold;")
        del_btn.clicked.connect(self.delete_plot_row)
        self.plot_table.setCellWidget(row_index, 8, self._create_centered_widget(del_btn))
        
        self._update_row_display(row_index)
        self._update_row_visual_state(row_index) # Set initial color

    def swap_rows(self, r1, r2):
        self.plot_table.blockSignals(True)

        row1_data = self._extract_row_data(r1)
        row2_data = self._extract_row_data(r2)

        self._populate_row_data(r1, row2_data)
        self._populate_row_data(r2, row1_data)

        self.plot_table.blockSignals(False)
        self.update_plots()
        self._update_move_buttons_visibility()
        self._update_plot_labels()

    def _create_combo(self, placeholder: str) -> QComboBox:
        combo = QComboBox()
        combo.addItem(placeholder)
        combo.setCurrentIndex(0)
        return combo

    def _resync_rows_to_paths(self, paths):
        """Re-points rows at the current study keys and drops orphaned ones.

        Study keys gain or lose their path prefix as chips come and go, so a row's
        stable identity is the (source path, raw study folder) pair kept in UserRole+2.
        Rows whose path chip is gone are removed; everything else is re-pointed.
        """
        origins = getattr(self, 'study_origins', None) or {}

        for row in range(self.plot_table.rowCount()):
            item = self.plot_table.item(row, 1)
            if not item:
                continue

            name = item.data(Qt.ItemDataRole.UserRole) or item.text()
            study, system, x_axis, y_axis = self._parse_plot_name(name)
            origin = item.data(Qt.ItemDataRole.UserRole + 2)

            if not origin:
                # Row from a single-path session: adopt the origin of its study key.
                found = origins.get(study)
                if found:
                    item.setData(Qt.ItemDataRole.UserRole + 2, found)
                continue

            new_key = LogParser.qualify_study(origins, origin['path'], origin['study'])
            if new_key and new_key != study:
                item.setData(Qt.ItemDataRole.UserRole,
                             self._build_plot_name(new_key, system, x_axis, y_axis))
                self._update_row_display(row)

        self._drop_rows_of_unloaded_paths(paths)

    def _drop_rows_of_unloaded_paths(self, paths):
        """Removes the rows of paths whose chip is gone, then rebuilds the survivors.

        The rebuild is the point: every per-row cell widget captures its row index in
        its signal handlers, so a bare removeRow() would leave each row below it wired
        to the wrong index.
        """
        loaded = {LogParser.path_key(p) for p in paths}
        survivors, dropped_ids = [], []
        for row in range(self.plot_table.rowCount()):
            item = self.plot_table.item(row, 1)
            origin = item.data(Qt.ItemDataRole.UserRole + 2) if item else None
            source = (origin or {}).get('path')
            if source is None or LogParser.path_key(source) in loaded:
                survivors.append(self._extract_row_data(row))
            else:
                dropped_ids.append(item.data(Qt.ItemDataRole.UserRole + 1))

        if not dropped_ids:
            return

        for plot_id in dropped_ids:
            self._detach_fits_from_plot(plot_id)

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

        self._update_all_row_displays()
        self._update_move_buttons_visibility()

        if self.plot_table.rowCount() == 0:
            self.add_new_plot_row()
        else:
            self.plot_table.selectRow(min(max(selected, 0), self.plot_table.rowCount() - 1))

    def _parse_plot_name(self, plot_name: str) -> tuple:
        """Safely parse plot name into 4 components: study, system, x_axis, y_axis."""
        parts = plot_name.split(' | ')
        if len(parts) >= 4:
            return parts[0], parts[1], parts[2], parts[3]
        # Fallback for malformed names
        return (parts + ['N/A', 'N/A', 'N/A', 'N/A'])[:4]

    def _get_row_display_name(self, row: int) -> str:
        """Get the display name for a row, using global custom labels if set."""
        item = self.plot_table.item(row, 1)
        if not item:
            return "N/A | N/A | N/A | N/A"
        
        full_name = item.data(Qt.ItemDataRole.UserRole) or item.text()
        study, system, x_prop, y_prop = self._parse_plot_name(full_name)
        
        # Use global labels if set, otherwise use properties
        x_display = self.global_label_map.get(x_prop, x_prop)
        y_display = self.global_label_map.get(y_prop, y_prop)
        
        return f"{study} | {system} | {x_display} | {y_display}"

    def _update_row_display(self, row: int):
        """Update the displayed text in the table for a row."""
        item = self.plot_table.item(row, 1)
        if not item:
            return
        
        display_name = self._get_row_display_name(row)
        item.setText(display_name)
        item.setToolTip("") # Clear old tooltips

    def _update_all_row_displays(self):
        """Update the display text for all rows in the table."""
        for row in range(self.plot_table.rowCount()):
            self._update_row_display(row)

    def _get_all_unique_properties(self) -> list[str]:
        """Scans the table and returns a list of unique property names used in axes."""
        properties = set()
        for row in range(self.plot_table.rowCount()):
            item = self.plot_table.item(row, 1)
            if not item:
                continue
            
            full_name = item.data(Qt.ItemDataRole.UserRole) or item.text()
            _, _, x_prop, y_prop = self._parse_plot_name(full_name)
            
            if x_prop and x_prop != "N/A":
                properties.add(x_prop)
            if y_prop and y_prop != "N/A":
                properties.add(y_prop)
        return list(properties)

    def _edit_global_labels(self):
        """Open a dialog to edit global property labels."""
        unique_properties = self._get_all_unique_properties()
        if not unique_properties:
            QMessageBox.information(self, "No Properties", 
                                  "No plot properties found to label. Add plots first.")
            return

        dialog = GlobalLabelEditorDialog(unique_properties, self.global_label_map, self)
        if dialog.exec():
            self.global_label_map = dialog.get_updated_map()
            self._update_all_row_displays()
            self.update_plots()
            self._update_plot_labels()

    def _build_plot_name(self, study: str, system: str, x_axis: str, y_axis: str) -> str:
        """Build plot name from components."""
        return f"{study} | {system} | {x_axis} | {y_axis}"

    def _get_average_system_options(self, systems: list[str]) -> list[str]:
        """Return synthetic system options for multi-system studies."""
        if len(systems) > 1:
            return ["average & std", "average"]
        return []

    @staticmethod
    def _is_average_system(system: str) -> bool:
        return system in {"average", "average & std"}

    @staticmethod
    def _uses_forced_average_std(system: str) -> bool:
        return system == "average & std"

    def _update_selected_row_name_component(self, component: str, value: str, refresh: bool = True):
        """Update only a specific component of the plot name for ALL selected rows."""
        if self._is_internal_update:
            return

        # Handle "Custom" interception
        if value and value.strip().lower() == "custom":
            combo = None
            if component == 'x_axis': combo = self.xaxis_combo
            elif component == 'y_axis': combo = self.yaxis_combo
            
            if combo:
                self._open_custom_dialog(combo)
                return

        # 1. Get Selected Rows
        # Use selectionModel to correctly capture multi-selection even if focus is elsewhere
        selected_model_rows = self.plot_table.selectionModel().selectedRows()
        rows_to_update = [idx.row() for idx in selected_model_rows]
        
        # Fallback: If no selection model rows, use the current active row
        if not rows_to_update:
            current = self.plot_table.currentRow()
            if current != -1:
                rows_to_update = [current]
        
        # Deduplicate
        rows_to_update = list(set(rows_to_update))
        
        if not rows_to_update:
            return

        # 2. Iterate and Apply with Validity Checks
        for row in rows_to_update:
            plot_name_item = self.plot_table.item(row, 1)
            if not plot_name_item:
                continue
            
            # Parse current name
            current_name = plot_name_item.data(Qt.ItemDataRole.UserRole) or plot_name_item.text()
            study, system, x_axis, y_axis = self._parse_plot_name(current_name)
            
            new_study, new_system, new_x, new_y = study, system, x_axis, y_axis
            should_update = True
            
            if component == 'study':
                if value and value != "Select Study":
                    new_study = value
                    # Remember which path chip this study came from. The key itself is
                    # not stable - it gains a prefix as soon as another loaded path
                    # contributes a study folder of the same name.
                    plot_name_item.setData(Qt.ItemDataRole.UserRole + 2,
                                           (self.study_origins or {}).get(value))
                    # Note: Changing study might invalidate the current System.
                    # We keep the old system string; if invalid, data fetching handles it (returns None).
                    
            elif component == 'system':
                if value and value != "Select System":
                    # CHECK VALIDITY: Does this System exist in the row's Study?
                    # If 'value' is not available for this row's study, SKIP this row.
                    available_systems = self.data_manager.get_system_names(study)

                    allowed_values = set(available_systems)
                    allowed_values.update(self._get_average_system_options(available_systems))

                    if value in allowed_values:
                        new_system = value
                    else:
                        should_update = False # Skip unavailable property

            elif component == 'x_axis':
                if value and value != "Select X-Axis":
                    # We assume global column availability for simplicity, or could check specific study
                    # Generally if it's in the dropdown, it's valid for the project.
                    new_x = value

            elif component == 'y_axis':
                if value and value != "Select Y-Axis":
                    new_y = value
            
            if should_update:
                new_name = self._build_plot_name(new_study, new_system, new_x, new_y)
                plot_name_item.setData(Qt.ItemDataRole.UserRole, new_name)
                
                # Update visual text and state
                self._update_row_display(row)
                self._update_row_visual_state(row)

        if refresh:
            self._finish_row_name_update()

    def _finish_row_name_update(self):
        self.update_plots()
        self._update_plot_labels()
        # Global fitting handled inside update_plots (as before via apply_current_locks).

    def _connect_signals(self):
        self.add_btn.clicked.connect(self.add_new_plot_row)
        self.save_btn.clicked.connect(self.save_session)
        self.load_btn.clicked.connect(self.load_session)
        # self.export_btn click is handled by its dropdown menu
        
        # Exit closes the whole application window
        self.exit_btn.clicked.connect(self.main_window.close)
        
        self.lock_axes_btn.toggled.connect(self._on_lock_axes_toggled)
        self.lock_axes_btn.rightClicked.connect(self._on_scale_lock_toggled)
        self.view_lock_btn.toggled.connect(self._on_view_lock_toggled)
        
        self.study_combo.currentTextChanged.connect(self.on_study_selected)
        self.system_combo.currentTextChanged.connect(self.on_system_selected)
        self.xaxis_combo.currentTextChanged.connect(lambda text: self._update_selected_row_name_component('x_axis', text))
        self.yaxis_combo.currentTextChanged.connect(lambda text: self._update_selected_row_name_component('y_axis', text))
        
        self.plot_table.itemSelectionChanged.connect(self.on_table_selection_changed)
        self.plot_table.cellDoubleClicked.connect(self._on_table_double_click)

        # clicked() only fires on real mouse activation, so programmatic selectRow
        # calls (adding a row, restoring a session) never move the "last clicked" mark.
        self.plot_table.clicked.connect(lambda _idx: self._note_table_click('plot'))
        self.fit_table.clicked.connect(lambda _idx: self._note_table_click('fit'))

        self.popout_btn.clicked.connect(self.launch_popout_window)

    def _note_table_click(self, which: str):
        self._last_clicked_table = which

    def _on_table_double_click(self, row: int, column: int):
        """Handle double-click on table row to edit labels."""
        self._edit_global_labels()

    def _is_single_axis_lock_mode(self):
        return len(self.plot_controller.y_axes) == 1

    def _is_multi_axis_lock_mode(self):
        return len(self.plot_controller.y_axes) > 1

    def _is_multi_axis_lock_active(self):
        return self._is_multi_axis_lock_mode() and (self.plot_controller.axes_locked or self.scale_lock_enabled)

    def _schedule_update_views(self):
        if self._update_views_pending or not hasattr(self, 'plot_controller') or not self.plot_controller:
            return
        if len(getattr(self.plot_controller, 'y_axes', {})) <= 1:
            return
        self._update_views_pending = True
        QTimer.singleShot(0, self._do_update_views)

    def _do_update_views(self):
        self._update_views_pending = False
        try:
            if hasattr(self, 'plot_controller') and self.plot_controller:
                self.plot_controller.update_views()
        except Exception:
            pass

    def _update_lock_button_position(self):
        if not hasattr(self, 'plot_widget'):
            return
        right_x = max(0, self.plot_widget.width() - 40)
        if hasattr(self, 'lock_axes_btn'):
            self.lock_axes_btn.setGeometry(right_x, 0, 40, 30)
        if hasattr(self, 'view_lock_btn'):
            view_x = 0 if self._is_multi_axis_lock_mode() else right_x
            self.view_lock_btn.setGeometry(view_x, 0, 40, 30)

    def _set_button_checked_silent(self, button, checked):
        old_block = button.blockSignals(True)
        button.setChecked(checked)
        button.blockSignals(old_block)

    def _set_lock_button_checked_silent(self, checked):
        self._set_button_checked_silent(self.lock_axes_btn, checked)

    def _set_view_lock_button_checked_silent(self, checked):
        if hasattr(self, 'view_lock_btn'):
            self._set_button_checked_silent(self.view_lock_btn, checked)

    def _capture_single_axis_view_range(self):
        self.single_axis_view_ranges = copy.deepcopy(self.plot_controller.get_view_ranges())

    def _restore_preserved_view_ranges(self, ranges, single_axis_ranges=None):
        """Restore plot ranges after a metadata refresh without updating lock state."""
        if not ranges:
            return

        old_suppress = self._suppress_single_axis_range_capture
        self._suppress_single_axis_range_capture = True
        try:
            self.plot_controller.set_view_ranges(ranges)
            if self.single_axis_view_lock_enabled:
                self.single_axis_view_ranges = copy.deepcopy(
                    single_axis_ranges if single_axis_ranges is not None else ranges
                )
        finally:
            self._suppress_single_axis_range_capture = old_suppress

    def _apply_single_axis_view_range(self):
        if not self.single_axis_view_ranges:
            return
        self._applying_single_axis_lock = True
        try:
            if isinstance(self.single_axis_view_ranges, dict):
                self.plot_controller.set_view_ranges(self.single_axis_view_ranges)
            else:
                xr, yr = self.single_axis_view_ranges
                vb = self.plot_widget.getPlotItem().getViewBox()
                vb.setRange(xRange=xr, yRange=yr, padding=0)
        finally:
            self._applying_single_axis_lock = False

    def _on_single_axis_view_range_changed(self, *_):
        if (
            self.single_axis_view_lock_enabled
            and not self._applying_single_axis_lock
            and not self._suppress_single_axis_range_capture
        ):
            self._capture_single_axis_view_range()

    def _update_lock_button_visuals(self):
        """Update separate view-lock and multi-axis alignment/scale lock buttons."""
        has_axes = len(self.plot_controller.y_axes) > 0
        is_multi_axis = self._is_multi_axis_lock_mode()

        if hasattr(self, 'view_lock_btn'):
            self._set_view_lock_button_checked_silent(self.single_axis_view_lock_enabled)
            self.view_lock_btn.setText("\U0001f512" if self.single_axis_view_lock_enabled else "\U0001f513")
            self.view_lock_btn.setToolTip("Left Click: Lock/Unlock View Limits")
            self.view_lock_btn.setStyleSheet("")
            if hasattr(self.view_lock_btn, "set_checked_background_color"):
                self.view_lock_btn.set_checked_background_color(None)
            self.view_lock_btn.setVisible(has_axes)

        self.lock_axes_btn.setVisible(is_multi_axis)
        self._set_lock_button_checked_silent(self.plot_controller.axes_locked)
        text = "\U0001f512" if self.plot_controller.axes_locked else "\U0001f513"
        if self.scale_lock_enabled:
            text += " \u2195"
        self.lock_axes_btn.setToolTip("Left Click: Lock/Unlock 0-alignment\nRight Click: Lock/Unlock Scaling")
        self.lock_axes_btn.setText(text)

        red_active = is_multi_axis and (self.plot_controller.axes_locked or self.scale_lock_enabled)
        if hasattr(self.lock_axes_btn, "set_checked_background_color"):
            self.lock_axes_btn.setStyleSheet("")
            self.lock_axes_btn.set_checked_background_color(QColor("#c62828") if red_active else None)
        elif red_active:
            self.lock_axes_btn.setStyleSheet("QPushButton:checked { background-color: #c62828; }")
        else:
            self.lock_axes_btn.setStyleSheet("")

        self._update_lock_button_position()

    def _on_view_lock_toggled(self, checked):
        """Handle the independent view-limit lock button."""
        self.single_axis_view_lock_enabled = checked
        if checked:
            self._capture_single_axis_view_range()
        else:
            self.single_axis_view_ranges = None
            self.plot_controller.reset_view()
        self._update_lock_button_visuals()

    def _on_lock_axes_toggled(self, checked):
        """Handle multi-axis 0-alignment lock."""
        if not self._is_multi_axis_lock_mode():
            self._update_lock_button_visuals()
            return

        self.plot_controller.toggle_axes_lock(checked, reset_view=False)
        if checked:
            self.plot_controller.align_zero_preserve_scale()
            if self.single_axis_view_lock_enabled:
                self._capture_single_axis_view_range()
        self._update_lock_button_visuals()
        self._schedule_update_views()

    def _on_scale_lock_toggled(self):
        """Handle multi-axis scale lock."""
        if not self._is_multi_axis_lock_mode():
            return
        self.scale_lock_enabled = not self.scale_lock_enabled
        self.plot_controller.toggle_scale_lock(self.scale_lock_enabled)
        self._schedule_update_views()
        if self.single_axis_view_lock_enabled:
            self._capture_single_axis_view_range()
        self._update_lock_button_visuals()

    def _show_mean_header_context_menu(self, pos):
        header = self.plot_table.horizontalHeader()
        column_index = header.logicalIndexAt(pos)
        
        # Check if it's the 'Mean' column header (index 3)
        if column_index != 3:
            return

        menu = QMenu(self)
        group = QActionGroup(self)
        group.setExclusive(True)

        options = [
            ("Moving Averages", None),
            ("Valid Window (Exclude Borders)", "valid_window"),
            ("Symmetric Window (Shrink Borders)", "symmetric_window"),
            ("Asymmetric Window (Use All Available)", "asymmetric_window"),
            ("Advanced Methods", None),
            ("Gaussian Filter", "gaussian"),
            ("Savitzky-Golay (Order 2)", "savgol_2"),
            ("Savitzky-Golay (Order 3)", "savgol_3"),
            ("Savitzky-Golay (Order 4)", "savgol_4"),
            ("B-Splines (Smoothing Splines)", "bspline"),
            ("Exponential Moving Average (EMA)", "ema"),
            ("Data Manipulation", None),
            ("Enforce Start at 0", "zero_start"),
            (f"Quantize ({self.quantize_points} pts)" if self.quantize_points else "Quantize", "quantize"),
            ("Averaging Order", None),
            ("Smooth before averaging", "smooth_before_averaging"),
        ]

        tooltips = {
            "valid_window": "Centered rolling mean — windows that would exceed data borders are excluded (curve shortens by window-1).",
            "symmetric_window": "Centered rolling mean — at borders the window shrinks symmetrically (curve keeps full length).",
            "asymmetric_window": "Centered rolling mean — at borders use all available points asymmetrically (keeps length, slightly biased at edges).",
            "gaussian": "Gaussian smoothing (scipy.ndimage.gaussian_filter1d) with sigma = window/2. Successive points are weighted by a Gaussian kernel.",
            "savgol_2": "Savitzky-Golay smoothing (order 2, window made odd). Fits a 2nd-degree polynomial per window — preserves peaks better than plain mean.",
            "savgol_3": "Savitzky-Golay smoothing (order 3, window made odd). 3rd-degree polynomial per window — more flexible, needs window > order.",
            "savgol_4": "Savitzky-Golay smoothing (order 4, window made odd). 4th-degree polynomial per window — most flexible, most noise-sensitive.",
            "bspline": "Smoothing B-spline (scipy.interpolate.UnivariateSpline). Window scales the smoothing factor s = N·var·(window/100).",
            "ema": "Exponential moving average (pandas ewm, span = window, adjust=False). Weights decay exponentially — recent points dominate.",
            "zero_start": "If checked, inserts a (0, 0) anchor at the origin when the (smoothed) curve does not already start there. Raw 'Orig' data is not altered.",
            "quantize": "Resamples the smoothed curve to N evenly spaced X points via linear interpolation (np.linspace + np.interp). Enforce-zero expands span to 0..end.",
            "smooth_before_averaging": "Only applies to \"average\" system dropdown selection. If checked, the Mean window is applied to each system separately, then averaged. If unchecked, systems are averaged first, then smoothed (via the options selected here).",
        }

        actions_dict = {}
        for text, data in options:
            if data is None:
                try:
                    menu.addSection(text)
                except AttributeError:
                    action = QAction(text, self)
                    action.setDisabled(True)
                    menu.addAction(action)
            else:
                action = QAction(text, self)
                action.setCheckable(True)
                action.setData(data)
                if data in tooltips:
                    action.setToolTip(tooltips[data])
                    try:
                        action.setStatusTip(tooltips[data])
                    except Exception:
                        pass
                
                # Custom check logic for special toggles
                if data == "zero_start":
                    action.setChecked(self.enforce_zero_start)
                    action.triggered.connect(self._toggle_enforce_zero)
                elif data == "quantize":
                    action.setChecked(self.quantize_points is not None)
                    action.triggered.connect(self._open_quantize_dialog)
                elif data == "smooth_before_averaging":
                    action.setChecked(self.smooth_before_averaging)
                    action.triggered.connect(self._toggle_smooth_before_averaging)
                    # Show tooltip also as WhatsThis for broader visibility
                    try:
                        action.setWhatsThis(tooltips[data])
                    except Exception:
                        pass
                else:
                    group.addAction(action)
                    if data == self.running_mean_setting:
                        action.setChecked(True)
                
                menu.addAction(action)
                actions_dict[data] = action

        if not any(a.isChecked() for a in group.actions()):
            # Fallback for moving average group
            actions_dict.get("symmetric_window", QAction()).setChecked(True)
        
        group.triggered.connect(self._set_running_mean_setting)
        
        menu.exec(header.mapToGlobal(pos))

    def _toggle_enforce_zero(self, checked):
        self.enforce_zero_start = checked
        self.update_plots()

    def _toggle_smooth_before_averaging(self, checked):
        self.smooth_before_averaging = bool(checked)
        self.update_plots()

    def _open_quantize_dialog(self, checked=False):
        dialog = QuantizeDialog(self.quantize_points, self)
        if dialog.exec():
            self.quantize_points = dialog.get_value()
            self.update_plots()

    def _set_running_mean_setting(self, action):
        new_setting = action.data()
        if new_setting and self.running_mean_setting != new_setting:
            self.running_mean_setting = new_setting
            self.update_plots()

    def _on_header_clicked(self, column_index):
        """Handle header clicks to toggle synchronization for specific columns."""
        # Only allow sync for columns 2-7 (Show, Mean, Std, Style, thk)
        # Column 1 is Plot name, Column 8 is Del button
        if column_index not in range(2, 8) or column_index == 5:
            return

        header = self.plot_table.horizontalHeader()

        if column_index in self.synchronized_columns:
            # If already synchronized, unsynchronize
            self.synchronized_columns.remove(column_index)
            # Reset the header color to default system appearance
            header.model().setHeaderData(column_index, Qt.Orientation.Horizontal,
                                        None, Qt.ItemDataRole.BackgroundRole)
        else:
            # If not synchronized, synchronize and copy value from selected row
            self.synchronized_columns.add(column_index)

            # Copy the value from the selected row to all other rows
            if self.plot_table.rowCount() > 1:
                self._sync_column_values(column_index)

            # Change header appearance to indicate it's synchronized with a different background color
            header.model().setHeaderData(column_index, Qt.Orientation.Horizontal,
                                        QColor(100, 200, 100), Qt.ItemDataRole.BackgroundRole)
                                        
        self.update_plots()

    def _sync_column_values(self, column_index):
        """Sync values in all rows of a column based on the selected row."""
        if self.plot_table.rowCount() == 0:
            return

        source_row = self.plot_table.currentRow()
        if source_row == -1:
            source_row = 0 # Fallback to the first row if none is selected
        
        # Get the value from the source row
        source_widget = self.plot_table.cellWidget(source_row, column_index)
        if not source_widget:
            return

        # Handle widget-based columns (2-7: Show, Mean, Std, Style, thk)
        if column_index == 2:  # Show (checkbox)
            source_value = source_widget.findChild(QCheckBox).isChecked()
            for row in range(self.plot_table.rowCount()):
                widget = self.plot_table.cellWidget(row, column_index)
                if widget:
                    checkbox = widget.findChild(QCheckBox)
                    checkbox.blockSignals(True)
                    checkbox.setChecked(source_value)
                    checkbox.blockSignals(False)
        elif column_index == 3:  # Mean (line edit)
            source_value = source_widget.findChild(QLineEdit).text()
            for row in range(self.plot_table.rowCount()):
                show_widget = self.plot_table.cellWidget(row, 2)
                mean_widget = self.plot_table.cellWidget(row, 3)

                if mean_widget:
                    is_show_checked = show_widget.findChild(QCheckBox).isChecked() if show_widget else False
                    mean_text = mean_widget.findChild(QLineEdit).text()
                    is_mean_active = mean_text.isdigit() and int(mean_text) != 0

                    if is_show_checked or is_mean_active:
                        line_edit = mean_widget.findChild(QLineEdit)
                        line_edit.blockSignals(True)
                        line_edit.setText(source_value)
                        line_edit.blockSignals(False)
        elif column_index == 4:  # Std (checkbox)
            source_value = source_widget.findChild(QCheckBox).isChecked()
            for row in range(self.plot_table.rowCount()):
                widget = self.plot_table.cellWidget(row, column_index)
                if widget:
                    checkbox = widget.findChild(QCheckBox)
                    checkbox.blockSignals(True)
                    checkbox.setChecked(source_value)
                    checkbox.blockSignals(False)
        elif column_index == 6:  # Style (combo box)
            source_value = source_widget.currentText()
            for row in range(self.plot_table.rowCount()):
                widget = self.plot_table.cellWidget(row, column_index)
                if widget:
                    widget.blockSignals(True)
                    widget.setCurrentText(source_value)
                    widget.blockSignals(False)
        elif column_index == 7:  # thk (line edit)
            source_value = source_widget.findChild(QLineEdit).text()
            for row in range(self.plot_table.rowCount()):
                widget = self.plot_table.cellWidget(row, column_index)
                if widget:
                    line_edit = widget.findChild(QLineEdit)
                    line_edit.blockSignals(True)
                    line_edit.setText(source_value)
                    line_edit.blockSignals(False)

    def _update_sync_based_on_column(self, column_index, current_row):
        """Apply synchronization to all rows when a value changes in a synchronized column."""
        if column_index not in self.synchronized_columns:
            return

        if self.plot_table.rowCount() <= 1:
            return

        # Get the new value from the current row
        current_widget = self.plot_table.cellWidget(current_row, column_index)
        if not current_widget:
            return

        new_value = None
        if column_index == 2:  # Show (checkbox)
            new_value = current_widget.findChild(QCheckBox).isChecked()
        elif column_index == 3:  # Mean (line edit)
            new_value = current_widget.findChild(QLineEdit).text()
        elif column_index == 4:  # Std (checkbox)
            new_value = current_widget.findChild(QCheckBox).isChecked()
        elif column_index == 6:  # Style (combo box)
            new_value = current_widget.currentText()
        elif column_index == 7:  # thk (line edit)
            new_value = current_widget.findChild(QLineEdit).text()

        # Apply the new value to all other rows
        for row in range(self.plot_table.rowCount()):
            if row == current_row:
                continue

            target_widget = self.plot_table.cellWidget(row, column_index)
            if not target_widget:
                continue
            
            # Check eligibility for Mean column sync before applying
            if column_index == 3:
                show_widget = self.plot_table.cellWidget(row, 2)
                mean_widget = self.plot_table.cellWidget(row, 3)
                is_eligible = False
                if mean_widget:
                    is_show_checked = show_widget.findChild(QCheckBox).isChecked() if show_widget else False
                    mean_text = mean_widget.findChild(QLineEdit).text()
                    is_mean_active = mean_text.isdigit() and int(mean_text) != 0
                    if is_show_checked or is_mean_active:
                        is_eligible = True
                
                if not is_eligible:
                    continue # Skip this row

            if column_index == 2:  # Show (checkbox)
                checkbox = target_widget.findChild(QCheckBox)
                checkbox.blockSignals(True)
                checkbox.setChecked(new_value)
                checkbox.blockSignals(False)
            elif column_index == 3:  # Mean (line edit)
                line_edit = target_widget.findChild(QLineEdit)
                line_edit.blockSignals(True)
                line_edit.setText(new_value)
                line_edit.blockSignals(False)
            elif column_index == 4:  # Std (checkbox)
                checkbox = target_widget.findChild(QCheckBox)
                checkbox.blockSignals(True)
                checkbox.setChecked(new_value)
                checkbox.blockSignals(False)
            elif column_index == 6:  # Style (combo box)
                target_widget.blockSignals(True)
                target_widget.setCurrentText(new_value)
                target_widget.blockSignals(False)
            elif column_index == 7:  # thk (line edit)
                line_edit = target_widget.findChild(QLineEdit)
                line_edit.blockSignals(True)
                line_edit.setText(new_value)
                line_edit.blockSignals(False)

    def _revert_combo_selection(self, combo):
        """Reverts the combo box selection to match the current table state."""
        if self.plot_table.currentRow() == -1:
            combo.setCurrentIndex(0)
            return

        selected_row = self.plot_table.currentRow()
        item = self.plot_table.item(selected_row, 1)
        if not item:
            combo.setCurrentIndex(0)
            return

        plot_name = item.data(Qt.ItemDataRole.UserRole) or item.text()
        _, _, x_ax, y_ax = self._parse_plot_name(plot_name)
        
        target_val = x_ax if combo == self.xaxis_combo else y_ax
        
        # Try to set text, fallback to index 0
        index = combo.findText(target_val)
        if index != -1:
            combo.setCurrentIndex(index)
        else:
            combo.setCurrentIndex(0)

    def load_project(self, root_path, keywords, show_discovery_warnings: bool = True, force_reload: bool = False, keep_table: bool = False, target_system=None):
        paths = LogParser.normalize_paths(root_path)
        # Prevent redundant reloading if path is same and not forced
        if not force_reload and self.loaded_path == paths:
            return

        preserved_view_ranges = None
        preserved_single_axis_ranges = None
        if keep_table and self.plot_controller and self.plot_controller.y_axes:
            preserved_view_ranges = copy.deepcopy(self.plot_controller.get_view_ranges())
            preserved_single_axis_ranges = copy.deepcopy(self.single_axis_view_ranges)

        self.running_mean_setting = "symmetric_window" 
        self.average_user_choices.clear()
        
        if not keywords:
            self.data_manager.data.clear()
            self.data_manager.available_columns = []
            self.loaded_path = None # Clear loaded path
            self._update_ui_state(project_loaded=False)
            return

        studies, warnings, file_map, origins = LogParser.discover_multi(paths, keywords)
        self.study_origins = origins

        # Hide/show study/system selectors based on project structure
        # In Flat/Parent mode, we HAVE studies ('.' and '..'), so selectors should be VISIBLE.
        # Previously we hid them if keys == ['.']. 
        # Now we might have ['.'] or ['.', '..'].
        # If we have ONLY ['.'], we might still want to show them if the user entered a file?
        # User said: "the other files should also be part in the System dropdown".
        # So selectors should be visible.
        # Let's adjust logic: Only hide if we detected a "Classic Flat" logic where we wanted to simplify UI.
        # But now we use '.' explicitly.
        # Let's show selectors always if we have valid data.
        
        # Actually, let's keep it visible if we have data.
        # Old logic: is_flat_structure = list(studies.keys()) == ['.']
        # If we have ['.'], we have 1 study.
        # If we have ['.', '..'], we have 2 studies.
        # If we have multiple classic studies, we have multiple.
        
        # Use simpler logic: Hide ONLY if we have EXACTLY 1 study AND it is named '.' (pure flat dir scan)?
        # But even then, we have multiple systems, so we need the System selector.
        # We only hid STUDY selector?
        # self.study_combo.setVisible(not is_flat_structure)
        # self.system_combo.setVisible(not is_flat_structure) -> Wait, if hidden, how to select system?
        # The code hid BOTH! That implies if flat, we couldn't select systems?
        # Ah, previously flat mode meant "All files are merged" or something?
        # No, "Systems: [sys1, sys2]".
        # If hidden, user can't select. That seems like a bug in previous logic or intended for single-system?
        # Let's force visibility if we have systems to select.
        
        self.study_label_widget.setVisible(True)
        self.study_combo.setVisible(True)
        self.system_label_widget.setVisible(True)
        self.system_combo.setVisible(True)

        # --- Header selection: only if a single file has >1 header internally ---
        # Group files that internally have multiple headers by their header set; each distinct set gets one dialog.
        # Single-header files (even if headers differ across files) do not trigger a dialog.
        batches = []
        try:
            batches = LogParser.get_files_with_multiple_headers(file_map) if file_map else []
        except Exception:
            batches = []
        # Cache for save/restore; batches define what we showed
        self._header_batches_cache = batches

        # Decide whether to show dialogs
        # - If no batch -> no dialog, include all (no filter)
        # - If batches exist: on re-parse (force_reload) always re-prompt;
        #   otherwise (initial load, session restore) reuse saved allowed_headers if valid.
        show_dialog = len(batches) > 0
        is_reparse = bool(force_reload)  # keywords/path/refresh set force_reload=True
        if show_dialog and not is_reparse and self.allowed_headers is not None:
            # Reuse saved choice silently if it covers all batches' headers
            all_batch_headers = set()
            for b in batches:
                all_batch_headers.update(b['header_set'])
            if all(h in all_batch_headers or h not in all_batch_headers for h in self.allowed_headers):
                # Actually check if saved headers are subset of current all headers and still meaningful
                # If saved headers still exist in current batches, reuse
                if any(h in all_batch_headers for h in self.allowed_headers):
                    # Check that at least one header per batch is still selected
                    show_dialog = False
                else:
                    show_dialog = True
            else:
                show_dialog = True
        # Also suppress dialog during session restore where keep_table=False and not force_reload but we have saved headers
        if show_dialog and not is_reparse and self.allowed_headers is not None and len(batches) > 0:
            # If we have a saved selection that matches current batches' header sets, reuse
            # Compare saved set against current all headers
            current_all = set()
            for b in batches:
                current_all.update(b['header_set'])
            if self.allowed_headers.issubset(current_all) or self.allowed_headers == current_all:
                # Saved selection still valid -> reuse without prompting
                # But if batches differ from last cache, we must prompt; handled via is_reparse above
                # Here is_reparse==False, so reuse
                show_dialog = False

        if len(batches) == 0:
            self.allowed_headers = None
            self.data_manager.set_allowed_headers(None)
        elif show_dialog:
            # Show one dialog per distinct header set (batch), sequential
            final_allowed = set()
            # Collect headers from single-header files (always allowed)
            # They are not in batches, but their headers should stay allowed
            # Gather all headers from all files, then subtract multi-header ones not selected?
            # Simpler: start with all single-header files' headers automatically allowed
            try:
                all_groups = LogParser.get_header_groups_for_filemap(file_map) if file_map else {}
                # Headers that appear only in single-header files are those not in any batch's set
                multi_headers = set()
                for b in batches:
                    multi_headers.update(b['header_set'])
                single_headers = set(all_groups.keys()) - multi_headers
                final_allowed.update(single_headers)
            except Exception:
                final_allowed = set()

            for batch in batches:
                dlg = HeaderSelectionDialog(batch['groups'], self)
                # Optional: set title to indicate batch
                if len(batches) > 1:
                    dlg.setWindowTitle(f"Select Thermo Headers - Batch {batches.index(batch)+1}/{len(batches)} ({len(batch['files'])} files)")
                if dlg.exec():
                    sel = dlg.get_selected_or_all()
                else:
                    # Cancel -> keep largest header in this batch
                    max_hdr = max(batch['groups'].items(), key=lambda kv: kv[1].get('rows', 0))[0]
                    sel = {max_hdr}
                final_allowed.update(sel)
            self.allowed_headers = final_allowed if final_allowed else None
            self.data_manager.set_allowed_headers(self.allowed_headers)
        else:
            # Reuse saved
            self.data_manager.set_allowed_headers(self.allowed_headers)

        if warnings and show_discovery_warnings:
             # Relax warning for flat mode
             pass
        
        warnings, successful_keywords = self.data_manager.load_project_data(studies, paths, keywords, file_map, allowed_headers=self.allowed_headers)
        
        self.main_window.chip_input.update_chip_styles(successful_keywords)

        if warnings:
            QMessageBox.warning(self, "Data Loading Warning", "\n".join(warnings))
            
        self.main_window.studies_label.setText(f"Studies: {len(self.data_manager.get_study_names())}")
        
        studies_dict = self.data_manager.data
        if studies_dict:
            first_study_name = next(iter(studies_dict))
            num_systems = len(studies_dict[first_study_name])
            self.main_window.systems_label.setText(f"Systems: {num_systems}")
        else:
            self.main_window.systems_label.setText("Systems: 0")

        # Units and timestep come from the first path that can answer; they describe
        # the simulation setup, and mixing them across projects would be meaningless.
        units = None
        timestep = None
        for base in paths:
            base = Path(base)
            if base.is_dir():
                units = units or LogParser.get_units(base)
                timestep = timestep or LogParser.get_timestep(base)

        time_units_map = {'lj': 'tau', 'real': 'fs', 'metal': 'ps', 'si': 's', 'cgs': 's', 'electron': 'fs', 'micro': 'us', 'nano': 'ns'}
        self.main_window.units_label.setText(f"Unit: {units or 'N/A'}")
        t_unit = time_units_map.get(units, "")
        self.main_window.timestep_label.setText(f"Timestep: {timestep} {t_unit}" if timestep else "Timestep: N/A")

        # Update state tracking
        self.loaded_path = paths

        # Rows remember (path, raw study); re-point them at the study keys this load
        # produced and drop the ones whose path chip is gone.
        self._resync_rows_to_paths(paths)

        self._update_ui_state(project_loaded=True, keep_table=keep_table)
        
        # Handle Auto-Selection
        if target_system:
            if "." in studies:
                self.study_combo.setCurrentText(".")
                # Note: setCurrentText triggers on_study_selected which populates system_combo.
                # However, signals might be blocked or async?
                # on_study_selected is synchronous.
                # So system_combo should be populated now.
                index = self.system_combo.findText(target_system)
                if index != -1:
                    self.system_combo.setCurrentIndex(index)
        
        # If keeping table, trigger a plot update to refresh data sources
        if keep_table:
            self.update_plots()
            self._restore_preserved_view_ranges(preserved_view_ranges, preserved_single_axis_ranges)

    def _update_ui_state(self, project_loaded: bool, keep_table: bool = False):
        # Only clear table/plots if we are NOT keeping the table (e.g., new path load)
        if not keep_table:
            # 1. Clear Plot Table
            self.plot_table.setRowCount(0)
            self.next_plot_id = 0  # Reset unique ID counter for plots
            self._update_move_buttons_visibility()
            self.plot_controller.clear_all_plots()

            # 2. Clear Fit Table and Resources (Fix for dangling references)
            self.fit_table.setRowCount(0)
            
            # Close any open fit configuration windows to prevent orphans
            for dialog in self.fit_dialogs.values():
                dialog.close()
            
            self.fit_dialogs.clear()
            self.fit_results.clear()
            self.next_fit_id = 0 # Reset unique ID counter for fits

        def reset_combo(combo, placeholder, items):
            combo.blockSignals(True)
            current_text = combo.currentText()
            combo.clear()
            combo.addItem(placeholder)
            if items:
                combo.addItems(items)
            
            if current_text in items:
                combo.setCurrentText(current_text)
            else:
                combo.setCurrentIndex(0)
            combo.blockSignals(False)

        all_combos = [self.study_combo, self.system_combo, self.xaxis_combo, self.yaxis_combo]
        if project_loaded:
            for combo in all_combos:
                combo.setEnabled(True)
            self.add_btn.setEnabled(True)

            reset_combo(self.study_combo, "Select Study", self.data_manager.get_study_names())
            reset_combo(self.system_combo, "Select System", self.data_manager.get_all_system_names())
            
            # Refresh axis choices while preserving valid selections on keyword refresh.
            if keep_table:
                self._refresh_axis_combos()
            else:
                self._populate_axis_combo(self.xaxis_combo, self.data_manager.get_all_column_names())
                self._populate_axis_combo(self.yaxis_combo, self.data_manager.get_all_column_names())
            
            # Only add a default row if the table is empty (either because we cleared it,
            # or because nothing was there to keep). Keyed on the row count rather than
            # on keep_table so a preserved-but-empty table still gets its default row.
            if self.plot_table.rowCount() == 0:
                if self.data_manager.get_study_names() or self.data_manager.get_all_system_names():
                    self.add_new_plot_row()
                else:
                    QMessageBox.warning(self, "No Data", "Project loaded, but no valid study or system data was found.")

        else:
            for combo in all_combos:
                combo.clear()
                combo.addItem("...")
                combo.setEnabled(False)
            self.add_btn.setEnabled(False)

    def on_study_selected(self, text=""):
        """
        Handle Study selection. 
        1. Filter System dropdown (Study-specific or All).
        2. Auto-select System if study has exactly ONE.
        3. Update the selected row(s) Study/System components.
        """
        study = self.study_combo.currentText()
        prev_study = getattr(self, '_prev_study', None)
        # For derived sequence when no explicit global yet, remember where current_sys stood.
        prev_seq_target = None
        current_sys = self.system_combo.currentText() if hasattr(self, 'system_combo') else "Select System"
        if getattr(self, '_explicit_system_seq', None) is None and prev_study and current_sys and current_sys != "Select System":
            try:
                prev_systems = self.data_manager.get_system_names(prev_study) if prev_study != "Select Study" else []
                if current_sys in prev_systems:
                    prev_seq_target = prev_systems.index(current_sys)
            except Exception:
                prev_seq_target = None
        
        # 1. Reset and filter the system combo
        self.system_combo.blockSignals(True)
        # current_sys already captured before clear
        self.system_combo.clear()
        self.system_combo.addItem("Select System")
        
        # If Study is selected, show its systems. If "Select Study", show ALL systems.
        systems = self.data_manager.get_system_names(study) if self.study_combo.currentIndex() > 0 else self.data_manager.get_all_system_names()
        
        self.system_combo.addItems(self._get_average_system_options(systems))
        self.system_combo.addItems(systems)
        
        # 2. Auto-selection / Preservation logic for System dropdown
        final_sys = "Select System"
        if len(systems) == 1:
            # Study has exactly one system -> Auto-select it
            final_sys = systems[0]
            self.system_combo.setCurrentText(final_sys)
        elif current_sys in systems or current_sys in self._get_average_system_options(systems):
            # Keep previous selection if it still exists in the new context
            final_sys = current_sys
            self.system_combo.setCurrentText(final_sys)
        else:
            # No exact match and not single -> pattern fallback (digit groups = 1 token)
            avg_opts = self._get_average_system_options(systems)
            candidates_ordered = avg_opts + systems  # dropdown order
            matched = _find_pattern_matched_system(current_sys, candidates_ordered)
            if matched is not None:
                final_sys = matched
                self.system_combo.setCurrentText(final_sys)
            else:
                # Sequence fallback: remembered explicit index or derived from previous study.
                target_idx = None
                if getattr(self, '_explicit_system_seq', None) is not None:
                    target_idx = self._explicit_system_seq
                elif prev_seq_target is not None:
                    target_idx = prev_seq_target
                if target_idx is not None and len(systems) > 0:
                    # Count only real systems, clamp to closest smaller (or exact).
                    clamped = max(0, min(int(target_idx), len(systems) - 1))
                    final_sys = systems[clamped]
                    self.system_combo.setCurrentText(final_sys)
                else:
                    self.system_combo.setCurrentIndex(0)
            
        self.system_combo.blockSignals(False)
        # Remember this study for next switch's derived fallback
        self._prev_study = study if study != "Select Study" else None
        
        # 3. Update the selected row(s) - ONLY if this was a manual user change
        if not self._is_internal_update:
            self._update_selected_row_name_component('study', text, refresh=False)
            if final_sys != "Select System":
                self._update_selected_row_name_component('system', final_sys, refresh=False)
            self._finish_row_name_update()

    def on_system_selected(self, text: str):
        """
        Handle System selection. 
        1. If Study is 'Select Study' and selected system belongs to only ONE study, auto-select it.
        2. Filter System dropdown to match the selected/auto-selected study.
        3. Update selected rows Study/System components.
        """
        if not text or text == "Select System":
            if not self._is_internal_update:
                self._update_selected_row_name_component('system', text)
            return

        final_study = self.study_combo.currentText()

        # 1. Handle unique study auto-selection
        if self.study_combo.currentIndex() == 0: # "Select Study"
            matching_studies = []
            for s_name, s_map in self.data_manager.data.items():
                if text in s_map:
                    matching_studies.append(s_name)
            
            if len(matching_studies) == 1:
                # Unique study found! 
                final_study = matching_studies[0]
                
                # Update UI Study combo
                self.study_combo.blockSignals(True)
                self.study_combo.setCurrentText(final_study)
                self.study_combo.blockSignals(False)
                
                # IMPORTANT: Now that a study is selected, the system dropdown 
                # MUST be updated to only contain systems from THIS study.
                systems = self.data_manager.get_system_names(final_study)
                
                self.system_combo.blockSignals(True)
                self.system_combo.clear()
                self.system_combo.addItem("Select System")
                self.system_combo.addItems(self._get_average_system_options(systems))
                self.system_combo.addItems(systems)
                self.system_combo.setCurrentText(text) # Restore selection
                self.system_combo.blockSignals(False)

        # 2. Update the row data - ONLY if manual user change
        if not self._is_internal_update:
            self._update_selected_row_name_component('system', text)

    def on_table_selection_changed(self):
        # Update dropdowns to match selected row; clear priority when nothing selected
        if not self.plot_table.selectedItems() or self.plot_table.currentRow() == -1:
            self.update_plots()
            return

        selected_row = self.plot_table.currentRow()
        plot_name_item = self.plot_table.item(selected_row, 1)
        if not plot_name_item:
            return

        plot_name = plot_name_item.data(Qt.ItemDataRole.UserRole) or plot_name_item.text()
        
        # Set internal update flag to prevent loop/overwriting during selection change
        self._is_internal_update = True
        
        try:
            parts = plot_name.split(' | ')
            study, system, x_ax, y_ax = (parts + ['N/A'] * 4)[:4]

            def set_combo_text(combo, text):
                index = combo.findText(text)
                if index != -1:
                    combo.setCurrentIndex(index)
                else:
                    combo.setCurrentIndex(0)

            # Triggering study change will now also repopulate system combo correctly via on_study_selected
            set_combo_text(self.study_combo, study)
            set_combo_text(self.system_combo, system)
            set_combo_text(self.xaxis_combo, x_ax)
            set_combo_text(self.yaxis_combo, y_ax)
        
        finally:
            self._is_internal_update = False
        
        # As before: unlocked row click fits global union (all visible X), not
        # selected-only/group. The X values are still per-row raw.
        # Trigger plot update because x-axis might need to change
        self.update_plots()

    def add_new_plot_row(self):
        source_row_index = self.plot_table.currentRow()

        # Rows are appended, matching trj/dsd. Appending also leaves every existing
        # row index untouched, so the fit table keeps pointing at the same plots.
        insert_row = self.plot_table.rowCount()
        self.plot_table.insertRow(insert_row)

        # Gather existing colors to generate a new, distinct color
        existing_colors = []
        for row in range(self.plot_table.rowCount()):
            if row == insert_row: continue
            color_widget = self.plot_table.cellWidget(row, 5)
            if color_widget:
                existing_colors.append(color_widget.color())
        
        new_color = self._get_distinct_color(existing_colors)
        
        # Start with default data for the new row
        plot_data = {
            'plot_name': "N/A | N/A | N/A | N/A",
            'show': True,
            'mean': "0",
            'std': False,
            'color': new_color.name(),
            'style': "-",
            'thickness': "1",
            'plot_id': self.next_plot_id
        }
        self.next_plot_id += 1
        
        copied_from_selection = False
        # If a row was selected, copy its Study, System, X-Axis, and Y-Axis
        if source_row_index != -1:
            try:
                # The new row went to the end, so the source kept its index
                row_to_copy_from = source_row_index

                source_plot_data = self._extract_row_data(row_to_copy_from)
                parts = source_plot_data['plot_name'].split(' | ')
                
                if len(parts) == 4:
                    study, system, x_ax, y_ax = parts
                    # Construct the new plot name, copying all four parts
                    plot_data['plot_name'] = f"{study} | {system} | {x_ax} | {y_ax}"
                    copied_from_selection = True

            except (AttributeError, ValueError, IndexError):
                pass # Fallback to defaults if something goes wrong

        # Fallback for special cases if we didn't copy from a selection
        if not copied_from_selection:
            is_single_file_mode = len(self.data_manager.get_study_names()) == 1 and self.data_manager.get_study_names()[0] == '.'
            if is_single_file_mode:
                study = '.'
                system = self.data_manager.get_system_names(study)[0]
                x_ax = "N/A"
                if "Step" in self.data_manager.available_columns:
                    x_ax = "Step"
                elif self.data_manager.available_columns:
                    x_ax = self.data_manager.available_columns[0]
                plot_data['plot_name'] = f"{study} | {system} | {x_ax} | N/A"

        self._populate_row_data(insert_row, plot_data)

        self.plot_table.selectRow(insert_row)
        self._update_move_buttons_visibility()
        self._update_plot_labels()

    def update_plots(self, auto_trigger_fits=True):
        """
        Main function to refresh the plot widget. 
        It handles data fetching, processing (averaging/smoothing), 
        caching for curve fitting, and rendering both original data and fits.
        """
        self._suppress_single_axis_range_capture = True

        # 1. Clear existing plots and reset view
        self.plot_controller.clear_all_plots()
        
        # [Fit Integration] Update sources for the fit table dropdowns so they match current plot names
        self._update_fit_source_combos()
        # [Fit Integration] Logic Change: Update available "Types" based on Source "Mean" availability
        self._update_fit_type_combos()
        
        # Only honour currentRow when it is actually selected - deselected table must not keep priority
        _sel = self.plot_table.selectionModel().selectedRows() if self.plot_table.selectionModel() else []
        selected_row_idx = self.plot_table.currentRow() if _sel else -1
        selected_rows_set = set(idx.row() for idx in _sel) if _sel else set()
        all_plot_info = []
        
        # [Fit Integration] Cache to store source data (x, y) so fits can run even if source is hidden
        plot_data_cache = {}

        # --- A. Gather Configuration from Plot Table ---
        for row in range(self.plot_table.rowCount()):
            try:
                item = self.plot_table.item(row, 1)
                if not item: continue

                plot_name = item.data(Qt.ItemDataRole.UserRole) or item.text()
                plot_id = item.data(Qt.ItemDataRole.UserRole + 1) # What fits bind to

                study, system, x_ax, y_ax = plot_name.split(' | ')
                is_valid = "N/A" not in [study, system, x_ax, y_ax]

                # UI State Retrieval
                show_original = self.plot_table.cellWidget(row, 2).findChild(QCheckBox).isChecked()
                mean_text = self.plot_table.cellWidget(row, 3).findChild(QLineEdit).text()
                mean_window = int(mean_text) if mean_text.isdigit() else 0
                show_std = self.plot_table.cellWidget(row, 4).findChild(QCheckBox).isChecked()
                color = self.plot_table.cellWidget(row, 5).color()
                style_text = self.plot_table.cellWidget(row, 6).currentText()
                style = {'Solid': Qt.PenStyle.SolidLine, '-': Qt.PenStyle.SolidLine, 'Dash': Qt.PenStyle.DashLine, '--': Qt.PenStyle.DashLine, 'Dot': Qt.PenStyle.DotLine, ':': Qt.PenStyle.DotLine, '-.': Qt.PenStyle.DashDotLine, 'DashDot': Qt.PenStyle.DashDotLine}.get(style_text, Qt.PenStyle.SolidLine)
                thickness = float(self.plot_table.cellWidget(row, 7).findChild(QLineEdit).text())

                is_active = show_original or (mean_window > 0) or show_std
                
                # [Fit Integration] Check if this plot is a source for an ACTIVE fit
                is_fit_source = False
                if self.fit_table_visible and plot_id is not None:
                    for fr in range(self.fit_table.rowCount()):
                        src_combo = self.fit_table.cellWidget(fr, 1)
                        # Type combo is at column 2
                        type_combo = self.fit_table.cellWidget(fr, 2)
                        # It is a source only if the fit points at THIS row AND is not "Off"
                        if src_combo and src_combo.currentData() == plot_id and type_combo.currentText() != "Off":
                            is_fit_source = True
                            break

                plot_info = {
                    'row': row, 'plot_name': plot_name, 'plot_id': plot_id,
                    'study': study, 'system': system, 'x_ax': x_ax, 'y_ax': y_ax, 
                    'color': color, 'style': style, 'thickness': thickness,
                    'show_original': show_original, 'mean_window': mean_window, 'show_std': show_std,
                    'is_valid': is_valid, 'is_active': is_active,
                    'is_fit_source': is_fit_source
                }
                all_plot_info.append(plot_info)
            except (ValueError, AttributeError, IndexError):
                continue
        
        # --- Determine Selected Y for draw ordering ---
        selected_y_ax = None
        selected_x_ax = None

        if selected_row_idx != -1 and selected_row_idx < len(all_plot_info):
            selected_plot_info = all_plot_info[selected_row_idx]
            if selected_plot_info['is_active'] and selected_plot_info['is_valid']:
                selected_x_ax = selected_plot_info['x_ax']
                selected_y_ax = selected_plot_info['y_ax']

        # Fallback for X label ordering when nothing selected
        if not selected_x_ax:
            for info in all_plot_info:
                if info['is_active'] and info['is_valid']:
                    selected_x_ax = info['x_ax']
                    break
        
        # Sort plots: legend = table row order, selected rows at bottom (most top plot last)
        # previous y-axis grouping caused table 0:y1,1:y2,2:y1 → legend y1:0,2 then y2:1. Now interleaved.
        all_plot_info.sort(key=lambda x: (x['row'] in selected_rows_set, x['row']))

        # --- B. Main Loop: Data Fetching, Caching, and Plotting ---
        for plot_info in all_plot_info:
            if not plot_info['is_valid']: continue
            
            # [Optimization] Skip data fetch only if plot is HIDDEN AND NOT needed for a fit
            if not plot_info['is_active'] and not plot_info['is_fit_source']:
                continue

            # Resolve Labels
            x_label = self.global_label_map.get(plot_info['x_ax'], plot_info['x_ax'])
            y_label = self.global_label_map.get(plot_info['y_ax'], plot_info['y_ax'])
            
            z_offset = 100 if plot_info['row'] in selected_rows_set else 0
            
            # --- Consistency Check (Original Logic) ---
            user_choices = None
            if self._is_average_system(plot_info['system']):
                study_name = plot_info['study']
                current_consistency = self.data_manager.check_data_consistency(study_name)
                if current_consistency:
                    cached_data = self.average_user_choices.get(study_name)
                    if cached_data and cached_data.get('lengths') == current_consistency:
                        user_choices = cached_data['choice']
                    else:
                        if plot_info['is_active']:
                            dialog = InconsistentDataDialog(current_consistency, self)
                            if dialog.exec():
                                user_choices = dialog.get_choices()
                                self.average_user_choices[study_name] = {'choice': user_choices, 'lengths': current_consistency}
                            else:
                                continue 
                        else:
                            continue

            # Determine if we need to compute raw inter-system standard deviation
            force_raw_std = self._uses_forced_average_std(plot_info['system'])
            compute_raw_std = (
                self._is_average_system(plot_info['system']) and
                (plot_info['show_std'] or force_raw_std) and
                (plot_info['mean_window'] == 0 or force_raw_std)
            )
            
            # Each row uses its OWN x column raw values on the shared ViewBox
            current_x_ax = plot_info['x_ax']

            # --- Fetch Data ---
            # Smooth-before-averaging mode only matters for average systems with a mean window
            use_smooth_before = (
                self.smooth_before_averaging
                and self._is_average_system(plot_info['system'])
                and plot_info['mean_window'] >= 1
            )
            # For pale-band calculation we still need force_raw_std in both modes
            # In smooth-before mode the inter-system std is computed on smoothed curves
            if use_smooth_before:
                # Fetch raw averaged data for Orig line and cache (no smoothing yet)
                raw_compute_std = (
                    self._is_average_system(plot_info['system']) and
                    (plot_info['show_std'] or force_raw_std) and
                    (plot_info['mean_window'] == 0 or force_raw_std)
                )
                # For smooth-before, Orig's pale band will be replaced by smoothed pale,
                # so raw pale is only needed when there is NO mean window (handled above).
                # We still fetch without smoothing for Orig display; for mean case we suppress raw std.
                raw_data_for_orig = self.data_manager.get_plot_data(
                    plot_info['study'], 'average' if self._is_average_system(plot_info['system']) else plot_info['system'],
                    current_x_ax, plot_info['y_ax'],
                    False if plot_info['mean_window'] >= 1 and force_raw_std else raw_compute_std,
                    user_choices
                )
                if not raw_data_for_orig:
                    # Fallback: at least need raw for Orig; if none, skip
                    continue
                data = raw_data_for_orig
                # Keep raw for Orig plotting
                try:
                    x_np = data['x'].to_numpy(dtype=float) if hasattr(data['x'], 'to_numpy') else np.array(data['x'], dtype=float)
                    y_np = data['y'].to_numpy(dtype=float) if hasattr(data['y'], 'to_numpy') else np.array(data['y'], dtype=float)
                except ValueError:
                    continue
                plot_data_cache[plot_info['plot_id']] = {
                    'x': x_np,
                    'y': y_np,
                    'color': plot_info['color'],
                    'mean_window': plot_info['mean_window'],
                    'raw_x_ax': plot_info['x_ax'],
                    'raw_y_ax': plot_info['y_ax'],
                    'y_label': y_label,
                    'x_label': x_label
                }
                # Compute smoothed-averaged data
                smooth_data = self._compute_smooth_before_average_data(
                    plot_info['study'], current_x_ax, plot_info['y_ax'],
                    plot_info['mean_window'], plot_info['show_std'], force_raw_std, user_choices
                )
                if smooth_data is None:
                    # Fallback to regular smoothing (average-then-smooth) if per-system failed
                    running_mean_y = self._calculate_running_average(y_np, plot_info['mean_window'], 'mean')
                    running_std = None
                    if plot_info['show_std']:
                        running_std = self._calculate_running_average(y_np, plot_info['mean_window'], 'std')
                    len_diff = len(x_np) - len(running_mean_y)
                    if len_diff > 0:
                        start_idx = len_diff // 2
                        end_idx = len(x_np) - (len_diff - start_idx)
                        running_mean_x = x_np[start_idx:end_idx]
                    else:
                        running_mean_x = x_np
                    std_inter_smooth = None
                else:
                    running_mean_x = smooth_data['x']
                    running_mean_y = smooth_data['y']
                    running_std = smooth_data.get('running_std')
                    std_inter_smooth = smooth_data.get('std_inter')
                # --- Quantization (shared x for both std bands) ---
                if self.quantize_points and isinstance(self.quantize_points, int) and self.quantize_points > 1:
                    if len(running_mean_x) > 1:
                        target_start_x = 0.0 if self.enforce_zero_start else running_mean_x[0]
                        x_quant = np.linspace(target_start_x, running_mean_x[-1], self.quantize_points)
                        y_quant = np.interp(x_quant, running_mean_x, running_mean_y)
                        if running_std is not None:
                            running_std = np.interp(x_quant, running_mean_x, running_std)
                        if std_inter_smooth is not None:
                            std_inter_smooth = np.interp(x_quant, running_mean_x, std_inter_smooth)
                        running_mean_x, running_mean_y = x_quant, y_quant
                # --- Hard Anchor ---
                if self.enforce_zero_start and len(running_mean_x) > 0:
                    starts_at_origin = running_mean_x[0] == 0 and running_mean_y[0] == 0
                    if not starts_at_origin:
                        running_mean_x = np.insert(running_mean_x, 0, 0.0)
                        running_mean_y = np.insert(running_mean_y, 0, 0.0)
                        if running_std is not None:
                            running_std = np.insert(running_std, 0, 0.0)
                        if std_inter_smooth is not None:
                            std_inter_smooth = np.insert(std_inter_smooth, 0, 0.0)
                # Store for fit + x-range + plotting; keep smooth stds distinct
                plot_data_cache[plot_info['plot_id']]['mean_x'] = running_mean_x
                plot_data_cache[plot_info['plot_id']]['mean_y'] = running_mean_y
                plot_data_cache[plot_info['plot_id']]['mean_std'] = running_std
                # Keep smoothed inter-system std for pale band on smoothed curve
                plot_data_cache[plot_info['plot_id']]['mean_std_inter'] = std_inter_smooth
                # No longer needed as separate variable after branch, but keep for later draw check
                # Use a flag to indicate we are in smooth-before path
                _smooth_before_active = True
                # For later draw logic we need to know std_inter_smooth exists
                # Store it in local variable for Draw section
                running_mean_y_sb = running_mean_y
                running_mean_x_sb = running_mean_x
                running_std_sb = running_std
                # Keep std_inter in closure
            else:
                data = self.data_manager.get_plot_data(
                    plot_info['study'], 'average' if self._is_average_system(plot_info['system']) else plot_info['system'], current_x_ax, plot_info['y_ax'], 
                    compute_raw_std, user_choices
                )
                
                if not data: continue

                # [Fit Integration] Keep Orig data raw; enforce-0 belongs to the averaged line below.
                try:
                    x_np = data['x'].to_numpy(dtype=float) if hasattr(data['x'], 'to_numpy') else np.array(data['x'], dtype=float)
                    y_np = data['y'].to_numpy(dtype=float) if hasattr(data['y'], 'to_numpy') else np.array(data['y'], dtype=float)
                except ValueError:
                    continue 
                plot_data_cache[plot_info['plot_id']] = {
                    'x': x_np,
                    'y': y_np,
                    'color': plot_info['color'],
                    'mean_window': plot_info['mean_window'],
                    'raw_x_ax': plot_info['x_ax'],
                    'raw_y_ax': plot_info['y_ax'],
                    'y_label': y_label,
                    'x_label': x_label
                }

                # [Fit Integration] Pre-calculate running mean if needed for fit (Mean Type)
                running_mean_y = None
                running_mean_x = None

                if plot_info['mean_window'] >= 1:
                    running_mean_y = self._calculate_running_average(y_np, plot_info['mean_window'], 'mean')
                    # Calculate running std here to allow for quantization
                    running_std = None
                    if plot_info['show_std']:
                        running_std = self._calculate_running_average(y_np, plot_info['mean_window'], 'std')

                    len_diff = len(x_np) - len(running_mean_y)
                    if len_diff > 0:
                        start_idx = len_diff // 2
                        end_idx = len(x_np) - (len_diff - start_idx)
                        running_mean_x = x_np[start_idx:end_idx]
                    else:
                        running_mean_x = x_np
                    
                    # --- Quantization ---
                    if self.quantize_points and isinstance(self.quantize_points, int) and self.quantize_points > 1:
                        if len(running_mean_x) > 1:
                            # If Enforce 0 is on, we wrap the entire span from 0 to the end
                            target_start_x = 0.0 if self.enforce_zero_start else running_mean_x[0]
                            x_quant = np.linspace(target_start_x, running_mean_x[-1], self.quantize_points)
                            y_quant = np.interp(x_quant, running_mean_x, running_mean_y)
                            if running_std is not None:
                                running_std = np.interp(x_quant, running_mean_x, running_std)
                            running_mean_x, running_mean_y = x_quant, y_quant

                    # --- Hard Anchor: Draw averaged data to the origin without changing Orig data ---
                    if self.enforce_zero_start and len(running_mean_x) > 0:
                        starts_at_origin = running_mean_x[0] == 0 and running_mean_y[0] == 0
                        if not starts_at_origin:
                            running_mean_x = np.insert(running_mean_x, 0, 0.0)
                            running_mean_y = np.insert(running_mean_y, 0, 0.0)
                            if running_std is not None:
                                running_std = np.insert(running_std, 0, 0.0)

                    plot_data_cache[plot_info['plot_id']]['mean_x'] = running_mean_x
                    plot_data_cache[plot_info['plot_id']]['mean_y'] = running_mean_y
                    plot_data_cache[plot_info['plot_id']]['mean_std'] = running_std
                _smooth_before_active = False

            # [Fit Integration] Barrier: If plot is hidden in table, stop here.
            if not plot_info['is_active']:
                continue

            # --- Visualization Preparation ---
            data['x_col'] = plot_info['x_ax']
            data['x_label'] = x_label if x_label else plot_info['x_ax']
            data['y_col'] = plot_info['y_ax']
            data['y_label'] = y_label if y_label else plot_info['y_ax']
            
            display_x = x_label if x_label else plot_info['x_ax']
            display_y = y_label if y_label else plot_info['y_ax']
            legend_name = f"{plot_info['study']} | {plot_info['system']} | {display_x} | {display_y}"

            # --- 1. Draw Original Data ---
            if plot_info['show_original']:
                plot_data = data.copy()
                plot_data['row'] = plot_info['row']
                if not compute_raw_std: plot_data['std'] = None
                if force_raw_std:
                    pale_std_color = QColor(plot_info['color'])
                    h, s, v, a = pale_std_color.getHsv()
                    muted_s = int(s * 0.45)
                    brightened_v = min(255, int(v + (255 - v) * 0.25))
                    pale_std_color.setHsv(h, muted_s, brightened_v, a)
                    plot_data['error_color'] = pale_std_color
                    plot_data['error_alpha_multiplier'] = 0.5
                    plot_data['error_layer_priority'] = -100
                self.plot_controller.add_or_update_plot(
                    legend_name, plot_data, plot_info['color'], 
                    plot_info['style'], thickness=plot_info['thickness'],
                    layer_priority=z_offset
                )

            # --- 2. Draw Running Mean / Std Deviation ---
            if plot_info['mean_window'] >= 1:
                if _smooth_before_active:
                    # Smoothed inter-system band (pale) for 'average & std' when smooth-before is active
                    # This is the std across per-system smoothed curves, centered on the averaged smoothed mean.
                    # It replaces the raw pale band (which now stays off the Orig line).
                    std_inter_smooth = plot_data_cache[plot_info['plot_id']].get('mean_std_inter')
                    if force_raw_std and std_inter_smooth is not None and len(std_inter_smooth) == len(running_mean_y):
                        pale_data = {
                            'x': running_mean_x, 'y': running_mean_y, 'std': std_inter_smooth,
                            'x_col': plot_info['x_ax'], 'x_label': data['x_label'],
                            'y_col': plot_info['y_ax'], 'y_label': data['y_label'],
                            'row': plot_info['row']
                        }
                        pale_color = QColor(plot_info['color'])
                        h, s, v, a = pale_color.getHsv()
                        muted_s = int(s * 0.45)
                        brightened_v = min(255, int(v + (255 - v) * 0.25))
                        pale_color.setHsv(h, muted_s, brightened_v, a)
                        pale_data['error_color'] = pale_color
                        pale_data['error_alpha_multiplier'] = 0.5
                        pale_data['error_layer_priority'] = -100
                        # Use a distinct layer so pale stays behind running band
                        self.plot_controller.add_or_update_plot_with_custom_colors(
                            legend_name + "_running_mean_std_inter", pale_data, pale_color,
                            Qt.PenStyle.NoPen, layer_priority=0 + z_offset
                        )
                    if plot_info['show_std']:
                        if running_std is not None and len(running_std) == len(running_mean_y):
                            std_data = {
                                'x': running_mean_x, 'y': running_mean_y, 'std': running_std,
                                'x_col': plot_info['x_ax'], 'x_label': data['x_label'],
                                'y_col': plot_info['y_ax'], 'y_label': data['y_label'],
                                'row': plot_info['row']
                            }
                            std_color = QColor(plot_info['color'])
                            std_color.setHsv(std_color.hue(), int(std_color.saturation() * 0.66), int(std_color.value() * 0.5), int(std_color.alpha() * 0.5))
                            self.plot_controller.add_or_update_plot_with_custom_colors(
                                legend_name + "_running_mean_std", std_data, std_color,
                                Qt.PenStyle.NoPen, layer_priority=1 + z_offset
                            )
                else:
                    if plot_info['show_std']:
                        if running_std is not None and len(running_std) == len(running_mean_y):
                            std_data = {
                                'x': running_mean_x, 'y': running_mean_y, 'std': running_std, 
                                'x_col': plot_info['x_ax'], 'x_label': data['x_label'],
                                'y_col': plot_info['y_ax'], 'y_label': data['y_label'],
                                'row': plot_info['row']
                            }
                            std_color = QColor(plot_info['color'])
                            std_color.setHsv(std_color.hue(), int(std_color.saturation() * 0.66), int(std_color.value() * 0.5), int(std_color.alpha() * 0.5))
                            
                            self.plot_controller.add_or_update_plot_with_custom_colors(
                                legend_name + "_running_mean_std", std_data, std_color, 
                                Qt.PenStyle.NoPen, layer_priority=1 + z_offset
                            )
                
                if plot_info['mean_window'] > 0:
                    # Ensure running_mean_x/y are defined in both branches
                    # (In smooth-before they were set above; in regular they were set in else-branch)
                    try:
                        _rmx = running_mean_x
                        _rmy = running_mean_y
                    except NameError:
                        _rmx = plot_data_cache[plot_info['plot_id']].get('mean_x')
                        _rmy = plot_data_cache[plot_info['plot_id']].get('mean_y')
                    if _rmx is None or _rmy is None:
                        _rmx = running_mean_x if 'running_mean_x' in locals() else None
                        _rmy = running_mean_y if 'running_mean_y' in locals() else None
                    # Prefer the cache values which are already quantized/zero-handled
                    _rmx = plot_data_cache[plot_info['plot_id']].get('mean_x', _rmx)
                    _rmy = plot_data_cache[plot_info['plot_id']].get('mean_y', _rmy)
                    if _rmx is not None and _rmy is not None:
                        mean_color = QColor(plot_info['color'])
                        if plot_info['show_original']:
                            mean_color.setHsvF(mean_color.hueF(), mean_color.saturationF(), mean_color.valueF() * 0.5, mean_color.alphaF())
                        else:
                            mean_color.setHsvF(mean_color.hueF(), mean_color.saturationF(), mean_color.valueF() * 0.8, mean_color.alphaF())
                        
                        mean_data = {
                            'x': _rmx, 'y': _rmy, 'std': None, 
                            'x_col': plot_info['x_ax'], 'x_label': data['x_label'],
                            'y_col': plot_info['y_ax'], 'y_label': data['y_label'],
                            'row': plot_info['row']
                        }
                        self.plot_controller.add_or_update_plot_with_custom_colors(
                            legend_name + "_running_mean", mean_data, mean_color, 
                            plot_info['style'], layer_priority=2 + z_offset, thickness=plot_info['thickness']
                        )

        # Keep the fetched data around: "Add Fit" seeds its x-range from the source row.
        self._plot_data_cache = plot_data_cache

        # --- C. Fit Plotting Loop ---
        if self.fit_table_visible:
            for r in range(self.fit_table.rowCount()):
                try:
                    # Robust lookup: Use Item Data as Source of Truth
                    item_id = self.fit_table.item(r, 0)
                    if not item_id: continue
                    fit_id = item_id.data(Qt.ItemDataRole.UserRole)
                    
                    # Ensure we aren't processing a "False" ID (legacy safety)
                    if fit_id is False: continue
                    
                    source_combo = self.fit_table.cellWidget(r, 1)
                    source_name = source_combo.currentText()
                    # The binding is the plot id, not the name - two rows can share a name
                    source_pid = source_combo.currentData()
                    fit_type = self.fit_table.cellWidget(r, 2).currentText()

                    if fit_type == "Off":
                        continue

                    # NOTE: For Preview Mode, the Fit Result contains x/y fit data,
                    # so we carry on to fit_results even when the source has no data
                    # (hidden row, or a source that was deleted).
                    src_data = plot_data_cache.get(source_pid, {}) if source_pid is not None else {}
                    
                    # Visuals
                    color_btn = self.fit_table.cellWidget(r, 5)
                    # Auto-color logic (fallback)
                    if color_btn.color() == QColor("gray") and 'color' in src_data:
                        src_color = src_data['color']
                        new_color = QColor(src_color)
                        new_color.setHsvF(new_color.hueF(), new_color.saturationF(), max(0, new_color.valueF() * 0.6), new_color.alphaF())
                        color_btn.blockSignals(True)
                        color_btn.set_color(new_color)
                        color_btn.blockSignals(False)
                    
                    fit_color = color_btn.color()
                    style_text = self.fit_table.cellWidget(r, 6).currentText()
                    style = {'Solid': Qt.PenStyle.SolidLine, '-': Qt.PenStyle.SolidLine, 'Dash': Qt.PenStyle.DashLine, '--': Qt.PenStyle.DashLine, 'Dot': Qt.PenStyle.DotLine, ':': Qt.PenStyle.DotLine, '-.': Qt.PenStyle.DashDotLine, 'DashDot': Qt.PenStyle.DashDotLine}.get(style_text, Qt.PenStyle.SolidLine)
                    thickness = float(self.fit_table.cellWidget(r, 7).findChild(QLineEdit).text())

                    # Data Push (only if source is valid and we are not in preview/loading)
                    if 'x' in src_data and fit_id in self.fit_dialogs:
                         # Data Source Selection
                        if fit_type == "Mean" and 'mean_x' in src_data:
                            x_fit_src = src_data['mean_x']
                            y_fit_src = src_data['mean_y']
                        else:
                            x_fit_src = src_data['x']
                            y_fit_src = src_data['y']

                        dialog = self.fit_dialogs[fit_id]
                        data_changed = dialog.set_data(x_fit_src, y_fit_src)
                        
                        should_auto_trigger = (
                            auto_trigger_fits and 
                            not getattr(self, '_is_loading', False) and
                            data_changed and 
                            dialog.current_worker is None
                        )
                        if should_auto_trigger:
                            dialog.calculate_fit()
                    
                    # Plot Result
                    if fit_id in self.fit_results:
                        res = self.fit_results[fit_id]
                        valid_statuses = ['Success', 'Fit Poor / Failed', 'Preview']
                        
                        if res.get('status') in valid_statuses:
                            # If we are plotting a cached result, ensure the table error label reflects it
                            err_val = res.get('final_error', float('inf'))
                            err_lbl = self.fit_table.cellWidget(r, 4)
                            if err_lbl:
                                if res.get('status') == 'Preview':
                                    err_lbl.setText("Preview")
                                elif err_val > 1e15:
                                    err_lbl.setText("Fail")
                                else:
                                    err_lbl.setText(f"{err_val:.2e}")

                            legend = f"Fit: {source_name} ({fit_type}) [ID:{fit_id}]"
                            
                            fit_y_col = src_data.get('raw_y_ax', 'N/A')
                            fit_y_label = src_data.get('y_label', 'N/A')
                            fit_x_col = src_data.get('raw_x_ax', src_data.get('x_label', 'N/A'))
                            fit_x_label = src_data.get('x_label', fit_x_col)
                            
                            fit_plot_data = {
                                'x': res['x_fit'],
                                'y': res['y_fit'],
                                'std': None,
                                'x_col': fit_x_col,
                                'x_label': fit_x_label,
                                'y_col': fit_y_col,
                                'y_label': fit_y_label
                            }
                            
                            self.plot_controller.add_or_update_plot_with_custom_colors(
                                legend, fit_plot_data, fit_color, style, 
                                layer_priority=200, thickness=thickness
                            )
                except (ValueError, AttributeError, IndexError):
                    continue
                except Exception as e:
                    print(f"Fit Plotting Error row {r}: {e}")
                    continue

        # --- Finalize View ---
        self.current_x_axis = selected_x_ax
        visible_plots = [p for p in all_plot_info if p['is_active'] and p['is_valid']]
        
        self._update_axis_properties(visible_plots, selected_x_ax)
        
        for row in range(self.plot_table.rowCount()):
            self._update_row_visual_state(row)
        
        self._suppress_single_axis_range_capture = False
        if self.single_axis_view_lock_enabled:
            if self.single_axis_view_ranges is None:
                self._capture_single_axis_view_range()
            self._apply_single_axis_view_range()
        else:
            # As before: unlocked, every update (including row click) fits
            # global X union of all visible plots; per-row X values are still
            # plotted as-is on the single shared ViewBox.
            self.plot_controller.apply_current_locks()
            self._apply_x_range_global(visible_plots, plot_data_cache)
            if hasattr(self, '_pending_x_mode'):
                delattr(self, '_pending_x_mode')
        # geometry may have changed (new axis, new ranges, window resize) - keep aux VBs aligned
        try:
            if len(getattr(self.plot_controller, 'y_axes', {})) > 1:
                self.plot_controller.update_views()
                QTimer.singleShot(50, self._do_update_views)
        except Exception:
            pass

    def _update_fit_type_combos(self):
        """
        New logic: Iterates through fit rows, checks if the selected source has a valid
        Mean Window. If not, removes 'Mean' from the dropdown. If 'Mean' was selected
        and is no longer valid, auto-switches to 'Orig'.
        """
        # 1. Build a map of Source Plot Id -> HasValidMean
        source_mean_map = {}
        for r in range(self.plot_table.rowCount()):
            pid = self._get_plot_row_id(r)
            mean_widget = self.plot_table.cellWidget(r, 3)
            has_valid_mean = False
            if mean_widget:
                txt = mean_widget.findChild(QLineEdit).text()
                if txt.isdigit() and int(txt) > 0:
                    has_valid_mean = True
            if pid is not None:
                source_mean_map[pid] = has_valid_mean

        # 2. Update Fit Table Rows
        for r in range(self.fit_table.rowCount()):
            src_combo = self.fit_table.cellWidget(r, 1)
            type_combo = self.fit_table.cellWidget(r, 2)
            
            if not src_combo or not type_combo: continue

            has_mean = source_mean_map.get(src_combo.currentData(), False)
            
            current_type = type_combo.currentText()
            
            # Rebuild items
            type_combo.blockSignals(True)
            type_combo.clear()
            items = ["Off", "Orig"]
            if has_mean:
                items.append("Mean")
            type_combo.addItems(items)
            
            # Restore selection or Auto-Switch
            if current_type in items:
                type_combo.setCurrentText(current_type)
            elif current_type == "Mean" and not has_mean:
                # Requirement: Switch to Orig if Mean is no longer applicable
                type_combo.setCurrentText("Orig")
            else:
                type_combo.setCurrentIndex(0)
                
            type_combo.blockSignals(False)
        
    def _handle_table_widget_change(self, row, column):
        """
        Unified handler for any widget change in the plot table.
        Applies changes to all selected rows if multiple are selected.
        """
        # 1. Determine the New Value from the triggering widget
        source_widget = self.plot_table.cellWidget(row, column)
        new_val = None
        
        if column == 2:   # Show (Checkbox)
            new_val = source_widget.findChild(QCheckBox).isChecked()
        elif column == 3: # Mean (LineEdit)
            new_val = source_widget.findChild(QLineEdit).text()
        elif column == 4: # Std (Checkbox)
            new_val = source_widget.findChild(QCheckBox).isChecked()
        elif column == 5: # Color (ColorButton)
            new_val = source_widget.color()
        elif column == 6: # Style (ComboBox)
            new_val = source_widget.currentText()
        elif column == 7: # Thickness (LineEdit)
            new_val = source_widget.findChild(QLineEdit).text()

        # 2. Apply to other selected rows (Batch Update)
        # Use selectionModel().selectedRows() for reliable row detection
        selected_model_rows = self.plot_table.selectionModel().selectedRows()
        selected_rows_set = set(idx.row() for idx in selected_model_rows)
        
        # Logic to prevent accidental sync if widget click didn't update selection (e.g. checkbox)
        if row not in selected_rows_set:
            # The user interacted with a row NOT currently selected.
            # Assume single-row edit.
            unique_rows = {row}
        else:
            # The row IS selected, so apply to the full selection group.
            unique_rows = selected_rows_set
        
        if len(unique_rows) > 1 and new_val is not None:
            for r in unique_rows:
                if r == row: continue # Skip the source row
                
                target_widget = self.plot_table.cellWidget(r, column)
                if not target_widget: continue
                
                # Apply based on column type, BLOCKING SIGNALS to prevent recursion
                if column == 2:   # Show
                    cb = target_widget.findChild(QCheckBox)
                    if cb:
                        cb.blockSignals(True)
                        cb.setChecked(new_val)
                        cb.blockSignals(False)
                elif column == 3: # Mean
                    le = target_widget.findChild(QLineEdit)
                    if le:
                        le.blockSignals(True)
                        le.setText(new_val)
                        le.blockSignals(False)
                elif column == 4: # Std
                    cb = target_widget.findChild(QCheckBox)
                    if cb:
                        cb.blockSignals(True)
                        cb.setChecked(new_val)
                        cb.blockSignals(False)
                elif column == 5: # Color
                    if target_widget:
                        target_widget.blockSignals(True)
                        target_widget.set_color(new_val)
                        target_widget.blockSignals(False)
                elif column == 6: # Style
                    if target_widget:
                        target_widget.blockSignals(True)
                        target_widget.setCurrentText(new_val)
                        target_widget.blockSignals(False)
                elif column == 7: # Thickness
                    le = target_widget.findChild(QLineEdit)
                    if le:
                        le.blockSignals(True)
                        le.setText(new_val)
                        le.blockSignals(False)
                
                # Update visual state for the modified row
                self._update_row_visual_state(r)

        # 3. Standard Processing (Sync & Update)
        self._update_sync_based_on_column(column, row)
        
        if column == 5:
            self._update_fit_row_colors()
        
        self.update_plots()
        self._update_row_visual_state(row)

    def _calculate_running_average(self, data, window_size, statistic='mean'):
        """
        Calculates the running mean or std deviation based on the current setting.
        Returns a numpy array. For the 'valid' method, the array will be shorter.
        """
        if len(data) < 1 or window_size < 1:
            return data
        
        # pandas Series is efficient for this
        series = pd.Series(data)
        data_np = np.array(data)

        if self.running_mean_setting == 'valid_window':
            rolling_obj = series.rolling(window=window_size, center=True, min_periods=window_size)
            result = rolling_obj.mean() if statistic == 'mean' else rolling_obj.std()
            return result.dropna().to_numpy()

        elif self.running_mean_setting == 'symmetric_window':
            result = np.zeros_like(data_np, dtype=float)
            half_window = window_size // 2
            for i in range(len(data_np)):
                k = min(half_window, i, len(data_np) - 1 - i)
                start_idx, end_idx = i - k, i + k + 1
                window_slice = data_np[start_idx:end_idx]
                result[i] = np.mean(window_slice) if statistic == 'mean' else np.std(window_slice)
            return result

        elif self.running_mean_setting == 'asymmetric_window':
            result = np.zeros_like(data_np, dtype=float)
            half_window = window_size // 2
            for i in range(len(data_np)):
                start_idx = max(0, i - half_window)
                end_idx = min(len(data_np), i + half_window + 1)
                window_slice = data_np[start_idx:end_idx]
                result[i] = np.mean(window_slice) if statistic == 'mean' else np.std(window_slice)
            return result

        elif self.running_mean_setting == 'gaussian':
            if statistic == 'mean':
                from scipy.ndimage import gaussian_filter1d
                sigma = max(1.0, window_size / 2.0)
                return gaussian_filter1d(data_np, sigma=sigma)
            else:
                return series.rolling(window=window_size, center=True, min_periods=1).std().bfill().ffill().to_numpy()

        elif self.running_mean_setting.startswith('savgol_'):
            polyorder = int(self.running_mean_setting.split('_')[1])
            window_len = window_size
            if window_len % 2 == 0:
                window_len += 1 # Savgol requires odd window length
            if window_len <= polyorder:
                window_len = polyorder + 1
                if window_len % 2 == 0:
                    window_len += 1
            if statistic == 'mean':
                from scipy.signal import savgol_filter
                try:
                    return savgol_filter(data_np, window_length=window_len, polyorder=polyorder)
                except Exception as e:
                    print(f"Savgol failed: {e}")
                    return data_np
            else:
                return series.rolling(window=window_size, center=True, min_periods=1).std().bfill().ffill().to_numpy()

        elif self.running_mean_setting == 'bspline':
            if statistic == 'mean':
                from scipy.interpolate import UnivariateSpline
                try:
                    variance = np.var(data_np)
                    if variance == 0: return data_np
                    # Normalizing s so that it roughly correlates with window size
                    s_val = len(data_np) * variance * (window_size / 100.0)
                    spline = UnivariateSpline(np.arange(len(data_np)), data_np, s=s_val)
                    return spline(np.arange(len(data_np)))
                except Exception as e:
                    print(f"BSpline failed: {e}")
                    return data_np
            else:
                return series.rolling(window=window_size, center=True, min_periods=1).std().bfill().ffill().to_numpy()

        elif self.running_mean_setting == 'ema':
            if statistic == 'mean':
                return series.ewm(span=window_size, adjust=False).mean().to_numpy()
            else:
                return series.ewm(span=window_size, adjust=False).std().bfill().to_numpy()

        return data_np

    def _compute_smooth_before_average_data(self, study: str, x_col: str, y_col: str,
                                            mean_window: int, show_std: bool,
                                            force_raw_std: bool,
                                            user_choices: dict = None):
        """Per-system smoothing then averaging. Returns dict or None.

        For 'Smooth before averaging' mode: each system's y is smoothed independently
        with the current running_mean_setting, then the smoothed y's are averaged.
        - x_mean: mean of per-system smoothed x (handles valid_window truncation)
        - y_mean: mean of per-system smoothed y
        - std_inter: std across per-system smoothed y (for 'average & std' pale band)
        - running_std: mean of per-system running stds (for Std column when show_std)
        Quantization / enforce_zero are NOT applied here — caller does that on the
        final averaged curve so all std bands share the same x.
        """
        if study not in self.data_manager.data:
            return None
        if mean_window is None or mean_window < 1:
            return None

        # Ensure all systems loaded
        for sys_name in list(self.data_manager.data[study].keys()):
            self.data_manager._ensure_system_loaded(study, sys_name)

        study_dfs = self.data_manager.data[study]
        # Build filtered list mirroring LogDataManager.get_plot_data
        dfs_to_process = []
        if user_choices:
            truncate_len = user_choices.get('truncate_len')
            exclude_systems = set(user_choices.get('exclude', []))
            for sys_name, entry in study_dfs.items():
                if sys_name in exclude_systems:
                    continue
                if isinstance(entry, pd.DataFrame):
                    if truncate_len is not None:
                        dfs_to_process.append(entry.iloc[:truncate_len])
                    else:
                        dfs_to_process.append(entry)
        else:
            dfs_to_process = [df for df in study_dfs.values() if isinstance(df, pd.DataFrame)]

        if not dfs_to_process:
            return None

        # Determine alignment column
        align_col = 'Step'
        if not all(align_col in df.columns for df in dfs_to_process):
            align_col = 'index'

        # Preprocess each df like manager: de-duplicate + set index
        processed_dfs = []
        for df in dfs_to_process:
            temp = df.copy()
            if align_col == 'index':
                temp = temp.reset_index()
            temp = temp.drop_duplicates(subset=[align_col], keep='last')
            temp = temp.set_index(align_col)
            processed_dfs.append(temp)

        y_list = []
        x_list = []
        std_running_list = []

        for p_df in processed_dfs:
            x_s = self.data_manager._get_series(p_df, x_col)
            y_s = self.data_manager._get_series(p_df, y_col)
            if x_s is None or y_s is None:
                continue
            # Convert to numpy
            try:
                y_np = y_s.to_numpy(dtype=float) if hasattr(y_s, 'to_numpy') else np.array(y_s, dtype=float)
                x_np = x_s.to_numpy(dtype=float) if hasattr(x_s, 'to_numpy') else np.array(x_s, dtype=float)
            except Exception:
                continue
            if len(y_np) == 0 or len(x_np) == 0:
                continue

            # Smooth y
            try:
                y_smooth = self._calculate_running_average(y_np, mean_window, 'mean')
            except Exception as e:
                print(f"Smooth-before-avg y failed: {e}")
                continue

            # Slice x / index to match y_smooth length (valid_window shortens)
            len_diff = len(x_np) - len(y_smooth)
            if len_diff > 0:
                start_idx = len_diff // 2
                end_idx = len(x_np) - (len_diff - start_idx)
                try:
                    x_smooth = x_np[start_idx:end_idx]
                    idx_smooth = y_s.index[start_idx:end_idx]
                except Exception:
                    # Fallback to simple slice
                    x_smooth = x_np[start_idx:end_idx]
                    idx_smooth = y_s.index[start_idx:end_idx] if len(y_s.index) >= end_idx else y_s.index[:len(y_smooth)]
            else:
                x_smooth = x_np
                idx_smooth = y_s.index

            # Guard length after slicing
            if len(x_smooth) != len(y_smooth) or len(y_smooth) == 0:
                # If mismatch, trim to min length
                min_len = min(len(x_smooth), len(y_smooth), len(idx_smooth))
                y_smooth = y_smooth[:min_len]
                x_smooth = x_smooth[:min_len]
                idx_smooth = idx_smooth[:min_len]

            try:
                y_series = pd.Series(y_smooth, index=idx_smooth)
                x_series = pd.Series(x_smooth, index=idx_smooth)
            except Exception:
                continue

            y_list.append(y_series)
            x_list.append(x_series)

            if show_std:
                try:
                    std_np = self._calculate_running_average(y_np, mean_window, 'std')
                except Exception as e:
                    print(f"Smooth-before-avg std failed: {e}")
                    continue
                # Align std length to y_smooth length
                # For methods where std keeps full length while y_smooth is truncated (valid_window),
                # std_np will be truncated similarly; for others lengths match. If mismatch, slice.
                if len(std_np) != len(y_smooth):
                    # Bring std_np to same indexing as y_smooth
                    # For valid_window both truncated same amount -> lengths already equal
                    # For other mismatches, slice std_np same as x_np was sliced
                    if len(std_np) > len(y_smooth):
                        # std longer -> slice middle like x
                        sd_diff = len(std_np) - len(y_smooth)
                        s_start = sd_diff // 2
                        s_end = len(std_np) - (sd_diff - s_start)
                        std_np = std_np[s_start:s_end]
                    else:
                        # std shorter (should not happen) -> pad? just truncate y
                        min_l = min(len(std_np), len(y_smooth))
                        std_np = std_np[:min_l]
                        y_smooth = y_smooth[:min_l]
                        x_smooth = x_smooth[:min_l]
                        idx_smooth = idx_smooth[:min_l]
                        # Rebuild y/x series already added, need to adjust last entries
                        # For simplicity, skip this rare case
                        if len(std_np) != len(idx_smooth):
                            idx_smooth = idx_smooth[:len(std_np)]
                try:
                    std_series = pd.Series(std_np, index=idx_smooth)
                except Exception:
                    continue
                std_running_list.append(std_series)

        if not y_list or not x_list:
            return None

        try:
            y_df = pd.concat(y_list, axis=1, join='inner')
            x_df = pd.concat(x_list, axis=1, join='inner')
        except Exception as e:
            print(f"Smooth-before-avg concat failed: {e}")
            return None

        if y_df.empty or x_df.empty:
            return None

        y_mean = y_df.mean(axis=1)
        x_mean = x_df.mean(axis=1)

        # Ensure alignment of x_mean and y_mean (inner join already ensures common index)
        # Reindex to common index
        common_idx = y_mean.index.intersection(x_mean.index)
        if len(common_idx) == 0:
            return None
        y_mean = y_mean.reindex(common_idx)
        x_mean = x_mean.reindex(common_idx)

        result = {
            'x': x_mean.to_numpy(dtype=float) if hasattr(x_mean, 'to_numpy') else np.array(x_mean, dtype=float),
            'y': y_mean.to_numpy(dtype=float) if hasattr(y_mean, 'to_numpy') else np.array(y_mean, dtype=float),
            'index': common_idx,
        }

        # Inter-system std of smoothed (for average & std pale band)
        if force_raw_std:
            try:
                std_inter = y_df.reindex(common_idx).std(axis=1)
                result['std_inter'] = std_inter.to_numpy(dtype=float) if hasattr(std_inter, 'to_numpy') else np.array(std_inter, dtype=float)
            except Exception:
                result['std_inter'] = None
        else:
            result['std_inter'] = None

        # Running std: mean of per-system running stds
        if show_std and std_running_list:
            try:
                std_df = pd.concat(std_running_list, axis=1, join='inner')
                std_df = std_df.reindex(common_idx)
                # Some methods produce NaNs at edges (valid) already dropped; for those keeping length,
                # bfill/ffill already done. But after inner join we may still have NaNs.
                avg_std = std_df.mean(axis=1)
                # Fill NaNs that may remain
                avg_std = avg_std.fillna(0)
                result['running_std'] = avg_std.to_numpy(dtype=float) if hasattr(avg_std, 'to_numpy') else np.array(avg_std, dtype=float)
            except Exception as e:
                print(f"Smooth-before-avg avg std failed: {e}")
                result['running_std'] = None
        else:
            result['running_std'] = None

        return result

    def _update_axis_properties(self, visible_plots_info, selected_x_ax: str = None):
        # Build comma-separated X label: unique, selected-first, mapped through global_label_map
        unique_x_labels = []
        if selected_x_ax:
            lbl = self.global_label_map.get(selected_x_ax, selected_x_ax)
            if lbl not in unique_x_labels:
                unique_x_labels.append(lbl)
        for info in visible_plots_info:
            lbl = self.global_label_map.get(info['x_ax'], info['x_ax'])
            if lbl not in unique_x_labels:
                unique_x_labels.append(lbl)
        x_label = ", ".join(unique_x_labels)

        y_labels = {}
        for info in visible_plots_info:
            if info['y_ax'] not in y_labels:
                y_labels[info['y_ax']] = self.global_label_map.get(info['y_ax'], info['y_ax'])

        self.plot_controller.set_axis_labels(x_label, y_labels)

        # --- Axis Coloring Logic ---
        black_color = QColor("black")
        # Only color axes if there are multiple different y-axes being shown
        if len(y_labels) > 1:
            # Collect unique y_ax -> color mappings to avoid redundant calls
            axis_colors = {info['y_ax']: info['color'] for info in visible_plots_info}
            for y_ax, color in axis_colors.items():
                self.plot_controller.set_axis_color(y_ax, color)
        else:
            # Reset all axes to black if one or zero plots are visible
            for y_ax in self.plot_controller.y_axes.keys():
                self.plot_controller.set_axis_color(y_ax, black_color)
        
        # Update view-lock and multi-axis alignment/scale lock button visibility.
        self._update_lock_button_visuals()

    def _apply_x_range_selected_group(self, visible_plots_info, plot_data_cache, selected_x_ax):
        """Set main ViewBox X range to the single clicked row's X data (when unlocked)."""
        # Find the single selected row's plot_id via visible_plots_info
        # visible_plots_info is filtered to active/valid; caller also supplies
        # selected_x_ax but we now zoom to the row itself, not the group.
        selected_pid = None
        # The panel tracks current selection via table currentRow
        try:
            sel_row = self.plot_table.currentRow()
            item = self.plot_table.item(sel_row, 1) if sel_row != -1 else None
            if item is not None:
                selected_pid = item.data(Qt.ItemDataRole.UserRole + 1)
        except Exception:
            selected_pid = None
        # Fallback to first visible with matching x_ax if pid not in cache
        if selected_pid is None:
            if not visible_plots_info or not selected_x_ax:
                self._apply_x_range_global(visible_plots_info, plot_data_cache)
                return
            for info in visible_plots_info:
                if info.get('x_ax') == selected_x_ax:
                    selected_pid = info.get('plot_id')
                    break
        cache = plot_data_cache.get(selected_pid) if selected_pid is not None else None
        if not cache:
            self._apply_x_range_global(visible_plots_info, plot_data_cache)
            return
        gmin, gmax = float('inf'), float('-inf')
        found = False
        for key in ('x', 'mean_x'):
            arr = cache.get(key)
            if arr is None or len(arr) == 0:
                continue
            try:
                cur_min = float(np.nanmin(arr))
                cur_max = float(np.nanmax(arr))
            except Exception:
                continue
            if not (np.isfinite(cur_min) and np.isfinite(cur_max)):
                continue
            gmin = min(gmin, cur_min)
            gmax = max(gmax, cur_max)
            found = True
        if not found:
            self._apply_x_range_global(visible_plots_info, plot_data_cache)
            return
        if gmax == gmin:
            pad = 1.0
            gmin -= pad
            gmax += pad
        else:
            pad = (gmax - gmin) * 0.05
            gmin -= pad
            gmax += pad
        self._suppress_single_axis_range_capture = True
        try:
            vb = self.plot_widget.getPlotItem().getViewBox()
            vb.setXRange(gmin, gmax, padding=0)
        finally:
            pass

    def _apply_x_range_global(self, visible_plots_info, plot_data_cache):
        """Set X range to union of all visible plots (global auto-range)."""
        if not visible_plots_info:
            return
        gmin, gmax = float('inf'), float('-inf')
        found = False
        pid_to_info = {info['plot_id']: info for info in visible_plots_info if info.get('plot_id') is not None}
        for pid in pid_to_info:
            cache = plot_data_cache.get(pid)
            if not cache:
                continue
            for key in ('x', 'mean_x'):
                arr = cache.get(key)
                if arr is None or len(arr) == 0:
                    continue
                try:
                    cur_min = float(np.nanmin(arr))
                    cur_max = float(np.nanmax(arr))
                except Exception:
                    continue
                if not (np.isfinite(cur_min) and np.isfinite(cur_max)):
                    continue
                gmin = min(gmin, cur_min)
                gmax = max(gmax, cur_max)
                found = True
        if not found:
            return
        if gmax == gmin:
            pad = 1.0
            gmin -= pad
            gmax += pad
        else:
            pad = (gmax - gmin) * 0.05
            gmin -= pad
            gmax += pad
        self._suppress_single_axis_range_capture = True
        try:
            vb = self.plot_widget.getPlotItem().getViewBox()
            vb.setXRange(gmin, gmax, padding=0)
        finally:
            pass

    def delete_plot_row(self):
        button = self.sender()
        if not button:
            return

        # Find the row of the button that was clicked
        parent_widget = button.parentWidget()
        pos = parent_widget.mapTo(self.plot_table.viewport(), QPoint(0, 0))
        row_to_delete = self.plot_table.indexAt(pos).row()

        if row_to_delete < 0:
            return

        # Detach Fits
        # 1. Get the ID of the plot being deleted
        item = self.plot_table.item(row_to_delete, 1)
        # We need the ID (UserRole + 1) to reliably identify the plot
        deleted_plot_id = item.data(Qt.ItemDataRole.UserRole + 1) if item else None

        # 2. Update Fit Table: Detach any fit using this Source ID
        self._detach_fits_from_plot(deleted_plot_id)

        self.plot_table.removeRow(row_to_delete)

        # After removing, if the table is now empty, add a new default row.
        if self.plot_table.rowCount() == 0:
            self.add_new_plot_row()
        else:
            # Otherwise, update everything.
            self._update_move_buttons_visibility()
            self._update_plot_labels()
            self.update_plots()

    def _detach_fits_from_plot(self, plot_id):
        """Turns off and unhooks any fit whose source is the given plot row."""
        if plot_id is None or self.fit_table.rowCount() == 0:
            return

        for r in range(self.fit_table.rowCount()):
            source_combo = self.fit_table.cellWidget(r, 1)
            if not source_combo: continue

            # The combo holds the plot_id in the UserRole data
            if source_combo.currentData() != plot_id:
                continue

            # Turn Fit Type to "Off"
            type_combo = self.fit_table.cellWidget(r, 2)
            if type_combo:
                type_combo.blockSignals(True)
                type_combo.setCurrentText("Off")
                type_combo.blockSignals(False)

            # Detach (Clear selection visually)
            # When update_plots runs later, this item will naturally disappear
            # from the dropdown because it's removed from options,
            # but setting index -1 ensures it's clean immediately.
            source_combo.blockSignals(True)
            source_combo.setCurrentIndex(-1)
            source_combo.blockSignals(False)
            # Stays blank until the user picks a new source: without this flag the
            # next combo rebuild would silently bind the fit to the first plot row.
            source_combo.setProperty("detached", True)

    def _create_centered_widget(self, widget: QWidget) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.addWidget(widget)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setContentsMargins(0,0,0,0)
        return container
    
    def _update_row_visual_state(self, row: int):
        """Greys out the plot name if the row is inactive or incomplete."""
        item = self.plot_table.item(row, 1)
        if not item:
            return
        
        # Check for activity
        show_widget = self.plot_table.cellWidget(row, 2)
        mean_widget = self.plot_table.cellWidget(row, 3)
        std_widget = self.plot_table.cellWidget(row, 4)

        if not all([show_widget, mean_widget, std_widget]):
            return # One of the widgets is missing, cannot update state.

        show_orig = show_widget.findChild(QCheckBox).isChecked()
        mean_text = mean_widget.findChild(QLineEdit).text()
        show_std = std_widget.findChild(QCheckBox).isChecked()
        is_mean_active = mean_text.isdigit() and int(mean_text) > 0
        is_active = show_orig or is_mean_active or show_std
        
        # Check for completeness
        plot_name = item.data(Qt.ItemDataRole.UserRole) or item.text()
        is_complete = "N/A" not in plot_name

        # Apply color
        if not is_active:
            item.setForeground(Qt.GlobalColor.lightGray)
        elif not is_complete:
            item.setForeground(Qt.GlobalColor.gray)
        else:
            item.setForeground(Qt.GlobalColor.black)


    def _get_distinct_color(self, existing_colors: list[QColor]) -> QColor:
        """Generates a new, visually distinct color by finding the candidate furthest from existing colors."""
        # Generate a list of vibrant, random candidate colors
        candidates = []
        for _ in range(20): # Generate 20 candidates
            hue = random.random()
            candidates.append(QColor.fromHsvF(hue, 1.0, 1.0, 1.0))

        if not existing_colors:
            return candidates[0]

        best_candidate = None
        max_min_dist = -1

        for candidate in candidates:
            # Calculate the minimum distance to any existing color
            r1, g1, b1, _ = candidate.getRgb()
            min_dist_sq = float('inf')
            for existing in existing_colors:
                r2, g2, b2, _ = existing.getRgb()
                dist_sq = (r1 - r2)**2 + (g1 - g2)**2 + (b1 - b2)**2
                if dist_sq < min_dist_sq:
                    min_dist_sq = dist_sq
            
            # If this candidate is further from its nearest neighbor than any other candidate, it's our new best
            if min_dist_sq > max_min_dist:
                max_min_dist = min_dist_sq
                best_candidate = candidate

        return best_candidate

    def _create_style_combo(self) -> QComboBox:
        combo = QComboBox()
        combo.addItems(["-", "--", ":", "-."])
        font_metrics = combo.fontMetrics()
        max_width = 0
        for i in range(combo.count()):
            width = font_metrics.horizontalAdvance(combo.itemText(i))
            if width > max_width:
                max_width = width
        combo.view().setMinimumWidth(max_width + 30)
        return combo

    def _update_fit_row_colors(self):
        """
        Updates two things:
        1. The text color in the source dropdowns (both items and the closed combo itself).
        2. The color of the fit row (ColorButton) if it hasn't been manually set.
        """
        if not self.fit_table_visible:
            return
        
        # Build a map of plot id -> color from plot table (ids, not names: rows
        # duplicated from one another share a name but never an id)
        plot_color_map = {}
        for r in range(self.plot_table.rowCount()):
            pid = self._get_plot_row_id(r)
            color_btn = self.plot_table.cellWidget(r, 5)
            if pid is not None and color_btn:
                plot_color_map[pid] = color_btn.color()

        # Apply logic to fit table
        for r in range(self.fit_table.rowCount()):
            combo = self.fit_table.cellWidget(r, 1)
            fit_color_btn = self.fit_table.cellWidget(r, 5)

            if not combo: continue

            # A. Update Dropdown Items Colors
            for i in range(combo.count()):
                combo.setItemData(i, plot_color_map.get(combo.itemData(i)),
                                  Qt.ItemDataRole.ForegroundRole)

            # B. Update The Combo Box Text Color (Closed State)
            current_color = plot_color_map.get(combo.currentData())
            if current_color:
                # Set stylesheet to color the text area
                combo.setStyleSheet(f"color: {current_color.name()};")
            else:
                combo.setStyleSheet("")

            # C. Propagate to Fit Row Button (40% Darker Rule)
            # Only if the fit row color hasn't been set manually by the user
            if fit_color_btn and not fit_color_btn.property("manually_set"):
                if current_color:
                    source_color = current_color

                    # Calculate 40% darker (0.6x brightness)
                    new_color = QColor(source_color)
                    h, s, v, a = new_color.getHsv()
                    new_color.setHsv(h, s, int(v * 0.6), a)
                    
                    # Apply without triggering the manual flag (signals blocked)
                    fit_color_btn.blockSignals(True)
                    fit_color_btn.set_color(new_color)
                    fit_color_btn.blockSignals(False)
        
    def _get_current_state_dict(self):
        """Helper to gather current state for saving."""
        project_paths = self.main_window.get_project_paths()
        config = {
            'type': 'log',  # Simplified type identifier
            # 'path' stays for older builds; 'project_paths' is authoritative.
            'path': project_paths[0] if project_paths else '',
            'project_paths': project_paths,
            'keywords': self.main_window.chip_input.get_chips(), # Added keywords
            'average_choices': self.average_user_choices,
            'running_mean_setting': self.running_mean_setting,
            'enforce_zero_start': self.enforce_zero_start,
            'quantize_points': self.quantize_points,
            'smooth_before_averaging': self.smooth_before_averaging,
            'global_label_map': self.global_label_map,
            'custom_properties': self.custom_properties,
            'scale_lock': self.scale_lock_enabled,
            'axes_lock': self.plot_controller.axes_locked,
            'view_ranges': self.plot_controller.get_view_ranges(),
            'single_axis_view_lock': self.single_axis_view_lock_enabled,
            'single_axis_view_ranges': self.single_axis_view_ranges,
            'fit_table_visible': self.fit_table_visible,
            'selected_row': self.plot_table.currentRow(),
            'allowed_headers': [list(h) for h in self.allowed_headers] if self.allowed_headers else None,
            'plots': [],
            'fits': []
        }
        
        # Save Plots
        for row in range(self.plot_table.rowCount()):
            item = self.plot_table.item(row, 1)
            if item:
                full_name = item.data(Qt.ItemDataRole.UserRole) or item.text()
                plot_info = {
                    'id': item.data(Qt.ItemDataRole.UserRole + 1),
                    'name': full_name,
                    # Which path chip this row's study belongs to (see _resync_rows_to_paths)
                    'origin': item.data(Qt.ItemDataRole.UserRole + 2),
                    'show': self.plot_table.cellWidget(row, 2).findChild(QCheckBox).isChecked(),
                    'mean': self.plot_table.cellWidget(row, 3).findChild(QLineEdit).text(),
                    'std': self.plot_table.cellWidget(row, 4).findChild(QCheckBox).isChecked(),
                    'color': self.plot_table.cellWidget(row, 5).color().name(),
                    'style': self.plot_table.cellWidget(row, 6).currentText(),
                    'thickness': self.plot_table.cellWidget(row, 7).findChild(QLineEdit).text(),
                }
                config['plots'].append(plot_info)

        # Save Fits
        for row in range(self.fit_table.rowCount()):
            item = self.fit_table.item(row, 0)
            if not item: continue
            fit_id = item.data(Qt.ItemDataRole.UserRole)
            
            fit_info = {
                'id': fit_id,
                # 'source' is the label an older build reads; 'source_id' is the
                # actual binding and survives renames and duplicate names.
                'source': self.fit_table.cellWidget(row, 1).currentText(),
                'source_id': self.fit_table.cellWidget(row, 1).currentData(),
                'type': self.fit_table.cellWidget(row, 2).currentText(),
                'color': self.fit_table.cellWidget(row, 5).color().name(),
                'style': self.fit_table.cellWidget(row, 6).currentText(),
                'thickness': self.fit_table.cellWidget(row, 7).findChild(QLineEdit).text(),
                'dialog_state': self.fit_dialogs[fit_id].get_state() if fit_id in self.fit_dialogs else None
            }
            config['fits'].append(fit_info)
            
        return config

    def _is_session_valid(self):
        """Checks if the current session contains meaningful data to save."""
        # 1. Check if a project path is set
        if not self.main_window.get_project_paths():
            return False
            
        # 2. Check if there are rows
        if self.plot_table.rowCount() == 0:
            return False
            
        # 3. Check if at least one row is valid (not just default "N/A")
        has_valid_row = False
        for row in range(self.plot_table.rowCount()):
            item = self.plot_table.item(row, 1)
            if item:
                # Check UserRole data or text for default "N/A" components
                name = item.data(Qt.ItemDataRole.UserRole) or item.text()
                # A default row usually looks like "N/A | N/A | N/A | N/A" or contains "Select"
                if "N/A | N/A" not in name and "Select" not in name:
                    has_valid_row = True
                    break
        
        return has_valid_row

    def save_session_to_file(self, path):
        """Saves session to a specific file path."""
        # Validate before saving to prevent overwriting good data with an empty state
        if not self._is_session_valid():
            print(f"[System] Save aborted: Session contains no valid data.")
            return False

        try:
            success = SettingsManager.save_state(path, self._get_current_state_dict())
            if success:
                print(f"[System] Saved session: {path} for mode Log Plot")
            else:
                print(f"[System] Failed to save session: {path}")
            return success
        except Exception as e:
            print(f"[System] Error saving session: {e}")
            return False

    def _save_state_on_exit(self):
        config_dir = os.path.join(os.path.expanduser('~'), '.LMPvisualizer')
        os.makedirs(config_dir, exist_ok=True)
        path = os.path.join(config_dir, 'autosave.json')
        SettingsManager.save_state(path, self._get_current_state_dict())

    def save_session(self):
        """Opens file dialog to save session manually."""
        path, _ = QFileDialog.getSaveFileName(self, "Save Session", "", "JSON Files (*.json)")
        if not path:
            return
        
        if self.save_session_to_file(path):
            QMessageBox.information(self, "Success", "Session saved successfully.")
        else:
            QMessageBox.critical(self, "Error", "Failed to save session.")
            
    def load_state_on_startup(self):
        config_dir = os.path.join(os.path.expanduser('~'), '.LMPvisualizer')
        os.makedirs(config_dir, exist_ok=True)
        path = os.path.join(config_dir, 'autosave.json')
        if os.path.exists(path):
            self.load_session_from_file(path)

    def load_session(self):
        """Opens file dialog to load session manually."""
        path, _ = QFileDialog.getOpenFileName(self, "Load Session", "", "JSON Files (*.json)")
        if path:
            self.load_session_from_file(path)

    def load_session_from_file(self, path: str):
        """Loads a session from a file, handling mode mismatches and preventing UI reload triggers."""
        if not path or not os.path.exists(path):
            return

        data = SettingsManager.load_state(path)
        if not data:
            QMessageBox.critical(self, "Error", "Failed to load log plot mode session file.")
            return

        # --- Mismatch Check ---
        file_type = data.get('type')
        if file_type not in ['log', 'log_plot']:
            # Check for known types
            is_dsd = file_type in ['dsd', 'dsd_plot']
            is_trj = file_type in ['trj', 'trj_plot']
            
            if is_dsd or is_trj:
                target_mode = "DSD Mode" if is_dsd else "Trajectory Plot"
                target_idx = self.main_window.MODE_DSD if is_dsd else self.main_window.MODE_TRJ
                target_panel = self.main_window.dsd_plot_panel if is_dsd else self.main_window.trj_plot_panel

                msg = QMessageBox(self.main_window)
                msg.setWindowTitle("Mode Mismatch")
                msg.setText(f"Mode Mismatch. This is a {target_mode} session.")
                abort_btn = msg.addButton("Abort", QMessageBox.ButtonRole.RejectRole)
                switch_btn = msg.addButton(f"Switch to {target_mode}", QMessageBox.ButtonRole.AcceptRole)
                msg.exec()

                if msg.clickedButton() == switch_btn:
                    # Save current (Log) state before switching
                    self.main_window.save_session_for_mode(self.main_window.MODE_LOG)
                    # Set flag to skip orchestration's autoload
                    self.main_window._skip_next_orchestration = True
                    # Switch to Target Mode
                    self.main_window.switch_to_mode(target_idx)
                    # After mode switch completes, force load the file in the new mode
                    target_panel.load_session_from_file(path)
                # If abort, do nothing - stay in current mode
                return
            else:
                 # Unknown Type -> Error only
                QMessageBox.warning(self.main_window, "Invalid Session File", 
                                    f"The file has an unknown or invalid type: '{file_type}'.\n"
                                    "Cannot load this session.")
                return

        print(f"[System] Loading session: {path} for mode Log Plot")


        # --- Load Logic ---
        self.average_user_choices = data.get('average_choices', {})
        self.global_label_map = data.get('global_label_map', {})
        self.custom_properties = data.get('custom_properties', {})
        self.data_manager.set_custom_properties(self.custom_properties)
        # Header selection must be restored BEFORE load_project so dialog can be suppressed on autoload
        hdr = data.get('allowed_headers')
        if hdr:
            try:
                self.allowed_headers = set(tuple(h) for h in hdr)
                self.data_manager.set_allowed_headers(self.allowed_headers)
            except Exception:
                self.allowed_headers = None
                self.data_manager.set_allowed_headers(None)
        else:
            self.allowed_headers = None
            self.data_manager.set_allowed_headers(None)

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

                # Internal load without UI warnings
                self.load_project(project_paths, keywords, show_discovery_warnings=False, keep_table=False)
                # Refresh combos
                self._refresh_axis_combos()

                # If relocated and project was found, auto-update the session file
                if relocated and self.data_manager.data:
                    if self.save_session_to_file(path):
                        print(f"[System] Relocation successful. Session file updated: {path}")

            # Restore Settings
            self.running_mean_setting = data.get('running_mean_setting', 'symmetric_window')
            self.enforce_zero_start = data.get('enforce_zero_start', False)
            self.quantize_points = data.get('quantize_points', None)
            self.smooth_before_averaging = bool(data.get('smooth_before_averaging', False))
            self.scale_lock_enabled = data.get('scale_lock', False)
            axes_locked = data.get('axes_lock', False)
            self.single_axis_view_lock_enabled = data.get('single_axis_view_lock', False)
            self.single_axis_view_ranges = data.get('single_axis_view_ranges')
            # Header selection already restored before load_project; keep as is (may have been updated by dialog)
            self.plot_controller.toggle_axes_lock(axes_locked)
            self.plot_controller.toggle_scale_lock(self.scale_lock_enabled)
            self._update_lock_button_visuals()

            # Restore Plots
            self.plot_table.setRowCount(0)
            self.next_plot_id = 0
            max_loaded_id = -1
            
            for plot_info in data.get('plots', []):
                row = self.plot_table.rowCount()
                self.plot_table.insertRow(row)
                
                saved_id = plot_info.get('id', self.next_plot_id)
                if isinstance(saved_id, int) and saved_id > max_loaded_id:
                    max_loaded_id = saved_id
                
                # Back-compat: Solid/Dash/Dot -> -/--/:
                _old_to_new = {"Solid": "-", "Dash": "--", "Dot": ":"}
                plot_data = {
                    'plot_name': plot_info.get('name', "N/A | N/A | N/A | N/A"),
                    'show': plot_info.get('show', True),
                    'mean': plot_info.get('mean', "0"),
                    'std': plot_info.get('std', False),
                    'color': plot_info.get('color', QColor("black").name()),
                    'style': _old_to_new.get(plot_info.get('style', "-"), plot_info.get('style', "-")),
                    'thickness': plot_info.get('thickness', "1"),
                    'plot_id': saved_id,
                    'origin': plot_info.get('origin')
                }
                if 'id' not in plot_info:
                    self.next_plot_id += 1
                self._populate_row_data(row, plot_data)

            # Rows are restored after load_project ran, so bind them to their paths here.
            self._resync_rows_to_paths(self.main_window.get_project_paths())

            if self.plot_table.rowCount() > 0:
                self.plot_table.selectRow(0)
                
            self.next_plot_id = max(self.next_plot_id, max_loaded_id + 1)
            self._update_all_row_displays()
            self._update_move_buttons_visibility()
            self._update_plot_labels()
            
            # Restore Selection
            saved_row = data.get('selected_row', 0)
            if saved_row >= 0 and saved_row < self.plot_table.rowCount():
                self.plot_table.selectRow(saved_row)

            # Restore Fits
            self._is_loading = True
            self.fit_table.setRowCount(0)
            
            # Close old dialogs before clearing the dict
            for dialog in self.fit_dialogs.values():
                dialog.close()

            self.fit_dialogs = {}
            self.fit_results = {}
            self.next_fit_id = 0
            
            saved_fits = data.get('fits', [])
            if saved_fits:
                max_id = 0
                for fit_info in saved_fits:
                    saved_id = fit_info['id']
                    if saved_id >= max_id: max_id = saved_id
                    
                    self.add_fit_row(target_fit_id=saved_id)
                    row = self.fit_table.rowCount() - 1

                    self._restore_fit_source(self.fit_table.cellWidget(row, 1),
                                             fit_info.get('source_id'),
                                             fit_info.get('source', ''))
                    self.fit_table.cellWidget(row, 2).setCurrentText(fit_info.get('type', 'Orig'))
                    self.fit_table.cellWidget(row, 5).set_color(QColor(fit_info.get('color', 'gray')))
                    _f_old_new = {"Solid": "-", "Dash": "--", "Dot": ":"}
                    self.fit_table.cellWidget(row, 6).setCurrentText(_f_old_new.get(fit_info.get('style', '--'), fit_info.get('style', '--')))
                    self.fit_table.cellWidget(row, 7).findChild(QLineEdit).setText(fit_info.get('thickness', '1'))
                    
                    if saved_id in self.fit_dialogs:
                        dialog = self.fit_dialogs[saved_id]
                        dialog.set_state(fit_info.get('dialog_state'))
                        func_str = dialog.func_input.text()
                        display_text = (func_str[:10] + "...") if len(func_str) > 10 else func_str
                        self.fit_table.cellWidget(row, 3).setText(display_text)

                self.next_fit_id = max_id + 1

            # Restore Fit Table Visibility
            fit_visible = data.get('fit_table_visible', False)
            if fit_visible != self.fit_table_visible:
                self._toggle_fit_ui()
            
            self._is_loading = False  # Clear flag before update
            self.update_plots() # Trigger update with recalculation
            
            # Update fit row colors after everything is loaded
            self._update_fit_row_colors()
            
            # Trigger fit calculations after state is fully restored
            if saved_fits:
                for fit_info in saved_fits:
                    fit_id = fit_info['id']
                    if fit_id in self.fit_dialogs:
                        dialog = self.fit_dialogs[fit_id]
                        # Force recalculation with restored function
                        if dialog.current_worker is None:  # Only if not already calculating
                            dialog.calculate_fit()

        finally:
            self.main_window.path_input.blockSignals(False)
            self.main_window.chip_input.blockSignals(False)

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
            self.plot_controller.export_plot(path, figsize=(width_in, height_in))

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
            self.plot_controller.export_plot(path, figsize=(width_in, height_in))

    def launch_popout_window(self):
        """Creates a new independent window with the current plot data."""
        if self.plot_table.rowCount() == 0:
            return

        try:
            import matplotlib
        except ImportError:
            QMessageBox.critical(self, "Error", "Matplotlib is required for the Pop Out feature.\nPlease install it via pip: pip install matplotlib")
            return

        plot_state = self.plot_controller.get_current_plot_state()
        
        if not plot_state['y_axes']:
            QMessageBox.information(self, "Info", "No visible data to display in Pop Out.")
            return

        dpi = self.logicalDpiX()
        w_in = self.plot_widget.width() / dpi
        h_in = self.plot_widget.height() / dpi
        
        popout = PopOutWindow(plot_state, figsize=(w_in, h_in))
        popout.show()
        
        # Keep reference to prevent GC
        self.popout_windows.append(popout)
        
        # Clean up closed windows
        # (Optional: Connect destroyed signal to remove from list, or just let list grow - small overhead)
        popout.destroyed.connect(lambda: self.popout_windows.remove(popout) if popout in self.popout_windows else None)
