import threading
import cv2
import sqlite3
import json
from time import time, strftime, localtime
from PyQt6.QtCore import pyqtSignal, QTime, Qt, pyqtSlot, QSize
from PyQt6.QtGui import QPainter, QColor, QFontMetrics
from PyQt6.QtWidgets import (
    QMainWindow, QLabel, QPushButton, QApplication, QWidget,
    QDialog, QVBoxLayout, QHBoxLayout, QDialogButtonBox, QTimeEdit, QListWidget,
    QListWidgetItem, QSizePolicy, QMessageBox
)
# Required libraries: PyQt6, opencv-python, gaze-tracking, numpy
# pip install PyQt6 opencv-python gaze-tracking numpy
# (numpy might be installed as a dependency)
from gaze_tracking import GazeTracking

# --- Configuration ---
DB_NAME = 'focus_tracker.db'
SAMPLE_INTERVAL_SECONDS = 1
ALERT_THRESHOLD_SECONDS = 5

# --- Database Functions ---
def init_db():
    """Initializes database"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sessions (
            session_id INTEGER PRIMARY KEY AUTOINCREMENT,
            start_time INTEGER NOT NULL,
            end_time INTEGER NOT NULL,
            focus_percentage REAL NOT NULL,
            focus_data TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

def save_session(start_time, end_time, focus_percentage, timeline):
    """Saves session to db with error handling"""
    conn = None
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        focus_data_json = json.dumps(timeline)
        cursor.execute('''
            INSERT INTO sessions (start_time, end_time, focus_percentage, focus_data)
            VALUES (?, ?, ?, ?)
        ''', (int(start_time), int(end_time), focus_percentage, focus_data_json))
        conn.commit()
        print(f"Successfully saved session starting at {start_time}")
    except sqlite3.Error as e:
        print(f"Database Error in save_session: {e}")
    except Exception as e:
        print(f"Unexpected Error in save_session: {e}")
    finally:
        if conn:
            conn.close()


def load_sessions():
    """Loads sessions from db"""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT session_id, start_time, end_time, focus_percentage, focus_data
        FROM sessions
        ORDER BY start_time DESC
    ''')
    sessions = []
    for row in cursor.fetchall():
        timeline = json.loads(row['focus_data'])
        session_data = dict(row)
        session_data['timeline'] = timeline
        session_data['duration_secs'] = row['end_time'] - row['start_time']
        sessions.append(session_data)
    conn.close()
    return sessions

def delete_session(session_id):
    """Deletes specific session"""
    conn = None
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            DELETE FROM sessions WHERE session_id = ?
        ''', (session_id,))
        conn.commit()
        print(f"Deleted session with ID: {session_id}")
    except sqlite3.Error as e:
        print(f"Database Error in delete_session: {e}")
    except Exception as e:
        print(f"Unexpected Error in delete_session: {e}")
    finally:
        if conn:
            conn.close()


# --- Helper Classes ---
class EyeBoundary:
    """Stores and checks eye coordinate boundaries"""
    def __init__(self):
        self.min_x = 1_000_000
        self.max_x = 0
        self.min_y = 1_000_000
        self.max_y = 0
        self.calibrated = False

    def adjust_coords(self, coords):
        """Updates boundaries with new pupil coordinates"""
        if coords is None: return
        x, y = coords
        if x is None or y is None or (x == 0 and y == 0): return # Ignore invalid coords
        self.min_x = min(self.min_x, x)
        self.max_x = max(self.max_x, x)
        self.min_y = min(self.min_y, y)
        self.max_y = max(self.max_y, y)
        if self.max_x > self.min_x and self.max_y > self.min_y:
            self.calibrated = True

    def check_coords(self, coords):
        """Checks if coordinates are outside the calibrated boundaries"""
        if not self.calibrated or coords is None: return True # Assume not focused if not calibrated or no coords
        x, y = coords
        if x is None or y is None: return True # Assume not focused if coords are invalid
        tolerance = 5
        return (x < self.min_x - tolerance or x > self.max_x + tolerance or
                y < self.min_y - tolerance or y > self.max_y + tolerance)

    def reset(self):
        """Resets boundaries for recalibration"""
        self.min_x = 1_000_000
        self.max_x = 0
        self.min_y = 1_000_000
        self.max_y = 0
        self.calibrated = False

class FocusBar(QWidget):
    """Custom widget to draw a focus timeline bar"""
    def __init__(self, timeline=None, parent=None):
        super().__init__(parent)
        self.timeline = timeline if timeline is not None else []
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumHeight(20)

    def setTimeline(self, timeline):
        self.timeline = timeline
        self.update() # Request repaint

    def paintEvent(self, event):
        """Draws focus bar"""
        painter = QPainter(self)
        rect = self.rect()
        n = len(self.timeline)

        painter.setPen(Qt.GlobalColor.black)
        painter.drawRect(rect.adjusted(0, 0, -1, -1)) # Border

        if n == 0:
            painter.fillRect(rect.adjusted(1, 1, -1, -1), QColor(Qt.GlobalColor.lightGray))
            return

        total_width = rect.width() - 2
        sample_width = max(1.0, total_width / n)
        current_x = 1

        for i, sample in enumerate(self.timeline):
            segment_width = round((i + 1) * sample_width) - round(i * sample_width)
            if current_x + segment_width < total_width + 1: segment_width = max(1, segment_width)
            if current_x + segment_width > total_width + 1: segment_width = total_width + 1 - current_x
            if segment_width <= 0: continue

            color = Qt.GlobalColor.green if sample else Qt.GlobalColor.red
            painter.fillRect(int(current_x), 1, int(segment_width), rect.height() - 2, QColor(color))
            current_x += segment_width

    def sizeHint(self):
        """Provides recommended size for widget"""
        return QSize(200, self.minimumHeight())

# --- Main Application Window ---
class MainWindow(QMainWindow):
    """Main application window"""
    # Signals for thread-safe UI updates
    update_status_signal = pyqtSignal(str, bool, bool) # (status_text, is_focused, is_alerting)
    show_stats_signal = pyqtSignal(str, list)          # (stats_text, timeline_list)
    update_history_signal = pyqtSignal(list)           # (session_list)
    update_state_signal = pyqtSignal(int)              # (new_state)
    trigger_alert_signal = pyqtSignal()                # Signal for window alert

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Focus Tracker")
        self.setMinimumSize(600, 400)

        # --- Internal State ---
        self.gaze = GazeTracking()
        self.webcam = None
        self.tracking_thread = None
        self.state = 0  # 0: idle, 1: calibrating, 2: session in progress
        self.study_time = QTime(0, 1, 0) # Default to 1 minute
        self.eyeLeft_boundary = EyeBoundary()
        self.eyeRight_boundary = EyeBoundary()
        self.consecutive_unfocused_start = None
        self.alert_sounded = False # Flag for audible/window alert

        init_db()
        self._setup_ui()
        self._connect_signals()
        self.update_ui_for_state()
        self.load_and_display_history()

    def _setup_ui(self):
        """Creates and arranges UI elements"""
        self.central_widget = QWidget()
        self.main_layout = QHBoxLayout(self.central_widget)

        # Left Panel (Controls)
        self.controls_widget = QWidget()
        self.controls_layout = QVBoxLayout(self.controls_widget)
        self.controls_layout.setContentsMargins(10, 10, 10, 10)
        self.controls_layout.setSpacing(10)

        self.label1 = QLabel()
        self.label1.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.controls_layout.addWidget(self.label1)

        self.timer_edit = QTimeEdit(self.study_time)
        self.timer_edit.setDisplayFormat("HH:mm:ss")
        self.controls_layout.addWidget(self.timer_edit)

        self.focus_status_label = QLabel()
        self.focus_status_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.focus_status_label.setStyleSheet("padding: 5px; border-radius: 3px;")
        self.controls_layout.addWidget(self.focus_status_label)

        self.label2 = QLabel() # Secondary status/warning
        self.label2.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.controls_layout.addWidget(self.label2)

        self.button = QPushButton()
        self.controls_layout.addWidget(self.button)
        self.controls_layout.addStretch(1)
        self.controls_widget.setLayout(self.controls_layout)


        # Right Panel (History)
        self.history_widget = QWidget()
        self.history_layout = QVBoxLayout(self.history_widget)
        self.history_layout.setContentsMargins(10, 10, 10, 10)

        self.history_label = QLabel("Previous Sessions:")
        self.history_layout.addWidget(self.history_label)

        self.history_list = QListWidget()
        self.history_list.setStyleSheet("QListWidget::item { border-bottom: 1px solid lightgrey; padding: 2px; }")
        self.history_layout.addWidget(self.history_list)
        self.history_widget.setLayout(self.history_layout)

        # Add panels to main layout
        self.main_layout.addWidget(self.controls_widget, 1)
        self.main_layout.addWidget(self.history_widget, 2)
        self.setCentralWidget(self.central_widget)

    def _connect_signals(self):
        """Connects UI element signals to slots"""
        self.timer_edit.timeChanged.connect(self.timeChanged)
        self.button.clicked.connect(self.press_button)
        # Connect signals from tracking thread to main thread slots
        self.update_status_signal.connect(self.update_status_labels)
        self.show_stats_signal.connect(self.callStatsDialog)
        self.update_history_signal.connect(self.populate_history_list)
        self.update_state_signal.connect(self.set_state)
        self.trigger_alert_signal.connect(self.handle_window_alert) # Connect new signal

    @pyqtSlot()
    def handle_window_alert(self):
        """Triggers the OS-specific window alert (e.g., taskbar flash)"""
        QApplication.alert(self)

    def timeChanged(self, qtime: QTime):
        if self.state == 0: self.study_time = qtime

    def load_and_display_history(self):
        sessions = load_sessions()
        self.update_history_signal.emit(sessions)

    @pyqtSlot(list)
    def populate_history_list(self, sessions):
        """Fills the history list widget with session data"""
        self.history_list.clear()
        if not sessions:
            self.history_list.addItem("No previous sessions found.")
            return

        for session in sessions:
            session_id = session['session_id']
            start_dt = strftime('%Y-%m-%d %H:%M', localtime(session['start_time']))
            duration_mins = session['duration_secs'] // 60
            duration_secs = session['duration_secs'] % 60
            focus_perc = session['focus_percentage']

            item_widget = QWidget()
            item_main_layout = QHBoxLayout(item_widget)
            item_main_layout.setContentsMargins(5, 5, 5, 5)
            item_main_layout.setSpacing(10)

            info_widget = QWidget()
            info_layout = QVBoxLayout(info_widget)
            info_layout.setContentsMargins(0,0,0,0)
            info_layout.setSpacing(2)

            info_text = f"{start_dt} ({duration_mins}m {duration_secs}s) - {focus_perc:.1f}%"
            info_label = QLabel(info_text)
            info_label.setToolTip(f"Session ID: {session_id}")
            info_layout.addWidget(info_label)

            focus_bar = FocusBar(session['timeline'])
            info_layout.addWidget(focus_bar)
            info_widget.setLayout(info_layout)

            delete_button = QPushButton("-")
            delete_button.setFixedSize(25, 25)
            delete_button.setToolTip(f"Delete session {session_id}")
            delete_button.clicked.connect(lambda checked=False, sid=session_id: self.handle_delete_session(sid))

            item_main_layout.addWidget(info_widget, 1)
            item_main_layout.addWidget(delete_button)
            item_widget.setLayout(item_main_layout)

            list_item = QListWidgetItem(self.history_list)
            fm = QFontMetrics(info_label.font())
            label_height = fm.height()
            bar_height = focus_bar.sizeHint().height()
            button_height = delete_button.sizeHint().height()
            content_height = max(label_height + bar_height + info_layout.spacing(), button_height)
            total_height = content_height + item_main_layout.contentsMargins().top() + item_main_layout.contentsMargins().bottom()
            list_item.setSizeHint(QSize(int(item_widget.sizeHint().width()), int(total_height + 5)))

            self.history_list.addItem(list_item)
            self.history_list.setItemWidget(list_item, item_widget)

    def handle_delete_session(self, session_id):
        """Confirms and deletes a session"""
        reply = QMessageBox.question(self, 'Confirm Deletion',
                                     f"Delete session {session_id}?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            try:
                delete_session(session_id)
                self.load_and_display_history() # Refresh list
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Could not delete session: {e}")

    @pyqtSlot(str, list)
    def callStatsDialog(self, stats: str, timeline: list):
        """Shows the post-session stats dialog"""
        dlg = QDialog(self)
        dlg.setWindowTitle("Session Statistics")
        dlgLayout = QVBoxLayout()
        dlgLabel = QLabel(stats)
        dlgLayout.addWidget(dlgLabel)
        focus_bar = FocusBar(timeline)
        dlgLayout.addWidget(focus_bar)
        dlgButtonBox = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        dlgButtonBox.accepted.connect(dlg.accept)
        dlgLayout.addWidget(dlgButtonBox)
        dlg.setLayout(dlgLayout)
        dlg.exec()
        self.load_and_display_history() # Refresh history after dialog

    def press_button(self):
        """Handles main button clicks based on state"""
        if self.state == 0: self.set_state(1) # Idle -> Calibrating
        elif self.state == 1: self.set_state(2) # Calibrating -> Session
        elif self.state == 2: self.set_state(0) # Session -> Idle (Stop)

    @pyqtSlot(int)
    def set_state(self, new_state):
        """Updates the application state and triggers UI/thread changes"""
        if self.state == new_state: return # Avoid redundant state changes
        previous_state = self.state
        self.state = new_state

        if new_state == 1: # Starting Calibration
            self.eyeLeft_boundary.reset()
            self.eyeRight_boundary.reset()
            self.webcam = cv2.VideoCapture(0)
            if not self.webcam.isOpened():
                self.update_status_signal.emit("Error: Cannot open webcam.", False, False)
                self.set_state(0)
                return
            self.tracking_thread = threading.Thread(target=self.tracking_loop, daemon=True)
            self.tracking_thread.start()

        elif new_state == 2: # Starting Session
            if not self.eyeLeft_boundary.calibrated or not self.eyeRight_boundary.calibrated:
                 self.update_status_signal.emit("Calibration incomplete. Look around more.", False, False)
                 self.set_state(1)
                 return
            # Session continues in the existing thread

        elif new_state == 0: # Stopping Session/Calibration
             # Thread exits based on self.state check
             if self.webcam:
                self.webcam.release()
                self.webcam = None
             self.tracking_thread = None

        self.update_ui_for_state()

    def update_ui_for_state(self):
        """Updates UI elements based on the current state."""
        if self.state == 0: # Idle
            self.label1.setText("Select duration and press Start")
            self.button.setText("Start")
            self.timer_edit.show()
            self.timer_edit.setEnabled(True)
            self.focus_status_label.setText("Status: Idle")
            self.focus_status_label.setStyleSheet("background-color: lightgrey; padding: 5px; border-radius: 3px;")
            self.label2.clear()
            self.history_widget.show()
        elif self.state == 1: # Calibrating
            self.label1.setText("Calibrating: Look at screen edges, then press Calibrate")
            self.button.setText("Calibrate")
            self.timer_edit.hide()
            self.timer_edit.setEnabled(False)
            self.focus_status_label.setText("Status: Calibrating")
            self.focus_status_label.setStyleSheet("background-color: lightblue; padding: 5px; border-radius: 3px;")
            self.history_widget.hide()
        elif self.state == 2: # Session in progress
            self.label1.setText("Session in progress...")
            self.button.setText("End Session")
            self.timer_edit.hide()
            self.timer_edit.setEnabled(False)
            # focus_status_label is updated by signal
            self.history_widget.hide()

    @pyqtSlot(str, bool, bool)
    def update_status_labels(self, status_text, is_focused, is_alerting):
        """Updates status labels via signals from the tracking thread"""
        self.label2.setText(status_text) # Always update label2

        if self.state == 2:  # Only update focus status label during session
            if is_alerting:
                self.focus_status_label.setText("Status: Not Focusing!")
                self.focus_status_label.setStyleSheet(
                    "background-color: #FFC0CB; padding: 5px; border-radius: 3px; color: black;")  # Light red
            elif is_focused:
                self.focus_status_label.setText("Status: Focusing")
                self.focus_status_label.setStyleSheet(
                    "background-color: #90EE90; padding: 5px; border-radius: 3px; color: black;")  # Light green
            else:
                self.focus_status_label.setText("Status: Not Focusing")
                self.focus_status_label.setStyleSheet(
                    "background-color: lightgrey; padding: 5px; border-radius: 3px; color: black;")  # Grey

    def tracking_loop(self):
        """Background thread loop for camera feed processing and tracking"""
        timeline = []
        session_startTime = None
        last_sample_time = None
        session_running = False

        while self.state != 0: # Loop while not idle
            try: # <<< Main try block for the loop iteration >>>
                status_msg = "" # Reset status message at the start of each iteration

                if not self.webcam or not self.webcam.isOpened():
                    status_msg = "Webcam error."
                    self.update_status_signal.emit(status_msg, False, False)
                    break # Exit loop on webcam error

                ret, frame = self.webcam.read()
                if not ret:
                    status_msg = "Frame read error."
                    self.update_status_signal.emit(status_msg, False, False)
                    continue # Skip iteration on frame read error

                # --- Inner try for gaze tracking specifically ---
                try:
                    self.gaze.refresh(frame)
                    pupils_located = self.gaze.pupils_located
                    left_coords = self.gaze.pupil_left_coords()
                    right_coords = self.gaze.pupil_right_coords()
                except Exception as e_gaze:
                    print(f"Gaze tracking error: {e_gaze}")
                    pupils_located = False
                    left_coords = None
                    right_coords = None
                    status_msg = "Tracking Error" # Set status on gaze error
                # --- End inner try ---

                current_time = time()
                is_focused = False
                is_alerting = False
                # status_msg is already reset, only set it if needed now

                if self.state == 1: # Calibration phase
                    if not pupils_located:
                        status_msg = "Warning: Pupil not detected"
                    if pupils_located:
                        self.eyeLeft_boundary.adjust_coords(left_coords)
                        self.eyeRight_boundary.adjust_coords(right_coords)

                elif self.state == 2: # Session phase
                    if not session_running: # First loop iteration for the session
                        session_startTime = current_time
                        last_sample_time = session_startTime
                        session_running = True
                        timeline = []
                        self.consecutive_unfocused_start = None
                        self.alert_sounded = False # Reset alert sound flag at session start

                    total_study_secs = QTime(0, 0, 0).secsTo(self.study_time)
                    elapsed_session_secs = current_time - session_startTime

                    if pupils_located:
                         left_outside = self.eyeLeft_boundary.check_coords(left_coords)
                         right_outside = self.eyeRight_boundary.check_coords(right_coords)
                         is_focused = not (left_outside and right_outside)
                    else:
                        is_focused = False
                        status_msg = "Warning: Pupil not detected"

                    # Alert Logic
                    if not is_focused:
                        if self.consecutive_unfocused_start is None:
                            self.consecutive_unfocused_start = current_time
                            # Reset sound flag only when unfocused streak *starts*
                            self.alert_sounded = False
                        elif current_time - self.consecutive_unfocused_start >= ALERT_THRESHOLD_SECONDS:
                            is_alerting = True
                            if not self.alert_sounded:
                                QApplication.beep() # Play system beep sound
                                self.trigger_alert_signal.emit() # Emit signal for window alert
                                self.alert_sounded = True # Mark alert as triggered for this streak
                    else:
                         self.consecutive_unfocused_start = None
                         self.alert_sounded = False # Reset when focus is regained

                    # Sampling Logic
                    if current_time - last_sample_time >= SAMPLE_INTERVAL_SECONDS:
                        timeline.append(is_focused)
                        last_sample_time = current_time

                    # Session End Logic
                    if elapsed_session_secs >= total_study_secs:
                        self.finish_session(session_startTime, current_time, timeline)
                        break # Exit loop -> thread finishes

                # Emit status updates for UI
                self.update_status_signal.emit(status_msg, is_focused, is_alerting)

                # Allow OpenCV processing time
                if cv2.waitKey(1) & 0xFF == ord('q'):
                     break

            except Exception as e_loop: # <<< Catch unexpected errors in the loop >>>
                print(f"Unexpected error in tracking loop iteration: {e_loop}")
                # Optionally add more handling, e.g., emit an error signal to UI
                # break # Uncomment to stop tracking on unexpected errors

        # --- Cleanup after loop exit ---
        # This part runs *after* the loop (or break)
        if session_running and self.state == 0: # Manual stop
            print("Loop finished, calling finish_session for manual stop.")
            self.finish_session(session_startTime, time(), timeline) # Call cleanup

        # Signal state change back to idle just in case
        self.update_state_signal.emit(0)
        print("Tracking thread finished.")


    def finish_session(self, start_time, end_time, timeline):
        """Finalizes a session: calculates stats, saves, shows dialog with error handling"""
        try:
            elapsed_secs = int(end_time - start_time)
            total_samples = len(timeline)
            focus_percentage = 0.0
            if total_samples > 0:
                focus_count = sum(1 for sample in timeline if sample)
                focus_percentage = (focus_count / total_samples) * 100

            # Avoid saving zero-duration sessions if stopped immediately
            if elapsed_secs > 0:
                save_session(start_time, end_time, focus_percentage, timeline)
            else:
                print("Session too short, not saving.")


            minutes_elapsed = elapsed_secs // 60
            seconds_rem = elapsed_secs % 60
            stats = (
                f"Session Finished\n\n"
                f"Duration: {minutes_elapsed}m {seconds_rem}s\n"
                f"Focus: {focus_percentage:.1f}%"
            )
            # Ensure signal emission happens even for short sessions to close dialog if open
            self.show_stats_signal.emit(stats, timeline) # Trigger dialog in main thread
        except Exception as e:
            print(f"Error during finish_session: {e}")
            # Optionally emit a signal here to inform the user via the UI

    def closeEvent(self, event):
        """Handles window close event for proper cleanup"""
        print("Closing application...")
        self.set_state(0) # Signal thread to stop
        # No forceful join to avoid freezing UI if thread hangs
        if self.webcam: self.webcam.release()
        cv2.destroyAllWindows()
        event.accept()

# --- Main Execution ---
if __name__ == '__main__':
    app = QApplication([])
    window = MainWindow()
    window.show()
    app.exec()
