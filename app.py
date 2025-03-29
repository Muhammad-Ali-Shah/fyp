import threading
import cv2
from time import time
from PyQt6.QtCore import pyqtSignal, QTime, Qt
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import (
    QMainWindow, QLabel, QGridLayout, QPushButton, QApplication, QWidget,
    QDialog, QVBoxLayout, QDialogButtonBox, QTimeEdit
)
from gaze_tracking import GazeTracking


class EyeBoundary:
    def __init__(self):
        self.min_x = 1_000_000
        self.max_x = 0
        self.min_y = 1_000_000
        self.max_y = 0

    def adjust_coords(self, coords):
        x, y = coords
        self.min_x = min(self.min_x, x)
        self.max_x = max(self.max_x, x)
        self.min_y = min(self.min_y, y)
        self.max_y = max(self.max_y, y)

    def check_coords(self, coords):
        x, y = coords
        return x < self.min_x or x > self.max_x or y < self.min_y or y > self.max_y


class FocusBar(QWidget):
    """
    Custom widget that draws a horizontal time bar. Each segment is either:
         - Green if focusing, or
         - Red if not focusing
    """
    def __init__(self, timeline=None, parent=None):
        super().__init__(parent)
        self.timeline = timeline if timeline is not None else []

    def setTimeline(self, timeline):
        self.timeline = timeline
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        rect = self.rect()
        n = len(self.timeline)
        if n == 0:
            # If no samples, fill with a neutral color (light grey).
            painter.fillRect(rect, Qt.GlobalColor.lightGray)
            return

        sample_width = rect.width() / n
        for i, sample in enumerate(self.timeline):
            x = int(i * sample_width)
            color = Qt.GlobalColor.green if sample else Qt.GlobalColor.red
            painter.fillRect(x, 0, int(sample_width), rect.height(), color)


