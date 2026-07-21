from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QCheckBox, QLabel, QDialog, 
    QFormLayout, QComboBox, QSpinBox, QDoubleSpinBox, QDialogButtonBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QMenu,
    QPushButton, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QStyledItemDelegate, QListView, QFrame, QScrollArea, QTextEdit,
    QGroupBox, QToolButton, QSizePolicy, QGridLayout
)
from PyQt6.QtCore import Qt, pyqtSignal, QPoint, QEvent, QRect, QSize, QRectF, QLocale
from PyQt6.QtGui import (
    QColor, QIntValidator, QDoubleValidator, QStandardItemModel, QStandardItem, 
    QMouseEvent, QPalette, QPainter, QBrush, QPen, QFont, QLinearGradient
)
from ui_components import ColorButton
import random
import bisect
import numpy as np

class HorizontalFilterBarWidget(QWidget):
    """
    Horizontal version of FilterBarWidget for managing domain splits.
    Shows handle positions as text below handles. Allows clicking text to edit.
    """
    rangesChanged = pyqtSignal(list)
    valueChanged = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(60)
        self.data_min = 0.0
        self.data_max = 100.0
        
        # We store splits and states
        self.splits = [] # handle positions
        self.active_segments = [True] # length = len(splits) + 1
        
        self.dragging_handle_idx = -1
        self.hover_handle_idx = -1
        self.label_rects = [] # Store (rect, index) for hit testing labels
        self.editor = None # Reference to active editor
        
        self.setMouseTracking(True)

    def set_range(self, dmin, dmax):
        if dmin >= dmax: dmax = dmin + 1.0
        self.data_min = dmin
        self.data_max = dmax
        self.update()

    def set_data(self, splits, states):
        self.splits = sorted([float(s) for s in splits])
        num_segs = len(self.splits) + 1
        if len(states) != num_segs:
            new_states = states[:num_segs]
            if len(new_states) < num_segs:
                new_states.extend([True] * (num_segs - len(new_states)))
            self.active_segments = new_states
        else:
            self.active_segments = states
        self.update()

    def get_data(self):
        return self.splits, self.active_segments

    def val_to_x(self, val, rect):
        if self.data_max == self.data_min: return rect.left()
        rel = (val - self.data_min) / (self.data_max - self.data_min)
        return rect.left() + rel * rect.width()

    def x_to_val(self, x, rect):
        if rect.width() == 0: return self.data_min
        rel = (x - rect.left()) / rect.width()
        return self.data_min + rel * (self.data_max - self.data_min)

    def _get_bar_rect(self):
        # Tighter layout: Bar starts at y=10, height 18
        return QRect(15, 10, self.width() - 30, 18)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self._get_bar_rect()
        
        # Background
        painter.setBrush(QColor(220, 220, 220))
        painter.setPen(QPen(Qt.GlobalColor.black, 1))
        painter.drawRect(rect)
        
        # Draw Segments
        curr_x = rect.left()
        active_idx = 1
        for i in range(len(self.active_segments)):
            if i < len(self.splits):
                next_x = self.val_to_x(self.splits[i], rect)
            else:
                next_x = rect.right()
                
            seg_rect = QRectF(curr_x, rect.top(), next_x - curr_x, rect.height())
            is_active = self.active_segments[i]
            
            if is_active:
                painter.setBrush(QColor(100, 200, 100, 150)) # Translucent Green
            else:
                painter.setBrush(QColor(150, 150, 150, 150))
            
            painter.drawRect(seg_rect)
            
            # Draw Segment Index only for green segments
            if is_active and seg_rect.width() > 10:
                painter.setPen(Qt.GlobalColor.black)
                font = painter.font()
                font.setBold(True)
                painter.setFont(font)
                painter.drawText(seg_rect, Qt.AlignmentFlag.AlignCenter, str(active_idx))
                active_idx += 1
            
            curr_x = next_x
            
        # Draw Handles and Positions
        handle_w = 10
        # Handle top relative to bar
        handle_top = rect.top() - 4
        handle_h = rect.height() + 8
        
        painter.setFont(QFont("Segoe UI", 8))
        self.label_rects = [] # Reset hit targets
        
        for i, s in enumerate(self.splits):
            hx = self.val_to_x(s, rect)
            
            # Handle Color
            if i == self.dragging_handle_idx: color = QColor(255, 150, 50)
            elif i == self.hover_handle_idx: color = QColor(100, 180, 255)
            else: color = QColor(80, 80, 80)
            
            # Draw Handle
            painter.setBrush(color)
            painter.setPen(QPen(Qt.GlobalColor.black, 1))
            h_rect = QRectF(hx - handle_w/2, handle_top, handle_w, handle_h)
            painter.drawRoundedRect(h_rect, 2, 2)
            
            # Text Label
            painter.setPen(Qt.GlobalColor.black)
            txt = f"{s:.2f}"
            fm = painter.fontMetrics()
            t_width = fm.horizontalAdvance(txt)
            t_height = fm.height()
            
            # Center text below handle
            # Y position: bottom of bar + 2px spacing + text height
            text_x = int(hx - t_width/2)
            text_y = int(rect.bottom() + 4)
            
            # Store rect for clicking
            l_rect = QRect(text_x - 2, text_y, t_width + 4, t_height + 4)
            self.label_rects.append((l_rect, i))
            
            # Draw background if hovering
            if i == self.hover_handle_idx or i == self.dragging_handle_idx:
                painter.fillRect(l_rect, QColor(255, 255, 200))
                painter.setPen(Qt.GlobalColor.blue)
            
            painter.drawText(l_rect, Qt.AlignmentFlag.AlignCenter, txt)

    def mousePressEvent(self, event: QMouseEvent):
        if self.editor: 
            self._finish_edit()

        rect = self._get_bar_rect()
        val = self.x_to_val(event.pos().x(), rect)
        
        # 1. Check Label Clicks (Edit Mode)
        for l_rect, idx in self.label_rects:
            if l_rect.contains(event.pos()):
                self._show_value_editor(idx, l_rect)
                return

        # 2. Check Handle Clicks (Drag/Remove)
        for i, s in enumerate(self.splits):
            hx = self.val_to_x(s, rect)
            # Hit test +/- 6px
            if abs(event.pos().x() - hx) < 6:
                if event.button() == Qt.MouseButton.LeftButton:
                    self.dragging_handle_idx = i
                    self.update()
                    return
                elif event.button() == Qt.MouseButton.RightButton:
                    self.splits.pop(i)
                    self.active_segments.pop(i) 
                    self.valueChanged.emit()
                    self.update()
                    return

        # 3. Bar Interaction
        if rect.contains(event.pos()):
            if event.button() == Qt.MouseButton.LeftButton:
                bisect.insort(self.splits, val)
                idx = self.splits.index(val)
                self.active_segments.insert(idx, self.active_segments[idx])
                self.valueChanged.emit()
                self.update()
            elif event.button() == Qt.MouseButton.RightButton:
                idx = 0
                for s in self.splits:
                    if val < s: break
                    idx += 1
                if idx < len(self.active_segments):
                    self.active_segments[idx] = not self.active_segments[idx]
                    self.valueChanged.emit()
                    self.update()

    def mouseMoveEvent(self, event: QMouseEvent):
        rect = self._get_bar_rect()
        if self.dragging_handle_idx != -1:
            val = self.x_to_val(event.pos().x(), rect)
            val = max(self.data_min, min(self.data_max, val))
            
            self.splits[self.dragging_handle_idx] = val
            self.splits.sort()
            try:
                self.dragging_handle_idx = self.splits.index(val)
            except ValueError:
                pass
            
            self.valueChanged.emit()
            self.update()
        else:
            old_h = self.hover_handle_idx
            self.hover_handle_idx = -1
            
            # Check handles
            for i, s in enumerate(self.splits):
                hx = self.val_to_x(s, rect)
                if abs(event.pos().x() - hx) < 6:
                    self.hover_handle_idx = i
                    break
            
            # Check labels
            if self.hover_handle_idx == -1:
                for l_rect, idx in self.label_rects:
                    if l_rect.contains(event.pos()):
                        self.hover_handle_idx = idx
                        break

            if old_h != self.hover_handle_idx:
                self.update()

    def mouseReleaseEvent(self, event):
        self.dragging_handle_idx = -1
        self.update()

    def _show_value_editor(self, index, rect):
        """Spawns a small QLineEdit over the label to edit exact value."""
        self._current_edit_index = index
        val = self.splits[index]
        
        self.editor = QLineEdit(self)
        self.editor.setValidator(QDoubleValidator())
        
        # Use full precision string instead of rounding
        text_val = str(val)
        self.editor.setText(text_val)
        self.editor.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # Calculate required width for full text to prevent cropping
        fm = self.editor.fontMetrics()
        text_width = fm.horizontalAdvance(text_val) + 12 # Padding
        
        # Ensure editor is at least as wide as the label, or wider if needed
        final_width = max(rect.width() + 10, text_width)
        
        # Center the editor horizontally over the original label
        center_x = rect.center().x()
        final_x = int(center_x - final_width / 2)
        
        self.editor.setGeometry(final_x, rect.y() - 2, final_width, rect.height() + 4)
        self.editor.show()
        self.editor.setFocus()
        self.editor.selectAll()
        
        # Install Event Filter to catch Enter/Tab
        self.editor.installEventFilter(self)

    def _finish_edit(self):
        if not self.editor: return
        try:
            new_val = float(self.editor.text())
            new_val = max(self.data_min, min(self.data_max, new_val))
            self.splits[self._current_edit_index] = new_val
            self.splits.sort()
            self.valueChanged.emit()
            self.update()
        except ValueError:
            pass
        self.editor.deleteLater()
        self.editor = None

    def eventFilter(self, obj, event):
        if obj == self.editor and event.type() == QEvent.Type.KeyPress:
             if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Tab):
                 self._finish_edit()
                 return True # Consume event so dialog doesn't close
        return super().eventFilter(obj, event)

