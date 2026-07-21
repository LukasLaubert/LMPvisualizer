# lmp_visualizer/trj_widgets.py

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSlider, QPushButton, 
    QSpinBox, QDoubleSpinBox, QDialog, QFormLayout, QDialogButtonBox,
    QFrame, QStyle, QLineEdit
)
from PyQt6.QtCore import Qt, pyqtSignal, QRectF
from PyQt6.QtGui import QPainter, QBrush, QColor, QLinearGradient, QMouseEvent, QFont

class ConstrainedDoubleSpinBox(QDoubleSpinBox):
    """
    A QDoubleSpinBox that allows typing values outside a local range
    but clamps scrolling to that local range.
    """
    def __init__(self, c_min, c_max, parent=None):
        super().__init__(parent)
        self._c_min = c_min
        self._c_max = c_max
        # Set a very wide range for typing, but internal logic will use _c_min/_c_max for scrolling
        self.setRange(-1e12, 1e12) 

    def stepBy(self, steps):
        current_val = self.value()
        step_size = self.singleStep()

        new_val = current_val + step_size * steps
        
        # Clamp new_val to the local constraints for scrolling
        if new_val < self._c_min:
            new_val = self._c_min
        elif new_val > self._c_max:
            new_val = self._c_max
        
        # Prevent stepping beyond the constraint bounds if already at them
        if (current_val == self._c_min and steps < 0) or \
           (current_val == self._c_max and steps > 0):
            return # Don't change value

        self.setValue(new_val)

