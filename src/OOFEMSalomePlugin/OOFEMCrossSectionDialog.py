"""Dialog for creating and editing OOFEM cross sections."""

from OOFEMSalomePlugin.OOFEMQt import Qt, QtWidgets


def _format_parameter(value, parameter_type):
    if value is None:
        return ""
    if parameter_type == "float_list" and isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value)
    return str(value)


def _coerce_parameter(text, parameter_type):
    if parameter_type == "float":
        return float(text)
    if parameter_type == "int":
        return int(text)
    if parameter_type == "string":
        return str(text)
    if parameter_type == "float_list":
        parts = [part.strip() for part in text.split(",")]
        if not parts or any(not part for part in parts):
            raise ValueError("expected comma-separated numbers")
        return [float(part) for part in parts]
    raise ValueError("unsupported parameter type '{}'".format(parameter_type))


class OOFEMCrossSectionDialog(QtWidgets.QDialog):
    """Create or edit a cross section and its mesh/material references."""

    NLGEO_MODES = {"inherit", "on", "off"}

    def __init__(
        self,
        cs_templates,
        material_map,
        mesh_groups,
        salome_element_types,
        existing_cs=None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Cross Section Definition")

        self.cs_templates = list(cs_templates or [])
        self.material_map = list(material_map or [])
        self.mesh_groups = list(mesh_groups or [])
        self.salome_element_types = sorted(
            {
                str(element_type).strip()
                for element_type in (salome_element_types or [])
                if str(element_type).strip()
            }
        )
        self._existing_cs = existing_cs or None

        layout = QtWidgets.QVBoxLayout(self)
        form_layout = QtWidgets.QFormLayout()
        layout.addLayout(form_layout)

        self.nameEdit = QtWidgets.QLineEdit()
        self.typeCombo = QtWidgets.QComboBox()
        self.materialCombo = QtWidgets.QComboBox()
        self.groupCombo = QtWidgets.QComboBox()
        self.nlgeoCombo = QtWidgets.QComboBox()
        self.nlgeoCombo.addItem("Inherit solver preset", "inherit")
        self.nlgeoCombo.addItem("Enabled (write nlgeo 1)", "on")
        self.nlgeoCombo.addItem("Disabled", "off")
        self.nlgeoCombo.setToolTip(
            "Controls the nlgeo field on continuum element records in the "
            "assigned mesh group. This option is independent of contact."
        )

        form_layout.addRow("Instance Name:", self.nameEdit)
        form_layout.addRow("Cross Section Type:", self.typeCombo)
        form_layout.addRow("Use Material:", self.materialCombo)
        form_layout.addRow("Assign to Mesh Group:", self.groupCombo)
        form_layout.addRow("Element nlgeo:", self.nlgeoCombo)

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

        self.overrideGroup = QtWidgets.QGroupBox("Element Mapping Override")
        self.overrideGroup.setCheckable(True)
        self.overrideGroup.setChecked(False)
        override_layout = QtWidgets.QVBoxLayout(self.overrideGroup)

        self.overrideTable = QtWidgets.QTableWidget()
        self.overrideTable.setColumnCount(2)
        self.overrideTable.setHorizontalHeaderLabels(["SALOME Type", "OOFEM Type"])
        self.overrideTable.horizontalHeader().setSectionResizeMode(
            0, QtWidgets.QHeaderView.Stretch
        )
        self.overrideTable.horizontalHeader().setSectionResizeMode(
            1, QtWidgets.QHeaderView.Stretch
        )
        override_layout.addWidget(self.overrideTable)
        layout.addWidget(self.overrideGroup)

        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        self.typeCombo.addItems(
            [
                template.get("display_name", template.get("oofem_name", ""))
                for template in self.cs_templates
            ]
        )
        for material in self.material_map:
            self.materialCombo.addItem(
                material.get("name", "Unnamed"), material.get("id")
            )
        self.groupCombo.addItems(self.mesh_groups)

        existing_overrides = (
            dict(existing_cs.get("element_mapping_override") or {})
            if existing_cs
            else {}
        )
        override_types = sorted(
            set(self.salome_element_types).union(existing_overrides)
        )
        for salome_type in override_types:
            row = self.overrideTable.rowCount()
            self.overrideTable.insertRow(row)
            name_item = QtWidgets.QTableWidgetItem(salome_type)
            name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
            self.overrideTable.setItem(row, 0, name_item)
            self.overrideTable.setItem(
                row,
                1,
                QtWidgets.QTableWidgetItem(existing_overrides.get(salome_type, "")),
            )

        if existing_cs:
            self.nameEdit.setText(existing_cs.get("name", ""))
            type_index = self._template_index(existing_cs.get("oofem_type"))
            if type_index >= 0:
                self.typeCombo.setCurrentIndex(type_index)
            else:
                self.typeCombo.setCurrentIndex(-1)

            material_index = self.materialCombo.findData(
                existing_cs.get("material_id")
            )
            if material_index >= 0:
                self.materialCombo.setCurrentIndex(material_index)
            else:
                self.materialCombo.setCurrentIndex(-1)

            group_index = self.groupCombo.findText(
                existing_cs.get("assigned_group") or ""
            )
            if group_index >= 0:
                self.groupCombo.setCurrentIndex(group_index)
            else:
                self.groupCombo.setCurrentIndex(-1)

            if existing_overrides:
                self.overrideGroup.setChecked(True)

            raw_element_options = existing_cs.get("element_options")
            if raw_element_options is None:
                raw_element_options = {}
            if isinstance(raw_element_options, dict):
                raw_nlgeo_mode = raw_element_options.get(
                    "nlgeo", "inherit"
                )
            else:
                raw_nlgeo_mode = "invalid element_options"
            nlgeo_mode = str(raw_nlgeo_mode).strip().casefold()
            nlgeo_index = self.nlgeoCombo.findData(nlgeo_mode)
            if nlgeo_index < 0:
                self.nlgeoCombo.addItem(
                    "[invalid] {}".format(raw_nlgeo_mode), nlgeo_mode
                )
                nlgeo_index = self.nlgeoCombo.count() - 1
            self.nlgeoCombo.setCurrentIndex(nlgeo_index)

        self.typeCombo.currentIndexChanged.connect(self._populate_parameters)
        self._populate_parameters()

    def _template_index(self, oofem_type):
        expected = str(oofem_type or "").lower()
        return next(
            (
                index
                for index, template in enumerate(self.cs_templates)
                if str(template.get("oofem_name") or "").lower() == expected
            ),
            -1,
        )

    def _selected_template(self):
        index = self.typeCombo.currentIndex()
        if 0 <= index < len(self.cs_templates):
            return self.cs_templates[index]
        return None

    def _populate_parameters(self, index=None):
        del index
        self.parameterTable.setRowCount(0)
        template = self._selected_template()
        if template is None:
            return

        existing_params = None
        if (
            self._existing_cs
            and str(self._existing_cs.get("oofem_type") or "").lower()
            == str(template.get("oofem_name") or "").lower()
        ):
            existing_params = self._existing_cs.get("params") or {}

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

    def _override_mapping(self):
        if not self.overrideGroup.isChecked():
            return None
        overrides = {}
        for row in range(self.overrideTable.rowCount()):
            salome_item = self.overrideTable.item(row, 0)
            oofem_item = self.overrideTable.item(row, 1)
            salome_type = salome_item.text().strip() if salome_item else ""
            oofem_type = oofem_item.text().strip() if oofem_item else ""
            if salome_type and oofem_type:
                overrides[salome_type] = oofem_type
        return overrides

    def _validated_data(self):
        name = self.nameEdit.text().strip()
        if not name:
            raise ValueError("Instance name must not be empty.")

        template = self._selected_template()
        if template is None:
            raise ValueError("A cross section type must be selected.")

        if (
            self.materialCombo.currentIndex() < 0
            or self.materialCombo.currentData() is None
        ):
            raise ValueError("A material reference must be selected.")

        group_name = self.groupCombo.currentText().strip()
        if self.groupCombo.currentIndex() < 0 or not group_name:
            raise ValueError("A mesh group reference must be selected.")

        nlgeo_mode = str(
            self.nlgeoCombo.currentData() or ""
        ).strip().casefold()
        if nlgeo_mode not in self.NLGEO_MODES:
            raise ValueError(
                "Element nlgeo must be Inherit, Enabled, or Disabled."
            )
        existing_options = (
            self._existing_cs.get("element_options")
            if self._existing_cs
            else None
        )
        element_options = (
            dict(existing_options)
            if isinstance(existing_options, dict)
            else {}
        )
        element_options["nlgeo"] = nlgeo_mode

        return {
            "name": name,
            "oofem_type": template["oofem_name"],
            "material_id": self.materialCombo.currentData(),
            "assigned_group": group_name,
            "element_options": element_options,
            "element_mapping_override": self._override_mapping(),
            "params": self._parameters(),
        }

    def accept(self):
        try:
            self._validated_data()
        except ValueError as error:
            QtWidgets.QMessageBox.warning(
                self, "Invalid Cross Section", str(error)
            )
            return
        super().accept()

    def get_data(self):
        """Return the validated cross-section data."""
        return self._validated_data()

    @staticmethod
    def run(
        cs_templates,
        material_map,
        mesh_groups,
        salome_element_types,
        existing_cs=None,
        parent=None,
    ):
        """Create, execute, and return data from the dialog."""
        if not cs_templates:
            QtWidgets.QMessageBox.warning(
                parent,
                "No Cross Section Types",
                "No cross section templates are available.",
            )
            return None
        if not material_map:
            QtWidgets.QMessageBox.warning(
                parent,
                "No Materials Defined",
                "You must define at least one material before creating a cross section.",
            )
            return None
        if not mesh_groups:
            QtWidgets.QMessageBox.warning(
                parent,
                "No Mesh Groups",
                "The selected mesh has no groups to which a cross section can be assigned.",
            )
            return None

        dialog = OOFEMCrossSectionDialog(
            cs_templates,
            material_map,
            mesh_groups,
            salome_element_types,
            existing_cs,
            parent,
        )
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            return dialog.get_data()
        return None
