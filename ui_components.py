# lmp_visualizer/ui_components.py

from PyQt6.QtWidgets import (QDialog, QPushButton, QVBoxLayout, QTableWidget,
                             QDialogButtonBox, QHeaderView, QTableWidgetItem,
                             QCheckBox, QSpinBox, QLabel, QFormLayout, QColorDialog,
                              QWidget, QHBoxLayout, QLineEdit, QFrame, QApplication, QToolTip,
                             QStyledItemDelegate, QComboBox, QSizePolicy, QMessageBox, QFileDialog,
                             QStyleOptionButton, QStyle)
from PyQt6.QtGui import QColor, QPalette, QFontMetrics, QFont, QPainter
from PyQt6.QtCore import pyqtSignal, Qt, QEvent, QTimer

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

        self.lengths = dict(lengths)
        min_len = min(self.lengths.values()) if self.lengths else 1

        layout = QVBoxLayout(self)
        layout.setSizeConstraint(QVBoxLayout.SizeConstraint.SetMinAndMaxSize)

        lbl = QLabel(
            "The systems in this study have different numbers of data points.\n"
            "Please choose how to proceed with averaging."
        )
        lbl.setWordWrap(True)
        layout.addWidget(lbl)

        self.table = QTableWidget(len(self.lengths), 3)
        self.table.setHorizontalHeaderLabels(["System", "Timesteps", "Include"])
        header = self.table.horizontalHeader()
        # Every column is only as wide as its header/content needs — window
        # scales with the table instead of truncating system names.
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(False)
        self.table.verticalHeader().hide()
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.setSizeAdjustPolicy(QTableWidget.SizeAdjustPolicy.AdjustToContents)
        self.table.horizontalHeader().setHighlightSections(False)
        # Include checkboxes centered, Timesteps numbers right-aligned
        self.table.horizontalHeaderItem(1).setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.table.horizontalHeaderItem(2).setTextAlignment(Qt.AlignmentFlag.AlignCenter)

        for i, (system, length) in enumerate(self.lengths.items()):
            sys_item = QTableWidgetItem(system)
            sys_item.setFlags(sys_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(i, 0, sys_item)

            num_item = QTableWidgetItem(str(length))
            num_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            num_item.setFlags(num_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(i, 1, num_item)

            checkbox = QCheckBox()
            checkbox.setChecked(True)
            checkbox.setStyleSheet("QCheckBox { spacing: 0px; }")
            container = QWidget()
            c_layout = QHBoxLayout(container)
            c_layout.setContentsMargins(0, 0, 0, 0)
            c_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            c_layout.addWidget(checkbox)
            self.table.setCellWidget(i, 2, container)
            checkbox.toggled.connect(self._update_truncate_range)

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

        self.table.resizeColumnsToContents()
        if self.table.columnWidth(2) < 90:
            self.table.setColumnWidth(2, 90)
        else:
            self.table.setColumnWidth(2, self.table.columnWidth(2) + 12)
        self.table.resizeRowsToContents()
        content_w = self.table.horizontalHeader().length()
        self.adjustSize()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(2, max(self.table.columnWidth(2), 90))
        # Minimal width = width needed for the first label line
        # ("The systems in this study have different numbers of data points.")
        # instead of a hard-coded 560, so the dialog is not wider than necessary.
        fm = QFontMetrics(lbl.font())
        label_needed = fm.horizontalAdvance("The systems in this study have different numbers of data points.") + 50
        # Also ensure second line fits if it is longer
        label_needed = max(label_needed, fm.horizontalAdvance("Please choose how to proceed with averaging.") + 50)
        # Table content may still be wider (long system names)
        min_w = max(content_w + 40, label_needed)
        self.setMinimumWidth(min_w)
        # Start at the minimal width — columns fill it, no empty right gap
        self.resize(min_w, self.height())
        self.setSizeGripEnabled(True)

    def _included_lengths(self):
        included = []
        for i, (system, length) in enumerate(self.lengths.items()):
            cont = self.table.cellWidget(i, 2)
            cb = cont.findChild(QCheckBox) if cont else None
            if cb is not None and cb.isChecked():
                included.append(length)
        return included

    def _update_truncate_range(self):
        included = self._included_lengths()
        if not included:
            new_max = max(self.lengths.values()) if self.lengths else 1
        else:
            new_max = min(included)
        self.truncate_box.blockSignals(True)
        self.truncate_box.setRange(1, new_max)
        # Always jump to the max of the currently included systems (user
        # expects the field to reflect the new limit immediately).
        self.truncate_box.setValue(new_max)
        self.truncate_box.blockSignals(False)

    def get_choices(self) -> dict:
        excluded = []
        for i in range(self.table.rowCount()):
            cont = self.table.cellWidget(i, 2)
            cb = cont.findChild(QCheckBox) if cont else None
            # Include = checked, so unchecked -> excluded
            if cb is not None and not cb.isChecked():
                item = self.table.item(i, 0)
                if item:
                    excluded.append(item.text())

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
        # Teardown belongs to the owner (remove_chip); deleting here as well left the
        # widget in the layout until the next DeferredDelete, holding its space.
        self.removed.emit(self.text)

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
                    # setParent(None) first: deleteLater alone leaves the widget in the
                    # layout - and holding its width - until the event loop gets round
                    # to it, which pushed the input field to the right.
                    widget.setParent(None)
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

class PathChip(QFrame):
    """A chip holding one project path. The label is elided, the tooltip is not."""
    removed = pyqtSignal(str)

    # Everything the chip needs beside its text: layout margins (4+4), spacing (4),
    # remove button (14) and the stylesheet's own padding (4+4) and margin (1+1).
    CHROME = 36
    # A couple of pixels the label keeps to itself, so the last glyph of the path can
    # never be shaved off by a rounding difference between metrics and rendering.
    TEXT_PAD = 4

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self.path = path
        # QLabel derives from QFrame, so a bare "QFrame { padding: 1px 4px; margin: 1px }"
        # applies to the label too and shrinks its text box by ~22px. With the label
        # width capped to the space left over by the x, that inset used to clip the tail
        # of the path - the part worth reading - right where the x sits. Reset it.
        self.setStyleSheet("""
            QFrame {
                background-color: #d8e4f0;
                border-radius: 8px;
                padding: 1px 4px;
                margin: 1px;
            }
            QLabel {
                background-color: transparent;
                border: none;
                border-radius: 0px;
                padding: 0px;
                margin: 0px;
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
        self.setToolTip(path)

        # Same skeleton as the keyword chips: label, spacing, x. Nothing between them,
        # so the x always follows the text at a constant distance.
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 1, 4, 1)
        layout.setSpacing(4)

        self.label = QLabel(path)
        layout.addWidget(self.label)

        remove_button = QPushButton("x")
        remove_button.setFixedSize(14, 14)
        remove_button.setToolTip("Remove this path")
        remove_button.clicked.connect(self.on_remove)
        layout.addWidget(remove_button)

        self.set_available_width(self.natural_width())

    def natural_width(self) -> int:
        """Width at which the whole path is readable."""
        advance = QFontMetrics(self.label.font()).horizontalAdvance(self.path)
        return advance + self.TEXT_PAD + self.CHROME

    def set_available_width(self, px: int):
        """Shows as much of the path tail as fits in px, eliding from the left."""
        px = max(int(px), self.CHROME + self.TEXT_PAD + 20)
        text_px = px - self.CHROME - self.TEXT_PAD
        fm = QFontMetrics(self.label.font())
        shown = fm.elidedText(self.path, Qt.TextElideMode.ElideLeft, text_px)
        self.label.setText(shown)
        # Pin the label to exactly what its text needs. A label that is merely capped
        # can still be handed less than that by a squeezed layout, and QLabel does not
        # re-elide - it clips, which is what put the end of the path under the x.
        self.label.setFixedWidth(min(fm.horizontalAdvance(shown) + self.TEXT_PAD, text_px))
        self.setMaximumWidth(px)

    def on_remove(self):
        self.removed.emit(self.path)

    def mouseReleaseEvent(self, event):
        # Right-click copies the full path straight to the clipboard - no menu.
        # On release: a tooltip shown during the press is hidden again by Qt
        # the moment the button state changes.
        if event.button() == Qt.MouseButton.RightButton:
            QApplication.clipboard().setText(self.path)
            QToolTip.showText(event.globalPosition().toPoint(),
                              f"Path copied to clipboard:\n{self.path}", self)
            QTimer.singleShot(3000, QToolTip.hideText)
            return
        super().mouseReleaseEvent(event)


class PathChipInputWidget(QWidget):
    """Project paths as chips, plus a line edit for typing or pasting a new one.

    Mirrors ChipInputWidget's interaction model so the two rows of the top bar behave
    the same way. Committed text is only announced - the owner decides whether it is a
    project path or a session file.
    """
    pathsChanged = pyqtSignal(list)
    submitted = pyqtSignal(str)

    MIN_INPUT_WIDTH = 140
    MIN_CHIP_WIDTH = 70
    MAX_CHIP_SHARE = 2.0 / 3.0  # chips together never take more than this much room

    def __init__(self, parent=None):
        super().__init__(parent)
        self._paths = []

        # Same skeleton as ChipInputWidget so both rows of the top bar start at the
        # same x and behave identically.
        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)

        self.chip_container = QWidget()
        self.chip_layout = QHBoxLayout(self.chip_container)
        self.chip_layout.setContentsMargins(0, 0, 0, 0)
        self.chip_layout.setSpacing(2)
        self.chip_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)

        self.input_line = QLineEdit()
        self.input_line.setPlaceholderText("Paste a path or select a project root directory...")
        self.input_line.setMinimumWidth(self.MIN_INPUT_WIDTH)
        # editingFinished already covers Return; also connecting returnPressed would
        # submit the same text twice.
        self.input_line.editingFinished.connect(self._on_submit)

        self.layout.addWidget(self.chip_container)
        self.layout.addWidget(self.input_line)
        self.layout.setStretchFactor(self.input_line, 1)

    # --- interaction ---------------------------------------------------------

    def _on_submit(self):
        text = self.input_line.text().strip().strip('"')
        if text:
            self.submitted.emit(text)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout_chips()

    def showEvent(self, event):
        # Chips added before the first layout pass were sized against a stale width.
        super().showEvent(event)
        self._relayout_chips()

    def _relayout_chips(self):
        """Hands every chip its share of the width; each elides itself to fit."""
        chips = self._chip_widgets()
        if not chips:
            return

        spacing = self.chip_layout.spacing() * max(len(chips) - 1, 0)
        # Two limits, whichever bites first: leave the type-in field its minimum, and
        # never let the chips take more than MAX_CHIP_SHARE of the whole row.
        budget = min(self.width() - self.MIN_INPUT_WIDTH,
                     int(self.width() * self.MAX_CHIP_SHARE)) - self.layout.spacing() - spacing
        share = max(budget // len(chips), self.MIN_CHIP_WIDTH)

        for chip in chips:
            chip.set_available_width(min(share, chip.natural_width()))

    def _schedule_relayout(self):
        """Sizes the chips now, then again once the layout has settled.

        A chip added before the widget has its final width would otherwise keep the
        minimum size it was given at that moment.
        """
        self._relayout_chips()
        QTimer.singleShot(0, self._relayout_chips)

    def _chip_widgets(self):
        return [self.chip_layout.itemAt(i).widget() for i in range(self.chip_layout.count())
                if isinstance(self.chip_layout.itemAt(i).widget(), PathChip)]

    # --- public API ----------------------------------------------------------

    def add_path(self, path: str, silent: bool = False) -> bool:
        # One spelling per path, so the chip set, discovery's origins and the paths
        # stored on the table rows compare equal - and so "D:/proj" and "D:\proj"
        # cannot end up as two chips over the same project.
        from log_parser import LogParser
        path = LogParser.canonical_path(path)
        if not path or any(LogParser.same_path(path, p) for p in self._paths):
            return False

        self._paths.append(path)
        chip = PathChip(path)
        chip.removed.connect(self.remove_path)
        self.chip_layout.addWidget(chip)
        self._schedule_relayout()
        if not silent:
            self.pathsChanged.emit(self.get_paths())
        return True

    def remove_path(self, path: str, silent: bool = False):
        from log_parser import LogParser
        path = next((p for p in self._paths if LogParser.same_path(p, path)), None)
        if path is None:
            return
        self._paths.remove(path)
        for chip in self._chip_widgets():
            if chip.path == path:
                chip.setParent(None)
                chip.deleteLater()
                break
        self._schedule_relayout()
        if not silent:
            self.pathsChanged.emit(self.get_paths())

    def get_paths(self) -> list:
        return self._paths.copy()

    def set_paths(self, paths: list, silent: bool = False):
        """Replaces the whole chip set in one go (one signal, not one per chip)."""
        for path in list(self._paths):
            self.remove_path(path, silent=True)
        for path in paths or []:
            self.add_path(path, silent=True)
        if not silent:
            self.pathsChanged.emit(self.get_paths())

    def clear_input(self):
        self.input_line.blockSignals(True)
        self.input_line.clear()
        self.input_line.blockSignals(False)

    def setEnabled(self, enabled: bool):
        super().setEnabled(enabled)
        self.input_line.setEnabled(enabled)


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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._checked_background_color = None

    def set_checked_background_color(self, color):
        self._checked_background_color = QColor(color) if color else None
        self.update()

    def paintEvent(self, event):
        if self._checked_background_color is not None and self.isChecked():
            option = QStyleOptionButton()
            self.initStyleOption(option)
            for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
                option.palette.setColor(group, QPalette.ColorRole.Button, self._checked_background_color)
                option.palette.setColor(group, QPalette.ColorRole.Highlight, self._checked_background_color)
            painter = QPainter(self)
            self.style().drawControl(QStyle.ControlElement.CE_PushButton, option, painter, self)
            return
        super().paintEvent(event)

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

class MissingPathDialog(QDialog):
    def __init__(self, parent, project_path):
        super().__init__(parent)
        self.setWindowTitle("Project Path Missing")
        self.setFixedWidth(500)
        
        layout = QVBoxLayout(self)
        layout.setSpacing(15)
        
        # Icon and Message
        msg_layout = QHBoxLayout()
        icon_label = QLabel()
        icon_style = self.style().standardIcon(self.style().StandardPixmap.SP_MessageBoxWarning)
        icon_label.setPixmap(icon_style.pixmap(32, 32))
        icon_label.setAlignment(Qt.AlignmentFlag.AlignTop)
        msg_layout.addWidget(icon_label)
        
        text_label = QLabel(f"The project path specified in the session file does not exist:\n{project_path}")
        text_label.setWordWrap(True)
        msg_layout.addWidget(text_label, 1)
        layout.addLayout(msg_layout)
        
        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)
        
        self.btn_relocate = QPushButton("Relocate Project")
        self.btn_relocate.setToolTip("Choose the new location of this project and restore the session.")
        self.btn_relocate.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        
        self.btn_change = QPushButton("Change Project")
        self.btn_change.setToolTip("Pick a completely different project and clear the session data.")
        self.btn_change.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        
        self.btn_fresh = QPushButton("Discard & Reset")
        self.btn_fresh.setToolTip("Cancel loading and reset the GUI to a blank state.")
        self.btn_fresh.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        
        btn_layout.addWidget(self.btn_relocate)
        btn_layout.addWidget(self.btn_change)
        btn_layout.addWidget(self.btn_fresh)
        layout.addLayout(btn_layout)
        
        self.result_action = "cancel"
        
        self.btn_relocate.clicked.connect(lambda: self.finish("relocate"))
        self.btn_change.clicked.connect(lambda: self.finish("change"))
        self.btn_fresh.clicked.connect(lambda: self.finish("reset"))

    def finish(self, action):
        self.result_action = action
        self.accept()

class MissingPathResolver:
    @staticmethod
    def resolve(parent, project_path):
        """
        Handles missing project path and returns a tuple (action, new_path).
        Actions: 'relocate', 'change', 'reset', 'cancel'
        """
        dialog = MissingPathDialog(parent, project_path)
        if dialog.exec():
            action = dialog.result_action
            if action == 'relocate':
                new_path = QFileDialog.getExistingDirectory(parent, "Relocate Project Folder", "")
                return ("relocate", new_path) if new_path else ("cancel", None)
            elif action == 'change':
                new_path = QFileDialog.getExistingDirectory(parent, "Select New Project Folder", "")
                return ("change", new_path) if new_path else ("cancel", None)
            return (action, None)
        return ("cancel", None)