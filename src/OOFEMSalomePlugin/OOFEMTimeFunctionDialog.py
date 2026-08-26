"""Dialog for creating and editing OOFEM time functions."""

from OOFEMSalomePlugin.OOFEMParameterCoercion import (
    coerce_parameter_value as _coerce_parameter,
    format_parameter_value as _format_parameter,
)
from OOFEMSalomePlugin.OOFEMQt import Qt, QtWidgets


class OOFEMTimeFunctionDialog(QtWidgets.QDialog):
    """Create or edit a time function from a template."""

    def __init__(self, tf_templates, existing_tf=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Time Function Definition")

        self.tf_templates = list(tf_templates or [])
        self._existing_tf = existing_tf or None

        layout = QtWidgets.QVBoxLayout(self)
        form_layout = QtWidgets.QFormLayout()
        layout.addLayout(form_layout)

        self.nameEdit = QtWidgets.QLineEdit()
        self.typeCombo = QtWidgets.QComboBox()

        form_layout.addRow("Instance Name:", self.nameEdit)
        form_layout.addRow("Time Function Type:", self.typeCombo)

        self.parameterTable = QtWidgets.QTableWidget()
        self.parameterTable.setColumnCount(2)
        self.parameterTable.setHorizontalHeaderLabels(["Parameter", "Value"])
        self.parameterTable.horizontalHeader().setSectionResizeMode(
            0, QtWidgets.QHeaderView.Stretch
        )
        self.parameterTable.horizontalHeader().setSectionResizeMode(
            1, QtWidgets.QHeaderView.Stretch
        )
        layout.addWidget(self.parameterTable)

        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        self.typeCombo.addItems(
            [
                template.get("display_name", template.get("oofem_name", ""))
                for template in self.tf_templates
            ]
        )

        if existing_tf:
            self.nameEdit.setText(existing_tf.get("name", ""))
            type_index = self._template_index(existing_tf.get("oofem_type"))
            if type_index >= 0:
                self.typeCombo.setCurrentIndex(type_index)
            else:
                self.typeCombo.setCurrentIndex(-1)

        self.typeCombo.currentIndexChanged.connect(self._populate_parameters)
        self._populate_parameters()

    def _template_index(self, oofem_type):
        expected = str(oofem_type or "").lower()
        return next(
            (
                index
                for index, template in enumerate(self.tf_templates)
                if str(template.get("oofem_name") or "").lower() == expected
            ),
            -1,
        )

    def _selected_template(self):
        index = self.typeCombo.currentIndex()
        if 0 <= index < len(self.tf_templates):
            return self.tf_templates[index]
        return None

    def _populate_parameters(self, index=None):
        del index
        self.parameterTable.setRowCount(0)
        template = self._selected_template()
        if template is None:
            return

        existing_params = None
        if (
            self._existing_tf
            and str(self._existing_tf.get("oofem_type") or "").lower()
            == str(template.get("oofem_name") or "").lower()
        ):
            existing_params = self._existing_tf.get("params") or {}

        for parameter in template.get("params", []):
            row = self.parameterTable.rowCount()
            self.parameterTable.insertRow(row)

            optional = bool(parameter.get("optional", False))
            label = parameter.get("name", parameter.get("key", ""))
            if optional:
                label += " (optional)"
            name_item = QtWidgets.QTableWidgetItem(label)
            name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
            name_item.setData(Qt.UserRole, parameter.get("key"))
            name_item.setData(Qt.UserRole + 1, parameter.get("type", "float"))
            name_item.setData(Qt.UserRole + 2, optional)

            if existing_params is not None and parameter.get("key") in existing_params:
                value = existing_params[parameter["key"]]
            elif existing_params is not None and optional:
                value = ""
            else:
                value = parameter.get("default", "")
            value_item = QtWidgets.QTableWidgetItem(
                _format_parameter(value, parameter.get("type", "float"))
            )

            description = parameter.get("description")
            if description:
                name_item.setToolTip(description)
                value_item.setToolTip(description)

            self.parameterTable.setItem(row, 0, name_item)
            self.parameterTable.setItem(row, 1, value_item)

    def _parameters(self):
        parameters = {}
        for row in range(self.parameterTable.rowCount()):
            name_item = self.parameterTable.item(row, 0)
            value_item = self.parameterTable.item(row, 1)
            key = name_item.data(Qt.UserRole)
            parameter_type = name_item.data(Qt.UserRole + 1) or "float"
            optional = bool(name_item.data(Qt.UserRole + 2))
            value_text = value_item.text().strip() if value_item else ""

            if not value_text:
                if optional:
                    continue
                raise ValueError("Parameter '{}' is required.".format(key))
            try:
                parameters[key] = _coerce_parameter(value_text, parameter_type)
            except (TypeError, ValueError) as error:
                raise ValueError(
                    "Parameter '{}' has an invalid {} value: {}".format(
                        key, parameter_type, error
                    )
                ) from error
        return parameters

    @staticmethod
    def _validate_piecewise(template, parameters):
        if "piecewise" not in template.get("oofem_name", "").lower():
            return

        times = parameters.get("t")
        values = parameters.get("f(t)")
        if not isinstance(times, list) or not isinstance(values, list):
            raise ValueError(
                "A piecewise function requires comma-separated t and f(t) values."
            )
        if len(times) != len(values):
            raise ValueError(
                "Piecewise t and f(t) lists must have the same length."
            )
        if len(times) < 2:
            raise ValueError("A piecewise function requires at least two points.")
        if not all(left < right for left, right in zip(times, times[1:])):
            raise ValueError("Piecewise time coordinates must be strictly increasing.")

    def _validated_data(self):
        name = self.nameEdit.text().strip()
        if not name:
            raise ValueError("Instance name must not be empty.")

        template = self._selected_template()
        if template is None:
            raise ValueError("A time function type must be selected.")

        parameters = self._parameters()
        self._validate_piecewise(template, parameters)
        return {
            "name": name,
            "oofem_type": template["oofem_name"],
            "params": parameters,
        }

    def accept(self):
        try:
            self._validated_data()
        except ValueError as error:
            QtWidgets.QMessageBox.warning(
                self, "Invalid Time Function", str(error)
            )
            return
        super().accept()

    def get_data(self):
        """Return the validated time-function data."""
        return self._validated_data()

    @staticmethod
    def run(tf_templates, existing_tf=None, parent=None):
        """Create, execute, and return data from the dialog."""
        if not tf_templates:
            QtWidgets.QMessageBox.warning(
                parent,
                "No Time Function Types",
                "No time function templates are available.",
            )
            return None
        dialog = OOFEMTimeFunctionDialog(tf_templates, existing_tf, parent)
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            return dialog.get_data()
        return None