class FilterBarWidget(QWidget):
    """
    Vertical bar widget for filtering multiple ranges (min/max segments).
    Includes logic to maintain relative handle positions when data range changes.
    Supports splitting, joining, creating, and deleting segments.
    """
    rangesChanged = pyqtSignal(list)  # Emits list of (min, max) tuples

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(40)
        self.setMinimumHeight(150)

        self.data_min = 0.0
        self.data_max = 100.0
        
        # List of tuples [(min, max), ...] sorted by value (descending visual, but handled by logic)
        self.current_ranges = [(0.0, 100.0)] 

        self.hover_handle = None  # (index, 'top'|'bottom'|'center')
        self.dragging = None
        self.drag_start_y = 0
        self.drag_start_vals = (0.0, 0.0)

        self.bar_width = 8
        self.margin_top = 20
        self.margin_bottom = 20
        self.bar_x = (40 - self.bar_width) // 2

        self.setMouseTracking(True)

    def set_data_range(self, dmin, dmax):
        if dmin > dmax: dmin, dmax = dmax, dmin
        if dmin == dmax: dmax += 0.001
        
        old_range = self.data_max - self.data_min
        if old_range == 0: old_range = 1.0
        
        # Calculate relative positions for all existing ranges
        rel_ranges = []
        for cmin, cmax in self.current_ranges:
            rmin = (cmin - self.data_min) / old_range
            rmax = (cmax - self.data_min) / old_range
            rel_ranges.append((max(0.0, min(1.0, rmin)), max(0.0, min(1.0, rmax))))
            
        self.data_min = dmin
        self.data_max = dmax
        new_range = dmax - dmin
        
        # Reconstruct ranges based on new data limits
        self.current_ranges = []
        for rmin, rmax in rel_ranges:
            nmin = self.data_min + (rmin * new_range)
            nmax = self.data_min + (rmax * new_range)
            self.current_ranges.append((nmin, nmax))
            
        self.update()

    def set_current_ranges(self, ranges):
        """Sets the list of active ranges."""
        valid_ranges = []
        for start, end in ranges:
            start = max(self.data_min, min(self.data_max, start))
            end = max(self.data_min, min(self.data_max, end))
            if start > end: start, end = end, start
            valid_ranges.append((start, end))
        
        # Sort and merge overlapping if necessary (though UI prevents overlap usually)
        valid_ranges.sort(key=lambda x: x[0])
        self.current_ranges = valid_ranges
        self.update()

    # Backwards compatibility wrapper
    def set_current_range(self, cmin, cmax):
        self.set_current_ranges([(cmin, cmax)])

    @property
    def current_min(self):
        # Return global min across all ranges for compatibility
        if not self.current_ranges: return self.data_min
        return min(r[0] for r in self.current_ranges)

    @property
    def current_max(self):
        # Return global max across all ranges for compatibility
        if not self.current_ranges: return self.data_max
        return max(r[1] for r in self.current_ranges)

    def val_to_y(self, val):
        h = self.height() - self.margin_top - self.margin_bottom
        if h <= 0: return self.margin_top
        
        ratio = (val - self.data_min) / (self.data_max - self.data_min)
        return (self.margin_top + h) - (ratio * h)

    def y_to_val(self, y):
        h = self.height() - self.margin_top - self.margin_bottom
        y_bottom = self.margin_top + h
        
        if h <= 0: return self.data_min

        y = max(self.margin_top, min(y_bottom, y))
        ratio = (y_bottom - y) / h
        return self.data_min + ratio * (self.data_max - self.data_min)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()

        painter.setPen(Qt.GlobalColor.black)
        font = painter.font()
        font.setPointSize(7)
        painter.setFont(font)
        
        painter.drawText(QRectF(0, 0, w, self.margin_top), Qt.AlignmentFlag.AlignCenter, f"{self.data_max:.1e}")
        painter.drawText(QRectF(0, h - self.margin_bottom, w, self.margin_bottom), Qt.AlignmentFlag.AlignCenter, f"{self.data_min:.1e}")

        bar_h = h - self.margin_top - self.margin_bottom
        bar_rect = QRectF(self.bar_x, self.margin_top, self.bar_width, bar_h)
        
        painter.setBrush(QColor(230, 230, 230))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(bar_rect, 3, 3)

        handle_h = 6
        handle_w = 20
        handle_x = self.bar_x + (self.bar_width - handle_w) / 2

        for i, (cmin, cmax) in enumerate(self.current_ranges):
            y_high = self.val_to_y(cmax)
            y_low = self.val_to_y(cmin)
            
            range_rect = QRectF(self.bar_x, y_high, self.bar_width, y_low - y_high)
            painter.setBrush(QColor(255, 150, 150))
            painter.drawRect(range_rect)

            # Top Handle
            is_hover_top = (self.hover_handle == (i, 'top'))
            col_top = QColor(0, 120, 240) if is_hover_top else QColor(100, 100, 100)
            painter.setBrush(col_top)
            painter.drawRoundedRect(QRectF(handle_x, y_high - handle_h/2, handle_w, handle_h), 2, 2)
            
            # Bottom Handle
            is_hover_bot = (self.hover_handle == (i, 'bottom'))
            col_bot = QColor(0, 120, 240) if is_hover_bot else QColor(100, 100, 100)
            painter.setBrush(col_bot)
            painter.drawRoundedRect(QRectF(handle_x, y_low - handle_h/2, handle_w, handle_h), 2, 2)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and self.hover_handle:
            self.dragging = self.hover_handle
            self.drag_start_y = event.position().y()
            idx, _ = self.hover_handle
            self.drag_start_vals = self.current_ranges[idx]
        elif event.button() == Qt.MouseButton.RightButton:
            self.show_context_menu(event.position())

    def show_context_menu(self, pos):
        from PyQt6.QtWidgets import QMenu
        val = self.y_to_val(pos.y())
        
        # Check if inside a segment
        target_idx = -1
        for i, (rmin, rmax) in enumerate(self.current_ranges):
            if rmin <= val <= rmax:
                target_idx = i
                break
        
        menu = QMenu(self)
        if target_idx != -1:
            # Inside a red segment
            edit_action = menu.addAction("Set Filter Range")
            edit_action.triggered.connect(lambda: self.open_set_range_dialog(target_idx))

            split_action = menu.addAction("Split Filter segment")
            split_action.triggered.connect(lambda: self.split_segment(target_idx, val))
            
            del_action = menu.addAction("Delete Filter segment")
            del_action.triggered.connect(lambda: self.delete_segment(target_idx))
        else:
            # Inside a pale area
            create_action = menu.addAction("Create Filter segment")
            create_action.triggered.connect(lambda: self.create_segment(val))
            
            # Check for joining
            # Find neighbors
            # Since self.current_ranges is likely sorted by min logic or just implementation order
            # but let's ensure we find the gap.
            sorted_ranges = sorted(enumerate(self.current_ranges), key=lambda x: x[1][0])
            
            below_idx = -1
            above_idx = -1
            
            for i in range(len(sorted_ranges)):
                idx, (rmin, rmax) = sorted_ranges[i]
                if rmax < val:
                    below_idx = idx # Found a segment strictly below val
                if rmin > val:
                    above_idx = idx # Found a segment strictly above val
                    break # First one above is the immediate neighbor
            
            if below_idx != -1 and above_idx != -1:
                join_action = menu.addAction("Join Filter segments")
                join_action.triggered.connect(lambda: self.join_segments(below_idx, above_idx))
                
        menu.exec(self.mapToGlobal(pos.toPoint()))

    def open_set_range_dialog(self, target_idx):
        if target_idx < 0 or target_idx >= len(self.current_ranges):
            return

        # Ensure sorted for checking neighbors
        sorted_indices = sorted(range(len(self.current_ranges)), key=lambda k: self.current_ranges[k][0])
        sorted_pos = sorted_indices.index(target_idx)
        
        # Determine constraints
        constraint_min = self.data_min
        constraint_max = self.data_max
        
        if sorted_pos > 0:
            prev_idx = sorted_indices[sorted_pos - 1]
            constraint_min = self.current_ranges[prev_idx][1] # Max of previous
        
        if sorted_pos < len(self.current_ranges) - 1:
            next_idx = sorted_indices[sorted_pos + 1]
            constraint_max = self.current_ranges[next_idx][0] # Min of next
        
        dialog = QDialog(self)
        dialog.setWindowTitle("Set Filter Range")
        layout = QFormLayout(dialog)
        
        cur_min, cur_max = self.current_ranges[target_idx]
        
        def clamp_val(spinbox):
            val = spinbox.value()
            clamped = max(constraint_min, min(constraint_max, val))
            if val != clamped:
                spinbox.setValue(clamped)

        sp_max = ConstrainedDoubleSpinBox(constraint_min, constraint_max)
        sp_max.setDecimals(4)
        sp_max.setValue(cur_max)
        sp_max.editingFinished.connect(lambda: clamp_val(sp_max))
        
        sp_min = ConstrainedDoubleSpinBox(constraint_min, constraint_max)
        sp_min.setDecimals(4)
        sp_min.setValue(cur_min)
        sp_min.editingFinished.connect(lambda: clamp_val(sp_min))
        
        layout.addRow("Max:", sp_max)
        layout.addRow("Min:", sp_min)
        layout.addRow(QLabel(f"Bounds: [{constraint_min:.4f}, {constraint_max:.4f}]"))
        
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dialog.accept)
        btns.rejected.connect(dialog.reject)
        layout.addWidget(btns)
        
        if dialog.exec():
            v1 = sp_min.value()
            v2 = sp_max.value()
            
            # Final clamp
            v1 = max(constraint_min, min(constraint_max, v1))
            v2 = max(constraint_min, min(constraint_max, v2))
            
            if v1 > v2: v1, v2 = v2, v1
            
            self.current_ranges[target_idx] = (v1, v2)
            self.current_ranges.sort(key=lambda x: x[0])
            self.rangesChanged.emit(self.current_ranges)
            self.update()

    def split_segment(self, idx, split_val):
        rmin, rmax = self.current_ranges[idx]
        if abs(rmin - split_val) < 1e-6 or abs(rmax - split_val) < 1e-6:
            return # Too close to edge
            
        # Create two new segments
        seg1 = (rmin, split_val)
        seg2 = (split_val, rmax)
        
        self.current_ranges.pop(idx)
        self.current_ranges.append(seg1)
        self.current_ranges.append(seg2)
        # Keep sorted
        self.current_ranges.sort(key=lambda x: x[0])
        self.rangesChanged.emit(self.current_ranges)
        self.update()

    def delete_segment(self, idx):
        self.current_ranges.pop(idx)
        self.rangesChanged.emit(self.current_ranges)
        self.update()

    def create_segment(self, val):
        # Create a small segment centered at val, but constrained by neighbors
        delta = (self.data_max - self.data_min) * 0.05 # 5% width default
        new_min = val - delta/2
        new_max = val + delta/2
        
        # Constrain
        new_min = max(self.data_min, new_min)
        new_max = min(self.data_max, new_max)
        
        # Check overlaps
        # Simplest way: just clip against existing ranges
        # Better: clamp to gap
        
        sorted_ranges = sorted(self.current_ranges, key=lambda x: x[0])
        gap_min = self.data_min
        gap_max = self.data_max
        
        for rmin, rmax in sorted_ranges:
            if rmax < val:
                gap_min = max(gap_min, rmax)
            if rmin > val:
                gap_max = min(gap_max, rmin)
                break
                
        new_min = max(new_min, gap_min)
        new_max = min(new_max, gap_max)
        
        if new_max > new_min:
            self.current_ranges.append((new_min, new_max))
            self.current_ranges.sort(key=lambda x: x[0])
            self.rangesChanged.emit(self.current_ranges)
            self.update()

    def join_segments(self, idx1, idx2):
        # Join range idx1 and idx2
        r1 = self.current_ranges[idx1]
        r2 = self.current_ranges[idx2]
        
        new_min = min(r1[0], r2[0])
        new_max = max(r1[1], r2[1])
        
        # Remove both
        # Be careful with indices shifting. Remove largest index first.
        if idx1 > idx2:
            self.current_ranges.pop(idx1)
            self.current_ranges.pop(idx2)
        else:
            self.current_ranges.pop(idx2)
            self.current_ranges.pop(idx1)
            
        self.current_ranges.append((new_min, new_max))
        self.current_ranges.sort(key=lambda x: x[0])
        self.rangesChanged.emit(self.current_ranges)
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent):
        y = event.position().y()
        tol = 8

        if not self.dragging:
            # Hit test
            found = False
            for i, (rmin, rmax) in enumerate(self.current_ranges):
                y_high = self.val_to_y(rmax)
                y_low = self.val_to_y(rmin)
                
                if abs(y - y_high) < tol:
                    self.hover_handle = (i, 'top')
                    self.setCursor(Qt.CursorShape.SizeVerCursor)
                    found = True
                    break
                elif abs(y - y_low) < tol:
                    self.hover_handle = (i, 'bottom')
                    self.setCursor(Qt.CursorShape.SizeVerCursor)
                    found = True
                    break
                elif y_high < y < y_low:
                    self.hover_handle = (i, 'center')
                    self.setCursor(Qt.CursorShape.SizeAllCursor)
                    found = True
                    break
            
            if not found:
                self.hover_handle = None
                self.setCursor(Qt.CursorShape.PointingHandCursor)
            
            self.update()
        else:
            idx, mode = self.dragging
            curr_ranges = sorted(self.current_ranges, key=lambda x: x[0])
            # Find where 'idx' is in the sorted list to check neighbors
            # (Assuming self.current_ranges stays sorted or we resort)
            # Actually, let's work with the actual object in current_ranges
            
            # Constraints
            # Find neighbors
            target_range = self.current_ranges[idx]
            # Since list might not be sorted by index, find position in sorted comparison
            sorted_indices = sorted(range(len(self.current_ranges)), key=lambda k: self.current_ranges[k][0])
            sorted_pos = sorted_indices.index(idx)
            
            # Constraint bounds
            constraint_min = self.data_min
            constraint_max = self.data_max
            
            if sorted_pos > 0:
                prev_idx = sorted_indices[sorted_pos - 1]
                constraint_min = self.current_ranges[prev_idx][1] # Max of previous
            
            if sorted_pos < len(self.current_ranges) - 1:
                next_idx = sorted_indices[sorted_pos + 1]
                constraint_max = self.current_ranges[next_idx][0] # Min of next
                
            delta_px = y - self.drag_start_y
            
            if mode == 'top':
                # Changing MAX value
                val = self.y_to_val(y)
                # Max cannot go below Min of this range
                val = max(target_range[0], val)
                # Max cannot go above next range's Min
                val = min(constraint_max, val)
                
                self.current_ranges[idx] = (target_range[0], val)

            elif mode == 'bottom':
                # Changing MIN value
                val = self.y_to_val(y)
                # Min cannot go above Max of this range
                val = min(target_range[1], val)
                # Min cannot go below prev range's Max
                val = max(constraint_min, val)
                
                self.current_ranges[idx] = (val, target_range[1])

            elif mode == 'center':
                start_min, start_max = self.drag_start_vals
                diff = start_max - start_min
                
                start_y_min = self.val_to_y(start_min)
                target_y_min = start_y_min + delta_px
                new_min = self.y_to_val(target_y_min)
                
                new_max = new_min + diff
                
                # Check bounds against constraints
                if new_min < constraint_min:
                    new_min = constraint_min
                    new_max = new_min + diff
                
                if new_max > constraint_max:
                    new_max = constraint_max
                    new_min = new_max - diff
                    
                self.current_ranges[idx] = (new_min, new_max)
            
            self.rangesChanged.emit(self.current_ranges)
            self.update()

    def mouseReleaseEvent(self, event):
        self.dragging = None
        # Ensure sorted order after drag
        self.current_ranges.sort(key=lambda x: x[0])
        self.update()

    def mouseDoubleClickEvent(self, event):
        pos = event.position().y()
        val = self.y_to_val(pos)
        
        target_idx = -1
        for i, (rmin, rmax) in enumerate(self.current_ranges):
            if rmin <= val <= rmax:
                target_idx = i
                break
        
        if target_idx != -1:
            self.open_set_range_dialog(target_idx)
        else:
            self.create_segment(val)


class HeatmapBarWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(50)
        self.setMinimumHeight(150)
        
        self.data_min = 0.0
        self.data_max = 1.0
        # Default to Rainbow (Jet) matching Controller default
        self.set_gradient_name("Rainbow")

    def set_range(self, dmin, dmax):
        self.data_min = dmin
        self.data_max = dmax
        self.update()

    def set_gradient_name(self, name):
        self.gradient = QLinearGradient(0, 0, 0, 1)
        self.gradient.setCoordinateMode(QLinearGradient.CoordinateMode.ObjectMode)

        # Qt Gradient 0.0 is Top (Max Value), 1.0 is Bottom (Min Value)
        # We map High Values (Top) to Low Values (Bottom)
        
        if name == "Viridis":
            # High(Yellow) -> Low(Purple)
            self.gradient.setColorAt(0.0, QColor(253, 231, 37))
            self.gradient.setColorAt(0.5, QColor(33, 145, 140))
            self.gradient.setColorAt(1.0, QColor(68, 1, 84))
        elif name == "Hot":
            # High(White) -> Low(Black)
            self.gradient.setColorAt(0.0, QColor("white"))
            self.gradient.setColorAt(0.33, QColor("yellow"))
            self.gradient.setColorAt(0.66, QColor("red"))
            self.gradient.setColorAt(1.0, QColor("black"))
        elif name == "Blue-Red":
            # High(Red) -> Low(Blue)
            self.gradient.setColorAt(0.0, QColor("red"))
            self.gradient.setColorAt(0.5, QColor("white"))
            self.gradient.setColorAt(1.0, QColor("blue"))
        elif name == "Plasma":
            # High(Yellow) -> Low(Purple/Blue)
            self.gradient.setColorAt(0.0, QColor(240, 249, 33))
            self.gradient.setColorAt(0.5, QColor(204, 71, 120))
            self.gradient.setColorAt(1.0, QColor(13, 8, 135))
        elif name == "Magma":
            # High(White/Yellow) -> Low(Black)
            self.gradient.setColorAt(0.0, QColor(252, 253, 191))
            self.gradient.setColorAt(0.5, QColor(183, 55, 121))
            self.gradient.setColorAt(1.0, QColor(0, 0, 4))
        else: 
            # Rainbow (Jet): High(Red) -> Low(Blue)
            self.gradient.setColorAt(0.0, QColor(255, 0, 0))
            self.gradient.setColorAt(0.25, QColor(255, 255, 0))
            self.gradient.setColorAt(0.5, QColor(0, 255, 0))
            self.gradient.setColorAt(0.75, QColor(0, 255, 255))
            self.gradient.setColorAt(1.0, QColor(0, 0, 255))
        
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        w = self.width()
        h = self.height()
        
        margin_top = 20
        margin_bottom = 20
        bar_w = 15
        bar_x = (w - bar_w) // 2
        bar_h = h - margin_top - margin_bottom
        
        painter.setPen(Qt.GlobalColor.black)
        font = painter.font()
        font.setPointSize(7)
        painter.setFont(font)
        
        # Draw Max at Top
        painter.drawText(QRectF(0, 0, w, margin_top), Qt.AlignmentFlag.AlignCenter, f"{self.data_max:.1e}")
        # Draw Min at Bottom
        painter.drawText(QRectF(0, h - margin_bottom, w, margin_bottom), Qt.AlignmentFlag.AlignCenter, f"{self.data_min:.1e}")
        
        painter.setBrush(QBrush(self.gradient))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRect(QRectF(bar_x, margin_top, bar_w, bar_h))


