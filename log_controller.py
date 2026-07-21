import pyqtgraph as pg
from PyQt6.QtGui import QColor
from typing import Dict, Any
import numpy as np
import copy

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

    def toggle_axes_lock(self, locked: bool):
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
        
        # If we are locking, trigger an initial sync to align everything
        if self.axes_locked and self.y_axes:
            # Use the main viewbox as source for initial sync
            self._synchronize_axes(source_vb=self.plot_item.getViewBox())

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
        if self.scale_locked and self.y_axes:
            self._synchronize_axes(source_vb=self.plot_item.getViewBox())

    def apply_current_locks(self):
        """Forces synchronization if any locks are active. Useful after reloading plots."""
        if (self.axes_locked or self.scale_locked) and self.y_axes:
            self._synchronize_axes(source_vb=self.plot_item.getViewBox())

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
            else: # Create a new ViewBox and Axis on the right for subsequent plots
                vb = pg.ViewBox()
                ax = pg.AxisItem('right')
                
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
            r, g, b, a = color.getRgb()
            brush = pg.mkBrush(color=pg.mkColor(r, g, b, int(a * 0.5)))
            
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
            error_item.setZValue(z_value)
        plot_data_item.setZValue(z_value + 1)
        
        self.plots[name] = {
            'item': plot_data_item, 
            'error_item': error_item, 
            'view_box': view_box,
            'layer_priority': layer_priority,
            'y_col': y_col_name,  # Store y_col for export
            'y_label': data.get('y_label', y_col_name)
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
            
        # Clear the dynamically added axes and viewboxes
        for y_col, axis_info in list(self.y_axes.items()):
            # Don't remove the default left axis
            if axis_info['axis'] is not self.plot_item.getAxis('left'):
                self.plot_item.layout.removeItem(axis_info['axis'])
                if axis_info['viewbox'].scene():
                    self.plot_item.scene().removeItem(axis_info['viewbox'])

        self.plots.clear()
        self.y_axes.clear()
        self.y_axis_colors.clear()  # Clear stored colors
        self.y_axis_labels.clear()  # Clear stored labels
        self.x_axis_label = ""       # Clear x-axis label
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
        """Sets the color of a specific y-axis, including its label and pen."""
        if y_col in self.y_axes:
            axis = self.y_axes[y_col]['axis']
            axis.setPen(color)
            axis.setTextPen(color)
            self.y_axis_colors[y_col] = color  # Store for export

    def export_plot(self, filename: str):
        """Exports the current plot view using Matplotlib with multiple y-axes support."""
        try:
            import matplotlib.pyplot as plt
            from matplotlib import rcParams
        except ImportError:
            print("Matplotlib is required for exporting.")
            return

        # Group plots by their y-axis column
        plots_by_yaxis = {}
        for name, plot_info in self.plots.items():
            item = plot_info.get('item')
            if not item or not item.isVisible():
                continue
            
            y_col = plot_info.get('y_col')
            if not y_col:
                continue
            
            if y_col not in plots_by_yaxis:
                plots_by_yaxis[y_col] = []
            plots_by_yaxis[y_col].append((name, plot_info))
        
        if not plots_by_yaxis:
            print("No visible plots to export.")
            return
        
        # Determine the order of y-axes (use the order from self.y_axes which matches visual order)
        y_axis_order = [y_col for y_col in self.y_axes.keys() if y_col in plots_by_yaxis]
        
        if not y_axis_order:
            print("No valid y-axes to export.")
            return
        
        # Create figure and primary axis
        fig, ax_primary = plt.subplots(figsize=(10, 6))
        
        # Create additional axes for each y-axis beyond the first
        matplotlib_axes = {y_axis_order[0]: ax_primary}
        ax_primary.spines['top'].set_visible(False)
        ax_primary.spines['right'].set_visible(False)
        
        for i, y_col in enumerate(y_axis_order[1:], start=1):
            ax_new = ax_primary.twinx()
            ax_new.spines['top'].set_visible(False)
            ax_new.spines['left'].set_visible(False) # Fix: Hide the left spine so it doesn't cover the primary axis
            
            # Position right-side axes with offset if there are multiple
            if i > 1:
                # Offset additional right axes
                ax_new.spines['right'].set_position(('outward', 60 * (i - 1)))
            
            matplotlib_axes[y_col] = ax_new
        
        # Plot each curve on its corresponding axis
        all_handles = []
        all_labels = []
        
        for y_col in y_axis_order:
            ax = matplotlib_axes[y_col]
            
            # Get axis color (default to black if not set)
            axis_color = self.y_axis_colors.get(y_col, QColor("black"))
            mpl_axis_color = axis_color.getRgbF()[:3]  # RGB without alpha
            
            # Set axis color
            ax.spines['left' if y_col == y_axis_order[0] else 'right'].set_edgecolor(mpl_axis_color)
            # Added direction='in' to point ticks inside
            ax.tick_params(axis='y', colors=mpl_axis_color, direction='in')
            ax.yaxis.label.set_color(mpl_axis_color)
            
            # Set axis label
            axis_label = self.y_axis_labels.get(y_col, y_col)
            ax.set_ylabel(axis_label)
            
            # Plot all curves for this y-axis
            for name, plot_info in plots_by_yaxis[y_col]:
                item = plot_info['item']
                data = item.getData()
                
                if not all(d is not None for d in data) or len(data[0]) == 0:
                    continue
                
                pen = item.opts['pen']
                color = pen.color().getRgbF()[:3]  # RGB without alpha
                width = pen.width()
                
                # Determine line style
                style_map = {
                    1: '-',      # SolidLine
                    2: '--',     # DashLine
                    3: ':',      # DotLine
                    4: '-.',     # DashDotLine
                }
                linestyle = style_map.get(pen.style(), '-')
                
                # Plot the main line
                line, = ax.plot(data[0], data[1], color=color, label=name, 
                               linewidth=width, linestyle=linestyle)
                
                # Handle error bands
                error_item = plot_info.get('error_item')
                if error_item:
                    curve1_data = error_item.curves[0].getData()
                    curve2_data = error_item.curves[1].getData()
                    if all(d is not None for d in curve1_data) and all(d is not None for d in curve2_data):
                        ax.fill_between(curve1_data[0], curve1_data[1], curve2_data[1], 
                                       color=color, alpha=0.25)
                
                all_handles.append(line)
                all_labels.append(name)
        
        # Set x-axis label and color to black
        ax_primary.set_xlabel(self.x_axis_label)
        ax_primary.spines['bottom'].set_edgecolor('black')
        # Added direction='in' to point ticks inside
        ax_primary.tick_params(axis='x', colors='black', direction='in')
        ax_primary.xaxis.label.set_color('black')
        
        ax_primary.grid(True, alpha=0.3)
        
        # Create a unified legend
        if all_handles:
            ax_primary.legend(all_handles, all_labels, loc='best')
        
        fig.tight_layout()
        
        try:
            fig.savefig(filename, bbox_inches='tight', dpi=300)
            print(f"Plot exported to {filename}")
        except Exception as e:
            print(f"Failed to save plot: {e}")
        finally:
            plt.close(fig)

    def get_current_plot_state(self) -> Dict[str, Any]:
        """
        Extracts the current state of all visible plots, including data and visual properties,
        structured for the PopOutWindow.
        """
        state = {
            'title': "", # User can set this in the popout
            'x_label': self.x_axis_label,
            'y_axes': {}
        }

        # Mapping for Qt Pen Styles to Matplotlib strings
        style_map = {
            1: '-',      # SolidLine
            2: '--',     # DashLine
            3: ':',      # DotLine
            4: '-.',     # DashDotLine
        }

        # Identify visible plots and group by Y-axis column
        for name, plot_info in self.plots.items():
            item = plot_info.get('item')
            if not item or not item.isVisible():
                continue

            y_col = plot_info.get('y_col')
            if not y_col: continue

            # Initialize Y-axis group if missing
            if y_col not in state['y_axes']:
                state['y_axes'][y_col] = {
                    'label': self.y_axis_labels.get(y_col, y_col),
                    'series': []
                }

            # Extract Data
            x_data, y_data = item.getData()
            if x_data is None or y_data is None: continue
            
            # Extract Std Dev Data if available
            std_data = None
            error_item = plot_info.get('error_item')
            if error_item:
                # Extract from FillBetweenItem curves
                c1 = error_item.curves[0].getData()
                c2 = error_item.curves[1].getData()
                # Derive std from the difference (assuming symmetric: y - lower)
                # c1 is lower, c2 is upper usually, or vice versa. 
                # Logic: y +/- std. So std = (upper - lower) / 2
                if c1[1] is not None and c2[1] is not None:
                    diff = np.abs(c2[1] - c1[1])
                    std_data = diff / 2.0

            # Extract Visuals
            pen = item.opts['pen']
            color = pen.color() # QColor
            qt_style = pen.style()
            width = pen.width()
            
            # Unique ID for the widget map
            series_id = f"{y_col}_{name}"

            series_entry = {
                'id': series_id,
                'name': name,
                'x': np.array(x_data),
                'y': np.array(y_data),
                'std': np.array(std_data) if std_data is not None else None,
                'color': color,
                'linestyle_qt': qt_style,
                'linestyle_matlab': style_map.get(qt_style, '-'),
                'width': width
            }

            state['y_axes'][y_col]['series'].append(series_entry)
            
        return state