import numpy as np


class _StubScreenshot:
    def __init__(self, w=1280, h=720):
        self.width = w
        self.height = h
        # solid dark gray frame
        self.raw = np.full((h, w, 4), 40, dtype=np.uint8).tobytes()


class mss:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def grab(self, monitor):
        return _StubScreenshot()