class MainWindow(QMainWindow):
    # This signal carries the stats text and the timeline list
    show_stats_signal = pyqtSignal(str, list)

    def __init__(self):
        super().__init__()

        self.show_stats_signal.connect(self.callStatsDialog)

        self.gaze = GazeTracking()
        self.webcam = None
        self.state = 0  # 0: idle, 1: calibrating, 2: session in progress

        self.study_time = QTime()
        layout1 = QGridLayout()

        self.label1 = QLabel("How long are you studying for?")
        layout1.addWidget(self.label1, 0, 0)

        self.timer_edit = QTimeEdit()
        self.timer_edit.setDisplayFormat("HH:mm")
        layout1.addWidget(self.timer_edit, 1, 0)
        self.timer_edit.timeChanged.connect(self.timeChanged)

        self.label2 = QLabel()
        layout1.addWidget(self.label2, 4, 0)

        self.button = QPushButton("Start")
        self.button.clicked.connect(self.press)
        layout1.addWidget(self.button, 5, 1)

        widget = QWidget()
        widget.setLayout(layout1)
        self.setCentralWidget(widget)

    def timeChanged(self, time: QTime):
        self.study_time = time

    def callStatsDialog(self, stats: str, timeline: list):
        dlg = QDialog(self)
        dlg.setWindowTitle("Session Statistics")
        dlg.setWindowFlags(dlg.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        dlgLayout = QVBoxLayout()

        # Display statistics in label
        dlgLabel = QLabel(stats)
        dlgLayout.addWidget(dlgLabel)

        # Create and add the custom timeline focus bar
        focus_bar = FocusBar(timeline)
        focus_bar.setMinimumHeight(30)
        dlgLayout.addWidget(focus_bar)

        dlgButtonBox = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        dlgButtonBox.accepted.connect(dlg.accept)
        dlgLayout.addWidget(dlgButtonBox)

        dlg.setLayout(dlgLayout)
        dlg.exec()

    def press(self):
        match self.state:
            case 0:
                # Start calibration phase
                self.state = 1
                self.timer_edit.hide()  # Hide time selection when starting
                self.webcam = cv2.VideoCapture(0)
                thread = threading.Thread(target=self.thread, daemon=True)
                thread.start()
                self.label1.setText("Look at the top, bottom, left and right of the screen, then press Continue")
                self.button.setText("Continue")
            case 1:
                # Calibration complete, start study session
                self.state = 2
                self.label1.setText("Session in progress...")
                self.button.setText("Stop")
            case 2:
                # User pressed Stop manually
                self.state = 0
                self.timer_edit.show()  # Show time selection when ending
                self.button.setText("Start")

    def thread(self):
        eyeLeft_boundary = EyeBoundary()
        eyeRight_boundary = EyeBoundary()
        timerStarted = False
        startTime = None
        sample_interval = 1  # seconds
        last_sample_time = None
        timeline = []  # list of booleans; True = focusing, False = not

        while self.state:
            ret, frame = self.webcam.read()
            if not ret:
                continue

            self.gaze.refresh(frame)

            if self.state == 1:
                # Calibration phase
                if self.gaze.pupils_located:
                    self.label2.clear()
                    left_x, left_y = self.gaze.pupil_left_coords()
                    right_x, right_y = self.gaze.pupil_right_coords()
                    eyeLeft_boundary.adjust_coords((int(left_x), int(left_y)))
                    eyeRight_boundary.adjust_coords((int(right_x), int(right_y)))
                else:
                    self.label2.setText("Warning: Pupil not detected")
            else:
                # Session phase (state = 2)
                if not timerStarted:
                    startTime = time()
                    last_sample_time = startTime
                    timerStarted = True
                elapsed = time() - startTime
                total_secs = QTime(0, 0).secsTo(self.study_time)

                # Sample the current focus state every sample_interval seconds
                current_time = time()
                if current_time - last_sample_time >= sample_interval:
                    # Determine focus state based on current pupil coordinates
                    if self.gaze.pupils_located:
                        # If both pupils are outside the calibrated boundaries, assume not focusing
                        if (eyeLeft_boundary.check_coords((int(self.gaze.pupil_left_coords()[0]), int(self.gaze.pupil_left_coords()[1]))) and
                            eyeRight_boundary.check_coords((int(self.gaze.pupil_right_coords()[0]), int(self.gaze.pupil_right_coords()[1])))):
                            sample = False
                        else:
                            sample = True
                    else:
                        sample = False
                    timeline.append(sample)
                    last_sample_time = current_time

                # Update focus label (using the most recent sample)
                if self.gaze.pupils_located:
                    if (eyeLeft_boundary.check_coords((int(self.gaze.pupil_left_coords()[0]), int(self.gaze.pupil_left_coords()[1]))) and
                        eyeRight_boundary.check_coords((int(self.gaze.pupil_right_coords()[0]), int(self.gaze.pupil_right_coords()[1])))):
                        self.label1.setText("Not focusing")
                    else:
                        self.label1.setText("Focusing")

                if elapsed > total_secs:
                    self.show_statistics(timeline, total_secs)
                    self.state = 0
                    break

        self.webcam.release()
        self.label1.setText("Press the Start button whenever you're ready")

        # If the session was stopped manually, show statistics
        if self.state == 0 and timerStarted:
            elapsed_secs = int(time() - startTime)
            self.show_statistics(timeline, elapsed_secs)

    def show_statistics(self, timeline, elapsed_secs):
        total_samples = len(timeline)
        if total_samples > 0:
            focus_count = sum(1 for sample in timeline if sample)
            focus_percentage = (focus_count / total_samples) * 100
        else:
            focus_percentage = 0
        minutes_elapsed = elapsed_secs // 60

        stats = (
            f"Session Stopped\n\n"
            f"Elapsed Time: {minutes_elapsed} minutes\n"
            f"Focus Percentage: {focus_percentage:.2f}%"
        )

        self.show_stats_signal.emit(stats, timeline)
        self.state = 0  # Reset session state
        self.timer_edit.show()


if __name__ == '__main__':
    app = QApplication([])
    window = MainWindow()
    window.show()
    app.exec()
