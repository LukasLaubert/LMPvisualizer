# lmp_visualizer/ui_components.py

from PyQt6.QtWidgets import (QDialog, QPushButton, QVBoxLayout, QTableWidget, 
                             QDialogButtonBox, QHeaderView, QTableWidgetItem, 
                             QCheckBox, QSpinBox, QLabel, QFormLayout, QColorDialog,
                             QWidget, QHBoxLayout, QLineEdit, QFrame)
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtCore import pyqtSignal, Qt, QEvent

class ColorButton(QPushButton):
    """A button that displays a color and opens a color dialog on click."""
    colorChanged = pyqtSignal(QColor)

    def __init__(self, color=QColor('white'), *args, **kwargs):
        super(ColorButton, self).__init__(*args, **kwargs)
        self._color = color
        self.set_color(self._color)
        self.clicked.connect(self.on_click)

    def set_color(self, color: QColor):
        self._color = color
        self.setStyleSheet(f"background-color: {self._color.name()};")
        self.colorChanged.emit(self._color)

    def color(self) -> QColor:
        return self._color

    def on_click(self):
        # Save the original style to restore if the dialog is cancelled
        original_style = self.styleSheet()
        
        # Temporarily clear style to prevent inheritance
        self.setStyleSheet("")
        
        dialog = QColorDialog(self)
        dialog.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog, True)
        dialog.setCurrentColor(self._color)
        
        if dialog.exec():
            new_color = dialog.selectedColor()
            if new_color.isValid():
                self.set_color(new_color)  # This sets the new color and style
            else:
                # If dialog was cancelled, restore the original color's style
                self.setStyleSheet(f"background-color: {self._color.name()};")
        else:
            # If dialog was cancelled, restore the original color's style
            self.setStyleSheet(f"background-color: {self._color.name()};")

class InconsistentDataDialog(QDialog):
    """Dialog to resolve averaging of data with different lengths."""
    def __init__(self, lengths: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Inconsistent Data Lengths")
        
        self.lengths = lengths
        min_len = min(lengths.values())
        
        layout = QVBoxLayout(self)
        
        layout.addWidget(QLabel(
            "The systems in this study have different numbers of data points.\n"
            "Please choose how to proceed with averaging."
        ))
        
        # Table showing lengths
        self.table = QTableWidget(len(lengths), 3)
        self.table.setHorizontalHeaderLabels(["System", "Timesteps", "Exclude"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        
        for i, (system, length) in enumerate(lengths.items()):
            self.table.setItem(i, 0, QTableWidgetItem(system))
            self.table.setItem(i, 1, QTableWidgetItem(str(length)))
            checkbox = QCheckBox()
            self.table.setCellWidget(i, 2, checkbox)
        
        layout.addWidget(self.table)
        
        # Truncation option
        form_layout = QFormLayout()
        self.truncate_box = QSpinBox()
        self.truncate_box.setRange(1, min_len)
        self.truncate_box.setValue(min_len)
        form_layout.addRow("Truncate all data to length:", self.truncate_box)
        layout.addLayout(form_layout)
        
        # Dialog buttons
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def get_choices(self) -> dict:
        """Returns the user's choices."""
        excluded = []
        for i in range(self.table.rowCount()):
            if self.table.cellWidget(i, 2).isChecked():
                excluded.append(self.table.item(i, 0).text())
        
        return {
            'truncate_len': self.truncate_box.value(),
            'exclude': excluded
        }

class Chip(QFrame):
    """A single chip widget with a label and a delete button."""
    removed = pyqtSignal(str)

    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.text = text
        self.setStyleSheet("""
            QFrame {
                background-color: #e1e1e1;
                border-radius: 8px;
                padding: 1px 4px;
                margin: 1px;
            }
            QPushButton {
                background-color: transparent;
                border: none;
                font-weight: bold;
                color: #555;
            }
            QPushButton:hover {
                color: black;
            }
        """)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 1, 4, 1)
        layout.setSpacing(4)

        self.label = QLabel(text)
        layout.addWidget(self.label)

        remove_button = QPushButton("x")
        remove_button.setFixedSize(14, 14)
        remove_button.clicked.connect(self.on_remove)
        layout.addWidget(remove_button)

    def on_remove(self):
        self.removed.emit(self.text)
        self.deleteLater()

    def set_bold(self, bold: bool):
        font = self.label.font()
        font.setBold(bold)
        self.label.setFont(font)

class ChipInputWidget(QWidget):
    """A widget for inputting text that becomes 'chips'."""
    chipsChanged = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._chips = []

        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)

        self.input_line = QLineEdit()
        self.input_line.setPlaceholderText("Add log file keywords...")
        self.input_line.installEventFilter(self)
        self.input_line.returnPressed.connect(self.add_chip_from_input)

        self.chip_container = QWidget()
        self.chip_layout = QHBoxLayout(self.chip_container)
        self.chip_layout.setContentsMargins(0,0,0,0)
        self.chip_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)

        self.layout.addWidget(self.chip_container)
        self.layout.addWidget(self.input_line)
        self.layout.setStretchFactor(self.input_line, 1)


    def eventFilter(self, source, event):
        if source is self.input_line and event.type() == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Tab):
                if self.input_line.text().strip():
                    self.add_chip_from_input()
                    return True # Event handled
        return super().eventFilter(source, event)

    def add_chip_from_input(self):
        text = self.input_line.text().strip()
        if not text or ' ' in text or text in self._chips:
            self.input_line.clear()
            return

        self._chips.append(text)
        chip_widget = Chip(text)
        chip_widget.removed.connect(self.remove_chip)
        self.chip_layout.addWidget(chip_widget)
        self.chipsChanged.emit(self._chips)
        self.input_line.clear()

    def remove_chip(self, text):
        if text in self._chips:
            self._chips.remove(text)
            for i in range(self.chip_layout.count()):
                widget = self.chip_layout.itemAt(i).widget()
                if isinstance(widget, Chip) and widget.text == text:
                    widget.deleteLater()
                    break
            self.chipsChanged.emit(self._chips)

    def get_chips(self) -> list:
        return self._chips.copy()

    def update_chip_styles(self, successful_keywords: set):
        for i in range(self.chip_layout.count()):
            widget = self.chip_layout.itemAt(i).widget()
            if isinstance(widget, Chip):
                widget.set_bold(widget.text in successful_keywords)
