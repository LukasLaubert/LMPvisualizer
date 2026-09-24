from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton,
    QListWidget, QLabel, QMessageBox, QGridLayout, QTextEdit,
    QListWidgetItem, QWidget, QSplitter
)
from PyQt6.QtCore import Qt, QEvent
from PyQt6.QtGui import QFont
import re
import numpy as np
from lmpvisualizer.log.log_data_manager import split_indexed_token, formula_min, formula_max

class ClickableListWidget(QListWidget):
    """A ListWidget that deselects items when clicking on empty space."""
    def mousePressEvent(self, event):
        # Check if an item is at the click position
        item = self.itemAt(event.position().toPoint())
        if not item:
            self.clearSelection()
            # Emit selection changed signal manually if needed, but clearSelection does it
        super().mousePressEvent(event)

class CustomPropertyDialog(QDialog):
    def __init__(self, available_columns, custom_properties, parent=None, index_validator=None):
        super().__init__(parent)
        self.setWindowTitle("Custom Axis Properties")
        self.resize(700, 600)
        
        self.available_columns = sorted(available_columns)
        self.custom_properties = custom_properties.copy() # Work on a copy
        self.original_properties = custom_properties # Reference to update on save
        self.index_validator = index_validator
        self.selected_result = None # Store the property to be used on close
        self.loaded_name = None # Entry currently shown in the editor (None = new)
        self._suppress_select = False # Select without loading (post-save/delete)
        
        self._init_ui()
        self._populate_lists()
        self._refresh_edit_state()
        self._refresh_delete_state()

    def _init_ui(self):
        main_layout = QHBoxLayout(self)

        # --- Left Side: Editor ---
        editor_panel = QWidget()
        editor_layout = QVBoxLayout(editor_panel)

        # Edit-state indicator: "New property" vs "Editing: <name> [●]"
        self.edit_state_label = QLabel("New property")
        self.edit_state_label.setStyleSheet("font-weight: bold;")
        editor_layout.addWidget(self.edit_state_label)

        # Name Input
        name_layout = QHBoxLayout()
        name_layout.addWidget(QLabel("Property Name:"))
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("e.g., TotalEnergy")
        self.name_input.textChanged.connect(self._refresh_edit_state)
        name_layout.addWidget(self.name_input)
        editor_layout.addLayout(name_layout)

        # Formula Input
        editor_layout.addWidget(QLabel("Formula:"))
        self.formula_input = QTextEdit()
        self.formula_input.setPlaceholderText("Select columns and symbols below... e.g., {PotentialEnergy} + {KineticEnergy}")
        self.formula_input.setMaximumHeight(100)
        self.formula_input.textChanged.connect(self._refresh_edit_state)
        editor_layout.addWidget(self.formula_input)

        # Math Symbols Grid (10 columns x 2 rows)
        symbols_group = QWidget()
        symbols_layout = QGridLayout(symbols_group)
        # Reduce default spacing for tighter packing
        symbols_layout.setHorizontalSpacing(5)
        symbols_layout.setVerticalSpacing(5)

        symbols = [
            ('+', '+'), ('-', '-'), ('*', '*'), ('/', '/'), ('^', '**'), ('(', '('), (')', ')'), ('min', 'min('), ('max', 'max('), ('sign', 'sign('),
            ('sqrt', 'sqrt('), ('sin', 'sin('), ('cos', 'cos('), ('tan', 'tan('), ('cot', 'cot('), ('e', 'np.e'), ('\u03c0', 'np.pi'), ('abs', 'abs('), ('exp', 'exp('), ('log', 'log('),
        ]

        row, col = 0, 0
        for label, value in symbols:
            btn = QPushButton(label)
            btn.setFixedWidth(38)

            btn.clicked.connect(lambda checked, v=value: self.insert_text(v))
            symbols_layout.addWidget(btn, row, col)
            col += 1
            if col > 9: # 10 columns, two rows
                col = 0
                row += 1

        # Add a stretch to the last column to push buttons to the left
        symbols_layout.setColumnStretch(10, 1)
        
        editor_layout.addWidget(symbols_group)

        # Columns List
        editor_layout.addWidget(QLabel("Available Properties (Double-click to insert):"))
        self.columns_list = QListWidget()
        self.columns_list.itemDoubleClicked.connect(self.insert_column)
        editor_layout.addWidget(self.columns_list)

        # Action Buttons
        btn_layout = QHBoxLayout()
        self.save_btn = QPushButton("Save")
        self.save_btn.clicked.connect(self.save_definition)
        self.clear_btn = QPushButton("New")
        self.clear_btn.clicked.connect(self.new_entry)
        btn_layout.addWidget(self.save_btn)
        btn_layout.addWidget(self.clear_btn)
        editor_layout.addLayout(btn_layout)

        # --- Right Side: Manager ---
        manager_panel = QWidget()
        manager_layout = QVBoxLayout(manager_panel)
        
        manager_layout.addWidget(QLabel("Defined Custom Properties (click to load):"))
        self.properties_list = ClickableListWidget()
        self.properties_list.itemSelectionChanged.connect(self.on_selection_changed)
        manager_layout.addWidget(self.properties_list)

        manager_btns = QHBoxLayout()
        self.delete_btn = QPushButton("Delete selected")
        self.delete_btn.clicked.connect(self.delete_property)
        manager_btns.addWidget(self.delete_btn)
        manager_layout.addLayout(manager_btns)

        # Add panels to splitter
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(editor_panel)
        splitter.addWidget(manager_panel)
        splitter.setSizes([500, 300])
        
        main_layout.addWidget(splitter)

        # Close Button
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.accept)
        self.close_btn.setFixedWidth(120)
        
        # Add close button to the right side manager layout
        manager_layout.addSpacing(10)
        manager_layout.addWidget(self.close_btn, alignment=Qt.AlignmentFlag.AlignRight)

    def _populate_lists(self):
        self.columns_list.clear()
        
        # Add regular columns
        for col in self.available_columns:
            self.columns_list.addItem(col)
            
        # Add existing custom properties to the list on the right
        self.properties_list.clear()
        for name, formula in self.custom_properties.items():
            item = QListWidgetItem(f"{name} = {formula}")
            item.setData(Qt.ItemDataRole.UserRole, name)
            # No italic styling here anymore
            self.properties_list.addItem(item)
            
        # Add existing custom properties to available columns
        for name in self.custom_properties:
            item = QListWidgetItem(name)
            font = item.font()
            font.setItalic(True)
            item.setFont(font)
            self.columns_list.addItem(item)

    def on_selection_changed(self):
        items = self.properties_list.selectedItems()
        if items:
            self.close_btn.setText("Set && Close") # Use literal &
        else:
            self.close_btn.setText("Close")
        self._refresh_delete_state()

        if self._suppress_select:
            return

        target = items[0].data(Qt.ItemDataRole.UserRole) if items else None
        if target == self.loaded_name:
            return # Re-clicked the loaded entry: keep edits, nothing to do
        if self._is_dirty():
            choice = self._prompt_unsaved_changes()
            if choice == 'save':
                if not self.save_definition(from_prompt=True):
                    self._reselect_loaded()
                    return
                # Save highlighted the saved entry; move the selection on
                # to the pending target so list and editor stay in sync.
                self._suppress_select = True
                try:
                    if target is None:
                        self.properties_list.clearSelection()
                    else:
                        for i in range(self.properties_list.count()):
                            item = self.properties_list.item(i)
                            if item.data(Qt.ItemDataRole.UserRole) == target:
                                self.properties_list.setCurrentItem(item)
                                break
                finally:
                    self._suppress_select = False
            elif choice == 'discard':
                pass
            else: # cancel
                self._reselect_loaded()
                return
        if target is None:
            self._show_new()
        else:
            self._load_name(target)

    def _reselect_loaded(self):
        """Restore the list selection to the entry shown in the editor."""
        self._suppress_select = True
        try:
            if self.loaded_name is None:
                self.properties_list.clearSelection()
            else:
                for i in range(self.properties_list.count()):
                    item = self.properties_list.item(i)
                    if item.data(Qt.ItemDataRole.UserRole) == self.loaded_name:
                        self.properties_list.setCurrentItem(item)
                        break
                else:
                    self.properties_list.clearSelection()
        finally:
            self._suppress_select = False
        self._refresh_edit_state()

    def _refresh_delete_state(self):
        # Delete acts on the visible selection only; a stale current item
        # (e.g. after clearSelection) must never be deletable.
        self.delete_btn.setEnabled(bool(self.properties_list.selectedItems()))

    def _prompt_unsaved_changes(self):
        """Ask what to do with a dirty editor. Returns 'save'/'discard'/'cancel'."""
        box = QMessageBox(self)
        box.setWindowTitle("Unsaved Changes")
        box.setText("The editor has unsaved changes. What should happen to them?")
        save_btn = box.addButton("Save", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Discard", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(save_btn)
        box.exec()
        if box.clickedButton() == save_btn:
            return 'save'
        for btn in box.buttons():
            if box.buttonRole(btn) == QMessageBox.ButtonRole.DestructiveRole and box.clickedButton() == btn:
                return 'discard'
        return 'cancel'

    def _is_dirty(self):
        """Editor differs from the loaded entry (or non-empty in new mode)."""
        name = self.name_input.text().strip()
        formula = self.formula_input.toPlainText().strip()
        if self.loaded_name is None:
            return bool(name or formula)
        return name != self.loaded_name or formula != self.custom_properties.get(self.loaded_name, "")

    def _refresh_edit_state(self):
        if self.loaded_name is None:
            base = "New property"
        else:
            base = f"Editing: {self.loaded_name}"
        if self._is_dirty():
            base += " \u25cf"
        self.edit_state_label.setText(base)

    def _set_editor(self, name, formula):
        self.name_input.blockSignals(True)
        self.formula_input.blockSignals(True)
        try:
            self.name_input.setText(name)
            self.formula_input.setText(formula)
        finally:
            self.name_input.blockSignals(False)
            self.formula_input.blockSignals(False)
        self._refresh_edit_state()

    def _load_name(self, name):
        self.loaded_name = name
        self._set_editor(name, self.custom_properties.get(name, ""))

    def _show_new(self):
        self.loaded_name = None
        self._set_editor("", "")

    def new_entry(self):
        """'New' button: leave a dirty editor only via the save prompt."""
        if self._is_dirty():
            choice = self._prompt_unsaved_changes()
            if choice == 'save':
                if not self.save_definition(from_prompt=True):
                    return
            elif choice == 'cancel':
                return
        self._suppress_select = True
        try:
            self.properties_list.clearSelection()
        finally:
            self._suppress_select = False
        self._show_new()
        self._refresh_delete_state()

    def load_property(self, item):
        name = item.data(Qt.ItemDataRole.UserRole)
        if name in self.custom_properties:
            self._load_name(name)

    def insert_text(self, text):
        self.formula_input.insertPlainText(text)
        self.formula_input.setFocus()

    def insert_column(self, item):
        # Wrap in curly braces to identify as a variable/column
        text = f"{{{item.text()}}}"
        self.insert_text(text)

    def delete_property(self):
        items = self.properties_list.selectedItems()
        if not items:
            return
        item = items[0]

        row = self.properties_list.row(item)
        name = item.data(Qt.ItemDataRole.UserRole)
        if name not in self.custom_properties:
            return
        del self.custom_properties[name]

        # Update the external dict immediately
        if name in self.original_properties:
            del self.original_properties[name]

        # Deleting the loaded entry keeps the editor content as a new entry,
        # and the adjacent auto-select must not load over it. Deleting any
        # other entry auto-selects the neighbor normally so list and editor
        # can never disagree (clicking a highlighted row emits no signal,
        # so a silent mismatch would strand edits on the wrong entry).
        keep_editor = (name == self.loaded_name)
        if keep_editor:
            self.loaded_name = None
        self._suppress_select = True
        try:
            self._populate_lists()
        finally:
            self._suppress_select = False
        if keep_editor:
            self._suppress_select = True
            try:
                # Auto-select adjacent item
                count = self.properties_list.count()
                if count > 0:
                    if row < count:
                        self.properties_list.setCurrentRow(row)
                    else:
                        self.properties_list.setCurrentRow(count - 1)
            finally:
                self._suppress_select = False
            self._refresh_edit_state()
        else:
            # Unsuppressed: loads the neighbor (or clears to new when the
            # list is empty). The editor is clean here, so no prompt appears.
            count = self.properties_list.count()
            if count > 0:
                if row < count:
                    self.properties_list.setCurrentRow(row)
                else:
                    self.properties_list.setCurrentRow(count - 1)
        self._refresh_delete_state()

    def validate_formula(self, formula):
        """Checks if the formula has valid syntax and uses allowed tokens."""
        
        # 1. Identify all tokens {Token}
        tokens = re.findall(r"\{([^}]+)\}", formula)

        # Check if symbols inside {} are available, allowing indexed tokens like {Lx(end-1)}
        known_names = set(self.available_columns) | set(self.custom_properties)
        for token in tokens:
            base_token, _ = split_indexed_token(token, known_names)
            if base_token not in known_names:
                return False, f"Symbol '{{{token}}}' is not found in available properties."
        
        # 2. Replace {Token} with 'var' in the formula for syntax checking
        clean_formula = re.sub(r"\{[^}]+\}", "var", formula)
        
        # 3. Handle cot() logic
        clean_formula = clean_formula.replace("cot(", "1/np.tan(")

        # Handle '^' -> '**' for user convenience (matches LogDataManager)
        clean_formula = clean_formula.replace("^", "**")
        
        # 4. Define safe context
        # This context must match log_data_manager's safe_globals + 'var'
        safe_globals = {
            "__builtins__": None,
            "np": np,
            "sqrt": np.sqrt,
            "sin": np.sin,
            "cos": np.cos,
            "tan": np.tan,
            "min": formula_min,
            "max": formula_max,
            "abs": np.abs,
            "sign": np.sign,
            "exp": np.exp,
            "log": np.log,
            "e": np.e,
            "pi": np.pi,
            "var": 1.0 # Dummy value for columns
        }
        
        try:
            # Use eval to catch NameError (unknown variables) and SyntaxError
            eval(clean_formula, safe_globals)
        except Exception as e:
            return False, str(e)

        if self.index_validator:
            return self.index_validator(formula, self.custom_properties)

        return True, ""

    def _store_definition(self, name, formula):
        self.custom_properties[name] = formula
        self.original_properties[name] = formula # Update the reference passed in __init__

    def _ask_rename_or_new(self, old_name, new_name):
        """Name changed on save: create new (keep old) or rename (replace old)?"""
        box = QMessageBox(self)
        box.setWindowTitle("Save Property")
        box.setText(f"'{old_name}' was renamed to '{new_name}'.")
        new_btn = box.addButton("Create New", QMessageBox.ButtonRole.AcceptRole)
        rename_btn = box.addButton("Rename", QMessageBox.ButtonRole.ActionRole)
        box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(new_btn)
        box.setInformativeText("Create New keeps the old entry; Rename replaces it.")
        box.exec()
        clicked = box.clickedButton()
        if clicked == new_btn:
            return 'new'
        if clicked == rename_btn:
            return 'rename'
        return 'cancel'

    def save_definition(self, from_prompt=False):
        """Store the editor content.

        Same-name saves overwrite silently. A changed name asks
        Create New vs Rename — but only for a direct Save press
        (from_prompt=False). Saves originating from the unsaved-changes
        prompt or dialog close store in place without asking twice.
        """
        name = self.name_input.text().strip()
        formula = self.formula_input.toPlainText().strip()

        if not name:
            QMessageBox.warning(self, "Error", "Property name cannot be empty.")
            return False
        if not formula:
            QMessageBox.warning(self, "Error", "Formula cannot be empty.")
            return False

        # Validation
        is_valid, error_msg = self.validate_formula(formula)
        if not is_valid:
             QMessageBox.warning(self, "Syntax Error", f"Formula syntax is invalid:\n{error_msg}")
             return False

        loaded = self.loaded_name
        if loaded is not None and name != loaded:
            # Renamed in the editor: direct Save asks create-new vs rename,
            # prompt-originated saves store in place without asking twice.
            if name in self.custom_properties:
                QMessageBox.warning(self, "Error", f"Property '{name}' already exists.")
                return False
            if from_prompt:
                choice = 'rename'
            else:
                choice = self._ask_rename_or_new(loaded, name)
            if choice == 'cancel':
                return False
            if choice == 'rename':
                del self.custom_properties[loaded]
                if loaded in self.original_properties:
                    del self.original_properties[loaded]
            self._store_definition(name, formula)
        else:
            if loaded is None and name in self.custom_properties:
                # New-mode save onto an existing name: confirm overwrite.
                reply = QMessageBox.question(
                    self, "Overwrite Property",
                    f"Property '{name}' already exists. Overwrite it?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel)
                if reply != QMessageBox.StandardButton.Yes:
                    return False
            self._store_definition(name, formula)

        self._suppress_select = True
        try:
            self._populate_lists()
        finally:
            self._suppress_select = False

        # Keep the saved entry in the editor (clean, not cleared) and
        # highlight it for Set & Close without triggering a reload.
        self.loaded_name = name
        self._refresh_edit_state()
        self._suppress_select = True
        try:
            for i in range(self.properties_list.count()):
                item = self.properties_list.item(i)
                if item.data(Qt.ItemDataRole.UserRole) == name:
                    self.properties_list.setCurrentItem(item)
                    break
        finally:
            self._suppress_select = False
        self._refresh_delete_state()

        return True

    def get_properties(self):
        return self.custom_properties

    def get_selected_property(self):
        return self.selected_result

    def accept(self):
        # Implicit save if editor has content
        if self.name_input.text().strip() or self.formula_input.toPlainText().strip():
            if not self.save_definition(from_prompt=True):
                return
        
        # If an item is selected, we return it as the result
        items = self.properties_list.selectedItems()
        if items:
            self.selected_result = items[0].data(Qt.ItemDataRole.UserRole)
        else:
            self.selected_result = None
        super().accept()