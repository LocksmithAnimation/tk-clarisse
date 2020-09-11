from functools import partial

import sgtk
from sgtk.platform.qt import QtGui, QtCore
from sgtk.platform import restart


class ProxyWidget(QtGui.QWidget):
    do_restart = QtCore.Signal()

    def __init__(self, parent=None, f=QtCore.Qt.WindowFlags()):
        super(ProxyWidget, self).__init__(parent, f)
        self.do_restart.connect(restart, QtCore.Qt.QueuedConnection)

    def restart_engine(self):
        self.do_restart.emit()
