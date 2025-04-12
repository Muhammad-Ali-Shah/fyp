import threading
import cv2
import sqlite3
import json
import datetime
from time import time, strftime, localtime
from PyQt6.QtCore import pyqtSignal, QTime, Qt, pyqtSlot, QSize, QDate, QRectF
from PyQt6.QtGui import QPainter, QColor, QFontMetrics, QFont, QPixmap
from PyQt6.QtWidgets import (
    QMainWindow, QLabel, QPushButton, QApplication, QWidget,
    QDialog, QVBoxLayout, QHBoxLayout, QDialogButtonBox, QTimeEdit, QListWidget,
    QListWidgetItem, QSizePolicy, QMessageBox, QStackedWidget, QSpacerItem
)
from gaze_tracking import GazeTracking

# --- Configuration ---
DB_NAME = 'focus_tracker.db'
SAMPLE_INTERVAL_SECONDS = 1
ALERT_THRESHOLD_SECONDS = 5

# --- Styling Constants ---
COLOR_BACKGROUND = "#2E2E2E" # Off-black
COLOR_TEXT = "#FFFFFF" # White
COLOR_BUTTON_BLUE_BG = "#000080" # Dark Blue
COLOR_BUTTON_GRAY_BG = "#555555" # Gray
COLOR_BUTTON_TEXT = "#FFFFFF" # White
COLOR_FOCUS_GREEN = "#32CD32" # Lime Green
COLOR_FOCUS_RED = "#DC143C" # Crimson
COLOR_BAR_GRAPH = "#A9A9A9" # Dark Gray for bars
BUTTON_RADIUS = 12

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
    """Loads all sessions from db"""
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
        try:
            timeline = json.loads(row['focus_data'])
            if not isinstance(timeline, list):
                 timeline = []
        except json.JSONDecodeError:
            timeline = []
        session_data = dict(row)
        session_data['timeline'] = timeline
        session_data['duration_secs'] = row['end_time'] - row['start_time']
        sessions.append(session_data)
    conn.close()
    return sessions