class EditableLabel(QWidget):
    """
    A label that turns into a QLineEdit when clicked.
    Emits valueChanged(text) when editing finishes (enter/loss of focus).
    Reverts on Escape.
    Supports a display prefix (e.g. "Step: ") that is hidden during editing.
    """
    valueChanged = pyqtSignal(str)

    def __init__(self, text="", prefix="", parent=None):
        super().__init__(parent)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)
        self.layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.prefix = prefix
        self.raw_value = text # The value without prefix
        self.display_text = f"{self.prefix}{self.raw_value}"

        self.label = QLabel(self.display_text)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setCursor(Qt.CursorShape.PointingHandCursor)
        
        self.edit = QLineEdit(self.raw_value)
        self.edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.edit.hide()
        
        # Install event filter or subclass? Subclassing QLineEdit is cleaner for key press
        self.edit.installEventFilter(self)
        self.edit.editingFinished.connect(self._on_editing_finished)

        self.layout.addWidget(self.label)
        self.layout.addWidget(self.edit)
        
        # Prevent layout jump by enforcing minimum height based on the line edit
        self.setMinimumHeight(self.edit.sizeHint().height())

    def mousePressEvent(self, event):
        if self.label.isVisible():
            self._start_editing()

    def eventFilter(self, obj, event):
        if obj == self.edit and event.type() == event.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape:
                self._cancel_editing()
                return True
        return super().eventFilter(obj, event)

    def _start_editing(self):
        self.label.hide()
        self.edit.show()
        self.edit.setText(self.raw_value)
        self.edit.selectAll()
        self.edit.setFocus()

    def _cancel_editing(self):
        self.edit.hide()
        self.label.show()
        # Reset edit text to current valid value
        self.edit.setText(self.raw_value)

    def _on_editing_finished(self):
        # If hidden, we probably cancelled already
        if not self.edit.isVisible(): return
        
        new_val = self.edit.text()
        self.edit.hide()
        self.label.show()
        
        if new_val != self.raw_value:
            self.raw_value = new_val
            self.display_text = f"{self.prefix}{self.raw_value}"
            self.label.setText(self.display_text)
            self.valueChanged.emit(new_val)
    
    def setText(self, text):
        self.raw_value = text
        self.display_text = f"{self.prefix}{self.raw_value}"
        self.label.setText(self.display_text)
        self.edit.setText(text) # Prepare edit for next time
    
    @property
    def text_value(self):
        return self.raw_value


