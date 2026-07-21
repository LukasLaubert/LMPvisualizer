# lmp_visualizer/plotting_controller.py

import pyqtgraph as pg
from PyQt6.QtGui import QColor
from typing import Dict, Any
import numpy as np

class PlottingController:
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
        self.axes_locked = False
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
            
            # If locking, reconnect the signal
            if locked:
                vb.sigRangeChanged.connect(self._on_axis_range_changed)
        
        # If we are locking, trigger an initial sync to align everything
        if locked and self.y_axes:
            # Use the first viewbox as the source for the initial sync
            first_vb = next(iter(self.y_axes.values()))['viewbox']
            self._synchronize_y_axes(source_vb=first_vb)

    def _on_axis_range_changed(self, changed_vb: pg.ViewBox):
        """Signal handler that triggers when any connected axis is moved."""
        if self._is_syncing_axes or not self.axes_locked:
            return

        self._is_syncing_axes = True
        try:
            self._synchronize_y_axes(source_vb=changed_vb)
        finally:
            self._is_syncing_axes = False

    def _synchronize_y_axes(self, source_vb: pg.ViewBox):
        """
        Synchronizes all other Y-axes based on the source ViewBox.
        This works by aligning the relative position of the zero-line.
        """
        if len(self.y_axes) < 2:
            return

        y_range = source_vb.viewRange()[1]
        height = y_range[1] - y_range[0]
        if height == 0: return

        # Calculate the relative position of the zero line on the source axis (0.0 to 1.0)
        zero_pos_relative = -y_range[0] / height

        # Apply this relative zero position to all other axes
        for axis_info in self.y_axes.values():
            vb = axis_info['viewbox']
            if vb is source_vb:
                continue # Don't update the one that's being dragged

            current_target_range = vb.viewRange()[1]
            current_target_height = current_target_range[1] - current_target_range[0]
            if current_target_height == 0: continue
            
            # Calculate the new range for the target that preserves its height (zoom)
            # but matches the zero position of the source.
            new_min = -zero_pos_relative * current_target_height
            new_max = (1 - zero_pos_relative) * current_target_height
            
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
                self.plot_item.layout.addItem(ax, 2, len(self.y_axes) + 2)
                self.plot_item.scene().addItem(vb)
                ax.linkToView(vb)
                vb.setXLink(self.plot_item.getViewBox())
                self.y_axes[y_col_name] = {'axis': ax, 'viewbox': vb}
            
            # If axes are locked, connect the signal to the newly created viewbox
            if self.axes_locked:
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
            'layer_priority': layer_priority
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
        """Removes all plots, axes, and viewboxes from the graph."""
        self.toggle_axes_lock(False) # Disengage lock and disconnect all signals
        
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
        
        # Reset labels for all managed axes
        for axis_info in self.y_axes.values():
            axis_info['axis'].setLabel(text="")

        # Set new labels only for currently plotted data
        for y_col, label in y_labels.items():
            if y_col in self.y_axes:
                self.y_axes[y_col]['axis'].setLabel(text=label)

    def set_axis_color(self, y_col: str, color: QColor):
        """Sets the color of a specific y-axis, including its label and pen."""
        if y_col in self.y_axes:
            axis = self.y_axes[y_col]['axis']
            axis.setPen(color)
            axis.setTextPen(color)

    def export_plot(self, filename: str):
        """Exports the current plot view using Matplotlib for high quality output."""
        try:
            import matplotlib.pyplot as plt
        except ImportError:
            print("Matplotlib is required for exporting.")
            return

        fig, ax = plt.subplots()
        
        # This export logic might need adjustment for multiple y-axes,
        # for now, it plots everything on a single matplotlib axis.
        for name, p in self.plots.items():
            item = p.get('item')
            if item and item.isVisible():
                data = item.getData()
                if not all(d is not None for d in data) or len(data[0]) == 0:
                    continue
                
                pen = item.opts['pen']
                color = pen.color().getRgbF()
                width = pen.width()
                
                error_item = p.get('error_item')
                error_data = None
                if error_item:
                    curve1_data = error_item.curves[0].getData()
                    curve2_data = error_item.curves[1].getData()
                    if all(d is not None for d in curve1_data) and all(d is not None for d in curve2_data):
                        error_data = (curve1_data[0], curve1_data[1], curve2_data[1])

                ax.plot(data[0], data[1], color=color, label=name, linewidth=width)
                if error_data:
                    ax.fill_between(error_data[0], error_data[1], error_data[2], color=color, alpha=0.25)
        
        ax.set_xlabel(self.plot_item.getAxis('bottom').labelText)
        ax.set_ylabel(self.plot_item.getAxis('left').labelText) # Note: only shows left axis label
        ax.grid(True)
        ax.legend()
        fig.tight_layout()
        
        try:
            fig.savefig(filename, bbox_inches='tight')
            print(f"Plot exported to {filename}")
        except Exception as e:
            print(f"Failed to save plot: {e}")
        plt.close(fig)