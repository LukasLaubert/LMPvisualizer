from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton,
    QListWidget, QLabel, QMessageBox, QGridLayout, QTextEdit,
    QListWidgetItem, QWidget, QSplitter
)
from PyQt6.QtCore import Qt, QEvent
from PyQt6.QtGui import QFont
import re
import numpy as np

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
    def __init__(self, available_columns, custom_properties, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Custom Axis Properties")
        self.resize(600, 600)
        
        self.available_columns = sorted(available_columns)
        self.custom_properties = custom_properties.copy() # Work on a copy
        self.original_properties = custom_properties # Reference to update on save
        self.selected_result = None # Store the property to be used on close
        
        self._init_ui()
        self._populate_lists()

    def _init_ui(self):
        main_layout = QHBoxLayout(self)

        # --- Left Side: Editor ---
        editor_panel = QWidget()
        editor_layout = QVBoxLayout(editor_panel)
        
        # Name Input
        name_layout = QHBoxLayout()
        name_layout.addWidget(QLabel("Property Name:"))
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("e.g., TotalEnergy")
        name_layout.addWidget(self.name_input)
        editor_layout.addLayout(name_layout)

        # Formula Input
        editor_layout.addWidget(QLabel("Formula:"))
        self.formula_input = QTextEdit()
        self.formula_input.setPlaceholderText("Select columns and symbols below... e.g., {PotentialEnergy} + {KineticEnergy}")
        self.formula_input.setMaximumHeight(100)
        editor_layout.addWidget(self.formula_input)

        # Math Symbols Grid
        symbols_group = QWidget()
        symbols_layout = QGridLayout(symbols_group)
        # Reduce default spacing for tighter packing
        symbols_layout.setHorizontalSpacing(5)
        symbols_layout.setVerticalSpacing(5)
        
        symbols = [('+', '+'), ('-', '-'), ('*', '*'), ('/', '/'), ('^', '**'), ('(', '('), (')', ')'), ('sqrt', 'sqrt('), ('sin', 'sin('), ('cos', 'cos('), ('tan', 'tan('), ('cot', 'cot('), ('e', 'np.e'), ('π', 'np.pi')]
        
        row, col = 0, 0
        for label, value in symbols:
            btn = QPushButton(label)
            btn.setFixedWidth(40)
            
            btn.clicked.connect(lambda checked, v=value: self.insert_text(v))
            symbols_layout.addWidget(btn, row, col)
            col += 1
            if col > 6: # 7 columns
                col = 0
                row += 1
        
        # Add a stretch to the last column to push buttons to the left
        symbols_layout.setColumnStretch(7, 1)
        
        editor_layout.addWidget(symbols_group)

        # Columns List
        editor_layout.addWidget(QLabel("Available Properties (Double-click to insert):"))
        self.columns_list = QListWidget()
        self.columns_list.itemDoubleClicked.connect(self.insert_column)
        editor_layout.addWidget(self.columns_list)

        # Action Buttons
        btn_layout = QHBoxLayout()
        self.save_btn = QPushButton("Save Definition")
        self.save_btn.clicked.connect(self.save_definition)
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.clicked.connect(self.clear_editor)
        btn_layout.addWidget(self.save_btn)
        btn_layout.addWidget(self.clear_btn)
        editor_layout.addLayout(btn_layout)

        # --- Right Side: Manager ---
        manager_panel = QWidget()
        manager_layout = QVBoxLayout(manager_panel)
        
        manager_layout.addWidget(QLabel("Defined Custom Properties:"))
        self.properties_list = ClickableListWidget()
        self.properties_list.itemDoubleClicked.connect(self.load_property)
        self.properties_list.itemSelectionChanged.connect(self.on_selection_changed)
        manager_layout.addWidget(self.properties_list)

        manager_btns = QHBoxLayout()
        self.edit_btn = QPushButton("Edit Selected")
        self.edit_btn.clicked.connect(self.load_selected_property)
        self.delete_btn = QPushButton("Delete Selected")
        self.delete_btn.clicked.connect(self.delete_property)
        manager_btns.addWidget(self.edit_btn)
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

    def insert_text(self, text):
        self.formula_input.insertPlainText(text)
        self.formula_input.setFocus()

    def insert_column(self, item):
        # Wrap in curly braces to identify as a variable/column
        text = f"{{{item.text()}}}"
        self.insert_text(text)

    def clear_editor(self):
        self.name_input.clear()
        self.formula_input.clear()

    def load_selected_property(self):
        item = self.properties_list.currentItem()
        if item:
            self.load_property(item)

    def load_property(self, item):
        name = item.data(Qt.ItemDataRole.UserRole)
        if name in self.custom_properties:
            self.name_input.setText(name)
            self.formula_input.setText(self.custom_properties[name])

    def delete_property(self):
        item = self.properties_list.currentItem()
        if not item:
            return
        
        row = self.properties_list.row(item)
        name = item.data(Qt.ItemDataRole.UserRole)
        del self.custom_properties[name]
        self._populate_lists()
        
        # Update the external dict immediately
        if name in self.original_properties:
            del self.original_properties[name]
            
        # Auto-select adjacent item
        count = self.properties_list.count()
        if count > 0:
            if row < count:
                self.properties_list.setCurrentRow(row)
            else:
                self.properties_list.setCurrentRow(count - 1)

    def validate_formula(self, formula):
        """Checks if the formula has valid syntax and uses allowed tokens."""
        
        # 1. Identify all tokens {Token}
        tokens = re.findall(r"\{([^}]+)\}", formula)

        # Check if symbols inside {} are available
        for token in tokens:
            if token not in self.available_columns and token not in self.custom_properties:
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
            "e": np.e,
            "pi": np.pi,
            "var": 1.0 # Dummy value for columns
        }
        
        try:
            # Use eval to catch NameError (unknown variables) and SyntaxError
            eval(clean_formula, safe_globals)
            return True, ""
        except Exception as e:
            return False, str(e)

    def save_definition(self):
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

        # Save
        self.custom_properties[name] = formula
        self.original_properties[name] = formula # Update the reference passed in __init__
        
        self.clear_editor()
        self._populate_lists()
        
        # Auto-select the new item
        for i in range(self.properties_list.count()):
            item = self.properties_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == name:
                self.properties_list.setCurrentItem(item)
                break
        
        return True
        
    def get_properties(self):
        return self.custom_properties

    def get_selected_property(self):
        return self.selected_result

    def accept(self):
        # Implicit save if editor has content
        if self.name_input.text().strip() or self.formula_input.toPlainText().strip():
            if not self.save_definition():
                return
        
        # If an item is selected, we return it as the result
        items = self.properties_list.selectedItems()
        if items:
            self.selected_result = items[0].data(Qt.ItemDataRole.UserRole)
        else:
            self.selected_result = None
        super().accept()