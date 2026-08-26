"""Qt binding selected by the running SALOME application."""

try:
    import SalomePyQt
except ImportError:
    SalomePyQt = None

if SalomePyQt is not None and SalomePyQt.usePySide():
    from PySide2 import QtCore, QtGui, QtWidgets
else:
    from PyQt5 import QtCore, QtGui, QtWidgets

Qt = QtCore.Qt