def get_session_by_id(session_id):
    """Loads a single session by its ID"""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT session_id, start_time, end_time, focus_percentage, focus_data
        FROM sessions
        WHERE session_id = ?
    ''', (session_id,))
    row = cursor.fetchone()
    session_data = None
    if row:
        try:
            timeline = json.loads(row['focus_data'])
            if not isinstance(timeline, list): timeline = []
        except json.JSONDecodeError:
            timeline = []
        session_data = dict(row)
        session_data['timeline'] = timeline
        session_data['duration_secs'] = row['end_time'] - row['start_time']
    conn.close()
    return session_data

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

def get_weekly_stats(start_date_timestamp):
    """
    Calculates total study duration for each day of the week starting from
    the Monday of the week containing start_date_timestamp.
    Returns a list of 7 durations (in seconds), index 0=Monday, ..., 6=Sunday.
    """
    start_dt = datetime.datetime.fromtimestamp(start_date_timestamp)
    start_of_week_dt = start_dt - datetime.timedelta(days=start_dt.weekday())
    start_of_week_ts = int(start_of_week_dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())

    daily_totals = [0] * 7

    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    end_of_week_ts = start_of_week_ts + (7 * 24 * 60 * 60) + (24 * 60 * 60)
    cursor.execute('''
        SELECT start_time, end_time
        FROM sessions
        WHERE start_time >= ? AND start_time < ?
    ''', (start_of_week_ts, end_of_week_ts))

    for row in cursor.fetchall():
        session_start_dt = datetime.datetime.fromtimestamp(row['start_time'])
        if (session_start_dt.timestamp() >= start_of_week_ts
                and session_start_dt.timestamp() < (start_of_week_ts + 7 * 24 * 60 * 60)):
            day_index = session_start_dt.weekday()
            duration = row['end_time'] - row['start_time']
            if duration > 0:
                daily_totals[day_index] += duration

    conn.close()
    return daily_totals

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
        self.setAutoFillBackground(True)

    def setTimeline(self, timeline):
        self.timeline = timeline
        self.update()

    def get_pixmap(self, width=200, height=20):
        """Renders the focus bar to a QPixmap"""
        original_size = self.size()
        self.setFixedSize(width, height)
        pixmap = QPixmap(self.size())
        pixmap.fill(QColor(COLOR_BACKGROUND))
        self.render(pixmap)
        self.setFixedSize(original_size)
        return pixmap

    def paintEvent(self, event):
        """Draws focus bar"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        n = len(self.timeline)

        painter.fillRect(rect, QColor(COLOR_BACKGROUND))
        painter.setPen(QColor(COLOR_TEXT))
        painter.drawRect(rect.adjusted(0, 0, -1, -1))

        if n == 0:
            return

        total_width = rect.width() - 2
        sample_width = max(1.0, total_width / n)
        current_x = 1

        painter.setPen(Qt.PenStyle.NoPen)
        for i, sample in enumerate(self.timeline):
            segment_width = round((i + 1) * sample_width) - round(i * sample_width)
            if current_x + segment_width < total_width + 1: segment_width = max(1, segment_width)
            if current_x + segment_width > total_width + 1: segment_width = total_width + 1 - current_x
            if segment_width <= 0: continue

            color = QColor(COLOR_FOCUS_GREEN) if sample else QColor(COLOR_FOCUS_RED)
            painter.fillRect(int(current_x), 1, int(segment_width), rect.height() - 2, color)
            current_x += segment_width

    def sizeHint(self):
        """Provides recommended size for widget"""
        return QSize(200, self.minimumHeight())

# --- Weekly Stats Widgets ---
class BarGraphWidget(QWidget):
    """Widget to display the weekly study time bar graph"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.daily_data = [0] * 7
        self.max_value = 1
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(150)

    def set_data(self, data):
        """Sets the data (list of 7 durations in seconds)"""
        if len(data) == 7:
            self.daily_data = data
            self.max_value = max(1, max(data))
            self.update()
        else:
            print("Error: Bar graph data must have 7 values.")

    def paintEvent(self, event):
        """Draws the bar graph"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        padding = 20
        graph_rect = rect.adjusted(padding + 20, padding, -padding, -padding - 25)

        if graph_rect.width() <= 0 or graph_rect.height() <= 0: return

        painter.setPen(QColor(COLOR_TEXT))
        painter.setFont(QFont("Arial", 10))

        max_hours = self.max_value / 3600
        painter.drawText(QRectF(0, 0, padding + 15, padding), Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight, f"{max_hours:.1f}h")
        painter.drawLine(graph_rect.bottomLeft(), graph_rect.bottomRight())

        num_bars = 7
        total_bar_width = graph_rect.width()
        bar_spacing_ratio = 0.2
        bar_width = total_bar_width / (num_bars * (1 + bar_spacing_ratio) + bar_spacing_ratio)
        spacing = bar_width * bar_spacing_ratio
        if bar_width < 1: bar_width = 1
        if spacing < 1: spacing = 1

        current_x = graph_rect.left() + spacing
        days = ["M", "T", "W", "T", "F", "S", "S"]

        painter.setBrush(QColor(COLOR_BAR_GRAPH))
        painter.setPen(Qt.PenStyle.NoPen)

        for i, value in enumerate(self.daily_data):
            bar_height = (value / self.max_value) * graph_rect.height() if self.max_value > 0 else 0
            bar_rect = QRectF(current_x, graph_rect.bottom() - bar_height, bar_width, bar_height)
            painter.drawRect(bar_rect)

            label_rect = QRectF(current_x, graph_rect.bottom() + 5, bar_width, 15)
            painter.setPen(QColor(COLOR_TEXT))
            painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, days[i])
            painter.setPen(Qt.PenStyle.NoPen)

            current_x += bar_width + spacing

class WeeklyStatsWidget(QWidget):
    """Screen/Widget to display weekly statistics"""
    def __init__(self, main_window_ref, parent=None):
        super().__init__(parent)
        self.main_window = main_window_ref
        today = QDate.currentDate()
        self.current_week_start_date = today.addDays(-today.dayOfWeek() + 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)
        self.setStyleSheet(f"background-color: {COLOR_BACKGROUND}; color: {COLOR_TEXT};")

        header_layout = QHBoxLayout()
        self.prev_week_button = QPushButton("<")
        self.prev_week_button.setFixedSize(30, 30)
        self.prev_week_button.setStyleSheet(f"""
            QPushButton {{ background-color: {COLOR_BUTTON_BLUE_BG}; color: {COLOR_BUTTON_TEXT}; border-radius: 15px; font-size: 16px; font-weight: bold; }}
            QPushButton:hover {{ background-color: #0000B0; }} QPushButton:pressed {{ background-color: #000050; }}
        """)
        self.week_label = QLabel("Week: ")
        self.week_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.week_label.setFont(QFont("Arial", 14))
        self.next_week_button = QPushButton(">")
        self.next_week_button.setFixedSize(30, 30)
        self.next_week_button.setStyleSheet(self.prev_week_button.styleSheet())

        header_layout.addWidget(self.prev_week_button)
        header_layout.addStretch(1)
        header_layout.addWidget(self.week_label)
        header_layout.addStretch(1)
        header_layout.addWidget(self.next_week_button)
        layout.addLayout(header_layout)

        self.bar_graph = BarGraphWidget()
        layout.addWidget(self.bar_graph, 1)

        footer_layout = QHBoxLayout()
        footer_layout.addStretch(1)
        self.back_button = QPushButton("Main Menu")
        self.back_button.setMinimumHeight(30)
        self.back_button.setStyleSheet(f"""
            QPushButton {{ background-color: {COLOR_BUTTON_BLUE_BG}; color: {COLOR_BUTTON_TEXT}; border-radius: {BUTTON_RADIUS}px; padding: 5px 15px; font-size: 14px; }}
            QPushButton:hover {{ background-color: #0000B0; }} QPushButton:pressed {{ background-color: #000050; }}
        """)
        footer_layout.addWidget(self.back_button)
        footer_layout.addStretch(1)
        layout.addLayout(footer_layout)

        self.prev_week_button.clicked.connect(self.show_prev_week)
        self.next_week_button.clicked.connect(self.show_next_week)
        self.back_button.clicked.connect(self.main_window.show_main_menu)

        self.update_display()

    def update_display(self):
        """Updates the week label and fetches/displays graph data"""
        start_dt = datetime.datetime(
            self.current_week_start_date.year(), self.current_week_start_date.month(), self.current_week_start_date.day(),
            tzinfo=datetime.timezone.utc
        )
        start_ts = start_dt.timestamp()
        try:
            weekly_data = get_weekly_stats(start_ts)
            self.bar_graph.set_data(weekly_data)
        except Exception as e:
            print(f"Error getting weekly stats: {e}")
            self.bar_graph.set_data([0]*7)
        start_str = self.current_week_start_date.toString("MMM d, yyyy")
        end_str = self.current_week_start_date.addDays(6).toString("MMM d, yyyy")
        self.week_label.setText(f"Week: {start_str} - {end_str}")

    def show_prev_week(self):
        self.current_week_start_date = self.current_week_start_date.addDays(-7)
        self.update_display()

    def show_next_week(self):
        self.current_week_start_date = self.current_week_start_date.addDays(7)
        today = QDate.currentDate()
        monday_of_this_week = today.addDays(-today.dayOfWeek() + 1)
        if self.current_week_start_date > monday_of_this_week:
            self.current_week_start_date = monday_of_this_week
        self.update_display()

# --- Main Application Window ---
class MainWindow(QMainWindow):
    """Main application window"""
    update_status_signal = pyqtSignal(str, bool, bool)
    show_stats_signal = pyqtSignal(str, list)
    update_history_signal = pyqtSignal(list)
    update_state_signal = pyqtSignal(int)
    trigger_alert_signal = pyqtSignal()

    PAGE_MAIN_MENU = 0
    PAGE_WEEKLY_STATS = 1

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Focus Tracker")
        self.setMinimumSize(600, 450)
        self.setStyleSheet(f"QMainWindow {{ background-color: {COLOR_BACKGROUND}; }}")

        self.gaze = GazeTracking()
        self.webcam = None
        self.tracking_thread = None
        self.state = 0
        self.study_time = QTime(0, 1, 0)
        self.eyeLeft_boundary = EyeBoundary()
        self.eyeRight_boundary = EyeBoundary()
        self.consecutive_unfocused_start = None
        self.alert_sounded = False

        init_db()
        self._setup_ui()
        self._connect_signals()
        self.update_ui_for_state()
        self.load_and_display_history()

    def _setup_ui(self):
        """Creates and arranges UI elements using QStackedWidget"""
        self.central_widget = QStackedWidget()
        self.setCentralWidget(self.central_widget)
        self.main_menu_widget = QWidget()
        self.main_layout = QHBoxLayout(self.main_menu_widget)
        self.controls_widget = QWidget()
        controls_layout = QVBoxLayout(self.controls_widget)
        controls_layout.setContentsMargins(15, 15, 15, 15)
        controls_layout.setSpacing(12)
        self.label1 = QLabel()
        self.label1.setStyleSheet(f"color: {COLOR_TEXT}; font-size: 14px;")
        self.label1.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.label1.setWordWrap(True)
        controls_layout.addWidget(self.label1)
        self.timer_edit = QTimeEdit(self.study_time)
        self.timer_edit.setDisplayFormat("HH:mm:ss")
        self.timer_edit.setStyleSheet(f"""
            QTimeEdit {{ background-color: #444444; color: {COLOR_TEXT}; border: 1px solid #555555; padding: 5px; font-size: 14px; min-height: 25px; }}
            QTimeEdit::up-button, QTimeEdit::down-button {{ width: 15px; }}
        """)
        controls_layout.addWidget(self.timer_edit)
        self.stats_button = QPushButton("Study Stats")
        self.stats_button.setMinimumHeight(30)
        self.stats_button.setStyleSheet(f"""
            QPushButton {{ background-color: {COLOR_BUTTON_GRAY_BG}; color: {COLOR_BUTTON_TEXT}; border-radius: {BUTTON_RADIUS}px; padding: 5px 15px; font-size: 14px; }}
            QPushButton:hover {{ background-color: #666666; }} QPushButton:pressed {{ background-color: #444444; }}
        """)
        controls_layout.addWidget(self.stats_button)
        self.focus_status_label = QLabel()
        self.focus_status_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.focus_status_label.setStyleSheet(f"color: {COLOR_TEXT}; padding: 5px; border-radius: 3px; font-size: 14px;")
        controls_layout.addWidget(self.focus_status_label)
        self.label2 = QLabel()
        self.label2.setStyleSheet(f"color: #FFAAAA;")
        self.label2.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.label2.setWordWrap(True)
        controls_layout.addWidget(self.label2)
        controls_layout.addSpacerItem(QSpacerItem(20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding))
        self.button = QPushButton()
        self.button.setMinimumHeight(30)
        self.button.setStyleSheet(f"""
            QPushButton {{ background-color: {COLOR_BUTTON_BLUE_BG}; color: {COLOR_BUTTON_TEXT}; border-radius: {BUTTON_RADIUS}px; padding: 5px 15px; font-size: 14px; font-weight: bold; }}
            QPushButton:hover {{ background-color: #0000B0; }} QPushButton:pressed {{ background-color: #000050; }}
        """)
        controls_layout.addWidget(self.button)
        self.controls_widget.setLayout(controls_layout)
        self.history_widget = QWidget()
        self.history_layout = QVBoxLayout(self.history_widget)
        self.history_layout.setContentsMargins(10, 15, 15, 15)
        self.history_label = QLabel("Previous Sessions:")
        self.history_label.setStyleSheet(f"color: {COLOR_TEXT}; font-size: 14px; margin-bottom: 5px;")
        self.history_layout.addWidget(self.history_label)
        self.history_list = QListWidget()
        self.history_list.setStyleSheet(f"""
            QListWidget {{ background-color: #3C3C3C; border: 1px solid #555555; color: {COLOR_TEXT}; }}
            QListWidget::item {{ border-bottom: 1px solid #555555; padding: 5px; }}
            QListWidget::item:hover {{ background-color: #4A4A4A; }}
            QScrollBar:vertical {{ border: 1px solid #555555; background: #3C3C3C; width: 10px; margin: 0px 0px 0px 0px; }}
            QScrollBar::handle:vertical {{ background: #555555; min-height: 20px; border-radius: 5px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; background: none; }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
        """)
        self.history_layout.addWidget(self.history_list)
        self.history_widget.setLayout(self.history_layout)
        self.main_layout.addWidget(self.controls_widget, 1)
        self.main_layout.addWidget(self.history_widget, 2)
        self.main_menu_widget.setLayout(self.main_layout)
        self.weekly_stats_page = WeeklyStatsWidget(self)
        self.central_widget.addWidget(self.main_menu_widget)
        self.central_widget.addWidget(self.weekly_stats_page)

    def _connect_signals(self):
        """Connects UI element signals to slots"""
        self.timer_edit.timeChanged.connect(self.timeChanged)
        self.button.clicked.connect(self.press_button)
        self.stats_button.clicked.connect(self.show_weekly_stats)
        self.update_status_signal.connect(self.update_status_labels)
        self.show_stats_signal.connect(self.callStatsDialog)
        self.update_history_signal.connect(self.populate_history_list)
        self.update_state_signal.connect(self.set_state)
        self.trigger_alert_signal.connect(self.handle_window_alert)

    def show_main_menu(self):
        self.central_widget.setCurrentIndex(self.PAGE_MAIN_MENU)

    def show_weekly_stats(self):
        self.weekly_stats_page.update_display()
        self.central_widget.setCurrentIndex(self.PAGE_WEEKLY_STATS)

    @pyqtSlot()
    def handle_window_alert(self):
        """Triggers the OS-specific window alert"""
        if not self.isActiveWindow():
            print("Triggering window alert.")
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
            item_widget = QLabel("No previous sessions found.")
            item_widget.setStyleSheet(f"color: {COLOR_TEXT}; padding: 10px;")
            item_widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
            list_item = QListWidgetItem(self.history_list)
            list_item.setSizeHint(item_widget.sizeHint())
            self.history_list.addItem(list_item)
            self.history_list.setItemWidget(list_item, item_widget)
            return

        for session in sessions:
            session_id = session['session_id']
            start_dt = strftime('%Y-%m-%d %H:%M', localtime(session['start_time']))
            duration_mins = session['duration_secs'] // 60
            duration_secs = session['duration_secs'] % 60
            focus_perc = session['focus_percentage']

            item_widget = QWidget()
            item_main_layout = QVBoxLayout(item_widget)
            item_main_layout.setContentsMargins(5, 5, 5, 5)
            item_main_layout.setSpacing(5)

            info_text = f"{start_dt} ({duration_mins}m {duration_secs}s) - {focus_perc:.1f}% Focus"
            info_label = QLabel(info_text)
            info_label.setStyleSheet(f"color: {COLOR_TEXT};")
            info_label.setToolTip(f"Session ID: {session_id}")
            item_main_layout.addWidget(info_label)

            focus_bar = FocusBar(session['timeline'])
            item_main_layout.addWidget(focus_bar)

            buttons_layout = QHBoxLayout()
            buttons_layout.addStretch(1)

            # --- Copy button ---
            copy_button = QPushButton("📋") # Clipboard emoji
            copy_button.setFixedSize(25, 25)
            copy_button.setToolTip(f"Copy session {session_id} summary image to clipboard")
            copy_button.setStyleSheet(f"""
                QPushButton {{ background-color: {COLOR_BUTTON_GRAY_BG}; color: {COLOR_BUTTON_TEXT}; border-radius: 5px; }}
                QPushButton:hover {{ background-color: #666666; }} QPushButton:pressed {{ background-color: #444444; }}
            """)
            copy_button.clicked.connect(lambda checked=False, sid=session_id: self.handle_copy_session_image(sid)) # Connect to image copy handler

            delete_button = QPushButton("-")
            delete_button.setFixedSize(25, 25)
            delete_button.setToolTip(f"Delete session {session_id}")
            delete_button.setStyleSheet(copy_button.styleSheet())
            delete_button.clicked.connect(lambda checked=False, sid=session_id: self.handle_delete_session(sid))
            buttons_layout.addWidget(delete_button)

            item_main_layout.addLayout(buttons_layout)
            item_widget.setLayout(item_main_layout)

            list_item = QListWidgetItem(self.history_list)
            list_item.setSizeHint(item_widget.sizeHint())

            self.history_list.addItem(list_item)
            self.history_list.setItemWidget(list_item, item_widget)

    # --- Combined Image Copy Handler ---
    def handle_copy_session_image(self, session_id):
        """Copies a combined image of session text and focus bar to clipboard"""
        print(f"Copying combined image for session ID: {session_id}")
        session = get_session_by_id(session_id)
        if not session:
            QMessageBox.warning(self, "Error", f"Could not find session {session_id} to copy.")
            return

        try:
            # 1. Prepare Text Lines
            start_dt = strftime('%Y-%m-%d %H:%M', localtime(session['start_time']))
            duration_mins = session['duration_secs'] // 60
            duration_secs = session['duration_secs'] % 60
            focus_perc = session['focus_percentage']
            line1 = f"Focus Session: {start_dt}"
            line2 = f"Duration: {duration_mins}m {duration_secs}s"
            line3 = f"Focus Percentage: {focus_perc:.1f}%"
            text_lines = [line1, line2, line3]

            # 2. Prepare Focus Bar Pixmap
            temp_focus_bar = FocusBar(session['timeline'])
            bar_width = 300
            bar_height = 30
            focus_pixmap = temp_focus_bar.get_pixmap(width=bar_width, height=bar_height)

            # 3. Calculate Combined Image Size
            padding = 10
            text_font = QFont("Arial", 12) # Choose font for text rendering
            fm = QFontMetrics(text_font)
            text_height = fm.height()
            total_text_height = text_height * len(text_lines)
            # Use text width or bar width, whichever is larger
            text_width = max(fm.horizontalAdvance(line) for line in text_lines)
            combined_width = max(text_width, bar_width) + 2 * padding
            combined_height = total_text_height + focus_pixmap.height() + 3 * padding # Padding top, between, bottom

            # 4. Create Combined Pixmap and Painter
            combined_pixmap = QPixmap(QSize(int(combined_width), int(combined_height)))
            combined_pixmap.fill(QColor(COLOR_BACKGROUND)) # Fill background
            painter = QPainter(combined_pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setFont(text_font)
            painter.setPen(QColor(COLOR_TEXT)) # Set text color

            # 5. Draw Text
            current_y = padding
            for line in text_lines:
                painter.drawText(padding, current_y + text_height - fm.descent(), line)
                current_y += text_height

            # 6. Draw Focus Bar Pixmap
            focus_bar_y = current_y + padding
            painter.drawPixmap(padding, focus_bar_y, focus_pixmap)

            # 7. End Painting
            painter.end()

            # 8. Copy Combined Image to Clipboard
            clipboard = QApplication.clipboard()
            clipboard.setPixmap(combined_pixmap)

            QMessageBox.information(self, "Copied", "Session summary image copied to clipboard.")

        except Exception as e:
            print(f"Error copying session image {session_id}: {e}")
            QMessageBox.warning(self, "Error", f"Could not copy session image: {e}")
    # --- End of Combined Image Copy Handler ---


    def handle_delete_session(self, session_id):
        """Confirms and deletes a session"""
        reply = QMessageBox.question(self, 'Confirm Deletion',
                                     f"Delete session {session_id}?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            try:
                delete_session(session_id)
                self.load_and_display_history()
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Could not delete session: {e}")

    @pyqtSlot(str, list)
    def callStatsDialog(self, stats: str, timeline: list):
        """Shows the post-session stats dialog"""
        dlg = QDialog(self)
        dlg.setWindowTitle("Session Statistics")
        dlg.setStyleSheet(f"QDialog {{ background-color: {COLOR_BACKGROUND}; color: {COLOR_TEXT}; }} QLabel {{ color: {COLOR_TEXT}; }}")
        dlgLayout = QVBoxLayout()
        dlgLabel = QLabel(stats)
        dlgLayout.addWidget(dlgLabel)
        focus_bar = FocusBar(timeline)
        dlgLayout.addWidget(focus_bar)
        dlgButtonBox = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        ok_button = dlgButtonBox.button(QDialogButtonBox.StandardButton.Ok)
        if ok_button:
             ok_button.setMinimumHeight(30)
             ok_button.setStyleSheet(self.button.styleSheet())
        dlgButtonBox.accepted.connect(dlg.accept)
        dlgLayout.addWidget(dlgButtonBox)
        dlg.setLayout(dlgLayout)
        dlg.exec()
        self.load_and_display_history()

    def press_button(self):
        """Handles main button clicks based on state"""
        if self.state == 0: self.set_state(1)
        elif self.state == 1: self.set_state(2)
        elif self.state == 2: self.set_state(0)

    @pyqtSlot(int)
    def set_state(self, new_state):
        """Updates the application state and triggers UI/thread changes"""
        if self.state == new_state: return
        print(f"Changing state from {self.state} to {new_state}")
        if new_state in [1, 2] and self.central_widget.currentIndex() != self.PAGE_MAIN_MENU:
             self.show_main_menu()
        previous_state = self.state
        self.state = new_state
        if new_state == 1:
            self.eyeLeft_boundary.reset()
            self.eyeRight_boundary.reset()
            try:
                self.webcam = cv2.VideoCapture(0)
                if not self.webcam or not self.webcam.isOpened():
                    raise ValueError("Cannot open webcam")
            except Exception as e:
                 print(f"Webcam Error: {e}")
                 self.update_status_signal.emit("Error: Cannot open webcam.", False, False)
                 self.set_state(0)
                 return
            self.tracking_thread = threading.Thread(target=self.tracking_loop, daemon=True)
            self.tracking_thread.start()
        elif new_state == 2:
            if not self.eyeLeft_boundary.calibrated or not self.eyeRight_boundary.calibrated:
                 self.update_status_signal.emit("Calibration incomplete. Look around more.", False, False)
                 self.set_state(1)
                 return
        elif new_state == 0:
             if self.webcam:
                try:
                    self.webcam.release()
                    print("Webcam released.")
                except Exception as e:
                    print(f"Error releasing webcam: {e}")
                self.webcam = None
             self.tracking_thread = None
        self.update_ui_for_state()

    def update_ui_for_state(self):
        """Updates UI elements based on the current state"""
        if self.state in [0, 1, 2]:
            self.show_main_menu()
        is_idle = (self.state == 0)
        is_calibrating = (self.state == 1)
        is_session = (self.state == 2)
        self.timer_edit.setVisible(is_idle)
        self.timer_edit.setEnabled(is_idle)
        self.stats_button.setVisible(is_idle)
        self.history_widget.setVisible(is_idle)
        self.focus_status_label.setVisible(is_calibrating or is_session)
        self.label2.setVisible(is_calibrating or is_session)
        if is_idle:
            self.label1.setText("Select duration and press Start")
            self.button.setText("Start")
            self.focus_status_label.setText("Status: Idle")
            self.focus_status_label.setStyleSheet(f"background-color: {COLOR_BACKGROUND}; color: {COLOR_TEXT}; padding: 5px; border-radius: 3px; font-size: 14px;")
            self.label2.clear()
        elif is_calibrating:
            self.label1.setText("Calibrating: Look at all screen edges.\nMove head slightly forward/backward too.\nThen press Calibrate.")
            self.button.setText("Calibrate")
            self.focus_status_label.setText("Status: Calibrating")
            self.focus_status_label.setStyleSheet(f"background-color: #003366; color: {COLOR_TEXT}; padding: 5px; border-radius: 3px; font-size: 14px;")
        elif is_session:
            self.label1.setText("Session in progress...")
            self.button.setText("End Session")

    @pyqtSlot(str, bool, bool)
    def update_status_labels(self, status_text, is_focused, is_alerting):
        """Updates status labels via signals from the tracking thread"""
        self.label2.setText(status_text)
        if self.state == 1:
             pass
        elif self.state == 2:
            if is_alerting:
                self.focus_status_label.setText("Status: Not Focusing!")
                self.focus_status_label.setStyleSheet(f"background-color: {COLOR_FOCUS_RED}; padding: 5px; border-radius: 3px; color: {COLOR_TEXT}; font-size: 14px;")
            elif is_focused:
                self.focus_status_label.setText("Status: Focusing")
                self.focus_status_label.setStyleSheet(f"background-color: {COLOR_FOCUS_GREEN}; padding: 5px; border-radius: 3px; color: #000000; font-size: 14px;")
            else:
                self.focus_status_label.setText("Status: Not Focusing")
                self.focus_status_label.setStyleSheet(f"background-color: #555555; padding: 5px; border-radius: 3px; color: {COLOR_TEXT}; font-size: 14px;")

    def tracking_loop(self):
        """Background thread loop for camera feed processing and tracking"""
        timeline = []
        session_start_time = None
        last_sample_time = None
        session_running = False
        while self.state != 0:
            try:
                status_msg = ""
                if not self.webcam or not self.webcam.isOpened():
                    status_msg = "Webcam error."
                    self.update_status_signal.emit(status_msg, False, False)
                    break
                ret, frame = self.webcam.read()
                if not ret:
                    status_msg = "Frame read error."
                    self.update_status_signal.emit(status_msg, False, False)
                    continue
                pupils_located = False
                left_coords = None
                right_coords = None
                try:
                    self.gaze.refresh(frame)
                    pupils_located = self.gaze.pupils_located
                    if pupils_located:
                        left_coords = self.gaze.pupil_left_coords()
                        right_coords = self.gaze.pupil_right_coords()
                except Exception as e_gaze:
                    print(f"Gaze tracking error: {e_gaze}")
                    status_msg = "Tracking Error"
                current_time = time()
                is_focused = False
                is_alerting = False
                if self.state == 1:
                    if not pupils_located and not status_msg:
                        status_msg = "Warning: Pupil not detected"
                    if pupils_located:
                        self.eyeLeft_boundary.adjust_coords(left_coords)
                        self.eyeRight_boundary.adjust_coords(right_coords)
                elif self.state == 2:
                    if not session_running:
                        session_start_time = current_time
                        last_sample_time = session_start_time
                        session_running = True
                        timeline = []
                        self.consecutive_unfocused_start = None
                        self.alert_sounded = False
                    if session_start_time is None:
                         print("Error: session_start_time is None in state 2")
                         session_start_time = current_time
                         last_sample_time = session_start_time
                    total_study_secs = QTime(0, 0, 0).secsTo(self.study_time)
                    elapsed_session_secs = current_time - session_start_time
                    if pupils_located:
                         left_outside = self.eyeLeft_boundary.check_coords(left_coords)
                         right_outside = self.eyeRight_boundary.check_coords(right_coords)
                         is_focused = not (left_outside and right_outside)
                    else:
                        is_focused = False
                        if not status_msg:
                            status_msg = "Warning: Pupil not detected"
                    if not is_focused:
                        if self.consecutive_unfocused_start is None:
                            self.consecutive_unfocused_start = current_time
                            self.alert_sounded = False
                        elif current_time - self.consecutive_unfocused_start >= ALERT_THRESHOLD_SECONDS:
                            is_alerting = True
                            if not self.alert_sounded:
                                QApplication.beep()
                                self.trigger_alert_signal.emit()
                                self.alert_sounded = True
                    else:
                         self.consecutive_unfocused_start = None
                         self.alert_sounded = False
                    if current_time - last_sample_time >= SAMPLE_INTERVAL_SECONDS:
                        timeline.append(is_focused)
                        last_sample_time = current_time
                    if elapsed_session_secs >= total_study_secs:
                        print("Session timer finished.")
                        self.finish_session(session_start_time, current_time, timeline)
                        break
                self.update_status_signal.emit(status_msg, is_focused, is_alerting)
                key = cv2.waitKey(1) & 0xFF
                if key == ord('q'):
                     self.update_state_signal.emit(0)
                     break
            except Exception as e_loop:
                print(f"Unexpected error in tracking loop iteration: {e_loop}")
                status_msg = "Critical Error in Tracking Loop"
                self.update_status_signal.emit(status_msg, False, False)
                break
        if session_running and self.state == 0:
            print("Loop finished, calling finish_session for manual stop.")
            if session_start_time is not None:
                 self.finish_session(session_start_time, time(), timeline)
            else:
                 print("Warning: Cannot finish session cleanly as start time was not recorded.")
        self.update_state_signal.emit(0)
        print("Tracking thread finished.")

    def finish_session(self, start_time, end_time, timeline):
        """Finalizes a session: calculates stats, saves, shows dialog with error handling"""
        print(f"Finishing session that started at {start_time}")
        try:
            elapsed_secs = int(end_time - start_time)
            total_samples = len(timeline)
            focus_percentage = 0.0
            if total_samples > 0:
                focus_count = sum(1 for sample in timeline if sample)
                focus_percentage = (focus_count / total_samples) * 100
            if elapsed_secs > 0:
                save_session(start_time, end_time, focus_percentage, timeline)
            else:
                print("Session too short, not saving.")
            minutes_elapsed = elapsed_secs // 60
            seconds_rem = elapsed_secs % 60
            stats = (f"Session Finished\n\nDuration: {minutes_elapsed}m {seconds_rem}s\nFocus: {focus_percentage:.1f}%")
            self.show_stats_signal.emit(stats, timeline)
        except Exception as e:
            print(f"Error during finish_session: {e}")

    def closeEvent(self, event):
        """Handles window close event for proper cleanup"""
        print("Closing application...")
        self.set_state(0)
        QApplication.processEvents()
        cv2.destroyAllWindows()
        event.accept()

# --- Main Execution ---
if __name__ == '__main__':
    app = QApplication([])
    app.setStyleSheet(f"""
        QWidget {{ color: {COLOR_TEXT}; font-size: 13px; }}
        QMainWindow {{ background-color: {COLOR_BACKGROUND}; }}
        QDialog {{ background-color: {COLOR_BACKGROUND}; }}
        QMessageBox {{ background-color: {COLOR_BACKGROUND}; }}
        QMessageBox QLabel {{ color: {COLOR_TEXT}; }}
        QMessageBox QPushButton {{
            background-color: {COLOR_BUTTON_BLUE_BG}; color: {COLOR_BUTTON_TEXT};
            border-radius: {BUTTON_RADIUS}px; padding: 5px 15px;
            min-width: 60px; min-height: 25px;
        }}
         QMessageBox QPushButton:hover {{ background-color: #0000B0; }}
         QMessageBox QPushButton:pressed {{ background-color: #000050; }}
    """)
    window = MainWindow()
    window.show()
    app.exec()
