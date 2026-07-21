import pyqtgraph as pg
import numpy as np
import pandas as pd
from PyQt6.QtCore import QObject, pyqtSignal, QTimer, Qt
from PyQt6.QtGui import QColor

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
        
        def update_vb2_views():
            self.vb2.setGeometry(self.plot_item.vb.sceneBoundingRect())
            self.vb2.linkedViewChanged(self.plot_item.vb, self.vb2.XAxis)
            
        self.plot_item.vb.sigResized.connect(update_vb2_views)
        
    def set_active_system(self, study, system):
        if self.current_study != study or self.current_system != system:
            try:
                old_idx = self.timesteps.index(self.current_timestep) if self.timesteps else 0
            except ValueError:
                old_idx = 0

            self.current_study = study
            self.current_system = system
            self.timesteps = self.data_manager.get_timesteps(study, system)
            self.data_manager.clear_cache()
            
            if self.timesteps:
                if old_idx < len(self.timesteps):
                    self.current_timestep = self.timesteps[old_idx]
                else:
                    self.current_timestep = self.timesteps[-1]
            else:
                self.current_timestep = 0

            if self.is_playing:
                self.pause()
                self.playbackStopped.emit()
            
            self.plot_item.autoRange()

    def set_view_lock(self, locked):
        self.view_locked = locked
        current_type = self.plot_config.get('plot_type', 'Displacement plot')
        
        # Always disconnect existing signals first to prevent duplicates
        try:
            self.plot_item.vb.sigRangeChanged.disconnect(self._on_vb_range_changed)
        except:
            pass

        if locked:
            # Capture the CURRENT view immediately when locking. 
            # This overwrites any previously stored limit to ensure we lock *this* view.
            self.view_limits[current_type] = self.plot_item.getViewBox().viewRange()
                
            self.plot_item.disableAutoRange()
            self.plot_item.vb.sigRangeChanged.connect(self._on_vb_range_changed)
        else:
            self.plot_item.enableAutoRange()

    def _on_vb_range_changed(self):
        if self.view_locked:
            current_type = self.plot_config.get('plot_type', 'Displacement plot')
            self.view_limits[current_type] = self.plot_item.getViewBox().viewRange()

    def auto_scale(self, mode='final'):
        if not self.timesteps: return
        
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
        slice_axis = self.plot_config.get('slice_axis')
        observe_axis = self.plot_config.get('observe_axis')
        options = self.plot_config.get('global_options', {})
        
        # Load Initial and Final Frames (if needed for filtering)
        df_init, box_init = self.data_manager.load_frame(study, system, self.timesteps[0])
        df_final = None
        if options.get('z_filter_ref') == 'Final' and len(self.timesteps) > 0:
            df_final, _ = self.data_manager.load_frame(study, system, self.timesteps[-1])
            
        if df_init is None: return

        for ts in search_steps:
             df_curr, box_curr = self.data_manager.load_frame(study, system, ts)
             if df_curr is None: continue
             
             for domain in self.domains:
                 results = self.data_manager.slice_disp_mean(
                    df_init, df_curr, domain, slice_axis, observe_axis, box_curr, 
                    z_col=options.get('z_filter_col'), z_ref=options.get('z_filter_ref'), 
                    z_ranges=self.plot_config.get('z_ranges'), 
                    df_final=df_final, box_init=box_init
                 )
                 for df_res in results:
                     if df_res is not None and not df_res.empty:
                         x = df_res['center'].values
                         y = df_res['mean_disp'].values
                         
                         if len(x) > 0:
                             g_min_x = min(g_min_x, np.min(x))
                             g_max_x = max(g_max_x, np.max(x))
                             g_min_y = min(g_min_y, np.min(y))
                             g_max_y = max(g_max_y, np.max(y))
                     
        if g_min_x != float('inf'):
            pad_x = (g_max_x - g_min_x) * 0.05 if g_max_x != g_min_x else 1.0
            pad_y = (g_max_y - g_min_y) * 0.05 if g_max_y != g_min_y else 1.0
            
            self.plot_item.setXRange(g_min_x - pad_x, g_max_x + pad_x, padding=0)
            self.plot_item.setYRange(g_min_y - pad_y, g_max_y + pad_y, padding=0)
            
            if self.view_locked:
                self.view_limits[self.plot_config.get('plot_type', 'Displacement plot')] = self.plot_item.getViewBox().viewRange()

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
        self.domains = domains
        self.plot_config = {
            'global_options': global_options,
            'slice_axis': slice_axis,
            'observe_axis': observe_axis,
            'plot_type': plot_type,
            'z_ranges': z_ranges
        }
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
        # Else 'Current' uses self.current_timestep (default)

        # Load the specific frame just to get the Min/Max of the column
        df, _ = self.data_manager.load_frame(study, system, ts_to_load)
        
        if df is not None and col in df.columns:
            return df[col].min(), df[col].max()
            
        return 0.0, 1.0

    def update_scene(self):
        if not self.timesteps: return
        
        # Clear / Setup
        self.plot_item.clear()
        self.vb2.clear()
        self.plot_item.showGrid(x=True, y=True, alpha=0.3)
        self.plot_items = {}
        
        # Labels default
        slice_axis = self.plot_config.get('slice_axis', 'X')
        observe_axis = self.plot_config.get('observe_axis', 'Y')
        self.plot_item.setLabel('bottom', f"Initial box center in {slice_axis}")
        self.plot_item.setLabel('left', f"Change in {observe_axis}")
        
        study, system = self.current_study, self.current_system
        ts = self.current_timestep
        
        # Load Data
        df_init, box_init = self.data_manager.load_frame(study, system, self.timesteps[0])
        df_curr, box_curr = self.data_manager.load_frame(study, system, ts)
        
        # Load Final Frame if filtering requires it (The Fix)
        options = self.plot_config.get('global_options', {})
        df_final = None
        if options.get('z_filter_ref') == 'Final' and len(self.timesteps) > 0:
             df_final, _ = self.data_manager.load_frame(study, system, self.timesteps[-1])
        
        if df_init is None or df_curr is None: return
        
        plot_type = self.plot_config.get('plot_type', 'Displacement plot')
        
        # Auto-Resize Logic
        if self.view_locked:
             if plot_type in self.view_limits:
                xr, yr = self.view_limits[plot_type]
                self.plot_item.setXRange(xr[0], xr[1], padding=0)
                self.plot_item.setYRange(yr[0], yr[1], padding=0)
        else:
            self.plot_item.enableAutoRange()
            
        # Dispatch
        if plot_type == 'Displacement plot':
            self._draw_displacement_plot(df_init, df_curr, df_final, box_init, box_curr, slice_axis, observe_axis, options)
        elif 'Strain' in plot_type:
            self._draw_strain_plots(study, system, slice_axis, observe_axis, ts, options, plot_type)

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

            for i, df_res in enumerate(valid_results):
                x = df_res['center'].values
                y = df_res['mean_disp'].values
                y_err = df_res['std_dev'].values if options.get('disp_std', True) else None
                
                seg_count = df_res['count'].sum()
                seg_weight = df_res['sum_weights'].sum()

                color = QColor(domain.get('color', 'blue'))
                style_str = domain.get('style', 'o')
                symbol, pen_style = self._get_pyqtgraph_style(style_str)
                try: width = int(domain.get('size', 5))
                except: width = 5

                # Plot Error Band
                if y_err is not None:
                    c1 = pg.PlotCurveItem(x, y + y_err, pen=None)
                    c2 = pg.PlotCurveItem(x, y - y_err, pen=None)
                    bc = QColor(color); bc.setAlpha(50)
                    self.plot_item.addItem(pg.FillBetweenItem(c1, c2, brush=pg.mkBrush(bc)))

                # Plot Domain Segment
                pen = pg.mkPen(color, width=2, style=pen_style) if pen_style != Qt.PenStyle.NoPen else None
                d_name = domain.get('name', f"Domain {idx+1}")
                p_name = f"{d_name}{i+1}" if len(valid_results) > 1 else d_name
                
                if symbol:
                    self.plot_item.plot(x, y, pen=pen, symbol=symbol, symbolBrush=color, symbolPen=color, symbolSize=width, name=p_name)
                else:
                    self.plot_item.plot(x, y, pen=pen, name=p_name)

                # Particle Counts Bars
                if show_right_axis:
                     count_val = df_res['sum_weights'].values if show_weighted_count else df_res['count'].values
                     w_bar = 1.0
                     if len(x) > 1:
                         diffs = np.diff(np.sort(x))
                         pos_diffs = diffs[diffs > 0]
                         if len(pos_diffs) > 0: w_bar = np.min(pos_diffs) * 0.8
                     cb = QColor(color).darker(200); cb.setAlpha(128)
                     self.vb2.addItem(pg.BarGraphItem(x=x, height=count_val, width=w_bar, brush=cb, pen=None))

                for _, r in df_res.iterrows():
                    all_pts_flat.append((r['center'], r['mean_disp']))
                
                report_data.append({
                    'name': p_name,
                    'df': df_res,
                    'count': seg_count,
                    'weight': seg_weight
                })

        # Call the final part (Optimal Line + Report)
        if all_pts_flat or (box_init and box_curr):
            self._draw_optimal_line_and_report(slice_axis, observe_axis, box_init, box_curr, all_pts_flat, any_domain_pbc, report_data, options)
        else:
            self.errorUpdated.emit("No data points available.")

    def _draw_optimal_line_and_report(self, slice_axis, observe_axis, box_init, box_curr, all_pts_flat, any_domain_pbc, report_data, options):
        s_idx = {'x':0,'y':1,'z':2}.get(slice_axis.lower())
        o_idx = {'x':0,'y':1,'z':2}.get(observe_axis.lower())
        
        # Determine Optimal Line Coords (Target)
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
            # Fallback
            all_pts_flat.sort(key=lambda p: p[0])
            x_vals = [p[0] for p in all_pts_flat]
            x1, x2 = min(x_vals), max(x_vals)
            y1 = all_pts_flat[0][1]
            y2 = all_pts_flat[-1][1]
            slope = (y2 - y1) / (x2 - x1) if x2 != x1 else 0.0
        
        self.last_target_strain = slope

        if options.get('opt_line', False):
            opt_settings = next((d for d in self.domains if d.get('is_optimal_line')), None)
            if opt_settings:
                oc = QColor(opt_settings.get('color', 'black'))
                os_str = opt_settings.get('style', '--')
                ops = Qt.PenStyle.DashLine if os_str == '--' else Qt.PenStyle.SolidLine
                try: width = int(opt_settings.get('size', 1))
                except: width = 1
                self.plot_item.plot([x1, x2], [y1, y2], pen=pg.mkPen(oc, width=width, style=ops), name="Optimal Line")

        # Report Generation
        headers = ["Split", "Strain", "Error", "#Parts", "#Weights"]
        data_rows = []
        data_rows.append(["Target", f"{slope: .4e}", f"{0.0: .4e}", "-", "-"])
        
        for item in report_data:
            derivs = self.data_manager.calculate_derivatives(item['df'])
            if derivs is not None and not derivs.empty:
                act_strain = derivs['strain'].mean()
                if slope != 0: rel_err = (act_strain - slope) / slope
                else: rel_err = 0.0 if act_strain == 0 else np.nan
                s_str = f"{act_strain: .4e}"
                e_str = f"{rel_err: .4e}"
            else:
                s_str, e_str = "N/A", "N/A"
            
            c_str = f"{int(item['count'])}"
            w_str = f"{item['weight']:.2f}"
            data_rows.append([item['name'], s_str, e_str, c_str, w_str])

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
        valid_timesteps = self.timesteps[:self.timesteps.index(ts)+1] if ts in self.timesteps else self.timesteps
        evolution_data = self.data_manager.calculate_full_evolution(
            self.domains, valid_timesteps, study, system, slice_axis, observe_axis, options
        )
        
        for curve in evolution_data:
            color = QColor(curve['color'])
            pen_style = Qt.PenStyle.DashLine if curve['style'] == '--' else Qt.PenStyle.SolidLine
            pen = pg.mkPen(color, width=2, style=pen_style)
            
            x_vals = []
            if plot_type == 'Strain Over Step':
                self.plot_item.setLabel('bottom', 'Load Step')
                x_vals = curve['x']
            elif plot_type == 'Strain Over Strain':
                self.plot_item.setLabel('bottom', 'Target Strain')
                if len(curve['y']) > 0:
                    x_vals = np.linspace(0, 1, len(curve['y'])) 
            
            if x_vals is not None and len(x_vals) == len(curve['y']):
                if options.get('strain_std', False) and 'y_err' in curve:
                    y_vals = np.array(curve['y'])
                    y_err = np.array(curve['y_err'])
                    mask = ~np.isnan(y_vals) & ~np.isnan(y_err)
                    if np.any(mask):
                        c1 = pg.PlotCurveItem(np.array(x_vals)[mask], y_vals[mask] + y_err[mask], pen=pg.mkPen(None))
                        c2 = pg.PlotCurveItem(np.array(x_vals)[mask], y_vals[mask] - y_err[mask], pen=pg.mkPen(None))
                        bc = QColor(color); bc.setAlpha(50)
                        self.plot_item.addItem(pg.FillBetweenItem(c1, c2, brush=pg.mkBrush(bc)))

                self.plot_item.plot(x_vals, curve['y'], pen=pen, name=curve['name'])
        
        if options.get('opt_line', False) and evolution_data:
            t_strain = getattr(self, 'last_target_strain', None)
            if t_strain is not None:
                all_xs = []
                for c in evolution_data:
                    if plot_type == 'Strain Over Step':
                        all_xs.extend(c['x'])
                    elif plot_type == 'Strain Over Strain':
                        all_xs.extend(np.linspace(0, 1, len(c['y'])))
                
                if all_xs:
                    x_min, x_max = min(all_xs), max(all_xs)
                    opt_x = np.array([x_min, x_max])
                    opt_y = np.array([t_strain, t_strain])
                    
                    opt_settings = next((d for d in self.domains if d.get('is_optimal_line')), None)
                    if opt_settings:
                        oc = QColor(opt_settings.get('color', 'black'))
                        os_str = opt_settings.get('style', '--')
                        ops = Qt.PenStyle.DashLine if os_str == '--' else Qt.PenStyle.SolidLine
                        p_item = self.plot_item.plot(opt_x, opt_y, pen=pg.mkPen(oc, width=2, style=ops), name="Optimal Line")
                        p_item.is_opt_line = True
        
        self.errorUpdated.emit("")

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

        # Matplotlib Export for high quality and proper legend scaling
        # We need to collect what's currently in the plot
        fig, ax1 = plt.subplots(figsize=figsize if figsize else (10, 6))
        
        # Determine if we have a right axis (Counts/Weights)
        show_right = self.plot_item.getAxis('right').isVisible()
        ax2 = ax1.twinx() if show_right else None
        if ax2:
            ax2.set_ylabel(self.plot_item.getAxis('right').labelText, fontsize=12)

        # Labels
        ax1.set_xlabel(self.plot_item.getAxis('bottom').labelText, fontsize=12)
        ax1.set_ylabel(self.plot_item.getAxis('left').labelText, fontsize=12)
        ax1.tick_params(axis='both', which='major', labelsize=10)
        
        handles, labels = [], []
        
        # Iterate over plot items to replicate them in Matplotlib
        for item in self.plot_item.items:
            if isinstance(item, pg.PlotDataItem) and item.isVisible():
                x, y = item.getData()
                if x is None or y is None or len(x) == 0: continue
                
                name = item.name()
                pen = item.opts.get('pen')
                color = pen.color().getRgbF()[:3] if pen else (0,0,1)
                width = pen.width() if pen else 1
                linestyle = self._get_mpl_linestyle(pen.style()) if (pen and pen.style() != Qt.PenStyle.NoPen) else 'None'
                # Fix: Handle both scatter (dot) and line cases
                symbol = item.opts.get('symbol')
                symbol_size = item.opts.get('symbolSize', 5)
                
                h, = ax1.plot(x, y, label=name, color=color, linewidth=width, linestyle=linestyle,
                               marker=self._get_trj_mpl_marker(symbol) if symbol else None,
                               markersize=symbol_size)
                if name:
                    handles.append(h)
                    labels.append(name)
            
        # Second pass to handle FillBetweenItems and associate them with legend names
        all_handles = list(handles)
        all_labels = list(labels)
        
        for item in self.plot_item.items:
            if isinstance(item, pg.FillBetweenItem) and item.isVisible():
                c1_data = item.curves[0].getData()
                c2_data = item.curves[1].getData()
                if c1_data[1] is not None and c2_data[1] is not None:
                    # Match this error band to a series name by checking midpoints
                    mid_y = (c1_data[1] + c2_data[1]) / 2.0
                    match_label = None
                    
                    for i, h in enumerate(handles):
                        h_x, h_y = h.get_data()
                        if len(h_x) == len(c1_data[0]) and np.allclose(h_y, mid_y, atol=1e-8):
                            match_label = f"{labels[i]} (Std)"
                            break
                    
                    # Fix: Handle brush being a method or property
                    brush = item.brush() if callable(item.brush) else item.brush
                    if hasattr(brush, 'color'):
                        c = brush.color() if callable(brush.color) else brush.color
                        color = (c.redF(), c.greenF(), c.blueF())
                        alpha = c.alphaF()
                    else:
                        color = (0.5, 0.5, 0.5)
                        alpha = 0.5
                    
                    fill = ax1.fill_between(c1_data[0], c1_data[1], c2_data[1], 
                                          color=color, alpha=alpha, linewidth=0, label=match_label)
                    
                    if match_label:
                        all_handles.append(fill)
                        all_labels.append(match_label)

        # Handle BarGraphItems (Counts/Weights) on ax2
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

        ax1.grid(True, alpha=0.3)
        if all_handles:
            # Legend size same as display: Increased font size for better visibility
            ax1.legend(all_handles, all_labels, loc='best', fontsize=12)

        plt.tight_layout()
        fig.savefig(filename, dpi=300, bbox_inches='tight')
        plt.close(fig)

    def _get_trj_mpl_marker(self, pg_symbol):
        mapping = {
            'o': 'o', 's': 's', 't': 'v', 't1': '^', 't2': '>', 't3': '<',
            'd': 'D', '+': '+', 'x': 'x', 'p': 'p', 'h': 'h', 'star': '*'
        }
        return mapping.get(pg_symbol, 'o')

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
                # Optimization: Look for a FillBetweenItem that covers this series
                # We assume if a FillBetweenItem exists, it surrounds the mean.
                # Actually, in DSD, we add FillBetween with bc (brush).
                # To be sure, we can check if any FillBetweenItem has curves that 
                # average to this Y.
                for other in self.plot_item.items:
                    if isinstance(other, pg.FillBetweenItem) and other.isVisible():
                        c1 = other.curves[0].getData()
                        c2 = other.curves[1].getData()
                        if np.array_equal(c1[0], x):
                            # Check if (c1+c2)/2 roughly equals y
                            mid = (c1[1] + c2[1]) / 2.0
                            if np.allclose(mid, y, atol=1e-10):
                                std = np.abs(c1[1] - c2[1]) / 2.0
                                break
                
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
            print(f"Export Error: {e}")

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
                
                pending_std = None
                # Look for a FillBetweenItem that covers this series
                for other in self.plot_item.items:
                    if isinstance(other, pg.FillBetweenItem) and other.isVisible():
                        c1_dt = other.curves[0].getData()
                        c2_dt = other.curves[1].getData()
                        # Use allclose for robust float comparison of centers and means
                        if (c1_dt[0] is not None and len(c1_dt[0]) == len(x_data) and 
                            np.allclose(c1_dt[0], x_data, atol=1e-8)):
                            
                            mid = (c1_dt[1] + c2_dt[1]) / 2.0
                            if np.allclose(mid, y_data, atol=1e-8):
                                pending_std = np.abs(c1_dt[1] - c2_dt[1]) / 2.0
                                break

                series_entry = {
                    'id': name,
                    'name': name,
                    'x': x_data,
                    'y': y_data,
                    'std': pending_std,
                    'color': color,
                    'linestyle_matlab': self._get_mpl_linestyle(pen.style()) if pen else 'None',
                    'width': pen.width() if pen else 0,
                    'marker': self._get_trj_mpl_marker(symbol) if symbol else 'None',
                    'mode': 'scatter' if (symbol and (not pen or pen.style() == Qt.PenStyle.NoPen)) else 'line',
                    'size': item.opts.get('symbolSize', 5),
                    'layer_priority': 10 if is_opt else 0
                }
                state['y_axes']['Y']['series'].append(series_entry)
                
        return state

    def _get_mpl_linestyle(self, qt_style):
        if hasattr(qt_style, 'value'):
            style_int = qt_style.value
        else:
            style_int = int(qt_style)
        mapping = {
            0: 'None', # NoPen
            1: '-', 
            2: '--', 
            3: ':', 
            4: '-.', 
            5: (0, (3, 1, 1, 1, 1, 1))
        }
        return mapping.get(style_int, '-')

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