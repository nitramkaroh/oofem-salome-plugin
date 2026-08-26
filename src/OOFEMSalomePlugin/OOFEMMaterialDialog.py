from OOFEMSalomePlugin.OOFEMQt import QtWidgets


class OOFEMMaterialDialog(QtWidgets.QDialog):
    """Create a material and assign it to a structural element group."""

    def __init__(
        self,
        material_templates,
        mesh_groups,
        material_library=None,
        existing_material=None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Material Definition")
        self.material_templates = material_templates
        self.material_library = material_library or []
        self.mesh_groups = (
            mesh_groups.get("elements", [])
            if isinstance(mesh_groups, dict)
            else list(mesh_groups)
        )

        layout = QtWidgets.QVBoxLayout(self)
        form_layout = QtWidgets.QFormLayout()
        layout.addLayout(form_layout)

        self.nameEdit = QtWidgets.QLineEdit()
        self.libraryCombo = QtWidgets.QComboBox()
        self.typeCombo = QtWidgets.QComboBox()
        self.groupCombo = QtWidgets.QComboBox()

        form_layout.addRow("Instance Name:", self.nameEdit)
        form_layout.addRow("Library Preset:", self.libraryCombo)
        form_layout.addRow("OOFEM Material Type:", self.typeCombo)
        form_layout.addRow("Assign to Element Group:", self.groupCombo)

        self.overrideGroup = QtWidgets.QGroupBox("Element Mapping Override")
        self.overrideGroup.setCheckable(True)
        self.overrideGroup.setChecked(False)
        override_layout = QtWidgets.QVBoxLayout(self.overrideGroup)
        self.overrideTable = QtWidgets.QTableWidget()
        self.overrideTable.setColumnCount(2)
        self.overrideTable.setHorizontalHeaderLabels(["SALOME Type", "OOFEM Type"])
        override_layout.addWidget(self.overrideTable)
        layout.addWidget(self.overrideGroup)

        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        self.typeCombo.addItems(
            [template["display_name"] for template in self.material_templates]
        )
        self.libraryCombo.addItem("<Template defaults>", None)
        for entry in self.material_library:
            self.libraryCombo.addItem(entry["display_name"], entry.get("id"))
        self.groupCombo.addItems(["<None>"] + self.mesh_groups)
        self.libraryCombo.currentIndexChanged.connect(self._on_library_changed)

        if existing_material:
            self.nameEdit.setText(existing_material.get("name", ""))
            oofem_type = existing_material.get("oofem_type")
            type_index = next(
                (
                    index
                    for index, template in enumerate(self.material_templates)
                    if template["oofem_name"] == oofem_type
                ),
                -1,
            )
            if type_index >= 0:
                self.typeCombo.setCurrentIndex(type_index)

            group_index = self.groupCombo.findText(
                existing_material.get("assigned_group") or "<None>"
            )
            if group_index >= 0:
                self.groupCombo.setCurrentIndex(group_index)

            override_map = existing_material.get("element_mapping_override")
            if override_map:
                self.overrideGroup.setChecked(True)
                for salome_type, oofem_type in override_map.items():
                    row = self.overrideTable.rowCount()
                    self.overrideTable.insertRow(row)
                    self.overrideTable.setItem(
                        row, 0, QtWidgets.QTableWidgetItem(salome_type)
                    )
                    self.overrideTable.setItem(
                        row, 1, QtWidgets.QTableWidgetItem(oofem_type)
                    )

    def _selected_library_entry(self):
        library_id = self.libraryCombo.currentData()
        return next(
            (entry for entry in self.material_library if entry.get("id") == library_id),
            None,
        )

    def _on_library_changed(self, index):
        entry = self._selected_library_entry()
        if entry is None:
            return
        compatible = entry.get("compatible_templates", [])
        current_type = (
            self.material_templates[self.typeCombo.currentIndex()]["oofem_name"]
            if 0 <= self.typeCombo.currentIndex() < len(self.material_templates)
            else None
        )
        if current_type not in compatible:
            # A preset usable by more than one OOFEM material type (e.g. an
            # isotropic elastic material is the same physical material
            # whether it backs a 2D or a 3D element) should not force a
            # specific one if the current selection already applies;
            # otherwise fall back to the first type it supports.
            type_index = next(
                (
                    index
                    for index, template in enumerate(self.material_templates)
                    if template["oofem_name"] in compatible
                ),
                -1,
            )
            if type_index >= 0:
                self.typeCombo.setCurrentIndex(type_index)
        if not self.nameEdit.text().strip():
            self.nameEdit.setText(entry.get("display_name", ""))

    def get_data(self):
        selected_template = self.material_templates[self.typeCombo.currentIndex()]
        parameters = {
            parameter["key"]: parameter["default"]
            for parameter in selected_template.get("params", [])
            if "default" in parameter
        }
        library_entry = self._selected_library_entry()
        if library_entry and selected_template["oofem_name"] in library_entry.get(
            "compatible_templates", []
        ):
            parameters.update(library_entry.get("params", {}))

        override_map = None
        if self.overrideGroup.isChecked():
            override_map = {}
            for row in range(self.overrideTable.rowCount()):
                salome_item = self.overrideTable.item(row, 0)
                oofem_item = self.overrideTable.item(row, 1)
                salome_type = salome_item.text().strip() if salome_item else ""
                oofem_type = oofem_item.text().strip() if oofem_item else ""
                if salome_type and oofem_type:
                    override_map[salome_type] = oofem_type

        group_name = self.groupCombo.currentText()
        return {
            "name": self.nameEdit.text().strip(),
            "oofem_type": selected_template["oofem_name"],
            "assigned_group": group_name if group_name != "<None>" else None,
            "element_mapping_override": override_map,
            "params": parameters,
            "library_id": library_entry.get("id") if library_entry else None,
        }

    @staticmethod
    def run(
        material_templates,
        mesh_groups,
        material_library=None,
        existing_material=None,
        parent=None,
    ):
        dialog = OOFEMMaterialDialog(
            material_templates,
            mesh_groups,
            material_library,
            existing_material,
            parent,
        )
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            return dialog.get_data()
        return None
