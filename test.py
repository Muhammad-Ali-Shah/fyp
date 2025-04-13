import unittest
import os
import sqlite3
import datetime
from time import time
from unittest.mock import patch, MagicMock

import app as focus_tracker_app
from app import WeeklyStatsWidget
from PyQt6.QtCore import QDate
from PyQt6.QtWidgets import QApplication


# --- Test Configuration ---
TEST_DB_NAME = 'test_focus_tracker.db'

# --- Ensures QApplication exists for all tests ---
# Some Qt components might require this even if not explicitly used in a test
_app_singleton = QApplication.instance()
if _app_singleton is None:
    _app_singleton = QApplication([])

class TestEyeBoundary(unittest.TestCase):
    """Tests the EyeBoundary helper class"""

    def setUp(self):
        """Set up for test methods"""
        self.boundary = focus_tracker_app.EyeBoundary()

    def test_initial_state(self):
        """Test initial values and calibrated status"""
        self.assertEqual(self.boundary.min_x, 1_000_000)
        self.assertEqual(self.boundary.max_x, 0)
        self.assertEqual(self.boundary.min_y, 1_000_000)
        self.assertEqual(self.boundary.max_y, 0)
        self.assertFalse(self.boundary.calibrated)
        # Should report outside if not calibrated
        self.assertTrue(self.boundary.check_coords((100, 100)))

    def test_adjust_coords(self):
        """Test coordinate adjustment"""
        self.boundary.adjust_coords((100, 200))
        self.assertEqual(self.boundary.min_x, 100)
        self.assertEqual(self.boundary.max_x, 100)
        self.assertEqual(self.boundary.min_y, 200)
        self.assertEqual(self.boundary.max_y, 200)
        self.assertFalse(self.boundary.calibrated) # Needs range

        self.boundary.adjust_coords((50, 250))
        self.assertEqual(self.boundary.min_x, 50)
        self.assertEqual(self.boundary.max_x, 100)
        self.assertEqual(self.boundary.min_y, 200)
        self.assertEqual(self.boundary.max_y, 250)
        self.assertTrue(self.boundary.calibrated) # Now has range

        self.boundary.adjust_coords((75, 225)) # Point within current range
        self.assertEqual(self.boundary.min_x, 50)
        self.assertEqual(self.boundary.max_x, 100)
        self.assertEqual(self.boundary.min_y, 200)
        self.assertEqual(self.boundary.max_y, 250)

        self.boundary.adjust_coords(None) # Should ignore None
        self.assertEqual(self.boundary.min_x, 50)
        self.assertEqual(self.boundary.max_x, 100)

        self.boundary.adjust_coords((0, 0)) # Should ignore (0,0)
        self.assertEqual(self.boundary.min_x, 50)
        self.assertEqual(self.boundary.max_x, 100)


    def test_check_coords(self):
        """Test checking coordinates against boundaries"""
        # Calibrate first
        self.boundary.adjust_coords((100, 100))
        self.boundary.adjust_coords((200, 200))
        self.assertTrue(self.boundary.calibrated)

        # Inside (allowing for tolerance)
        self.assertFalse(self.boundary.check_coords((150, 150)))
        self.assertFalse(self.boundary.check_coords((100, 100)))
        self.assertFalse(self.boundary.check_coords((200, 200)))
        self.assertFalse(self.boundary.check_coords((105, 195))) # Inside with tolerance

        # Outside
        self.assertTrue(self.boundary.check_coords((50, 150)))  # x too low
        self.assertTrue(self.boundary.check_coords((250, 150))) # x too high
        self.assertTrue(self.boundary.check_coords((150, 50)))  # y too low
        self.assertTrue(self.boundary.check_coords((150, 250))) # y too high
        self.assertTrue(self.boundary.check_coords((90, 90)))   # Both too low (outside tolerance)
        self.assertTrue(self.boundary.check_coords((210, 210))) # Both too high (outside tolerance)

        # Check None coords
        self.assertTrue(self.boundary.check_coords(None))
        self.assertTrue(self.boundary.check_coords((None, 150)))
        self.assertTrue(self.boundary.check_coords((150, None)))

    def test_reset(self):
        """Test resetting the boundary"""
        self.boundary.adjust_coords((100, 100))
        self.boundary.adjust_coords((200, 200))
        self.assertTrue(self.boundary.calibrated)

        self.boundary.reset()
        self.test_initial_state() # Should be back to initial state


