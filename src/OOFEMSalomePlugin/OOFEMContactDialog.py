"""Dialog for OOFEM structural master/slave penalty contact pairs."""

import math

from OOFEMSalomePlugin.OOFEMQt import QtWidgets


def _finite_float(text, label, positive=False, nonnegative=False):
    try:
        value = float(text)
    except (TypeError, ValueError) as error:
        raise ValueError("{} must be numeric.".format(label)) from error
    if not math.isfinite(value):
        raise ValueError("{} must be finite.".format(label))
    if positive and value <= 0.0:
        raise ValueError("{} must be positive.".format(label))
    if nonnegative and value < 0.0:
        raise ValueError("{} must be non-negative.".format(label))
    return value


def _existing_boolean(value, label):
    """Parse persisted boolean spellings without Python string truthiness."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, float) and math.isfinite(value) and value in (0.0, 1.0):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in ("true", "yes", "on", "1"):
            return True
        if normalized in ("false", "no", "off", "0", ""):
            return False
    raise ValueError("{} must be a boolean.".format(label))


def _existing_algorithm(value):
    """Parse persisted integer-like search algorithm values such as ``1.0``."""
    if isinstance(value, bool):
        raise ValueError("Search algorithm must be 0 or 1.")
    try:
        numeric_value = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError("Search algorithm must be 0 or 1.") from error
    if (
        not math.isfinite(numeric_value)
        or not numeric_value.is_integer()
        or int(numeric_value) not in (0, 1)
    ):
        raise ValueError("Search algorithm must be 0 or 1.")
    return int(numeric_value)


class OOFEMContactDialog(QtWidgets.QDialog):
    """Create or edit one structural penalty contact definition."""

    def __init__(
        self,
        boundary_groups,
        time_functions=None,
        existing_contact=None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("OOFEM Structural Contact")
        self.boundary_groups = sorted(set(boundary_groups or []))
        self.time_functions = list(time_functions or [])
        self._existing_contact = existing_contact or {}

        layout = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()

        self.nameEdit = QtWidgets.QLineEdit()
        form.addRow("Name:", self.nameEdit)

        self.masterGroupCombo = QtWidgets.QComboBox()
        for group_name in self.boundary_groups:
            self.masterGroupCombo.addItem(group_name, group_name)
        form.addRow("Master boundary group:", self.masterGroupCombo)

        self.slaveGroupCombo = QtWidgets.QComboBox()
        for group_name in self.boundary_groups:
            self.slaveGroupCombo.addItem(group_name, group_name)
        form.addRow("Slave boundary group:", self.slaveGroupCombo)

        self.normalPenaltyEdit = QtWidgets.QLineEdit("1000000")
        form.addRow("Normal penalty (pn):", self.normalPenaltyEdit)

        self.tangentialPenaltyEdit = QtWidgets.QLineEdit("1000000")
        form.addRow("Tangential penalty (pt):", self.tangentialPenaltyEdit)

        self.frictionEdit = QtWidgets.QLineEdit("0")
        form.addRow("Friction coefficient:", self.frictionEdit)

        self.algorithmCombo = QtWidgets.QComboBox()
        self.algorithmCombo.addItem("Direct surface search", 0)
        self.algorithmCombo.addItem("Sweep and prune (3D)", 1)
        form.addRow("Search algorithm:", self.algorithmCombo)

        self.timeFunctionCombo = QtWidgets.QComboBox()
        for function in self.time_functions:
            label = (
                function.get("name")
                or function.get("oofem_type")
                or function.get("id")
            )
            self.timeFunctionCombo.addItem(str(label), function.get("id"))
        form.addRow("Required time function record:", self.timeFunctionCombo)

        self.twoPassCheck = QtWidgets.QCheckBox(
            "Two-pass (also enforce slave-to-master)"
        )
        form.addRow("", self.twoPassCheck)
        self.reverseMasterCheck = QtWidgets.QCheckBox(
            "Reverse master element orientation"
        )
        form.addRow("", self.reverseMasterCheck)
        self.reverseSlaveCheck = QtWidgets.QCheckBox(
            "Reverse slave element orientation"
        )
        form.addRow("", self.reverseSlaveCheck)
        layout.addLayout(form)

        note = QtWidgets.QLabel(
            "The first integration supports line contact in 2D and triangular/"
            "quadrilateral surface contact in 3D. Start with frictionless "
            "contact (coefficient 0); non-zero friction is available in the "
            "current OOFEM contact implementation. Contact assembly requires "
            "a load-time-function reference but does not use it to ramp or "
            "disable contact."
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._load_existing()

    @staticmethod
    def _select_or_add_missing(combo, value, kind):
        """Select *value*, retaining stale references as visibly missing items."""
        if value is None or str(value).strip() == "":
            return False
        value = str(value)
        index = combo.findData(value)
        if index < 0:
            combo.addItem("[missing {}] {}".format(kind, value), value)
            index = combo.count() - 1
        combo.setCurrentIndex(index)
        return True

    @staticmethod
    def _combo_value(combo):
        value = combo.currentData()
        if value is None:
            value = combo.currentText()
        return str(value).strip()

    def _load_existing(self):
        contact = self._existing_contact
        parameters = contact.get("params") or {}
        self.nameEdit.setText(contact.get("name") or "Contact")
        master_group = contact.get("master_group") or contact.get("master")
        slave_group = contact.get("slave_group") or contact.get("slave")
        if master_group:
            self._select_or_add_missing(
                self.masterGroupCombo, master_group, "boundary group"
            )
        if slave_group:
            self._select_or_add_missing(
                self.slaveGroupCombo, slave_group, "boundary group"
            )
        elif self.slaveGroupCombo.count() > 1:
            self.slaveGroupCombo.setCurrentIndex(1)
        self.normalPenaltyEdit.setText(
            str(parameters.get("normal_penalty", parameters.get("pn", 1.0e6)))
        )
        self.tangentialPenaltyEdit.setText(
            str(
                parameters.get(
                    "tangential_penalty", parameters.get("pt", 1.0e6)
                )
            )
        )
        self.frictionEdit.setText(str(parameters.get("friction", 0.0)))
        algorithm = _existing_algorithm(
            parameters.get("algorithm", parameters.get("algo", 0))
        )
        index = self.algorithmCombo.findData(algorithm)
        if index >= 0:
            self.algorithmCombo.setCurrentIndex(index)
        self.twoPassCheck.setChecked(
            _existing_boolean(parameters.get("two_pass", False), "Two-pass option")
        )
        self.reverseMasterCheck.setChecked(
            _existing_boolean(
                parameters.get("reverse_master", False),
                "Reverse-master option",
            )
        )
        self.reverseSlaveCheck.setChecked(
            _existing_boolean(
                parameters.get("reverse_slave", False),
                "Reverse-slave option",
            )
        )
        function_id = contact.get("time_function_id")
        if function_id is not None:
            self._select_or_add_missing(
                self.timeFunctionCombo, function_id, "time function"
            )

    def get_data(self):
        name = self.nameEdit.text().strip()
        if not name:
            raise ValueError("Contact name must not be empty.")
        master_group = self._combo_value(self.masterGroupCombo)
        slave_group = self._combo_value(self.slaveGroupCombo)
        if not master_group or not slave_group:
            raise ValueError("Both master and slave boundary groups are required.")
        if master_group == slave_group:
            raise ValueError("Master and slave boundary groups must be different.")
        if (
            self.timeFunctionCombo.count()
            and self.timeFunctionCombo.currentData() is None
        ):
            raise ValueError("An activation time function must be selected.")

        data = {
            "name": name,
            "oofem_type": "StructuralPenaltyContactBC",
            "master_group": master_group,
            "slave_group": slave_group,
            "params": {
                "normal_penalty": _finite_float(
                    self.normalPenaltyEdit.text(), "Normal penalty", positive=True
                ),
                "tangential_penalty": _finite_float(
                    self.tangentialPenaltyEdit.text(),
                    "Tangential penalty",
                    positive=True,
                ),
                "friction": _finite_float(
                    self.frictionEdit.text(),
                    "Friction coefficient",
                    nonnegative=True,
                ),
                "algorithm": int(self.algorithmCombo.currentData()),
                "two_pass": self.twoPassCheck.isChecked(),
                "reverse_master": self.reverseMasterCheck.isChecked(),
                "reverse_slave": self.reverseSlaveCheck.isChecked(),
            },
        }
        function_id = self.timeFunctionCombo.currentData()
        if function_id is not None:
            data["time_function_id"] = function_id
        return data

    def accept(self):
        try:
            self.get_data()
        except ValueError as error:
            QtWidgets.QMessageBox.warning(self, "Invalid Contact", str(error))
            return
        super().accept()

    @staticmethod
    def run(
        boundary_groups,
        time_functions=None,
        existing_contact=None,
        parent=None,
    ):
        if not existing_contact and len(set(boundary_groups or [])) < 2:
            QtWidgets.QMessageBox.warning(
                parent,
                "OOFEM Contact",
                "Create at least two SALOME boundary groups for master and "
                "slave surfaces.",
            )
            return None
        try:
            dialog = OOFEMContactDialog(
                boundary_groups,
                time_functions=time_functions,
                existing_contact=existing_contact,
                parent=parent,
            )
        except ValueError as error:
            QtWidgets.QMessageBox.warning(
                parent, "Invalid Contact", str(error)
            )
            return None
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            return dialog.get_data()
        return None
