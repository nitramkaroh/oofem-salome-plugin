from OOFEMSalomePlugin.OOFEMQt import QtWidgets


class OOFEMBCDialog(QtWidgets.QDialog):
    """Create a BC and offer only compatible node or boundary groups."""

    def __init__(self, bc_templates, mesh_groups, existing_bc=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Boundary Condition Definition")
        self.bc_templates = bc_templates
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
        form_layout.addRow("Instance Name:", self.nameEdit)
        form_layout.addRow("OOFEM BC Type:", self.typeCombo)
        form_layout.addRow("Assign to Compatible Group:", self.groupCombo)

        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        self.typeCombo.addItems(
            [template["display_name"] for template in self.bc_templates]
        )
        self.typeCombo.currentIndexChanged.connect(self._populate_groups)

        selected_group = None
        if existing_bc:
            self.nameEdit.setText(existing_bc.get("name", ""))
            selected_group = existing_bc.get("assigned_group")
            oofem_type = existing_bc.get("oofem_type")
            type_index = next(
                (
                    index
                    for index, template in enumerate(self.bc_templates)
                    if template["oofem_name"] == oofem_type
                ),
                -1,
            )
            if type_index >= 0:
                self.typeCombo.setCurrentIndex(type_index)

        self._populate_groups(selected=selected_group)

    def _target_groups(self):
        if not self.bc_templates:
            return []
        template = self.bc_templates[self.typeCombo.currentIndex()]
        if template.get("apply_to") == "nodes":
            return self.mesh_groups.get("nodes", [])
        if template.get("apply_to") == "element_boundary":
            return self.mesh_groups.get("boundaries", [])
        return self.mesh_groups.get("elements", [])

    def _populate_groups(self, index=None, selected=None):
        if selected is None:
            selected = self.groupCombo.currentText()
        self.groupCombo.clear()
        self.groupCombo.addItems(["<None>"] + list(self._target_groups()))
        selected_index = self.groupCombo.findText(selected)
        if selected_index >= 0:
            self.groupCombo.setCurrentIndex(selected_index)

    def get_data(self):
        selected_template = self.bc_templates[self.typeCombo.currentIndex()]
        group_name = self.groupCombo.currentText()
        return {
            "name": self.nameEdit.text().strip(),
            "oofem_type": selected_template["oofem_name"],
            "assigned_group": group_name if group_name != "<None>" else None,
        }

    @staticmethod
    def run(bc_templates, mesh_groups, existing_bc=None, parent=None):
        dialog = OOFEMBCDialog(bc_templates, mesh_groups, existing_bc, parent)
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            return dialog.get_data()
        return None
