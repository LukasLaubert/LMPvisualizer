import os
import io
import sys
import shutil
import tempfile
import subprocess
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QComboBox,
    QSpinBox, QCheckBox, QPushButton, QLabel, QFileDialog, QMessageBox,
    QProgressDialog, QApplication
)
from PyQt6.QtCore import Qt, QBuffer, QIODevice
from PIL import Image, ImageOps

class VideoExportDialog(QDialog):
    def __init__(self, plot_panel, parent=None):
        super().__init__(parent or plot_panel)
        self.plot_panel = plot_panel
        self.controller = plot_panel.controller
        self.plot_widget = plot_panel.plot_widget
        self.timesteps = self.controller.timesteps if hasattr(self.controller, 'timesteps') else []
        
        self.setWindowTitle("Video / GIF Export")
        self.resize(450, 320)
        
        self.setStyleSheet("""
            QDialog { 
                background-color: #f5f5f5; 
            }
            QLabel {
                font-size: 12px;
                color: #333333;
            }
            QComboBox {
                padding: 4px;
                border: 1px solid #cccccc;
                border-radius: 4px;
                background-color: white;
            }
            QSpinBox {
                padding: 4px;
                padding-right: 20px;
                border: 1px solid #cccccc;
                border-radius: 4px;
                background-color: white;
                height: 20px;
            }
            QSpinBox::up-button {
                subcontrol-origin: border;
                subcontrol-position: top right;
                width: 16px;
                height: 15px;
                border-left: 1px solid #cccccc;
                border-bottom: 1px solid #cccccc;
                border-top-right-radius: 4px;
                background-color: #e0e0e0;
            }
            QSpinBox::up-button:hover {
                background-color: #d5d5d5;
            }
            QSpinBox::up-button:pressed {
                background-color: #c5c5c5;
            }
            QSpinBox::down-button {
                subcontrol-origin: border;
                subcontrol-position: bottom right;
                width: 16px;
                height: 15px;
                border-left: 1px solid #cccccc;
                border-bottom-right-radius: 4px;
                background-color: #e0e0e0;
            }
            QSpinBox::down-button:hover {
                background-color: #d5d5d5;
            }
            QSpinBox::down-button:pressed {
                background-color: #c5c5c5;
            }
            QCheckBox {
                spacing: 8px;
            }
        """)
        
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)
        
        # Title/Header
        header = QLabel("Configure Video / GIF Export")
        font = header.font()
        font.setPointSize(12)
        font.setBold(True)
        header.setFont(font)
        layout.addWidget(header)
        
        # Form Layout
        form_layout = QFormLayout()
        form_layout.setSpacing(10)
        
        # Format Dropdown
        self.format_combo = QComboBox()
        self.format_combo.addItems([
            "MP4 (H.264)",
            "AVI (MJPEG)",
            "MOV (H.264)",
            "Animated WebP",
            "Animated GIF"
        ])
        form_layout.addRow("Format:", self.format_combo)
        
        # FPS SpinBox
        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(1, 60)
        
        # Default value from the panel if it exists
        default_fps = 10
        if hasattr(self.plot_panel, 'player_controls') and hasattr(self.plot_panel.player_controls, 'fps_spin'):
            default_fps = self.plot_panel.player_controls.fps_spin.value()
        self.fps_spin.setValue(default_fps)
        form_layout.addRow("Framerate (FPS):", self.fps_spin)
        
        # Frequency (Skip frames) SpinBox
        self.freq_spin = QSpinBox()
        self.freq_spin.setRange(1, 10000)
        self.freq_spin.setValue(1)
        self.freq_spin.setSuffix(" (every Nth frame)")
        form_layout.addRow("Frame Frequency:", self.freq_spin)
        
        # Step Range layout
        range_layout = QHBoxLayout()
        self.start_spin = QSpinBox()
        self.end_spin = QSpinBox()
        
        num_steps = len(self.timesteps)
        max_idx = max(0, num_steps - 1)
        
        self.start_spin.setRange(0, max_idx)
        self.end_spin.setRange(0, max_idx)
        
        # Set default values to current step and last step
        current_step_idx = 0
        if hasattr(self.plot_panel, 'player_controls') and hasattr(self.plot_panel.player_controls, 'slider'):
            current_step_idx = self.plot_panel.player_controls.slider.value()
        elif hasattr(self.controller, 'current_timestep') and self.controller.current_timestep in self.timesteps:
            current_step_idx = self.timesteps.index(self.controller.current_timestep)
            
        self.start_spin.setValue(0)
        self.end_spin.setValue(max_idx)
        
        self.start_label = QLabel()
        self.end_label = QLabel()
        
        range_layout.addWidget(self.start_spin)
        range_layout.addWidget(self.start_label)
        range_layout.addWidget(QLabel("to"))
        range_layout.addWidget(self.end_spin)
        range_layout.addWidget(self.end_label)
        
        form_layout.addRow("Timestep Range:", range_layout)
        
        # Visual Overrides
        self.axes_cb = QCheckBox("Hide Axes and Labels")
        self.axes_cb.setChecked(False) # default unchecked
        self.grid_cb = QCheckBox("Hide Gridlines")
        self.grid_cb.setChecked(False)  # default unchecked
        self.crop_cb = QCheckBox("Auto-Crop White Margins")
        self.crop_cb.setChecked(False) # default unchecked; user can enable if desired
        
        # Connect crop toggle to auto-disable axes and grid
        self.crop_cb.toggled.connect(self._on_crop_toggled)
        
        form_layout.addRow("", self.axes_cb)
        form_layout.addRow("", self.grid_cb)
        form_layout.addRow("", self.crop_cb)
        
        layout.addLayout(form_layout)
        
        # Action Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)
        
        # Bottom-left Duration indicator
        self.duration_label = QLabel("Duration: 0.0s (0 frames)")
        self.duration_label.setStyleSheet("color: #555555; font-size: 11px; font-weight: bold;")
        btn_layout.addWidget(self.duration_label)
        
        self.btn_export = QPushButton("Export...")
        self.btn_export.setStyleSheet("""
            QPushButton {
                font-weight: bold; 
                background-color: #0078d7; 
                color: white; 
                padding: 6px 14px;
                border: none;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #1086e6;
            }
            QPushButton:pressed {
                background-color: #006cc1;
            }
        """)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setStyleSheet("""
            QPushButton {
                padding: 6px 14px;
                border: 1px solid #cccccc;
                border-radius: 4px;
                background-color: white;
            }
            QPushButton:hover {
                background-color: #f0f0f0;
            }
        """)
        
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_cancel)
        btn_layout.addWidget(self.btn_export)
        
        layout.addLayout(btn_layout)
        
        # Connections
        self.start_spin.valueChanged.connect(self._update_step_labels)
        self.end_spin.valueChanged.connect(self._update_step_labels)
        self.fps_spin.valueChanged.connect(self._update_step_labels)
        self.freq_spin.valueChanged.connect(self._update_step_labels)
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_export.clicked.connect(self.run_export)
        
        self._update_step_labels()

    def _on_crop_toggled(self, checked):
        if checked:
            self._prev_axes_checked = self.axes_cb.isChecked()
            self._prev_grid_checked = self.grid_cb.isChecked()
            self.axes_cb.setChecked(True)
            self.grid_cb.setChecked(True)
        else:
            if hasattr(self, '_prev_axes_checked'):
                self.axes_cb.setChecked(self._prev_axes_checked)
            if hasattr(self, '_prev_grid_checked'):
                self.grid_cb.setChecked(self._prev_grid_checked)
        self.axes_cb.setEnabled(not checked)
        self.grid_cb.setEnabled(not checked)

    def _update_step_labels(self):
        if not self.timesteps:
            self.start_label.setText("")
            self.end_label.setText("")
            self.duration_label.setText("Duration: 0.0s (0 frames)")
            return
            
        start_idx = self.start_spin.value()
        end_idx = self.end_spin.value()
        fps = self.fps_spin.value()
        freq = self.freq_spin.value()
        
        if 0 <= start_idx < len(self.timesteps):
            self.start_label.setText(f"({int(self.timesteps[start_idx])})")
        if 0 <= end_idx < len(self.timesteps):
            self.end_label.setText(f"({int(self.timesteps[end_idx])})")
            
        # Calculate duration
        if start_idx <= end_idx:
            num_frames = len(list(range(start_idx, end_idx + 1, freq)))
        else:
            num_frames = len(list(range(end_idx, start_idx + 1, freq)))
            
        duration_s = num_frames / fps
        self.duration_label.setText(f"Duration: {duration_s:.1f}s ({num_frames} frames)")

    def run_export(self):
        start_idx = self.start_spin.value()
        end_idx = self.end_spin.value()
        
        if start_idx > end_idx:
            start_idx, end_idx = end_idx, start_idx
            self.start_spin.setValue(start_idx)
            self.end_spin.setValue(end_idx)
            
        fmt = self.format_combo.currentText()
        if fmt.startswith("MP4"):
            ext, filter_str = ".mp4", "MP4 Video (*.mp4)"
        elif fmt.startswith("AVI"):
            ext, filter_str = ".avi", "AVI Video (*.avi)"
        elif fmt.startswith("MOV"):
            ext, filter_str = ".mov", "QuickTime Video (*.mov)"
        elif fmt.startswith("Animated WebP"):
            ext, filter_str = ".webp", "Animated WebP Image (*.webp)"
        else:
            ext, filter_str = ".gif", "Animated GIF Image (*.gif)"
            
        path, _ = QFileDialog.getSaveFileName(self, "Save Video", "", filter_str)
        if not path:
            return
            
        if not path.lower().endswith(ext):
            path += ext
            
        # Run the actual recording and encoding loop
        self.execute_export(path, start_idx, end_idx)

    def execute_export(self, filepath, start_idx, end_idx):
        # 1. Stop playback if playing
        was_playing = False
        if hasattr(self.plot_panel, 'controller') and self.plot_panel.controller is not None:
            if getattr(self.plot_panel.controller, 'is_playing', False):
                was_playing = True
                self.plot_panel.controller.pause()
                if hasattr(self.plot_panel, 'player_controls'):
                    self.plot_panel.player_controls.btn_play.setChecked(False)
                    
        # 2. Hide lock axis button temporarily
        lock_btn_visible = False
        if hasattr(self.plot_panel, 'lock_axes_btn'):
            lock_btn_visible = self.plot_panel.lock_axes_btn.isVisible()
            self.plot_panel.lock_axes_btn.hide()
            
        # 3. Store current visual settings to restore them later
        axes_hidden_by_user = self.axes_cb.isChecked() or self.crop_cb.isChecked()
        grid_hidden_by_user = self.grid_cb.isChecked() or self.crop_cb.isChecked()
        auto_crop = self.crop_cb.isChecked()
        fps = self.fps_spin.value()
        freq = self.freq_spin.value()
        
        plot_item = self.plot_widget.getPlotItem()
        
        original_axes_visibility = {}
        for ax_name in ['left', 'bottom', 'right', 'top']:
            original_axes_visibility[ax_name] = plot_item.getAxis(ax_name).isVisible()
            
        if axes_hidden_by_user:
            for ax_name in ['left', 'bottom', 'right', 'top']:
                plot_item.getAxis(ax_name).setVisible(False)
            
        if grid_hidden_by_user:
            plot_item.showGrid(x=False, y=False)
            
        # Keep track of original timestep index to restore later
        original_step_idx = 0
        if hasattr(self.plot_panel, 'player_controls') and hasattr(self.plot_panel.player_controls, 'slider'):
            original_step_idx = self.plot_panel.player_controls.slider.value()
        elif hasattr(self.controller, 'current_timestep') and self.controller.current_timestep in self.timesteps:
            original_step_idx = self.timesteps.index(self.controller.current_timestep)
            
        # Setup temporary directory for rendering frames
        temp_dir = tempfile.mkdtemp(prefix="lmp_video_")
        
        frames = []
        step_indices = list(range(start_idx, end_idx + 1, freq))
        num_frames = len(step_indices)
        
        # Track global bbox across all frames to ensure uniform cropping
        global_left = None
        global_top = None
        global_right = None
        global_bottom = None
        
        progress = QProgressDialog("Rendering frames...", "Cancel", 0, num_frames, self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setWindowTitle("Export Progress")
        progress.show()
        
        try:
            for idx, step_idx in enumerate(step_indices):
                if progress.wasCanceled():
                    break
                    
                # Update progress dialog
                progress.setValue(idx)
                progress.setLabelText(f"Rendering frame {idx+1}/{num_frames}...")
                QApplication.processEvents()
                
                # Update controller step
                self.controller.set_timestep_index(step_idx)
                if hasattr(self.plot_panel, 'player_controls'):
                    self.plot_panel.player_controls.set_step_index(step_idx)
                
                # Re-apply axis and grid overrides because controller.set_timestep_index() calls 
                # update_scene(), which internally calls setLabel() and automatically shows the axes again!
                if axes_hidden_by_user:
                    for ax_name in ['left', 'bottom', 'right', 'top']:
                        plot_item.getAxis(ax_name).setVisible(False)
                if grid_hidden_by_user:
                    plot_item.showGrid(x=False, y=False)
                
                # Force Qt to flush paint events
                QApplication.processEvents()
                
                # Grab plot canvas
                pixmap = self.plot_widget.grab()
                qimage = pixmap.toImage()
                
                # Convert QImage to PIL Image in memory
                buffer = QBuffer()
                buffer.open(QIODevice.OpenModeFlag.WriteOnly)
                qimage.save(buffer, "PNG")
                pil_img = Image.open(io.BytesIO(buffer.data().data()))
                pil_img.load()
                buffer.close()
                
                # Compute bounding box of current frame and update global bounding box
                if auto_crop:
                    rgb_img = pil_img.convert("RGB")
                    inverted = ImageOps.invert(rgb_img)
                    bbox = inverted.getbbox()
                    if bbox:
                        l, t, r, b = bbox
                        if global_left is None or l < global_left:
                            global_left = l
                        if global_top is None or t < global_top:
                            global_top = t
                        if global_right is None or r > global_right:
                            global_right = r
                        if global_bottom is None or b > global_bottom:
                            global_bottom = b
                            
                frames.append(pil_img)
                
            progress.setValue(num_frames)
            
            # Check if canceled
            if progress.wasCanceled():
                QMessageBox.information(self, "Canceled", "Export was canceled by user.")
                return
                
            # Perform uniform crop across all frames based on global bounding box
            if auto_crop and global_left is not None:
                width = global_right - global_left
                height = global_bottom - global_top
                
                # Adjust global bounding box to ensure even width and height (FFmpeg yuv420p requirement)
                if width % 2 != 0:
                    if global_left > 0:
                        global_left -= 1
                    else:
                        global_right += 1
                if height % 2 != 0:
                    if global_top > 0:
                        global_top -= 1
                    else:
                        global_bottom += 1
                        
                cropped_frames = []
                for img in frames:
                    cropped_img = img.crop((global_left, global_top, global_right, global_bottom))
                    cropped_frames.append(cropped_img)
                frames = cropped_frames
                
            # Save final frames for FFmpeg processing if required
            fmt = self.format_combo.currentText()
            if fmt.startswith("MP4") or fmt.startswith("AVI") or fmt.startswith("MOV"):
                for idx, img in enumerate(frames):
                    # Ensure even width/height for libx264+yuv420p compatibility (MP4/MOV only)
                    if fmt.startswith("MP4") or fmt.startswith("MOV"):
                        w, h = img.size
                        new_w = w + (w % 2)
                        new_h = h + (h % 2)
                        if new_w != w or new_h != h:
                            bg = (255, 255, 255, 255) if 'A' in img.mode else (255, 255, 255)
                            new_img = Image.new(img.mode, (new_w, new_h), bg)
                            new_img.paste(img, (0, 0))
                            img = new_img
                    frame_path = os.path.join(temp_dir, f"frame_{idx:05d}.png")
                    img.save(frame_path, "PNG")
                
            # Compile using Pillow or FFmpeg
            fmt = self.format_combo.currentText()
            if fmt.startswith("Animated WebP"):
                progress.setLabelText("Compiling Animated WebP...")
                QApplication.processEvents()
                frames[0].save(
                    filepath,
                    save_all=True,
                    append_images=frames[1:],
                    duration=int(1000 / fps),
                    loop=0,
                    lossless=True
                )
            elif fmt.startswith("Animated GIF"):
                progress.setLabelText("Compiling Animated GIF...")
                QApplication.processEvents()
                frames[0].save(
                    filepath,
                    save_all=True,
                    append_images=frames[1:],
                    duration=int(1000 / fps),
                    loop=0
                )
            else:
                progress.setLabelText(f"Compiling video via ffmpeg...")
                QApplication.processEvents()
                
                # Construct FFmpeg command
                if fmt.startswith("MP4"):
                    cmd = [
                        'ffmpeg', '-y', '-r', str(fps),
                        '-i', os.path.join(temp_dir, 'frame_%05d.png'),
                        '-c:v', 'libx264',
                        '-pix_fmt', 'yuv420p',
                        '-crf', '18',
                        filepath
                    ]
                elif fmt.startswith("MOV"):
                    cmd = [
                        'ffmpeg', '-y', '-r', str(fps),
                        '-i', os.path.join(temp_dir, 'frame_%05d.png'),
                        '-c:v', 'libx264',
                        '-pix_fmt', 'yuv420p',
                        '-crf', '18',
                        filepath
                    ]
                else: # AVI (MJPEG)
                    cmd = [
                        'ffmpeg', '-y', '-r', str(fps),
                        '-i', os.path.join(temp_dir, 'frame_%05d.png'),
                        '-c:v', 'mjpeg',
                        '-q:v', '2',
                        filepath
                    ]
                    
                # Run FFmpeg in the background with hidden console window on Windows
                startupinfo = None
                if sys.platform == 'win32':
                    startupinfo = subprocess.STARTUPINFO()
                    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    startupinfo.wShowWindow = subprocess.SW_HIDE
                    
                result = subprocess.run(cmd, startupinfo=startupinfo, capture_output=True, text=True)
                
                if result.returncode != 0:
                    raise Exception(f"FFmpeg failed with exit code {result.returncode}:\n{result.stderr}")
                    
            QMessageBox.information(self, "Success", f"Successfully exported animation to:\n{filepath}")
            self.accept()
            
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"An error occurred during export:\n{str(e)}")
            
        finally:
            # 4. Cleanup
            # Delete the temp folder
            shutil.rmtree(temp_dir, ignore_errors=True)
            
            # Restore visual overrides
            if axes_hidden_by_user:
                for ax_name, visible in original_axes_visibility.items():
                    plot_item.getAxis(ax_name).setVisible(visible)
            if grid_hidden_by_user:
                plot_item.showGrid(x=True, y=True, alpha=0.3)
                
            # Restore lock axes button
            if lock_btn_visible:
                self.plot_panel.lock_axes_btn.show()
                
            # Restore original timestep index
            self.controller.set_timestep_index(original_step_idx)
            if hasattr(self.plot_panel, 'player_controls'):
                self.plot_panel.player_controls.set_step_index(original_step_idx)
                
            # If was playing, resume playing
            if was_playing:
                self.controller.play()
                if hasattr(self.plot_panel, 'player_controls'):
                    self.plot_panel.player_controls.btn_play.setChecked(True)
