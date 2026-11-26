"""Run inside the media image: python -m unittest discover -s /tests."""
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

import cv2
import numpy as np
from fastapi import HTTPException
from media_worker.sessions import Capture, OpenRequest


class FakeCapture:
    def __init__(self):
        self.index = 0
        self.released = False

    def open(self, source, *args):
        self.source = source
        return True

    def isOpened(self):
        return True

    def get(self, key):
        return 10 if key == cv2.CAP_PROP_FPS else 4

    def read(self):
        if self.index >= 4:
            return False, None
        self.index += 1
        return True, np.zeros((16, 16, 3), dtype=np.uint8)

    def release(self):
        self.released = True


class CaptureTests(unittest.TestCase):
    def make_capture(self, interval=0, live=False):
        with patch('media_worker.sessions.cv2.VideoCapture', FakeCapture), patch('media_worker.main.resolve_source', return_value=0):
            capture = Capture(uuid4(), OpenRequest(uri='camera:0', interval_seconds=interval, live=live))
        self.addCleanup(capture.close)
        # Metadata tests do not create frame files.
        capture.write = lambda index, timestamp, frame: {'index': index, 'timestamp': timestamp}
        return capture

    def test_full_frames_and_sampled_source_timestamps(self):
        full = self.make_capture()
        self.assertEqual([full.next()['index'] for _ in range(4)], [0, 1, 2, 3])
        self.assertTrue(full.next()['eof'])
        sampled = self.make_capture(interval=.2)
        self.assertEqual(sampled.next(), {'index': 0, 'timestamp': 0})
        self.assertEqual(sampled.next(), {'index': 2, 'timestamp': 200})
        self.assertTrue(sampled.next()['eof'])

    def test_live_latest_frame_and_disconnect(self):
        capture = self.make_capture(live=True)
        deadline = time.monotonic() + 2
        while not capture.failed and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(capture.next()['index'], 3)
        self.assertEqual(capture.dropped, 3)
        with self.assertRaises(HTTPException):
            capture.next()
        capture.close()
        self.assertTrue(capture.stop.is_set())
        self.assertTrue(capture.cap.released)


if __name__ == '__main__':
    unittest.main()
