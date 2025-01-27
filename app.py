import threading

import cv2
from PyQt6.QtWidgets import QMainWindow, QLabel, QGridLayout, QPushButton, QApplication, QWidget
from gaze_tracking import GazeTracking

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.gaze = GazeTracking()
        self.webcam = None
        self.started = False  # indicates if recording is in progress

        layout1 = QGridLayout()

        introLabel = QLabel("Press the Start button whenever you're ready")
        layout1.addWidget(introLabel, 0, 0)

        self.dataLabel = QLabel()
        layout1.addWidget(self.dataLabel, 0, 1)

        self.button = QPushButton("Start")
        self.button.clicked.connect(self.press)
        layout1.addWidget(self.button, 1, 1)

        widget = QWidget()
        widget.setLayout(layout1)
        self.setCentralWidget(widget)

    def press(self):
        print("press")
        if not self.started:
            print("start")
            self.webcam = cv2.VideoCapture(0)
            thread = threading.Thread(target=self.thread, args = ())
            thread.start()
            self.button.setText("Stop")

        else:
            print("stop")
            self.webcam.release()
            self.started = False
            self.button.setText("Start")

    def thread(self):
        self.started = True
        while self.started:
            _, frame = self.webcam.read()
            self.gaze.refresh(frame)

            new_frame = self.gaze.annotated_frame()
            text = ""

            if self.gaze.is_right():
                text = "Looking right"
            elif self.gaze.is_left():
                text = "Looking left"
            elif self.gaze.is_center():
                text = "Looking center"

            self.dataLabel.setText(text)



app = QApplication([])
window = MainWindow()
window.show()
app.exec()
