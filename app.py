import threading

import cv2
from PyQt6.QtWidgets import QMainWindow, QLabel, QGridLayout, QPushButton, QApplication, QWidget
from gaze_tracking import GazeTracking

class EyeBoundary:
    def __init__(self):
        self.min_x = 1_000_000
        self.max_x = 0
        self.min_y = 1_000_000
        self.max_y = 0

    def adjust_coords(self, coords):
        x = coords[0]
        y = coords[1]

        if x < self.min_x:
            self.min_x = x
        if x > self.max_x:
            self.max_x = x
        if y < self.min_y:
            self.min_y = y
        if y > self.max_y:
            self.max_y = y


    # Uses given coordinates and returns true if they violate the boundary.
    def check_coords(self, coords):
        x = coords[0]
        y = coords[1]

        return x < self.min_x or x > self.max_x or y < self.min_y or y > self.max_y



class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.gaze = GazeTracking()
        self.webcam = None
        self.state = 0  # indicates if recording is in progress
        self.calibrated = False

        layout1 = QGridLayout()

        self.label = QLabel("Press the Start button whenever you're ready")
        layout1.addWidget(self.label, 0, 0)

        self.dataLabel1 = QLabel()
        layout1.addWidget(self.dataLabel1, 1, 0)

        self.dataLabel2 = QLabel()
        layout1.addWidget(self.dataLabel2, 2, 0)

        self.button = QPushButton("Start")
        self.button.clicked.connect(self.press)
        layout1.addWidget(self.button, 2, 1)

        widget = QWidget()
        widget.setLayout(layout1)
        self.setCentralWidget(widget)

    def press(self):
        match self.state:
            case 0:
                self.state = 1
                self.webcam = cv2.VideoCapture(0)
                thread = threading.Thread(target=self.thread, args = ())
                thread.start()
                self.label.setText("Look at the top, bottom, left and right of the screen, then press Continue")
                self.button.setText("Continue")

            case 1:
                self.state = 2
                self.label.setText("Detection in progress")
                self.button.setText("Stop")

            case 2:
                self.state = 0
                self.button.setText("Start")

    def thread(self):
        eyeLeft_boundary = EyeBoundary()
        eyeRight_boundary = EyeBoundary()
        while self.state > 0:
            _, frame = self.webcam.read()
            self.gaze.refresh(frame)

            new_frame = self.gaze.annotated_frame()
            self.dataLabel1.setText(str(self.gaze.pupil_left_coords()))
            self.dataLabel2.setText(str(self.gaze.pupil_right_coords()))

            if self.gaze.pupils_located:
                left_x, left_y = self.gaze.pupil_left_coords()
                right_x, right_y = self.gaze.pupil_right_coords()

                if self.state == 1:
                    eyeLeft_boundary.adjust_coords((int(left_x), int(left_y)))
                    eyeRight_boundary.adjust_coords((int(right_x), int(right_y)))

                else:
                    if (eyeLeft_boundary.check_coords((int(left_x), int(left_y)))
                          and eyeRight_boundary.check_coords((int(right_x), int(right_y)))):
                        self.label.setText("Not focusing")
                    else:
                        self.label.setText("Focusing")

            else:
                if self.state == 2:
                    self.label.setText("Pupils not found")

        self.webcam.release()
        self.label.setText("Press the Start button whenever you're ready")

        print(eyeLeft_boundary.min_x, eyeRight_boundary.max_x, eyeLeft_boundary.min_y, eyeRight_boundary.max_y)
        print(eyeRight_boundary.min_x, eyeRight_boundary.max_x, eyeRight_boundary.min_y, eyeRight_boundary.max_y)



app = QApplication([])
window = MainWindow()
window.show()
app.exec()
