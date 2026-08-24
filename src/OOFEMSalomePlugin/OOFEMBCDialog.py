"""Dialog for OOFEM loads and prescribed boundary conditions."""

from OOFEMSalomePlugin.OOFEMQt import Qt, QtWidgets


def _format_value(value, parameter_type):
    if value is None:
        return ""
    if parameter_type in ("int_list", "float_list") and isinstance(
        value, (list, tuple)
    ):
        return ", ".join(str(item) for item in value)
    return str(value)


def _coerce_value(text, parameter_type):
    if parameter_type == "int":
        return int(text)
    if parameter_type == "float":
        return float(text)
    if parameter_type == "string":
        return str(text)
    if parameter_type in ("int_list", "float_list"):
        parts = [part.strip() for part in text.split(",")]
        if not parts or any(not part for part in parts):
            raise ValueError("expected comma-separated numbers")
        converter = int if parameter_type == "int_list" else float
        return [converter(part) for part in parts]
    raise ValueError("unsupported parameter type '{}'".format(parameter_type))


class OOFEMBCDialog(QtWidgets.QDialog):
    """Create/edit a BC with compatible groups, components, and load history."""

    def __init__(
        self,
        bc_templates,
        mesh_groups,
        existing_bc=None,
        parent=None,
        time_functions=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Boundary Condition Definition")
        self.bc_templates = list(bc_templates or [])
        self.time_functions = list(time_functions or [])
        self._existing_bc = existing_bc or None
        if isinstance(mesh_groups, dict):
            self.mesh_groups = mesh_groups
        else:
            groups = list(mesh_groups)
            self.mesh_groups = {
                "nodes": groups,
                "boundaries": groups,
                "elements": groups,
            }

        layout = QtWidgets.QVBoxLayout(self)
        form_layout = QtWidgets.QFormLayout()
        layout.addLayout(form_layout)

        self.nameEdit = QtWidgets.QLineEdit()
        self.typeCombo = QtWidgets.QComboBox()
        self.groupCombo = QtWidgets.QComboBox()
        self.timeFunctionCombo = QtWidgets.QComboBox()
        form_layout.addRow("Instance Name:", self.nameEdit)
        form_layout.addRow("OOFEM BC Type:", self.typeCombo)
        form_layout.addRow("Assign to Compatible Group:", self.groupCombo)
        form_layout.addRow("Time Function:", self.timeFunctionCombo)

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
                for template in self.bc_templates
            ]
        )
        for index, function in enumerate(self.time_functions, start=1):
            self.timeFunctionCombo.addItem(
                function.get("name") or "Time function {}".format(index),
                function.get("id"),
            )
        self.typeCombo.currentIndexChanged.connect(self._populate_groups)
        self.typeCombo.currentIndexChanged.connect(self._populate_parameters)

        selected_group = None
        if existing_bc:
            self.nameEdit.setText(existing_bc.get("name", ""))
            selected_group = existing_bc.get("assigned_group")
            type_index = self._template_index(existing_bc.get("oofem_type"))
            if type_index >= 0:
                self.typeCombo.setCurrentIndex(type_index)
            else:
                self.typeCombo.setCurrentIndex(-1)

            function_index = self.timeFunctionCombo.findData(
                existing_bc.get("time_function_id")
            )
            if function_index >= 0:
                self.timeFunctionCombo.setCurrentIndex(function_index)

        self._populate_groups(selected=selected_group)
        self._populate_parameters()

    def _template_index(self, oofem_type):
        expected = str(oofem_type or "").lower()
        return next(
            (
                index
                for index, template in enumerate(self.bc_templates)
                if str(template.get("oofem_name") or "").lower() == expected
            ),
            -1,
        )

    def _selected_template(self):
        index = self.typeCombo.currentIndex()
        if 0 <= index < len(self.bc_templates):
            return self.bc_templates[index]
        return None

    def _target_groups(self):
        template = self._selected_template()
        if template is None:
            return []
        if template.get("apply_to") == "nodes":
            return self.mesh_groups.get("nodes", [])
        if template.get("apply_to") == "element_boundary":
            return self.mesh_groups.get("boundaries", [])
        return self.mesh_groups.get("elements", [])

    def _populate_groups(self, index=None, selected=None):
        del index
        if selected is None:
            selected = self.groupCombo.currentText()
        self.groupCombo.clear()
        self.groupCombo.addItems(["<None>"] + list(self._target_groups()))
        selected_index = self.groupCombo.findText(selected)
        if selected_index >= 0:
            self.groupCombo.setCurrentIndex(selected_index)

    def _existing_parameter(self, key):
        if not self._existing_bc:
            return None, False
        params = self._existing_bc.get("params") or {}
        if key in params:
            return params[key], True
        if key == "dofs" and "dof" in params:
            return [params["dof"]], True
        if key in ("values", "components") and "val" in params:
            return [params["val"]], True
        return None, False

    def _populate_parameters(self, index=None):
        del index
        self.parameterTable.setRowCount(0)
        template = self._selected_template()
        if template is None:
            return
        existing_matches = (
            self._existing_bc
            and str(self._existing_bc.get("oofem_type") or "").lower()
            == str(template.get("oofem_name") or "").lower()
        )
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
            value, found = self._existing_parameter(parameter.get("key"))
            if not existing_matches or not found:
                value = parameter.get("default", "")
            value_item = QtWidgets.QTableWidgetItem(
                _format_value(value, parameter.get("type", "float"))
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
                parameters[key] = _coerce_value(value_text, parameter_type)
            except (TypeError, ValueError) as error:
                raise ValueError(
                    "Parameter '{}' has an invalid {} value: {}".format(
                        key, parameter_type, error
                    )
                ) from error

        dofs = parameters.get("dofs")
        values = parameters.get("values", parameters.get("components"))
        if dofs is not None:
            if not dofs or any(dof < 1 for dof in dofs):
                raise ValueError("DOFs must be positive integers.")
            if len(set(dofs)) != len(dofs):
                raise ValueError("DOFs must not contain duplicates.")
            if not isinstance(values, list) or len(values) != len(dofs):
                raise ValueError(
                    "DOFs and values/components must have equal lengths."
                )
        return parameters

    def _validated_data(self):
        name = self.nameEdit.text().strip()
        if not name:
            raise ValueError("Instance name must not be empty.")
        template = self._selected_template()
        if template is None:
            raise ValueError("A boundary-condition type must be selected.")
        group_name = self.groupCombo.currentText().strip()
        if (
            self.groupCombo.currentIndex() < 0
            or not group_name
            or group_name == "<None>"
        ):
            raise ValueError("A compatible mesh group must be selected.")
        function_id = self.timeFunctionCombo.currentData()
        if self.time_functions and function_id is None:
            raise ValueError("A time function must be selected.")

        data = {
            "name": name,
            "oofem_type": template["oofem_name"],
            "assigned_group": group_name,
            "params": self._parameters(),
        }
        if function_id is not None:
            data["time_function_id"] = function_id
        return data

    def accept(self):
        try:
            self._validated_data()
        except ValueError as error:
            QtWidgets.QMessageBox.warning(
                self, "Invalid Boundary Condition", str(error)
            )
            return
        super().accept()

    def get_data(self):
        return self._validated_data()

    @staticmethod
    def run(
        bc_templates,
        mesh_groups,
        existing_bc=None,
        parent=None,
        time_functions=None,
    ):
        if not bc_templates:
            QtWidgets.QMessageBox.warning(
                parent, "No Boundary Conditions", "No BC templates are available."
            )
            return None
        dialog = OOFEMBCDialog(
            bc_templates,
            mesh_groups,
            existing_bc,
            parent,
            time_functions=time_functions,
        )
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            return dialog.get_data()
        return None
