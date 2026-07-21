# lmp_visualizer/main_window.py

import sys
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton,
    QComboBox, QFrame, QTableWidget, QHeaderView, QTableWidgetItem,
    QFileDialog, QMessageBox, QCheckBox, QLabel, QSplitter, QGridLayout
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
import pyqtgraph as pg
import random

from lammps_parser import LammpsParser
from data_manager import DataManager
from plotting_controller import PlottingController
from settings_manager import SettingsManager
from ui_components import ColorButton, InconsistentDataDialog

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("LAMMPS Log Visualizer")
        self.setGeometry(100, 100, 1400, 800)
        self.setStyleSheet("QWidget { background-color: #f0f0f0; }") # Light gray background

        # Backend components
        self.data_manager = DataManager()
        self.plot_controller = None # Will be initialized after layout

        # Central widget and layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # --- Top Controls (Path and Info) ---
        top_controls_layout = QHBoxLayout()

        # Path selection
        path_layout = QHBoxLayout()
        path_layout.addWidget(QLabel("Path:"))
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("Select Project Root Directory...")
        self.browse_btn = QPushButton("Browse...")
        path_layout.addWidget(self.path_edit)
        path_layout.addWidget(self.browse_btn)
        top_controls_layout.addLayout(path_layout)
        
        # Info panel (compact)
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
        info_widget.setFixedWidth(200) # Keep this panel compact
        top_controls_layout.addWidget(info_widget)
        
        main_layout.addLayout(top_controls_layout)

        # --- Selection Dropdowns (reorganized) ---
        selection_layout = QHBoxLayout()
        
        # Left side dropdowns
        left_selection_layout = QGridLayout()
        left_selection_layout.addWidget(QLabel("Study"), 0, 0)
        self.study_combo = self._create_combo("Select Study")
        left_selection_layout.addWidget(self.study_combo, 0, 1)
        
        left_selection_layout.addWidget(QLabel("System"), 1, 0)
        self.system_combo = self._create_combo("Select System")
        left_selection_layout.addWidget(self.system_combo, 1, 1)
        
        # Right side dropdowns
        right_selection_layout = QGridLayout()
        right_selection_layout.addWidget(QLabel("X-Axis"), 0, 0)
        self.xaxis_combo = self._create_combo("Select X-Axis")
        right_selection_layout.addWidget(self.xaxis_combo, 0, 1)

        right_selection_layout.addWidget(QLabel("Y-Axis"), 1, 0)
        self.yaxis_combo = self._create_combo("Select Y-Axis")
        right_selection_layout.addWidget(self.yaxis_combo, 1, 1)
        
        selection_layout.addLayout(left_selection_layout)
        selection_layout.addLayout(right_selection_layout)
        
        self.std_checkbox = QCheckBox("Compute std")
        selection_layout.addWidget(self.std_checkbox, alignment=Qt.AlignmentFlag.AlignVCenter)
        selection_layout.addStretch() # Push everything to the left
        
        main_layout.addLayout(selection_layout)


        # --- Main content area (Plot + Controls/Table) ---
        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        
        # Left side: Plot + Action buttons
        left_panel = QWidget()
        left_layout = QHBoxLayout(left_panel)
        
        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setBackground('w') # White background for the plot
        self.plot_controller = PlottingController(self.plot_widget)

        action_buttons_layout = QVBoxLayout()
        self.add_btn = QPushButton("Add")
        self.load_btn = QPushButton("Load")
        self.popout_btn = QPushButton("Pop Out")
        self.save_btn = QPushButton("Save")
        self.export_btn = QPushButton("Export")
        action_buttons_layout.addWidget(self.add_btn)
        action_buttons_layout.addWidget(self.load_btn)
        action_buttons_layout.addWidget(self.popout_btn)
        action_buttons_layout.addWidget(self.save_btn)
        action_buttons_layout.addWidget(self.export_btn)
        action_buttons_layout.addStretch()
        
        left_layout.addWidget(self.plot_widget)
        left_layout.addLayout(action_buttons_layout)
        
        # Right side: Plot Table (with adjusted columns)
        self.plot_table = QTableWidget()
        self.plot_table.setColumnCount(5)
        self.plot_table.setHorizontalHeaderLabels(["Plot", "Color", "Style", "Show", "Del"])
        header = self.plot_table.horizontalHeader()
        # Set column resize modes
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch) # 'Plot' column
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents) # 'Color'
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents) # 'Style'
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents) # 'Show'
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents) # 'Del'
        self.plot_table.verticalHeader().hide()

        main_splitter.addWidget(left_panel)
        main_splitter.addWidget(self.plot_table)
        main_splitter.setSizes([900, 500]) # Adjust initial splitter sizes
        main_layout.addWidget(main_splitter)

        self._connect_signals()
        self._update_ui_state()

    def _create_combo(self, placeholder: str) -> QComboBox:
        combo = QComboBox()
        # The placeholder is now set via addItem and setting the first index
        combo.addItem(placeholder)
        combo.setCurrentIndex(0)
        return combo

    def _connect_signals(self):
        self.browse_btn.clicked.connect(self.browse_for_directory)
        
        self.study_combo.currentTextChanged.connect(self.on_study_selected)
        self.system_combo.currentTextChanged.connect(self.on_selection_changed)
        self.xaxis_combo.currentTextChanged.connect(self.on_selection_changed)
        self.yaxis_combo.currentTextChanged.connect(self.on_selection_changed)
        self.std_checkbox.stateChanged.connect(self.on_selection_changed)

        self.add_btn.clicked.connect(self.add_plot_from_selection)
        self.save_btn.clicked.connect(self.save_session)
        self.load_btn.clicked.connect(self.load_session)
        self.export_btn.clicked.connect(self.export_plot)
    
    def browse_for_directory(self):
        directory = QFileDialog.getExistingDirectory(self, "Select Project Root")
        if directory:
            self.path_edit.setText(directory)
            try:
                root_path = LammpsParser.find_project_root(directory)
                self.path_edit.setText(str(root_path))
                self.load_project(root_path)
            except FileNotFoundError as e:
                QMessageBox.critical(self, "Error", str(e))

    def load_project(self, root_path):
        studies, warnings = LammpsParser.discover_studies_systems(root_path)
        if warnings:
            QMessageBox.warning(self, "Project Discovery Warning", "\n".join(warnings))
        
        self.data_manager.load_project_data(studies, root_path)
        if self.data_manager.warnings:
            QMessageBox.warning(self, "Data Loading Warning", "\n".join(self.data_manager.warnings))
            
        self.studies_label.setText(f"Studies: {len(self.data_manager.get_study_names())}")
        
        # --- FIX for System Count and Timestep Unit ---
        # Get system count from the first available study after loading
        studies_dict = self.data_manager.data
        if studies_dict:
            first_study_name = next(iter(studies_dict))
            num_systems = len(studies_dict[first_study_name])
            self.systems_label.setText(f"Systems: {num_systems}")
        else:
            self.systems_label.setText("Systems: 0")

        # Update metadata including units for timestep
        units = LammpsParser.get_units(root_path)
        self.units_label.setText(f"Unit: {units or 'N/A'}")
        timestep = LammpsParser.get_timestep(root_path)
        if timestep and units:
            self.timestep_label.setText(f"Timestep: {timestep} {units}")
        else:
            self.timestep_label.setText(f"Timestep: {timestep or 'N/A'}")
        # --- End of Fix ---

        self._update_ui_state()

    def _update_ui_state(self, clear_plots=True):
        if clear_plots:
            self.plot_table.setRowCount(0)
            for name in list(self.plot_controller.plots.keys()):
                self.plot_controller.remove_plot(name)

        # Helper to reset a combo box with a placeholder
        def reset_combo(combo, placeholder, items):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(placeholder)
            combo.addItems(items)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)

        reset_combo(self.study_combo, "Select Study", self.data_manager.get_study_names())
        
        self.on_study_selected() # Trigger update for other combos
        self.on_selection_changed() # Update temp plot

    def on_study_selected(self):
        study = self.study_combo.currentText()
        
        def reset_combo_with_systems(combo, placeholder, items):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(placeholder)
            if len(items) > 1:
                combo.addItem("average")
            combo.addItems(items)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)

        systems = self.data_manager.get_system_names(study)
        reset_combo_with_systems(self.system_combo, "Select System", systems)
        # Note: The main systems label is now set once in load_project

        def reset_combo(combo, placeholder, items):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(placeholder)
            combo.addItems(items)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)

        reset_combo(self.xaxis_combo, "Select X-Axis", self.data_manager.available_columns)
        reset_combo(self.yaxis_combo, "Select Y-Axis", self.data_manager.available_columns)
        
        self.on_selection_changed()

    def is_selection_valid(self):
        """Check if dropdowns have a valid selection (not placeholder)."""
        return all(combo.currentIndex() > 0 for combo in [self.study_combo, self.system_combo, self.xaxis_combo, self.yaxis_combo])

    def on_selection_changed(self):
        self._update_temp_plot_entry()
        is_average = self.system_combo.currentText() == "average"
        self.std_checkbox.setVisible(is_average)
        
    def _update_temp_plot_entry(self):
        # Check if a temporary row exists, if not, create one
        has_temp = False
        if self.plot_table.rowCount() > 0:
            item = self.plot_table.item(self.plot_table.rowCount() - 1, 0)
            if item and item.data(Qt.ItemDataRole.UserRole) == 'temp':
                has_temp = True
        
        if not self.is_selection_valid():
             if has_temp: # remove if selection incomplete
                 self.plot_table.removeRow(self.plot_table.rowCount() - 1)
             self.add_btn.setEnabled(False)
             return

        self.add_btn.setEnabled(True)
        study = self.study_combo.currentText()
        system = self.system_combo.currentText()
        x_ax = self.xaxis_combo.currentText()
        y_ax = self.yaxis_combo.currentText()
        plot_name = f"{study}_{system}_{x_ax}_{y_ax}"

        if not has_temp:
            self.plot_table.insertRow(self.plot_table.rowCount())
        
        row = self.plot_table.rowCount() - 1
        # Item for name
        name_item = QTableWidgetItem(plot_name)
        name_item.setData(Qt.ItemDataRole.UserRole, 'temp')
        name_item.setForeground(Qt.GlobalColor.gray)
        self.plot_table.setItem(row, 0, name_item)

        # Other widgets for the temp row
        if self.plot_table.cellWidget(row, 1) is None:
            self.plot_table.setCellWidget(row, 1, ColorButton(self._get_random_color()))
            self.plot_table.setCellWidget(row, 2, self._create_style_combo())
            self.plot_table.setCellWidget(row, 3, QCheckBox())
            self.plot_table.setCellWidget(row, 4, QLabel(" ")) # Placeholder

    def add_plot_from_selection(self):
        row = self.plot_table.rowCount() - 1
        if row < 0: return
        
        item = self.plot_table.item(row, 0)
        if not item or item.data(Qt.ItemDataRole.UserRole) != 'temp':
            return
            
        plot_name = item.text()
        
        # Solidify the row
        item.setData(Qt.ItemDataRole.UserRole, 'permanent')
        item.setForeground(Qt.GlobalColor.black)
        
        # Make connections for the permanent row
        color_btn = self.plot_table.cellWidget(row, 1)
        style_combo = self.plot_table.cellWidget(row, 2)
        show_check = self.plot_table.cellWidget(row, 3)
        
        color_btn.colorChanged.connect(self.update_all_plots)
        style_combo.currentTextChanged.connect(self.update_all_plots)
        show_check.stateChanged.connect(self.update_all_plots)
        show_check.setChecked(True) # Show by default

        del_btn = QPushButton("X")
        del_btn.clicked.connect(lambda checked, name=plot_name: self.delete_plot_row(name))
        self.plot_table.setCellWidget(row, 4, del_btn)

        self.on_selection_changed() # Create a new temp row
        self.update_all_plots()

    def update_all_plots(self):
        for name in list(self.plot_controller.plots.keys()):
            self.plot_controller.remove_plot(name)
            
        for row in range(self.plot_table.rowCount()):
            item = self.plot_table.item(row, 0)
            if item and item.data(Qt.ItemDataRole.UserRole) == 'permanent' and self.plot_table.cellWidget(row, 3).isChecked():
                plot_name = item.text()
                study, system, x_ax, y_ax = plot_name.split('_')
                
                user_choices = None
                if system == 'average':
                    consistency = self.data_manager.check_data_consistency(study)
                    if consistency:
                        dialog = InconsistentDataDialog(consistency, self)
                        if dialog.exec():
                            user_choices = dialog.get_choices()
                        else:
                            continue # User cancelled
                
                data = self.data_manager.get_plot_data(study, system, x_ax, y_ax, self.std_checkbox.isChecked(), user_choices)
                
                if data:
                    color = self.plot_table.cellWidget(row, 1).color()
                    style_text = self.plot_table.cellWidget(row, 2).currentText()
                    style = {'Solid': Qt.PenStyle.SolidLine, 'Dash': Qt.PenStyle.DashLine, 'Dot': Qt.PenStyle.DotLine}.get(style_text)
                    self.plot_controller.add_or_update_plot(plot_name, data, color, style)
                    
        self._update_axis_labels()

    def _update_axis_labels(self):
        # For simplicity, use the x-axis from the first shown plot
        # and create labels for each unique y-axis
        x_label = ""
        y_labels = {}
        for row in range(self.plot_table.rowCount()):
             item = self.plot_table.item(row, 0)
             if item and item.data(Qt.ItemDataRole.UserRole) == 'permanent' and self.plot_table.cellWidget(row, 3).isChecked():
                plot_name = item.text()
                _, _, x_ax, y_ax = plot_name.split('_')
                if not x_label:
                    x_label = x_ax
                if y_ax not in y_labels:
                    y_labels[y_ax] = y_ax
        
        self.plot_controller.set_axis_labels(x_label, y_labels)

    def delete_plot_row(self, plot_name_to_delete: str):
        for row in range(self.plot_table.rowCount()):
            item = self.plot_table.item(row, 0)
            if item and item.text() == plot_name_to_delete:
                self.plot_table.removeRow(row)
                self.plot_controller.remove_plot(plot_name_to_delete)
                self.update_all_plots() # Refresh plot view
                break

    def _get_random_color(self) -> QColor:
        return QColor(random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))

    def _create_style_combo(self) -> QComboBox:
        combo = QComboBox()
        combo.addItems(["Solid", "Dash", "Dot"])
        return combo
        
    def save_session(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save Session", "", "JSON Files (*.json)")
        if not path:
            return
            
        config = {'path': self.path_edit.text(), 'plots': []}
        for row in range(self.plot_table.rowCount()):
            item = self.plot_table.item(row, 0)
            if item and item.data(Qt.ItemDataRole.UserRole) == 'permanent':
                plot_info = {
                    'name': item.text(),
                    'color': self.plot_table.cellWidget(row, 1).color().name(),
                    'style': self.plot_table.cellWidget(row, 2).currentText(),
                    'show': self.plot_table.cellWidget(row, 3).isChecked()
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

                # Now load plots
                self.plot_table.setRowCount(0)
                for plot_info in config.get('plots', []):
                    row = self.plot_table.rowCount()
                    self.plot_table.insertRow(row)
                    
                    item = QTableWidgetItem(plot_info['name'])
                    item.setData(Qt.ItemDataRole.UserRole, 'permanent')
                    self.plot_table.setItem(row, 0, item)
                    
                    self.plot_table.setCellWidget(row, 1, ColorButton(QColor(plot_info['color'])))
                    style_combo = self._create_style_combo()
                    style_combo.setCurrentText(plot_info['style'])
                    self.plot_table.setCellWidget(row, 2, style_combo)
                    
                    show_check = QCheckBox()
                    show_check.setChecked(plot_info['show'])
                    self.plot_table.setCellWidget(row, 3, show_check)
                    
                    # Wire up signals
                    color_btn = self.plot_table.cellWidget(row, 1)
                    del_btn = QPushButton("X")
                    del_btn.clicked.connect(lambda checked, name=plot_info['name']: self.delete_plot_row(name))
                    self.plot_table.setCellWidget(row, 4, del_btn)

                    color_btn.colorChanged.connect(self.update_all_plots)
                    style_combo.currentTextChanged.connect(self.update_all_plots)
                    show_check.stateChanged.connect(self.update_all_plots)
                
                self.update_all_plots()
                self.on_selection_changed() # Add temp row

            except FileNotFoundError as e:
                QMessageBox.critical(self, "Error", f"Could not find project path from session file:\n{e}")

    def export_plot(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export Plot", "", "SVG Files (*.svg);;PDF Files (*.pdf)")
        if path:
            self.plot_controller.export_plot(path)