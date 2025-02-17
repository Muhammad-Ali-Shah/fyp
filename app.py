import threading
import cv2
from time import time
from PyQt6.QtCore import pyqtSignal, QTime, Qt
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import (
    QMainWindow, QLabel, QGridLayout, QPushButton, QApplication, QWidget,
    QDialog, QVBoxLayout, QDialogButtonBox, QTimeEdit, QWidget
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
    A custom widget that draws a horizontal bar with two colors.
    Green represents the portion of time the user was focusing,
    and red represents the portion of time not focusing.
    """
    def __init__(self, focus_percentage=0, parent=None):
        super().__init__(parent)
        self.focus_percentage = focus_percentage

    def setFocusPercentage(self, focus_percentage):
        self.focus_percentage = focus_percentage
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        rect = self.rect()
        # Calculate width for the focusing portion.
        focus_width = int(rect.width() * self.focus_percentage / 100)
        # Draw the focusing (green) and not-focusing (red) portions.
        painter.fillRect(0, 0, focus_width, rect.height(), Qt.GlobalColor.green)
        painter.fillRect(focus_width, 0, rect.width() - focus_width, rect.height(), Qt.GlobalColor.red)


class MainWindow(QMainWindow):
    # Signal now carries both the stats text and the focus percentage (float)
    show_stats_signal = pyqtSignal(str, float)

    def __init__(self):
        super().__init__()

        self.show_stats_signal.connect(self.callStatsDialog)

        self.gaze = GazeTracking()
        self.webcam = None
        self.state = 0  # 0: idle, 1: calibrating, 2: session in progress

        self.study_time = QTime()
        layout1 = QGridLayout()

        self.label1 = QLabel("Select a study mode")
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

    def callStatsDialog(self, stats: str, focus_percentage: float):
        dlg = QDialog(self)
        dlg.setWindowTitle("Session Statistics")
        dlgLayout = QVBoxLayout()

        # Display textual statistics.
        dlgLabel = QLabel(stats)
        dlgLayout.addWidget(dlgLabel)

        # Create and add the custom focus bar.
        focus_bar = FocusBar(focus_percentage)
        focus_bar.setMinimumHeight(30)
        dlgLayout.addWidget(focus_bar)

        dlgButtonBox = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        dlgButtonBox.accepted.connect(dlg.accept)
        dlgLayout.addWidget(dlgButtonBox)

        dlg.setWindowFlags(dlg.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)


        dlg.setLayout(dlgLayout)
        dlg.exec()

    def press(self):
        match self.state:
            case 0:
                self.state = 1
                self.timer_edit.hide()  # Hide time selection when starting
                self.webcam = cv2.VideoCapture(0)
                thread = threading.Thread(target=self.thread, daemon=True)
                thread.start()
                self.label1.setText("Look at the top, bottom, left and right of the screen, then press Continue")
                self.button.setText("Continue")
            case 1:
                self.state = 2
                self.label1.setText("Session in progress...")
                self.button.setText("Stop")
            case 2:
                self.state = 0
                self.timer_edit.show()  # Show time selection when ending
                self.button.setText("Start")

    def thread(self):
        eyeLeft_boundary = EyeBoundary()
        eyeRight_boundary = EyeBoundary()
        timerStarted = False
        startTime = None
        focusedCount = 0
        notFocusedCount = 0

        while self.state:
            ret, frame = self.webcam.read()
            if not ret:
                continue

            self.gaze.refresh(frame)

            if self.state == 1:
                # Calibration phase.
                if self.gaze.pupils_located:
                    self.label2.clear()
                    left_x, left_y = self.gaze.pupil_left_coords()
                    right_x, right_y = self.gaze.pupil_right_coords()
                    eyeLeft_boundary.adjust_coords((int(left_x), int(left_y)))
                    eyeRight_boundary.adjust_coords((int(right_x), int(right_y)))
                else:
                    self.label2.setText("Warning: Pupil not detected")
            else:
                # Session phase.
                if not timerStarted:
                    startTime = time()
                    timerStarted = True
                elapsed = time() - startTime
                secs = QTime(0, 0).secsTo(self.study_time)

                if elapsed > secs:
                    self.show_statistics(focusedCount, notFocusedCount, secs)
                    self.state = 0
                    break

                if self.gaze.pupils_located:
                    left_x, left_y = self.gaze.pupil_left_coords()
                    right_x, right_y = self.gaze.pupil_right_coords()

                    if (eyeLeft_boundary.check_coords((int(left_x), int(left_y))) and
                        eyeRight_boundary.check_coords((int(right_x), int(right_y)))):
                        self.label1.setText("Not focusing")
                        notFocusedCount += 1
                    else:
                        self.label1.setText("Focusing")
                        focusedCount += 1

        self.webcam.release()
        self.label1.setText("Press the Start button whenever you're ready")
        self.timer_edit.show()  # Ensure time selection is visible when session ends

        # Show statistics if the session was stopped manually.
        if self.state == 0 and timerStarted:
            elapsed_secs = int(time() - startTime)
            self.show_statistics(focusedCount, notFocusedCount, elapsed_secs)

    def show_statistics(self, focusedCount, notFocusedCount, elapsed_secs):
        total_frames = focusedCount + notFocusedCount
        focus_percentage = (focusedCount / total_frames * 100) if total_frames > 0 else 0
        minutes_elapsed = elapsed_secs // 60

        stats = (
            f"Session Stopped\n\n"
            f"Elapsed Time: {minutes_elapsed} minutes\n"
            f"Focus Percentage: {focus_percentage:.2f}%"
        )

        self.show_stats_signal.emit(stats, focus_percentage)
        self.state = 0  # Reset session state
        self.timer_edit.show()  # Ensure time selection is visible


if __name__ == '__main__':
    app = QApplication([])
    window = MainWindow()
    window.show()
    app.exec()
