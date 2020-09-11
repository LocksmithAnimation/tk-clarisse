import ix

from sgtk.platform.qt import QtGui, QtCore


def exec_(application):
    PySideAppClarisseHelper.exec_(application)


def are_windows_visible():
    # return if any top window is visible
    return any(w.isVisible() for w in QtGui.QApplication.topLevelWidgets())


class PySideAppClarisseHelper(object):
    _helper = None
    
    @classmethod
    def exec_(cls, app):
        if not cls._helper:
            cls._helper = cls(app)
            ix.application.add_to_event_loop_single(cls._helper.process_events)
        return cls._helper
    
    def __init__(self, app):
        self.app = app
        self.event_loop = QtCore.QEventLoop()

    def process_events(self):
        if are_windows_visible():
            # call Qt main loop
            self.event_loop.processEvents()
            # flush stacked events
            self.app.sendPostedEvents(None, 0)
            # add the callback to Clarisse main loop
        if self.app and not self.app.closingDown():
            ix.application.add_to_event_loop_single(self.process_events)
        else:
            self._helper = None