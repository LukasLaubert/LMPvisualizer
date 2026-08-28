import pyqtgraph as pg
from PyQt6.QtGui import QColor
from typing import Dict, Any
import numpy as np
import copy

# Custom AxisItem that separates axis line color from grid/tick color
class ColoredAxis(pg.AxisItem):
    """AxisItem with separate colors for axis line and grid/ticks."""
    
    def __init__(self, orientation, pen=None, textPen=None, axisPen=None, linkView=None, parent=None, maxTickLength=-5, showValues=True, text='', units='', unitPrefix='', **args):
        super().__init__(orientation, pen=pen, textPen=textPen, linkView=linkView, parent=parent, maxTickLength=maxTickLength, showValues=showValues, text=text, units=units, unitPrefix=unitPrefix, **args)
        self.axisPen = axisPen
        if self.axisPen is None:
            self.axisPen = self.pen()
    
    def drawPicture(self, p, axisSpec, tickSpecs, textSpecs):
        """Override drawPicture to use separate pen for axis line."""
        p.setRenderHint(p.RenderHint.Antialiasing, False)
        p.setRenderHint(p.RenderHint.TextAntialiasing, True)
        
        ## draw long line along axis line, using axisPen
        pen, p1, p2 = axisSpec
        p.setPen(self.axisPen)
        p.drawLine(p1, p2)
        
        ## draw ticks using normal pen (grid color)
        for pen, p1, p2 in tickSpecs:
            p.setPen(pen)
            p.drawLine(p1, p2)
        
        ## Draw all text
        if self.style['tickFont'] is not None:
            p.setFont(self.style['tickFont'])
        p.setPen(self.textPen())
        bounding = self.boundingRect().toAlignedRect()
        p.setClipRect(bounding)
        for rect, flags, text in textSpecs:
            p.drawText(rect, int(flags), text)
    
    def setAxisPen(self, pen):
        """Set the pen used for drawing the axis line."""
        self.axisPen = pen
        self.picture = None
        self.update()