class TestDatabaseFunctions(unittest.TestCase):
    """Tests the database interaction functions"""

    @classmethod
    def setUpClass(cls):
        """Ensure a clean slate before tests"""
        if os.path.exists(TEST_DB_NAME):
            os.remove(TEST_DB_NAME)

    def setUp(self):
        """Set up for each test method"""
        # Ensure clean DB for each test
        if os.path.exists(TEST_DB_NAME):
            os.remove(TEST_DB_NAME)
        # Patch the DB_NAME constant in the application module being tested
        self.db_name_patcher = patch('app.DB_NAME', TEST_DB_NAME) # <-- Patched app
        self.mock_db_name = self.db_name_patcher.start()
        # Initialise the database using the function from the app
        focus_tracker_app.init_db()

    def tearDown(self):
        """Clean up after each test method"""
        self.db_name_patcher.stop()
        if os.path.exists(TEST_DB_NAME):
            os.remove(TEST_DB_NAME)

    def test_init_db(self):
        """Test if init_db creates the table"""
        conn = sqlite3.connect(TEST_DB_NAME)
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sessions';")
            result = cursor.fetchone()
            self.assertIsNotNone(result)
            self.assertEqual(result[0], 'sessions')
        finally:
            conn.close()

    def test_save_and_load_session(self):
        """Test saving a session and loading it back"""
        t1 = int(time()) - 60
        t2 = int(time())
        timeline = [True, True, False, True]
        focus_perc = 75.0

        focus_tracker_app.save_session(t1, t2, focus_perc, timeline)

        sessions = focus_tracker_app.load_sessions()
        self.assertEqual(len(sessions), 1)
        session = sessions[0]

        self.assertEqual(session['start_time'], t1)
        self.assertEqual(session['end_time'], t2)
        self.assertAlmostEqual(session['focus_percentage'], focus_perc)
        self.assertEqual(session['timeline'], timeline)
        self.assertEqual(session['duration_secs'], t2 - t1)
        self.assertIn('session_id', session)

    def test_load_multiple_sessions_order(self):
        """Test loading multiple sessions ensures descending order by start_time"""
        t1 = int(time()) - 120
        t2 = int(time()) - 60
        t3 = int(time())
        timeline1 = [True, False]
        timeline2 = [False, True]

        focus_tracker_app.save_session(t1, t2 - 1, 50.0, timeline1) # Session 1 (earliest)
        focus_tracker_app.save_session(t2, t3, 50.0, timeline2)     # Session 2 (latest)

        sessions = focus_tracker_app.load_sessions()
        self.assertEqual(len(sessions), 2)
        # Check if the latest session (t2 start time) is first
        self.assertEqual(sessions[0]['start_time'], t2)
        self.assertEqual(sessions[1]['start_time'], t1)

    def test_delete_session(self):
        """Test deleting a specific session"""
        t1 = int(time()) - 120
        t2 = int(time()) - 60
        t3 = int(time())
        timeline1 = [True]
        timeline2 = [False]

        focus_tracker_app.save_session(t1, t2 - 1, 100.0, timeline1)
        focus_tracker_app.save_session(t2, t3, 0.0, timeline2)

        sessions_before = focus_tracker_app.load_sessions()
        self.assertEqual(len(sessions_before), 2)
        # Get ID of the earlier session (which will be second in the list)
        session_to_delete_id = sessions_before[1]['session_id']

        focus_tracker_app.delete_session(session_to_delete_id)

        sessions_after = focus_tracker_app.load_sessions()
        self.assertEqual(len(sessions_after), 1)
        # Only the latter session should remain
        self.assertEqual(sessions_after[0]['start_time'], t2)
        self.assertNotEqual(sessions_after[0]['session_id'], session_to_delete_id)

    def test_load_empty_db(self):
        """Test loading from an empty database"""
        sessions = focus_tracker_app.load_sessions()
        self.assertEqual(len(sessions), 0)

    def test_get_session_by_id_exists(self):
        """Test retrieving a specific session by its ID."""
        t1 = int(time()) - 60
        t2 = int(time())
        timeline = [True, False]
        focus_perc = 50.0
        focus_tracker_app.save_session(t1, t2, focus_perc, timeline)
        sessions = focus_tracker_app.load_sessions() # Load to get the ID
        saved_id = sessions[0]['session_id']

        retrieved_session = focus_tracker_app.get_session_by_id(saved_id)

        self.assertIsNotNone(retrieved_session)
        self.assertEqual(retrieved_session['session_id'], saved_id)
        self.assertEqual(retrieved_session['start_time'], t1)
        self.assertEqual(retrieved_session['end_time'], t2)
        self.assertAlmostEqual(retrieved_session['focus_percentage'], focus_perc)
        self.assertEqual(retrieved_session['timeline'], timeline)
        self.assertEqual(retrieved_session['duration_secs'], t2 - t1)

    def test_get_session_by_id_not_exists(self):
        """Test retrieving a non-existent session ID."""
        non_existent_id = 99999
        retrieved_session = focus_tracker_app.get_session_by_id(non_existent_id)
        self.assertIsNone(retrieved_session)

    def test_get_session_by_id_invalid_json(self):
        """Test retrieving a session with invalid JSON data."""
        t1 = int(time()) - 60
        t2 = int(time())
        valid_timeline = [True]
        # Save normally first to get an ID
        focus_tracker_app.save_session(t1, t2, 100.0, valid_timeline)
        sessions = focus_tracker_app.load_sessions()
        saved_id = sessions[0]['session_id']

        # Manually corrupt the JSON data in the database
        conn = sqlite3.connect(TEST_DB_NAME)
        cursor = conn.cursor()
        try:
            cursor.execute("UPDATE sessions SET focus_data = ? WHERE session_id = ?",
                           ('this is not json', saved_id))
            conn.commit()
        finally:
            conn.close()

        # Attempt to retrieve the corrupted session
        retrieved_session = focus_tracker_app.get_session_by_id(saved_id)

        self.assertIsNotNone(retrieved_session, "Should still retrieve row data even if JSON fails")
        self.assertEqual(retrieved_session['session_id'], saved_id)
        # Check that the timeline is defaulted to an empty list due to JSON error
        self.assertEqual(retrieved_session['timeline'], [])
        # Other fields should still be present
        self.assertEqual(retrieved_session['start_time'], t1)
        self.assertEqual(retrieved_session['end_time'], t2)
        self.assertAlmostEqual(retrieved_session['focus_percentage'], 100.0)

    def test_load_sessions_handles_invalid_json(self):
        """Test load_sessions gracefully handles rows with invalid JSON."""
        t1 = int(time()) - 120
        t2 = int(time()) - 60
        t3 = int(time())
        valid_timeline = [True, False]
        # Save one valid session
        focus_tracker_app.save_session(t2, t3, 50.0, valid_timeline)
        # Save another session and then corrupt its JSON
        focus_tracker_app.save_session(t1, t2 - 1, 100.0, [True])
        sessions_temp = focus_tracker_app.load_sessions()
        corrupt_id = sessions_temp[1]['session_id'] # ID of the earlier session

        conn = sqlite3.connect(TEST_DB_NAME)
        cursor = conn.cursor()
        try:
            cursor.execute("UPDATE sessions SET focus_data = ? WHERE session_id = ?",
                           ('{invalid json', corrupt_id))
            conn.commit()
        finally:
            conn.close()

        # Act: Load all sessions
        sessions = focus_tracker_app.load_sessions()

        # Assert
        self.assertEqual(len(sessions), 2, "Should load both rows")
        # Find the session that was corrupted (it will be the second one due to ordering)
        corrupted_session_data = sessions[1]
        valid_session_data = sessions[0]

        self.assertEqual(corrupted_session_data['session_id'], corrupt_id)
        self.assertEqual(corrupted_session_data['timeline'], [], "Timeline should be empty list for corrupted JSON")
        self.assertEqual(corrupted_session_data['start_time'], t1) # Other data should be intact

        self.assertEqual(valid_session_data['timeline'], valid_timeline, "Valid session timeline should be correct")
        self.assertEqual(valid_session_data['start_time'], t2)


    def test_get_weekly_stats_basic(self):
        """Test calculating weekly stats with sessions on multiple days."""
        # --- Arrange ---
        now = datetime.datetime.now()
        start_of_current_week = now - datetime.timedelta(days=now.weekday())
        monday_start_dt = start_of_current_week.replace(hour=0, minute=0, second=0, microsecond=0)
        monday_ts = int(monday_start_dt.timestamp())

        duration_mon = 1800 # 30 mins
        duration_wed = 3600 # 60 mins
        duration_fri = 900  # 15 mins

        ts_mon_start = monday_ts + (10 * 60 * 60) # Monday 10:00
        ts_mon_end = ts_mon_start + duration_mon
        ts_wed_start = monday_ts + (2 * 24 * 60 * 60) + (14 * 60 * 60) # Wednesday 14:00
        ts_wed_end = ts_wed_start + duration_wed
        ts_fri_start = monday_ts + (4 * 24 * 60 * 60) + (9 * 60 * 60) # Friday 09:00
        ts_fri_end = ts_fri_start + duration_fri
        ts_wed_start_2 = ts_wed_start + 7200 # Wednesday 16:00
        ts_wed_end_2 = ts_wed_start_2 + duration_wed # Another 60 mins

        focus_tracker_app.save_session(ts_mon_start, ts_mon_end, 50.0, [True, False])
        focus_tracker_app.save_session(ts_wed_start, ts_wed_end, 75.0, [True]*3 + [False])
        focus_tracker_app.save_session(ts_fri_start, ts_fri_end, 100.0, [True])
        focus_tracker_app.save_session(ts_wed_start_2, ts_wed_end_2, 80.0, [True]*4 + [False])

        # --- Act ---
        weekly_stats = focus_tracker_app.get_weekly_stats(monday_ts)

        # --- Assert ---
        self.assertIsInstance(weekly_stats, list)
        self.assertEqual(len(weekly_stats), 7, "Weekly stats should have 7 days")
        self.assertEqual(weekly_stats[0], duration_mon)
        self.assertEqual(weekly_stats[1], 0) # Tuesday
        self.assertEqual(weekly_stats[2], duration_wed + duration_wed) # Sum of both Wed sessions
        self.assertEqual(weekly_stats[3], 0) # Thursday
        self.assertEqual(weekly_stats[4], duration_fri)
        self.assertEqual(weekly_stats[5], 0) # Saturday
        self.assertEqual(weekly_stats[6], 0) # Sunday

    def test_get_weekly_stats_empty_week(self):
        """Test calculating weekly stats for a week with no sessions."""
        # --- Arrange ---
        now = datetime.datetime.now()
        start_of_current_week = now - datetime.timedelta(days=now.weekday())
        monday_start_dt = start_of_current_week.replace(hour=0, minute=0, second=0, microsecond=0)
        monday_ts = int(monday_start_dt.timestamp())
        # DB is empty due to setUp

        # --- Act ---
        weekly_stats = focus_tracker_app.get_weekly_stats(monday_ts)

        # --- Assert ---
        self.assertEqual(weekly_stats, [0, 0, 0, 0, 0, 0, 0], "Stats for an empty week should be all zeros")

    def test_get_weekly_stats_boundary_conditions(self):
        """Test weekly stats calculation at the exact start and end of the week."""
        # --- Arrange ---
        now = datetime.datetime.now()
        start_of_target_week_dt = (now - datetime.timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        target_monday_ts = int(start_of_target_week_dt.timestamp())

        one_week_secs = 7 * 24 * 60 * 60
        duration = 600 # 10 mins

        # Session exactly at the start of Monday
        ts_mon_start = target_monday_ts
        ts_mon_end = ts_mon_start + duration

        # Session just before the end of Sunday (of the target week)
        ts_sun_end = target_monday_ts + one_week_secs # This is next Monday 00:00:00
        ts_sun_start = ts_sun_end - duration # Start 10 mins before next Mon 00:00

        # Session just BEFORE the target week (previous Sunday)
        ts_prev_sun_start = target_monday_ts - duration - 1 # Ends 1 sec before target Mon
        ts_prev_sun_end = target_monday_ts - 1

        # Session exactly ON the next Monday (outside target week)
        ts_next_mon_start = target_monday_ts + one_week_secs
        ts_next_mon_end = ts_next_mon_start + duration

        # Save sessions
        focus_tracker_app.save_session(ts_mon_start, ts_mon_end, 100.0, [True])        # Should be in index 0
        focus_tracker_app.save_session(ts_sun_start, ts_sun_end, 100.0, [True])        # Should be in index 6
        focus_tracker_app.save_session(ts_prev_sun_start, ts_prev_sun_end, 100.0, [True]) # Should NOT be included
        focus_tracker_app.save_session(ts_next_mon_start, ts_next_mon_end, 100.0, [True]) # Should NOT be included

        # --- Act ---
        weekly_stats = focus_tracker_app.get_weekly_stats(target_monday_ts)

        # --- Assert ---
        self.assertEqual(len(weekly_stats), 7)
        self.assertEqual(weekly_stats[0], duration, "Session starting Mon 00:00 should be included")
        self.assertEqual(weekly_stats[1], 0)
        self.assertEqual(weekly_stats[2], 0)
        self.assertEqual(weekly_stats[3], 0)
        self.assertEqual(weekly_stats[4], 0)
        self.assertEqual(weekly_stats[5], 0)
        self.assertEqual(weekly_stats[6], duration, "Session ending just before next Mon 00:00 should be included")


class TestMainWindowLogic(unittest.TestCase):
    """Tests specific logic within the MainWindow class"""

    def setUp(self):
        """Set up for MainWindow tests"""
        # Patch the DB name for main window tests that might interact with history
        self.db_name_patcher = patch('app.DB_NAME', TEST_DB_NAME) # <-- Patched app
        self.db_name_patcher.start()
        # Clean DB before main window tests that load history
        if os.path.exists(TEST_DB_NAME):
            os.remove(TEST_DB_NAME)
        focus_tracker_app.init_db()


    def tearDown(self):
        """Clean up DB patch"""
        self.db_name_patcher.stop()
        if os.path.exists(TEST_DB_NAME):
            os.remove(TEST_DB_NAME)


    @patch('app.QMessageBox') # Patch MessageBox in the app module
    @patch('app.delete_session') # Patch delete_session in the app module
    @patch.object(focus_tracker_app.MainWindow, 'load_and_display_history') # Mock method directly
    def test_handle_delete_session_confirm_yes(self, mock_load_history, mock_delete, mock_msgbox):
        """Test delete handler when user confirms 'Yes'"""
        # Arrange
        mock_msgbox.question.return_value = focus_tracker_app.QMessageBox.StandardButton.Yes
        # Create instance within test to ensure mocks are active
        window = focus_tracker_app.MainWindow()
        # Reset mock called during __init__ before testing the method
        mock_load_history.reset_mock()
        test_session_id = 123

        # Act
        window.handle_delete_session(test_session_id)

        # Assert
        mock_msgbox.question.assert_called_once()
        mock_delete.assert_called_once_with(test_session_id)
        mock_load_history.assert_called_once() # Check history is refreshed *after* delete

    @patch('app.QMessageBox')
    @patch('app.delete_session')
    @patch.object(focus_tracker_app.MainWindow, 'load_and_display_history')
    def test_handle_delete_session_confirm_no(self, mock_load_history, mock_delete, mock_msgbox):
        """Test delete handler when user confirms 'No'"""
        # Arrange
        mock_msgbox.question.return_value = focus_tracker_app.QMessageBox.StandardButton.No
        window = focus_tracker_app.MainWindow()
        mock_load_history.reset_mock()
        test_session_id = 456

        # Act
        window.handle_delete_session(test_session_id)

        # Assert
        mock_msgbox.question.assert_called_once()
        mock_delete.assert_not_called() # Ensure delete_session was NOT called
        mock_load_history.assert_not_called() # Ensure history refresh was NOT called

class TestWeeklyStatsWidgetLogic(unittest.TestCase):
    """Tests logic specific to the WeeklyStatsWidget"""

    def setUp(self):
        """Set up for WeeklyStatsWidget tests"""
        # WeeklyStatsWidget needs a reference to the main window, mock it
        self.mock_main_window = MagicMock()
        # Create the widget instance for testing
        self.widget = WeeklyStatsWidget(self.mock_main_window)

    def test_weekly_stats_widget_navigation(self):
        """Test the show_prev_week and show_next_week methods."""
        initial_date = self.widget.current_week_start_date
        expected_prev_date = initial_date.addDays(-7)
        expected_next_date = initial_date # After going back then forward

        # Patch the update_display method on the widget instance
        with patch.object(self.widget, 'update_display') as mock_update:
            # Test going back one week
            self.widget.show_prev_week()
            self.assertEqual(self.widget.current_week_start_date, expected_prev_date)
            mock_update.assert_called_once()

            # Reset mock and test going forward one week
            mock_update.reset_mock()
            self.widget.show_next_week()
            self.assertEqual(self.widget.current_week_start_date, expected_next_date)
            mock_update.assert_called_once()

    def test_weekly_stats_widget_next_week_boundary(self):
        """Test that show_next_week doesn't advance beyond the current week."""
        # Calculate Monday of the current actual week
        today = QDate.currentDate()
        monday_this_week = today.addDays(-today.dayOfWeek() + 1)

        # Set the widget's date to this Monday
        self.widget.current_week_start_date = monday_this_week
        stored_date = self.widget.current_week_start_date # Store for comparison

        # Patch the update_display method
        with patch.object(self.widget, 'update_display') as mock_update:
            # Try to advance past the current week
            self.widget.show_next_week()

            # Assert the date did not change
            self.assertEqual(self.widget.current_week_start_date, stored_date)
            # Assert update_display was still called (to refresh display even if date didn't change)
            mock_update.assert_called_once()

        past_monday = monday_this_week.addDays(-7)
        self.widget.current_week_start_date = past_monday
        with patch.object(self.widget, 'update_display') as mock_update:
            self.widget.show_next_week()
            self.assertEqual(self.widget.current_week_start_date, monday_this_week)
            mock_update.assert_called_once()

if __name__ == '__main__':
    unittest.main(argv=['first-arg-is-ignored'], exit=False)
