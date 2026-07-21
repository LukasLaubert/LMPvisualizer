# lmp_visualizer/ui_components.py

from PyQt6.QtWidgets import (QDialog, QPushButton, QVBoxLayout, QTableWidget, 
                             QDialogButtonBox, QHeaderView, QTableWidgetItem, 
                             QCheckBox, QSpinBox, QLabel, QFormLayout, QColorDialog)
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtCore import pyqtSignal

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
        original_style = self.styleSheet()
        try:
            self.setStyleSheet("") # Temporarily clear style to prevent inheritance
            
            dialog = QColorDialog(self)
            dialog.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog, True)
            dialog.setCurrentColor(self._color)
            
            if dialog.exec():
                new_color = dialog.selectedColor()
                if new_color.isValid():
                    self.set_color(new_color)
        finally:
            # Always restore the original style
            self.setStyleSheet(original_style)

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