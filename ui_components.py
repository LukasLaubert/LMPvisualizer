# lmp_visualizer/ui_components.py

from PyQt6.QtWidgets import (QDialog, QPushButton, QVBoxLayout, QTableWidget,
                             QDialogButtonBox, QHeaderView, QTableWidgetItem,
                             QCheckBox, QSpinBox, QLabel, QFormLayout, QColorDialog,
                             QWidget, QHBoxLayout, QLineEdit, QFrame, QApplication, 
                             QStyledItemDelegate, QComboBox, QSizePolicy)
from PyQt6.QtGui import QColor, QPalette, QFontMetrics, QFont
from PyQt6.QtCore import pyqtSignal, Qt, QEvent

class NeutralPanel(QWidget):
    """
    The Neutral 'Boot' Panel displayed when no mode is selected.
    Allows user to choose mode and toggle session loading.
    """
    modeSelected = pyqtSignal(str) # 'log' or 'trj'
    autoloadToggled = pyqtSignal(bool) # True = Autoload On (Checkbox Unchecked)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(30)

        # Title
        title = QLabel("Boot Mode Selection")
        font = title.font()
        font.setPointSize(16)
        font.setBold(True)
        title.setFont(font)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        # Buttons Container
        btn_container = QWidget()
        btn_layout = QHBoxLayout(btn_container)
        btn_layout.setSpacing(20)

        # Log Plot Button
        self.btn_log = QPushButton("Log Plot")
        self.btn_log.setMinimumSize(150, 100)
        self.btn_log.setStyleSheet("font-size: 14px; font-weight: bold;")
        self.btn_log.clicked.connect(lambda: self.modeSelected.emit('log'))
        btn_layout.addWidget(self.btn_log)

        # Trj Plot Button
        self.btn_trj = QPushButton("Trajectory Plot")
        self.btn_trj.setMinimumSize(150, 100)
        self.btn_trj.setStyleSheet("font-size: 14px; font-weight: bold;")
        self.btn_trj.clicked.connect(lambda: self.modeSelected.emit('trj'))
        btn_layout.addWidget(self.btn_trj)

        # DSD Mode Button
        self.btn_dsd = QPushButton("DSD Mode")
        self.btn_dsd.setMinimumSize(150, 100)
        self.btn_dsd.setStyleSheet("font-size: 14px; font-weight: bold;")
        self.btn_dsd.clicked.connect(lambda: self.modeSelected.emit('dsd'))
        btn_layout.addWidget(self.btn_dsd)

        layout.addWidget(btn_container)

        # Autoload Checkbox (Logic inverted: Checkbox = Disable loading)
        self.chk_disable_load = QCheckBox("Disable session file loading")
        self.chk_disable_load.setStyleSheet("font-size: 12px;")
        self.chk_disable_load.toggled.connect(self._on_check_toggled)
        layout.addWidget(self.chk_disable_load, alignment=Qt.AlignmentFlag.AlignCenter)

    def _on_check_toggled(self, checked):
        # If checked (Disable), autoload is False
        # If unchecked (Enable), autoload is True
        self.autoloadToggled.emit(not checked)

    def set_autoload_state(self, enabled):
        # If enabled is True, checkbox should be Unchecked
        self.chk_disable_load.setChecked(not enabled)

