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

from lmpvisualizer.shared.ui_components import (ChipInputWidget, PathChipInputWidget, NoNewLineDelegate,
                           ColorButton, NeutralPanel)
from lmpvisualizer.log.log_parser import LogParser
from lmpvisualizer.shared.panel_protocol import call_load_project, has_panel_method
from lmpvisualizer.log.log_plot_panel import LogPlotPanel
from lmpvisualizer.trj.trj_plot_panel import TrjPlotPanel
from lmpvisualizer.dsd.dsd_plot_panel import DSDPlotPanel
from lmpvisualizer.shared.settings_manager import SettingsManager
from lmpvisualizer.shared.logger_setup import get_logger

logger = get_logger(__name__)

class MainWindow(QMainWindow):
    # Constants for Mode Management
    MODE_NEUTRAL = -1
    MODE_LOG = 0
    MODE_TRJ = 1
    MODE_DSD = 2
    
    AUTOSAVE_FILES = {
        MODE_LOG: 'autosave_log.json',
        MODE_TRJ: 'autosave_trj.json',
        MODE_DSD: 'autosave_dsd.json'
    }

    def __init__(self, startup_mode=None, startup_autoload=True):
        super().__init__()
        self.setWindowTitle("LMPvisualizer")
        self.setGeometry(100, 100, 1400, 800)
        self.setStyleSheet("QMainWindow { background-color: #f0f0f0; }")

        self.current_project_path = ""
        self.autoload_enabled = startup_autoload
        self._skip_next_orchestration = False
        # Last-used file dialog folders, per purpose; running session only.
        self._last_dialog_dirs = {}
        
        # Keyword Storage for Modes
        # 0: Log Plot, 1: Trj Plot, 2: DSD Mode
        self.mode_keywords = {
            self.MODE_LOG: ['.lammpslog', '.out'],
            self.MODE_TRJ: ['.lammpstrj', '.dump'],
            self.MODE_DSD: ['.lammpstrj', '.dump']
        }
        self.current_mode_index = self.MODE_NEUTRAL

        self._init_ui()
        
        # Handle Startup Mode
        if startup_mode == 'log':
            self.switch_to_mode(self.MODE_LOG)
        elif startup_mode == 'trj':
            self.switch_to_mode(self.MODE_TRJ)
        elif startup_mode == 'dsd':
            self.switch_to_mode(self.MODE_DSD)
        else:
            self.switch_to_neutral()

    def _session_purpose(self):
        """Dialog memory key for sessions: one per mode."""
        try:
            if self.current_mode_index == self.MODE_LOG:
                return "session:log"
            if self.current_mode_index == self.MODE_TRJ:
                return "session:trj"
            if self.current_mode_index == self.MODE_DSD:
                return "session:dsd"
        except Exception:
            pass
        return "session"

    def get_last_dialog_dir(self, purpose):
        """Folder a file dialog for `purpose` should open in ("" = Qt default)."""
        try:
            folder = self._last_dialog_dirs.get(purpose, "")
            if folder and os.path.isdir(folder):
                return folder
        except Exception:
            pass
        return ""

    def remember_dialog_dir(self, purpose, path):
        """Remember the folder of a dialog-chosen `path` for the running session."""
        # Session folders are remembered per mode - each mode has its own
        # "last path opened" memory.
        if purpose == "session":
            purpose = self._session_purpose()
        try:
            if not path:
                return
            folder = os.path.dirname(os.path.abspath(path))
            if os.path.isdir(folder):
                self._last_dialog_dirs[purpose] = folder
        except Exception:
            pass

    def get_session_dialog_dir(self):
        """Start folder for the session Load/Save dialogs.

        The remembered folder wins; without one, the project path holding
        most table rows does, so the dialog opens where the bulk of the
        loaded data lives.
        """
        folder = self.get_last_dialog_dir(self._session_purpose())
        if folder:
            return folder
        try:
            panel = self.stacked_widget.currentWidget()
            get = getattr(panel, 'dominant_project_path', None)
            path = get() if callable(get) else ""
            if path and os.path.isdir(path):
                return path
        except Exception:
            pass
        return ""

    def set_loaded_session(self, path):
        """Shows the loaded session file name in the window title.

        Autosave files reset to the bare title; anything else appends
        the file stem after a bullet (e.g. "LMPvisualizer • my_run").
        """
        try:
            stem = os.path.splitext(os.path.basename(str(path)))[0] if path else ""
        except Exception:
            stem = ""
        try:
            if not stem or stem == "autosave" or stem.startswith("autosave_"):
                self.setWindowTitle("LMPvisualizer")
            else:
                self.setWindowTitle(f"LMPvisualizer • {stem}")
        except Exception:
            pass

    def clear_session_title(self):
        """Drops the session name, back to the bare application title."""
        try:
            self.setWindowTitle("LMPvisualizer")
        except Exception:
            pass

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
        
        self.path_input = PathChipInputWidget()
        self.path_input.submitted.connect(self.on_path_submitted)
        self.path_input.pathsChanged.connect(self.on_paths_changed)
        # Legacy alias: everything that only ever wanted the typing field still works.
        self.path_edit = self.path_input.input_line
        self.top_controls_layout.addWidget(self.path_input, 0, 1)
        
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
        self.mode_combo.addItem("DSD\nMode")
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
        
        # Index 2: DSD Panel
        self.dsd_plot_panel = DSDPlotPanel(self)
        self.stacked_widget.addWidget(self.dsd_plot_panel)
        
        # Index 3: Neutral Panel
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
        self.path_input.blockSignals(True)
        self.path_input.set_paths([], silent=True)
        self.path_input.clear_input()
        self.path_input.blockSignals(False)
        self.current_project_path = ""
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

        self.clear_session_title()

    def switch_to_mode(self, mode_index):
        """Switches to a specific active mode (Log/Trj)."""
        previous_index = self.current_mode_index
        self.current_mode_index = mode_index

        # A mode switch leaves any named session behind; an explicit load
        # right after (if any) sets the title again.
        self.clear_session_title()
        
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
        attach = getattr(new_panel, 'attach_mode_combo', None)
        if callable(attach):
            attach(self.mode_combo)
            
        # 6. Load Session (Orchestration)
        self.orchestrate_load(mode_index, previous_index)

    def set_interface_locked(self, locked: bool):
        """Locks or unlocks the top controls."""
        self.path_input.setEnabled(not locked)
        self.browse_btn.setEnabled(not locked)
        self.refresh_btn.setEnabled(not locked)
        self.chip_input.setEnabled(not locked)
        self.mode_combo.setEnabled(not locked)

    def on_neutral_mode_selected(self, mode_str):
        if mode_str == 'log':
            self.switch_to_mode(self.MODE_LOG)
        elif mode_str == 'trj':
            self.switch_to_mode(self.MODE_TRJ)
        elif mode_str == 'dsd':
            self.switch_to_mode(self.MODE_DSD)

    def on_autoload_toggled(self, enabled):
        self.autoload_enabled = enabled

    def on_mode_combo_changed(self, index):
        """Handles user changing mode via dropdown."""
        # 1. Save Old Mode State
        self.save_session_for_mode(self.current_mode_index, trigger="mode switch")
        
        # 2. Save Keywords for Old Mode
        self.mode_keywords[self.current_mode_index] = self.chip_input.get_chips()
        
        # 3. Switch to New Mode
        self.switch_to_mode(index)

    # --- Session Orchestration ---

    def orchestrate_load(self, new_mode_index, previous_mode_index):
        """Handles loading the session based on autoload settings."""
        if self._skip_next_orchestration:
            self._skip_next_orchestration = False
            return

        mode_names = {
            self.MODE_LOG: "Log Plot",
            self.MODE_TRJ: "Trajectory Plot",
            self.MODE_DSD: "DSD Mode"
        }
        mode_name = mode_names.get(new_mode_index, "Unknown")
        
        should_load = False
        if self.autoload_enabled:
            should_load = True
        elif previous_mode_index != self.MODE_NEUTRAL:
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
            logger.info("[System] Switching to %s. Loading skipped.", mode_name)

    def save_session_for_mode(self, mode_index, trigger=None):
        """Saves the session state for the given mode index."""
        if mode_index == self.MODE_NEUTRAL:
            return

        panel = None
        if mode_index == self.MODE_LOG:
            panel = self.log_plot_panel
        elif mode_index == self.MODE_TRJ:
            panel = self.trj_plot_panel
        elif mode_index == self.MODE_DSD:
            panel = self.dsd_plot_panel
            
        filename = self.AUTOSAVE_FILES.get(mode_index)
        
        if panel and filename:
            config_dir = os.path.join(os.path.expanduser('~'), '.LMPvisualizer')
            os.makedirs(config_dir, exist_ok=True)
            path = os.path.join(config_dir, filename)

            if has_panel_method(panel, 'save_session_to_file'):
                panel.save_session_to_file(path, trigger=trigger)

    def load_session_for_mode(self, mode_index):
        """Loads the session state for the given mode index."""
        mode_names = {
            self.MODE_LOG: "Log Plot",
            self.MODE_TRJ: "Trajectory Plot",
            self.MODE_DSD: "DSD Mode"
        }
        mode_str = mode_names.get(mode_index, "Unknown")
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
            elif mode_index == self.MODE_DSD:
                panel = self.dsd_plot_panel
            
            if panel and has_panel_method(panel, 'load_session_from_file'):
                try:
                    panel.load_session_from_file(path)
                except Exception as e:
                    # A broken session must never kill the mode switch.
                    logger.warning("[System] Session load failed for %s: %s", mode_str, e)
        else:
             logger.info("[System] No autosave found for %s.", mode_str)

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
        existing = self.get_project_paths()
        start_path = existing[-1] if existing else self.path_edit.text()
        if not os.path.exists(start_path):
            start_path = str(Path.home())

        if clicked_button == dir_button:
            path = QFileDialog.getExistingDirectory(self, "Select Project Root", directory=start_path)
        elif clicked_button == file_button:
            path, _ = QFileDialog.getOpenFileName(self, "Select Single File", directory=start_path)

        if path:
            # Browse ADDS a path; it no longer replaces what is already loaded.
            self.on_path_submitted(path)

    # --- Project paths (chips) ---

    def get_project_paths(self) -> list:
        return self.path_input.get_paths()

    def set_project_paths(self, paths, silent=True):
        """Replaces the chip set, by default without triggering a reload.

        Session loaders use silent=True because they run their own load right after.
        """
        self.path_input.blockSignals(silent)
        try:
            self.path_input.set_paths([p for p in (paths or []) if p], silent=silent)
        finally:
            self.path_input.blockSignals(False)
        self.current_project_path = self.get_project_paths()[0] if self.get_project_paths() else ""

    @staticmethod
    def session_paths(data: dict) -> list:
        """Reads the project paths out of a session dict.

        'project_paths' is the current field; sessions written before multi-path
        support only carry the single 'project_path'.
        """
        paths = data.get('project_paths')
        if paths is None:
            single = data.get('project_path') or data.get('path') or ''
            paths = [single] if single else []
        return [p for p in paths if p]

    def resolve_session_paths(self, panel, paths):
        """Runs MissingPathResolver over every path a session refers to.

        Returns (paths, relocated, action). An action of 'cancel', 'reset' or 'change'
        means the caller must abort - the resolver has already taken over.
        """
        from lmpvisualizer.shared.ui_components import MissingPathResolver

        resolved, relocated = [], False
        for project_path in paths:
            if os.path.exists(project_path):
                resolved.append(project_path)
                continue

            action, new_path = MissingPathResolver.resolve(panel, project_path)
            if action == 'cancel':
                return paths, False, 'cancel'
            if action == 'reset':
                # Drop every path and reset the panel.
                self.set_project_paths([], silent=False)
                return [], False, 'reset'
            if action == 'change':
                # Start over from the new path, discarding the session's rows.
                self.set_project_paths([new_path], silent=False)
                return [new_path], False, 'change'
            if action == 'relocate':
                resolved.append(new_path)
                relocated = True

        return resolved, relocated, 'ok'

    def on_path_submitted(self, text: str):
        """A path was typed, pasted or picked through Browse. It becomes a new chip.

        A session .json is not a project path - it still switches mode and loads,
        exactly as before.
        """
        # Committing a path also commits a half-typed keyword, as it always has.
        self.chip_input.add_chip_from_input()

        text = (text or "").strip().strip('"')
        if not text:
            return

        path = Path(text)
        if not path.exists():
            # Clear first: editingFinished fires again on focus-out and would otherwise
            # re-raise the same dialog for the same bad text.
            self.path_input.clear_input()
            logger.warning("The specified path does not exist: %s", text)
            QMessageBox.critical(self, "Error", "The specified path does not exist.")
            return

        if path.is_file() and path.suffix.lower() == '.json':
            self.path_input.clear_input()
            self._load_session_file(path)
            return

        if path.is_dir():
            try:
                # Attempt to find root if inside a subdirectory
                path = LogParser.find_project_root(str(path))
            except FileNotFoundError:
                pass

        self.path_input.clear_input()
        if not self.path_input.add_path(str(path)):
            logger.info("[System] Path already loaded: %s", path)

    def _load_session_file(self, path: Path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            logger.warning("Failed to parse JSON file %s: %s", path, e)
            QMessageBox.warning(self, "Error", f"Failed to parse JSON file:\n{e}")
            return

        file_type = data.get('type')

        # Map type string to Mode Constant (Support legacy *_plot suffix)
        type_map = {
            'dsd': self.MODE_DSD, 'dsd_plot': self.MODE_DSD,
            'trj': self.MODE_TRJ, 'trj_plot': self.MODE_TRJ,
            'log': self.MODE_LOG, 'log_plot': self.MODE_LOG
        }

        if file_type not in type_map:
            logger.warning("Invalid session file '%s' with unknown type: '%s'", path.name, file_type)
            QMessageBox.warning(self, "Invalid Session File",
                f"The file '{path.name}' is not a valid session file OR has an unknown type.\n\n"
                f"Found type: '{file_type}'\n\n"
                "Valid types:\n"
                "- 'dsd' (DSD Mode)\n"
                "- 'trj' (Trajectory Plot)\n"
                "- 'log' (Log Plot)")
            return

        self.switch_to_mode(type_map[file_type])
        current_panel = self.stacked_widget.currentWidget()
        if has_panel_method(current_panel, 'load_session_from_file'):
            current_panel.load_session_from_file(str(path))

    def on_paths_changed(self, paths):
        """A chip was added or removed - reload the current panel against the new set."""
        self.current_project_path = paths[0] if paths else ""

        if not paths:
            current_panel = self.stacked_widget.currentWidget()
            # Panels that want a say in what survives a cleared path provide
            # on_path_cleared(); the rest fall back to the plain reset.
            on_cleared = getattr(current_panel, 'on_path_cleared', None)
            if callable(on_cleared):
                on_cleared()
            elif has_panel_method(current_panel, '_update_ui_state'):
                current_panel.loaded_path = None
                current_panel._update_ui_state(project_loaded=False)
            return

        # keep_table=True so adding or removing a path does not destroy the user's rows;
        # rows belonging to a removed path are pruned by the panel itself.
        self.propagate_load(paths, force_reload=True, keep_table=True)

    def on_refresh_clicked(self):
        paths = [p for p in self.get_project_paths() if os.path.exists(p)]
        if paths:
            # keep_table=True: a refresh re-reads the same project, it must not wipe the table.
            self.propagate_load(paths, force_reload=True, keep_table=True)

    def on_keywords_changed(self, keywords):
        paths = self.get_project_paths()
        if paths:
            # Pass keep_table=True so we don't wipe the user's work when adding a keyword
            self.propagate_load(paths, force_reload=True, keep_table=True)

    def propagate_load(self, path, force_reload=False, keep_table=False, target_system=None):
        # Send load command to CURRENT panel.
        # Duck-typed contract, see panel_protocol.PanelProtocol: every mode panel
        # exposes load_project with the same routing kwargs, so plain
        # hasattr/getattr dispatch replaces the former inspect.signature branching.
        current_panel = self.stacked_widget.currentWidget()
        keywords = self.chip_input.get_chips()

        call_load_project(current_panel, path, keywords,
                          force_reload=force_reload,
                          keep_table=keep_table,
                          target_system=target_system)

    def closeEvent(self, event):
        """Handle application exit: Save state for current mode."""
        if self.current_mode_index != self.MODE_NEUTRAL:
            self.save_session_for_mode(self.current_mode_index, trigger="app close")
        super().closeEvent(event)