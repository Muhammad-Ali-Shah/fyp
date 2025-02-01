import threading

import cv2
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QMainWindow, QLabel, QGridLayout, QPushButton, QApplication, QWidget, QDialog, QVBoxLayout, \
    QDialogButtonBox
from gaze_tracking import GazeTracking
from time import time

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
    show_dialog_signal = pyqtSignal()

    def __init__(self):
        super().__init__()

        self.show_dialog_signal.connect(self.callDialogBox)

        self.gaze = GazeTracking()
        self.webcam = None
        self.state = 0  # indicates if recording is in progress
        self.calibrated = False

        layout1 = QGridLayout()

        self.label1 = QLabel("Press the Start button whenever you're ready")
        layout1.addWidget(self.label1, 0, 0)

        self.label2 = QLabel()
        layout1.addWidget(self.label2, 1, 0)

        self.button = QPushButton("Start")
        self.button.clicked.connect(self.press)
        layout1.addWidget(self.button, 4, 1)

        widget = QWidget()
        widget.setLayout(layout1)
        self.setCentralWidget(widget)

    def callDialogBox(self):
        dlg = QDialog()
        def pressOkButton():
            self.state = 2
            self.press()
            dlg.accept()

        def pressRecalibrateButton():
            self.press()
            dlg.reject()

        dlgLayout = QVBoxLayout()

        dlgLabel = QLabel("You have not been focusing for a while")
        dlgLayout.addWidget(dlgLabel)

        dlgButtonBox = QDialogButtonBox()
        dlgButtonBox.addButton(QPushButton("Recalibrate"), QDialogButtonBox.ButtonRole.RejectRole)
        dlgButtonBox.addButton(QDialogButtonBox.StandardButton.Ok)
        dlgButtonBox.accepted.connect(pressOkButton)
        dlgButtonBox.rejected.connect(pressRecalibrateButton)
        dlgLayout.addWidget(dlgButtonBox)

        dlg.setLayout(dlgLayout)
        dlg.exec()

    def press(self):
        match self.state:
            case 0:
                self.state = 1
                self.webcam = cv2.VideoCapture(0)
                thread = threading.Thread(target=self.thread, args = ())
                thread.start()
                self.label1.setText("Look at the top, bottom, left and right of the screen, then press Continue")
                self.button.setText("Continue")

            case 1:
                self.state = 2
                self.label1.setText("Detection in progress")
                self.button.setText("Stop")

            case 2:
                self.state = 0
                self.button.setText("Start")

    def thread(self):
        eyeLeft_boundary = EyeBoundary()
        eyeRight_boundary = EyeBoundary()
        timerStarted = False
        startTime = None
        focusedCount = 0
        notFocusedCount = 0
        while self.state:
            _, frame = self.webcam.read()
            self.gaze.refresh(frame)

            new_frame = self.gaze.annotated_frame()

            if self.state == 1:
                if self.gaze.pupils_located:
                    self.label2.clear()
                    left_x, left_y = self.gaze.pupil_left_coords()
                    right_x, right_y = self.gaze.pupil_right_coords()

                    eyeLeft_boundary.adjust_coords((int(left_x), int(left_y)))
                    eyeRight_boundary.adjust_coords((int(right_x), int(right_y)))

                else:
                    self.label2.setText("Warning: Pupil not detected")

            else:
                if not timerStarted:
                    startTime = time()
                    timerStarted = True
                if time() - startTime > 10:
                    timerStarted = False
                    if notFocusedCount > 2 * focusedCount:
                        self.show_dialog_signal.emit()
                        self.state = 0

                        self.label2.setText("Caution: You have not been focusing for a while")
                if self.gaze.pupils_located:
                    left_x, left_y = self.gaze.pupil_left_coords()
                    right_x, right_y = self.gaze.pupil_right_coords()

                    if (eyeLeft_boundary.check_coords((int(left_x), int(left_y)))
                          and eyeRight_boundary.check_coords((int(right_x), int(right_y)))):
                        self.label1.setText("Not focusing")
                        notFocusedCount += 1
                    else:
                        self.label1.setText("Focusing")
                        focusedCount += 1

        self.webcam.release()
        self.label1.setText("Press the Start button whenever you're ready")

app = QApplication([])
window = MainWindow()
window.show()
app.exec()
