# lmp_visualizer/trj_widgets.py

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSlider, QPushButton, 
    QSpinBox, QDoubleSpinBox, QDialog, QFormLayout, QDialogButtonBox,
    QFrame, QStyle
)
from PyQt6.QtCore import Qt, pyqtSignal, QRectF
from PyQt6.QtGui import QPainter, QBrush, QColor, QLinearGradient, QMouseEvent, QFont

class FilterBarWidget(QWidget):
    """
    Vertical bar widget for filtering a range (min/max).
    Includes logic to maintain relative handle positions when data range changes.
    """
    rangeChanged = pyqtSignal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(40) # Shrunk width as requested
        self.setMinimumHeight(150)

        self.data_min = 0.0
        self.data_max = 100.0
        self.current_min = 0.0
        self.current_max = 100.0

        self.hover_handle = None 
        self.dragging = None
        self.drag_start_y = 0
        self.drag_start_vals = (0.0, 0.0)

        self.bar_width = 14
        self.margin_top = 20
        self.margin_bottom = 20
        self.bar_x = (40 - self.bar_width) // 2

        self.setMouseTracking(True)

    def set_data_range(self, dmin, dmax):
        if dmin > dmax: dmin, dmax = dmax, dmin
        if dmin == dmax: dmax += 0.001
        
        old_range = self.data_max - self.data_min
        if old_range == 0: old_range = 1.0
        
        rel_min = (self.current_min - self.data_min) / old_range
        rel_max = (self.current_max - self.data_min) / old_range
        
        rel_min = max(0.0, min(1.0, rel_min))
        rel_max = max(0.0, min(1.0, rel_max))
        
        self.data_min = dmin
        self.data_max = dmax
        new_range = dmax - dmin
        
        self.current_min = self.data_min + (rel_min * new_range)
        self.current_max = self.data_min + (rel_max * new_range)
        
        self.update()

    def set_current_range(self, cmin, cmax):
        self.current_min = max(self.data_min, min(self.data_max, cmin))
        self.current_max = max(self.data_min, min(self.data_max, cmax))
        self.update()

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

        y_high = self.val_to_y(self.current_max)
        y_low = self.val_to_y(self.current_min)
        
        range_rect = QRectF(self.bar_x, y_high, self.bar_width, y_low - y_high)
        painter.setBrush(QColor(255, 150, 150))
        painter.drawRect(range_rect)

        handle_h = 6
        handle_w = self.bar_width + 6
        handle_x = self.bar_x - 3
        
        col_top = QColor(0, 120, 240) if self.hover_handle == 'top' else QColor(100, 100, 100)
        painter.setBrush(col_top)
        painter.drawRoundedRect(QRectF(handle_x, y_high - handle_h/2, handle_w, handle_h), 2, 2)
        
        col_bot = QColor(0, 120, 240) if self.hover_handle == 'bottom' else QColor(100, 100, 100)
        painter.setBrush(col_bot)
        painter.drawRoundedRect(QRectF(handle_x, y_low - handle_h/2, handle_w, handle_h), 2, 2)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and self.hover_handle:
            self.dragging = self.hover_handle
            self.drag_start_y = event.position().y()
            self.drag_start_vals = (self.current_min, self.current_max)

    def mouseMoveEvent(self, event: QMouseEvent):
        y = event.position().y()
        y_high = self.val_to_y(self.current_max)
        y_low = self.val_to_y(self.current_min)
        tol = 8

        if not self.dragging:
            if abs(y - y_high) < tol:
                self.hover_handle = 'top'
                self.setCursor(Qt.CursorShape.SizeVerCursor)
            elif abs(y - y_low) < tol:
                self.hover_handle = 'bottom'
                self.setCursor(Qt.CursorShape.SizeVerCursor)
            elif y_high < y < y_low:
                self.hover_handle = 'center'
                self.setCursor(Qt.CursorShape.SizeAllCursor)
            else:
                self.hover_handle = None
                self.setCursor(Qt.CursorShape.ArrowCursor)
            self.update()
        else:
            delta_px = y - self.drag_start_y
            
            if self.dragging == 'top':
                val = self.y_to_val(y)
                val = max(self.current_min, val)
                self.current_max = val
                self.rangeChanged.emit(self.current_min, self.current_max)

            elif self.dragging == 'bottom':
                val = self.y_to_val(y)
                val = min(self.current_max, val)
                self.current_min = val
                self.rangeChanged.emit(self.current_min, self.current_max)

            elif self.dragging == 'center':
                start_min, start_max = self.drag_start_vals
                diff = start_max - start_min
                start_y_min = self.val_to_y(start_min)
                target_y_min = start_y_min + delta_px
                new_min = self.y_to_val(target_y_min)
                
                if new_min < self.data_min: new_min = self.data_min
                if new_min + diff > self.data_max: new_min = self.data_max - diff
                
                self.current_min = new_min
                self.current_max = new_min + diff
                self.rangeChanged.emit(self.current_min, self.current_max)
            
            self.update()

    def mouseReleaseEvent(self, event):
        self.dragging = None
        self.update()

    def mouseDoubleClickEvent(self, event):
        dialog = QDialog(self)
        dialog.setWindowTitle("Set Filter Range")
        layout = QFormLayout(dialog)
        
        sp_min = QDoubleSpinBox()
        sp_min.setRange(-1e12, 1e12)
        sp_min.setDecimals(4)
        sp_min.setValue(self.current_min)
        
        sp_max = QDoubleSpinBox()
        sp_max.setRange(-1e12, 1e12)
        sp_max.setDecimals(4)
        sp_max.setValue(self.current_max)
        
        layout.addRow("Min:", sp_min)
        layout.addRow("Max:", sp_max)
        
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dialog.accept)
        btns.rejected.connect(dialog.reject)
        layout.addWidget(btns)
        
        if dialog.exec():
            v1 = sp_min.value()
            v2 = sp_max.value()
            if v1 > v2: v1, v2 = v2, v1
            self.set_current_range(v1, v2)
            self.rangeChanged.emit(self.current_min, self.current_max)


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


