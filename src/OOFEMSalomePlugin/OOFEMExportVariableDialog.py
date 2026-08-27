"""Dialog for adding and editing OOFEM exported variables for VTK XML output."""

from OOFEMSalomePlugin.OOFEMExportCatalog import (
    get_primary_variables,
    get_internal_variables,
    get_variable_categories,
    describe_variable,
)
from OOFEMSalomePlugin.OOFEMQt import Qt, QtWidgets


class OOFEMExportVariableDialog(QtWidgets.QDialog):
    """Create or edit an export variable for the vtkxml export module."""

    CUSTOM_ID_TEXT = "Custom ID..."

    def __init__(self, existing_variable=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export Variable Definition")
        self.resize(480, 260)

        self._existing_variable = existing_variable or None
        self._categories = get_variable_categories()
        self._primary_vars = get_primary_variables()
        self._internal_vars = get_internal_variables()

        layout = QtWidgets.QVBoxLayout(self)
        form_layout = QtWidgets.QFormLayout()
        layout.addLayout(form_layout)

        # 1. Category selector
        self.categoryCombo = QtWidgets.QComboBox()
        self._category_keys = ["primvars", "vars", "cellvars", "ipvars"]
        for key in self._category_keys:
            cat_info = self._categories.get(key, {})
            label = cat_info.get("display_name", key)
            self.categoryCombo.addItem(label, key)
        self.categoryCombo.currentIndexChanged.connect(self._on_category_changed)
        form_layout.addRow("Variable Category:", self.categoryCombo)

        # 2. Variable selector
        self.variableCombo = QtWidgets.QComboBox()
        self.variableCombo.currentIndexChanged.connect(self._on_variable_changed)
        form_layout.addRow("Variable Type:", self.variableCombo)

        # 3. Custom ID spin box
        self.customIdSpin = QtWidgets.QSpinBox()
        self.customIdSpin.setRange(1, 99999)
        self.customIdSpin.setValue(1)
        self.customIdSpin.valueChanged.connect(self._on_custom_id_changed)
        self.customIdLabel = QtWidgets.QLabel("Custom Integer ID:")
        form_layout.addRow(self.customIdLabel, self.customIdSpin)

        # 4. Description box
        self.descriptionLabel = QtWidgets.QLabel("")
        self.descriptionLabel.setWordWrap(True)
        self.descriptionLabel.setStyleSheet(
            "QLabel { background-color: palette(window); border: 1px solid palette(mid); padding: 6px; border-radius: 3px; }"
        )
        form_layout.addRow("Description:", self.descriptionLabel)

        # Button Box
        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        # Initialize selection
        self._populate_variables()
        if existing_variable:
            self._load_existing(existing_variable)

    def _on_category_changed(self, index=None):
        del index
        self._populate_variables()

    def _selected_category_key(self):
        return self.categoryCombo.currentData()

    def _populate_variables(self):
        category = self._selected_category_key()
        self.variableCombo.blockSignals(True)
        self.variableCombo.clear()

        var_list = self._primary_vars if category == "primvars" else self._internal_vars

        for item in var_list:
            display_text = "{} ({})".format(item["name"], item["id"])
            self.variableCombo.addItem(display_text, item)

        # Add custom option
        self.variableCombo.addItem(self.CUSTOM_ID_TEXT, None)
        self.variableCombo.blockSignals(False)

        self._on_variable_changed()

    def _on_variable_changed(self, index=None):
        del index
        data = self.variableCombo.currentData()
        if data is None:
            # Custom ID selected
            self.customIdLabel.setVisible(True)
            self.customIdSpin.setVisible(True)
            self._on_custom_id_changed(self.customIdSpin.value())
        else:
            self.customIdLabel.setVisible(False)
            self.customIdSpin.setVisible(False)
            desc = data.get("description", "")
            self.descriptionLabel.setText(desc or "No description available.")

    def _on_custom_id_changed(self, value):
        category = self._selected_category_key()
        desc = describe_variable(category, value)
        self.descriptionLabel.setText(desc.get("description", ""))

    def _load_existing(self, var):
        cat = str(var.get("category", "cellvars")).lower()
        cat_index = self.categoryCombo.findData(cat)
        if cat_index >= 0:
            self.categoryCombo.setCurrentIndex(cat_index)
        self._populate_variables()

        var_id = var.get("id")
        # Try to find existing by ID in combo
        matched = False
        for i in range(self.variableCombo.count()):
            data = self.variableCombo.itemData(i)
            if data and data.get("id") == var_id:
                self.variableCombo.setCurrentIndex(i)
                matched = True
                break

        if not matched and var_id is not None:
            # Set to Custom ID
            custom_idx = self.variableCombo.findText(self.CUSTOM_ID_TEXT)
            if custom_idx >= 0:
                self.variableCombo.setCurrentIndex(custom_idx)
                try:
                    self.customIdSpin.setValue(int(var_id))
                except (TypeError, ValueError):
                    pass

    def get_variable_data(self):
        """Return the constructed variable dictionary."""
        category = self._selected_category_key()
        data = self.variableCombo.currentData()
        if data is not None:
            return {
                "category": category,
                "id": data["id"],
                "name": data["name"],
                "description": data.get("description", ""),
            }
        else:
            custom_id = self.customIdSpin.value()
            return describe_variable(category, custom_id)

    @classmethod
    def run(cls, existing_variable=None, parent=None):
        dialog = cls(existing_variable=existing_variable, parent=parent)
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            return dialog.get_variable_data()
        return None

