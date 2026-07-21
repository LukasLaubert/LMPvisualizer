import re
import math
import random
import numpy as np
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLineEdit, QPushButton,
    QLabel, QComboBox, QDoubleSpinBox, QSpinBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QCheckBox, QFrame, QMessageBox, QApplication, QSizePolicy
)
from PyQt6.QtCore import Qt, pyqtSignal, QThreadPool
from PyQt6.QtGui import QIcon, QValidator

from fitting_engine import FitWorker

class ScientificSpinBox(QDoubleSpinBox):
    """
    A SpinBox that displays values in scientific notation (e.g., 1.000000000000e+01).
    Locale-aware: uses system default for decimal separators ('.' or ',').
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDecimals(100)
        self.setRange(-float('inf'), float('inf'))
        self.setKeyboardTracking(False)

    def validate(self, text, pos):
        """
        Allow typing valid local numbers or scientific notation.
        Adapts regex to the current locale's decimal point.
        """
        if not text:
            return QValidator.State.Intermediate, text, pos
            
        # Get system decimal point (e.g. ',' or '.')
        dp = self.locale().decimalPoint()
        escaped_dp = re.escape(dp)

        # check if it parses via locale immediately
        val, ok = self.locale().toDouble(text)
        if ok:
            return QValidator.State.Acceptable, text, pos

        # Allow intermediate typing (e.g. "-", "1,", "1.2e")
        # Regex: Optional +/- -> Digits + Optional Decimal + Optional Digits -> Optional Exponent
        pattern = f"^[-+]?(\\d+({escaped_dp}\\d*)?|{escaped_dp}\\d+)?([eE][-+]?\\d*)?$"
        
        if re.match(pattern, text):
            return QValidator.State.Intermediate, text, pos
            
        return QValidator.State.Invalid, text, pos

    def textFromValue(self, value):
        # Format to scientific 'e' notation with 16 digits (max double precision)
        return self.locale().toString(value, 'e', 16)

    def valueFromText(self, text):
        # Parse using system locale
        val, ok = self.locale().toDouble(text)
        return val if ok else 0.0

    def stepBy(self, steps):
        val = self.value()
        if val == 0:
            magnitude = 0
        else:
            magnitude = math.floor(math.log10(abs(val)))

        modifiers = QApplication.keyboardModifiers()
        is_shift = modifiers & Qt.KeyboardModifier.ShiftModifier

        if is_shift:
            # Shift: Modify the integer digit (10^e)
            step_size = 10.0 ** magnitude
        else:
            # Normal: Modify the 1st digit after comma -> 10^(e-1)
            step_size = 10.0 ** (magnitude - 1)

        if val == 0:
            step_size = 1.0 if is_shift else 0.1

        self.setValue(val + (steps * step_size))

class FitFunctionDialog(QWidget):
    """
    Redesigned intuitive curve fitting configuration.
    Structure: Define -> Configure -> Execute.
    """
    fitUpdated = pyqtSignal(dict) # Emits result to main window
    
    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Tool)
        self.setWindowTitle("Fit Function Configuration")
        self.resize(450, 650)
        
        self.x_data = []
        self.y_data = []
        self.current_worker = None
        self.thread_pool = QThreadPool()
        
        # Internal state for fit repetitions
        self.target_reps = 1
        self.current_rep_count = 0
        
        # Map friendly names to SciPy method codes
        self.methods_map = {
            "Nelder-Mead (Simplex)": "Nelder-Mead",
            "Powell (Direction Set)": "Powell",
            "BFGS (Quasi-Newton)": "BFGS",
            "L-BFGS-B (Bounded Quasi-Newton)": "L-BFGS-B",
            "Conjugate Gradient (CG)": "CG",
            "Cobyla (Constrained)": "COBYLA"
        }
        
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(15, 15, 15, 15)
        
        # --- Zone 1: Definition ---
        preset_layout = QHBoxLayout()
        preset_layout.addWidget(QLabel("Preset:"))
        self.preset_combo = QComboBox()
        self._populate_presets()
        self.preset_combo.currentTextChanged.connect(self._apply_preset)
        preset_layout.addWidget(self.preset_combo, 1)
        layout.addLayout(preset_layout)

        layout.addWidget(QLabel("Function y(x) ="))
        
        func_box = QHBoxLayout()
        self.func_input = QLineEdit("a*x + b")
        self.func_input.setPlaceholderText("e.g. a*x + b")
        self.func_input.textChanged.connect(self._on_func_changed)
        
        self.help_btn = QPushButton("?")
        self.help_btn.setFixedWidth(30)
        self.help_btn.setToolTip("Click for Syntax Help")
        self.help_btn.clicked.connect(self._show_syntax_help)
        
        func_box.addWidget(self.func_input)
        func_box.addWidget(self.help_btn)
        layout.addLayout(func_box)

        # --- Zone 2: Parameters (The "Smart" part) ---
        # Modified header layout to include Shuffle button
        param_header_layout = QHBoxLayout()
        param_header_layout.addWidget(QLabel("Parameters (Initial Guess):"))
        param_header_layout.addStretch()
        
        self.shuffle_btn = QPushButton("Shuffle")
        self.shuffle_btn.setToolTip("Multiply unlocked parameters by a random factor (0.01 - 100)")
        self.shuffle_btn.setFixedWidth(60)
        self.shuffle_btn.setStyleSheet("padding: 2px; font-size: 10pt;")
        self.shuffle_btn.clicked.connect(self._shuffle_params)
        param_header_layout.addWidget(self.shuffle_btn)
        
        layout.addLayout(param_header_layout)
        
        self.param_table = QTableWidget()
        self.param_table.setColumnCount(3)
        self.param_table.setHorizontalHeaderLabels(["Name", "Value", "Lock"])
        
        # Column sizing
        header = self.param_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.param_table.setColumnWidth(2, 60)
        
        self.param_table.verticalHeader().hide()
        self.param_table.setMaximumHeight(150)
        layout.addWidget(self.param_table)

        # --- Zone 3: Range / Scope ---
        range_group = QFrame()
        range_group.setFrameShape(QFrame.Shape.StyledPanel)
        range_layout = QHBoxLayout(range_group)
        
        # Min X
        self.min_lbl = QLabel("X Min:")
        self.min_spin = ScientificSpinBox()
        
        # Max X
        self.max_lbl = QLabel("X Max:")
        self.max_spin = ScientificSpinBox()
        self.max_spin.setValue(100.0)
        
        # Add stretch factor '1' to spinboxes to make them expand
        range_layout.addWidget(self.min_lbl)
        range_layout.addWidget(self.min_spin, 1) 
        range_layout.addSpacing(15)
        range_layout.addWidget(self.max_lbl)
        range_layout.addWidget(self.max_spin, 1)
        layout.addWidget(range_group)

        # --- Zone 4: Advanced (Grid Layout) ---
        adv_grid = QGridLayout()
        adv_grid.setContentsMargins(0, 0, 0, 0)
        adv_grid.setHorizontalSpacing(10)
        
        # 1. Optimization Method
        adv_grid.addWidget(QLabel("Optimization Method:"), 0, 0)
        self.method_combo = QComboBox()
        self.method_combo.addItems(list(self.methods_map.keys()))
        self.method_combo.setMaximumWidth(150)
        self.method_combo.view().setMinimumWidth(240) 
        adv_grid.addWidget(self.method_combo, 1, 0)
        
        # 2. Minimize Error
        adv_grid.addWidget(QLabel("Minimize Error:"), 0, 1)
        self.error_combo = QComboBox()
        self.error_combo.addItems(["Mean Squared Error", "Mean Absolute Error", "Mean Bias", "Mean Cubed Error"])
        self.error_combo.setMaximumWidth(140)
        adv_grid.addWidget(self.error_combo, 1, 1)

        # 3. Fit Reps
        reps_lbl = QLabel("Fit reps:")
        adv_grid.addWidget(reps_lbl, 0, 2)
        
        self.reps_spin = QSpinBox()
        self.reps_spin.setRange(1, 99)
        self.reps_spin.setValue(3)
        self.reps_spin.setToolTip("Repeat the fit this many times, refining results.")
        # Fixed width ~20% smaller than standard
        self.reps_spin.setFixedWidth(60)
        adv_grid.addWidget(self.reps_spin, 1, 2)
        
        # 4. Rep Perturb
        perturb_lbl = QLabel("Rep perturb:")
        adv_grid.addWidget(perturb_lbl, 0, 3)
        
        self.perturb_spin = QDoubleSpinBox()
        self.perturb_spin.setRange(0.0, 100.0)
        self.perturb_spin.setValue(10.0)
        self.perturb_spin.setSuffix("%")
        self.perturb_spin.setToolTip("Randomly perturb parameters by ±X% before each repetition.")
        # Increased width to fit label and percentage
        self.perturb_spin.setFixedWidth(90)
        adv_grid.addWidget(self.perturb_spin, 1, 3)

        # Column Stretches: 
        # Method: 0 (Fit to max width)
        # Error: 1 (Take all remaining space)
        # Reps/Perturb: 0 (Fit to fixed width)
        adv_grid.setColumnStretch(0, 0)
        adv_grid.setColumnStretch(1, 1)
        adv_grid.setColumnStretch(2, 0)
        adv_grid.setColumnStretch(3, 0)
        
        # Align labels to match field start
        adv_grid.setAlignment(reps_lbl, Qt.AlignmentFlag.AlignLeft)
        adv_grid.setAlignment(perturb_lbl, Qt.AlignmentFlag.AlignLeft)

        layout.addLayout(adv_grid)

        # --- Zone 5: Action & Results ---
        self.calc_btn = QPushButton("Calculate Fit")
        self.calc_btn.setStyleSheet("font-weight: bold; padding: 6px; font-size: 11pt;")
        self.calc_btn.clicked.connect(self._trigger_fit) 
        layout.addWidget(self.calc_btn)
        
        self.result_lbl = QLabel("Status: Ready")
        self.result_lbl.setWordWrap(True)
        self.result_lbl.setStyleSheet("color: #333; border: 1px solid #ccc; padding: 8px; background: #f9f9f9;")
        layout.addWidget(self.result_lbl)
        
        layout.addStretch()

        # Initial Parse
        self._on_func_changed()

    def _populate_presets(self):
        # Display Name -> Python Formula (using ** for power)
        self.presets = {
            "Custom...": "",
            "Linear Line (a*x + b)": "a*x + b",
            "Quadratic Parabola (a*x^2 + b*x + c)": "a*x**2 + b*x + c",
            "Broken Rational ((a*x + b) / (x + c))": "(a*x + b) / (x + c)",
            "Hyperbola (a / (x + b) + c)": "a / (x + b) + c",
            "Exponential Growth (a * exp(b*x))": "a * exp(b*x)",
            "Power Law (a * x^b)": "a * x**b",
            "Gaussian Bell": "a * exp(-((x-b)**2)/(2*c**2))",
            "Sine Wave": "a * sin(b*x + c) + d",
            "Sigmoid / Logistic": "a / (1 + exp(-b*(x-c)))",
            "Logarithmic": "a * log(b*x) + c"
        }
        self.preset_combo.addItems(self.presets.keys())

    def _apply_preset(self, text):
        func = self.presets.get(text)
        if func:
            self.func_input.setText(func)
            self.preset_combo.blockSignals(True)
            self.preset_combo.setCurrentIndex(0) # Reset to Custom so user can edit
            self.preset_combo.blockSignals(False)

    def _show_syntax_help(self):
        msg = (
            "<h3>Supported Math Syntax</h3>"
            "<ul>"
            "<li><b>Operations:</b> +, -, *, /, ^ (or **)</li>"
            "<li><b>Functions:</b> sqrt(), sin(), cos(), tan(), exp(), log() (natural), log10(), abs()</li>"
            "<li><b>Constants:</b> pi, e</li>"
            "</ul>"
            "<p><i>Note: 'x' is the independent variable. Any other single letter (a, b, k...) is treated as a parameter to fit.</i></p>"
        )
        QMessageBox.information(self, "Syntax Help", msg)

    def _on_func_changed(self):
        text = self.func_input.text()
        
        # Simple parser to find params
        ignore = {'x', 'sin', 'cos', 'tan', 'sqrt', 'exp', 'log', 'abs', 'pi', 'e', 'np', 'power', 'log10'}
        tokens = re.findall(r'\b[a-zA-Z_]\w*\b', text)
        params = sorted(list(set([t for t in tokens if t not in ignore])))
        
        # Preserve existing values if possible
        current_data = {}
        for row in range(self.param_table.rowCount()):
            name_item = self.param_table.item(row, 0)
            if not name_item: continue
            name = name_item.text()
            
            val_widget = self.param_table.cellWidget(row, 1)
            val = val_widget.value() if val_widget else 1.0
            
            # Robustly check lock state
            locked = False
            container = self.param_table.cellWidget(row, 2)
            if container:
                children = container.findChildren(QCheckBox)
                if children:
                    locked = children[0].isChecked()
            
            current_data[name] = (val, locked)
            
        self.param_table.setRowCount(len(params))
        
        for i, p in enumerate(params):
            # Name
            item = QTableWidgetItem(p)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.param_table.setItem(i, 0, item)
            
            # Value (Must use ScientificSpinBox to show e-17 correctly)
            spin = ScientificSpinBox()
            if p in current_data:
                spin.setValue(current_data[p][0])
            else:
                spin.setValue(0.0 if p in ['b', 'c', 'd'] else 1.0)
            self.param_table.setCellWidget(i, 1, spin)
            
            # Lock Checkbox (Centered)
            chk = QCheckBox()
            if p in current_data:
                chk.setChecked(current_data[p][1])
                
            # Centering Container
            container = QWidget()
            l = QHBoxLayout(container)
            l.setContentsMargins(0,0,0,0)
            l.setAlignment(Qt.AlignmentFlag.AlignCenter)
            l.addWidget(chk)
            self.param_table.setCellWidget(i, 2, container)

    def set_data(self, x_data, y_data):
        # Check for change
        changed = False
        
        # Helper to compare arrays handling None
        def arrays_different(a, b):
            if a is None and b is None: return False
            if a is None or b is None: return True
            
            # Convert to numpy for comparison if not already
            # (Safety for lists vs arrays)
            try:
                a_arr = np.asanyarray(a)
                b_arr = np.asanyarray(b)
            except Exception:
                return True # Cannot convert, assume changed
            
            if a_arr.shape != b_arr.shape: return True
            if a_arr.size == 0 and b_arr.size == 0: return False
            
            try:
                # Use array_equal for exact match (fast and robust)
                # equal_nan=True ensures NaN == NaN
                return not np.array_equal(a_arr, b_arr, equal_nan=True)
            except Exception:
                return True

        if arrays_different(self.x_data, x_data) or arrays_different(self.y_data, y_data):
            changed = True

        self.x_data = x_data
        self.y_data = y_data
        
        # Auto-range if defaults are untouched
        if self.min_spin.value() == 0 and self.max_spin.value() == 100:
            if x_data is not None and len(x_data) > 0:
                if hasattr(x_data, 'min'):
                    self.min_spin.setValue(float(x_data.min()))
                    self.max_spin.setValue(float(x_data.max()))
                else:
                    self.min_spin.setValue(float(np.min(x_data)))
                    self.max_spin.setValue(float(np.max(x_data)))
        
        return changed

    def _shuffle_params(self):
        """Randomize all unlocked parameters by a factor of 0.01 to 100."""
        for row in range(self.param_table.rowCount()):
            # Check lock state
            container = self.param_table.cellWidget(row, 2)
            locked = False
            if container:
                children = container.findChildren(QCheckBox)
                if children:
                    locked = children[0].isChecked()
            
            if not locked:
                spin = self.param_table.cellWidget(row, 1)
                current_val = spin.value()
                # Multiply by random factor
                factor = random.uniform(0.01, 100.0)
                spin.setValue(current_val * factor)

    def _apply_perturbation(self):
        """Apply random perturbation to unlocked parameters before next rep."""
        percent = self.perturb_spin.value()
        if percent <= 0: return

        # Convert percent to fraction (e.g., 10% -> 0.1)
        p = percent / 100.0
        
        for row in range(self.param_table.rowCount()):
            container = self.param_table.cellWidget(row, 2)
            locked = False
            if container:
                children = container.findChildren(QCheckBox)
                if children and children[0].isChecked():
                    locked = True
            
            if not locked:
                spin = self.param_table.cellWidget(row, 1)
                current_val = spin.value()
                
                # Random factor between (1-p) and (1+p)
                factor = random.uniform(1.0 - p, 1.0 + p)
                
                # Handle edge case where value is 0
                if current_val == 0:
                    spin.setValue(random.uniform(-p, p)) # Small nudge from 0
                else:
                    spin.setValue(current_val * factor)

    def _trigger_fit(self):
        """Initialize repetition logic and start the first fit."""
        if self.current_worker:
            return

        # Initialize loop variables
        self.target_reps = self.reps_spin.value()
        self.current_rep_count = 0
        
        self.calculate_fit()

    def calculate_fit(self):
        """Triggers the fitting process. Can be called internally (button loop) or externally."""
        if self.x_data is None or len(self.x_data) == 0:
            self.result_lbl.setText("Status: No Data Source linked.")
            return

        # Gather config
        func_str = self.func_input.text()
        params_config = {}
        
        for row in range(self.param_table.rowCount()):
            name_item = self.param_table.item(row, 0)
            if not name_item: continue
            name = name_item.text()
            
            val_widget = self.param_table.cellWidget(row, 1)
            val = val_widget.value()
            
            # Robust Checkbox Retrieval
            locked = False
            container = self.param_table.cellWidget(row, 2)
            if container:
                children = container.findChildren(QCheckBox)
                if children:
                    locked = children[0].isChecked()
            
            params_config[name] = {'guess': val, 'locked': locked}

        if not params_config:
            self.result_lbl.setText("Status: No parameters to fit.")
            return

        bounds = (self.min_spin.value(), self.max_spin.value())
            
        status_msg = f"Status: Fitting..."
        if self.target_reps > 1:
            status_msg = f"Status: Fitting (Rep {self.current_rep_count + 1}/{self.target_reps})..."
        self.result_lbl.setText(status_msg)
        
        # NOTE: We do NOT disable the button here to preserve focus logic
        self.calc_btn.setText("Fitting...") 

        friendly_method = self.method_combo.currentText()
        tech_method = self.methods_map.get(friendly_method, "Nelder-Mead")

        # Pass None/Default for time limit since UI control was removed
        self.current_worker = FitWorker(
            self.x_data, self.y_data, func_str, params_config, bounds,
            tech_method,
            self.error_combo.currentText(),
            10.0 # Default fallback time limit, logic now relies on repetitions
        )
        self.current_worker.signals.finished.connect(self._on_fit_finished)
        self.current_worker.signals.error.connect(self._on_fit_error)
        
        self.thread_pool.start(self.current_worker)

    def _on_fit_finished(self, result):
        self.current_worker = None
        
        # Update table with results of THIS run
        for row in range(self.param_table.rowCount()):
            name_item = self.param_table.item(row, 0)
            if not name_item: continue
            name = name_item.text()
            
            if name in result['best_params']:
                self.param_table.cellWidget(row, 1).setValue(result['best_params'][name])

        # Repetition Logic
        self.current_rep_count += 1
        
        if self.current_rep_count < self.target_reps:
            # Prepare for next rep
            self._apply_perturbation()
            # Trigger next run immediately
            self.calculate_fit()
        else:
            # All reps finished
            self.calc_btn.setText("Calculate Fit")
            
            # Final Status Update
            err_name = self.error_combo.currentText()
            err_val = result['final_error']
            p_str = "\n".join([f"{k} = {v:.4g}" for k, v in result['best_params'].items()])
            
            self.result_lbl.setText(f"<b>Fit Successful!</b> (Reps: {self.target_reps})<br>Error ({err_name}): {err_val:.4g}<br>Parameters:<br>{p_str}")
            
            self.fitUpdated.emit(result)

    def _on_fit_error(self, msg):
        self.current_worker = None
        self.calc_btn.setText("Calculate Fit")
        self.result_lbl.setText(f"<font color='red'>Error: {msg}</font>")

    def get_state(self):
        p_data = {}
        for row in range(self.param_table.rowCount()):
            name_item = self.param_table.item(row, 0)
            if not name_item: continue
            name = name_item.text()
            
            val = self.param_table.cellWidget(row, 1).value()
            
            container = self.param_table.cellWidget(row, 2)
            locked = False
            if container:
                children = container.findChildren(QCheckBox)
                if children: locked = children[0].isChecked()
            
            p_data[name] = {'val': val, 'locked': locked}

        return {
            'function': self.func_input.text(),
            'params': p_data,
            'min': self.min_spin.value(),
            'max': self.max_spin.value(),
            'method': self.method_combo.currentText(),
            'error': self.error_combo.currentText(),
            'reps': self.reps_spin.value(),
            'perturb': self.perturb_spin.value()
        }

    def set_state(self, state):
        if not state: return
        self.func_input.setText(state.get('function', 'a*x+b'))
        self.min_spin.setValue(state.get('min', 0))
        self.max_spin.setValue(state.get('max', 100))
        self.method_combo.setCurrentText(state.get('method', list(self.methods_map.keys())[0]))
        self.error_combo.setCurrentText(state.get('error', 'Mean Squared Error'))
        
        # Load new state fields if present
        if 'reps' in state: self.reps_spin.setValue(state['reps'])
        if 'perturb' in state: self.perturb_spin.setValue(state['perturb'])
        
        self._on_func_changed()
        saved_params = state.get('params', {})
        for row in range(self.param_table.rowCount()):
            name_item = self.param_table.item(row, 0)
            if not name_item: continue
            name = name_item.text()
            
            if name in saved_params:
                self.param_table.cellWidget(row, 1).setValue(saved_params[name]['val'])
                
                container = self.param_table.cellWidget(row, 2)
                if container:
                    children = container.findChildren(QCheckBox)
                    if children:
                        children[0].setChecked(saved_params[name]['locked'])