class SplitManagerWidget(HorizontalFilterBarWidget):
    """Alias for compatibility with existing code during transition."""
    def __init__(self, parent=None):
        super().__init__(parent)


class CheckableComboBox(QComboBox):
    selectionChanged = pyqtSignal()

    def __init__(self, available_types, selected_types=None, parent=None):
        super().__init__(parent)
        self.available_types = sorted(list(available_types)) if available_types else []
        self._selected_types = set(selected_types) if selected_types is not None else set(self.available_types)
        
        self.setEditable(True)
        self.lineEdit().setReadOnly(True)
        self.lineEdit().installEventFilter(self)
        
        # Custom model for checkable items
        self.model = QStandardItemModel(self)
        self.setModel(self.model)
        
        # Setup view to handle clicks without closing immediately (optional, but good UX)
        self.view().viewport().installEventFilter(self)
        
        self._init_items()
        self._update_text()
        
        # Connect signals
        self.model.itemChanged.connect(self._on_item_changed)
        # Using a timer or similar to detect when popup closes to emit final signal? 
        # Or just emit on every change. 
        
    def _init_items(self):
        self.blockSignals(True)
        self.model.clear()
        
        # Special Actions
        self.item_all = QStandardItem("Select All")
        self.item_all.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        self.model.appendRow(self.item_all)
        
        self.item_none = QStandardItem("Deselect All")
        self.item_none.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        self.model.appendRow(self.item_none)
        
        # Data Items (No separator)
        for t in self.available_types:
            item = QStandardItem(str(t))
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsSelectable)
            if t in self._selected_types:
                item.setCheckState(Qt.CheckState.Checked)
            else:
                item.setCheckState(Qt.CheckState.Unchecked)
            self.model.appendRow(item)
            
        self.blockSignals(False)

    def _on_item_changed(self, item):
        # Handle Check Changes
        if item.isCheckable():
            val_str = item.text()
            try:
                val = int(val_str)
            except:
                val = val_str
                
            if item.checkState() == Qt.CheckState.Checked:
                self._selected_types.add(val)
            else:
                self._selected_types.discard(val)
            self._update_text()
            self.selectionChanged.emit()

    def eventFilter(self, obj, event):
        # Handle "Select All" / "Deselect All" clicks in the view
        if obj == self.view().viewport():
            if event.type() == QEvent.Type.MouseButtonRelease:
                index = self.view().indexAt(event.pos())
                if index.isValid():
                    item = self.model.itemFromIndex(index)
                    if item == self.item_all:
                        self._select_all(True)
                        return True
                    elif item == self.item_none:
                        self._select_all(False)
                        return True
                    elif item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                        # Toggle check state manually if needed, or let default handle it.
                        # QComboBox view behavior is a bit tricky. 
                        # Default behavior toggles check state on click for checkable items.
                        # But it also closes the popup.
                        # We want to keep it open.
                        if item.checkState() == Qt.CheckState.Checked:
                            item.setCheckState(Qt.CheckState.Unchecked)
                        else:
                            item.setCheckState(Qt.CheckState.Checked)
                        return True # Consume event to prevent popup close
        
        elif obj == self.lineEdit():
             if event.type() == QEvent.Type.MouseButtonPress:
                 self.showPopup()
                 return True

        return super().eventFilter(obj, event)

    def showPopup(self):
        super().showPopup()
        # Reset text just in case
        self._update_text()

    def hidePopup(self):
        super().hidePopup()
        self._update_text()

    def _select_all(self, select):
        self.blockSignals(True)
        start_row = 2
        for i in range(start_row, self.model.rowCount()):
            item = self.model.item(i)
            item.setCheckState(Qt.CheckState.Checked if select else Qt.CheckState.Unchecked)
            
            # Update internal set
            val_str = item.text()
            try:
                val = int(val_str)
            except:
                val = val_str
            
            if select:
                self._selected_types.add(val)
            else:
                self._selected_types.discard(val)
                
        self.blockSignals(False)
        self._update_text()
        self.selectionChanged.emit()

    def _update_text(self):
        if not self._selected_types:
            text = "None"
        elif len(self._selected_types) == len(self.available_types):
            text = f"All ({len(self._selected_types)})"
        else:
            sorted_types = sorted(list(self._selected_types), key=lambda x: (isinstance(x, str), x))
            text = ", ".join(map(str, sorted_types))
            
        self.lineEdit().setText(text)

    def get_selection(self):
        return sorted(list(self._selected_types), key=lambda x: (isinstance(x, str), x))


