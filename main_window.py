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

from ui_components import ChipInputWidget, NoNewLineDelegate, ColorButton, NeutralPanel
from log_parser import LogParser
from log_plot_panel import LogPlotPanel
from trj_plot_panel import TrjPlotPanel
from settings_manager import SettingsManager

class MainWindow(QMainWindow):
    # Constants for Mode Management
    MODE_NEUTRAL = -1
    MODE_LOG = 0
    MODE_TRJ = 1
    
    AUTOSAVE_FILES = {
        MODE_LOG: 'autosave_log.json',
        MODE_TRJ: 'autosave_trj.json'
    }

    def __init__(self, startup_mode=None, startup_autoload=True):
        super().__init__()
        self.setWindowTitle("LAMMPS Visualizer")
        self.setGeometry(100, 100, 1400, 800)
        self.setStyleSheet("QMainWindow { background-color: #f0f0f0; }")

        self.current_project_path = ""
        self.autoload_enabled = startup_autoload
        self._skip_next_orchestration = False
        
        # Keyword Storage for Modes
        # 0: Log Plot, 1: Trj Plot
        self.mode_keywords = {
            self.MODE_LOG: ['.log', '.out'],
            self.MODE_TRJ: ['.lammpstrj', '.dump']
        }
        self.current_mode_index = self.MODE_NEUTRAL

        self._init_ui()
        
        # Handle Startup Mode
        if startup_mode == 'log':
            self.switch_to_mode(self.MODE_LOG)
        elif startup_mode == 'trj':
            self.switch_to_mode(self.MODE_TRJ)
        else:
            self.switch_to_neutral()

    def _init_ui(self):
        # Central Container
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # --- Top Controls Group (Shared) ---
        top_controls_group = QFrame()
        top_controls_group.setFrameShape(QFrame.Shape.StyledPanel)
        
        # Save as class attribute for access in switch_to_neutral
        self.top_controls_layout = QGridLayout(top_controls_group)
        self.top_controls_layout.setContentsMargins(5, 5, 5, 5)
        self.top_controls_layout.setHorizontalSpacing(10)

        # Row 0: Path and Browse
        path_lbl = QLabel("Path:")
        path_lbl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self.top_controls_layout.addWidget(path_lbl, 0, 0)
        
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("Select Project Root Directory...")
        self.path_edit.editingFinished.connect(self.on_path_entered)
        self.top_controls_layout.addWidget(self.path_edit, 0, 1)
        
        self.browse_btn = QPushButton("Browse...")
        self.browse_btn.setFixedWidth(80)
        self.browse_btn.clicked.connect(self.browse_for_path)
        self.top_controls_layout.addWidget(self.browse_btn, 0, 2)

        self.refresh_btn = QPushButton("⟳")
        self.refresh_btn.setFixedWidth(30)
        self.refresh_btn.setToolTip("Reload Path")
        self.refresh_btn.clicked.connect(self.on_refresh_clicked)
        self.top_controls_layout.addWidget(self.refresh_btn, 0, 3)

        # Row 1: Keywords and Info
        kw_lbl = QLabel("Keywords:")
        kw_lbl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self.top_controls_layout.addWidget(kw_lbl, 1, 0)
        
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
        self.timestep_label = QLabel("Timestep: N/A")
        
        for lbl in [self.studies_label, self.systems_label, self.units_label, self.timestep_label]:
            lbl.setStyleSheet("color: #444;")
            info_layout.addWidget(lbl)
        
        row1_container.addLayout(info_layout)
        self.top_controls_layout.addLayout(row1_container, 1, 1, 1, 3)

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
        
        # Connect signal but block it initially until fully setup
        self.mode_combo.currentIndexChanged.connect(self.on_mode_combo_changed)

        # --- Stacked Panels ---
        self.stacked_widget = QStackedWidget()
        
        # Index 0: Log Panel
        self.log_plot_panel = LogPlotPanel(self)
        self.stacked_widget.addWidget(self.log_plot_panel)
        
        # Index 1: Trj Panel
        self.trj_plot_panel = TrjPlotPanel(self)
        self.stacked_widget.addWidget(self.trj_plot_panel)
        
        # Index 2: Neutral Panel
        self.neutral_panel = NeutralPanel(self)
        self.neutral_panel.modeSelected.connect(self.on_neutral_mode_selected)
        self.neutral_panel.autoloadToggled.connect(self.on_autoload_toggled)
        self.stacked_widget.addWidget(self.neutral_panel)

        main_layout.addWidget(self.stacked_widget, 1)

    # --- Mode Switching Logic ---

    def switch_to_neutral(self):
        """Switches to the Neutral 'Boot' Mode."""
        self.current_mode_index = self.MODE_NEUTRAL
        
        # Switch View
        self.stacked_widget.setCurrentWidget(self.neutral_panel)
        self.neutral_panel.set_autoload_state(self.autoload_enabled)
        
        # Reset Top Controls
        self.path_edit.clear()
        self.chip_input.blockSignals(True)
        self.chip_input.set_chips([])
        self.chip_input.blockSignals(False)
        self.studies_label.setText("Studies: 0")
        self.systems_label.setText("Systems: 0")
        self.units_label.setText("Unit: N/A")
        self.timestep_label.setText("Timestep: N/A")
        
        # Disable Interface
        self.set_interface_locked(True)
        
        # Reset Dropdown to "Select Mode..." state visually by disabling it
        # We also remove it from any panel layout if attached
        self.mode_combo.setParent(None)
        
        # Re-attach to the Top Controls Layout (now accessed directly)
        # Note: addWidget automatically reparents the widget
        self.top_controls_layout.addWidget(self.mode_combo, 0, 0, 2, 1)
            
        self.mode_combo.setEnabled(False)
        
        # Critical Fix: Block signals to prevent triggering load logic with index -1
        self.mode_combo.blockSignals(True)
        self.mode_combo.setCurrentIndex(-1)
        self.mode_combo.blockSignals(False)

    def switch_to_mode(self, mode_index):
        """Switches to a specific active mode (Log/Trj)."""
        previous_index = self.current_mode_index
        self.current_mode_index = mode_index
        
        # 1. Unlock Interface
        self.set_interface_locked(False)
        
        # 2. Update Dropdown
        self.mode_combo.blockSignals(True)
        self.mode_combo.setEnabled(True)
        self.mode_combo.setCurrentIndex(mode_index)
        self.mode_combo.blockSignals(False)
        
        # 3. Handle Keywords
        self.chip_input.blockSignals(True)
        self.chip_input.set_chips(self.mode_keywords[mode_index])
        self.chip_input.blockSignals(False)

        # 4. Switch Stack
        self.stacked_widget.setCurrentIndex(mode_index)
        new_panel = self.stacked_widget.currentWidget()
        
        # 5. Reparent Mode Combo
        self.mode_combo.setParent(None)
        if hasattr(new_panel, 'attach_mode_combo'):
            new_panel.attach_mode_combo(self.mode_combo)
            
        # 6. Load Session (Orchestration)
        self.orchestrate_load(mode_index, previous_index)

    def set_interface_locked(self, locked: bool):
        """Locks or unlocks the top controls."""
        self.path_edit.setEnabled(not locked)
        self.browse_btn.setEnabled(not locked)
        self.refresh_btn.setEnabled(not locked)
        self.chip_input.setEnabled(not locked)
        self.mode_combo.setEnabled(not locked)

    def on_neutral_mode_selected(self, mode_str):
        if mode_str == 'log':
            self.switch_to_mode(self.MODE_LOG)
        elif mode_str == 'trj':
            self.switch_to_mode(self.MODE_TRJ)

    def on_autoload_toggled(self, enabled):
        self.autoload_enabled = enabled

    def on_mode_combo_changed(self, index):
        """Handles user changing mode via dropdown."""
        # 1. Save Old Mode State
        self.save_session_for_mode(self.current_mode_index)
        
        # 2. Save Keywords for Old Mode
        self.mode_keywords[self.current_mode_index] = self.chip_input.get_chips()
        
        # 3. Switch to New Mode
        self.switch_to_mode(index)

    # --- Session Orchestration ---

    def orchestrate_load(self, new_mode_index, previous_mode_index):
        """Handles loading the session based on autoload settings."""
        # Check if we should skip orchestration (e.g., manual file load in progress)
        if self._skip_next_orchestration:
            self._skip_next_orchestration = False
            return

        mode_name = "Log Plot" if new_mode_index == self.MODE_LOG else "Trajectory Plot"
        
        should_load = False
        if self.autoload_enabled:
            should_load = True
        elif previous_mode_index != self.MODE_NEUTRAL:
            # Runtime switch: Ask user
            reply = QMessageBox.question(
                self, 
                "Load Session?", 
                f"Do you want to load the previous session for {mode_name}?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                should_load = True
        
        if should_load:
            self.load_session_for_mode(new_mode_index)
        else:
            print(f"[System] Switching to {mode_name}. Loading skipped.")

    def save_session_for_mode(self, mode_index):
        """Saves the session state for the given mode index."""
        if mode_index == self.MODE_NEUTRAL:
            return

        panel = None
        if mode_index == self.MODE_LOG:
            panel = self.log_plot_panel
        elif mode_index == self.MODE_TRJ:
            panel = self.trj_plot_panel
            
        filename = self.AUTOSAVE_FILES.get(mode_index)
        
        if panel and filename:
             config_dir = os.path.join(os.path.expanduser('~'), '.LMPvisualizer')
             os.makedirs(config_dir, exist_ok=True)
             path = os.path.join(config_dir, filename)
             
             # Actual save call using the methods added to panels
             if hasattr(panel, 'save_session_to_file'):
                 panel.save_session_to_file(path)

    def load_session_for_mode(self, mode_index):
        """Loads the session state for the given mode index."""
        mode_str = "Log Plot" if mode_index == self.MODE_LOG else "Trajectory Plot"
        filename = self.AUTOSAVE_FILES.get(mode_index)
        if not filename: return

        config_dir = os.path.join(os.path.expanduser('~'), '.LMPvisualizer')
        path = os.path.join(config_dir, filename)
        
        if os.path.exists(path):
            panel = None
            if mode_index == self.MODE_LOG:
                panel = self.log_plot_panel
            elif mode_index == self.MODE_TRJ:
                panel = self.trj_plot_panel
            
            # Actual load call
            if panel and hasattr(panel, 'load_session_from_file'):
                panel.load_session_from_file(path)
        else:
             print(f"[System] No autosave found for {mode_str}.")

    # --- Existing Functionality ---

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
        
        if not path_str:
             # Path cleared: Reset current panel to default
             self.current_project_path = ""
             current_panel = self.stacked_widget.currentWidget()
             if hasattr(current_panel, '_update_ui_state'):
                 current_panel.loaded_path = None
                 current_panel._update_ui_state(project_loaded=False)
             return

        if path_str == self.current_project_path:
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

    def on_refresh_clicked(self):
        path_str = self.path_edit.text()
        if path_str and os.path.exists(path_str):
            self.propagate_load(Path(path_str), force_reload=True)

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

    def closeEvent(self, event):
        """Handle application exit: Save state for current mode."""
        if self.current_mode_index != self.MODE_NEUTRAL:
            self.save_session_for_mode(self.current_mode_index)
        super().closeEvent(event)