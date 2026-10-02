# auto_index_dialog.py

import os
import json
import multiprocessing
import numpy as np
from pathlib import Path
from typing import List, Dict, Tuple, Any, Optional
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QListWidget, 
                             QListWidgetItem, QPushButton, QProgressBar, QLabel, 
                             QMessageBox)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QKeyEvent
from lmpvisualizer.shared.trajectory_parser import TrajectoryParser
from lmpvisualizer.shared.logger_setup import get_logger

logger = get_logger(__name__)

class BulkToggleListWidget(QListWidget):
    """Specialized list that forces Space/Enter to toggle ALL selected items."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.locked = False

    def keyPressEvent(self, event: QKeyEvent):
        if self.locked:
            super().keyPressEvent(event)
            return
        if event.key() in [Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter]:
            items = self.selectedItems()
            if items:
                new_state = Qt.CheckState.Unchecked if items[0].checkState() == Qt.CheckState.Checked else Qt.CheckState.Checked
                self.blockSignals(True)
                for item in items:
                    item.setCheckState(new_state)
                self.blockSignals(False)
                event.accept()
                return
        super().keyPressEvent(event)

def index_file_worker(task_data: dict):
    """Worker for Structural and DSD Preloading."""
    try:
        fpath = Path(task_data['filepath']).resolve()
        mode = task_data['mode']
        
        parser = TrajectoryParser(fpath)
        parser._build_full_index()
        
        if mode == 'dsd' and task_data.get('dsd_config'):
            from lmpvisualizer.dsd.dsd_data_manager import DSDDataManager
            dm = DSDDataManager()
            dm.parsers[""] = {"": parser}
            conf = task_data['dsd_config']
            steps = parser.get_timesteps()
            
            # RESPECT UI INITIAL STEP:
            # If the UI is set to Step 1000, calculate hashes relative to Step 1000.
            ui_steps = conf.get('timesteps', [])
            if ui_steps:
                ui_start = ui_steps[0]
                full_arr = np.array(steps)
                start_idx = (np.abs(full_arr - ui_start)).argmin()
                # Pass the subset starting at the matched initial step
                steps = steps[start_idx:]

            dm.calculate_strain_evolution_cached(
                conf['domains'], steps, "", "", 
                conf['slice_axis'], conf['observe_axis'], conf['options']
            )
        return True, str(fpath), ""
    except Exception as e:
        return False, str(Path(task_data.get('filepath', 'unknown')).resolve()), str(e)

class AutoIndexWorker(QThread):
    progress = pyqtSignal(int, int)
    file_finished = pyqtSignal(str, bool)
    finished = pyqtSignal(int, int)
    log = pyqtSignal(str)

    def __init__(self, tasks: List[dict]):
        super().__init__()
        self.tasks = tasks
        self._is_running = True

    def stop(self):
        self._is_running = False

    def run(self):
        total = len(self.tasks)
        if total == 0:
            self.finished.emit(0, 0)
            return

        success_count, failed_count = 0, 0
        num_cores = min(4, max(1, multiprocessing.cpu_count() - 1))
        
        with multiprocessing.Pool(processes=num_cores) as pool:
            results = pool.imap_unordered(index_file_worker, self.tasks, chunksize=1)
            for i, (ok, path, err) in enumerate(results):
                if not self._is_running:
                    pool.terminate()
                    break
                self.file_finished.emit(path, ok)
                if ok:
                    success_count += 1
                    self.log.emit(f"Done: {Path(path).name}")
                else:
                    failed_count += 1
                    self.log.emit(f"Error: {Path(path).name}")
                self.progress.emit(i + 1, total)

        self.finished.emit(success_count, failed_count)

class AutoIndexDialog(QDialog):
    def __init__(self, parent, study_system_map: Dict[str, Dict[str, Any]], dsd_config: Optional[dict] = None):
        super().__init__(parent)
        self.setWindowTitle("Auto Preload Tool")
        self.setMinimumSize(600, 600)
        self.study_system_map = study_system_map
        self.dsd_config = dsd_config
        self.worker = None
        self._init_ui()
        self._check_all_statuses()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        
        legend_layout = QHBoxLayout()
        legend_layout.addWidget(QLabel("<b>Status:</b>"))
        status_types = [
            ("#ef9a9a", "Unloaded"), 
            ("#fff59d", "Trj Indexed"), 
            ("#a5d6a7", "Full DSD Ready")
        ]
        for col, txt in status_types:
            lbl = QLabel(txt)
            lbl.setStyleSheet(f"background-color: {col}; border: 1px solid #666; padding: 2px 6px; border-radius: 3px; font-size: 10px;")
            legend_layout.addWidget(lbl)
        legend_layout.addStretch()
        layout.addLayout(legend_layout)

        layout.addWidget(QLabel("Select systems (Space/Enter toggles all highlighted rows):"))
        
        self.list_widget = BulkToggleListWidget()
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.list_widget.setStyleSheet("QListWidget::item:selected { background-color: #3498db; color: white; }")
        
        for study, systems in sorted(self.study_system_map.items()):
            for system, p_or_p in sorted(systems.items()):
                item = QListWidgetItem(f"{study}  >  {system}")
                fpath = p_or_p if isinstance(p_or_p, Path) else getattr(p_or_p, 'filepath', None)
                if fpath:
                    item.setData(Qt.ItemDataRole.UserRole, str(Path(fpath).resolve()))
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(Qt.CheckState.Checked)
                    self.list_widget.addItem(item)
        
        layout.addWidget(self.list_widget)
        
        sel_layout = QHBoxLayout()
        self.btn_all = QPushButton("Select All")
        self.btn_none = QPushButton("Clear All")
        self.btn_all.clicked.connect(lambda: self._set_all_checks(Qt.CheckState.Checked))
        self.btn_none.clicked.connect(lambda: self._set_all_checks(Qt.CheckState.Unchecked))
        sel_layout.addWidget(self.btn_all)
        sel_layout.addWidget(self.btn_none)
        sel_layout.addStretch()
        layout.addLayout(sel_layout)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)
        
        self.status_label = QLabel("Ready")
        self.status_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(self.status_label)
        
        bottom_layout = QHBoxLayout()
        self.btn_cancel = QPushButton("Close")
        self.btn_basic = QPushButton("Trj Index Preload")
        self.btn_dsd = QPushButton("Full DSD Preload")
        
        for b in [self.btn_cancel, self.btn_basic, self.btn_dsd]: b.setMinimumHeight(35)
        self.btn_basic.setMinimumWidth(200)
        self.btn_dsd.setMinimumWidth(200)
        
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_basic.clicked.connect(lambda: self._start_work('basic'))
        self.btn_dsd.setStyleSheet("background-color: #e8f5e9; font-weight: bold; border: 1px solid #4caf50;")
        self.btn_dsd.clicked.connect(lambda: self._start_work('dsd'))
        
        bottom_layout.addWidget(self.btn_cancel)
        bottom_layout.addStretch()
        bottom_layout.addWidget(self.btn_basic)
        bottom_layout.addWidget(self.btn_dsd)
        layout.addLayout(bottom_layout)

    def _check_all_statuses(self):
        """Logic to accurately find pre-calculated results in the .idx files."""
        from lmpvisualizer.dsd.dsd_data_manager import DSDDataManager
        dm = DSDDataManager()
        
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            self._update_single_item_status(item, dm)

    def _update_single_item_status(self, item, dm=None):
        from lmpvisualizer.dsd.dsd_data_manager import DSDDataManager
        if dm is None: dm = DSDDataManager()

        path_str = item.data(Qt.ItemDataRole.UserRole)
        if not path_str: return
        
        fpath = Path(path_str)
        idx_path = fpath.with_suffix(fpath.suffix + ".idx")
        bg_color = "#ef9a9a" # Unloaded
        
        if idx_path.exists():
            bg_color = "#fff59d" # Trj Indexed
            try:
                if os.path.getmtime(fpath) <= os.path.getmtime(idx_path):
                    with open(idx_path, 'r', encoding='utf-8') as f:
                        idx_data = json.load(f)
                    
                    lib = idx_data.get('results_library', {})
                    
                    if self.dsd_config and self.dsd_config.get('domains'):
                        # Use UI initial step but simulation's available frames
                        idx_map = idx_data.get('index', {})
                        if idx_map:
                            sim_steps = sorted([int(k) for k in idx_map.keys()])
                            
                            # Construct a virtual timestep list starting at user's initial step
                            # but using the simulation's available steps
                            ui_start = self.dsd_config['timesteps'][0] if self.dsd_config.get('timesteps') else sim_steps[0]
                            
                            # Find index of closest step in simulation to UI start
                            full_arr = np.array(sim_steps)
                            start_idx = (np.abs(full_arr - ui_start)).argmin()
                            
                            # The hashes depend on the Initial Step.
                            # Check if results starting at THIS specific initial step are ready.
                            check_steps = sim_steps[start_idx:]
                            
                            req_hashes = dm.get_required_hashes(
                                self.dsd_config['domains'], check_steps,
                                self.dsd_config['slice_axis'], self.dsd_config['observe_axis'], 
                                self.dsd_config['options']
                            )
                            if req_hashes and all(h in lib for h in req_hashes):
                                bg_color = "#a5d6a7" # Full DSD Ready
            except: pass
        item.setBackground(QColor(bg_color))

    def _set_all_checks(self, state):
        for i in range(self.list_widget.count()): self.list_widget.item(i).setCheckState(state)

    def _start_work(self, mode):
        tasks = []
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                tasks.append({'filepath': item.data(Qt.ItemDataRole.UserRole), 'mode': mode, 'dsd_config': self.dsd_config})
        
        if not tasks:
            logger.warning("Auto preload started with no systems selected.")
            QMessageBox.warning(self, "No selection", "Please check at least one system.")
            return
            
        self._set_ui_locked(True)
        self.status_label.setText("Working...")
        self.btn_cancel.setText("Stop")
        self.btn_cancel.clicked.disconnect()
        self.btn_cancel.clicked.connect(self._on_stop_clicked)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.progress_bar.setMaximum(len(tasks))
        
        self.worker = AutoIndexWorker(tasks)
        self.worker.progress.connect(lambda c, t: self.progress_bar.setValue(c))
        self.worker.file_finished.connect(self._on_file_done)
        self.worker.log.connect(lambda msg: self.status_label.setText(msg))
        self.worker.finished.connect(self._on_finished)
        self.worker.start()

    def _on_file_done(self, filepath, success):
        if not success: return
        try:
            done_path = Path(filepath)
            for i in range(self.list_widget.count()):
                item = self.list_widget.item(i)
                item_path = Path(item.data(Qt.ItemDataRole.UserRole))
                if item_path.samefile(done_path):
                    item.setCheckState(Qt.CheckState.Unchecked)
                    item.setSelected(False)
                    self._update_single_item_status(item)
                    break
        except: pass

    def _on_stop_clicked(self):
        if self.worker:
            self.worker.stop()
            self.status_label.setText("Stopping...")

    def _set_ui_locked(self, locked):
        self.list_widget.locked = locked
        
        # Use flags to disable interaction while keeping scrolling enabled
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            flags = item.flags()
            if locked:
                flags &= ~Qt.ItemFlag.ItemIsSelectable
                flags &= ~Qt.ItemFlag.ItemIsUserCheckable
            else:
                flags |= Qt.ItemFlag.ItemIsSelectable
                flags |= Qt.ItemFlag.ItemIsUserCheckable
            item.setFlags(flags)

        self.btn_basic.setEnabled(not locked)
        self.btn_dsd.setEnabled(not locked)
        self.btn_all.setEnabled(not locked)
        self.btn_none.setEnabled(not locked)

    @pyqtSlot(int, int)
    def _on_finished(self, success, failed):
        self.status_label.setText(f"Finished: {success} success, {failed} failed.")
        logger.info("Preload finished: %s success, %s failed.", success, failed)
        self._set_ui_locked(False)
        self.btn_cancel.setText("Close")
        self.btn_cancel.clicked.disconnect()
        self.btn_cancel.clicked.connect(self.reject)
        self._check_all_statuses()
        if failed == 0: QMessageBox.information(self, "Preload Complete", f"Processed {success} systems.")
        else: QMessageBox.warning(self, "Preload Complete", f"Finished with {failed} errors.")