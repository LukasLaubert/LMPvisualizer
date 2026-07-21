# lmp_visualizer/trj_controller.py

import pyqtgraph as pg
import numpy as np
import pandas as pd
import time
from PyQt6.QtCore import QTimer, QObject, pyqtSignal
from PyQt6.QtGui import QColor, QBrush
from trj_data_manager import TrjDataManager

class TrjController(QObject):
    """
    Manages the visualization logic for Trajectory Plot Mode.
    Handles data fetching, filtering, complex coloring (Heatmaps), and playback.
    """
    frameChanged = pyqtSignal(int)      # Current timestep index changed
    boundsChanged = pyqtSignal(dict)    # Min/Max values updated (for bars)
    playbackStopped = pyqtSignal()      # Emitted when playback stops (e.g. end of loop)

    def __init__(self, plot_widget: pg.PlotWidget, data_manager: TrjDataManager):
        super().__init__()
        self.widget = plot_widget
        self.data_manager = data_manager
        
        # Setup Plot Item
        self.plot_item = self.widget.getPlotItem()
        self.plot_item.showGrid(x=True, y=True, alpha=0.3)
        self.plot_item.setLabel('left', '')
        self.plot_item.setLabel('bottom', '')
        self.plot_item.setAspectLocked(False) 
        
        # Scatter Item
        self.scatter = pg.ScatterPlotItem(pxMode=True)
        self.plot_item.addItem(self.scatter)
        
        # Playback State
        self.timer = QTimer()
        self.timer.timeout.connect(self._on_timer_tick)
        self.is_playing = False
        self.auto_replay = False
        self.fps = 10
        self.playback_start_time = 0.0
        self.playback_start_index = 0
        
        # Current State Config
        self.current_study = None
        self.current_system = None
        self.current_timestep = 0
        self.timesteps = []
        
        # Cache for "Initial" and "Final" Reference Frames used in Heatmaps
        # Structure: { 'initial': DataFrame, 'final': DataFrame }
        self._ref_cache = {} 
        
        # Store last rendered colors for Export/PopOut
        self.last_render_colors = []
        self.last_render_brushes = []
        
        # Cache for View Config (set by Panel)
        self.view_config = {}
        
        # Store active data for export
        self.active_df = None

    def set_active_system(self, study: str, system: str):
        """Called when Study/System selection changes."""
        # Only reset if actually changing system
        if self.current_study != study or self.current_system != system:
            
            is_study_change = (self.current_study != study)
            
            self.current_study = study
            self.current_system = system
            self.timesteps = self.data_manager.get_timesteps(study, system)
            
            # Reset Cache
            self._ref_cache = {}
            
            # Pre-load Initial Frame (Step 0) for caching immediately if available
            if self.timesteps:
                df_init, _ = self.data_manager.get_frame(study, system, self.timesteps[0])
                if df_init is not None:
                    self._ref_cache['initial'] = df_init

            # Handle Playback and Step Reset Logic
            if is_study_change:
                # 1. Study Change: Reset everything
                if self.is_playing:
                    self.pause()
                    self.playbackStopped.emit()
                
                self.current_timestep = self.timesteps[0] if self.timesteps else 0
            else:
                # 2. System Change (Same Study): Keep state
                # Try to keep current timestep
                if self.current_timestep not in self.timesteps:
                    if self.timesteps:
                        # Find closest or just fallback to 0
                        # For simplicity, if exact step not found, go to 0, or maybe clamp?
                        # User said: "keep the current step... if play is active... let it run"
                        # Usually systems in same study have same timesteps. 
                        # If not, we'll fallback to 0 to be safe.
                        self.current_timestep = self.timesteps[0]
                    else:
                        self.current_timestep = 0
                
                # If playing, we need to re-anchor the timer to avoid jumps or index errors
                if self.is_playing and self.timesteps:
                    try:
                        curr_idx = self.timesteps.index(self.current_timestep)
                    except ValueError:
                        curr_idx = 0
                    
                    self.playback_start_index = curr_idx
                    self.playback_start_time = time.time()

            # Reset view limits to auto only on system change
            self.plot_item.autoRange()

    def get_available_timesteps(self):
        return self.timesteps

    def set_timestep_index(self, idx: int):
        """Sets current step by index (from slider)."""
        if 0 <= idx < len(self.timesteps):
            self.current_timestep = self.timesteps[idx]
            self.update_scene()

    def set_fps(self, fps: int):
        self.fps = max(1, fps)
        if self.is_playing:
            self.timer.setInterval(int(1000 / self.fps))

    def set_auto_replay(self, enabled: bool):
        self.auto_replay = enabled

    def play(self):
        if not self.timesteps: return
        self.is_playing = True
        
        try:
            current_idx = self.timesteps.index(self.current_timestep)
        except ValueError:
            current_idx = 0
            
        if current_idx >= len(self.timesteps) - 1:
            current_idx = 0 # Restart if at end
            
        self.playback_start_index = current_idx
        self.playback_start_time = time.time()
        
        self.timer.start(int(1000 / self.fps))

    def pause(self):
        self.is_playing = False
        self.timer.stop()

    def _on_timer_tick(self):
        """Handles playback loop with drift correction and auto-replay."""
        if not self.timesteps:
            self.pause()
            self.playbackStopped.emit()
            return

        now = time.time()
        elapsed = now - self.playback_start_time
        
        frames_to_advance = int(elapsed * self.fps)
        target_idx = self.playback_start_index + frames_to_advance
        
        if target_idx >= len(self.timesteps):
            if self.auto_replay:
                # Loop back to start
                # Reset timing base
                self.playback_start_time = now
                self.playback_start_index = 0
                target_idx = 0
            else:
                # Stop at end
                target_idx = len(self.timesteps) - 1
                self.pause()
                self.playbackStopped.emit()
                # Return here to avoid updating to index out of bounds or wrong state
                return
        
        new_step = self.timesteps[target_idx]
        if new_step != self.current_timestep:
            self.current_timestep = new_step
            self.update_scene()
            self.frameChanged.emit(target_idx)

    def update_view_config(self, config: dict):
        """Receives configuration from UI."""
        self.view_config = config
        self.update_scene()

    def update_scene(self):
        """Main rendering pipeline."""
        # 1. Apply View Sync (Aspect Ratio) FIRST
        # If Sync is ON, we lock aspect ratio. If OFF, we unlock.
        is_synced = self.view_config.get('view_sync', False)
        self.plot_item.setAspectLocked(is_synced)

        if not self.timesteps or not self.view_config.get('active', False):
            self.scatter.clear()
            return

        study, system = self.current_study, self.current_system
        ts = self.current_timestep
        
        # 2. Fetch Current Data
        df, box_bounds = self.data_manager.get_frame(study, system, ts)
        if df is None:
            return

        x_col = self.view_config.get('x_col')
        y_col = self.view_config.get('y_col')
        
        if x_col not in df.columns or y_col not in df.columns:
            return

        # 3. Filter Data (Z-Filter)
        mask = np.ones(len(df), dtype=bool)
        z_col = self.view_config.get('z_filter_col')
        
        if z_col and z_col not in ["Select Z-Filter", "No Z-Filter"]:
            z_ref = self.view_config.get('z_filter_ref', 'Current')
            
            # Helper to combine multiple ranges into a mask
            def get_range_mask(series, ranges):
                combined = np.zeros(len(series), dtype=bool)
                if not ranges: # If no ranges, maybe show all or none? Usually means uninitialized filter
                    return np.ones(len(series), dtype=bool) 
                    
                for rmin, rmax in ranges:
                    combined |= (series >= rmin) & (series <= rmax)
                return combined

            if z_ref == 'Current':
                # Dynamic per-step filtering relative to current distribution
                if z_col in df.columns:
                    curr_min = df[z_col].min()
                    curr_max = df[z_col].max()
                    
                    # Update UI with current bounds
                    self.boundsChanged.emit({'z_filter': (curr_min, curr_max)})
                    
                    # Apply relative handle positions to current bounds
                    # z_ranges_rel should be a list of (rel_min, rel_max)
                    rel_ranges = self.view_config.get('z_ranges_rel', [(0.0, 1.0)])
                    
                    abs_ranges = []
                    for rel_min, rel_max in rel_ranges:
                        thresh_min = curr_min + rel_min * (curr_max - curr_min)
                        thresh_max = curr_min + rel_max * (curr_max - curr_min)
                        abs_ranges.append((thresh_min, thresh_max))
                        
                    mask = get_range_mask(df[z_col], abs_ranges)

            else:
                # Initial / Final Logic (Absolute filtering)
                # z_ranges should be a list of (min, max)
                z_ranges = self.view_config.get('z_ranges', [])
                if not z_ranges:
                    # Fallback for single range legacy or default
                    legacy_range = self.view_config.get('z_range', (float('-inf'), float('inf')))
                    z_ranges = [legacy_range]
                
                if z_ref in ['Initial', 'Final']:
                    target_key = 'initial' if z_ref == 'Initial' else 'final'
                    if target_key == 'final' and 'final' not in self._ref_cache:
                        df_final, _ = self.data_manager.get_frame(study, system, self.timesteps[-1])
                        if df_final is not None:
                            self._ref_cache['final'] = df_final
                            
                    ref_df = self._ref_cache.get(target_key)
                    if ref_df is not None and z_col in ref_df.columns and 'id' in ref_df.columns:
                        ref_mask = get_range_mask(ref_df[z_col], z_ranges)
                        valid_ids = ref_df.loc[ref_mask, 'id'].values
                        if 'id' in df.columns:
                            mask = df['id'].isin(valid_ids)
        
        df_filtered = df[mask]
        self.active_df = df_filtered 
        if df_filtered.empty:
            self.scatter.clear()
            return

        x_data = df_filtered[x_col].values
        y_data = df_filtered[y_col].values
        
        # 4. Coloring
        brushes = []
        heatmap_col = self.view_config.get('heatmap_col')
        
        if heatmap_col and heatmap_col not in ["Select Heatmap", "No Heatmap"]:
            ref_type = self.view_config.get('heatmap_ref', 'Current')
            h_min, h_max = self._get_bounds(study, system, heatmap_col, ref_type)
            self.boundsChanged.emit({'heatmap': (h_min, h_max)})
            
            vals = None
            if ref_type in ['Initial', 'Final']:
                target_key = 'initial' if ref_type == 'Initial' else 'final'
                if target_key == 'final' and 'final' not in self._ref_cache:
                    df_final, _ = self.data_manager.get_frame(study, system, self.timesteps[-1])
                    if df_final is not None:
                        self._ref_cache['final'] = df_final
                
                ref_df = self._ref_cache.get(target_key)
                if ref_df is not None and 'id' in ref_df.columns and 'id' in df_filtered.columns:
                    if heatmap_col in ref_df.columns:
                        ref_map = ref_df.set_index('id', drop=False)[heatmap_col]
                        vals = df_filtered['id'].map(ref_map).values
                        vals = np.nan_to_num(vals, nan=h_min)
                    else:
                        vals = df_filtered[heatmap_col].values
                else:
                    vals = df_filtered[heatmap_col].values
            else:
                if heatmap_col in df_filtered.columns:
                    vals = df_filtered[heatmap_col].values
                else:
                    vals = np.zeros(len(df_filtered))

            if h_max > h_min:
                norm = (vals - h_min) / (h_max - h_min)
            else:
                norm = np.zeros_like(vals)
            
            norm = np.clip(norm, 0.0, 1.0)
            
            grad_name = self.view_config.get('heatmap_gradient', 'Rainbow')
            lut = self._generate_lut(grad_name)
            
            indices = (norm * 255).astype(int)
            brushes = lut[indices]
            self.last_render_brushes = brushes
            
        else:
            c = self.view_config.get('color', QColor('black'))
            b = pg.mkBrush(c)
            brushes = [b] * len(df_filtered)
            self.last_render_brushes = brushes

        # 5. Draw
        size = int(self.view_config.get('size', 5))
        symbol = self.view_config.get('symbol', 'o')
        
        self.scatter.setData(
            x=x_data, 
            y=y_data, 
            brush=brushes,
            size=size, 
            symbol=symbol,
            pen=None
        )
        
        x_lbl = self.view_config.get('x_label', x_col)
        y_lbl = self.view_config.get('y_label', y_col)
        self.plot_item.setLabel('bottom', x_lbl)
        self.plot_item.setLabel('left', y_lbl)

        # 6. Auto-Fit Logic
        # If view_lock is TRUE, we do NOT touch the range.
        # If view_lock is FALSE (Unlocked), we call autoRange to fit data.
        if not self.view_config.get('view_lock', True):
            self.plot_item.autoRange()

    def get_scope_min_max(self, study, system, col, ref_type):
        """
        Calculates the data range for the Z-Filter based on the reference type.
        Used by the UI to set the Filter Bar range.
        """
        # Initial Frame Min/Max
        if ref_type == 'Initial':
            if 'initial' not in self._ref_cache:
                if self.timesteps:
                    df, _ = self.data_manager.get_frame(study, system, self.timesteps[0])
                    self._ref_cache['initial'] = df
            
            df = self._ref_cache.get('initial')
            if df is not None and col in df.columns:
                return df[col].min(), df[col].max()
                
        # Final Frame Min/Max
        elif ref_type == 'Final':
            if 'final' not in self._ref_cache:
                if self.timesteps:
                    df, _ = self.data_manager.get_frame(study, system, self.timesteps[-1])
                    self._ref_cache['final'] = df
            
            df = self._ref_cache.get('final')
            if df is not None and col in df.columns:
                return df[col].min(), df[col].max()
        
        # 'Current' (Default): Return limits of the current timestep
        df, _ = self.data_manager.get_frame(study, system, self.current_timestep)
        if df is not None and col in df.columns:
            return df[col].min(), df[col].max()

        return 0.0, 1.0

    def _get_bounds(self, study, system, col, ref_type):
        """Calculates Min/Max for coloring based on Reference Type."""
        # 1. Initial Frame Bounds
        if ref_type == 'Initial':
            if 'initial' not in self._ref_cache:
                if self.timesteps:
                    df, _ = self.data_manager.get_frame(study, system, self.timesteps[0])
                    self._ref_cache['initial'] = df
            
            df = self._ref_cache.get('initial')
            if df is not None and col in df.columns:
                return df[col].min(), df[col].max()
        
        # 2. Final Frame Bounds
        elif ref_type == 'Final':
            if 'final' not in self._ref_cache:
                 if self.timesteps:
                     df, _ = self.data_manager.get_frame(study, system, self.timesteps[-1])
                     if df is not None:
                         self._ref_cache['final'] = df
            
            df = self._ref_cache.get('final')
            if df is not None and col in df.columns:
                return df[col].min(), df[col].max()

        # 3. Current Frame (Default case)
        df, _ = self.data_manager.get_frame(study, system, self.current_timestep)
        if df is not None and col in df.columns:
            return df[col].min(), df[col].max()

        # Fallback to prevent crashes if data is missing
        return 0.0, 1.0

    def _generate_lut(self, gradient_name):
        import matplotlib.cm
        map_name = 'jet' 
        if gradient_name == 'Viridis': map_name = 'viridis'
        elif gradient_name == 'Hot': map_name = 'hot'
        elif gradient_name == 'Blue-Red': map_name = 'coolwarm'
        elif gradient_name == 'Plasma': map_name = 'plasma'
        elif gradient_name == 'Magma': map_name = 'magma'
        try: cmap = matplotlib.cm.get_cmap(map_name)
        except: cmap = matplotlib.cm.get_cmap('jet')
        x = np.linspace(0, 1, 256)
        rgba = cmap(x)
        rgba = (rgba * 255).astype(np.uint8)
        brushes = np.array([pg.mkBrush(QColor(r, g, b, 255)) for r,g,b,a in rgba])
        return brushes

    def get_data_min_max(self, study, system, col):
        return self.data_manager.get_global_min_max(study, system, col)

    def export_plot(self, filename: str, figsize=None):
        """Dispatches to image or text export based on file extension."""
        if filename.lower().endswith(('.csv', '.tsv')):
            self._export_text_data(filename)
            return

        try:
            import matplotlib.pyplot as plt
            fig, ax = plt.subplots(figsize=figsize if figsize else (10, 6))
            x_data = self.scatter.data['x']
            y_data = self.scatter.data['y']
            if len(x_data) == 0: return
            
            colors = []
            for b in self.last_render_brushes:
                # Handle both QBrush and QColor objects safely
                if hasattr(b, 'color'):
                    c = b.color() if not callable(b.color) else b.color()
                elif hasattr(b, 'getRgbF'):
                    c = b
                else:
                    c = QColor(0, 0, 0)
                colors.append((c.redF(), c.greenF(), c.blueF(), c.alphaF()))
            
            size = self.view_config.get('size', 5)
            
            # Get symbol and translate to marker
            pg_symbol = self.view_config.get('symbol', 'o')
            mpl_marker = self._get_mpl_marker(pg_symbol)
            
            ax.scatter(x_data, y_data, c=colors, s=size**2, 
                       marker=mpl_marker, edgecolors='none')
            
            vb = self.plot_item.getViewBox()
            ax.set_xlim(vb.viewRange()[0])
            ax.set_ylim(vb.viewRange()[1])
            
            x_lbl = self.view_config.get('x_label', self.view_config.get('x_col', ''))
            y_lbl = self.view_config.get('y_label', self.view_config.get('y_col', ''))
            ax.set_xlabel(x_lbl)
            ax.set_ylabel(y_lbl)
            
            fig.savefig(filename, dpi=300, bbox_inches='tight')
            plt.close(fig)
            print(f"Exported to {filename}")
        except Exception as e:
            print(f"Export Error: {e}")

    def _export_text_data(self, filename: str):
        """Exports the active trajectory data to CSV or TSV (X and Y only)."""
        if self.active_df is None or self.active_df.empty:
            return

        import csv
        delimiter = '\t' if filename.lower().endswith('.tsv') else ','
        
        x_col = self.view_config.get('x_col')
        y_col = self.view_config.get('y_col')
        
        if not x_col or not y_col:
            return
            
        # Matplotlib-style headers like Log mode
        h1 = ['x', 'y']
        h2 = [self.view_config.get('x_label', x_col), self.view_config.get('y_label', y_col)]
        h3 = ['', self.view_config.get('plot_name', 'Scatter Data')]
        
        try:
            with open(filename, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f, delimiter=delimiter)
                writer.writerow(h1)
                writer.writerow(h2)
                writer.writerow(h3)
                
                # Write rows using only X and Y
                for _, row in self.active_df[[x_col, y_col]].iterrows():
                    writer.writerow([str(val) for val in row.values])
        except Exception as e:
            print(f"Export Error: {e}")

    def get_current_plot_state(self):
        if not self.scatter.isVisible() or len(self.scatter.data) == 0:
            return {}
        colors_list = []
        for b in self.last_render_brushes:
            colors_list.append(b.color())
            
        x_lbl = self.view_config.get('x_label', self.view_config.get('x_col', ''))
        y_lbl = self.view_config.get('y_label', self.view_config.get('y_col', ''))
        
        # Translate symbol
        pg_symbol = self.view_config.get('symbol', 'o')
        mpl_marker = self._get_mpl_marker(pg_symbol)
            
        state = {
            'title': f"{self.current_study} | {self.current_system}",
            'x_label': x_lbl,
            'x_limits': self.plot_item.getViewBox().viewRange()[0],
            'y_axes': {
                'primary': {
                    'label': y_lbl,
                    'y_limits': self.plot_item.getViewBox().viewRange()[1],
                    'series': [{
                        'id': 'trj_scatter',
                        'name': 'Trajectory Data',
                        'mode': 'scatter',
                        'x': self.scatter.data['x'],
                        'y': self.scatter.data['y'],
                        'colors': colors_list,
                        'size': self.view_config.get('size', 5),
                        'marker': mpl_marker, # Pass translated marker
                        'std': None,
                        'color': QColor('black')
                    }]
                }
            }
        }
        return state

    def _get_mpl_marker(self, pg_symbol):
        """Maps PyQtGraph symbols to Matplotlib marker strings."""
        # PyQtGraph symbols: o, s, t, t1, t2, t3, d, +, x, p, h, star
        # 't' is usually triangle down (v) in pg, 't1' is up (^)
        mapping = {
            'o': 'o', 
            's': 's', 
            't': 'v',  # Triangle Down
            't1': '^', # Triangle Up
            't2': '>', # Triangle Right
            't3': '<', # Triangle Left
            'd': 'D',  # Diamond (Standard)
            '+': '+', 
            'x': 'x',
            'p': 'p',  # Pentagon
            'h': 'h',  # Hexagon
            'star': '*'
        }
        return mapping.get(pg_symbol, 'o')