# lmp_visualizer/main_window.py

import sys
import os
import json
from pathlib import Path
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton,
    QComboBox, QFrame, QGridLayout, QLabel, QSizePolicy, QStackedWidget, 
    QFileDialog, QMessageBox, QTableWidgetItem
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor

from ui_components import ChipInputWidget, NoNewLineDelegate, ColorButton
from log_parser import LogParser
from log_plot_panel import LogPlotPanel
from trj_plot_panel import TrjPlotPanel
from settings_manager import SettingsManager

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("LAMMPS Visualizer")
        self.setGeometry(100, 100, 1400, 800)
        self.setStyleSheet("QMainWindow { background-color: #f0f0f0; }")

        self.current_project_path = ""

        # Keyword Storage for Modes
        # 0: Log Plot, 1: Trj Plot
        self.mode_keywords = {
            0: ['.log', '.out'],
            1: ['.lammpstrj', '.dump']
        }
        self.current_mode_index = 0

        # Central Container
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # --- Top Controls Group (Shared) ---
        top_controls_group = QFrame()
        top_controls_group.setFrameShape(QFrame.Shape.StyledPanel)
        top_controls_layout = QGridLayout(top_controls_group)
        top_controls_layout.setContentsMargins(5, 5, 5, 5)
        top_controls_layout.setHorizontalSpacing(10)

        # Row 0: Path and Browse
        path_lbl = QLabel("Path:")
        path_lbl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        top_controls_layout.addWidget(path_lbl, 0, 0)
        
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("Select Project Root Directory...")
        self.path_edit.editingFinished.connect(self.on_path_entered)
        top_controls_layout.addWidget(self.path_edit, 0, 1)
        
        self.browse_btn = QPushButton("Browse...")
        self.browse_btn.setFixedWidth(80)
        self.browse_btn.clicked.connect(self.browse_for_path)
        top_controls_layout.addWidget(self.browse_btn, 0, 2)

        # Row 1: Keywords and Info
        kw_lbl = QLabel("Keywords:")
        kw_lbl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        top_controls_layout.addWidget(kw_lbl, 1, 0)
        
        row1_container = QHBoxLayout()
        row1_container.setContentsMargins(0, 0, 0, 0)
        row1_container.setSpacing(15)

        self.chip_input = ChipInputWidget()
        self.chip_input.chipsChanged.connect(self.on_keywords_changed)
        row1_container.addWidget(self.chip_input, stretch=1)

        info_layout = QHBoxLayout()
        info_layout.setSpacing(15)
        self.studies_label = QLabel("Studies: 0")
        self.systems_label = QLabel("Systems: 0")
        self.units_label = QLabel("Unit: N/A")
        self.timestep_label = QLabel("Timestep: 1.0")
        
        for lbl in [self.studies_label, self.systems_label, self.units_label, self.timestep_label]:
            lbl.setStyleSheet("color: #444;")
            info_layout.addWidget(lbl)
        
        row1_container.addLayout(info_layout)
        top_controls_layout.addLayout(row1_container, 1, 1, 1, 2)

        main_layout.addWidget(top_controls_group)

        # --- Mode Selector (Floating) ---
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Log\nPlot")
        self.mode_combo.addItem("Trj\nPlot")
        self.mode_combo.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.mode_combo.setFixedWidth(75)
        
        # Use Delegate to prevent newlines in the dropdown list
        delegate = NoNewLineDelegate(self.mode_combo)
        self.mode_combo.setItemDelegate(delegate)
        
        self.mode_combo.currentIndexChanged.connect(self.on_mode_changed)

        # --- Stacked Panels ---
        self.stacked_widget = QStackedWidget()
        self.log_plot_panel = LogPlotPanel(self)
        self.trj_plot_panel = TrjPlotPanel(self)
        
        self.stacked_widget.addWidget(self.log_plot_panel)
        self.stacked_widget.addWidget(self.trj_plot_panel)

        main_layout.addWidget(self.stacked_widget, 1)

        # Initialize chips for the default mode (Log Plot)
        self.chip_input.blockSignals(True)
        self.chip_input.set_chips(self.mode_keywords[0])
        self.chip_input.blockSignals(False)
        
        # Attach Mode Combo to the initial panel
        self.log_plot_panel.attach_mode_combo(self.mode_combo)

    def browse_for_path(self):
        self.chip_input.add_chip_from_input()

        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("Select Path Type")
        msg_box.setText("How do you want to select your data?")
        msg_box.addButton(QMessageBox.StandardButton.Cancel)
        file_button = msg_box.addButton("Single File", QMessageBox.ButtonRole.ActionRole)
        dir_button = msg_box.addButton("Project Directory", QMessageBox.ButtonRole.ActionRole)

        file_button.setMinimumWidth(120)
        dir_button.setMinimumWidth(120)
        
        cancel_button = msg_box.button(QMessageBox.StandardButton.Cancel)
        if cancel_button: cancel_button.setVisible(False)

        msg_box.exec()

        clicked_button = msg_box.clickedButton()
        if clicked_button is None or clicked_button == cancel_button:
            return

        path = None
        start_path = self.path_edit.text()
        if not os.path.exists(start_path):
            start_path = str(Path.home())

        if clicked_button == dir_button:
            path = QFileDialog.getExistingDirectory(self, "Select Project Root", directory=start_path)
        elif clicked_button == file_button:
            path, _ = QFileDialog.getOpenFileName(self, "Select Single File", directory=start_path)

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
             self.path_edit.setText(self.current_project_path)
             return
             
        if path.is_dir():
            try:
                 # Attempt to find root if inside a subdirectory
                 root = LogParser.find_project_root(path_str)
                 self.path_edit.setText(str(root))
                 path = root
            except FileNotFoundError:
                 pass
        
        self.current_project_path = self.path_edit.text()
        self.propagate_load(path)

    def on_keywords_changed(self, keywords):
        path_str = self.path_edit.text()
        if path_str:
            # Pass keep_table=True so we don't wipe the user's work when adding a keyword
            self.propagate_load(Path(path_str), force_reload=True, keep_table=True)

    def propagate_load(self, path, force_reload=False, keep_table=False):
        # Send load command to CURRENT panel
        current_panel = self.stacked_widget.currentWidget()
        keywords = self.chip_input.get_chips()
        
        # Check if the panel accepts 'keep_table'
        if hasattr(current_panel, 'load_project'):
            import inspect
            sig = inspect.signature(current_panel.load_project)
            if 'keep_table' in sig.parameters:
                current_panel.load_project(path, keywords, force_reload=force_reload, keep_table=keep_table)
            else:
                current_panel.load_project(path, keywords, force_reload=force_reload)

    def on_mode_changed(self, index):
        # 1. Save keywords of the *previous* mode
        self.mode_keywords[self.current_mode_index] = self.chip_input.get_chips()
        self.current_mode_index = index
        
        # 2. Load keywords for the *new* mode
        self.chip_input.blockSignals(True)
        self.chip_input.set_chips(self.mode_keywords[index])
        self.chip_input.blockSignals(False)

        # 3. Switch Panel
        self.stacked_widget.setCurrentIndex(index)
        new_panel = self.stacked_widget.currentWidget()
        
        # 4. Reparent the Mode Combo
        self.mode_combo.setParent(None) 
        if hasattr(new_panel, 'attach_mode_combo'):
            new_panel.attach_mode_combo(self.mode_combo)

        # 5. Load data if path exists (Propagate load with new keywords)
        if self.current_project_path:
             self.propagate_load(Path(self.current_project_path), force_reload=False, keep_table=True)

    def load_state_on_startup(self):
        """Loads autosave states for both panels if available."""
        # 1. Log Plot Load
        if hasattr(self.log_plot_panel, 'load_state_on_startup'):
            self.log_plot_panel.load_state_on_startup()
            
        # 2. Trj Plot Load
        config_dir = os.path.join(os.path.expanduser('~'), '.LMPvisualizer')
        trj_path = os.path.join(config_dir, 'autosave_trj.json')
        
        if os.path.exists(trj_path):
            data = SettingsManager.load_state(trj_path)
            if data and data.get('type') == 'trj_plot':
                # Restore Project
                path = data.get('project_path', '')
                keywords = data.get('keywords', [])
                if path and os.path.exists(path):
                    # Update global keywords if we are in Trj mode, or store them for later
                    self.mode_keywords[1] = keywords
                    
                    # Trigger load
                    self.trj_plot_panel.load_project(Path(path), keywords, force_reload=True)
                    self.trj_plot_panel.global_label_map = data.get('global_label_map', {})
                    
                    # Restore Table Rows
                    table = self.trj_plot_panel.plot_table
                    table.setRowCount(0)
                    
                    for entry in data.get('rows', []):
                        state = entry.get('state')
                        visuals = entry.get('visuals', {})
                        
                        # Determine heatmap state (handle legacy "Select Heatmap" string if present)
                        h_col = state.get('h_col', "No Heatmap")
                        is_heatmap = (h_col != "No Heatmap" and h_col != "Select Heatmap")

                        # Construct data dictionary for the panel's helper method
                        row_data = {
                            'state': state,
                            'is_heatmap': is_heatmap,
                            'color': visuals.get('color', '#000000'),
                            'heatmap_grad': visuals.get('gradient'),
                            'style': visuals.get('symbol', 'o'),
                            'size': str(visuals.get('size', 5))
                        }
                        
                        row = table.rowCount()
                        table.insertRow(row)
                        
                        # Use the new helper method to populate the row correctly
                        self.trj_plot_panel._populate_row_data(row, row_data)
                    
                    # Select first row if exists to trigger view update
                    if table.rowCount() > 0:
                        table.selectRow(0)

    def closeEvent(self, event):
        """Handle application exit: Save state for both modes."""
        
        # 1. Save Log Plot State
        if hasattr(self.log_plot_panel, 'save_state_for_exit'):
            self.log_plot_panel.save_state_for_exit()
            
        # 2. Save Trj Plot State
        try:
            config_dir = os.path.join(os.path.expanduser('~'), '.LMPvisualizer')
            os.makedirs(config_dir, exist_ok=True)
            path = os.path.join(config_dir, 'autosave_trj.json')

            # Determine path/keywords to save
            # If Trj Panel has data loaded, use that path, otherwise current path
            trj_path_val = self.trj_plot_panel.loaded_path or self.path_edit.text()
            # Use current keywords if mode is Trj, else use stored keywords
            trj_kw = self.chip_input.get_chips() if self.current_mode_index == 1 else self.mode_keywords[1]

            session_data = {
                'type': 'trj_plot',
                'project_path': str(trj_path_val),
                'keywords': trj_kw,
                'rows': [],
                'global_label_map': self.trj_plot_panel.global_label_map
            }
            
            table = self.trj_plot_panel.plot_table
            for row in range(table.rowCount()):
                item = table.item(row, 1)
                if not item: continue
                state = item.data(Qt.ItemDataRole.UserRole)
                
                # Visuals
                color_widget = table.cellWidget(row, 2)
                style_widget = table.cellWidget(row, 3)
                size_widget = table.cellWidget(row, 4)
                
                color_val = "#000000"
                gradient_val = None
                if isinstance(color_widget, ColorButton):
                    color_val = color_widget.color().name()
                elif isinstance(color_widget, QComboBox):
                    gradient_val = color_widget.currentText()
                
                # Handle size widget type safety
                size_val = 1
                if hasattr(size_widget, 'value'):
                    size_val = size_widget.value()
                elif hasattr(size_widget, 'text'):
                    try: size_val = int(size_widget.text())
                    except: pass

                row_data = {
                    'state': state,
                    'visuals': {
                        'color': color_val,
                        'gradient': gradient_val,
                        'symbol': style_widget.currentText() if style_widget else 'o',
                        'size': size_val
                    }
                }
                session_data['rows'].append(row_data)
            
            SettingsManager.save_state(path, session_data)
        except Exception as e:
            print(f"Error autosaving Trj Plot state: {e}")

        super().closeEvent(event)