class PlayerControlWidget(QWidget):
    stepChanged = pyqtSignal(int)
    playToggled = pyqtSignal(bool)
    fpsChanged = pyqtSignal(int)
    autoReplayToggled = pyqtSignal(bool)
    rangeRequested = pyqtSignal(float, float)
    jumpToStepRequested = pyqtSignal(float)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(5, 5, 5, 5)
        main_layout.setSpacing(10)
        
        # Nav Buttons container (Grouped tightly)
        nav_container = QWidget()
        nav_layout = QHBoxLayout(nav_container)
        nav_layout.setContentsMargins(0,0,0,0)
        nav_layout.setSpacing(0)
        
        self.btn_first = QPushButton("<<")
        self.btn_prev = QPushButton("<")
        self.btn_play = QPushButton("▶")
        self.btn_next = QPushButton(">")
        self.btn_last = QPushButton(">>")
        
        for btn in [self.btn_first, self.btn_prev, self.btn_play, self.btn_next, self.btn_last]:
            btn.setFixedWidth(28)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            nav_layout.addWidget(btn)
            
        main_layout.addWidget(nav_container)
            
        self.btn_play.setCheckable(True)
        self.btn_play.toggled.connect(self._on_play_toggled)
        
        # FPS Control
        lbl_fps = QLabel("FPS:")
        main_layout.addWidget(lbl_fps)
        
        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(1, 60)
        self.fps_spin.setValue(10)
        self.fps_spin.setFixedWidth(50)
        # Styling for smaller arrows
        self.fps_spin.setStyleSheet("""
            QSpinBox::up-button, QSpinBox::down-button { width: 12px; } 
        """) 
        self.fps_spin.valueChanged.connect(self.fpsChanged.emit)
        main_layout.addWidget(self.fps_spin)

        # Replay Button (Separate)
        self.btn_replay = QPushButton("↻")
        self.btn_replay.setFixedWidth(28)
        self.btn_replay.setCheckable(True)
        self.btn_replay.setToolTip("Auto-Replay Loop")
        self.btn_replay.toggled.connect(self.autoReplayToggled.emit)
        main_layout.addWidget(self.btn_replay)
        
        # Slider & Label Stack
        slider_container = QWidget()
        slider_layout = QVBoxLayout(slider_container)
        slider_layout.setContentsMargins(0, 0, 0, 0)
        slider_layout.setSpacing(2)
        
        slider_row = QWidget()
        row_layout = QHBoxLayout(slider_row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        
        self.min_lbl = EditableLabel("0")
        self.min_lbl.setFixedWidth(60)
        self.min_lbl.valueChanged.connect(self._on_range_edited)
        
        self.max_lbl = EditableLabel("100")
        self.max_lbl.setFixedWidth(60)
        self.max_lbl.valueChanged.connect(self._on_range_edited)
        
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.valueChanged.connect(self._on_slider_change)
        
        row_layout.addWidget(self.min_lbl)
        row_layout.addWidget(self.slider)
        row_layout.addWidget(self.max_lbl)
        
        slider_layout.addWidget(slider_row)
        
        # Step Label Container
        # Simplified: Use a single EditableLabel with prefix
        self.step_val_lbl = EditableLabel("N/A", prefix="Step: ")
        self.step_val_lbl.label.setStyleSheet("font-weight: bold; color: #444;")
        # No Fixed Width to allow centering, or fixed if we want stability
        # The user complained about it jumping to right. 
        # Using a layout with alignment center should keep it centered.
        # But if we want it strictly centered under the slider, let's just add it to layout
        # with center alignment.
        self.step_val_lbl.setFixedWidth(120) # Enough for "Step: 1000000"
        self.step_val_lbl.valueChanged.connect(self._on_step_val_edited)
        
        step_wrapper = QWidget()
        step_wrap_layout = QHBoxLayout(step_wrapper)
        step_wrap_layout.setContentsMargins(0,0,0,0)
        step_wrap_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        step_wrap_layout.addWidget(self.step_val_lbl)
        
        slider_layout.addWidget(step_wrapper)
        
        main_layout.addWidget(slider_container, 1)

        self.btn_first.clicked.connect(self._on_first_clicked)
        self.btn_prev.clicked.connect(lambda: self.set_step_index(self.slider.value() - 1))
        self.btn_next.clicked.connect(lambda: self.set_step_index(self.slider.value() + 1))
        self.btn_last.clicked.connect(self._on_last_clicked)
        
        self.timesteps = []

    def _on_range_edited(self, text):
        try:
            min_val = float(self.min_lbl.text_value)
            max_val = float(self.max_lbl.text_value)
            self.rangeRequested.emit(min_val, max_val)
        except ValueError:
            # Revert to current valid values
            if self.timesteps:
                self.min_lbl.setText(str(self.timesteps[0]))
                self.max_lbl.setText(str(self.timesteps[-1]))

    def _on_step_val_edited(self, text):
        try:
            val = float(text)
            self.jumpToStepRequested.emit(val)
        except ValueError:
            # Revert to current slider value if invalid
            self._update_step_label()

    def set_timesteps(self, steps: list):
        self.timesteps = steps
        if not steps:
            self.slider.setEnabled(False)
            self.step_val_lbl.setText("N/A")
            return
        
        self.slider.setEnabled(True)
        self.slider.setRange(0, len(steps) - 1)
        
        # Update text only if different to avoid loop if possible, 
        # but EditableLabel.setText updates internal value too.
        # This overwrites user input with valid ranges from controller.
        self.min_lbl.setText(str(steps[0]))
        self.max_lbl.setText(str(steps[-1]))
        
        if self.slider.value() >= len(steps):
            self.slider.blockSignals(True)
            self.slider.setValue(0)
            self.slider.blockSignals(False)
            
        self._update_step_label()

    def set_step_index(self, idx):
        if not self.timesteps: return
        idx = max(0, min(idx, len(self.timesteps)-1))
        self.slider.setValue(idx)
        # Force label update even if slider didn't move (e.g. reverting invalid text input)
        self._update_step_label()

    def _on_first_clicked(self):
        if self.btn_play.isChecked():
            self.btn_play.setChecked(False)
        self.set_step_index(0)

    def _on_last_clicked(self):
        if self.btn_play.isChecked():
            self.btn_play.setChecked(False)
        self.set_step_index(self.slider.maximum())

    def _on_slider_change(self, val):
        self._update_step_label()
        if self.timesteps and 0 <= val < len(self.timesteps):
            self.stepChanged.emit(val)

    def _update_step_label(self):
        if self.timesteps:
            val = self.timesteps[self.slider.value()]
            self.step_val_lbl.setText(f"{val}")
        else:
            self.step_val_lbl.setText("N/A")

    def _on_play_toggled(self, checked):
        # Allow jumping to start/end even while playing
        self.btn_first.setEnabled(True) 
        self.btn_last.setEnabled(True)
        
        self.btn_prev.setEnabled(not checked)
        self.btn_next.setEnabled(not checked)
        
        if checked:
            self.btn_play.setText("||")
        else:
            self.btn_play.setText("▶")
            
        self.playToggled.emit(checked)