class DSDAddDomainDialog(QDialog):
    def __init__(self, parent=None, current_data=None, current_slice_axis=None, available_types=None, axis_bounds=None):
        super().__init__(parent)
        self.setWindowTitle("Add/Edit Domain")
        self.resize(350, 100) # Start small, let layout expand
        
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setSpacing(10)
        # Ensure dialog shrinks to fit
        self.main_layout.setSizeConstraint(QVBoxLayout.SizeConstraint.SetFixedSize)

        # --- 1. Appearance & Data Group ---
        grp_data = QGroupBox("Appearance & Data")
        lay_data = QFormLayout(grp_data)
        lay_data.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        lay_data.setContentsMargins(10, 10, 10, 10)

        # Name
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Domain Name")
        if current_data:
            self.name_input.setText(current_data.get('name', ''))
        lay_data.addRow("Name:", self.name_input)

        # Atom Types & Color (Merged Row)
        self.type_selector = CheckableComboBox(
            available_types, 
            current_data.get('atom_types') if current_data else None,
            self
        )
        
        init_color = QColor(current_data.get('color')) if (current_data and 'color' in current_data) else self._get_random_color()
        self.color_btn = ColorButton(init_color)
        self.color_btn.setFixedSize(30, 24)
        
        h_types = QHBoxLayout()
        h_types.setContentsMargins(0,0,0,0)
        h_types.addWidget(self.type_selector, 1)
        h_types.addWidget(QLabel("Color:"))
        h_types.addWidget(self.color_btn)
        
        lay_data.addRow("Atom Types:", h_types)
        
        # Style row removed as requested
        
        self.main_layout.addWidget(grp_data)

        # --- 2. Domain Segmentation Group ---
        self.grp_seg = QGroupBox("Domain Segmentation")
        lay_seg = QVBoxLayout(self.grp_seg)
        lay_seg.setContentsMargins(10, 20, 10, 10) 
        lay_seg.setSpacing(5)
        
        # Info Button
        self.info_btn = QToolButton(self.grp_seg)
        self.info_btn.setText("i")
        self.info_btn.setCheckable(True)
        self.info_btn.setStyleSheet("QToolButton { border-radius: 8px; border: 1px solid gray; font-weight: bold; background: #f0f0f0; }")
        self.info_btn.setFixedSize(16, 16)
        self.info_btn.setToolTip("Show/Hide Help")
        self.info_btn.clicked.connect(self._toggle_help)
        self.info_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        
        # Help Text
        self.lbl_help = QLabel(
            "• Left-click bar to split, Right-click handle to remove.\n"
            "• Click numeric labels to edit values.\n"
            "• Right-click segments to toggle active."
        )
        self.lbl_help.setStyleSheet("color: #555; font-style: italic; margin-bottom: 5px; font-size: 10px;")
        self.lbl_help.setVisible(False)
        lay_seg.addWidget(self.lbl_help)

        # Split Manager
        self.split_manager = SplitManagerWidget()
        if axis_bounds:
            self.split_manager.set_range(axis_bounds[0], axis_bounds[1])
        if current_data and 'splits' in current_data:
            self.split_manager.set_data(current_data['splits'], current_data.get('active_segments', []))
        
        self.split_manager.valueChanged.connect(self._check_splits)
        lay_seg.addWidget(self.split_manager)
        
        self.main_layout.addWidget(self.grp_seg)

        # --- 3. Calculation Settings Group ---
        grp_calc = QGroupBox("Calculation Settings")
        lay_calc = QGridLayout(grp_calc)
        lay_calc.setContentsMargins(10, 10, 10, 10)
        lay_calc.setColumnStretch(1, 1)
        
        lbl_sampling = QLabel("<b>SAMPLING</b>")
        lbl_options = QLabel("<b>OPTIONS</b>")
        lay_calc.addWidget(lbl_sampling, 0, 0)
        lay_calc.addWidget(lbl_options, 0, 1)
        
        h_box = QHBoxLayout()
        h_box.addWidget(QLabel("Box Count:"))
        self.num_boxes = QSpinBox()
        self.num_boxes.setRange(1, 1000)
        self.num_boxes.setValue(current_data.get('number_boxes', 10) if current_data else 10)
        h_box.addWidget(self.num_boxes)
        lay_calc.addLayout(h_box, 1, 0)
        
        h_arr = QHBoxLayout()
        h_arr.addWidget(QLabel("Arrangement:"))
        self.arrangement = QComboBox()
        self.arrangement.addItems(['inside', 'protrude', 'combined'])
        if current_data:
            self.arrangement.setCurrentText(current_data.get('box_arrangement', 'protrude'))
        else:
             self.arrangement.setCurrentText('protrude')
        h_arr.addWidget(self.arrangement)
        lay_calc.addLayout(h_arr, 2, 0)
        
        self.chk_pbc = QCheckBox("Consider PBCs")
        if current_data:
            self.chk_pbc.setChecked(current_data.get('pbc', False))
            
        if current_slice_axis and current_slice_axis not in ['x', 'y', 'z']:
            self.chk_pbc.setEnabled(False)
        lay_calc.addWidget(self.chk_pbc, 1, 1)
        
        self.chk_weighted = QCheckBox("Weighted average")
        if current_data:
            self.chk_weighted.setChecked(current_data.get('weighted', False))
        lay_calc.addWidget(self.chk_weighted, 2, 1)
        
        # Geometric center Mode (Independent)
        self.chk_bary = QCheckBox("Centroid Mode")
        self.chk_bary.setToolTip("Ensures box centers match the mean geometric position of particles in the bin.")
        if current_data:
            self.chk_bary.setChecked(current_data.get('bary_mid_twoside_weight', False))
        else:
            self.chk_bary.setChecked(False)
        lay_calc.addWidget(self.chk_bary, 3, 1)
        
        self.main_layout.addWidget(grp_calc)
        
        # No addStretch() to keep it compact

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.validate_and_accept)
        self.buttons.rejected.connect(self.reject)
        self.main_layout.addWidget(self.buttons)
        
        self._check_splits()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'grp_seg') and hasattr(self, 'info_btn'):
            margin_right = 5
            self.info_btn.move(self.grp_seg.width() - self.info_btn.width() - margin_right, 0)

    def _get_random_color(self):
        hue = random.random()
        return QColor.fromHsvF(hue, 1.0, 1.0, 1.0)
    
    def _toggle_help(self):
        self.lbl_help.setVisible(self.info_btn.isChecked())
        
    def _check_splits(self):
        splits, active_segments = self.split_manager.get_data()
        
        if "Disabled" in self.chk_pbc.text():
            return

        allow_pbc = False
        if not splits:
            allow_pbc = True
        elif active_segments and active_segments[0] and active_segments[-1]:
            allow_pbc = True
            
        if allow_pbc:
            self.chk_pbc.setEnabled(True)
        else:
            self.chk_pbc.setEnabled(False)
            self.chk_pbc.setChecked(False)

    def validate_and_accept(self):
        selection = self.type_selector.get_selection()
        if self.type_selector.available_types and not selection:
            QMessageBox.warning(self, "Invalid Selection", "Please select at least one Atom Type.")
            return
        self.accept()

    def get_data(self):
        splits, active_segments = self.split_manager.get_data()
        return {
            'name': self.name_input.text() or "Domain",
            'atom_types': self.type_selector.get_selection(),
            'color': self.color_btn.color().name(),
            # Style removed from dialog, preserved in table logic
            'number_boxes': self.num_boxes.value(),
            'box_arrangement': self.arrangement.currentText(),
            'pbc': self.chk_pbc.isChecked(),
            'weighted': self.chk_weighted.isChecked(),
            'bary_mid_twoside_weight': self.chk_bary.isChecked(),
            'splits': splits,
            'active_segments': active_segments
        }

