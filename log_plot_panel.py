import os
from pathlib import Path
import re
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton,
    QComboBox, QFrame, QTableWidget, QHeaderView, QTableWidgetItem,
    QMessageBox, QCheckBox, QLabel, QSplitter, QGridLayout, QSizePolicy, 
    QMenu, QFileDialog
)
from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtGui import QColor, QIntValidator, QAction, QActionGroup, QFont
import pyqtgraph as pg
import random
import numpy as np
import pandas as pd
import os

from log_parser import LogParser
from log_data_manager import LogDataManager
from log_controller import LogController
from settings_manager import SettingsManager
from ui_components import ColorButton, InconsistentDataDialog, RightClickButton, NoNewLineDelegate
from global_label_editor_dialog import GlobalLabelEditorDialog
from custom_property_dialog import CustomPropertyDialog
from popout_window import PopOutWindow
from fit_dialog import FitFunctionDialog

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
        
        # Fit Feature Properties
        self.fit_dialogs = {} # row_id -> FitFunctionDialog instance
        self.fit_results = {} # row_id -> result dict (x, y, error, params)
        self.fit_table_visible = False
        self.next_fit_id = 0 # Unique ID for fit rows to track dialogs reliably
        self.next_plot_id = 0 # Unique ID for plot rows

        # Track loaded path to prevent clearing data on mode switch
        self.loaded_path = None 

        self._init_ui()
        self._connect_signals()
        self._update_ui_state(project_loaded=False)

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
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

        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setBackground('w')
        self.plot_controller = LogController(self.plot_widget)

        self.lock_axes_btn = RightClickButton()
        self.lock_axes_btn.setCheckable(True)
        self.lock_axes_btn.setText("🔓")
        self.lock_axes_btn.setToolTip("Left Click: Lock/Unlock 0-alignment\nRight Click: Lock/Unlock Scaling")
        self.lock_axes_btn.hide()
        self.lock_axes_btn.setParent(self.plot_widget)
        self.lock_axes_btn.setGeometry(self.plot_widget.width() - 40, 00, 40, 30)

        original_resize = self.plot_widget.resizeEvent
        def custom_resize_event(event):
            if hasattr(self, 'lock_axes_btn'):
                self.lock_axes_btn.setGeometry(self.plot_widget.width() - 40, 00, 40, 30)
            if original_resize:
                original_resize(event)
        self.plot_widget.resizeEvent = custom_resize_event

        left_layout.addWidget(self.plot_widget)

        self.popout_btn = QPushButton("Pop Out")
        self.export_btn = QPushButton("Quick Export")
        plot_buttons_layout = QHBoxLayout()
        plot_buttons_layout.addWidget(self.popout_btn)
        plot_buttons_layout.addWidget(self.export_btn)
        left_layout.addLayout(plot_buttons_layout)

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
        self.plot_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
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
        self.hide_fit_btn = QPushButton("Hide Fitting")
        self.hide_fit_btn.clicked.connect(self._toggle_fit_ui)
        
        self.add_fit_btn.hide()
        self.hide_fit_btn.hide()
        
        fit_control_layout.addWidget(self.show_fit_btn)
        fit_control_layout.addWidget(self.add_fit_btn)
        fit_control_layout.addWidget(self.hide_fit_btn)
        
        top_table_layout.addWidget(self.fit_control_widget)

        # --- Table Buttons (Save/Load/Exit) ---
        self.save_btn = QPushButton("Save")
        self.load_btn = QPushButton("Load")
        self.exit_btn = QPushButton("Exit")
        table_buttons_layout = QHBoxLayout()
        table_buttons_layout.addWidget(self.save_btn)
        table_buttons_layout.addWidget(self.load_btn)
        table_buttons_layout.addWidget(self.exit_btn)
        # Note: Added to right_layout later to ensure it's at the bottom
        
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
        # Col 3 is now stretch (Function)
        self.fit_table.setColumnWidth(4, 50)
        self.fit_table.setColumnWidth(5, 10)
        self.fit_table.setColumnWidth(6, 35)
        self.fit_table.setColumnWidth(7, 25)
        self.fit_table.setColumnWidth(8, 25)
        self.fit_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.fit_table.verticalHeader().hide()
        
        fit_table_layout.addWidget(self.fit_table)
        
        self.fit_table_container.hide() # Hidden by default
        self.right_splitter.addWidget(self.fit_table_container)
        
        right_layout.addWidget(self.right_splitter)
        right_layout.addLayout(table_buttons_layout) # Buttons always at bottom

        main_splitter.addWidget(left_panel)
        main_splitter.addWidget(right_panel)
        main_splitter.setSizes([900, 500])
        main_layout.addWidget(main_splitter, 1)

    def _connect_signals(self):
        self.add_btn.clicked.connect(self.add_new_plot_row)
        self.save_btn.clicked.connect(self.save_session)
        self.load_btn.clicked.connect(self.load_session)
        self.export_btn.clicked.connect(self.export_plot)
        
        # Exit closes the whole application window
        self.exit_btn.clicked.connect(self.main_window.close)
        
        self.lock_axes_btn.toggled.connect(self._on_lock_axes_toggled)
        self.lock_axes_btn.rightClicked.connect(self._on_scale_lock_toggled)
        
        self.study_combo.currentTextChanged.connect(self.on_study_selected)
        self.system_combo.currentTextChanged.connect(lambda text: self._update_selected_row_name_component('system', text))
        # Use _handle_axis_change for axis combos to intercept "Custom" selection
        self.xaxis_combo.currentTextChanged.connect(lambda text: self._handle_axis_change(self.xaxis_combo, 'x_axis', text))
        self.yaxis_combo.currentTextChanged.connect(lambda text: self._handle_axis_change(self.yaxis_combo, 'y_axis', text))
        
        self.plot_table.itemSelectionChanged.connect(self.on_table_selection_changed)
        self.plot_table.cellDoubleClicked.connect(self._on_table_double_click)
        
        self.popout_btn.clicked.connect(self.launch_popout_window)

    def _toggle_fit_ui(self):
        self.fit_table_visible = not self.fit_table_visible
        
        if self.fit_table_visible:
            self.show_fit_btn.hide()
            self.add_fit_btn.show()
            self.hide_fit_btn.show()
            self.fit_table_container.show()
            
            if self.fit_table.rowCount() == 0:
                self.add_fit_row()
        else:
            self.show_fit_btn.show()
            self.add_fit_btn.hide()
            self.hide_fit_btn.hide()
            self.fit_table_container.hide()
            
        self.update_plots()

    def add_fit_row(self, target_fit_id=None):
        # QPushButton.clicked emits 'False'. We must ignore this bool to generate a new ID.
        if isinstance(target_fit_id, bool):
            target_fit_id = None

        row = self.fit_table.rowCount()
        self.fit_table.insertRow(row)
        
        # Determine ID
        if target_fit_id is not None:
            fit_id = target_fit_id
        else:
            fit_id = self.next_fit_id
            self.next_fit_id += 1
        
        # 1. Store ID in the Item (Source of Truth)
        id_item = QTableWidgetItem()
        id_item.setData(Qt.ItemDataRole.UserRole, fit_id)
        self.fit_table.setItem(row, 0, id_item)
        
        # 2. Setup Widgets
        self.fit_table.setCellWidget(row, 0, self._create_fit_move_widget())
        
        # Plot Combo
        plot_combo = QComboBox()
        plot_combo.currentIndexChanged.connect(self._on_fit_type_changed)
        self.fit_table.setCellWidget(row, 1, plot_combo)
        
        # Type Combo
        type_combo = QComboBox()
        type_combo.addItems(["Off", "Orig"])
        type_combo.setProperty("fit_id", fit_id) # Tag for signal handling
        type_combo.currentIndexChanged.connect(self._on_fit_type_changed)
        self.fit_table.setCellWidget(row, 2, type_combo)

        # Fit Fun Button
        fun_btn = QPushButton("a*x + b")
        fun_btn.setProperty("fit_id", fit_id) # Tag for signal handling
        fun_btn.clicked.connect(self._on_fit_fun_clicked)
        self.fit_table.setCellWidget(row, 3, fun_btn)
        
        # Error Label
        err_label = QLabel("N/A")
        err_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        err_label.setProperty("fit_id", fit_id) # Tag
        self.fit_table.setCellWidget(row, 4, err_label)
        
        # Color
        color_btn = ColorButton(QColor("gray"))
        color_btn.colorChanged.connect(self.update_plots)
        self.fit_table.setCellWidget(row, 5, color_btn)
        
        # Style
        style_combo = self._create_style_combo()
        style_combo.setCurrentText("Dash")
        style_combo.currentTextChanged.connect(self.update_plots)
        self.fit_table.setCellWidget(row, 6, style_combo)
        
        # Thickness
        thk_edit = QLineEdit("1")
        thk_edit.setValidator(QIntValidator(1, 10))
        thk_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thk_edit.textChanged.connect(self.update_plots)
        self.fit_table.setCellWidget(row, 7, self._create_centered_widget(thk_edit))
        
        # Delete Button
        del_btn = QPushButton("X")
        del_btn.setStyleSheet("color: red; font-weight: bold;")
        del_btn.setProperty("fit_id", fit_id) # Tag
        del_btn.clicked.connect(self.delete_fit_row)
        self.fit_table.setCellWidget(row, 8, self._create_centered_widget(del_btn))
        
        # Initialize Independent Dialog
        # Use default arg fid=fit_id to capture the specific integer for this row
        dialog = FitFunctionDialog(self)
        dialog.fitUpdated.connect(lambda res, fid=fit_id: self._on_fit_updated(fid, res))
        self.fit_dialogs[fit_id] = dialog
        
        self._update_fit_source_combos()
        self._update_fit_type_combos()
        
        type_combo.setCurrentIndex(0)

    def _on_fit_type_changed(self):
        """Handle changes in the Fit Type dropdown (Off/Orig/Mean)."""
        sender = self.sender()
        if not isinstance(sender, QComboBox): return
        
        fit_id = sender.property("fit_id")
        text = sender.currentText()
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
        self.fit_table.blockSignals(True)
        
        # 1. Swap ID Items (The Data)
        item1 = self.fit_table.item(r1, 0)
        item2 = self.fit_table.item(r2, 0)
        id1 = item1.data(Qt.ItemDataRole.UserRole)
        id2 = item2.data(Qt.ItemDataRole.UserRole)
        item1.setData(Qt.ItemDataRole.UserRole, id2)
        item2.setData(Qt.ItemDataRole.UserRole, id1)

        # 2. Update Widget Properties (The Signals)
        # We must re-tag the widgets so they point to their new location's ID
        for r, fid in [(r1, id2), (r2, id1)]:
            self.fit_table.cellWidget(r, 2).setProperty("fit_id", fid) # Type
            self.fit_table.cellWidget(r, 3).setProperty("fit_id", fid) # Fun Btn
            self.fit_table.cellWidget(r, 4).setProperty("fit_id", fid) # Err Lbl
            # Del Btn is inside a container
            container = self.fit_table.cellWidget(r, 8)
            if container:
                btn = container.findChild(QPushButton)
                if btn: btn.setProperty("fit_id", fid)

        # 3. Swap Visual Values
        def swap_combo(c):
            w1 = self.fit_table.cellWidget(r1, c)
            w2 = self.fit_table.cellWidget(r2, c)
            t1, t2 = w1.currentText(), w2.currentText()
            w1.setCurrentText(t2)
            w2.setCurrentText(t1)
        
        swap_combo(1) # Plot Combo
        swap_combo(2) # Type Combo
        swap_combo(6) # Style Combo
        
        # Swap Button Text
        btn1 = self.fit_table.cellWidget(r1, 3)
        btn2 = self.fit_table.cellWidget(r2, 3)
        txt1, txt2 = btn1.text(), btn2.text()
        btn1.setText(txt2)
        btn2.setText(txt1)
        
        # Swap Error Label Text
        lbl1 = self.fit_table.cellWidget(r1, 4)
        lbl2 = self.fit_table.cellWidget(r2, 4)
        txt1, txt2 = lbl1.text(), lbl2.text()
        lbl1.setText(txt2)
        lbl2.setText(txt1)
        
        # Swap Colors
        col1 = self.fit_table.cellWidget(r1, 5)
        col2 = self.fit_table.cellWidget(r2, 5)
        c1, c2 = col1.color(), col2.color()
        col1.set_color(c2)
        col2.set_color(c1)
        
        # Swap Thickness
        thk1 = self.fit_table.cellWidget(r1, 7).findChild(QLineEdit)
        thk2 = self.fit_table.cellWidget(r2, 7).findChild(QLineEdit)
        v1, v2 = thk1.text(), thk2.text()
        thk1.setText(v2)
        thk2.setText(v1)

        self.fit_table.blockSignals(False)
        self.update_plots()

    def _update_fit_source_combos(self):
        # 1. Gather all plot row options (Text + ID)
        options = []
        for r in range(self.plot_table.rowCount()):
            text = self._get_row_display_name(r)
            item = self.plot_table.item(r, 1)
            pid = item.data(Qt.ItemDataRole.UserRole + 1) if item else None
            options.append((text, pid))
            
        for r in range(self.fit_table.rowCount()):
            combo = self.fit_table.cellWidget(r, 1)
            # Apply delegate if needed
            if not isinstance(combo.view().itemDelegate(), NoNewLineDelegate):
                combo.setItemDelegate(NoNewLineDelegate(combo))
            
            # Save current selection
            current_id = combo.currentData()
            current_text = combo.currentText()

            combo.blockSignals(True)
            combo.clear()
            
            # Repopulate
            target_index = 0
            found_id = False
            
            for i, (text, pid) in enumerate(options):
                combo.addItem(text, pid) # pid stored in UserRole
                
                # Check match
                # Priority 1: Match by ID (robust to renaming)
                if current_id is not None and pid == current_id:
                    target_index = i
                    found_id = True
            
            # Priority 2: Match by Text (fallback for loaded sessions or fresh rows)
            if not found_id and current_text:
                index = combo.findText(current_text)
                if index != -1:
                    target_index = index

            if combo.count() > 0:
                combo.setCurrentIndex(target_index)
                
            combo.blockSignals(False)

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
            dialog = CustomPropertyDialog(self.data_manager.get_all_column_names(), self.custom_properties, self)
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

    def load_project(self, root_path, keywords, show_discovery_warnings: bool = True, force_reload: bool = False, keep_table: bool = False):
        # Prevent redundant reloading if path is same and not forced
        if not force_reload and self.loaded_path == root_path:
            return

        self.running_mean_setting = "symmetric_window" 
        self.average_user_choices.clear()
        
        if not keywords:
            self.data_manager.data.clear()
            self.data_manager.available_columns = []
            self.loaded_path = None # Clear loaded path
            self._update_ui_state(project_loaded=False)
            return

        studies, warnings, file_map = LogParser.discover_studies_systems(root_path, keywords)
        
        # Hide/show study/system selectors based on project structure
        is_flat_structure = list(studies.keys()) == ['.']
        self.study_label_widget.setVisible(not is_flat_structure)
        self.study_combo.setVisible(not is_flat_structure)
        self.system_label_widget.setVisible(not is_flat_structure)
        self.system_combo.setVisible(not is_flat_structure)

        if warnings and show_discovery_warnings:
            if not (is_flat_structure and "No standard project structure found" in warnings[0]):
                QMessageBox.warning(self, "Project Discovery Warning", "\n".join(warnings))
        
        warnings, successful_keywords = self.data_manager.load_project_data(studies, root_path, keywords, file_map)
        
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

        units = None
        timestep = None
        if root_path.is_dir():
            units = LogParser.get_units(root_path)
            timestep = LogParser.get_timestep(root_path)

        time_units_map = {'lj': 'tau', 'real': 'fs', 'metal': 'ps', 'si': 's', 'cgs': 's', 'electron': 'fs', 'micro': 'us', 'nano': 'ns'}
        self.main_window.units_label.setText(f"Unit: {units or 'N/A'}")
        time_unit = time_units_map.get(units, "")
        self.main_window.timestep_label.setText(f"Timestep: {timestep} {time_unit}" if timestep else "Timestep: N/A")

        # Update state tracking
        self.loaded_path = root_path
        
        self._update_ui_state(project_loaded=True)

    def on_keywords_changed(self, keywords):
        # Just reload if path exists
        path_str = self.main_window.path_edit.text()
        if path_str and os.path.exists(path_str):
            self.load_project(Path(path_str), keywords)

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
                    else:
                        short_system = shortened_systems[row] if row < len(shortened_systems) else system_name

                    new_name = f"{short_study} | {short_system} | {parts[2]} | {parts[3]}"
                    item.setText(new_name)

    def _extract_row_data(self, row_index):
        data = {}
        item = self.plot_table.item(row_index, 1)
        # Column 1: Plot name (QTableWidgetItem)
        data['plot_name'] = item.data(Qt.ItemDataRole.UserRole) or item.text() if item else "N/A | N/A | N/A | N/A"
        # Extract ID (UserRole + 1)
        if item:
            data['plot_id'] = item.data(Qt.ItemDataRole.UserRole + 1)

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
        if 'plot_id' in data:
            name_item.setData(Qt.ItemDataRole.UserRole + 1, data['plot_id'])
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

    def _update_selected_row_name_component(self, component: str, value: str):
        """Update only a specific component of the selected row's plot name."""
        # Intercept "Custom" selection here to be safe
        if value and value.strip().lower() == "custom":
            # Determine which combo triggered this based on component
            combo = None
            if component == 'x_axis': combo = self.xaxis_combo
            elif component == 'y_axis': combo = self.yaxis_combo
            
            if combo:
                self._open_custom_dialog(combo)
                return

        if self.plot_table.currentRow() == -1:
            return
        
        selected_row = self.plot_table.currentRow()
        plot_name_item = self.plot_table.item(selected_row, 1)
        if not plot_name_item:
            return
        
        # Get current name and parse it
        current_name = plot_name_item.data(Qt.ItemDataRole.UserRole) or plot_name_item.text()
        study, system, x_axis, y_axis = self._parse_plot_name(current_name)
        
        # Update only the specified component
        if component == 'study':
            study = value if value and value != "Select Study" else "N/A"
        elif component == 'system':
            system = value if value and value != "Select System" else "N/A"
        elif component == 'x_axis':
            x_axis = value if value and value != "Select X-Axis" else "N/A"
        elif component == 'y_axis':
            y_axis = value if value and value != "Select Y-Axis" else "N/A"
        
        # Rebuild name
        new_name = self._build_plot_name(study, system, x_axis, y_axis)
        plot_name_item.setData(Qt.ItemDataRole.UserRole, new_name)
        
        self._update_row_display(selected_row)
        
        self._update_row_visual_state(selected_row)
        self.update_plots()
        self._update_plot_labels()
        
        # Reset view when user manually changes an axis configuration
        if self.plot_controller:
            self.plot_controller.reset_view()

    def _connect_signals(self):
        self.add_btn.clicked.connect(self.add_new_plot_row)
        self.save_btn.clicked.connect(self.save_session)
        self.load_btn.clicked.connect(self.load_session)
        self.export_btn.clicked.connect(self.export_plot)
        
        # Exit closes the whole application window
        self.exit_btn.clicked.connect(self.main_window.close)
        
        self.lock_axes_btn.toggled.connect(self._on_lock_axes_toggled)
        self.lock_axes_btn.rightClicked.connect(self._on_scale_lock_toggled)
        
        self.study_combo.currentTextChanged.connect(self.on_study_selected)
        self.system_combo.currentTextChanged.connect(lambda text: self._update_selected_row_name_component('system', text))
        self.xaxis_combo.currentTextChanged.connect(lambda text: self._update_selected_row_name_component('x_axis', text))
        self.yaxis_combo.currentTextChanged.connect(lambda text: self._update_selected_row_name_component('y_axis', text))
        
        self.plot_table.itemSelectionChanged.connect(self.on_table_selection_changed)
        self.plot_table.cellDoubleClicked.connect(self._on_table_double_click)
        
        self.popout_btn.clicked.connect(self.launch_popout_window)

    def _on_table_double_click(self, row: int, column: int):
        """Handle double-click on table row to edit labels."""
        self._edit_global_labels()

    def _update_lock_button_visuals(self):
        """Updates the lock button text to reflect 0-lock (Checked) and Scale-lock (Flag) states."""
        # 0-Lock is represented by the button's Checked state (visualized by the OS/Theme, usually darker/blue)
        # Scale-Lock is represented by the Vertical Arrow symbol
        
        # Base icon: Locked or Unlocked based on 0-alignment (Position)
        text = "🔒" if self.lock_axes_btn.isChecked() else "🔓"
        
        # Append Vertical Arrow if Scaling is locked
        if self.scale_lock_enabled:
            text += " ↕"
            
        self.lock_axes_btn.setText(text)
        
        # Clear any specific stylesheets to ensure the native "Checked" state is visible
        self.lock_axes_btn.setStyleSheet("")

    def _on_lock_axes_toggled(self, checked):
        """Handle Left Click: Toggle 0-alignment lock."""
        self.plot_controller.toggle_axes_lock(checked)
        self._update_lock_button_visuals()

    def _on_scale_lock_toggled(self):
        """Handle Right Click: Toggle Scale lock."""
        self.scale_lock_enabled = not self.scale_lock_enabled
        self.plot_controller.toggle_scale_lock(self.scale_lock_enabled)
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

        action_valid = QAction("Valid Window (Exclude Borders)", self, checkable=True)
        action_valid.setData("valid_window")
        
        action_symmetric = QAction("Symmetric Window (Shrink Borders)", self, checkable=True)
        action_symmetric.setData("symmetric_window")
        
        action_asymmetric = QAction("Asymmetric Window (Use All Available)", self, checkable=True)
        action_asymmetric.setData("asymmetric_window")

        group.addAction(action_valid)
        group.addAction(action_symmetric)
        group.addAction(action_asymmetric)

        menu.addAction(action_valid)
        menu.addAction(action_symmetric)
        menu.addAction(action_asymmetric)

        # Set the checkmark on the currently active setting
        if self.running_mean_setting == "valid_window":
            action_valid.setChecked(True)
        elif self.running_mean_setting == "symmetric_window":
            action_symmetric.setChecked(True)
        else: # asymmetric_window
            action_asymmetric.setChecked(True)
        
        group.triggered.connect(self._set_running_mean_setting)
        
        menu.exec(header.mapToGlobal(pos))

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

    def load_project(self, root_path, keywords, show_discovery_warnings: bool = True, force_reload: bool = False, keep_table: bool = False):
        # Prevent redundant reloading if path is same and not forced
        if not force_reload and self.loaded_path == root_path:
            return

        if not keep_table:
            self.running_mean_setting = "symmetric_window" 
            self.average_user_choices.clear()
        
        # Use the passed keywords argument
        if not keywords:
            self.data_manager.data.clear()
            self.data_manager.available_columns = []
            self.loaded_path = None # Clear loaded path
            self._update_ui_state(project_loaded=False, keep_table=keep_table)
            return

        studies, warnings, file_map = LogParser.discover_studies_systems(root_path, keywords)
        
        # Hide/show study/system selectors based on project structure
        is_flat_structure = list(studies.keys()) == ['.']
        self.study_label_widget.setVisible(not is_flat_structure)
        self.study_combo.setVisible(not is_flat_structure)
        self.system_label_widget.setVisible(not is_flat_structure)
        self.system_combo.setVisible(not is_flat_structure)

        if warnings and show_discovery_warnings:
            if not (is_flat_structure and "No standard project structure found" in warnings[0]):
                QMessageBox.warning(self, "Project Discovery Warning", "\n".join(warnings))
        
        warnings, successful_keywords = self.data_manager.load_project_data(studies, root_path, keywords, file_map)
        
        # Access chip_input via main_window
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

        units = None
        timestep = None
        if root_path.is_dir():
            units = LogParser.get_units(root_path)
            timestep = LogParser.get_timestep(root_path)

        time_units_map = {'lj': 'tau', 'real': 'fs', 'metal': 'ps', 'si': 's', 'cgs': 's', 'electron': 'fs', 'micro': 'us', 'nano': 'ns'}
        self.main_window.units_label.setText(f"Unit: {units or 'N/A'}")
        time_unit = time_units_map.get(units, "")
        self.main_window.timestep_label.setText(f"Timestep: {timestep} {time_unit}" if timestep else "Timestep: N/A")

        # Update state tracking
        self.loaded_path = root_path
        
        self._update_ui_state(project_loaded=True, keep_table=keep_table)
        
        # If keeping table, trigger a plot update to refresh data sources
        if keep_table:
            self.update_plots()

    def _update_ui_state(self, project_loaded: bool, keep_table: bool = False):
        # Only clear table/plots if we are NOT keeping the table (e.g., new path load)
        if not keep_table:
            self.plot_table.setRowCount(0)
            self._update_move_buttons_visibility()
            self.plot_controller.clear_all_plots()

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
            
            # Use new population method for axes
            self._populate_axis_combo(self.xaxis_combo, self.data_manager.get_all_column_names())
            self._populate_axis_combo(self.yaxis_combo, self.data_manager.get_all_column_names())
            
            # Only add a default row if we cleared the table and have data
            if not keep_table:
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
        study = self.study_combo.currentText()

        def reset_combo_with_systems(combo, placeholder, items):
            combo.blockSignals(True)
            current_text = combo.currentText()
            combo.clear()
            combo.addItem(placeholder)
            if len(items) > 1:
                combo.addItem("average")
            combo.addItems(items)
            if current_text in items or (current_text == "average" and len(items) > 1):
                combo.setCurrentText(current_text)
            else:
                combo.setCurrentIndex(0)
            combo.blockSignals(False)

        systems = self.data_manager.get_system_names(study) if self.study_combo.currentIndex() > 0 else self.data_manager.get_all_system_names()
        reset_combo_with_systems(self.system_combo, "Select System", systems)

        # Update only the study component of the selected row
        self._update_selected_row_name_component('study', text)

    def on_table_selection_changed(self):
        # Update dropdowns to match selected row
        if not self.plot_table.selectedItems() or self.plot_table.currentRow() == -1:
            return

        selected_row = self.plot_table.currentRow()
        plot_name_item = self.plot_table.item(selected_row, 1)
        if not plot_name_item:
            return

        plot_name = plot_name_item.data(Qt.ItemDataRole.UserRole) or plot_name_item.text()
        
        for combo in [self.study_combo, self.system_combo, self.xaxis_combo, self.yaxis_combo]:
            combo.blockSignals(True)

        try:
            parts = plot_name.split(' | ')
            study, system, x_ax, y_ax = (parts + ['N/A'] * 4)[:4]

            def set_combo_text(combo, text):
                index = combo.findText(text)
                if index != -1:
                    combo.setCurrentIndex(index)
                else:
                    combo.setCurrentIndex(0)

            set_combo_text(self.study_combo, study)

            # Manually update system combo based on the selected study
            systems = self.data_manager.get_system_names(study) if study not in ["N/A", "."] else self.data_manager.get_all_system_names()
            self.system_combo.clear()
            self.system_combo.addItem("Select System")
            if len(systems) > 1:
                self.system_combo.addItem("average")
            self.system_combo.addItems(systems)

            set_combo_text(self.system_combo, system)
            set_combo_text(self.xaxis_combo, x_ax)
            set_combo_text(self.yaxis_combo, y_ax)
        
        finally:
            for combo in [self.study_combo, self.system_combo, self.xaxis_combo, self.yaxis_combo]:
                combo.blockSignals(False)
        
        # Trigger plot update because x-axis might need to change
        self.update_plots()

    def add_new_plot_row(self):
        source_row_index = self.plot_table.currentRow()

        insert_row = 0
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
            'style': "Solid",
            'thickness': "1",
            'plot_id': self.next_plot_id
        }
        self.next_plot_id += 1
        
        copied_from_selection = False
        # If a row was selected, copy its Study, System, X-Axis, and Y-Axis
        if source_row_index != -1:
            try:
                # The row we want to copy from is now at a new index
                row_to_copy_from = source_row_index + 1
                
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
        
        Args:
            auto_trigger_fits: If False, prevents automatic fit calculation triggers.
                              Used to prevent recursive calculations when called from fit callbacks.
        """
        # 1. Clear existing plots and reset view
        self.plot_controller.clear_all_plots()
        
        # [Fit Integration] Update sources for the fit table dropdowns so they match current plot names
        self._update_fit_source_combos()
        # [Fit Integration] Logic Change: Update available "Types" based on Source "Mean" availability
        self._update_fit_type_combos()
        
        selected_row_idx = self.plot_table.currentRow()
        all_plot_info = []
        
        # [Fit Integration] Cache to store source data (x, y) so fits can run even if source is hidden
        plot_data_cache = {}

        # --- A. Gather Configuration from Plot Table ---
        for row in range(self.plot_table.rowCount()):
            try:
                item = self.plot_table.item(row, 1)
                if not item: continue

                plot_name = item.data(Qt.ItemDataRole.UserRole) or item.text()
                display_name = self._get_row_display_name(row) # Used to match fit source dropdowns
                
                study, system, x_ax, y_ax = plot_name.split(' | ')
                is_valid = "N/A" not in [study, system, x_ax, y_ax]

                # UI State Retrieval
                show_original = self.plot_table.cellWidget(row, 2).findChild(QCheckBox).isChecked()
                mean_text = self.plot_table.cellWidget(row, 3).findChild(QLineEdit).text()
                mean_window = int(mean_text) if mean_text.isdigit() else 0
                show_std = self.plot_table.cellWidget(row, 4).findChild(QCheckBox).isChecked()
                color = self.plot_table.cellWidget(row, 5).color()
                style_text = self.plot_table.cellWidget(row, 6).currentText()
                style = {'Solid': Qt.PenStyle.SolidLine, 'Dash': Qt.PenStyle.DashLine, 'Dot': Qt.PenStyle.DotLine}.get(style_text)
                thickness = float(self.plot_table.cellWidget(row, 7).findChild(QLineEdit).text())

                is_active = show_original or (mean_window > 0) or show_std
                
                # [Fit Integration] Check if this plot is a source for an ACTIVE fit
                is_fit_source = False
                if self.fit_table_visible:
                    for fr in range(self.fit_table.rowCount()):
                        src_combo = self.fit_table.cellWidget(fr, 1)
                        # Type combo is at column 2
                        type_combo = self.fit_table.cellWidget(fr, 2)
                        # It is a source only if names match AND the fit type is NOT "Off"
                        if src_combo and src_combo.currentText() == display_name and type_combo.currentText() != "Off":
                            is_fit_source = True
                            break

                plot_info = {
                    'row': row, 'plot_name': plot_name, 'display_name': display_name,
                    'study': study, 'system': system, 'x_ax': x_ax, 'y_ax': y_ax, 
                    'color': color, 'style': style, 'thickness': thickness,
                    'show_original': show_original, 'mean_window': mean_window, 'show_std': show_std,
                    'is_valid': is_valid, 'is_active': is_active,
                    'is_fit_source': is_fit_source
                }
                all_plot_info.append(plot_info)
            except (ValueError, AttributeError, IndexError):
                continue
        
        # --- Determine Preferred Axes (for View Locking) ---
        preferred_x_ax = None
        selected_y_ax = None 

        if selected_row_idx != -1 and selected_row_idx < len(all_plot_info):
            selected_plot_info = all_plot_info[selected_row_idx]
            if selected_plot_info['is_active'] and selected_plot_info['is_valid']:
                preferred_x_ax = selected_plot_info['x_ax']
                selected_y_ax = selected_plot_info['y_ax']

        if not preferred_x_ax:
            for info in all_plot_info:
                if info['is_active'] and info['is_valid']:
                    preferred_x_ax = info['x_ax']
                    break
        
        # Sort plots: Selected Y-axis plots draw last (on top)
        all_plot_info.sort(key=lambda x: (x['y_ax'] == selected_y_ax if selected_y_ax else False, x['row']))

        # --- B. Main Loop: Data Fetching, Caching, and Plotting ---
        for plot_info in all_plot_info:
            if not plot_info['is_valid']: continue
            
            # [Optimization] Skip data fetch only if plot is HIDDEN AND NOT needed for a fit
            if not plot_info['is_active'] and not plot_info['is_fit_source']:
                continue

            # Resolve Labels
            x_label = self.global_label_map.get(plot_info['x_ax'], plot_info['x_ax'])
            y_label = self.global_label_map.get(plot_info['y_ax'], plot_info['y_ax'])
            
            z_offset = 100 if plot_info['row'] == selected_row_idx else 0
            
            # --- Consistency Check (Original Logic) ---
            user_choices = None
            if plot_info['system'] == 'average':
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
            compute_raw_std = (
                plot_info['system'] == 'average' and 
                plot_info['show_std'] and 
                plot_info['mean_window'] == 0
            )
            
            current_x_ax = preferred_x_ax or plot_info['x_ax']

            # --- Fetch Data ---
            data = self.data_manager.get_plot_data(
                plot_info['study'], plot_info['system'], current_x_ax, plot_info['y_ax'], 
                compute_raw_std, user_choices
            )
            
            if not data: continue

            # [Fit Integration] Cache Data for Engine
            try:
                x_np = data['x'].to_numpy(dtype=float) if hasattr(data['x'], 'to_numpy') else np.array(data['x'], dtype=float)
                y_np = data['y'].to_numpy(dtype=float) if hasattr(data['y'], 'to_numpy') else np.array(data['y'], dtype=float)
            except ValueError:
                continue 

            plot_data_cache[plot_info['display_name']] = {
                'x': x_np, 
                'y': y_np, 
                'color': plot_info['color'],
                'mean_window': plot_info['mean_window'],
                'raw_y_ax': plot_info['y_ax'],
                'y_label': y_label
            }

            # [Fit Integration] Pre-calculate running mean if needed for fit (Mean Type)
            running_mean_y = None
            running_mean_x = None

            if plot_info['mean_window'] >= 1:
                running_mean_y = self._calculate_running_average(y_np, plot_info['mean_window'], 'mean')
                len_diff = len(x_np) - len(running_mean_y)
                if len_diff > 0:
                    start_idx = len_diff // 2
                    end_idx = len(x_np) - (len_diff - start_idx)
                    running_mean_x = x_np[start_idx:end_idx]
                else:
                    running_mean_x = x_np
                
                plot_data_cache[plot_info['display_name']]['mean_x'] = running_mean_x
                plot_data_cache[plot_info['display_name']]['mean_y'] = running_mean_y

            # [Fit Integration] Barrier: If plot is hidden in table, stop here.
            if not plot_info['is_active']:
                continue

            # --- Visualization Preparation ---
            data['y_col'] = plot_info['y_ax']
            data['y_label'] = y_label if y_label else plot_info['y_ax']
            
            display_x = x_label if x_label else plot_info['x_ax']
            display_y = y_label if y_label else plot_info['y_ax']
            legend_name = f"{plot_info['study']} | {plot_info['system']} | {display_x} | {display_y}"

            # --- 1. Draw Original Data ---
            if plot_info['show_original']:
                plot_data = data.copy()
                if not compute_raw_std: plot_data['std'] = None
                self.plot_controller.add_or_update_plot(
                    legend_name, plot_data, plot_info['color'], 
                    plot_info['style'], thickness=plot_info['thickness'],
                    layer_priority=z_offset
                )

            # --- 2. Draw Running Mean / Std Deviation ---
            if plot_info['mean_window'] >= 1:
                if plot_info['show_std']:
                    running_std = self._calculate_running_average(y_np, plot_info['mean_window'], 'std')
                    if running_std is not None and len(running_std) == len(running_mean_y):
                        std_data = {
                            'x': running_mean_x, 'y': running_mean_y, 'std': running_std, 
                            'y_col': plot_info['y_ax'], 'y_label': data['y_label']
                        }
                        std_color = QColor(plot_info['color'])
                        std_color.setHsv(std_color.hue(), int(std_color.saturation() * 0.66), int(std_color.value() * 0.5), int(std_color.alpha() * 0.5))
                        
                        self.plot_controller.add_or_update_plot_with_custom_colors(
                            legend_name + "_running_mean_std", std_data, std_color, 
                            Qt.PenStyle.NoPen, layer_priority=1 + z_offset
                        )
                
                if plot_info['mean_window'] > 0:
                    mean_color = QColor(plot_info['color'])
                    if plot_info['show_original']:
                        mean_color.setHsvF(mean_color.hueF(), mean_color.saturationF(), mean_color.valueF() * 0.5, mean_color.alphaF())
                    else:
                        mean_color.setHsvF(mean_color.hueF(), mean_color.saturationF(), mean_color.valueF() * 0.8, mean_color.alphaF())
                    
                    mean_data = {
                        'x': running_mean_x, 'y': running_mean_y, 'std': None, 
                        'y_col': plot_info['y_ax'], 'y_label': data['y_label']
                    }
                    self.plot_controller.add_or_update_plot_with_custom_colors(
                        legend_name + "_running_mean", mean_data, mean_color, 
                        plot_info['style'], layer_priority=2 + z_offset, thickness=plot_info['thickness']
                    )

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
                    
                    source_name = self.fit_table.cellWidget(r, 1).currentText()
                    fit_type = self.fit_table.cellWidget(r, 2).currentText()
                    
                    if fit_type == "Off":
                        continue

                    if source_name not in plot_data_cache:
                        continue
                        
                    src_data = plot_data_cache[source_name]
                    
                    # Visuals
                    color_btn = self.fit_table.cellWidget(r, 5)
                    # Auto-color logic
                    if color_btn.color() == QColor("gray"):
                        src_color = src_data['color']
                        new_color = QColor(src_color)
                        new_color.setHsvF(new_color.hueF(), new_color.saturationF(), max(0, new_color.valueF() * 0.75), new_color.alphaF())
                        color_btn.blockSignals(True)
                        color_btn.set_color(new_color)
                        color_btn.blockSignals(False)
                    
                    fit_color = color_btn.color()
                    style_text = self.fit_table.cellWidget(r, 6).currentText()
                    style = {'Solid': Qt.PenStyle.SolidLine, 'Dash': Qt.PenStyle.DashLine, 'Dot': Qt.PenStyle.DotLine}.get(style_text)
                    thickness = float(self.fit_table.cellWidget(r, 7).findChild(QLineEdit).text())

                    # Data Source Selection
                    if fit_type == "Mean" and 'mean_x' in src_data:
                        x_fit_src = src_data['mean_x']
                        y_fit_src = src_data['mean_y']
                    else:
                        x_fit_src = src_data['x']
                        y_fit_src = src_data['y']

                    # Push Data to Dialog
                    if fit_id in self.fit_dialogs:
                        dialog = self.fit_dialogs[fit_id]
                        data_changed = dialog.set_data(x_fit_src, y_fit_src)
                        
                        # Auto-calculate if data changed and idle, BUT only if auto_trigger_fits is True
                        if auto_trigger_fits and data_changed and dialog.current_worker is None:
                            dialog.calculate_fit()
                    
                    # Plot Result
                    if fit_id in self.fit_results:
                        res = self.fit_results[fit_id]
                        if res.get('status') in ['Success', 'Fit Poor / Failed']:
                            legend = f"Fit: {source_name} ({fit_type}) [ID:{fit_id}]"
                            
                            fit_y_col = src_data.get('raw_y_ax', 'N/A')
                            fit_y_label = src_data.get('y_label', 'N/A')
                            
                            fit_plot_data = {
                                'x': res['x_fit'],
                                'y': res['y_fit'],
                                'std': None,
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
        self.current_x_axis = preferred_x_ax
        visible_plots = [p for p in all_plot_info if p['is_active'] and p['is_valid']]
        
        self._update_axis_properties(visible_plots, preferred_x_ax)
        
        for row in range(self.plot_table.rowCount()):
            self._update_row_visual_state(row)
        
        self.plot_controller.apply_current_locks()

    def _update_fit_type_combos(self):
        """
        New logic: Iterates through fit rows, checks if the selected source has a valid
        Mean Window. If not, removes 'Mean' from the dropdown. If 'Mean' was selected
        and is no longer valid, auto-switches to 'Orig'.
        """
        # 1. Build a map of Source Display Name -> HasValidMean
        source_mean_map = {}
        for r in range(self.plot_table.rowCount()):
            display_name = self._get_row_display_name(r)
            mean_widget = self.plot_table.cellWidget(r, 3)
            has_valid_mean = False
            if mean_widget:
                txt = mean_widget.findChild(QLineEdit).text()
                if txt.isdigit() and int(txt) > 0:
                    has_valid_mean = True
            source_mean_map[display_name] = has_valid_mean

        # 2. Update Fit Table Rows
        for r in range(self.fit_table.rowCount()):
            src_combo = self.fit_table.cellWidget(r, 1)
            type_combo = self.fit_table.cellWidget(r, 2)
            
            if not src_combo or not type_combo: continue
            
            selected_source = src_combo.currentText()
            has_mean = source_mean_map.get(selected_source, False)
            
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
        This enforces the correct order: first synchronize the state, then update the plot.
        """
        # First, apply synchronization logic. This will do nothing if the column isn't synced.
        self._update_sync_based_on_column(column, row)
        
        # Second, now that the entire table state is consistent, redraw the plot.
        self.update_plots()

        # Update the visual state of the row (e.g., for greying out)
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

        if self.running_mean_setting == 'valid_window':
            # 1) Exclude border values. Output is shorter.
            # min_periods=window_size ensures only full windows are used.
            rolling_obj = series.rolling(window=window_size, center=True, min_periods=window_size)
            if statistic == 'mean':
                result = rolling_obj.mean()
            else: # std
                result = rolling_obj.std()
            return result.dropna().to_numpy()

        elif self.running_mean_setting == 'symmetric_window':
            # 2) Symmetric window, shrinks at borders.
            data_np = np.array(data)
            result = np.zeros_like(data_np, dtype=float)
            half_window = window_size // 2
            
            for i in range(len(data_np)):
                # Determine symmetric radius
                k = min(half_window, i, len(data_np) - 1 - i)
                start_idx = i - k
                end_idx = i + k + 1
                window_slice = data_np[start_idx:end_idx]
                
                if statistic == 'mean':
                    result[i] = np.mean(window_slice)
                else: # std
                    result[i] = np.std(window_slice)
            return result

        else: # 'asymmetric_window'
            # 3) Use all available values up to window size (original behavior)
            data_np = np.array(data)
            result = np.zeros_like(data_np, dtype=float)
            half_window = window_size // 2
            
            for i in range(len(data_np)):
                start_idx = max(0, i - half_window)
                end_idx = min(len(data_np), i + half_window + 1)
                window_slice = data_np[start_idx:end_idx]
                
                if statistic == 'mean':
                    result[i] = np.mean(window_slice)
                else: # std
                    result[i] = np.std(window_slice)
            return result

    def _update_axis_properties(self, visible_plots_info, x_label_override: str = None):
        # x_label_override is the x-property from the selected row.
        x_label = self.global_label_map.get(x_label_override, x_label_override) if x_label_override else ""

        y_labels = {}
        for info in visible_plots_info:
            if info['y_ax'] not in y_labels:
                y_labels[info['y_ax']] = self.global_label_map.get(info['y_ax'], info['y_ax'])

        self.plot_controller.set_axis_labels(x_label, y_labels)

        # --- Axis Coloring Logic ---
        black_color = QColor("black")
        # Only color axes if there are multiple different y-axes being shown
        if len(y_labels) > 1:
            for info in visible_plots_info:
                self.plot_controller.set_axis_color(info['y_ax'], info['color'])
        else:
            # Reset all axes to black if one or zero plots are visible
            for y_ax in self.plot_controller.y_axes.keys():
                self.plot_controller.set_axis_color(y_ax, black_color)
        
        # Show/hide lock button
        self.lock_axes_btn.setVisible(len(y_labels) > 1)

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

        self.plot_table.removeRow(row_to_delete)

        # After removing, if the table is now empty, add a new default row.
        if self.plot_table.rowCount() == 0:
            self.add_new_plot_row()
        else:
            # Otherwise, update everything.
            self._update_move_buttons_visibility()
            self._update_plot_labels()
            self.update_plots()

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
        combo.addItems(["Solid", "Dash", "Dot"])
        font_metrics = combo.fontMetrics()
        max_width = 0
        for i in range(combo.count()):
            width = font_metrics.horizontalAdvance(combo.itemText(i))
            if width > max_width:
                max_width = width
        combo.view().setMinimumWidth(max_width + 30)
        return combo
        
    def _get_current_state_dict(self):
        """Helper to gather current state for saving."""
        config = {
            'path': self.main_window.path_edit.text(),
            'average_choices': self.average_user_choices,
            'running_mean_setting': self.running_mean_setting,
            'global_label_map': self.global_label_map,
            'custom_properties': self.custom_properties,
            'scale_lock': self.scale_lock_enabled,
            'axes_lock': self.lock_axes_btn.isChecked(),
            'view_ranges': self.plot_controller.get_view_ranges(),
            'fit_table_visible': self.fit_table_visible,
            'plots': [],
            'fits': []
        }
        
        # Save Plots
        for row in range(self.plot_table.rowCount()):
            item = self.plot_table.item(row, 1)
            if item:
                full_name = item.data(Qt.ItemDataRole.UserRole) or item.text()
                plot_info = {
                    'name': full_name,
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
            item = self.fit_table.item(row, 0) # ID is here
            if not item: continue
            fit_id = item.data(Qt.ItemDataRole.UserRole)
            
            fit_info = {
                'id': fit_id,
                'source': self.fit_table.cellWidget(row, 1).currentText(),
                'type': self.fit_table.cellWidget(row, 2).currentText(),
                'color': self.fit_table.cellWidget(row, 5).color().name(),
                'style': self.fit_table.cellWidget(row, 6).currentText(),
                'thickness': self.fit_table.cellWidget(row, 7).findChild(QLineEdit).text(),
                'dialog_state': self.fit_dialogs[fit_id].get_state() if fit_id in self.fit_dialogs else None
            }
            config['fits'].append(fit_info)
            
        return config

    def _save_state_on_exit(self):
        config_dir = os.path.join(os.path.expanduser('~'), '.LMPvisualizer')
        os.makedirs(config_dir, exist_ok=True)
        path = os.path.join(config_dir, 'autosave.json')
        SettingsManager.save_state(path, self._get_current_state_dict())

    def save_session(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save Session", "", "JSON Files (*.json)")
        if not path:
            return
        
        if SettingsManager.save_state(path, self._get_current_state_dict()):
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
        path, _ = QFileDialog.getOpenFileName(self, "Load Session", "", "JSON Files (*.json)")
        if path:
            self.load_session_from_file(path)

    def load_session_from_file(self, path: str):
        if not path:
            return

        config = SettingsManager.load_state(path)
        if not config:
            QMessageBox.critical(self, "Error", "Failed to load session file.")
            return

        self.average_user_choices = config.get('average_choices', {})
        self.global_label_map = config.get('global_label_map', {})
        self.custom_properties = config.get('custom_properties', {})
        self.data_manager.set_custom_properties(self.custom_properties)

        project_path = config.get('path')
        if project_path:
            self.main_window.path_edit.setText(project_path)
            try:
                root_path = Path(project_path)
                if not root_path.exists():
                    raise FileNotFoundError(f"Path from session file does not exist: {root_path}")
                
                # Get keywords from UI and pass them to load_project
                keywords = self.main_window.chip_input.get_chips()
                self.load_project(root_path, keywords, show_discovery_warnings=False)
                
                # Refresh combos to show loaded custom properties
                self._refresh_axis_combos()
                
                # Load settings
                self.running_mean_setting = config.get('running_mean_setting', 'symmetric_window')
                self.scale_lock_enabled = config.get('scale_lock', False)
                axes_locked = config.get('axes_lock', False)
                self.lock_axes_btn.setChecked(axes_locked)
                self.plot_controller.toggle_scale_lock(self.scale_lock_enabled)
                self._update_lock_button_visuals()

                # Restore Plots
                self.plot_table.setRowCount(0)
                self.next_plot_id = 0
                for plot_info in config.get('plots', []):
                    row = self.plot_table.rowCount()
                    self.plot_table.insertRow(row)
                    
                    plot_data = {
                        'plot_name': plot_info.get('name', "N/A | N/A | N/A | N/A"),
                        'show': plot_info.get('show', True),
                        'mean': plot_info.get('mean', "0"),
                        'std': plot_info.get('std', False),
                        'color': plot_info.get('color', QColor("black").name()),
                        'style': plot_info.get('style', "Solid"),
                        'thickness': plot_info.get('thickness', "1"),
                        'plot_id': self.next_plot_id
                    }
                    self.next_plot_id += 1
                    self._populate_row_data(row, plot_data)
                
                if self.plot_table.rowCount() > 0:
                    self.plot_table.selectRow(0)

                self._update_all_row_displays()
                self._update_move_buttons_visibility()
                self._update_plot_labels()
                
                # Restore Fits
                # First clear existing fits and reset state
                self.fit_table.setRowCount(0)
                self.fit_dialogs = {}
                self.fit_results = {}
                self.next_fit_id = 0
                
                saved_fits = config.get('fits', [])
                if saved_fits:
                    max_id = 0
                    for fit_info in saved_fits:
                        saved_id = fit_info['id']
                        if saved_id >= max_id: max_id = saved_id
                        
                        # 1. Create Row with the EXACT Saved ID
                        # This ensures signals are connected to 'saved_id' from the start
                        self.add_fit_row(target_fit_id=saved_id)
                        
                        row = self.fit_table.rowCount() - 1
                        
                        # 2. Restore Widget Values
                        # (Note: IDs on widgets are already correct due to add_fit_row)
                        self.fit_table.cellWidget(row, 1).setCurrentText(fit_info.get('source', ''))
                        self.fit_table.cellWidget(row, 2).setCurrentText(fit_info.get('type', 'Orig'))
                        self.fit_table.cellWidget(row, 5).set_color(QColor(fit_info.get('color', 'gray')))
                        self.fit_table.cellWidget(row, 6).setCurrentText(fit_info.get('style', 'Dash'))
                        self.fit_table.cellWidget(row, 7).findChild(QLineEdit).setText(fit_info.get('thickness', '1'))
                        
                        # 3. Restore Dialog State
                        if saved_id in self.fit_dialogs:
                            dialog = self.fit_dialogs[saved_id]
                            dialog.set_state(fit_info.get('dialog_state'))
                            
                            # Update Function Button Text
                            func_str = dialog.func_input.text()
                            display_text = (func_str[:10] + "...") if len(func_str) > 10 else func_str
                            self.fit_table.cellWidget(row, 3).setText(display_text)

                    # Ensure future IDs don't collide
                    self.next_fit_id = max_id + 1

                # Restore Visibility
                fit_visible = config.get('fit_table_visible', False)
                if fit_visible != self.fit_table_visible:
                    self._toggle_fit_ui() # This calls update_plots internally
                
                # Explicit update if fit visibility didn't trigger it
                if not fit_visible:
                    self.update_plots()

            except FileNotFoundError as e:
                QMessageBox.critical(self, "Error", f"Could not find project path from session file:\n{e}")
        
        # Synchronize the current path state after a successful session load
        self.main_window.current_project_path = self.main_window.path_edit.text()

    def export_plot(self):
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
        path, filter_used = QFileDialog.getSaveFileName(self, "Export Plot", "", filters)
        
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