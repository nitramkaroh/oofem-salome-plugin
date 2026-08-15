from OOFEMSalomePlugin.OOFEMQt import Qt, QtWidgets
from OOFEMSalomePlugin.OOFEMMainWidget import OOFEMMainWidget


class OOFEMDockWidget(QtWidgets.QDockWidget):
    OBJECT_NAME = "OOFEMSalomePluginDock"

    def __init__(self, parent=None):
        super().__init__("OOFEM", parent)
        self.setObjectName(self.OBJECT_NAME)
        self.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)

        self.mainWidget = OOFEMMainWidget(self)
        self.setWidget(self.mainWidget)
