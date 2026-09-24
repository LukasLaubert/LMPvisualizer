import pyqtgraph as pg
import numpy as np
import pandas as pd
from PyQt6.QtCore import QObject, pyqtSignal, QTimer, Qt
from PyQt6.QtGui import QColor
from lmpvisualizer.shared import plot_model
from lmpvisualizer.shared.logger_setup import get_logger

logger = get_logger(__name__)
# Shared appearance defaults, so the plot cannot diverge from what the table row shows.
from lmpvisualizer.dsd.dsd_widgets import DEFAULT_STYLE, DEFAULT_SIZE, DEFAULT_STRAIN_STYLE, DEFAULT_STRAIN_SIZE

class DSDController(QObject):
    frameChanged = pyqtSignal(int)
    boundsChanged = pyqtSignal(dict)
    playbackStopped = pyqtSignal()
    errorUpdated = pyqtSignal(str) 
    
    def __init__(self, plot_widget, data_manager):
        super().__init__()
        self.widget = plot_widget
        self.data_manager = data_manager
        
        self.plot_item = self.widget.getPlotItem()
        self.plot_item.showGrid(x=True, y=True, alpha=0.3)
        self.plot_item.setLabel('left', '')
        self.plot_item.setLabel('bottom', "Slice Position")
        
        self.timer = QTimer()
        self.timer.timeout.connect(self._on_timer_tick)
        self.is_playing = False
        self.auto_replay = False
        self.fps = 10
        self.playback_start_time = 0.0
        self.playback_start_index = 0
        
        self.current_study = None
        self.current_system = None
        self.current_timestep = 0
        self.timesteps = []
        self.full_timesteps = [] # Store all available steps for range adjustment
        
        self.plot_config = {}
        self.domains = []
        
        # Plot objects cache
        self.plot_items = {} 
        self.optimal_line = None
        
        # View Locking
        self.view_locked = False
        self.view_limits = {} 
        self.last_target_strain = None
        
        # Second Axis (Counts)
        self.vb2 = pg.ViewBox()
        self.plot_item.scene().addItem(self.vb2)
        self.plot_item.getAxis('right').linkToView(self.vb2)
        self.vb2.setXLink(self.plot_item)
        
        # Ensure main ViewBox is on top and interactive for BOTH axes
        self.plot_item.vb.setZValue(10)
        self.plot_item.vb.setMouseEnabled(x=True, y=True)
        
        # Place secondary ViewBox behind and disable its mouse interaction
        self.vb2.setZValue(-1)
        self.vb2.setMouseEnabled(x=False, y=False)
        
        def update_vb2_views():
            self.vb2.setGeometry(self.plot_item.vb.sceneBoundingRect())
            self.vb2.linkedViewChanged(self.plot_item.vb, self.vb2.XAxis)
            
        self.plot_item.vb.sigResized.connect(update_vb2_views)

        # Caching for Strain Plots
        self._strain_cache = None
        self._strain_cache_key = None
        
    def set_active_system(self, study, system, force=False):
        """Switches the active study/system.

        force=True re-derives the timestep lists even when the names are unchanged.
        Needed after a project reload: study/system are derived from folder names
        (grandparent/parent), so a different project can carry identical names while
        the underlying data manager has been reset.
        """
        if force or self.current_study != study or self.current_system != system:
            # 1. Capture current state for Hybrid Conservation
            old_start_val = self.timesteps[0] if self.timesteps else 0
            
            pct_window = 1.0 # Default to showing 100% of remaining
            pct_current = 0.0 # Default to start of window
            
            if self.timesteps and self.full_timesteps:
                # A. Calculate "Percentage of Remaining" for the Final Step
                # How much of the available future did we see?
                try:
                    # We use values to find indices in the full list to be robust
                    old_full_start_idx = self.full_timesteps.index(self.timesteps[0])
                    old_full_end_idx = self.full_timesteps.index(self.timesteps[-1])
                    old_total_remaining = len(self.full_timesteps) - 1 - old_full_start_idx
                    
                    if old_total_remaining > 0:
                        current_window_len = old_full_end_idx - old_full_start_idx
                        pct_window = current_window_len / old_total_remaining
                except ValueError:
                    pass

                # B. Calculate "Percentage of Window" for the Current Step
                if len(self.timesteps) > 1:
                    try:
                        curr_in_window_idx = self.timesteps.index(self.current_timestep)
                        pct_current = curr_in_window_idx / (len(self.timesteps) - 1)
                    except ValueError:
                        pass

            self.current_study = study
            self.current_system = system
            
            if system == "Strain average":
                # Aggregate timesteps from all systems in the study
                all_ts = []
                parsers = self.data_manager.parsers.get(study, {})
                for sys_name in parsers:
                    all_ts.append(set(self.data_manager.get_timesteps(study, sys_name)))
                
                if all_ts:
                    common_ts = set.intersection(*all_ts)
                    self.full_timesteps = sorted(list(common_ts))
                else:
                    self.full_timesteps = []
            else:
                self.full_timesteps = self.data_manager.get_timesteps(study, system)
            
            self.data_manager.clear_cache()
            
            # 2. Apply Hybrid Range Conservation
            if self.full_timesteps:
                full_arr = np.array(self.full_timesteps)
                
                # A. New Initial (Absolute)
                # Find closest match to old absolute start value
                new_start_idx = (np.abs(full_arr - old_start_val)).argmin()
                
                # B. New Final (Percentage of Remaining)
                new_total_remaining = len(self.full_timesteps) - 1 - new_start_idx
                if new_total_remaining > 0:
                    new_window_len = int(new_total_remaining * pct_window)
                    new_end_idx = new_start_idx + new_window_len
                else:
                    new_end_idx = new_start_idx
                
                # Clamp
                new_end_idx = min(new_end_idx, len(self.full_timesteps) - 1)
                
                self.timesteps = self.full_timesteps[new_start_idx : new_end_idx + 1]
                
                # C. New Current (Percentage of Window)
                if len(self.timesteps) > 1:
                    new_curr_idx = int(pct_current * (len(self.timesteps) - 1))
                    self.current_timestep = self.timesteps[new_curr_idx]
                else:
                    self.current_timestep = self.timesteps[0]
            else:
                self.timesteps = []
                self.current_timestep = 0

            if self.is_playing:
                self.pause()
                self.playbackStopped.emit()
            
            if not self.view_locked:
                self.plot_item.autoRange()

    def set_timestep_range(self, min_val, max_val):
        """
        Updates the active subset of timesteps based on user-entered min/max.
        Rounds to nearest available steps.
        """
        if not self.full_timesteps: return
        
        # 1. Round to nearest
        full_arr = np.array(self.full_timesteps)
        
        # Find closest indices (searchsorted finds insertion points, we need closest value)
        idx_min = (np.abs(full_arr - min_val)).argmin()
        idx_max = (np.abs(full_arr - max_val)).argmin()
        
        # Ensure proper order
        if idx_min > idx_max: idx_min, idx_max = idx_max, idx_min
        
        # 2. Slice
        new_timesteps = self.full_timesteps[idx_min : idx_max + 1]
        
        if not new_timesteps:
            new_timesteps = [self.full_timesteps[idx_min]] # at least one
            
        # 3. Apply if changed
        if new_timesteps != self.timesteps:
            self.timesteps = new_timesteps
            
            # Clamp current timestep
            if self.current_timestep not in self.timesteps:
                self.current_timestep = self.timesteps[0]
            
            # Notify UI
            # We must return the new list so the panel can update the slider
            return self.timesteps
        
        # Even if unchanged, return current list to force UI refresh (revert invalid inputs)
        return self.timesteps

    def set_view_lock(self, locked):
        self.view_locked = locked
        current_type = self.plot_config.get('plot_type', 'Displacement plot')
        
        # Always disconnect existing signals first to prevent duplicates
        try:
            self.plot_item.vb.sigRangeChanged.disconnect(self._on_vb_range_changed)
        except:
            pass

        if locked:
            # Always capture the CURRENT view when locking.
            # This ensures "what you see is what you lock".
            # Session loading sets the view range BEFORE locking, so this remains correct for loading too.
            self.view_limits[current_type] = self.plot_item.getViewBox().viewRange()
            
            self.plot_item.disableAutoRange()
            self.plot_item.vb.sigRangeChanged.connect(self._on_vb_range_changed)
        else:
            self.plot_item.enableAutoRange()

    def _on_vb_range_changed(self):
        if self.view_locked:
            current_type = self.plot_config.get('plot_type', 'Displacement plot')
            self.view_limits[current_type] = self.plot_item.getViewBox().viewRange()

    def auto_scale(self, mode='final', save_limits=True):
        if not self.timesteps: return
        
        # Temporarily disconnect the range change signal during auto_scale
        # to prevent premature updates from overwriting our calculated range.
        was_connected = False
        if self.view_locked:
            try:
                self.plot_item.vb.sigRangeChanged.disconnect(self._on_vb_range_changed)
                was_connected = True
            except:
                pass

        target_step = self.current_timestep
        search_steps = [self.current_timestep]
        
        if mode == 'final':
            target_step = self.timesteps[-1]
            search_steps = [self.timesteps[-1]]
        elif mode == 'max':
            search_steps = self.timesteps
            
        g_min_x, g_max_x = float('inf'), float('-inf')
        g_min_y, g_max_y = float('inf'), float('-inf')
        
        study, system = self.current_study, self.current_system
        options = self.plot_config.get('global_options', {})
        slice_axis = self.plot_config.get('slice_axis', 'X')
        observe_axis = self.plot_config.get('observe_axis', 'Y')
        plot_type = self.plot_config.get('plot_type', 'Displacement plot')
        
        # Load Initial and Final Frames (if needed for filtering)
        df_init, box_init = None, None
        df_final = None
        
        if system != "Strain average":
            df_init, box_init = self.data_manager.load_frame(study, system, self.timesteps[0])
            if options.get('z_filter_ref') == 'Final' and len(self.timesteps) > 0:
                df_final, _ = self.data_manager.load_frame(study, system, self.timesteps[-1])
            
            if df_init is None:
                if was_connected: self.plot_item.vb.sigRangeChanged.connect(self._on_vb_range_changed)
                return

        if 'Strain' in plot_type:
            # ... (keep existing strain logic) ...
            if self._strain_cache is None:
                if system == "Strain average":
                    self._strain_cache = self.data_manager.calculate_strain_average_evolution(
                        self.domains, self.timesteps, study, slice_axis, observe_axis, options
                    )
                else:
                    self._strain_cache = self.data_manager.calculate_strain_evolution_cached(
                        self.domains, self.timesteps, study, system, slice_axis, observe_axis, options
                    )
            if self._strain_cache:
                current_ts_idx = self.timesteps.index(self.current_timestep) if self.current_timestep in self.timesteps else 0
                slice_idx = len(self.timesteps) if mode in ['final', 'max'] else current_ts_idx + 1

                if plot_type == 'Strain Over Strain':
                    x_pts = np.array(self._strain_cache['target_strains'][:slice_idx])
                else: # Strain Over Step
                    x_pts = np.array(self._strain_cache['timesteps'][:slice_idx])
                
                valid_mask_x = ~np.isnan(x_pts)
                if np.any(valid_mask_x):
                    g_min_x, g_max_x = np.min(x_pts[valid_mask_x]), np.max(x_pts[valid_mask_x])

                for d_res in self._strain_cache['domains']:
                    s_idx = d_res.get('source_index')
                    if s_idx is not None and s_idx < len(self.domains):
                        if not self.domains[s_idx].get('strain_show', True): continue

                    y_pts = np.array(d_res['strains'][:slice_idx])
                    y_err = np.array(d_res['stds'][:slice_idx]) if options.get('strain_std', False) else None
                    valid_mask_y = ~np.isnan(y_pts)
                    if np.any(valid_mask_y):
                        g_min_y, g_max_y = min(g_min_y, np.min(y_pts[valid_mask_y])), max(g_max_y, np.max(y_pts[valid_mask_y]))
                        if y_err is not None:
                            valid_mask_err = ~np.isnan(y_err) & valid_mask_y
                            if np.any(valid_mask_err):
                                g_min_y = min(g_min_y, np.min(y_pts[valid_mask_err] - y_err[valid_mask_err]))
                                g_max_y = max(g_max_y, np.max(y_pts[valid_mask_err] + y_err[valid_mask_err]))
        else:
            # Displacement plot logic - OPTIMIZED
            # 1. Try to use Persistent Library results to find bounds instantly
            found_in_lib = False
            parser = self.data_manager.get_parser(study, system)
            if parser and mode == 'max':
                total_y_min, total_y_max = float('inf'), float('-inf')
                total_x_min, total_x_max = float('inf'), float('-inf')
                matches = 0
                
                for domain in self.domains:
                    if domain.get('is_optimal_line') or not domain.get('show', True): continue
                    
                    # Generate hash for this domain definition
                    d_hash = self.data_manager.get_domain_hash(domain, self.timesteps, slice_axis, observe_axis, options)
                    lib_entry = parser.results_library.get(d_hash)
                    
                    if lib_entry and len(lib_entry['data']['strains']) == len(self.timesteps):
                        # Use the precise min/max displacement values stored in the library
                        d_data = lib_entry['data']
                        y_mins = np.array(d_data.get('min_y', []))
                        y_maxs = np.array(d_data.get('max_y', []))
                        x_mins = np.array(d_data.get('min_x', []))
                        x_maxs = np.array(d_data.get('max_x', []))
                        
                        valid = ~np.isnan(y_mins) & ~np.isnan(y_maxs)
                        if np.any(valid):
                            total_y_min = min(total_y_min, np.min(y_mins[valid]))
                            total_y_max = max(total_y_max, np.max(y_maxs[valid]))
                            total_x_min = min(total_x_min, np.min(x_mins[valid]))
                            total_x_max = max(total_x_max, np.max(x_maxs[valid]))
                            matches += 1
                
                if matches > 0:
                    g_min_x, g_max_x = total_x_min, total_x_max
                    g_min_y, g_max_y = total_y_min, total_y_max
                    found_in_lib = True

            if not found_in_lib:
                # 2. Fallback: Sampling strategy (max 10 frames) instead of reading every frame
                if mode == 'max' and len(search_steps) > 10:
                    indices = np.linspace(0, len(search_steps) - 1, 10, dtype=int)
                    search_steps = [search_steps[i] for i in indices]

                for ts in search_steps:
                     df_curr, box_curr = self.data_manager.load_frame(study, system, ts)
                     if df_curr is None: continue
                     for domain in self.domains:
                         if not domain.get('show', True): continue
                         results = self.data_manager.slice_disp_mean(df_init, df_curr, domain, slice_axis, observe_axis, box_curr, 
                                                                   z_col=options.get('z_filter_col'), z_ref=options.get('z_filter_ref'), 
                                                                   z_ranges=self.plot_config.get('z_ranges'), df_final=df_final, box_init=box_init)
                         for df_res in results:
                             if df_res is not None and not df_res.empty:
                                 x, y = df_res['center'].values, df_res['mean_disp'].values
                                 if len(x) > 0:
                                     g_min_x, g_max_x = min(g_min_x, np.min(x)), max(g_max_x, np.max(x))
                                     g_min_y, g_max_y = min(g_min_y, np.min(y)), max(g_max_y, np.max(y))
                                     if options.get('disp_std', True) and 'std_dev' in df_res.columns:
                                         y_err = df_res['std_dev'].values
                                         if np.any(y_err):
                                             g_min_y, g_max_y = min(g_min_y, np.min(y - y_err)), max(g_max_y, np.max(y + y_err))
            
            pass

        if g_min_x != float('inf'):
            pad_x = (g_max_x - g_min_x) * 0.05 if g_max_x != g_min_x else 1.0
            self.plot_item.setXRange(g_min_x - pad_x, g_max_x + pad_x, padding=0)
            
        if g_min_y != float('inf'):
            pad_y = (g_max_y - g_min_y) * 0.05 if g_max_y != g_min_y else 1.0
            self.plot_item.setYRange(g_min_y - pad_y, g_max_y + pad_y, padding=0)
            
        # ALWAYS save the new limits if locked AND save_limits is True
        if self.view_locked and save_limits:
            plot_type = self.plot_config.get('plot_type', 'Displacement plot')
            self.view_limits[plot_type] = self.plot_item.getViewBox().viewRange()

        # Reconnect signal
        if was_connected:
            self.plot_item.vb.sigRangeChanged.connect(self._on_vb_range_changed)

    def get_available_timesteps(self):
        return self.timesteps

    def set_timestep_index(self, idx):
        if 0 <= idx < len(self.timesteps):
            self.current_timestep = self.timesteps[idx]
            self.update_scene()
            
    def set_fps(self, fps):
        self.fps = max(1, fps)
        if self.is_playing:
            self.timer.setInterval(int(1000/self.fps))
            
    def set_auto_replay(self, enabled):
        self.auto_replay = enabled
        
    def play(self):
        if not self.timesteps: return
        self.is_playing = True
        try:
            curr_idx = self.timesteps.index(self.current_timestep)
        except:
            curr_idx = 0
        
        if curr_idx >= len(self.timesteps) - 1:
            curr_idx = 0
            
        self.playback_start_index = curr_idx
        import time
        self.playback_start_time = time.time()
        
        self.timer.start(int(1000/self.fps))
        
    def pause(self):
        self.is_playing = False
        self.timer.stop()
        
    def _on_timer_tick(self):
        if not self.timesteps:
            self.pause()
            self.playbackStopped.emit()
            return
            
        import time
        now = time.time()
        elapsed = now - self.playback_start_time
        frames_to_advance = int(elapsed * self.fps)
        target_idx = self.playback_start_index + frames_to_advance
        
        if target_idx >= len(self.timesteps):
            if self.auto_replay:
                self.playback_start_time = now
                self.playback_start_index = 0
                target_idx = 0
            else:
                target_idx = len(self.timesteps) - 1
                self.pause()
                self.playbackStopped.emit()
                return
                
        new_step = self.timesteps[target_idx]
        if new_step != self.current_timestep:
            self.current_timestep = new_step
            self.update_scene()
            self.frameChanged.emit(target_idx)

    def update_config(self, domains, global_options, slice_axis, observe_axis, plot_type, z_ranges=None):
        old_type = self.plot_config.get('plot_type')

        # Cache Invalidation Check — centralized identity in dsd_data_manager:
        # same result-affecting fields as the persistent .idx hash; visual-only
        # props (color, style/size/show and strain_* variants) are excluded there.
        merged_options = dict(global_options or {})
        merged_options['z_ranges'] = z_ranges
        new_key = self.data_manager.build_strain_cache_key(
            domains, self.timesteps, slice_axis, observe_axis, merged_options,
            study=self.current_study, system=self.current_system)

        if self._strain_cache_key != new_key:
            self._strain_cache = None  # Invalidate
            self._strain_cache_key = new_key

        self.domains = domains
        self.plot_config = {
            'global_options': global_options,
            'slice_axis': slice_axis,
            'observe_axis': observe_axis,
            'plot_type': plot_type,
            'z_ranges': z_ranges
        }
        
        # If switching plot types, we MUST reset the axes to ensure labels (Ticks) 
        # for Displacement (e.g. 0.1) don't bleed into Strain (e.g. 0.001).
        if old_type != plot_type:
            # 1. Force an auto-scale to the new data range
            # Pass save_limits=False to avoid overwriting stored zoom levels during the transition
            self.auto_scale(mode='max', save_limits=False)
            
            # 2. If view was locked, immediately re-apply the SPECIFIC zoom for this new type
            if self.view_locked:
                stored_limits = self.view_limits.get(plot_type)
                if stored_limits:
                    self.plot_item.setXRange(stored_limits[0][0], stored_limits[0][1], padding=0)
                    self.plot_item.setYRange(stored_limits[1][0], stored_limits[1][1], padding=0)

        self.update_scene()

    def get_scope_min_max(self, study, system, col, ref):
        """
        Calculates the data range for the Z-Filter based on the reference type.
        Used by the UI to set the Filter Bar range.
        """
        if not self.timesteps: return 0.0, 1.0

        ts_to_load = self.current_timestep
        
        if ref == 'Initial':
            ts_to_load = self.timesteps[0]
        elif ref == 'Final':
            ts_to_load = self.timesteps[-1]
        elif ref.startswith("Step "):
            try:
                ts_to_load = int(ref.split(" ")[1])
            except:
                ts_to_load = self.current_timestep
        # Else 'Current' uses self.current_timestep (default)

        # Load the specific frame just to get the Min/Max of the column
        df, _ = self.data_manager.load_frame(study, system, ts_to_load)
        
        if df is not None and col in df.columns:
            return df[col].min(), df[col].max()
            
        return 0.0, 1.0

    def _reset_canvas(self):
        """Drops every drawn item and restores the empty canvas' visual state."""
        # Clear EVERYTHING first
        self.plot_item.clear()
        self.vb2.clear()
        self.plot_items = {}

        # Restore Legend (clear() removes it)
        if self.plot_item.legend:
            try:
                if self.plot_item.legend.scene():
                    self.plot_item.legend.scene().removeItem(self.plot_item.legend)
            except Exception:
                pass
            finally:
                self.plot_item.legend = None

        self.plot_item.addLegend()

        # Restore fundamental visual state
        self.plot_item.showGrid(x=True, y=True, alpha=0.3)

        # Ensure right axis is hidden by default (Displacement plot will show if needed)
        self.plot_item.hideAxis('right')

    def clear_scene(self):
        """Empties the canvas and unbinds the data, leaving the configuration alone.

        Used when the project path is cleared: the domains stay in the table, but the
        canvas must not keep showing a project that is no longer loaded.
        """
        self.pause()
        self._reset_canvas()
        self.plot_item.setLabel('bottom', '')
        self.plot_item.setLabel('left', '')

        self.current_study, self.current_system = None, None
        self.timesteps, self.full_timesteps = [], []
        self.current_timestep = 0
        self._strain_cache, self._strain_cache_key = None, None

    def update_scene(self):
        if not self.timesteps: return

        plot_type = self.plot_config.get('plot_type', 'Displacement plot')
        slice_axis = self.plot_config.get('slice_axis', 'X')
        observe_axis = self.plot_config.get('observe_axis', 'Y')

        # CRITICAL: Disable auto-range BEFORE clearing/drawing when view is locked
        # This prevents any intermediate auto-range during scene setup
        if self.view_locked:
            self.plot_item.disableAutoRange()

        self._reset_canvas()

        # Set labels AFTER clear, specific to plot type
        if plot_type == 'Displacement plot':
            self.plot_item.setLabel('bottom', f"Initial box center in {slice_axis}")
            self.plot_item.setLabel('left', f"Change in {observe_axis}")
        elif plot_type == 'Strain Over Step':
            self.plot_item.setLabel('bottom', 'Timestep')
            self.plot_item.setLabel('left', 'Actual strain')
        elif plot_type == 'Strain Over Strain':
            self.plot_item.setLabel('bottom', 'Target strain')
            self.plot_item.setLabel('left', 'Actual strain')
        
        study, system = self.current_study, self.current_system
        ts = self.current_timestep
        
        # Prepare options for drawing (inject z_ranges into global_options)
        options = self.plot_config.get('global_options', {}).copy()
        options['z_ranges'] = self.plot_config.get('z_ranges')
        
        if system == "Strain average" and plot_type == "Displacement plot":
            self.errorUpdated.emit("Displacement plot not available for Strain average.")
            return

        # Load Data
        df_init, box_init = None, None
        df_curr, box_curr = None, None
        
        if system != "Strain average":
            df_init, box_init = self.data_manager.load_frame(study, system, self.timesteps[0])
            df_curr, box_curr = self.data_manager.load_frame(study, system, ts)
            
            df_final = None
            if options.get('z_filter_ref') == 'Final' and len(self.timesteps) > 0:
                 df_final, _ = self.data_manager.load_frame(study, system, self.timesteps[-1])
            elif options.get('z_filter_ref', '').startswith("Step "):
                try:
                    custom_ts = int(options.get('z_filter_ref').split(" ")[1])
                    df_final, _ = self.data_manager.load_frame(study, system, custom_ts)
                except:
                    pass
            
            if df_init is None or df_curr is None: return
                
            # Dispatch to appropriate draw method
            if plot_type == 'Displacement plot':
                self._draw_displacement_plot(df_init, df_curr, df_final, box_init, box_curr, slice_axis, observe_axis, options)
            elif 'Strain' in plot_type:
                self._draw_strain_plots(study, system, slice_axis, observe_axis, ts, options, plot_type)
        else:
            # Strain average case
            if 'Strain' in plot_type:
                self._draw_strain_plots(study, system, slice_axis, observe_axis, ts, options, plot_type)

        # Apply View Limits AFTER drawing (critical for locked view stability)
        if self.view_locked:
             if plot_type in self.view_limits:
                xr, yr = self.view_limits[plot_type]
                # Block signals to prevent "tiny jumps" or recursive calls during step change
                self.plot_item.vb.blockSignals(True)
                self.plot_item.setXRange(xr[0], xr[1], padding=0)
                self.plot_item.setYRange(yr[0], yr[1], padding=0)
                self.plot_item.vb.blockSignals(False)
        else:
            self.plot_item.enableAutoRange()

    def _draw_displacement_plot(self, df_init, df_curr, df_final, box_init, box_curr, slice_axis, observe_axis, options):
        # Check particle count mode
        show_total = options.get('total_count', False)
        show_weighted_count = options.get('weighted_count', False)
        show_right_axis = show_total or show_weighted_count
        
        if show_right_axis:
             self.plot_item.showAxis('right')
             if show_total:
                 self.plot_item.getAxis('right').setLabel('Particle Count')
             else:
                 self.plot_item.getAxis('right').setLabel('Particle weight sum')
        else:
             self.plot_item.hideAxis('right')

        # Real-time Bounds Update for "Current" Reference
        z_col = options.get('z_filter_col')
        z_ref = options.get('z_filter_ref')
        if z_col and z_col != "No Z-Filter" and z_ref == 'Current':
            if z_col in df_curr.columns:
                c_min, c_max = float(df_curr[z_col].min()), float(df_curr[z_col].max())
                # This informs the UI that the labels on the filter bar need to change
                self.boundsChanged.emit({'z_filter': (c_min, c_max)})

        all_pts_flat = [] 
        any_domain_pbc = False 
        report_data = [] 

        for idx, domain in enumerate(self.domains):
            if domain.get('is_optimal_line'): continue
            if not domain.get('show', True): continue
            
            if domain.get('pbc', False): any_domain_pbc = True
            
            # Pass df_final explicitly for filtering logic
            results = self.data_manager.slice_disp_mean(
                df_init, df_curr, domain, slice_axis, observe_axis, box_curr,
                z_col=options.get('z_filter_col'), z_ref=options.get('z_filter_ref'),
                z_ranges=self.plot_config.get('z_ranges'), 
                df_final=df_final, box_init=box_init
            )
            valid_results = [r for r in results if r is not None and not r.empty]
            valid_results.sort(key=lambda r: r['center'].mean())

            # Layering: Top row (low idx) -> Top Layer (High Z)
            z_val = len(self.domains) - idx

            for i, df_res in enumerate(valid_results):
                x = df_res['center'].values
                y = df_res['mean_disp'].values
                y_err = df_res['std_dev'].values if options.get('disp_std', True) else None
                
                seg_count = df_res['count'].sum()
                seg_weight = df_res['sum_weights'].sum()

                color = QColor(domain.get('color', 'blue'))
                # Fall back to the same defaults the table row displays (dsd_widgets),
                # otherwise a fresh domain plots as 'o'/3.0 while its row shows Dots/2 -
                # and 'o' additionally draws a connecting line that "Dots" does not.
                style_str = domain.get('style', DEFAULT_STYLE)
                symbol, pen_style = self._get_pyqtgraph_style(style_str)
                try: width = float(domain.get('size', DEFAULT_SIZE))
                except: width = float(DEFAULT_SIZE)

                # Plot Error Band
                if y_err is not None:
                    c1 = pg.PlotCurveItem(x, y + y_err, pen=None)
                    c2 = pg.PlotCurveItem(x, y - y_err, pen=None)
                    bc = QColor(color); bc.setAlpha(50)
                    fill = pg.FillBetweenItem(c1, c2, brush=pg.mkBrush(bc))
                    fill.setZValue(z_val - 0.5)
                    self.plot_item.addItem(fill)

                # Plot Domain Segment
                pen = pg.mkPen(color, width=2.0, style=pen_style) if pen_style != Qt.PenStyle.NoPen else None
                d_name = domain.get('name', f"Domain {idx+1}")
                p_name = f"{d_name}{i+1}" if len(valid_results) > 1 else d_name
                
                item = None
                if symbol:
                    item = self.plot_item.plot(x, y, pen=pen, symbol=symbol, symbolBrush=color, symbolPen=color, symbolSize=width, name=p_name)
                else:
                    item = self.plot_item.plot(x, y, pen=pen, name=p_name)
                
                if item: item.setZValue(z_val)

                # Particle Counts Bars
                if show_right_axis:
                     count_val = df_res['sum_weights'].values if show_weighted_count else df_res['count'].values
                     w_bar = 1.0
                     if len(x) > 1:
                         diffs = np.diff(np.sort(x))
                         pos_diffs = diffs[diffs > 0]
                         if len(pos_diffs) > 0: w_bar = np.min(pos_diffs) * 0.8
                     
                     cb = QColor(color).darker(200); cb.setAlpha(128)
                     bar = pg.BarGraphItem(x=x, height=count_val, width=w_bar, brush=cb, pen=None)
                     bar.setZValue(z_val - 0.2)
                     self.vb2.addItem(bar)

                for _, r in df_res.iterrows():
                    all_pts_flat.append((r['center'], r['mean_disp']))
                
                report_data.append({
                    'name': p_name,
                    'df': df_res,
                    'count': seg_count,
                    'weight': seg_weight
                })

        # Call the final part (End-to-end + Report)
        if all_pts_flat or (box_init and box_curr):
            self._draw_end_to_end_and_report(slice_axis, observe_axis, box_init, box_curr, all_pts_flat, any_domain_pbc, report_data, options)
        else:
            self.errorUpdated.emit("No data points available.")

    def _draw_end_to_end_and_report(self, slice_axis, observe_axis, box_init, box_curr, all_pts_flat, any_domain_pbc, report_data, options):
        s_idx = {'x':0,'y':1,'z':2}.get(slice_axis.lower())
        o_idx = {'x':0,'y':1,'z':2}.get(observe_axis.lower())
        
        # Determine End-to-end Line Coords (Target)
        use_global_box = any_domain_pbc and (s_idx is not None) and (o_idx is not None) and box_init and box_curr
        
        x1, x2, y1, y2 = 0, 1, 0, 0
        slope = 0.0

        if use_global_box:
            # Helper to back-calculate standard LAMMPS triclinic parameters
            def get_box_params(box_data):
                xb, yb, zb = box_data[0], box_data[1], box_data[2]
                xy = xb[2] if len(xb) > 2 else 0.0
                xz = yb[2] if len(yb) > 2 else 0.0 
                yz = zb[2] if len(zb) > 2 else 0.0
                
                zlo, lz = zb[0], zb[1] - zb[0]
                ylo, ly = yb[0] - min(0.0, yz), (yb[1] - yb[0]) - abs(yz)

                mins_x = min(0.0, xy, xz, xy+xz)
                maxs_x = max(0.0, xy, xz, xy+xz)
                xlo, lx = xb[0] - mins_x, (xb[1] - xb[0]) - (maxs_x - mins_x)
                
                return {'xlo': xlo, 'ylo': ylo, 'zlo': zlo, 
                        'lx': max(1e-9, lx), 'ly': max(1e-9, ly), 'lz': max(1e-9, lz), 
                        'xy': xy, 'xz': xz, 'yz': yz}

            p_i = get_box_params(box_init)
            p_c = get_box_params(box_curr)
            
            def calc_u(obj_idx, x_i, y_i, z_i):
                w0 = (z_i - p_i['zlo']) / p_i['lz']
                v0 = (y_i - (p_i['ylo'] + w0 * p_i['yz'])) / p_i['ly']
                u0 = (x_i - (p_i['xlo'] + v0 * p_i['xy'] + w0 * p_i['xz'])) / p_i['lx']
                
                x_c = p_c['xlo'] + u0 * p_c['lx'] + v0 * p_c['xy'] + w0 * p_c['xz']
                y_c = p_c['ylo'] + v0 * p_c['ly'] + w0 * p_c['yz']
                z_c = p_c['zlo'] + w0 * p_c['lz']
                
                res = [x_c - x_i, y_c - y_i, z_c - z_i]
                return res[obj_idx]

            c_i = [p_i['xlo'] + 0.5*p_i['lx'], p_i['ylo'] + 0.5*p_i['ly'], p_i['zlo'] + 0.5*p_i['lz']]
            x1, x2 = box_init[s_idx][0], box_init[s_idx][1]
            
            def get_u_at(slice_val):
                pos = list(c_i)
                pos[s_idx] = slice_val
                return calc_u(o_idx, *pos)

            y1, y2 = get_u_at(x1), get_u_at(x2)
            slope = (y2 - y1) / (x2 - x1) if x2 != x1 else 0.0

        elif all_pts_flat:
            # Fallback - Optimized O(N)
            # all_pts_flat is list of (x, y) tuples
            
            min_p = min(all_pts_flat, key=lambda p: p[0])
            max_p = max(all_pts_flat, key=lambda p: p[0])
            
            x1, y1 = min_p
            x2, y2 = max_p
            
            slope = (y2 - y1) / (x2 - x1) if x2 != x1 else 0.0
        
        self.last_target_strain = slope

        # End-to-end Line Plotting (Independent of Menu Option)
        opt_settings = next((d for d in self.domains if d.get('is_optimal_line')), None)
        
        # Use 'show' from table, default to True if missing
        show_opt = opt_settings.get('show', True) if opt_settings else True
        
        if opt_settings and show_opt:
            oc = QColor(opt_settings.get('color', 'black'))
            os_str = opt_settings.get('style', '--')
            ops = Qt.PenStyle.DashLine if os_str == '--' else Qt.PenStyle.SolidLine
            try: width = int(opt_settings.get('size', 1))
            except: width = 1
            opt_item = self.plot_item.plot([x1, x2], [y1, y2], pen=pg.mkPen(oc, width=width, style=ops), name="End-to-end")
            opt_item.setZValue(1000)

        # Report Generation
        headers = ["Split", "Strain", "Error", "#Parts", "#Weights"]
        data_rows = []
        
        for item in report_data:
            derivs = self.data_manager.calculate_derivatives(item['df'])
            if derivs is not None and not derivs.empty:
                act_strain = derivs['strain'].mean()
                if slope != 0: rel_err = (act_strain - slope) / slope
                else: rel_err = 0.0 if act_strain == 0 else np.nan
                s_str = f"{act_strain: .3e}"
                e_str = f"{rel_err: .3e}"
            else:
                s_str, e_str = "N/A", "N/A"
            
            c_str = f"{int(item['count'])}"
            w_str = f"{item['weight']:.2f}"
            data_rows.append([item['name'], s_str, e_str, c_str, w_str])

        # Add E2E (End-to-end) row at the bottom
        data_rows.append(["E2E", f"{slope: .3e}", f"{0.0: .3e}", "-", "-"])

        widths = [len(h) for h in headers]
        for row in data_rows:
            for i, val in enumerate(row):
                if len(val) > widths[i]: widths[i] = len(val)
                    
        row_fmt = f"{{:<{widths[0]}}} | {{:>{widths[1]}}} | {{:>{widths[2]}}} | {{:>{widths[3]}}} | {{:>{widths[4]}}}"
        lines = [row_fmt.format(*headers), "-" * len(row_fmt.format(*headers))]
        for row in data_rows:
            lines.append(row_fmt.format(*row))

        self.errorUpdated.emit("\n".join(lines))

    def _draw_strain_plots(self, study, system, slice_axis, observe_axis, ts, options, plot_type):
        if not self.timesteps: 
            self.errorUpdated.emit("No timesteps available.")
            return

        # 1. Ensure Cache is Populated
        if system == "Strain average":
            if self._strain_cache is None:
                self._strain_cache = self.data_manager.calculate_strain_average_evolution(
                    self.domains, self.timesteps, study, slice_axis, observe_axis, options
                )
        else:
            if self._strain_cache is None:
                self._strain_cache = self.data_manager.calculate_strain_evolution_cached(
                    self.domains, self.timesteps, study, system, slice_axis, observe_axis, options
                )
        
        if not self._strain_cache:
            self.errorUpdated.emit("Failed to calculate strain evolution.")
            return

        # 2. Determine X-axis and Current Index
        try:
            curr_idx = self.timesteps.index(ts)
        except ValueError:
            curr_idx = 0

        # Plot up to current timestep
        steps_to_plot = slice(0, curr_idx + 1)
        
        if plot_type == 'Strain Over Step':
            x_full = np.array(self._strain_cache['timesteps'], dtype=float)
        else: # Strain Over Strain
            x_full = np.array(self._strain_cache['target_strains'], dtype=float)

        x_plot = x_full[steps_to_plot]
        
        # 3. Plot Domains
        line_styles_order = ['-', '--', '.-']
        
        # Iterate over self.domains (CURRENT Table Order)
        for idx, domain in enumerate(self.domains):
            if domain.get('is_optimal_line'): continue
            if not domain.get('strain_show', True): continue

            # Identify identity
            current_id = (domain.get('name'), tuple(domain.get('splits', [])), tuple(domain.get('active_segments', [])))
            
            # Find ALL matching data in cache (handles splits)
            domain_results = [d for d in self._strain_cache['domains'] if d.get('parent_identity') == current_id]
            if not domain_results: continue
                
            # Sort results by name to ensure spatial/index consistency
            domain_results.sort(key=lambda d: d.get('name', ''))

            # Visual Properties
            color = QColor(domain.get('color', 'blue'))
            try: width = float(domain.get('strain_size', DEFAULT_STRAIN_SIZE))
            except: width = float(DEFAULT_STRAIN_SIZE)

            # Resolve Starting Style
            user_style = domain.get('strain_style', DEFAULT_STRAIN_STYLE)
            if not user_style: user_style = DEFAULT_STRAIN_STYLE
            
            # Determine Cycle of 3 styles
            if user_style in line_styles_order:
                # User picked a line style -> cycle through the 3 standard line styles starting there
                start_idx = line_styles_order.index(user_style)
                cycle = [
                    line_styles_order[start_idx],
                    line_styles_order[(start_idx + 1) % 3],
                    line_styles_order[(start_idx + 2) % 3]
                ]
            else:
                # User picked a custom marker -> cycle: UserMarker, '-', '--'
                cycle = [user_style, '-', '--']
                          
            z_val = len(self.domains) - idx

            # Iterate over splits
            for i, d_data in enumerate(domain_results):
                y_full = np.array(d_data['strains'])
                y_err_full = np.array(d_data['stds'])
                
                y_plot = y_full[steps_to_plot]
                y_err_plot = y_err_full[steps_to_plot]
                
                # Get current style from cycle
                current_style_str = cycle[i % 3]
                symbol, pen_style = self._get_pyqtgraph_style(current_style_str)
                
                # Mask NaNs for plotting error bands
                mask = ~np.isnan(x_plot) & ~np.isnan(y_plot)
                if np.any(mask):
                    if options.get('strain_std', False) and np.any(y_err_plot[mask] > 0):
                        c1 = pg.PlotCurveItem(x_plot[mask], y_plot[mask] + y_err_plot[mask], pen=None)
                        c2 = pg.PlotCurveItem(x_plot[mask], y_plot[mask] - y_err_plot[mask], pen=None)
                        bc = QColor(color); bc.setAlpha(50)
                        fill = pg.FillBetweenItem(c1, c2, brush=pg.mkBrush(bc))
                        fill.setZValue(z_val - 0.5)
                        self.plot_item.addItem(fill)

                    pen = pg.mkPen(color, width=width, style=pen_style) if pen_style != Qt.PenStyle.NoPen else None
                    
                    item = None
                    if symbol:
                        # Ensure symbol size matches line width or is scaled for visibility
                        item = self.plot_item.plot(x_plot[mask], y_plot[mask], pen=pen, symbol=symbol, 
                                            symbolBrush=color, symbolPen=color, symbolSize=width*3, name=d_data['name'])
                    else:
                        item = self.plot_item.plot(x_plot[mask], y_plot[mask], pen=pen, name=d_data['name'])
                    
                    if item: item.setZValue(z_val)

        # 4. Optimal Line (Target Strain)
        # Only drawn when the table actually carries an End-to-end row - previously this
        # defaulted to True when no such row existed, so the curve appeared (and was
        # legended) with no corresponding entry the user could switch off.
        # This mirrors the displacement path, which already requires opt_settings.
        opt_settings = next((d for d in self.domains if d.get('is_optimal_line')), None)
        show_opt = bool(opt_settings) and opt_settings.get('strain_show', True)
        
        # Get properties from settings, defaulting to strain defaults
        try: opt_width = float(opt_settings.get('strain_size', 2.0)) if opt_settings else 2.0
        except: opt_width = 2.0
        
        opt_style_str = opt_settings.get('strain_style', '--') if opt_settings else '--'
        # Support fallback if user picked a marker for the end-to-end line
        _, opt_pen_style = self._get_pyqtgraph_style(opt_style_str)
        
        opt_color = QColor(opt_settings.get('color', 'black')) if opt_settings else QColor('black')

        if show_opt:
            pen = pg.mkPen(opt_color, width=opt_width, style=opt_pen_style)
            
            if plot_type == 'Strain Over Strain':
                # Optimal is y=x
                valid_x = x_plot[~np.isnan(x_plot)]
                if len(valid_x) > 0:
                    x_min, x_max = np.min(valid_x), np.max(valid_x)
                    p_item = self.plot_item.plot([x_min, x_max], [x_min, x_max], 
                                               pen=pen, name="End-to-end")
                    p_item.is_opt_line = True
                    p_item.setZValue(1000)
            else: # Strain Over Step
                # Optimal is a curve of target strains over timesteps
                mask = ~np.isnan(x_full) & ~np.isnan(self._strain_cache['target_strains'])
                if np.any(mask):
                    effective_target = np.array(self._strain_cache['target_strains'])[steps_to_plot]
                    p_item = self.plot_item.plot(x_plot, effective_target, 
                                               pen=pen, name="End-to-end")
                    p_item.is_opt_line = True
                    p_item.setZValue(1000)

        # 5. Generate Error Report
        report = self._generate_strain_error_report(self._strain_cache, curr_idx)
        self.errorUpdated.emit(report)

    def _generate_strain_error_report(self, cache, curr_idx):
        """
        Calculates errors based on MATLAB logic:
        Iterates in table order using parent_identity matching.
        """
        target_strains = np.array(cache['target_strains'][:curr_idx+1])
        
        # Mask for valid target strains (avoid div by zero/NaN)
        mask = ~np.isnan(target_strains) & (np.abs(target_strains) > 1e-12)
        if not np.any(mask):
            return "No valid target strain data for error calculation."
            
        tar_m = target_strains[mask]
        
        headers = ["Domain", "Cum.Abs", "Cum.Sign", "Indv.Abs", "Indv.Sign"]
        rows = []
        
        # Iterate in CURRENT Table order
        for domain in self.domains:
            if domain.get('is_optimal_line'): continue
            if not domain.get('strain_show', True): continue
            
            # Identity of this domain
            target_ident = (domain.get('name'), tuple(domain.get('splits', [])), tuple(domain.get('active_segments', [])))
            
            # Find all matching results in cache
            domain_results = [d for d in cache['domains'] if d.get('parent_identity') == target_ident]
            
            for d_data in domain_results:
                name = d_data['name']
                y_m = np.array(d_data['strains'][:curr_idx+1])[mask]
                valid_mask = ~np.isnan(y_m)
                
                if not np.any(valid_mask):
                    errs = ["N/A"] * 4
                else:
                    y = y_m[valid_mask]
                    t = tar_m[valid_mask]
                    diff = y - t
                    
                    # Cum errors
                    c_abs = np.sum(np.abs(diff)) / np.sum(np.abs(t))
                    c_sign = np.sum(diff) / np.sum(np.abs(t))
                    
                    # Indv errors
                    indv_diff_rel = diff / t
                    i_abs = np.mean(np.abs(indv_diff_rel))
                    i_sign = np.mean(indv_diff_rel)
                    
                    errs = [f"{c_abs:.4f}", f"{c_sign:.4f}", f"{i_abs:.4f}", f"{i_sign:.4f}"]
                
                rows.append([name] + errs)

        if not rows:
            return "No visible domains to report."

        # Format as table string
        widths = [max(len(str(row[i])) for row in ([headers] + rows)) for i in range(len(headers))]
        row_fmt = " | ".join([f"{{:<{w}}}" if i==0 else f"{{:>{w}}}" for i, w in enumerate(widths)])
        
        lines = [row_fmt.format(*headers), "-" * len(row_fmt.format(*headers))]
        for row in rows:
            lines.append(row_fmt.format(*row))
            
        return "\n".join(lines)

        for base_name, segments in domain_groups.items():
            for seg in segments:
                rows.append([seg['name']] + [f"{v:.3e}" if not np.isnan(v) else "N/A" for v in seg['errs']])
            
            if len(segments) > 1:
                # Calculate mean for this row (domain)
                row_errs = []
                for i in range(4):
                    vals = [s['errs'][i] for s in segments if not np.isnan(s['errs'][i])]
                    row_errs.append(np.mean(vals) if vals else np.nan)
                
                rows.append([f"{base_name} Mean"] + [f"{v:.3e}" if not np.isnan(v) else "N/A" for v in row_errs])
                all_row_means.append(row_errs)
            else:
                # Add the only segment's error as a row mean for global mean calculation
                all_row_means.append(segments[0]['errs'])

        # Global Mean (mean of row means)
        if all_row_means:
            global_errs = []
            for i in range(4):
                vals = [r[i] for r in all_row_means if not np.isnan(r[i])]
                global_errs.append(np.mean(vals) if vals else np.nan)
            
            rows.append(["-"*10]*5)
            rows.append(["Overall Mean"] + [f"{v:.3e}" if not np.isnan(v) else "N/A" for v in global_errs])

        # Format table
        widths = [max(len(str(h)), max([len(str(r[i])) for r in rows if len(r)>i] or [0])) for i, h in enumerate(headers)]
        fmt = " | ".join([f"{{:<{w}}}" if i==0 else f"{{:>{w}}}" for i, w in enumerate(widths)])
        
        lines = [fmt.format(*headers), "-" * (sum(widths) + 3*len(headers))]
        for r in rows:
            if len(r) == 1: lines.append(r[0]) # separator
            else: lines.append(fmt.format(*r))
            
        return "\n".join(lines)

    def export_plot(self, filename: str, figsize=None):
        """Dispatches to image or text export based on file extension."""
        if filename.lower().endswith(('.csv', '.tsv')):
            self._export_text_data(filename)
            return

        try:
            import matplotlib.pyplot as plt
        except ImportError:
            # Fallback to ImageExporter if matplotlib is missing
            import pyqtgraph.exporters
            exporter = pyqtgraph.exporters.ImageExporter(self.plot_item)
            if figsize:
                exporter.parameters()['width'] = figsize[0] * 100
            else:
                exporter.parameters()['width'] = 1920
            exporter.export(filename)
            return

        # Matplotlib Export for high quality and proper legend scaling.
        # Consumes the same canonical state the popout uses, so the two
        # cannot diverge; bars stay export-only (popout has no counts axis).
        state = self.get_current_plot_state()
        series_list = []
        try:
            series_list = state.get('y_axes', {}).get('Y', {}).get('series', [])
        except Exception:
            series_list = []

        fig, ax1 = plt.subplots(figsize=figsize if figsize else (10, 6))

        # Apply limits from current view
        view_range = self.plot_item.getViewBox().viewRange()
        ax1.set_xlim(view_range[0])
        ax1.set_ylim(view_range[1])

        # Determine if we have a right axis (Counts/Weights)
        show_right = self.plot_item.getAxis('right').isVisible()
        ax2 = ax1.twinx() if show_right else None
        if ax2:
            plot_model.apply_axis_style(
                ax2,
                ylabel=self.plot_item.getAxis('right').labelText,
                grid=False,
            )
            # Try to match secondary view range if possible
            if self.vb2:
                try:
                    vr2 = self.vb2.viewRange()
                    if vr2 and len(vr2) > 1:
                        ax2.set_ylim(vr2[1])
                except Exception:
                    pass

        # Labels + ticks-in via the shared helper (same as popout/export).
        plot_model.apply_axis_style(
            ax1,
            xlabel=self.plot_item.getAxis('bottom').labelText,
            ylabel=self.plot_item.getAxis('left').labelText,
            grid=False,
        )

        legend_entries = []

        # Render every series from the canonical state (line/scatter +
        # FillBetween std matched inside get_current_plot_state).
        for series in series_list:
            try:
                color = plot_model.qcolor_to_rgba(series.get('color'))
            except Exception:
                color = (0.0, 0.0, 1.0, 1.0)
            _, _, entries = plot_model.render_series(
                ax1, series, color=color,
                linestyle=series.get('linestyle_matlab', '-'),
                linewidth=series.get('width', 1),
                marker=series.get('marker', 'None'),
                markersize=series.get('size', 5),
                show_std=True, show_std_legend=True, show_legend=True,
            )
            legend_entries.extend(entries)

        # Handle BarGraphItems (Counts/Weights) on ax2 — preserved as-is:
        # counts have no legend entry, widths/colors come from live items.
        if ax2:
            for item in self.vb2.allChildItems():
                if isinstance(item, pg.BarGraphItem) and item.isVisible():
                    opts = item.opts
                    x = opts.get('x')
                    height = opts.get('height')
                    width = opts.get('width', 1.0)
                    brush = opts.get('brush')
                    # Handle both QBrush and QColor objects
                    if hasattr(brush, 'color'):
                        c = brush.color() if callable(brush.color) else brush.color # QBrush.color()
                    elif hasattr(brush, 'getRgbF'):
                        c = brush # Already a QColor
                    else:
                        c = QColor(128, 128, 128, 128)

                    color = (c.redF(), c.greenF(), c.blueF())
                    alpha = c.alphaF()
                    ax2.bar(x, height, width=width, color=color, alpha=alpha, align='center')

        plot_model.apply_axis_style(ax1, grid=True, grid_alpha=0.3)
        legend_entries = plot_model.sort_legend_entries(legend_entries)
        all_handles = [h for _, _, _, h, _ in legend_entries]
        all_labels = [l for _, _, _, _, l in legend_entries]
        if all_handles:
            # Legend size same as display: Increased font size for better visibility
            ax1.legend(all_handles, all_labels, loc='best', fontsize=12)

        plt.tight_layout()
        fig.savefig(filename, dpi=300, bbox_inches='tight')
        plt.close(fig)

    def _get_trj_mpl_marker(self, pg_symbol):
        return plot_model.pg_symbol_to_mpl(pg_symbol)

    def _export_text_data(self, filename: str):
        """Internal handler for exporting data to CSV or TSV."""
        import csv
        delimiter = '\t' if filename.lower().endswith('.tsv') else ','
        
        # 1. Collect datasets from plot items
        x_groups = []
        
        # We need to find standard deviation associations.
        # In DSD, we don't have a clean map, so we'll look for FillBetweenItems
        # and try to associate them with PlotDataItems by checking if their curves match.
        
        series_data = []
        
        # First, find all PlotDataItems
        for item in self.plot_item.items:
            if isinstance(item, pg.PlotDataItem) and item.isVisible():
                name = item.name()
                if not name: continue
                
                x, y = item.getData()
                if x is None or y is None: continue
                
                std = None
                # Shared FillBetween matching (exact-x + 1e-10 midpoint,
                # same bytes as the old inline loop).
                try:
                    _bands_csv = []
                    for other in self.plot_item.items:
                        if isinstance(other, pg.FillBetweenItem) and other.isVisible():
                            c1 = other.curves[0].getData()
                            c2 = other.curves[1].getData()
                            if c1[0] is not None and c1[1] is not None and c2[1] is not None:
                                _bands_csv.append((c1[0], c1[1], c2[1]))
                    std = plot_model.match_std_band(x, y, _bands_csv, atol=1e-10, exact_x=True)
                except Exception:
                    std = None
                
                series_data.append({
                    'name': name,
                    'x': x,
                    'y': y,
                    'std': std
                })

        if not series_data:
            return

        # 2. Group by identical X
        for ds in series_data:
            found_group = False
            for group in x_groups:
                if np.array_equal(group['x'], ds['x']):
                    group['datasets'].append(ds)
                    found_group = True
                    break
            if not found_group:
                x_groups.append({
                    'x': ds['x'],
                    'datasets': [ds]
                })

        # 3. Construct Headers
        h1, h2, h3 = [], [], []
        data_cols = []
        max_rows = 0
        
        x_label = self.plot_item.getAxis('bottom').labelText
        y_label = self.plot_item.getAxis('left').labelText

        for group in x_groups:
            max_rows = max(max_rows, len(group['x']))
            h1.append('x'); h2.append(x_label); h3.append('')
            data_cols.append(group['x'])
            
            for ds in group['datasets']:
                h1.append('y'); h2.append(y_label); h3.append(ds['name'])
                data_cols.append(ds['y'])
                if ds['std'] is not None:
                    h1.append('std'); h2.append(''); h3.append('')
                    data_cols.append(ds['std'])

        # 4. Write
        try:
            with open(filename, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f, delimiter=delimiter)
                writer.writerow(h1)
                writer.writerow(h2)
                writer.writerow(h3)
                for i in range(max_rows):
                    row = []
                    for col in data_cols:
                        if i < len(col): row.append(str(col[i]))
                        else: row.append('')
                    writer.writerow(row)
        except Exception as e:
            logger.warning("Export Error: %s", e)

    def get_current_plot_state(self):
        """Extracts current state for PopOutWindow."""
        state = {
            'title': self.plot_config.get('plot_type', 'DSD Plot'),
            'x_label': self.plot_item.getAxis('bottom').label.toPlainText(),
            'x_limits': self.plot_item.getViewBox().viewRange()[0],
            'y_axes': {}
        }

        y_label = self.plot_item.getAxis('left').label.toPlainText()
        state['y_axes']['Y'] = {
            'label': y_label,
            'color': QColor('black'),
            'y_limits': self.plot_item.getViewBox().viewRange()[1],
            'series': []
        }

        # Collect FillBetween bands once for shared matching (same helper
        # as export_plot uses, so state/popout/export cannot diverge).
        bands = []
        for other in self.plot_item.items:
            if isinstance(other, pg.FillBetweenItem) and other.isVisible():
                try:
                    c1_dt = other.curves[0].getData()
                    c2_dt = other.curves[1].getData()
                    if c1_dt[0] is not None and c2_dt[1] is not None:
                        bands.append((c1_dt[0], c1_dt[1], c2_dt[1]))
                except Exception:
                    continue

        # Table-row order for legend: domain index when the name maps to
        # a domain, else scene order (update_scene draws in table order).
        domain_row = {}
        for idx, domain in enumerate(self.domains or []):
            dname = domain.get('name')
            if dname and dname not in domain_row:
                domain_row[dname] = idx

        scene_idx = 0
        for item in self.plot_item.items:
            if isinstance(item, pg.PlotDataItem):
                name = item.name()
                if not name: continue

                x_data, y_data = item.getData()
                if x_data is None or y_data is None: continue

                pen = item.opts.get('pen')

                # Extract color: prefer pen color, fall back to symbolBrush for scatter plots
                color = QColor('blue')  # Default fallback
                if pen and pen.style() != Qt.PenStyle.NoPen:
                    color = pen.color()
                else:
                    # For scatter plots, color is in symbolBrush
                    symbol_brush = item.opts.get('symbolBrush')
                    if symbol_brush is not None:
                        if isinstance(symbol_brush, QColor):
                            color = symbol_brush
                        elif hasattr(symbol_brush, 'color'):
                            color = symbol_brush.color()

                is_opt = getattr(item, 'is_opt_line', False)
                symbol = item.opts.get('symbol')

                pending_std = plot_model.match_std_band(x_data, y_data, bands, atol=1e-8)

                row = domain_row.get(name, None)
                if row is None:
                    # Split segments carry the domain name as prefix.
                    for dname, didx in domain_row.items():
                        if name.startswith(dname):
                            row = didx
                            break
                if row is None:
                    row = scene_idx
                if is_opt:
                    # End-to-end line sorts after every domain row.
                    row = plot_model.DEFAULT_ROW

                # Same dict shape as before, values sourced from the single builder.
                series_entry = plot_model.make_series(
                    name, name, x_data, y_data, pending_std,
                    color=color,
                    linestyle_matlab=self._get_mpl_linestyle(pen.style()) if pen else 'None',
                    width=pen.width() if pen else 0,
                    marker=self._get_trj_mpl_marker(symbol) if symbol else 'None',
                    mode='scatter' if (symbol and (not pen or pen.style() == Qt.PenStyle.NoPen)) else 'line',
                    size=item.opts.get('symbolSize', 5),
                    layer_priority=10 if is_opt else 0,
                    row=row,
                )
                state['y_axes']['Y']['series'].append(series_entry)
                scene_idx += 1

        return state

    def _get_mpl_linestyle(self, qt_style):
        return plot_model.qt_pen_style_to_mpl(qt_style)

    def _get_pyqtgraph_style(self, style_str):
        symbol = 'o'
        pen_style = Qt.PenStyle.SolidLine
        
        if style_str == '.-': 
            symbol = 'o'
        elif style_str == '--': 
            symbol = None
            pen_style = Qt.PenStyle.DashLine
        elif style_str == '-':
            symbol = None
            pen_style = Qt.PenStyle.SolidLine
        elif style_str == 'Dots':
            symbol = 'o'
            pen_style = Qt.PenStyle.NoPen
        elif style_str == '-o':
            symbol = 'o'
        elif style_str == '-*':
            symbol = 'star'
        else:
            # Custom symbol
            symbol = style_str
            
        return symbol, pen_style