class LogController:
    """Manages the pyqtgraph PlotWidget and its items."""
    
    def __init__(self, plot_widget: pg.PlotWidget):
        self.widget = plot_widget
        self.plot_item = self.widget.getPlotItem()
        self.plot_item.showGrid(x=True, y=True)
        self.plot_item.addLegend()
        self.plot_item.getAxis('left').setTextPen('k')
        self.plot_item.getAxis('bottom').setTextPen('k')
        
        # Single dictionary to hold all plot-related items by name
        self.plots: Dict[str, Dict[str, Any]] = {}
        # Dictionary to manage y-axes and their associated viewboxes
        self.y_axes: Dict[str, Dict[str, Any]] = {}
        # Store axis colors and labels for export
        self.y_axis_colors: Dict[str, QColor] = {}
        self.y_axis_labels: Dict[str, str] = {}
        self.x_axis_label: str = ""
        
        self.axes_locked = False
        self.scale_locked = False
        self._is_syncing_axes = False # Flag to prevent recursive signal handling

    def toggle_axes_lock(self, locked: bool, reset_view: bool = True):
        """Connects or disconnects the synchronization signal for all active Y-axes."""
        self.axes_locked = locked
        
        # Iterate over all existing viewboxes to connect or disconnect the signal
        for axis_info in self.y_axes.values():
            vb = axis_info['viewbox']
            # First, always try to disconnect to prevent duplicate connections
            try:
                vb.sigRangeChanged.disconnect(self._on_axis_range_changed)
            except (TypeError, RuntimeError):
                pass # Ignore if it wasn't connected
            
            # If locking (either 0-lock or scale-lock), reconnect the signal
            if self.axes_locked or self.scale_locked:
                vb.sigRangeChanged.connect(self._on_axis_range_changed)
        
        # Trigger reset view to apply the new locking logic immediately
        if reset_view and self.y_axes:
            self.reset_view()

    def toggle_scale_lock(self, locked: bool):
        """Toggles scaling lock state and updates connections."""
        self.scale_locked = locked
        
        # Re-evaluate connections
        for axis_info in self.y_axes.values():
            vb = axis_info['viewbox']
            try:
                vb.sigRangeChanged.disconnect(self._on_axis_range_changed)
            except (TypeError, RuntimeError):
                pass

            if self.axes_locked or self.scale_locked:
                vb.sigRangeChanged.connect(self._on_axis_range_changed)
        
        # Trigger immediate sync if enabling scale lock
        if locked and self.y_axes:
            self.reset_view()

    def align_zero_preserve_scale(self, source_vb: pg.ViewBox = None):
        """Align y-axis zero positions without changing current y-range heights."""
        if len(self.y_axes) < 2:
            return

        source_vb = source_vb or self.plot_item.getViewBox()
        src_min, src_max = source_vb.viewRange()[1]
        src_height = src_max - src_min
        if src_height == 0:
            return

        src_zero_rel = -src_min / src_height
        was_syncing = self._is_syncing_axes
        self._is_syncing_axes = True
        try:
            for axis_info in self.y_axes.values():
                vb = axis_info['viewbox']
                if vb is source_vb:
                    continue

                curr_min, curr_max = vb.viewRange()[1]
                curr_height = curr_max - curr_min
                if curr_height == 0:
                    continue

                new_min = -src_zero_rel * curr_height
                vb.setYRange(new_min, new_min + curr_height, padding=0)
        finally:
            self._is_syncing_axes = was_syncing

    def apply_current_locks(self):
        """Forces synchronization/reset of views. Useful after reloading plots."""
        # This ensures the view resets (to auto-fit) even when No Locks are active.
        if self.y_axes:
            self.reset_view()

    def _on_axis_range_changed(self, changed_vb: pg.ViewBox):
        """Signal handler that triggers when any connected axis is moved."""
        if self._is_syncing_axes or not (self.axes_locked or self.scale_locked):
            return

        self._is_syncing_axes = True
        try:
            self._synchronize_axes(source_vb=changed_vb)
        finally:
            self._is_syncing_axes = False

    def _synchronize_axes(self, source_vb: pg.ViewBox):
        """
        Synchronizes Y-axes based on current lock settings.
        - If Scale Locked: Syncs height (zoom level).
        - If Axes Locked: Syncs relative zero position.
        - If Both: Syncs both (effectively identical ranges).
        """
        if len(self.y_axes) < 2:
            return

        # Get source parameters
        src_range = source_vb.viewRange()[1]
        src_min, src_max = src_range
        src_height = src_max - src_min
        if src_height == 0: return

        # Calculate the relative position of the zero line on the source axis
        src_zero_rel = -src_min / src_height

        # Apply to all other axes
        for axis_info in self.y_axes.values():
            vb = axis_info['viewbox']
            if vb is source_vb:
                continue 

            curr_range = vb.viewRange()[1]
            curr_min, curr_max = curr_range
            curr_height = curr_max - curr_min
            if curr_height == 0: continue
            
            # Step 1: Determine new height (zoom)
            new_height = curr_height
            if self.scale_locked:
                new_height = src_height
            
            # Step 2: Determine new position (min)
            if self.axes_locked:
                # Align 0 position relative to height
                new_min = -src_zero_rel * new_height
            else:
                if self.scale_locked:
                    # If only scale is locked, preserve the current center of the target view
                    curr_center = (curr_min + curr_max) / 2
                    new_min = curr_center - (new_height / 2)
                else:
                    # If no lock active (shouldn't happen in this loop), keep current
                    new_min = curr_min

            new_max = new_min + new_height
            vb.setYRange(new_min, new_max, padding=0)

    def add_or_update_plot(self, name: str, data: dict, color: QColor, style, thickness: float = 1.0, layer_priority: int = 0):
        """Adds a new plot or updates an existing one by name."""
        self._add_or_update_plot_impl(name, data, color, style, layer_priority=layer_priority, thickness=thickness)

    def add_or_update_plot_with_custom_colors(self, name: str, data: dict, color: QColor, style, layer_priority: int = 0, thickness: float = 1.0):
        """Adds a new plot or updates an existing one by name with custom layer priority."""
        self._add_or_update_plot_impl(name, data, color, style, layer_priority, thickness=thickness)

    def _add_or_update_plot_impl(self, name: str, data: dict, color: QColor, style, layer_priority: int = 0, thickness: float = 1.0):
        """Internal implementation for adding/updating plots with layer priority."""
        if name in self.plots:
            self.remove_plot(name)

        x, y = data['x'], data['y']
        std = data.get('std')
        y_col_name = data.get('y_col')
        error_alpha_multiplier = data.get('error_alpha_multiplier', 0.5)
        error_layer_priority = data.get('error_layer_priority')
        error_color = data.get('error_color', color)

        if not y_col_name:
            return

        # --- Y-Axis and ViewBox Management ---
        if y_col_name not in self.y_axes:
            if not self.y_axes: # First axis is the default left one
                vb = self.plot_item.getViewBox()
                self.y_axes[y_col_name] = {
                    'axis': self.plot_item.getAxis('left'),
                    'viewbox': vb
                }
            else:  # Create a new ViewBox and Axis on the right for subsequent plots
                vb = pg.ViewBox()
                ax = ColoredAxis('right')

                # Assign a decreasing Z-value so that the first added right axis (inner) stays 'above' subsequent axes (outer) in the scene stack regarding event capture.
                ax.setZValue(1000 - len(self.y_axes))

                self.plot_item.layout.addItem(ax, 2, len(self.y_axes) + 2)
                self.plot_item.scene().addItem(vb)
                ax.linkToView(vb)
                vb.setXLink(self.plot_item.getViewBox())
                self.y_axes[y_col_name] = {'axis': ax, 'viewbox': vb}
            
            # If any lock is active, connect the signal to the newly created viewbox
            if self.axes_locked or self.scale_locked:
                self.y_axes[y_col_name]['viewbox'].sigRangeChanged.connect(self._on_axis_range_changed)

        view_box = self.y_axes[y_col_name]['viewbox']

        # --- Create Plot Items ---
        pen = pg.mkPen(color=color, style=style, width=thickness)
        plot_data_item = pg.PlotDataItem(x, y, pen=pen, name=name)
        
        error_item = None
        if std is not None and np.any(std):
            r, g, b, a = error_color.getRgb()
            brush_alpha = max(0, min(255, int(a * error_alpha_multiplier)))
            brush = pg.mkBrush(color=pg.mkColor(r, g, b, brush_alpha))
            
            # FIX: Initialize FillBetweenItem with its required curve arguments
            error_item = pg.FillBetweenItem(
                curve1=pg.PlotDataItem(x, y - std),
                curve2=pg.PlotDataItem(x, y + std),
                brush=brush
            )
            view_box.addItem(error_item)
        
        view_box.addItem(plot_data_item)
        
        z_value = layer_priority * 10
        if error_item:
            if error_layer_priority is not None:
                error_item.setZValue(error_layer_priority * 10)
            else:
                error_item.setZValue(z_value)
        plot_data_item.setZValue(z_value + 1)
        
        # If this plot has high priority (e.g. selected), ensure its ViewBox is also on top
        # to prevent it from being obscured by other ViewBoxes (axes).
        if layer_priority > 0:
            view_box.setZValue(100)
        
        self.plots[name] = {
            'item': plot_data_item, 
            'error_item': error_item, 
            'view_box': view_box,
            'layer_priority': layer_priority,
            'y_col': y_col_name,  # Store y_col for export
            'y_label': data.get('y_label', y_col_name),
            'x_col': data.get('x_col', ''),
            'x_label': data.get('x_label', data.get('x_col', ''))
        }
        self.update_views()

    def remove_plot(self, name: str):
        """Removes a plot and its associated items from the graph."""
        if name in self.plots:
            plot_info = self.plots.pop(name)
            if plot_info['item']:
                plot_info['view_box'].removeItem(plot_info['item'])
            if plot_info['error_item']:
                plot_info['view_box'].removeItem(plot_info['error_item'])
    
    def clear_all_plots(self):
        """Removes all plots, axes, and viewboxes from the graph. Preserves lock states."""
        # Disconnect signals from current viewboxes before deleting them
        for axis_info in self.y_axes.values():
            try:
                axis_info['viewbox'].sigRangeChanged.disconnect(self._on_axis_range_changed)
            except (TypeError, RuntimeError):
                pass

        for name in list(self.plots.keys()):
            self.remove_plot(name)
            
        # Clear the dynamically added axes and viewboxes.
        # Teardown must mirror creation exactly (layout.addItem + scene().addItem +
        # linkToView). Taking the axis out of the layout alone leaves it parented in
        # the scene - still painted, and still linked to a ViewBox we are about to
        # drop - which crashes GraphicsView.paintEvent once that ViewBox is collected.
        for y_col, axis_info in list(self.y_axes.items()):
            axis, viewbox = axis_info['axis'], axis_info['viewbox']
            # Don't remove the default left axis
            if axis is not self.plot_item.getAxis('left'):
                self.plot_item.layout.removeItem(axis)
                try:
                    axis.unlinkFromView()
                except Exception:
                    pass
                if axis.scene():
                    axis.scene().removeItem(axis)
                if viewbox.scene():
                    self.plot_item.scene().removeItem(viewbox)

        self.plots.clear()
        self.y_axes.clear()
        self.y_axis_colors.clear()  # Clear stored colors
        self.y_axis_labels.clear()  # Clear stored labels
        self.x_axis_label = ""       # Clear x-axis label
        
        # Reset main ViewBox Z-value to default
        self.plot_item.getViewBox().setZValue(0)
        
        # Note: axes_locked and scale_locked flags are preserved

    def update_views(self):
        """Updates the geometry of all viewboxes to match the main one."""
        main_vb = self.plot_item.getViewBox()
        if not main_vb or not main_vb.scene():
            return
            
        # Ensure the main viewbox geometry is correct first
        main_vb_rect = self.plot_item.vb.sceneBoundingRect()
        main_vb.setGeometry(main_vb_rect)
        
        # Update all other viewboxes to match this geometry
        for axis_info in self.y_axes.values():
            vb = axis_info['viewbox']
            if vb is not main_vb:
                vb.setGeometry(main_vb_rect)
                vb.linkedViewChanged(main_vb, vb.XAxis)

    def set_axis_labels(self, x_label: str, y_labels: Dict[str, str]):
        self.plot_item.setLabel('bottom', text=x_label)
        self.x_axis_label = x_label  # Store for export
        
        # Reset labels for all managed axes
        for axis_info in self.y_axes.values():
            axis_info['axis'].setLabel(text="")

        # Set new labels only for currently plotted data
        for y_col, label in y_labels.items():
            if y_col in self.y_axes:
                self.y_axes[y_col]['axis'].setLabel(text=label)
                self.y_axis_labels[y_col] = label  # Store for export

    def set_axis_color(self, y_col: str, color: QColor):
        """Sets the color of a specific y-axis, including its label and axis line.
        Grid lines remain black/gray regardless of axis color."""
        if y_col in self.y_axes:
            axis = self.y_axes[y_col]['axis']
            # Set text color
            axis.setTextPen(color)
            # Use setAxisPen for ColoredAxis to only affect axis line, not grid
            if isinstance(axis, ColoredAxis):
                axis.setAxisPen(pg.mkPen(color=color, width=1))
            else:
                axis.setPen(color)
            self.y_axis_colors[y_col] = color  # Store for export

    @staticmethod
    def _viewbox_axis_inverted(viewbox, axis: str) -> bool:
        key = 'xInverted' if axis == 'x' else 'yInverted'
        return bool(getattr(viewbox, 'state', {}).get(key, False))

    @staticmethod
    def _oriented_range(values, inverted: bool):
        if not values or len(values) != 2:
            return values
        return [values[1], values[0]] if inverted else values

    def _x_inverted(self) -> bool:
        return self._viewbox_axis_inverted(self.plot_item.getViewBox(), 'x')

    def _y_inverted(self, y_col: str) -> bool:
        if y_col in self.y_axes:
            return self._viewbox_axis_inverted(self.y_axes[y_col]['viewbox'], 'y')
        return False

    @staticmethod
    def _format_export_value(value):
        if value is None:
            return ''
        try:
            if np.isnan(value):
                return ''
        except TypeError:
            pass
        return str(value)

    @staticmethod
    def _subset_positions(base_x, sub_x):
        base = np.asarray(base_x, dtype=float)
        sub = np.asarray(sub_x, dtype=float)
        if len(sub) > len(base):
            return None

        positions = []
        search_start = 0
        for value in sub:
            if search_start >= len(base):
                return None
            matches = np.where(np.isclose(base[search_start:], value, rtol=1e-9, atol=1e-12))[0]
            if len(matches) == 0:
                return None
            pos = search_start + int(matches[0])
            positions.append(pos)
            search_start = pos + 1
        return positions

    @staticmethod
    def _align_values(length, positions, values):
        aligned = [None] * length
        for pos, value in zip(positions, values):
            aligned[pos] = value
        return aligned

    def export_plot(self, filename: str, figsize=None):
        # Dispatch to text export if applicable
        if filename.lower().endswith(('.csv', '.tsv')):
            self._export_text_data(filename)
            return

        try:
            import matplotlib.pyplot as plt
        except ImportError:
            print("Matplotlib is required for exporting.")
            return

        plots_by_yaxis = {}
        # Track max priority per axis to sort axes later
        axis_max_priority = {}
        
        for name, plot_info in self.plots.items():
            item = plot_info.get('item')
            if not item or not item.isVisible(): continue
            y_col = plot_info.get('y_col')
            if not y_col: continue
            if y_col not in plots_by_yaxis: plots_by_yaxis[y_col] = []
            plots_by_yaxis[y_col].append((name, plot_info))
            
            prio = plot_info.get('layer_priority', 0)
            if y_col not in axis_max_priority:
                axis_max_priority[y_col] = prio
            else:
                axis_max_priority[y_col] = max(axis_max_priority[y_col], prio)
        
        # Sort plots within each axis by priority
        for y_col in plots_by_yaxis:
            plots_by_yaxis[y_col].sort(key=lambda x: x[1].get('layer_priority', 0))

        if not plots_by_yaxis: return
        
        y_axis_order = [y_col for y_col in self.y_axes.keys() if y_col in plots_by_yaxis]
        # Sort axes so that higher priority ones are drawn later (on top)
        y_axis_order.sort(key=lambda y: axis_max_priority.get(y, 0))
        
        if not y_axis_order: return
        
        fig, ax_primary = plt.subplots(figsize=figsize if figsize else (10, 6))
        
        matplotlib_axes = {y_axis_order[0]: ax_primary}
        ax_primary.spines['top'].set_visible(False)
        ax_primary.spines['right'].set_visible(False)
        
        for i, y_col in enumerate(y_axis_order[1:], start=1):
            ax_new = ax_primary.twinx()
            ax_new.spines['top'].set_visible(False)
            ax_new.spines['left'].set_visible(False)
            if i > 1: ax_new.spines['right'].set_position(('outward', 60 * (i - 1)))
            matplotlib_axes[y_col] = ax_new
        
        # Apply Limits, preserving PyQtGraph axis inversion.
        vb_main = self.plot_item.getViewBox()
        ax_primary.set_xlim(self._oriented_range(vb_main.viewRange()[0], self._x_inverted()))

        for y_col in y_axis_order:
            ax = matplotlib_axes[y_col]
            if y_col in self.y_axes:
                vb = self.y_axes[y_col]['viewbox']
                ax.set_ylim(self._oriented_range(vb.viewRange()[1], self._y_inverted(y_col)))

        # Plot Curves
        all_handles = []
        all_labels = []
        
        for y_col in y_axis_order:
            ax = matplotlib_axes[y_col]
            axis_color = self.y_axis_colors.get(y_col, QColor("black"))
            mpl_axis_color = axis_color.getRgbF()[:3]
            
            ax.spines['left' if y_col == y_axis_order[0] else 'right'].set_edgecolor(mpl_axis_color)
            ax.tick_params(axis='y', colors=mpl_axis_color, direction='in')
            ax.yaxis.label.set_color(mpl_axis_color)
            ax.set_ylabel(self.y_axis_labels.get(y_col, y_col))
            
            for name, plot_info in plots_by_yaxis[y_col]:
                item = plot_info['item']
                data = item.getData()
                if not all(d is not None for d in data) or len(data[0]) == 0: continue
                
                pen = item.opts['pen']
                color = pen.color().getRgbF()[:3]
                width = pen.width()
                linestyle = self._get_mpl_linestyle(pen.style())
                
                error_item = plot_info.get('error_item')
                has_error_band = error_item is not None
                
                # Z-order derived from priority (Orig=0, Std=1, Mean=2)
                # + 2.0 ensures we are above default grid (0.5) and patches (1.0)
                z_val = 2.0 + plot_info.get('layer_priority', 0)

                if has_error_band:
                    # ONLY plot the error band.
                    c1 = error_item.curves[0].getData()
                    c2 = error_item.curves[1].getData()
                    if all(d is not None for d in c1) and all(d is not None for d in c2):
                        fill = ax.fill_between(c1[0], c1[1], c2[1], color=color, 
                                             alpha=0.25, linewidth=0, label=name, zorder=z_val)
                        all_handles.append(fill)
                        all_labels.append(name)
                else:
                    # Plot normal line (no error band)
                    line, = ax.plot(data[0], data[1], color=color, label=name, 
                                  linewidth=width, linestyle=linestyle, zorder=z_val)
                    all_handles.append(line)
                    all_labels.append(name)
        
        ax_primary.set_xlabel(self.x_axis_label)
        ax_primary.spines['bottom'].set_edgecolor('black')
        ax_primary.tick_params(axis='x', colors='black', direction='in')
        ax_primary.xaxis.label.set_color('black')
        ax_primary.grid(True, alpha=0.3)
        
        if all_handles:
            # Place legend on the last added axis (top of stack) to ensure z-order
            target_ax = matplotlib_axes[y_axis_order[-1]]
            leg = target_ax.legend(all_handles, all_labels, loc='best')
            leg.set_zorder(10000) # Ensure legend is on top of everything
        
        fig.tight_layout()
        try:
            fig.savefig(filename, bbox_inches='tight', dpi=300)
            print(f"Plot exported to {filename}")
        except Exception as e:
            print(f"Failed to save plot: {e}")
        finally:
            plt.close(fig)

    def _export_text_data(self, filename: str):
        """Internal handler for exporting data to CSV or TSV."""
        import csv

        delimiter = '\t' if filename.lower().endswith('.tsv') else ','

        plots_by_yaxis = {}
        axis_max_priority = {}
        for name, plot_info in self.plots.items():
            if name.endswith("_running_mean_std"):
                continue

            item = plot_info.get('item')
            if not item or not item.isVisible():
                continue
            y_col = plot_info.get('y_col')
            if not y_col:
                continue

            plots_by_yaxis.setdefault(y_col, []).append((name, plot_info))
            axis_max_priority[y_col] = max(axis_max_priority.get(y_col, 0), plot_info.get('layer_priority', 0))

        if not plots_by_yaxis:
            return

        y_axis_order = [y_col for y_col in self.y_axes.keys() if y_col in plots_by_yaxis]
        y_axis_order.sort(key=lambda y: axis_max_priority.get(y, 0))

        sorted_plot_list = []
        for y_col in y_axis_order:
            sorted_plot_list.extend(sorted(plots_by_yaxis[y_col], key=lambda x: x[1].get('layer_priority', 0)))

        x_groups = []

        for name, plot_info in sorted_plot_list:
            item = plot_info['item']
            x_data, y_data = item.getData()
            if x_data is None or y_data is None:
                continue

            x_values = np.asarray(x_data)
            y_values = np.asarray(y_data)
            std_data = None

            std_plot_name = name + "_std"
            if std_plot_name in self.plots:
                error_item = self.plots[std_plot_name].get('error_item')
                if error_item:
                    c1 = error_item.curves[0].getData()
                    c2 = error_item.curves[1].getData()
                    if c1[1] is not None and c2[1] is not None:
                        std_data = np.abs(c2[1] - c1[1]) / 2.0
            elif plot_info.get('error_item'):
                error_item = plot_info.get('error_item')
                c1 = error_item.curves[0].getData()
                c2 = error_item.curves[1].getData()
                if c1[1] is not None and c2[1] is not None:
                    std_data = np.abs(c2[1] - c1[1]) / 2.0

            dataset = {
                'name': name,
                'info': plot_info,
                'x': x_values,
                'y': y_values,
                'std': np.asarray(std_data) if std_data is not None else None,
            }

            target_group = None
            target_positions = None
            for group in x_groups:
                positions = self._subset_positions(group['x'], x_values)
                if positions is not None:
                    target_group = group
                    target_positions = positions
                    break

            if target_group is None:
                x_label = plot_info.get('x_label', plot_info.get('x_col', self.x_axis_label))
                target_group = {'x': x_values, 'x_label': x_label, 'datasets': []}
                target_positions = list(range(len(x_values)))
                x_groups.append(target_group)

            target_group['datasets'].append({
                'name': dataset['name'],
                'info': dataset['info'],
                'y': self._align_values(len(target_group['x']), target_positions, dataset['y']),
                'std': self._align_values(len(target_group['x']), target_positions, dataset['std']) if dataset['std'] is not None else None,
            })

        header_row_1 = []
        header_row_2 = []
        header_row_3 = []
        data_columns = []
        max_rows = 0
        x_inverted = self._x_inverted()

        for group in x_groups:
            order = list(range(len(group['x'])))
            if x_inverted:
                order.reverse()
            rows_in_group = len(order)
            max_rows = max(max_rows, rows_in_group)

            header_row_1.append('x')
            header_row_2.append(group.get('x_label', self.x_axis_label))
            header_row_3.append('')
            data_columns.append([group['x'][i] for i in order])

            for ds in group['datasets']:
                y_col_name = ds['info'].get('y_col', '')
                y_axis_label = self.y_axis_labels.get(y_col_name, y_col_name)

                header_row_1.append('y')
                header_row_2.append(y_axis_label)
                header_row_3.append(ds['name'])
                data_columns.append([ds['y'][i] for i in order])

                if ds['std'] is not None:
                    header_row_1.append('std')
                    header_row_2.append('')
                    header_row_3.append('')
                    data_columns.append([ds['std'][i] for i in order])

        try:
            with open(filename, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f, delimiter=delimiter)
                writer.writerow(header_row_1)
                writer.writerow(header_row_2)
                writer.writerow(header_row_3)

                for i in range(max_rows):
                    row_data = []
                    for col in data_columns:
                        row_data.append(self._format_export_value(col[i]) if i < len(col) else '')
                    writer.writerow(row_data)
            print(f"Data exported to {filename}")
        except Exception as e:
            print(f"Failed to save data: {e}")

    def get_current_plot_state(self) -> Dict[str, Any]:
        """Extracts current state for PopOutWindow."""
        state = {
            'title': "", 
            'x_label': self.x_axis_label,
            'x_limits': self.plot_item.getViewBox().viewRange()[0],
            'x_inverted': self._x_inverted(),
            'y_axes': {}
        }

        for name, plot_info in self.plots.items():
            item = plot_info.get('item')
            if not item or not item.isVisible(): continue

            y_col = plot_info.get('y_col')
            if not y_col: continue

            if y_col not in state['y_axes']:
                vb = self.y_axes[y_col]['viewbox']
                state['y_axes'][y_col] = {
                    'label': self.y_axis_labels.get(y_col, y_col),
                    'color': self.y_axis_colors.get(y_col, QColor("black")),
                    'y_limits': vb.viewRange()[1],
                    'y_inverted': self._y_inverted(y_col),
                    'series': []
                }

            x, y = item.getData()
            if x is None or y is None: continue
            
            std_data = None
            error_item = plot_info.get('error_item')
            if error_item:
                c1 = error_item.curves[0].getData()
                c2 = error_item.curves[1].getData()
                if c1[1] is not None and c2[1] is not None:
                    std_data = np.abs(c2[1] - c1[1]) / 2.0

            pen = item.opts['pen']
            
            # Pass translated style to PopOutWindow
            series_entry = {
                'id': f"{y_col}_{name}",
                'name': name,
                'x': np.array(x),
                'y': np.array(y),
                'std': np.array(std_data) if std_data is not None else None,
                'color': pen.color(),
                'linestyle_qt': pen.style(), # Raw Enum/Int for debug/qt use
                'linestyle_matlab': self._get_mpl_linestyle(pen.style()), # Translated string
                'width': pen.width(),
                'layer_priority': plot_info.get('layer_priority', 0)
            }
            state['y_axes'][y_col]['series'].append(series_entry)
        
        for y_col in state['y_axes']:
            state['y_axes'][y_col]['series'].sort(key=lambda s: s['layer_priority'])
            
        return state

    def get_global_y_range(self):
        """Calculates the global min and max Y values across all visible plots."""
        global_min = float('inf')
        global_max = float('-inf')
        found_data = False

        for plot_info in self.plots.values():
            item = plot_info.get('item')
            if not item or not item.isVisible():
                continue
            
            _, y = item.getData()
            if y is not None and len(y) > 0:
                try:
                    y_min = np.nanmin(y)
                    y_max = np.nanmax(y)
                    if np.isfinite(y_min) and np.isfinite(y_max):
                        found_data = True
                        global_min = min(global_min, float(y_min))
                        global_max = max(global_max, float(y_max))
                except Exception:
                    pass
                
                # Check error band if present
                error_item = plot_info.get('error_item')
                if error_item:
                    c1 = error_item.curves[0].getData()
                    c2 = error_item.curves[1].getData()
                    if c1[1] is not None and len(c1[1]) > 0:
                        try:
                            c1_min = np.nanmin(c1[1])
                            if np.isfinite(c1_min):
                                found_data = True
                                global_min = min(global_min, float(c1_min))
                        except Exception:
                            pass
                    if c2[1] is not None and len(c2[1]) > 0:
                        try:
                            c2_max = np.nanmax(c2[1])
                            if np.isfinite(c2_max):
                                found_data = True
                                global_max = max(global_max, float(c2_max))
                        except Exception:
                            pass

        if not found_data or not np.isfinite(global_min) or not np.isfinite(global_max):
            return None
        return global_min, global_max

    def reset_view(self):
        """
        Resets the view based on current lock states.
        Calculates ranges manually to avoid pyqtgraph auto-range issues.
        """
        # Always reset X to auto first
        self.plot_item.enableAutoRange(axis='x')

        # 1. Gather all active ViewBoxes and their specific data bounds
        axes_data = []
        
        # Main ViewBox
        main_vb = self.plot_item.getViewBox()
        dmin, dmax = self._get_data_bounds_for_viewbox(main_vb)
        axes_data.append({'vb': main_vb, 'min': dmin, 'max': dmax})
        
        # Aux ViewBoxes
        for axis_info in self.y_axes.values():
            vb = axis_info['viewbox']
            if vb is not main_vb:
                dmin, dmax = self._get_data_bounds_for_viewbox(vb)
                axes_data.append({'vb': vb, 'min': dmin, 'max': dmax})

        # Padding factor (e.g., 5%)
        pad = 0.05

        # --- SCENARIO 1: BOTH LOCKS (Global Union) ---
        if self.axes_locked and self.scale_locked:
            # Find global min/max
            gmin, gmax = float('inf'), float('-inf')
            has_data = False
            for d in axes_data:
                if d['min'] is not None:
                    gmin = min(gmin, d['min'])
                    gmax = max(gmax, d['max'])
                    has_data = True
            
            if not has_data:
                gmin, gmax = 0.0, 1.0
            
            # Apply global range to ALL viewboxes
            for d in axes_data:
                d['vb'].setYRange(gmin, gmax, padding=pad)

        # --- SCENARIO 2: AXES LOCK ONLY (Smart Zero Alignment) ---
        elif self.axes_locked:
            # Step A: Calculate Ideal Ratio (R) for each axis
            ideal_ratios = []
            
            for d in axes_data:
                dmin, dmax = d['min'], d['max']
                if dmin is None: continue

                span = dmax - dmin
                if span == 0: span = 1.0 if dmax == 0 else abs(dmax)

                if dmin >= 0:
                    r_i = 0.0 # All positive -> 0 at bottom
                elif dmax <= 0:
                    r_i = 1.0 # All negative -> 0 at top
                else:
                    # Crossing zero -> R is fraction of space for negative
                    r_i = abs(dmin) / (abs(dmin) + dmax)
                
                ideal_ratios.append(r_i)
            
            target_R = float(np.median(ideal_ratios)) if ideal_ratios else 0.5

            # Step B: Scale each axis individually to fit data into target_R
            for d in axes_data:
                vb, dmin, dmax = d['vb'], d['min'], d['max']
                if dmin is None:
                    vb.setYRange(-target_R, 1-target_R, padding=pad)
                    continue

                # Calculate Height (H) required
                req_h_min = 0.0
                req_h_max = 0.0
                
                if target_R > 1e-6 and dmin < 0:
                    req_h_min = abs(dmin) / target_R
                
                if (1 - target_R) > 1e-6 and dmax > 0:
                    req_h_max = dmax / (1 - target_R)
                
                H = max(req_h_min, req_h_max)
                if H == 0: H = 1.0
                
                # Apply bounds
                new_min = -H * target_R
                new_max = H * (1 - target_R)
                vb.setYRange(new_min, new_max, padding=pad)

        # --- SCENARIO 3: SCALE LOCK ONLY (Uniform Zoom) ---
        elif self.scale_locked:
            # Find the Maximum Data Span across all axes
            max_span = 0.0
            for d in axes_data:
                if d['min'] is not None:
                    span = d['max'] - d['min']
                    if span > max_span: max_span = span
            
            if max_span == 0: max_span = 1.0
            
            # Apply Max Span to each axis, centered on its own data
            # This ensures every axis has the same 'zoom level' (units per pixel)
            for d in axes_data:
                vb, dmin, dmax = d['vb'], d['min'], d['max']
                if dmin is None:
                    center = 0.5
                else:
                    center = (dmin + dmax) / 2.0
                
                half = max_span / 2.0
                vb.setYRange(center - half, center + half, padding=pad)

        # --- SCENARIO 4: NO LOCKS (Pure Independent) ---
        else:
            # Replicate the "Auto Scale" button behavior exactly.
            # This enables continuous auto-scaling and hides the 'A' symbol.
            for d in axes_data:
                d['vb'].enableAutoRange(axis='y')

    def get_view_ranges(self) -> Dict[str, Any]:
        """Returns a dictionary of view ranges for all active axes."""
        ranges = {}
        # Main ViewBox (associated with left axis)
        ranges['main'] = self.plot_item.getViewBox().viewRange()
        
        # Auxiliary Axes
        for y_col, axis_info in self.y_axes.items():
            if axis_info['viewbox'] is not self.plot_item.getViewBox():
                ranges[y_col] = axis_info['viewbox'].viewRange()
        return ranges

    def set_view_ranges(self, ranges: Dict[str, Any]):
        """Restores view ranges from a dictionary."""
        if not ranges: return

        was_syncing = self._is_syncing_axes
        self._is_syncing_axes = True
        try:
            # Restore Main
            if 'main' in ranges:
                xr, yr = ranges['main']
                self.plot_item.getViewBox().setRange(xRange=xr, yRange=yr, padding=0)

            # Restore Aux
            for y_col, axis_info in self.y_axes.items():
                if y_col in ranges:
                    xr, yr = ranges[y_col]
                    axis_info['viewbox'].setYRange(yr[0], yr[1], padding=0)
                    # X is linked to main, so no need to set X
        finally:
            self._is_syncing_axes = was_syncing

    def _get_data_bounds_for_viewbox(self, viewbox: pg.ViewBox):
        """Helper to get min/max Y data from all visible plots associated with a given ViewBox."""
        vmin = float('inf')
        vmax = float('-inf')
        has_data = False

        for plot_info in self.plots.values():
            if plot_info['view_box'] is viewbox:
                item = plot_info.get('item')
                if not item or not item.isVisible():
                    continue
                
                _, y = item.getData()
                if y is not None and len(y) > 0:
                    # Use nan-aware min/max to handle outer-joined NaNs from multi-header files
                    try:
                        y_min = np.nanmin(y)
                        y_max = np.nanmax(y)
                    except (ValueError, RuntimeWarning):
                        continue
                    if not np.isfinite(y_min) or not np.isfinite(y_max):
                        continue
                    has_data = True
                    vmin = min(vmin, float(y_min))
                    vmax = max(vmax, float(y_max))
                    
                    error_item = plot_info.get('error_item')
                    if error_item:
                        # FillBetweenItem stores curves. Check their data.
                        c1 = error_item.curves[0].getData()
                        c2 = error_item.curves[1].getData()
                        if c1[1] is not None and len(c1[1]) > 0:
                            try:
                                c1_min = np.nanmin(c1[1])
                                if np.isfinite(c1_min):
                                    has_data = True
                                    vmin = min(vmin, float(c1_min))
                            except Exception:
                                pass
                        if c2[1] is not None and len(c2[1]) > 0:
                            try:
                                c2_max = np.nanmax(c2[1])
                                if np.isfinite(c2_max):
                                    has_data = True
                                    vmax = max(vmax, float(c2_max))
                            except Exception:
                                pass
        
        if not has_data or not np.isfinite(vmin) or not np.isfinite(vmax):
            return None, None
        return vmin, vmax

    def _get_mpl_linestyle(self, qt_style):
        """Maps Qt PenStyle enums/ints to Matplotlib linestyle strings."""
        # Safely extract integer value from Qt.PenStyle enum or use directly if int
        if hasattr(qt_style, 'value'):
            style_int = qt_style.value
        else:
            style_int = int(qt_style)
        
        # 1: Solid, 2: Dash, 3: Dot, 4: DashDot, 5: DashDotDot
        mapping = {
            1: '-', 
            2: '--', 
            3: ':', 
            4: '-.', 
            5: (0, (3, 1, 1, 1, 1, 1)) # Matplotlib tuple for DashDotDot
        }
        return mapping.get(style_int, '-')