class PlayerControlWidget(QWidget):
    stepChanged = pyqtSignal(int)
    playToggled = pyqtSignal(bool)
    fpsChanged = pyqtSignal(int)
    autoReplayToggled = pyqtSignal(bool)
    
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
        
        self.min_lbl = QLabel("0")
        self.max_lbl = QLabel("100")
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.valueChanged.connect(self._on_slider_change)
        
        row_layout.addWidget(self.min_lbl)
        row_layout.addWidget(self.slider)
        row_layout.addWidget(self.max_lbl)
        
        slider_layout.addWidget(slider_row)
        
        self.step_lbl = QLabel("Step: N/A")
        self.step_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.step_lbl.setStyleSheet("font-weight: bold; color: #444;")
        slider_layout.addWidget(self.step_lbl)
        
        main_layout.addWidget(slider_container, 1)

        self.btn_first.clicked.connect(lambda: self.set_step_index(0))
        self.btn_prev.clicked.connect(lambda: self.set_step_index(self.slider.value() - 1))
        self.btn_next.clicked.connect(lambda: self.set_step_index(self.slider.value() + 1))
        self.btn_last.clicked.connect(lambda: self.set_step_index(self.slider.maximum()))
        
        self.timesteps = []

    def set_timesteps(self, steps: list):
        self.timesteps = steps
        if not steps:
            self.slider.setEnabled(False)
            self.step_lbl.setText("Step: N/A")
            return
        
        self.slider.setEnabled(True)
        self.slider.setRange(0, len(steps) - 1)
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

    def _on_slider_change(self, val):
        self._update_step_label()
        if self.timesteps and 0 <= val < len(self.timesteps):
            self.stepChanged.emit(val)

    def _update_step_label(self):
        if self.timesteps:
            val = self.timesteps[self.slider.value()]
            self.step_lbl.setText(f"Step: {val}")

    def _on_play_toggled(self, checked):
        self.btn_first.setEnabled(not checked)
        self.btn_prev.setEnabled(not checked)
        self.btn_next.setEnabled(not checked)
        self.btn_last.setEnabled(not checked)
        
        if checked:
            self.btn_play.setText("||")
        else:
            self.btn_play.setText("▶")
            
        self.playToggled.emit(checked)