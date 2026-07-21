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

    def add_or_update_plot(self, name: str, data: dict, color: QColor, style):
        """Adds a new plot or updates an existing one by name."""
        # If the plot already exists, remove it before adding the new one
        if name in self.plots:
            self.remove_plot(name)

        x, y = data['x'], data['y']
        std = data.get('std')
        y_col_name = name.split('_')[-1] if name != '_temp_' else data.get('y_col')

        # --- Y-Axis and ViewBox Management ---
        if y_col_name not in self.y_axes:
            if not self.y_axes: # First axis is the default one
                self.y_axes[y_col_name] = {
                    'axis': self.plot_item.getAxis('left'),
                    'viewbox': self.plot_item.getViewBox()
                }
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
        pen = pg.mkPen(color=color, style=style)
        plot_data_item = pg.PlotDataItem(x, y, pen=pen, name=name)
        
        error_item = None
        if std is not None and np.any(std):
            brush = pg.mkBrush(color=color.red(), green=color.green(), blue=color.blue(), alpha=70)
            error_item = pg.FillBetweenItem(
                pg.PlotDataItem(x, y - std),
                pg.PlotDataItem(x, y + std),
                brush=brush
            )
            view_box.addItem(error_item)

        view_box.addItem(plot_data_item)
        
        # Store all items associated with the plot name
        self.plots[name] = {
            'item': plot_data_item, 
            'error_item': error_item, 
            'view_box': view_box
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
                
                error_data = None
                if p['error_item']:
                    curve1_data = p['error_item'].curves[0].getData()
                    curve2_data = p['error_item'].curves[1].getData()
                    error_data = (curve1_data[0], curve1_data[1], curve2_data[1])

                ax.plot(data[0], data[1], color=color, label=name)
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