class DSDTableWidget(QTableWidget):
    """
    Table to manage domains.
    """
    rowRemoved = pyqtSignal(int)
    rowMoved = pyqtSignal(int, int) # old_idx, new_idx
    domainEdited = pyqtSignal(int, dict)

    def __init__(self, panel_ref=None, parent=None):
        super().__init__(parent)
        self.panel = panel_ref 
        self.setColumnCount(7)
        self.context_mode = 'displacement' # 'displacement' or 'strain'
        
        # Bold Header
        header_labels = ["↕", "Plot", "✅", "Color", "Style", "Size", "Del"]
        self.setHorizontalHeaderLabels(header_labels) 
        
        font = self.horizontalHeader().font()
        font.setBold(True)
        self.horizontalHeader().setFont(font)
        
        header = self.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed) # Arrows
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch) # Plot Name
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed) # Show
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed) # Color
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed) # Style
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed) # Size
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed) # Del
        
        self.setColumnWidth(0, 25)
        self.setColumnWidth(2, 25)
        self.setColumnWidth(3, 35) 
        self.setColumnWidth(4, 50) 
        self.setColumnWidth(5, 35)
        self.setColumnWidth(6, 30)
        
        self.verticalHeader().hide()
        self.opt_line_row = -1 # Track optimal line position

    def set_context(self, mode):
        """Switches between 'displacement' and 'strain' styling contexts."""
        if mode not in ['displacement', 'strain']: return
        self.context_mode = mode
        self.refresh_table_context()

    def refresh_table_context(self):
        """Updates displayed Style and Size values based on current context."""
        def format_val(val):
            try:
                f = float(val)
                return f"{f:g}"
            except: return str(val)

        for row in range(self.rowCount()):
            item = self.item(row, 0)
            if not item: continue
            settings = item.data(Qt.ItemDataRole.UserRole)
            
            # Update Style Combo
            style_combo = self.cellWidget(row, 4)
            if style_combo:
                style_combo.blockSignals(True)
                if self.context_mode == 'displacement':
                    style_combo.setCurrentText(settings.get('style', 'Dots'))
                else:
                    style_combo.setCurrentText(settings.get('strain_style', '-'))
                style_combo.blockSignals(False)
            
            # Update Size Edit
            size_edit = self.cellWidget(row, 5)
            if size_edit:
                size_edit.blockSignals(True)
                if self.context_mode == 'displacement':
                    size_edit.setText(format_val(settings.get('size', '2')))
                else:
                    size_edit.setText(format_val(settings.get('strain_size', '2.0')))
                size_edit.blockSignals(False)

            # Update Show Checkbox
            chk_container = self.cellWidget(row, 2)
            if chk_container:
                chk_show = chk_container.findChild(QCheckBox)
                if chk_show:
                    chk_show.blockSignals(True)
                    if self.context_mode == 'displacement':
                        chk_show.setChecked(settings.get('show', True))
                    else:
                        chk_show.setChecked(settings.get('strain_show', True))
                    chk_show.blockSignals(False)

    def add_domain(self, name, settings, is_optimal_line=False):
        if settings.get('is_optimal_line', False) or name in ["Optimal Line", "End-to-end"]:
            is_optimal_line = True
            
        def format_val(val):
            try:
                f = float(val)
                return f"{f:g}"
            except: return str(val)

        # Optimal line always at the BOTTOM
        if is_optimal_line:
            if self.opt_line_row != -1:
                return # Already exists
            row = self.rowCount()
            self.opt_line_row = row
            self.insertRow(row)
        else:
            if self.opt_line_row != -1:
                row = self.opt_line_row
                self.insertRow(row)
                self.opt_line_row += 1 # Push optimal line down
            else:
                row = self.rowCount()
                self.insertRow(row)
        
        # Col 0: Move Buttons
        if not is_optimal_line:
            move_container = QWidget()
            move_layout = QVBoxLayout(move_container)
            move_layout.setContentsMargins(0, 0, 0, 0)
            move_layout.setSpacing(0)
            move_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            
            btn_up = QPushButton("▲")
            btn_up.setObjectName("up_button")
            btn_up.clicked.connect(self._move_row_up)
            
            btn_down = QPushButton("▼")
            btn_down.setObjectName("down_button")
            btn_down.clicked.connect(self._move_row_down)
            
            move_layout.addWidget(btn_up)
            move_layout.addWidget(btn_down)
            self.setCellWidget(row, 0, move_container)
        else:
            self.setCellWidget(row, 0, QWidget())
            
        # Col 1: Name Button / Label
        if is_optimal_line:
            lbl = QLabel("End-to-end")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet("font-weight: bold;")
            self.setCellWidget(row, 1, lbl)
        else:
            btn = QPushButton(name)
            btn.clicked.connect(lambda: self._handle_edit_click(btn))
            self.setCellWidget(row, 1, btn)

        # Col 2: Show Checkbox
        chk_show = QCheckBox()
        # Explicitly center the checkbox
        chk_show.setStyleSheet("margin-left: 5px; margin-right: 5px;")
        
        # Init based on context
        if self.context_mode == 'displacement':
            chk_show.setChecked(settings.get('show', True))
        else:
            chk_show.setChecked(settings.get('strain_show', True))
            
        chk_show.toggled.connect(lambda checked: self._on_show_toggled(chk_show, checked))
        
        # Container to center it nicely
        chk_container = QWidget()
        chk_layout = QHBoxLayout(chk_container)
        chk_layout.setContentsMargins(0,0,0,0)
        chk_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        chk_layout.addWidget(chk_show)
        self.setCellWidget(row, 2, chk_container)
            
        # Col 3: Color
        color_btn = ColorButton(QColor(settings.get('color', 'black')))
        color_btn.colorChanged.connect(lambda: self._on_color_changed(color_btn))
        self.setCellWidget(row, 3, color_btn)
        
        # Col 4: Style
        style_combo = QComboBox()
        style_combo.addItems(["Dots", "o", "x", "+", "d", "s", "t", "p", "h", "star", "-", "--", ".-"])
        
        # Init based on context
        if self.context_mode == 'displacement':
            style_combo.setCurrentText(settings.get('style', 'Dots'))
        else:
            style_combo.setCurrentText(settings.get('strain_style', '-'))
            
        style_combo.currentTextChanged.connect(lambda t: self._on_style_changed(style_combo, t))
        self.setCellWidget(row, 4, style_combo)
        
        # Col 5: Size
        init_size = settings.get('size', '2') if self.context_mode == 'displacement' else settings.get('strain_size', '2.0')
        size_edit = QLineEdit(format_val(init_size))
        
        # Use Double Validator for decimal support with C Locale (International)
        from PyQt6.QtCore import QLocale
        val = QDoubleValidator(0.1, 100.0, 2)
        val.setLocale(QLocale(QLocale.Language.C))
        val.setNotation(QDoubleValidator.Notation.StandardNotation)
        size_edit.setValidator(val)
        
        size_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        size_edit.textChanged.connect(lambda t: self._on_size_changed(size_edit, t))
        self.setCellWidget(row, 5, size_edit)
        
        # Col 6: Del
        del_btn = QPushButton("X")
        del_btn.setStyleSheet("color: red; font-weight: bold;")
        del_btn.clicked.connect(lambda: self._delete_row(del_btn))
        
        del_container = QWidget()
        del_layout = QHBoxLayout(del_container)
        del_layout.setContentsMargins(0,0,0,0)
        del_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        del_layout.addWidget(del_btn)
        self.setCellWidget(row, 6, del_container)
        
        # Store settings in hidden item 0
        settings['is_optimal_line'] = is_optimal_line
        settings['show'] = settings.get('show', True)
        settings['strain_show'] = settings.get('strain_show', True)
        dummy = QTableWidgetItem()
        dummy.setData(Qt.ItemDataRole.UserRole, settings)
        self.setItem(row, 0, dummy)
        
        self._update_move_buttons_visibility()


    def _move_row_up(self):
        btn = self.sender()
        if not btn: return
        pos = btn.parentWidget().mapTo(self.viewport(), QPoint(0,0))
        row = self.indexAt(pos).row()
        
        if row > 0:
            self._swap_rows(row, row - 1)
            self._update_move_buttons_visibility()

    def _move_row_down(self):
        btn = self.sender()
        if not btn: return
        pos = btn.parentWidget().mapTo(self.viewport(), QPoint(0,0))
        row = self.indexAt(pos).row()
        
        if row < self.rowCount() - 1:
            self._swap_rows(row, row + 1)
            self._update_move_buttons_visibility()

    def _update_move_buttons_visibility(self):
        rowCount = self.rowCount()
        last_real_row = rowCount - 1
        if self.opt_line_row != -1:
            last_real_row = self.opt_line_row - 1
            
        for row in range(rowCount):
            cw = self.cellWidget(row, 0)
            if not cw: continue
            
            btn_up = cw.findChild(QPushButton, "up_button")
            btn_down = cw.findChild(QPushButton, "down_button")
            
            if btn_up and btn_down:
                if row == self.opt_line_row:
                    btn_up.setEnabled(False)
                    btn_down.setEnabled(False)
                else:
                    btn_up.setEnabled(row > 0)
                    btn_down.setEnabled(row < last_real_row)

    def _swap_rows(self, r1, r2):
        if r1 == self.opt_line_row or r2 == self.opt_line_row:
            return
            
        was_blocked = self.blockSignals(True)
        try:
            # 1. Swap Data
            item1 = self.item(r1, 0)
            item2 = self.item(r2, 0)
            data1 = item1.data(Qt.ItemDataRole.UserRole)
            data2 = item2.data(Qt.ItemDataRole.UserRole)
            item1.setData(Qt.ItemDataRole.UserRole, data2)
            item2.setData(Qt.ItemDataRole.UserRole, data1)
            
            # 2. Swap Widgets
            # Name/Edit (Col 1)
            w1 = self.cellWidget(r1, 1)
            w2 = self.cellWidget(r2, 1)
            
            if isinstance(w1, QPushButton) and isinstance(w2, QPushButton):
                t1 = w1.text()
                w1.setText(w2.text())
                w2.setText(t1)

            # Show (Col 2)
            c1_container = self.cellWidget(r1, 2)
            c2_container = self.cellWidget(r2, 2)
            chk1 = c1_container.findChild(QCheckBox)
            chk2 = c2_container.findChild(QCheckBox)
            if chk1 and chk2:
                 state1 = chk1.isChecked()
                 state2 = chk2.isChecked()
                 chk1.blockSignals(True)
                 chk2.blockSignals(True)
                 chk1.setChecked(state2)
                 chk2.setChecked(state1)
                 chk1.blockSignals(False)
                 chk2.blockSignals(False)
                
            # Color (Col 3)
            c1 = self.cellWidget(r1, 3)
            c2 = self.cellWidget(r2, 3)
            col1 = c1.color()
            col2 = c2.color()
            c1.blockSignals(True)
            c2.blockSignals(True)
            c1.set_color(col2)
            c2.set_color(col1)
            c1.blockSignals(False)
            c2.blockSignals(False)
            
            # Style (Col 4)
            s1 = self.cellWidget(r1, 4)
            s2 = self.cellWidget(r2, 4)
            st1 = s1.currentText()
            st2 = s2.currentText()
            s1.blockSignals(True)
            s2.blockSignals(True)
            s1.setCurrentText(st2)
            s2.setCurrentText(st1)
            s1.blockSignals(False)
            s2.blockSignals(False)
            
            # Size (Col 5)
            sz1 = self.cellWidget(r1, 5)
            sz2 = self.cellWidget(r2, 5)
            siz1 = sz1.text()
            siz2 = sz2.text()
            sz1.blockSignals(True)
            sz2.blockSignals(True)
            sz1.setText(siz2)
            sz2.setText(siz1)
            sz1.blockSignals(False)
            sz2.blockSignals(False)
        finally:
            self.blockSignals(was_blocked)
        
        self.rowMoved.emit(r1, r2) 

    def _on_show_toggled(self, chk, checked):
        row = -1
        for r in range(self.rowCount()):
             cw = self.cellWidget(r, 2)
             if cw and cw.findChild(QCheckBox) == chk:
                 row = r; break
        if row != -1:
             item = self.item(row, 0)
             if item:
                 settings = item.data(Qt.ItemDataRole.UserRole)
                 if self.context_mode == 'displacement':
                     settings['show'] = checked
                 else:
                     settings['strain_show'] = checked
                 item.setData(Qt.ItemDataRole.UserRole, settings)
                 self.domainEdited.emit(row, settings) 

    def _delete_row(self, btn):
        for r in range(self.rowCount()):
            cw = self.cellWidget(r, 6) 
            if cw:
                child = cw.findChild(QPushButton)
                if child == btn:
                    is_opt = (r == self.opt_line_row)
                    self.removeRow(r)
                    if is_opt:
                        self.opt_line_row = -1
                        if self.panel and hasattr(self.panel, 'on_opt_line_deleted'):
                            self.panel.on_opt_line_deleted()
                    elif self.opt_line_row != -1 and r < self.opt_line_row:
                        self.opt_line_row -= 1
                        
                    self.rowRemoved.emit(r)
                    self._update_move_buttons_visibility()
                    return

    def _on_style_changed(self, combo, text):
        row = -1
        for r in range(self.rowCount()):
            if self.cellWidget(r, 4) == combo:
                row = r; break
        if row != -1:
            item = self.item(row, 0)
            if item:
                settings = item.data(Qt.ItemDataRole.UserRole)
                # Store based on context
                if self.context_mode == 'displacement':
                    settings['style'] = text
                else:
                    settings['strain_style'] = text
                
                item.setData(Qt.ItemDataRole.UserRole, settings)
                self.domainEdited.emit(row, settings)

    def _on_size_changed(self, edit, text):
        row = -1
        for r in range(self.rowCount()):
            if self.cellWidget(r, 5) == edit:
                row = r; break
        if row != -1:
            item = self.item(row, 0)
            if item:
                settings = item.data(Qt.ItemDataRole.UserRole)
                # Store based on context
                if self.context_mode == 'displacement':
                    settings['size'] = text
                else:
                    settings['strain_size'] = text
                    
                item.setData(Qt.ItemDataRole.UserRole, settings)
                self.domainEdited.emit(row, settings)
            
    def _handle_edit_click(self, btn):
        # Find row
        row = -1
        for r in range(self.rowCount()):
            if self.cellWidget(r, 1) == btn:
                row = r
                break
        if row == -1: return

        item = self.item(row, 0)
        settings = item.data(Qt.ItemDataRole.UserRole)
        
        # Sync current state of widgets into settings before editing
        color_btn = self.cellWidget(row, 3)
        if color_btn: settings['color'] = color_btn.color().name()
        
        style_combo = self.cellWidget(row, 4)
        if style_combo:
            if self.context_mode == 'displacement':
                settings['style'] = style_combo.currentText()
            else:
                settings['strain_style'] = style_combo.currentText()
        
        size_edit = self.cellWidget(row, 5)
        if size_edit:
            if self.context_mode == 'displacement':
                settings['size'] = size_edit.text()
            else:
                settings['strain_size'] = size_edit.text()

        # Context
        current_slice_axis = None
        available_types = []
        axis_bounds = None
        
        if self.panel:
            if hasattr(self.panel, 'slice_axis_combo'):
                current_slice_axis = self.panel.slice_axis_combo.currentText()
            if hasattr(self.panel, 'data_manager') and hasattr(self.panel, 'controller'):
                study = self.panel.study_combo.currentText()
                system = self.panel.system_combo.currentText()
                if self.panel.controller.timesteps:
                    df, _ = self.panel.data_manager.load_frame(study, system, self.panel.controller.timesteps[0])
                    if df is not None:
                         if 'type' in df.columns:
                            available_types = sorted(df['type'].unique().tolist())
                         if current_slice_axis in df.columns:
                            axis_bounds = (df[current_slice_axis].min(), df[current_slice_axis].max())

        dlg = DSDAddDomainDialog(self, settings, current_slice_axis, available_types, axis_bounds)
        if dlg.exec():
            new_data = dlg.get_data()
            self.cellWidget(row, 1).setText(new_data['name'])
            self.cellWidget(row, 3).set_color(QColor(new_data['color']))
            # Style/Size are handled by table logic, preserved from before dialog
            
            # Restore styles/sizes from old settings + context logic
            new_data['style'] = settings.get('style', 'Dots')
            new_data['size'] = settings.get('size', '2')
            new_data['strain_style'] = settings.get('strain_style', '-')
            new_data['strain_size'] = settings.get('strain_size', '2.0')
            
            new_data['show'] = settings.get('show', True)
            new_data['strain_show'] = settings.get('strain_show', True)
            
            item.setData(Qt.ItemDataRole.UserRole, new_data)
            self.domainEdited.emit(row, new_data)

    def _on_color_changed(self, btn):
        row = -1
        for r in range(self.rowCount()):
            if self.cellWidget(r, 3) == btn:
                row = r; break
        if row != -1:
            item = self.item(row, 0)
            if item:
                settings = item.data(Qt.ItemDataRole.UserRole)
                settings['color'] = btn.color().name()
                item.setData(Qt.ItemDataRole.UserRole, settings)
                self.domainEdited.emit(row, settings)

    def get_domains(self):
        domains = []
        for r in range(self.rowCount()):
            item = self.item(r, 0)
            if item:
                settings = item.data(Qt.ItemDataRole.UserRole).copy()
                cw = self.cellWidget(r, 1)
                if isinstance(cw, QPushButton):
                    settings['name'] = cw.text()
                domains.append(settings)
        return domains
