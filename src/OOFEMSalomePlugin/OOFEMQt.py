"""Qt binding selected by the running SALOME application."""

try:
    import SalomePyQt
except ImportError:
    SalomePyQt = None

# Safely check if usePySide exists before calling it
if SalomePyQt is not None and hasattr(SalomePyQt, 'usePySide') and SalomePyQt.usePySide():
    from PySide2 import QtCore, QtGui, QtWidgets
else:
    from PyQt5 import QtCore, QtGui, QtWidgets

Qt = QtCore.Qt