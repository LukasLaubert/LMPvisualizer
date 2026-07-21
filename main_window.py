# lmp_visualizer/main_window.py

import sys
import os
from pathlib import Path
import re
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton,
    QComboBox, QFrame, QTableWidget, QHeaderView, QTableWidgetItem,
    QFileDialog, QMessageBox, QCheckBox, QLabel, QSplitter, QGridLayout, QSizePolicy, QMenu
)
from PyQt6.QtCore import Qt, pyqtSignal, QPoint
from PyQt6.QtGui import QColor, QIntValidator, QAction, QIcon
import pyqtgraph as pg
import random

from lammps_parser import LammpsParser
from data_manager import DataManager
from plotting_controller import PlottingController
from settings_manager import SettingsManager
from ui_components import ColorButton, InconsistentDataDialog, ChipInputWidget

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("LAMMPS Log Visualizer")
        self.setGeometry(100, 100, 1400, 800)
        self.setStyleSheet("QMainWindow { background-color: #f0f0f0; }")

        self.data_manager = DataManager()
        self.plot_controller = None
        self.sync_mean_enabled = False
        self.synchronized_columns = set()  # Track which columns are synchronized

        self.add_btn = QPushButton("Add")
        self.load_btn = QPushButton("Load")
        self.popout_btn = QPushButton("Pop Out")
        self.save_btn = QPushButton("Save")
        self.export_btn = QPushButton("Export")
        self.exit_btn = QPushButton("Exit")

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        
        top_controls_group = QFrame()
        top_controls_group.setFrameShape(QFrame.Shape.StyledPanel)
        top_controls_layout = QGridLayout(top_controls_group)
        
        top_controls_layout.addWidget(QLabel("Path:"), 0, 0)
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("Select Project Root Directory...")
        self.current_project_path = "" # Initialize empty project path
        top_controls_layout.addWidget(self.path_edit, 0, 1, 1, 4)
        self.browse_btn = QPushButton("Browse...")
        top_controls_layout.addWidget(self.browse_btn, 0, 5)

        top_controls_layout.addWidget(QLabel("Log Keywords:"), 1, 0)
        self.chip_input = ChipInputWidget()
        top_controls_layout.addWidget(self.chip_input, 1, 1, 1, 4)

        self.study_label_widget = QLabel("Study")
        top_controls_layout.addWidget(self.study_label_widget, 2, 0)
        self.study_combo = self._create_combo("Select Study")
        top_controls_layout.addWidget(self.study_combo, 2, 1)

        top_controls_layout.addWidget(QLabel("X-Axis"), 3, 0)
        self.xaxis_combo = self._create_combo("Select X-Axis")
        top_controls_layout.addWidget(self.xaxis_combo, 3, 1)

        self.system_label_widget = QLabel("System")
        top_controls_layout.addWidget(self.system_label_widget, 2, 2)
        self.system_combo = self._create_combo("Select System")
        top_controls_layout.addWidget(self.system_combo, 2, 3)

        top_controls_layout.addWidget(QLabel("Y-Axis"), 3, 2)
        self.yaxis_combo = self._create_combo("Select Y-Axis")
        top_controls_layout.addWidget(self.yaxis_combo, 3, 3)

        self.add_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        top_controls_layout.addWidget(self.add_btn, 2, 4, 2, 1)

        info_widget = QWidget()
        info_layout = QVBoxLayout(info_widget)
        info_layout.setContentsMargins(10, 0, 0, 0)
        self.studies_label = QLabel("Studies: 0")
        self.systems_label = QLabel("Systems: 0")
        self.units_label = QLabel("Unit: N/A")
        self.timestep_label = QLabel("Timestep: N/A")
        info_layout.addWidget(self.studies_label)
        info_layout.addWidget(self.systems_label)
        info_layout.addWidget(self.units_label)
        info_layout.addWidget(self.timestep_label)
        info_layout.addStretch()
        top_controls_layout.addWidget(info_widget, 2, 5, 2, 1)

        top_controls_layout.setColumnStretch(1, 1)
        top_controls_layout.setColumnStretch(3, 1)
        main_layout.addWidget(top_controls_group)

        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        
        # --- Left Panel (Plot) ---
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)

        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setBackground('w')
        self.plot_controller = PlottingController(self.plot_widget)

        # Add the lock button as an overlay on the plot widget
        self.lock_axes_btn = QPushButton()
        self.lock_axes_btn.setCheckable(True)
        self.lock_axes_btn.setIcon(QIcon.fromTheme("lock-unlocked")) # Placeholder
        self.lock_axes_btn.setToolTip("Lock/Unlock Y-Axes at Zero")
        self.lock_axes_btn.hide()
        self.lock_axes_btn.setParent(self.plot_widget)

        # Initially position the button at the top-right corner
        self.lock_axes_btn.setGeometry(self.plot_widget.width() - 40, 10, 30, 30)

        # Create a custom resize event for the plot widget to reposition the button
        original_resize = self.plot_widget.resizeEvent
        

        def custom_resize_event(event):
            # Position the button at the top-right of the plot widget
            if hasattr(self, 'lock_axes_btn'):
                self.lock_axes_btn.setGeometry(self.plot_widget.width() - 40, 10, 30, 30)
            # Call the original resize event to maintain normal functionality
            if original_resize:
                original_resize(event)

        self.plot_widget.resizeEvent = custom_resize_event

        left_layout.addWidget(self.plot_widget)

        plot_buttons_layout = QHBoxLayout()
        plot_buttons_layout.addWidget(self.popout_btn)
        plot_buttons_layout.addWidget(self.export_btn)
        left_layout.addLayout(plot_buttons_layout)

        # --- Right Panel (Table) ---
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        
        self.plot_table = QTableWidget()
        self.plot_table.setColumnCount(9)
        self.plot_table.setHorizontalHeaderLabels(["↨", "Plot", "Orig", "Mean", "Std", "", "Style", "thk", "Del"])
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
        self.plot_table.setColumnWidth(2, 35) # Orig
        self.plot_table.setColumnWidth(3, 45) # Mean
        self.plot_table.setColumnWidth(4, 35) # Std
        self.plot_table.setColumnWidth(5, 10) # Color
        self.plot_table.setColumnWidth(6, 35) # Style
        self.plot_table.setColumnWidth(7, 25) # thk
        self.plot_table.setColumnWidth(8, 25) # Del
        self.plot_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.plot_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.plot_table.verticalHeader().hide()

        # Enable header clicks to toggle synchronization
        header.sectionClicked.connect(self._on_header_clicked)
        right_layout.addWidget(self.plot_table)

        table_buttons_layout = QHBoxLayout()
        table_buttons_layout.addWidget(self.save_btn)
        table_buttons_layout.addWidget(self.load_btn)
        table_buttons_layout.addWidget(self.exit_btn)
        right_layout.addLayout(table_buttons_layout)

        main_splitter.addWidget(left_panel)
        main_splitter.addWidget(right_panel)
        main_splitter.setSizes([900, 500])
        main_layout.addWidget(main_splitter, 1)

        self._connect_signals()
        self._update_ui_state(project_loaded=False)

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

        split_pattern = r"([-_ ])"
        split_labels = [re.split(split_pattern, label) for label in labels]

        # Guard against empty strings from split
        split_labels = [s for s in split_labels if s]
        if len(split_labels) < 2:
            return labels

        min_len = min(len(s) for s in split_labels)
        common_parts_indices = []
        for i in range(min_len):
            if i % 2 == 0:  # Only check non-delimiter parts
                first_part = split_labels[0][i]
                if all(s[i] == first_part for s in split_labels):
                    common_parts_indices.append(i)

        if not common_parts_indices:
            return labels

        if len(split_labels[0]) == 1 and len(split_labels[0][0]) > 5 and 0 in common_parts_indices:
            part = split_labels[0][0]
            return [f"{part[:3]}..."] * len(labels)

        for i in common_parts_indices:
            for s in split_labels:
                if i < len(s):
                    s[i] = "..."
        
        new_labels = []
        for s in split_labels:
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
            new_labels.append("".join(result))
        return new_labels

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
        
        shortened_studies = MainWindow._shorten_labels(studies)
        shortened_systems = MainWindow._shorten_labels(systems)

        for row in range(rowCount):
            item = self.plot_table.item(row, 1)
            if item and row < len(full_names):
                parts = full_names[row].split(' | ')
                if len(parts) == 4:
                    short_study = shortened_studies[row] if row < len(shortened_studies) else parts[0]
                    short_system = shortened_systems[row] if row < len(shortened_systems) else parts[1]
                    new_name = f"{short_study} | {short_system} | {parts[2]} | {parts[3]}"
                    item.setText(new_name)

    def _extract_row_data(self, row_index):
        data = {}
        item = self.plot_table.item(row_index, 1)
        # Column 1: Plot name (QTableWidgetItem)
        data['plot_name'] = item.data(Qt.ItemDataRole.UserRole) or item.text() if item else "N/A | N/A | N/A | N/A"
        
        # Column 2: Show (QCheckBox)
        show_widget = self.plot_table.cellWidget(row_index, 2)
        data['show'] = show_widget.findChild(QCheckBox).isChecked() if show_widget else True
        
        # Column 3: Mean (QLineEdit)
        mean_edit = self.plot_table.cellWidget(row_index, 3)
        data['mean'] = mean_edit.findChild(QLineEdit).text() if mean_edit else "0"

        # Column 4: Std (QCheckBox)
        std_widget = self.plot_table.cellWidget(row_index, 4)
        data['std'] = std_widget.findChild(QCheckBox).isChecked() if std_widget else False

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
        name_item.setForeground(Qt.GlobalColor.gray if "N/A" in data['plot_name'] else Qt.GlobalColor.black)
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

    def _connect_signals(self):
        self.browse_btn.clicked.connect(self.browse_for_path)
        self.path_edit.editingFinished.connect(self.on_path_entered)
        self.add_btn.clicked.connect(self.add_new_plot_row)
        self.save_btn.clicked.connect(self.save_session)
        self.load_btn.clicked.connect(self.load_session)
        self.export_btn.clicked.connect(self.export_plot)
        self.exit_btn.clicked.connect(self.close)
        self.lock_axes_btn.toggled.connect(self._on_lock_axes_toggled)

        # Connect study and system selection to update available systems and selected row
        self.study_combo.currentTextChanged.connect(self.on_study_selected)
        self.system_combo.currentTextChanged.connect(self.update_selected_row_from_dropdowns)
        self.xaxis_combo.currentTextChanged.connect(self.update_selected_row_from_dropdowns)
        self.yaxis_combo.currentTextChanged.connect(self.update_selected_row_from_dropdowns)

        # Connect table selection changes to update dropdowns
        self.plot_table.itemSelectionChanged.connect(self.on_table_selection_changed)

    def _on_lock_axes_toggled(self, checked):
        if checked:
            self.lock_axes_btn.setIcon(QIcon.fromTheme("lock-locked"))
        else:
            self.lock_axes_btn.setIcon(QIcon.fromTheme("lock-unlocked"))
        self.plot_controller.toggle_axes_lock(checked)

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
            # If not synchronized, synchronize and copy value from first row
            self.synchronized_columns.add(column_index)

            # Copy the value from the first row to all other rows
            if self.plot_table.rowCount() > 1:
                self._sync_column_values(column_index)

            # Change header appearance to indicate it's synchronized with a different background color
            header.model().setHeaderData(column_index, Qt.Orientation.Horizontal,
                                        QColor(100, 200, 100), Qt.ItemDataRole.BackgroundRole)
                                        
        self.update_plots()

    def _sync_column_values(self, column_index):
        """Sync values in all rows of a column based on the first row."""
        if self.plot_table.rowCount() == 0:
            return

        # Get the value from the first row
        first_row_widget = self.plot_table.cellWidget(0, column_index)
        if not first_row_widget:
            # If it's a QTableWidgetItem (Plot name), handle it differently
            if column_index == 1:
                first_item = self.plot_table.item(0, 1)
                first_value = first_item.data(Qt.ItemDataRole.UserRole) if first_item else ""

                # Apply to all other rows
                for row in range(1, self.plot_table.rowCount()):
                    item = self.plot_table.item(row, 1)
                    if item:
                        item.setData(Qt.ItemDataRole.UserRole, first_value)
                        item.setText(first_value)
            return

        # Handle widget-based columns (2-7: Show, Mean, Std, Style, thk)
        first_widget = first_row_widget
        if column_index == 2:  # Show (checkbox)
            first_value = first_widget.findChild(QCheckBox).isChecked()
            for row in range(1, self.plot_table.rowCount()):
                widget = self.plot_table.cellWidget(row, column_index)
                if widget:
                    checkbox = widget.findChild(QCheckBox)
                    checkbox.blockSignals(True)
                    checkbox.setChecked(first_value)
                    checkbox.blockSignals(False)
        elif column_index == 3:  # Mean (line edit)
            first_value = first_widget.findChild(QLineEdit).text()
            for row in range(1, self.plot_table.rowCount()):
                # For Mean column sync, only sync to rows where Show column is active
                show_widget = self.plot_table.cellWidget(row, 2)  # Show column
                if show_widget:
                    show_checkbox = show_widget.findChild(QCheckBox)
                    if not show_checkbox.isChecked():  # Skip if Show is not active
                        continue

                widget = self.plot_table.cellWidget(row, column_index)
                if widget:
                    line_edit = widget.findChild(QLineEdit)
                    line_edit.blockSignals(True)
                    line_edit.setText(first_value)
                    line_edit.blockSignals(False)
        elif column_index == 4:  # Std (checkbox)
            first_value = first_widget.findChild(QCheckBox).isChecked()
            for row in range(1, self.plot_table.rowCount()):
                widget = self.plot_table.cellWidget(row, column_index)
                if widget:
                    checkbox = widget.findChild(QCheckBox)
                    checkbox.blockSignals(True)
                    checkbox.setChecked(first_value)
                    checkbox.blockSignals(False)
        elif column_index == 6:  # Style (combo box)
            first_value = first_widget.currentText()
            for row in range(1, self.plot_table.rowCount()):
                widget = self.plot_table.cellWidget(row, column_index)
                if widget:
                    widget.blockSignals(True)
                    widget.setCurrentText(first_value)
                    widget.blockSignals(False)
        elif column_index == 7:  # thk (line edit)
            first_value = first_widget.findChild(QLineEdit).text()
            for row in range(1, self.plot_table.rowCount()):
                widget = self.plot_table.cellWidget(row, column_index)
                if widget:
                    line_edit = widget.findChild(QLineEdit)
                    line_edit.blockSignals(True)
                    line_edit.setText(first_value)
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

            # For Mean column sync, only sync to rows where Show column is active
            if column_index == 3:  # Mean column
                show_widget = self.plot_table.cellWidget(row, 2)  # Show column
                if show_widget:
                    show_checkbox = show_widget.findChild(QCheckBox)
                    if not show_checkbox.isChecked():  # Skip if Show is not active
                        continue

            target_widget = self.plot_table.cellWidget(row, column_index)
            if target_widget:
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

    def browse_for_path(self):
        self.chip_input.add_chip_from_input()
        
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("Select Path Type")
        msg_box.setText("How do you want to select your data?")
        dir_button = msg_box.addButton("Project Directory", QMessageBox.ButtonRole.ActionRole)
        file_button = msg_box.addButton("Single Log File", QMessageBox.ButtonRole.ActionRole)
        msg_box.setStandardButtons(QMessageBox.StandardButton.Cancel)
        msg_box.exec()

        start_path = self.path_edit.text()
        if not os.path.exists(start_path):
            start_path = str(Path.home())

        if msg_box.clickedButton() == dir_button:
            path = QFileDialog.getExistingDirectory(self, "Select Project Root", directory=start_path)
        elif msg_box.clickedButton() == file_button:
            path, _ = QFileDialog.getOpenFileName(self, "Select Single Log File", directory=start_path)
        else:
            path = None

        if path:
            self.path_edit.setText(path)
            self.on_path_entered()
    
    def on_path_entered(self):
        self.chip_input.add_chip_from_input()
        path_str = self.path_edit.text()
        
        if not path_str or path_str == self.current_project_path:
            return
        
        path = Path(path_str)
        
        if not path.exists():
            QMessageBox.critical(self, "Error", "The specified path does not exist.")
            # Revert the text to the last valid path
            self.path_edit.setText(self.current_project_path)
            return

        if path.is_file():
            self.load_project(path)
        elif path.is_dir():
            try:
                # Restore the search for the project root for directories
                root_path = LammpsParser.find_project_root(path_str)
                self.path_edit.setText(str(root_path))
                self.load_project(root_path)
            except FileNotFoundError:
                # If find_project_root fails, it means it's not a standard project.
                # Proceed with the path as a potential flat directory.
                self.load_project(path)

        # On successful load, update the state variable to the new path.
        self.current_project_path = self.path_edit.text()

    def on_log_keywords_changed(self, keywords):
        if self.path_edit.text():
            self.on_path_entered()

    def load_project(self, root_path):
        keywords = self.chip_input.get_chips()
        if not keywords:
            self.data_manager.data.clear()
            self.data_manager.available_columns = []
            self._update_ui_state(project_loaded=False)
            return

        studies, warnings, file_map = LammpsParser.discover_studies_systems(root_path, keywords)
        
        # Hide/show study/system selectors based on project structure
        is_flat_structure = list(studies.keys()) == ['.']
        self.study_label_widget.setVisible(not is_flat_structure)
        self.study_combo.setVisible(not is_flat_structure)
        self.system_label_widget.setVisible(not is_flat_structure)
        self.system_combo.setVisible(not is_flat_structure)

        if warnings:
            # Filter out the non-standard structure warning if it was successful
            if not (is_flat_structure and "No standard project structure found" in warnings[0]):
                QMessageBox.warning(self, "Project Discovery Warning", "\n".join(warnings))
        
        warnings, successful_keywords = self.data_manager.load_project_data(studies, root_path, keywords, file_map)
        self.chip_input.update_chip_styles(successful_keywords)

        if warnings:
            QMessageBox.warning(self, "Data Loading Warning", "\n".join(warnings))
            
        self.studies_label.setText(f"Studies: {len(self.data_manager.get_study_names())}")
        
        studies_dict = self.data_manager.data
        if studies_dict:
            first_study_name = next(iter(studies_dict))
            num_systems = len(studies_dict[first_study_name])
            self.systems_label.setText(f"Systems: {num_systems}")
        else:
            self.systems_label.setText("Systems: 0")

        units = None
        timestep = None
        if root_path.is_dir():
            units = LammpsParser.get_units(root_path)
            timestep = LammpsParser.get_timestep(root_path)

        time_units_map = {'lj': 'tau', 'real': 'fs', 'metal': 'ps', 'si': 's', 'cgs': 's', 'electron': 'fs', 'micro': 'us', 'nano': 'ns'}
        self.units_label.setText(f"Unit: {units or 'N/A'}")
        time_unit = time_units_map.get(units, "")
        self.timestep_label.setText(f"Timestep: {timestep} {time_unit}" if timestep else "Timestep: N/A")

        self._update_ui_state(project_loaded=True)

    def _update_ui_state(self, project_loaded: bool):
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
            reset_combo(self.xaxis_combo, "Select X-Axis", self.data_manager.get_all_column_names())
            reset_combo(self.yaxis_combo, "Select Y-Axis", self.data_manager.get_all_column_names())
            
            if self.data_manager.get_study_names():
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

        self.update_selected_row_from_dropdowns()

    def on_table_selection_changed(self):
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
            systems = self.data_manager.get_system_names(study) if study != "N/A" else self.data_manager.get_all_system_names()
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

    def update_selected_row_from_dropdowns(self, text=""):
        if not self.plot_table.selectedItems() or self.plot_table.currentRow() == -1:
            return

        selected_row = self.plot_table.currentRow()
        plot_name_item = self.plot_table.item(selected_row, 1)
        if not plot_name_item:
            return

        study = self.study_combo.currentText() if self.study_combo.currentIndex() > 0 else "N/A"
        system = self.system_combo.currentText() if self.system_combo.currentIndex() > 0 else "N/A"
        x_ax = self.xaxis_combo.currentText() if self.xaxis_combo.currentIndex() > 0 else "N/A"
        y_ax = self.yaxis_combo.currentText() if self.yaxis_combo.currentIndex() > 0 else "N/A"
        
        plot_name = f"{study} | {system} | {x_ax} | {y_ax}"
        plot_name_item.setText(plot_name)
        plot_name_item.setData(Qt.ItemDataRole.UserRole, plot_name)

        is_complete = "N/A" not in [study, system, x_ax, y_ax]
        color = Qt.GlobalColor.black if is_complete else Qt.GlobalColor.gray
        plot_name_item.setForeground(color)

        self.update_plots()
        self._update_plot_labels()

    def add_new_plot_row(self):
        insert_row = 0
        self.plot_table.insertRow(insert_row)

        # Gather existing colors to generate a new, distinct color
        existing_colors = []
        for row in range(1, self.plot_table.rowCount()): # Skip the new row
            color_widget = self.plot_table.cellWidget(row, 5) # Color is at column 5
            if color_widget:
                existing_colors.append(color_widget.color())
        
        new_color = self._get_distinct_color(existing_colors)
        
        plot_data = {
            'plot_name': "N/A | N/A | N/A | N/A",
            'show': True,
            'mean': "0",
            'std': False,
            'color': new_color.name(),
            'style': "Solid",
            'thickness': "1"
        }

        if self.plot_table.rowCount() > 1:
            try:
                # Get data from the row that was previously at the top
                old_row_data = self._extract_row_data(1)
                parts = old_row_data['plot_name'].split(' | ')
                study, system, x_ax, _ = (parts + ['N/A'] * 4)[:4]
                
                plot_data['plot_name'] = f"{study} | {system} | {x_ax} | N/A"
                plot_data['style'] = old_row_data['style']

            except (AttributeError, ValueError, IndexError):
                pass
        
        self._populate_row_data(insert_row, plot_data)

        self.plot_table.selectRow(insert_row)
        self._update_move_buttons_visibility()
        self._update_plot_labels()

    def update_plots(self):
        self.plot_controller.clear_all_plots()

        all_plot_info = []
        for row in range(self.plot_table.rowCount()):
            try:
                item = self.plot_table.item(row, 1)
                if not item: continue

                plot_name = item.data(Qt.ItemDataRole.UserRole) or item.text()
                study, system, x_ax, y_ax = plot_name.split(' | ')
                if "N/A" in [study, system, x_ax, y_ax]:
                    continue

                show_original = self.plot_table.cellWidget(row, 2).findChild(QCheckBox).isChecked()
                mean_text = self.plot_table.cellWidget(row, 3).findChild(QLineEdit).text()
                mean_window = int(mean_text) if mean_text.isdigit() else 0
                show_std = self.plot_table.cellWidget(row, 4).findChild(QCheckBox).isChecked()
                color = self.plot_table.cellWidget(row, 5).color()
                style_text = self.plot_table.cellWidget(row, 6).currentText()
                style = {'Solid': Qt.PenStyle.SolidLine, 'Dash': Qt.PenStyle.DashLine, 'Dot': Qt.PenStyle.DotLine}.get(style_text)
                thickness = float(self.plot_table.cellWidget(row, 7).findChild(QLineEdit).text())

                plot_info = {
                    'row': row, 'plot_name': plot_name, 'study': study, 'system': system,
                    'x_ax': x_ax, 'y_ax': y_ax, 'color': color, 'style': style, 'thickness': thickness,
                    'show_original': show_original, 'mean_window': mean_window, 'show_std': show_std
                }
                all_plot_info.append(plot_info)
            except (ValueError, AttributeError, IndexError):
                continue
        
        for plot_info in all_plot_info:
            user_choices = None
            if plot_info['system'] == 'average':
                consistency = self.data_manager.check_data_consistency(plot_info['study'])
                if consistency:
                    dialog = InconsistentDataDialog(consistency, self)
                    if dialog.exec():
                        user_choices = dialog.get_choices()
                    else:
                        continue

            compute_raw_std = (
                plot_info['system'] == 'average' and 
                plot_info['show_std'] and 
                plot_info['mean_window'] == 0
            )
            
            data = self.data_manager.get_plot_data(
                plot_info['study'], plot_info['system'], plot_info['x_ax'], plot_info['y_ax'], 
                compute_raw_std, user_choices
            )
            
            if not data: continue

            data['y_col'] = plot_info['y_ax']
            original_x = data['x'].copy() if hasattr(data['x'], 'copy') else data['x']
            original_y = data['y'].copy() if hasattr(data['y'], 'copy') else data['y']

            if plot_info['show_original']:
                plot_data = data.copy()
                if not compute_raw_std:
                    plot_data['std'] = None
                
                self.plot_controller.add_or_update_plot(
                    plot_info['plot_name'], plot_data, plot_info['color'], 
                    plot_info['style'], thickness=plot_info['thickness']
                )

            if plot_info['mean_window'] > 1:
                running_mean_y = self._calculate_running_mean(original_y, plot_info['mean_window'])
                running_mean_x = original_x[:len(running_mean_y)]

                if plot_info['show_std']:
                    running_std = self._calculate_running_std(original_y, plot_info['mean_window'])
                    if running_std is not None:
                        std_data = {
                            'x': running_mean_x, 'y': running_mean_y, 'std': running_std[:len(running_mean_y)],
                            'y_col': plot_info['y_ax']
                        }
                        std_color = QColor(plot_info['color'])
                        std_color.setHsv(std_color.hue(), int(std_color.saturation() * 0.66), int(std_color.value() * 0.5), int(std_color.alpha() * 0.5))
                        self.plot_controller.add_or_update_plot_with_custom_colors(
                            plot_info['plot_name'] + "_running_mean_std", std_data, std_color, 
                            plot_info['style'], layer_priority=1
                        )

                mean_color = QColor(plot_info['color'])
                mean_color.setHsvF(mean_color.hueF(), mean_color.saturationF(), mean_color.valueF() * 0.5, mean_color.alphaF())
                mean_data = {'x': running_mean_x, 'y': running_mean_y, 'std': None, 'y_col': plot_info['y_ax']}
                self.plot_controller.add_or_update_plot_with_custom_colors(
                    plot_info['plot_name'] + "_running_mean", mean_data, mean_color, 
                    plot_info['style'], layer_priority=2, thickness=plot_info['thickness']
                )

        self._update_axis_properties([p for p in all_plot_info if p['show_original'] or p['mean_window'] > 1])
        
    def _handle_table_widget_change(self, row, column):
        """
        Unified handler for any widget change in the plot table.
        This enforces the correct order: first synchronize the state, then update the plot.
        """
        # First, apply synchronization logic. This will do nothing if the column isn't synced.
        self._update_sync_based_on_column(column, row)
        
        # Second, now that the entire table state is consistent, redraw the plot.
        self.update_plots()

    def _calculate_running_mean(self, data, window_size):
        """Calculate the running mean of data with specified window size using centered average."""
        if len(data) == 0:
            return data
        
        import numpy as np
        data = np.array(data)
        result = np.zeros_like(data, dtype=float)
        
        # Use a centered average approach: average from [i - window_size//2, i + window_size//2] for each position i
        half_window = window_size // 2
        
        for i in range(len(data)):
            start_idx = max(0, i - half_window)
            end_idx = min(len(data), i + half_window + 1)  # +1 because slicing is exclusive on the right
            result[i] = np.mean(data[start_idx:end_idx])
        
        return result

    def _calculate_running_std(self, data, window_size):
        """Calculate the running standard deviation of data with specified window size using centered approach."""
        if len(data) == 0:
            return data
        
        import numpy as np
        data = np.array(data)
        result = np.zeros_like(data, dtype=float)
        
        # Use a centered standard deviation approach: std from [i - window_size//2, i + window_size//2] for each position i
        half_window = window_size // 2
        
        for i in range(len(data)):
            start_idx = max(0, i - half_window)
            end_idx = min(len(data), i + half_window + 1)  # +1 because slicing is exclusive on the right
            result[i] = np.std(data[start_idx:end_idx])
        
        return result

    def _update_axis_properties(self, visible_plots_info):
        x_label = ""
        y_labels = {}
        for info in visible_plots_info:
            if not x_label: x_label = info['x_ax']
            if info['y_ax'] not in y_labels: y_labels[info['y_ax']] = info['y_ax']

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
        if not self.plot_table.selectedItems():
            return
            
        selected_row = self.plot_table.currentRow()

        if self.plot_table.rowCount() > 1:
            self.plot_table.removeRow(selected_row)
            self._update_move_buttons_visibility()
            self._update_plot_labels()
            self.update_plots()
        elif self.plot_table.rowCount() == 1:
            self.yaxis_combo.setCurrentIndex(0)

    def _create_centered_widget(self, widget: QWidget) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.addWidget(widget)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setContentsMargins(0,0,0,0)
        return container

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
        
    def _save_state_on_exit(self):
        config_dir = os.path.join(os.path.expanduser('~'), '.LMPvisualizer')
        os.makedirs(config_dir, exist_ok=True)
        path = os.path.join(config_dir, 'autosave.json')

        config = {'path': self.path_edit.text(), 'plots': []}
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
        
        SettingsManager.save_state(path, config)

    def closeEvent(self, event):
        self._save_state_on_exit()
        super().closeEvent(event)

    def save_session(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save Session", "", "JSON Files (*.json)")
        if not path:
            return
            
        config = {'path': self.path_edit.text(), 'plots': []}
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
        
        if SettingsManager.save_state(path, config):
            QMessageBox.information(self, "Success", "Session saved successfully.")
        else:
            QMessageBox.critical(self, "Error", "Failed to save session.")
            
    def load_state_on_startup(self):
        config_dir = os.path.join(os.path.expanduser('~'), '.LMPvisualizer')
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

        project_path = config.get('path')
        if project_path:
            self.path_edit.setText(project_path)
            try:
                root_path = Path(project_path)
                if not root_path.exists():
                    raise FileNotFoundError(f"Path from session file does not exist: {root_path}")
                
                self.load_project(root_path)

                self.plot_table.setRowCount(0)
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
                        'thickness': plot_info.get('thickness', "1")
                    }
                    self._populate_row_data(row, plot_data)
                
                if self.plot_table.rowCount() > 0:
                    self.plot_table.selectRow(0)
                self._update_move_buttons_visibility()
                self.update_plots()
                self._update_plot_labels()

            except FileNotFoundError as e:
                QMessageBox.critical(self, "Error", f"Could not find project path from session file:\n{e}")
        
        # Synchronize the current path state after a successful session load
        self.current_project_path = self.path_edit.text()

    def export_plot(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export Plot", "", "SVG Files (*.svg);;PDF Files (*.pdf)")
        if path:
            self.plot_controller.export_plot(path)