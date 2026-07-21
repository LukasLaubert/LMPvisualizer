# lmp_visualizer/main.py

import sys
import argparse
from PyQt6.QtWidgets import QApplication
from main_window import MainWindow

def parse_arguments():
    parser = argparse.ArgumentParser(description="LMPvisualizer")
    parser.add_argument("-mode", type=str, choices=['log', 'trj', 'dsd'], help="Start in 'log', 'trj' or 'dsd' mode")
    parser.add_argument("-autoload", type=str, choices=['on', 'off'], default='on', help="Enable or disable autosave loading (default: on)")
    return parser.parse_args()

if __name__ == '__main__':
    args = parse_arguments()
    app = QApplication(sys.argv)
    
    # Convert string arg to boolean
    autoload = True
    if args.autoload == 'off':
        autoload = False
        
    window = MainWindow(startup_mode=args.mode, startup_autoload=autoload)
    window.show()
    
    sys.exit(app.exec())