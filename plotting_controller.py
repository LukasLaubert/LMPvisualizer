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
        self._is_updating_ranges = False
        self._master_viewbox = None

    def toggle_axes_lock(self, locked: bool):
        self.axes_locked = locked
        if self._master_viewbox:
            if locked:
                self._master_viewbox.sigRangeChanged.connect(self._on_master_range_changed)
                # Trigger a sync immediately
                self._on_master_range_changed()
            else:
                try:
                    self._master_viewbox.sigRangeChanged.disconnect(self._on_master_range_changed)
                except (TypeError, RuntimeError):
                    pass # Ignore if not connected

    def _on_master_range_changed(self):
        if self._is_updating_ranges or not self.axes_locked or not self._master_viewbox:
            return

        self._is_updating_ranges = True
        
        try:
            y_range = self._master_viewbox.viewRange()[1]
            height = y_range[1] - y_range[0]
            if height == 0: return

            # Relative position of the zero line on the master axis (0.0 to 1.0)
            zero_pos = -y_range[0] / height

            for y_col, axis_info in self.y_axes.items():
                vb = axis_info['viewbox']
                if vb is self._master_viewbox:
                    continue

                current_range = vb.viewRange()[1]
                current_height = current_range[1] - current_range[0]
                if current_height == 0: continue
                
                new_min = -zero_pos * current_height
                new_max = (1 - zero_pos) * current_height
                
                vb.setYRange(new_min, new_max, padding=0)

        finally:
            self._is_updating_ranges = False

    def add_or_update_plot(self, name: str, data: dict, color: QColor, style, thickness: float = 1.0):
        """Adds a new plot or updates an existing one by name."""
        self._add_or_update_plot_impl(name, data, color, style, layer_priority=0, thickness=thickness)

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

        # Prevent crash if y-axis column name is not provided
        if not y_col_name:
            return

        # --- Y-Axis and ViewBox Management ---
        if y_col_name not in self.y_axes:
            if not self.y_axes: # First axis is the default one
                vb = self.plot_item.getViewBox()
                self.y_axes[y_col_name] = {
                    'axis': self.plot_item.getAxis('left'),
                    'viewbox': vb
                }
                self._master_viewbox = vb
            else: # Create a new ViewBox and Axis for subsequent plots
                vb = pg.ViewBox()
                ax = pg.AxisItem('right')
                # Position the new axis to the right of the previous one
                self.plot_item.layout.addItem(ax, 2, len(self.y_axes) + 2)
                self.plot_item.scene().addItem(vb)
                ax.linkToView(vb)
                vb.setXLink(self.plot_item.getViewBox())
                self.y_axes[y_col_name] = {'axis': ax, 'viewbox': vb}
        
        view_box = self.y_axes[y_col_name]['viewbox']

        # --- Create Plot Items ---
        pen = pg.mkPen(color=color, style=style, width=thickness)
        plot_data_item = pg.PlotDataItem(x, y, pen=pen, name=name)
        
        error_item = None
        if std is not None and np.any(std):
            # Create brush with color based on the original color but with 2/3 saturation and 50% opacity
            # Get the RGB values of the original color
            r, g, b, a = color.getRgb()
            # Create the brush with the same hue but 2/3 saturation and 50% opacity
            brush = pg.mkBrush(color=pg.mkColor(r, g, b, int(a * 0.5)))  # 50% opacity
            error_item = pg.FillBetweenItem(
                pg.PlotDataItem(x, y - std),
                pg.PlotDataItem(x, y + std),
                brush=brush
            )
            # Add error item first (background), then plot data item (foreground)
            view_box.addItem(error_item)
            view_box.addItem(plot_data_item)
        else:
            view_box.addItem(plot_data_item)
        
        # Set z-value based on layer priority for proper layering
        # Higher layer_priority should be in front (higher z-value)
        z_value = layer_priority * 10  # Use multiples for clear z-ordering
        
        if error_item:
            error_item.setZValue(z_value)  # Set std band z-value
        plot_data_item.setZValue(z_value + 1)  # Set curve z-value slightly higher than std band
        
        # Store all items associated with the plot name
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
            plot_info['view_box'].removeItem(plot_info['item'])
            if plot_info['error_item']:
                plot_info['view_box'].removeItem(plot_info['error_item'])
    
    def clear_all_plots(self):
        """Removes all plots from the graph."""
        self.toggle_axes_lock(False) # Ensure lock is disengaged
        self._master_viewbox = None
        for name in list(self.plots.keys()):
            self.remove_plot(name)
        # Also clear the dynamically added axes
        for y_col, axis_info in list(self.y_axes.items()):
            if axis_info['axis'] is not self.plot_item.getAxis('left'):
                self.plot_item.layout.removeItem(axis_info['axis'])
                self.plot_item.scene().removeItem(axis_info['viewbox'])
        self.y_axes.clear()


    def update_views(self):
        """Updates the geometry of all viewboxes to match the main one."""
        main_vb = self.plot_item.getViewBox()
        if not main_vb.scene(): # Skip if scene is not set
            return
        main_vb.setGeometry(self.plot_item.vb.sceneBoundingRect())
        
        for axis_info in self.y_axes.values():
            vb = axis_info['viewbox']
            if vb is not main_vb:
                vb.setGeometry(main_vb.sceneBoundingRect())
                vb.linkedViewChanged(main_vb, vb.XAxis)

    def set_axis_labels(self, x_label: str, y_labels: Dict[str, str]):
        self.plot_item.setLabel('bottom', text=x_label)
        
        # Reset all y-axis labels first
        for y_col, axis_info in self.y_axes.items():
            axis_info['axis'].setLabel(text="")

        # Set new labels based on what's currently plotted
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
            if p['item'].isVisible():
                data = p['item'].getData()
                pen = p['item'].opts['pen']
                color = pen.color().getRgbF()
                width = pen.width()
                
                error_data = None
                if p['error_item']:
                    curve1_data = p['error_item'].curves[0].getData()
                    curve2_data = p['error_item'].curves[1].getData()
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
