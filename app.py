import sys

from PyQt6.QtWidgets import QMainWindow, QVBoxLayout, QLabel, QGridLayout, QPushButton, QApplication, QWidget


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        layout1 = QGridLayout()

        introLabel = QLabel("Press the Start button whenever you're ready")
        layout1.addWidget(introLabel, 0, 0)

        button = QPushButton("Start")
        layout1.addWidget(button, 1, 1)

        widget = QWidget()
        widget.setLayout(layout1)
        self.setCentralWidget(widget)

app = QApplication([])
window = MainWindow()
window.show()
app.exec()




