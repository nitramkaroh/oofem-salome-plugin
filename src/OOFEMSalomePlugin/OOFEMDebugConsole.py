# src/OOFEMSalomePlugin/OOFEMDebugConsole.py
from OOFEMSalomePlugin.OOFEMQt import QtWidgets


class DebugConsole(QtWidgets.QDockWidget):
    OBJECT_NAME = "OOFEMSalomePluginLog"

    def __init__(self, parent=None):
        super().__init__("OOFEM Log", parent)
        self.setObjectName(self.OBJECT_NAME)

        self.text = QtWidgets.QTextEdit()
        self.text.setReadOnly(True)
        self.setWidget(self.text)

    def log(self, msg):
        self.text.append(str(msg))
        QtWidgets.QApplication.processEvents()  # force immediate refresh