# --- Existing Components ---

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
        self.setStyleSheet("")
        
        dialog = QColorDialog(self)
        dialog.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog, True)
        dialog.setCurrentColor(self._color)
        
        if dialog.exec():
            new_color = dialog.selectedColor()
            if new_color.isValid():
                self.set_color(new_color)
            else:
                self.setStyleSheet(original_style)
        else:
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
        
        form_layout = QFormLayout()
        self.truncate_box = QSpinBox()
        self.truncate_box.setRange(1, min_len)
        self.truncate_box.setValue(min_len)
        form_layout.addRow("Truncate all data to length:", self.truncate_box)
        layout.addLayout(form_layout)
        
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def get_choices(self) -> dict:
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

        self.add_chip(".log")
        self.add_chip(".out")

    def eventFilter(self, source, event):
        if source is self.input_line and event.type() == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
                if self.input_line.text().strip():
                    self.add_chip_from_input()
                    if event.key() == Qt.Key.Key_Backtab:
                        return False 
                    return True
        return super().eventFilter(source, event)

    def add_chip(self, text: str):
        text = text.strip()
        if not text or ' ' in text or text in self._chips:
            return

        self._chips.append(text)
        chip_widget = Chip(text)
        chip_widget.removed.connect(self.remove_chip)
        self.chip_layout.addWidget(chip_widget)
        self.chipsChanged.emit(self._chips)

    def add_chip_from_input(self):
        text = self.input_line.text().strip()
        self.add_chip(text)
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

    def set_chips(self, chips: list):
        while self._chips:
            self.remove_chip(self._chips[0])
        for chip in chips:
            self.add_chip(chip)

class DraggableTableWidget(QTableWidget):
    """A QTableWidget that supports drag and drop reordering of rows."""
    rowsReordered = pyqtSignal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setDragDropMode(QTableWidget.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setAlternatingRowColors(True)
        self.verticalHeader().setVisible(False)

    def dropEvent(self, event):
        if event.source() is self and event.dropAction() == Qt.DropAction.MoveAction:
            drop_position = event.position().toPoint()
            target_row = self.indexAt(drop_position).row()

            if target_row == -1:
                row_height = self.rowHeight(0) if self.rowCount() > 0 else 25
                header_height = self.horizontalHeader().height()
                relative_y = drop_position.y() - header_height
                target_row = max(0, min(self.rowCount(), int(relative_y // row_height)))

            source_row = self.currentRow()

            if source_row != target_row and source_row != -1:
                if source_row < target_row:
                    target_row -= 1

                source_items = []
                source_widgets = {}

                for col in range(self.columnCount()):
                    item = self.item(source_row, col)
                    source_items.append(item.clone() if item else None)
                    widget = self.cellWidget(source_row, col)
                    if widget:
                        source_widgets[col] = widget

                self.removeRow(source_row)

                if source_row < target_row:
                    target_row -= 1

                self.insertRow(target_row)

                for col, item in enumerate(source_items):
                    if item:
                        self.setItem(target_row, col, item)

                for col, widget in source_widgets.items():
                    # Critical: Reparent widget to avoid C++ deletion issues
                    widget.setParent(None)
                    self.setCellWidget(target_row, col, widget)

                self.selectRow(target_row)
                self.rowsReordered.emit()
                event.acceptProposedAction()
                return

        super().dropEvent(event)

class RightClickButton(QPushButton):
    rightClicked = pyqtSignal()

    def mousePressEvent(self, event: QEvent):
        if event.type() == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.RightButton:
                self.rightClicked.emit()
            else:
                super().mousePressEvent(event)

class NoNewLineDelegate(QStyledItemDelegate):
    """
    1. Replaces newlines with spaces in the collapsed view.
    2. Ensures the dropdown popup is wide enough to show the full text of items.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self._parent_combo = parent

    def displayText(self, value, locale):
        text = super().displayText(value, locale)
        return text.replace('\n', ' ')

    def sizeHint(self, option, index):
        # Calculate the width required for the full text
        text = index.data(Qt.ItemDataRole.DisplayRole)
        if not text:
            return super().sizeHint(option, index)
            
        font_metrics = option.fontMetrics
        width = font_metrics.horizontalAdvance(text) + 20  # Padding
        
        # If attached to a combobox, ensure the popup is wide enough
        if self._parent_combo and isinstance(self._parent_combo, QComboBox):
            # We can't set popup width directly via delegate, 
            # but we can update the view's minimum width if needed.
            view = self._parent_combo.view()
            if view and view.minimumWidth() < width:
                view.setMinimumWidth(width)
                
        return super().sizeHint(option, index)