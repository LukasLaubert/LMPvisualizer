# lmp_visualizer/main_window.py

import sys
import os
from pathlib import Path
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton,
    QComboBox, QFrame, QTableWidget, QHeaderView, QTableWidgetItem,
    QFileDialog, QMessageBox, QCheckBox, QLabel, QSplitter, QGridLayout, QSizePolicy
)
from PyQt6.QtCore import Qt, pyqtSignal, QPoint
from PyQt6.QtGui import QColor
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
        top_controls_layout.addWidget(self.path_edit, 0, 1, 1, 4)
        self.browse_btn = QPushButton("Browse...")
        top_controls_layout.addWidget(self.browse_btn, 0, 5)

        top_controls_layout.addWidget(QLabel("Log Keywords:"), 1, 0)
        self.chip_input = ChipInputWidget()
        top_controls_layout.addWidget(self.chip_input, 1, 1, 1, 4)

        top_controls_layout.addWidget(QLabel("Study"), 2, 0)
        self.study_combo = self._create_combo("Select Study")
        top_controls_layout.addWidget(self.study_combo, 2, 1)

        top_controls_layout.addWidget(QLabel("X-Axis"), 3, 0)
        self.xaxis_combo = self._create_combo("Select X-Axis")
        top_controls_layout.addWidget(self.xaxis_combo, 3, 1)

        top_controls_layout.addWidget(QLabel("System"), 2, 2)
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
        left_layout.addWidget(self.plot_widget)

        plot_buttons_layout = QHBoxLayout()
        plot_buttons_layout.addWidget(self.popout_btn)
        plot_buttons_layout.addWidget(self.export_btn)
        left_layout.addLayout(plot_buttons_layout)

        # --- Right Panel (Table) ---
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        
        # Add the new horizontal layout for the three checkmarks and number field
        processing_options_layout = QHBoxLayout()
        
        self.original_values_checkbox = QCheckBox("Original values")
        self.original_values_checkbox.setChecked(True)  # Default to checked
        processing_options_layout.addWidget(self.original_values_checkbox)
        
        self.running_mean_checkbox = QCheckBox("Running mean")
        processing_options_layout.addWidget(self.running_mean_checkbox)
        
        self.running_mean_field = QLineEdit("100")
        self.running_mean_field.setMaximumWidth(60)
        processing_options_layout.addWidget(self.running_mean_field)
        
        self.running_mean_std_checkbox = QCheckBox("Running mean std")
        processing_options_layout.addWidget(self.running_mean_std_checkbox)
        
        processing_options_layout.addStretch()  # Add stretch to fill remaining space
        right_layout.addLayout(processing_options_layout)
        
        self.std_checkbox = QCheckBox("Compute std")
        right_layout.addWidget(self.std_checkbox)

        self.plot_table = QTableWidget()
        self.plot_table.setColumnCount(7)
        self.plot_table.setHorizontalHeaderLabels(["↨", "Plot", "Color", "Style", "thk", "Show", "Del"])
        header = self.plot_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
        self.plot_table.setColumnWidth(0, 10)
        self.plot_table.setColumnWidth(2, 40)
        self.plot_table.setColumnWidth(3, 40)
        self.plot_table.setColumnWidth(4, 30)
        self.plot_table.setColumnWidth(5, 40)
        self.plot_table.setColumnWidth(6, 30)
        self.plot_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.plot_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.plot_table.verticalHeader().hide()
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
        down_button = QPushButton("▼")
        
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

    def _extract_row_data(self, row_index):
        data = {}
        # Column 1: Plot name (QTableWidgetItem)
        data['plot_name'] = self.plot_table.item(row_index, 1).text() if self.plot_table.item(row_index, 1) else "N/A | N/A | N/A | N/A"
        # Column 2: Color (ColorButton)
        color_btn = self.plot_table.cellWidget(row_index, 2)
        data['color'] = color_btn.color().name() if color_btn else QColor("black").name()
        # Column 3: Style (QComboBox)
        style_combo = self.plot_table.cellWidget(row_index, 3)
        data['style'] = style_combo.currentText() if style_combo else "Solid"
        # Column 4: Thickness (QLineEdit)
        thk_edit = self.plot_table.cellWidget(row_index, 4)
        data['thickness'] = thk_edit.findChild(QLineEdit).text() if thk_edit else "1"
        # Column 5: Show (QCheckBox)
        show_widget = self.plot_table.cellWidget(row_index, 5)
        data['show'] = show_widget.findChild(QCheckBox).isChecked() if show_widget else True
        return data

    def _populate_row_data(self, row_index, data):
        # Column 0: Move buttons
        move_widget = self._create_move_widget()
        self.plot_table.setCellWidget(row_index, 0, move_widget)

        # Column 1: Plot name
        name_item = QTableWidgetItem(data['plot_name'])
        name_item.setForeground(Qt.GlobalColor.gray if "N/A" in data['plot_name'] else Qt.GlobalColor.black)
        self.plot_table.setItem(row_index, 1, name_item)

        # Column 2: Color
        color_btn = ColorButton(QColor(data['color']))
        color_btn.colorChanged.connect(self.update_plots)
        self.plot_table.setCellWidget(row_index, 2, color_btn)

        # Column 3: Style
        style_combo = self._create_style_combo()
        style_combo.setCurrentText(data['style'])
        style_combo.currentTextChanged.connect(self.update_plots)
        self.plot_table.setCellWidget(row_index, 3, style_combo)

        # Column 4: Thickness
        thk_edit = QLineEdit(data['thickness'])
        thk_edit.textChanged.connect(self.update_plots)
        self.plot_table.setCellWidget(row_index, 4, self._create_centered_widget(thk_edit))

        # Column 5: Show
        show_check = QCheckBox()
        show_check.setChecked(data['show'])
        show_check.stateChanged.connect(self.update_plots)
        self.plot_table.setCellWidget(row_index, 5, self._create_centered_widget(show_check))

        # Column 6: Delete button
        del_btn = QPushButton("X")
        del_btn.setStyleSheet("color: red; font-weight: bold;")
        del_btn.clicked.connect(self.delete_plot_row)
        self.plot_table.setCellWidget(row_index, 6, self._create_centered_widget(del_btn))

    def swap_rows(self, r1, r2):
        self.plot_table.blockSignals(True)

        row1_data = self._extract_row_data(r1)
        row2_data = self._extract_row_data(r2)

        self._populate_row_data(r1, row2_data)
        self._populate_row_data(r2, row1_data)

        self.plot_table.blockSignals(False)
        self.update_plots()

    def _create_combo(self, placeholder: str) -> QComboBox:
        combo = QComboBox()
        combo.addItem(placeholder)
        combo.setCurrentIndex(0)
        return combo

    def _connect_signals(self):
        self.browse_btn.clicked.connect(self.browse_for_directory)
        self.path_edit.editingFinished.connect(self.on_path_entered)
        self.chip_input.chipsChanged.connect(self.on_log_keywords_changed)
        
        self.plot_table.itemSelectionChanged.connect(self.on_table_selection_changed)
        
        self.study_combo.currentTextChanged.connect(self.on_study_selected)
        self.system_combo.currentTextChanged.connect(self.update_selected_row_from_dropdowns)
        self.xaxis_combo.currentTextChanged.connect(self.update_selected_row_from_dropdowns)
        self.yaxis_combo.currentTextChanged.connect(self.update_selected_row_from_dropdowns)
        
        self.original_values_checkbox.stateChanged.connect(self.update_plots)
        self.running_mean_checkbox.stateChanged.connect(self.update_plots)
        self.running_mean_field.textChanged.connect(self.update_plots)
        self.running_mean_std_checkbox.stateChanged.connect(self.update_plots)
        self.std_checkbox.stateChanged.connect(self.update_plots)
        self.add_btn.clicked.connect(self.add_new_plot_row)
        self.save_btn.clicked.connect(self.save_session)
        self.load_btn.clicked.connect(self.load_session)
        self.export_btn.clicked.connect(self.export_plot)
        self.exit_btn.clicked.connect(self.close)

    
    def browse_for_directory(self):
        self.chip_input.add_chip_from_input()
        start_path = self.path_edit.text()
        start_path = os.path.expanduser(start_path)
        if not os.path.isdir(start_path):
            start_path = str(Path.home())

        directory = QFileDialog.getExistingDirectory(self, "Select Project Root", directory=start_path)
        if directory:
            self.path_edit.setText(directory)
            self.on_path_entered()
    
    def on_path_entered(self):
        self.chip_input.add_chip_from_input()
        path = self.path_edit.text()
        if not path:
            return
        try:
            root_path = LammpsParser.find_project_root(path)
            self.path_edit.setText(str(root_path))
            self.load_project(root_path)
        except FileNotFoundError as e:
            QMessageBox.critical(self, "Error", str(e))

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

        studies, warnings = LammpsParser.discover_studies_systems(root_path)
        if warnings:
            QMessageBox.warning(self, "Project Discovery Warning", "\n".join(warnings))
        
        warnings, successful_keywords = self.data_manager.load_project_data(studies, root_path, keywords)
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

        time_units_map = {'lj': 'tau', 'real': 'fs', 'metal': 'ps', 'si': 's', 'cgs': 's', 'electron': 'fs', 'micro': 'us', 'nano': 'ns'}
        units = LammpsParser.get_units(root_path)
        self.units_label.setText(f"Unit: {units or 'N/A'}")
        timestep = LammpsParser.get_timestep(root_path)
        time_unit = time_units_map.get(units, "")
        self.timestep_label.setText(f"Timestep: {timestep} {time_unit}" if timestep else "Timestep: N/A")

        self._update_ui_state(project_loaded=True)

    def _update_ui_state(self, project_loaded: bool):
        self.plot_table.setRowCount(0)
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

        plot_name = plot_name_item.text()
        
        for combo in [self.study_combo, self.system_combo, self.xaxis_combo, self.yaxis_combo]:
            combo.blockSignals(True)

        try:
            parts = plot_name.split(' | ')
            study, system, x_ax, y_ax = (parts + ['N/A'] * 4)[:4]

            def set_combo_text(combo, text):
                if text == 'N/A' or text not in [combo.itemText(i) for i in range(combo.count())]:
                    combo.setCurrentIndex(0)
                else:
                    combo.setCurrentText(text)

            set_combo_text(self.study_combo, study)
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

        is_complete = "N/A" not in [study, system, x_ax, y_ax]
        color = Qt.GlobalColor.black if is_complete else Qt.GlobalColor.gray
        plot_name_item.setForeground(color)

        self.update_plots()

    def add_new_plot_row(self):
        insert_row = 0
        self.plot_table.insertRow(insert_row)

        # Gather existing colors to generate a new, distinct color
        existing_colors = []
        for row in range(self.plot_table.rowCount()):
            if row == insert_row: continue
            color_widget = self.plot_table.cellWidget(row, 2)
            if color_widget:
                existing_colors.append(color_widget.color())
        
        color = self._get_distinct_color(existing_colors)
        plot_name = "N/A | N/A | N/A | N/A"
        style = "Solid"

        if self.plot_table.rowCount() > 1:
            try:
                old_plot_name = self.plot_table.item(1, 1).text()
                parts = old_plot_name.split(' | ')
                study, system, x_ax, _ = (parts + ['N/A'] * 4)[:4]
                plot_name = f"{study} | {system} | {x_ax} | N/A"
                style = self.plot_table.cellWidget(1, 3).currentText()
            except (AttributeError, ValueError):
                pass

        move_widget = self._create_move_widget()
        self.plot_table.setCellWidget(insert_row, 0, move_widget)

        name_item = QTableWidgetItem(plot_name)
        name_item.setForeground(Qt.GlobalColor.gray)
        self.plot_table.setItem(insert_row, 1, name_item)

        color_btn = ColorButton(color)
        color_btn.colorChanged.connect(self.update_plots)
        self.plot_table.setCellWidget(insert_row, 2, color_btn)

        style_combo = self._create_style_combo()
        style_combo.setCurrentText(style)
        style_combo.currentTextChanged.connect(self.update_plots)
        self.plot_table.setCellWidget(insert_row, 3, style_combo)

        thk_edit = QLineEdit("1")
        thk_edit.textChanged.connect(self.update_plots)
        self.plot_table.setCellWidget(insert_row, 4, self._create_centered_widget(thk_edit))

        show_check = QCheckBox()
        show_check.setChecked(True)
        show_check.stateChanged.connect(self.update_plots)
        self.plot_table.setCellWidget(insert_row, 5, self._create_centered_widget(show_check))

        del_btn = QPushButton("X")
        del_btn.setStyleSheet("color: red; font-weight: bold;")
        del_btn.clicked.connect(self.delete_plot_row)
        self.plot_table.setCellWidget(insert_row, 6, self._create_centered_widget(del_btn))

        self.plot_table.selectRow(insert_row)

    def update_plots(self):
        self.plot_controller.clear_all_plots()

        # Step 1: Gather all information for visible plots in a single loop
        visible_plots_info = []
        for row in range(self.plot_table.rowCount()):
            show_widget = self.plot_table.cellWidget(row, 5)
            show_checkbox = show_widget.findChild(QCheckBox)
            if not (show_checkbox and show_checkbox.isChecked()):
                continue

            item = self.plot_table.item(row, 1)
            if not item: continue

            plot_name = item.text()
            try:
                study, system, x_ax, y_ax = plot_name.split(' | ')
                if "N/A" in [study, system, x_ax, y_ax]:
                    continue
                
                color = self.plot_table.cellWidget(row, 2).color()
                style_text = self.plot_table.cellWidget(row, 3).currentText()
                style = {'Solid': Qt.PenStyle.SolidLine, 'Dash': Qt.PenStyle.DashLine, 'Dot': Qt.PenStyle.DotLine}.get(style_text)

                try:
                    thickness = float(self.plot_table.cellWidget(row, 4).findChild(QLineEdit).text())
                except (ValueError, AttributeError):
                    thickness = 1.0

                visible_plots_info.append({
                    'plot_name': plot_name, 'study': study, 'system': system,
                    'x_ax': x_ax, 'y_ax': y_ax, 'color': color, 'style': style,
                    'thickness': thickness
                })
            except ValueError:
                continue

        # Step 2: Create all plots and their axes
        # First, collect all the original plot data and add original plots
        processed_plot_data = []
        for plot_info in visible_plots_info:
            user_choices = None
            if plot_info['system'] == 'average':
                consistency = self.data_manager.check_data_consistency(plot_info['study'])
                if consistency:
                    dialog = InconsistentDataDialog(consistency, self)
                    if dialog.exec():
                        user_choices = dialog.get_choices()
                    else:
                        continue # Skip this plot if user cancels
            
            data = self.data_manager.get_plot_data(
                plot_info['study'], plot_info['system'], plot_info['x_ax'], plot_info['y_ax'], 
                self.std_checkbox.isChecked(), user_choices
            )
            
            if data:
                data['y_col'] = plot_info['y_ax']
                
                # Process data based on new checkboxes
                # Get the running mean window size from the field
                try:
                    running_mean_window = int(self.running_mean_field.text())
                    if running_mean_window <= 0:
                        running_mean_window = 1  # Minimum window size
                except ValueError:
                    running_mean_window = 100  # Default value if conversion fails
                
                # Create a copy of original data for processing
                original_x = data['x'].copy() if hasattr(data['x'], 'copy') else data['x']
                original_y = data['y'].copy() if hasattr(data['y'], 'copy') else data['y']
                
                # Add original values if checkbox is checked
                if self.original_values_checkbox.isChecked():
                    self.plot_controller.add_or_update_plot(
                        plot_info['plot_name'], data, plot_info['color'], 
                        plot_info['style'], thickness=plot_info['thickness']
                    )
                
                # Store processed data for running mean and running mean std calculation if needed
                if self.running_mean_checkbox.isChecked() or self.running_mean_std_checkbox.isChecked():
                    running_mean_y = self._calculate_running_mean(original_y, running_mean_window)
                    running_mean_x = original_x[:len(running_mean_y)]  # Adjust x to match new y length
                    running_std = self._calculate_running_std(original_y, running_mean_window) if self.running_mean_std_checkbox.isChecked() else None
                    
                    processed_plot_data.append({
                        'plot_info': plot_info,
                        'running_mean_x': running_mean_x,
                        'running_mean_y': running_mean_y,
                        'running_std': running_std,
                        'original_color': plot_info['color']
                    })

        # After all original plots are added, add running mean std plots (in front of originals but behind running mean)
        for plot_data in processed_plot_data:
            plot_info = plot_data['plot_info']
            running_mean_x = plot_data['running_mean_x']
            running_mean_y = plot_data['running_mean_y']
            running_std = plot_data['running_std']
            original_color = plot_data['original_color']
            
            # Add running mean std if checkbox is checked
            if self.running_mean_std_checkbox.isChecked() and running_std is not None:
                running_mean_std_data = {
                    'x': running_mean_x,
                    'y': running_mean_y,
                    'std': running_std[:len(running_mean_y)],
                    'y_col': plot_info['y_ax']
                }
                
                # Set std band color to 2/3 saturation with 50% opacity and 50% value
                std_color = QColor(original_color)
                h, s, v, a = std_color.getHsv()
                std_color.setHsv(h, int(s * (2.0/3.0)), int(v * 0.5), int(a * 0.5))  # 2/3 saturation, 50% value, 50% opacity
                
                self.plot_controller.add_or_update_plot_with_custom_colors(
                    plot_info['plot_name'] + "_running_mean_std", 
                    running_mean_std_data, 
                    std_color, 
                    plot_info['style'],
                    layer_priority=1  # In front of original values but behind running mean
                )
        
        # Finally, add running mean plots (in front of everything else)
        for plot_data in processed_plot_data:
            plot_info = plot_data['plot_info']
            running_mean_x = plot_data['running_mean_x']
            running_mean_y = plot_data['running_mean_y']
            original_color = plot_data['original_color']
            
            # Add running mean curve if checkbox is checked
            if self.running_mean_checkbox.isChecked():
                # Create data for running mean with half the value of original color
                mean_color = QColor(original_color)
                mean_color.setHsvF(
                    mean_color.hueF(), 
                    mean_color.saturationF(), 
                    mean_color.valueF() * 0.5,  # Half the value (brightness)
                    mean_color.alphaF()
                )
                
                running_mean_data = {
                    'x': running_mean_x,
                    'y': running_mean_y,
                    'std': None,  # std will be handled by std band if needed
                    'y_col': plot_info['y_ax']
                }
                
                # Add running mean curve in front of original and std band
                self.plot_controller.add_or_update_plot_with_custom_colors(
                    plot_info['plot_name'] + "_running_mean", 
                    running_mean_data, 
                    mean_color, 
                    plot_info['style'],
                    layer_priority=2,  # In front of both original and std band
                    thickness=plot_info['thickness']
                )

        # Step 3: Update axis labels and apply conditional coloring
        self._update_axis_properties(visible_plots_info)


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

    def delete_plot_row(self):
        if not self.plot_table.selectedItems():
            return
            
        selected_row = self.plot_table.currentRow()

        if self.plot_table.rowCount() > 1:
            self.plot_table.removeRow(selected_row)
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
        
    def save_session(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save Session", "", "JSON Files (*.json)")
        if not path:
            return
            
        config = {'path': self.path_edit.text(), 'plots': []}
        for row in range(self.plot_table.rowCount()):
            item = self.plot_table.item(row, 1)
            if item:
                plot_info = {
                    'name': item.text(),
                    'color': self.plot_table.cellWidget(row, 2).color().name(),
                    'style': self.plot_table.cellWidget(row, 3).currentText(),
                    'thickness': self.plot_table.cellWidget(row, 4).findChild(QLineEdit).text(),
                    'show': self.plot_table.cellWidget(row, 5).findChild(QCheckBox).isChecked()
                }
                config['plots'].append(plot_info)
        
        if SettingsManager.save_state(path, config):
            QMessageBox.information(self, "Success", "Session saved successfully.")
        else:
            QMessageBox.critical(self, "Error", "Failed to save session.")
            
    def load_session(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load Session", "", "JSON Files (*.json)")
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
                root_path = LammpsParser.find_project_root(project_path)
                self.load_project(root_path)

                self.plot_table.setRowCount(0)
                for plot_info in config.get('plots', []):
                    row = self.plot_table.rowCount()
                    self.plot_table.insertRow(row)
                    
                    move_widget = self._create_move_widget()
                    self.plot_table.setCellWidget(row, 0, move_widget)

                    name = plot_info['name']
                    is_complete = "N/A" not in name
                    color = Qt.GlobalColor.black if is_complete else Qt.GlobalColor.gray
                    
                    item = QTableWidgetItem(name)
                    item.setForeground(color)
                    self.plot_table.setItem(row, 1, item)
                    
                    color_btn = ColorButton(QColor(plot_info['color']))
                    color_btn.colorChanged.connect(self.update_plots)
                    self.plot_table.setCellWidget(row, 2, color_btn)

                    style_combo = self._create_style_combo()
                    style_combo.setCurrentText(plot_info['style'])
                    style_combo.currentTextChanged.connect(self.update_plots)
                    self.plot_table.setCellWidget(row, 3, style_combo)
                    
                    thk_edit = QLineEdit(plot_info.get('thickness', '1'))
                    thk_edit.textChanged.connect(self.update_plots)
                    self.plot_table.setCellWidget(row, 4, self._create_centered_widget(thk_edit))

                    show_check = QCheckBox()
                    show_check.setChecked(plot_info['show'])
                    show_check.stateChanged.connect(self.update_plots)
                    self.plot_table.setCellWidget(row, 5, self._create_centered_widget(show_check))
                    
                    del_btn = QPushButton("X")
                    del_btn.setStyleSheet("color: red; font-weight: bold;")
                    del_btn.clicked.connect(self.delete_plot_row)
                    self.plot_table.setCellWidget(row, 6, self._create_centered_widget(del_btn))
                
                if self.plot_table.rowCount() > 0:
                    self.plot_table.selectRow(0)
                self.update_plots()

            except FileNotFoundError as e:
                QMessageBox.critical(self, "Error", f"Could not find project path from session file:\n{e}")

    def export_plot(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export Plot", "", "SVG Files (*.svg);;PDF Files (*.pdf)")
        if path:
            self.plot_controller.export_plot(path)