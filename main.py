# lmp_visualizer/main.py

import sys
from PyQt6.QtWidgets import QApplication
from main_window import MainWindow

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    window.load_state_on_startup()
    sys.exit(app.exec())