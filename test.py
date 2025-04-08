import unittest
import os
import sqlite3
import json
from time import time
from unittest.mock import patch, MagicMock, ANY

import app as focus_tracker_app

# --- Test Configuration ---
TEST_DB_NAME = 'test_focus_tracker.db'

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
        self.db_name_patcher = patch('app.DB_NAME', TEST_DB_NAME)
        self.db_name_patcher.start()
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
        session_to_delete_id = sessions_before[1]['session_id'] # ID of the earlier session

        focus_tracker_app.delete_session(session_to_delete_id)

        sessions_after = focus_tracker_app.load_sessions()
        self.assertEqual(len(sessions_after), 1)
        self.assertEqual(sessions_after[0]['start_time'], t2) # Only the later session should remain
        self.assertNotEqual(sessions_after[0]['session_id'], session_to_delete_id)

    def test_load_empty_db(self):
        """Test loading from an empty database"""
        sessions = focus_tracker_app.load_sessions()
        self.assertEqual(len(sessions), 0)


# Ensure QApplication exists for tests that might need it
_app_singleton = focus_tracker_app.QApplication.instance()
if _app_singleton is None:
    _app_singleton = focus_tracker_app.QApplication([])

class TestMainWindowLogic(unittest.TestCase):
    """Tests specific logic within the MainWindow class (limited scope)"""

    @patch('app.QMessageBox')
    @patch('app.delete_session')
    @patch.object(focus_tracker_app.MainWindow, 'load_and_display_history') # Mock method directly
    def test_handle_delete_session_confirm_yes(self, mock_load_history, mock_delete, mock_msgbox):
        """Test delete handler when user confirms 'Yes'"""
        # Arrange
        mock_msgbox.question.return_value = focus_tracker_app.QMessageBox.StandardButton.Yes
        if focus_tracker_app.QApplication.instance() is None:
             self.app_instance = focus_tracker_app.QApplication([])
        window = focus_tracker_app.MainWindow() # Create instance
        test_session_id = 123

        mock_load_history.reset_mock()

        # Act
        window.handle_delete_session(test_session_id)

        # Assert
        mock_msgbox.question.assert_called_once()
        mock_delete.assert_called_once_with(test_session_id)
        # Check it was called exactly once after the reset
        mock_load_history.assert_called_once()

    @patch('app.QMessageBox')
    @patch('app.delete_session')
    @patch.object(focus_tracker_app.MainWindow, 'load_and_display_history')
    def test_handle_delete_session_confirm_no(self, mock_load_history, mock_delete, mock_msgbox):
        """Test delete handler when user confirms 'No'"""
        # Arrange
        mock_msgbox.question.return_value = focus_tracker_app.QMessageBox.StandardButton.No
        if focus_tracker_app.QApplication.instance() is None:
             self.app_instance = focus_tracker_app.QApplication([])
        window = focus_tracker_app.MainWindow()
        test_session_id = 456

        mock_load_history.reset_mock()

        # Act
        window.handle_delete_session(test_session_id)

        # Assert
        mock_msgbox.question.assert_called_once()
        mock_delete.assert_not_called() # Ensure delete_session was NOT called
        # Ensure history refresh was NOT called *after* the reset
        mock_load_history.assert_not_called()


if __name__ == '__main__':
    if focus_tracker_app.QApplication.instance() is None:
        _app_singleton_main = focus_tracker_app.QApplication([])
    unittest.main(argv=['first-arg-is-ignored'], exit=False)
