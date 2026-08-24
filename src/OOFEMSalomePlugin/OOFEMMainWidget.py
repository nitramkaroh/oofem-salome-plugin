import os
import uuid
import traceback

from OOFEMSalomePlugin.OOFEMQt import Qt, QtCore, QtWidgets
from OOFEMSalomePlugin.OOFEMMapping import DEFAULT_ELEMENT_MAP
from OOFEMSalomePlugin.OOFEMMaterialDialog import OOFEMMaterialDialog
from OOFEMSalomePlugin.OOFEMBCDialog import OOFEMBCDialog
from OOFEMSalomePlugin.OOFEMCrossSectionDialog import OOFEMCrossSectionDialog
from OOFEMSalomePlugin.OOFEMTimeFunctionDialog import OOFEMTimeFunctionDialog
from OOFEMSalomePlugin.OOFEMProject import (
    PROJECT_SCHEMA_VERSION,
    migrate_project_state,
)


class OOFEMMainWidget(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        # Defer study and state loading until populateAll() is called.
        self.study = None
        self.state = {}
        self.material_templates = []
        self.material_library = []
        self.bc_templates = []
        self.solver_presets = []
        self.analysis_templates = []
        self.cross_section_templates = []
        self.time_function_templates = []
        self._block_signals = False
        self.solverProcess = None
        self._solver_output_buffer = ""
        self._solver_cancelled = False
        self._solver_timed_out = False
        self.solverTimeoutTimer = QtCore.QTimer(self)
        self.solverTimeoutTimer.setSingleShot(True)
        self.solverTimeoutTimer.timeout.connect(self._solverTimedOut)
        self.last_export_file = ""

        layout = QtWidgets.QVBoxLayout()

        # --- Top-level controls ---
        top_layout = QtWidgets.QHBoxLayout()
        self.refreshBtn = QtWidgets.QPushButton("🔄 Load/Refresh Data from Study")
        self.refreshBtn.clicked.connect(self.populateAll)
        top_layout.addWidget(self.refreshBtn)
        self.logBtn = QtWidgets.QPushButton("Show Log")
        self.logBtn.clicked.connect(self.showLog)
        top_layout.addWidget(self.logBtn)
        layout.addLayout(top_layout)

        self.statusLabel = QtWidgets.QLabel("Open or create a study to begin.")
        self.statusLabel.setWordWrap(True)
        layout.addWidget(self.statusLabel)

        # Mesh selector
        layout.addWidget(QtWidgets.QLabel("Select Mesh:"))
        self.meshCombo = QtWidgets.QComboBox()
        self.meshCombo.currentIndexChanged.connect(self.onMeshChanged)
        layout.addWidget(self.meshCombo)

        # --- Tabbed interface for different settings ---
        self.tabs = QtWidgets.QTabWidget()
        layout.addWidget(self.tabs)

        # Analysis and load histories
        analysis_tab = QtWidgets.QWidget()
        analysis_layout = QtWidgets.QVBoxLayout(analysis_tab)
        analysis_form = QtWidgets.QFormLayout()
        self.analysisCombo = QtWidgets.QComboBox()
        self.analysisCombo.currentIndexChanged.connect(self.onAnalysisChanged)
        analysis_form.addRow("Engineering model:", self.analysisCombo)
        analysis_layout.addLayout(analysis_form)

        self.analysisPropsTable = QtWidgets.QTableWidget()
        self.analysisPropsTable.setColumnCount(2)
        self.analysisPropsTable.setHorizontalHeaderLabels(["Parameter", "Value"])
        self.analysisPropsTable.horizontalHeader().setSectionResizeMode(
            0, QtWidgets.QHeaderView.Stretch
        )
        self.analysisPropsTable.horizontalHeader().setSectionResizeMode(
            1, QtWidgets.QHeaderView.Stretch
        )
        self.analysisPropsTable.cellChanged.connect(self.onAnalysisPropertyChanged)
        analysis_layout.addWidget(self.analysisPropsTable)

        time_group = QtWidgets.QGroupBox("Load Time Functions")
        time_layout = QtWidgets.QVBoxLayout(time_group)
        time_buttons = QtWidgets.QHBoxLayout()
        self.addTimeFunctionBtn = QtWidgets.QPushButton("Add")
        self.addTimeFunctionBtn.clicked.connect(self.addTimeFunction)
        time_buttons.addWidget(self.addTimeFunctionBtn)
        self.editTimeFunctionBtn = QtWidgets.QPushButton("Edit")
        self.editTimeFunctionBtn.clicked.connect(self.editTimeFunction)
        time_buttons.addWidget(self.editTimeFunctionBtn)
        self.removeTimeFunctionBtn = QtWidgets.QPushButton("Remove")
        self.removeTimeFunctionBtn.clicked.connect(self.removeTimeFunction)
        time_buttons.addWidget(self.removeTimeFunctionBtn)
        time_layout.addLayout(time_buttons)
        self.timeFunctionTable = QtWidgets.QTableWidget()
        self.timeFunctionTable.setColumnCount(2)
        self.timeFunctionTable.setHorizontalHeaderLabels(["Name", "OOFEM Type"])
        self.timeFunctionTable.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectRows
        )
        self.timeFunctionTable.setEditTriggers(
            QtWidgets.QAbstractItemView.NoEditTriggers
        )
        self.timeFunctionTable.doubleClicked.connect(self.editTimeFunction)
        time_layout.addWidget(self.timeFunctionTable)
        analysis_layout.addWidget(time_group)
        self.tabs.addTab(analysis_tab, "Analysis")
        # Tab 1: Element Mapping
        elem_tab = QtWidgets.QWidget()
        elem_layout = QtWidgets.QVBoxLayout(elem_tab)
        self.elemTable = QtWidgets.QTableWidget()
        self.elemTable.setColumnCount(2)
        self.elemTable.setHorizontalHeaderLabels(["Salome Type", "OOFEM Type"])
        elem_layout.addWidget(self.elemTable)
        self.tabs.addTab(elem_tab, "Element Mapping")

        # Tab 2: Materials
        mat_tab = QtWidgets.QWidget()
        mat_layout = QtWidgets.QVBoxLayout(mat_tab)
        
        # Buttons for adding/removing materials
        mat_btn_layout = QtWidgets.QHBoxLayout()
        self.addMatBtn = QtWidgets.QPushButton("Add Material")
        self.addMatBtn.clicked.connect(self.addMaterial)
        self.removeMatBtn = QtWidgets.QPushButton("Remove Material")
        self.removeMatBtn.clicked.connect(self.removeMaterial)
        mat_btn_layout.addWidget(self.addMatBtn)
        mat_btn_layout.addWidget(self.removeMatBtn)
        mat_layout.addLayout(mat_btn_layout)

        # Table of defined materials
        self.matTable = QtWidgets.QTableWidget()
        self.matTable.setColumnCount(3)
        self.matTable.setHorizontalHeaderLabels(["Name", "OOFEM Type", "Assigned Group"])
        self.matTable.itemSelectionChanged.connect(self.populateMaterialDetails)
        self.matTable.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.matTable.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        mat_layout.addWidget(self.matTable)
        
        # --- Material Properties Editor ---
        props_group = QtWidgets.QGroupBox("Material Properties (select a material above)")
        props_layout = QtWidgets.QVBoxLayout(props_group)
        self.matPropsTable = QtWidgets.QTableWidget()
        self.matPropsTable.setColumnCount(2)
        self.matPropsTable.setHorizontalHeaderLabels(["Parameter", "Value"])
        self.matPropsTable.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self.matPropsTable.cellChanged.connect(self.onMaterialPropertyChanged)
        props_layout.addWidget(self.matPropsTable)
        mat_layout.addWidget(props_group)

        # Spacer
        mat_layout.addStretch()
        self.tabs.addTab(mat_tab, "Materials")

        # Explicit cross sections own material-to-element-group assignments.
        cs_tab = QtWidgets.QWidget()
        cs_layout = QtWidgets.QVBoxLayout(cs_tab)
        cs_buttons = QtWidgets.QHBoxLayout()
        self.addCrossSectionBtn = QtWidgets.QPushButton("Add Cross Section")
        self.addCrossSectionBtn.clicked.connect(self.addCrossSection)
        cs_buttons.addWidget(self.addCrossSectionBtn)
        self.editCrossSectionBtn = QtWidgets.QPushButton("Edit Cross Section")
        self.editCrossSectionBtn.clicked.connect(self.editCrossSection)
        cs_buttons.addWidget(self.editCrossSectionBtn)
        self.removeCrossSectionBtn = QtWidgets.QPushButton("Remove Cross Section")
        self.removeCrossSectionBtn.clicked.connect(self.removeCrossSection)
        cs_buttons.addWidget(self.removeCrossSectionBtn)
        cs_layout.addLayout(cs_buttons)
        self.crossSectionTable = QtWidgets.QTableWidget()
        self.crossSectionTable.setColumnCount(4)
        self.crossSectionTable.setHorizontalHeaderLabels(
            ["Name", "OOFEM Type", "Material", "Assigned Group"]
        )
        self.crossSectionTable.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectRows
        )
        self.crossSectionTable.setEditTriggers(
            QtWidgets.QAbstractItemView.NoEditTriggers
        )
        self.crossSectionTable.doubleClicked.connect(self.editCrossSection)
        cs_layout.addWidget(self.crossSectionTable)
        self.tabs.addTab(cs_tab, "Cross Sections")

        # Tab 3: Boundary Conditions
        bc_tab = QtWidgets.QWidget()
        bc_layout = QtWidgets.QVBoxLayout(bc_tab)

        bc_btn_layout = QtWidgets.QHBoxLayout()
        self.addBCBtn = QtWidgets.QPushButton("Add Boundary Condition")
        self.addBCBtn.clicked.connect(self.addBC)
        self.editBCBtn = QtWidgets.QPushButton("Edit Boundary Condition")
        self.editBCBtn.clicked.connect(self.editBC)
        self.removeBCBtn = QtWidgets.QPushButton("Remove Boundary Condition")
        self.removeBCBtn.clicked.connect(self.removeBC)
        bc_btn_layout.addWidget(self.addBCBtn)
        bc_btn_layout.addWidget(self.editBCBtn)
        bc_btn_layout.addWidget(self.removeBCBtn)
        bc_layout.addLayout(bc_btn_layout)

        self.bcTable = QtWidgets.QTableWidget()
        self.bcTable.setColumnCount(4)
        self.bcTable.setHorizontalHeaderLabels(
            ["Name", "OOFEM Type", "Assigned Group", "Time Function"]
        )
        self.bcTable.itemSelectionChanged.connect(self.populateBCDetails)
        self.bcTable.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.bcTable.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        bc_layout.addWidget(self.bcTable)

        bc_props_group = QtWidgets.QGroupBox("BC Properties (select a BC above)")
        bc_props_layout = QtWidgets.QVBoxLayout(bc_props_group)
        self.bcPropsTable = QtWidgets.QTableWidget()
        self.bcPropsTable.setColumnCount(2)
        self.bcPropsTable.setHorizontalHeaderLabels(["Parameter", "Value"])
        self.bcPropsTable.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self.bcPropsTable.cellChanged.connect(self.onBCPropertyChanged)
        bc_props_layout.addWidget(self.bcPropsTable)
        bc_layout.addWidget(bc_props_group)

        bc_layout.addStretch()
        self.tabs.addTab(bc_tab, "Boundary Conditions")

        # Tab 4: validated export and non-blocking solver execution
        export_tab = QtWidgets.QWidget()
        export_layout = QtWidgets.QVBoxLayout(export_tab)
        export_form = QtWidgets.QFormLayout()
        self.solverPresetCombo = QtWidgets.QComboBox()
        self.solverPresetCombo.currentIndexChanged.connect(self._solverSettingsChanged)
        export_form.addRow("Solver preset:", self.solverPresetCombo)

        executable_layout = QtWidgets.QHBoxLayout()
        self.oofemExecutableEdit = QtWidgets.QLineEdit()
        self.oofemExecutableEdit.setPlaceholderText(
            "OOFEM executable (or set OOFEM_BIN)"
        )
        self.oofemExecutableEdit.editingFinished.connect(self._solverSettingsChanged)
        executable_layout.addWidget(self.oofemExecutableEdit)
        self.browseExecutableBtn = QtWidgets.QPushButton("Browse…")
        self.browseExecutableBtn.clicked.connect(self.browseOOFEMExecutable)
        executable_layout.addWidget(self.browseExecutableBtn)
        export_form.addRow("OOFEM executable:", executable_layout)

        input_layout = QtWidgets.QHBoxLayout()
        self.inputFileEdit = QtWidgets.QLineEdit()
        self.inputFileEdit.setPlaceholderText("Output .in file")
        self.inputFileEdit.editingFinished.connect(self._solverSettingsChanged)
        input_layout.addWidget(self.inputFileEdit)
        self.browseInputBtn = QtWidgets.QPushButton("Browse…")
        self.browseInputBtn.clicked.connect(self.browseInputFile)
        input_layout.addWidget(self.browseInputBtn)
        export_form.addRow("Input file:", input_layout)
        export_layout.addLayout(export_form)

        export_buttons = QtWidgets.QHBoxLayout()
        self.validateBtn = QtWidgets.QPushButton("Validate")
        self.validateBtn.clicked.connect(self.validateModel)
        export_buttons.addWidget(self.validateBtn)
        self.exportBtn = QtWidgets.QPushButton("Generate Input")
        self.exportBtn.clicked.connect(self.export)
        export_buttons.addWidget(self.exportBtn)
        self.runBtn = QtWidgets.QPushButton("Generate && Run")
        self.runBtn.clicked.connect(self.runSolver)
        export_buttons.addWidget(self.runBtn)
        self.cancelRunBtn = QtWidgets.QPushButton("Cancel Run")
        self.cancelRunBtn.setEnabled(False)
        self.cancelRunBtn.clicked.connect(self.cancelSolver)
        export_buttons.addWidget(self.cancelRunBtn)
        export_layout.addLayout(export_buttons)

        self.exportSummaryLabel = QtWidgets.QLabel(
            "Validate the group assignments before generating the model."
        )
        self.exportSummaryLabel.setWordWrap(True)
        export_layout.addWidget(self.exportSummaryLabel)
        export_layout.addWidget(QtWidgets.QLabel("Solver output:"))
        self.solverLog = QtWidgets.QPlainTextEdit()
        self.solverLog.setReadOnly(True)
        export_layout.addWidget(self.solverLog)
        self.tabs.addTab(export_tab, "Export / Solve")

        # Tab 5: discover and open native OOFEM VTK output.
        post_tab = QtWidgets.QWidget()
        post_layout = QtWidgets.QVBoxLayout(post_tab)
        post_layout.addWidget(QtWidgets.QLabel("Result files for the current input:"))
        self.resultList = QtWidgets.QListWidget()
        post_layout.addWidget(self.resultList)
        post_buttons = QtWidgets.QHBoxLayout()
        self.refreshResultsBtn = QtWidgets.QPushButton("Refresh Results")
        self.refreshResultsBtn.clicked.connect(self.refreshResults)
        post_buttons.addWidget(self.refreshResultsBtn)
        self.openParaVisBtn = QtWidgets.QPushButton("Open in ParaVis")
        self.openParaVisBtn.clicked.connect(self.openSelectedResult)
        post_buttons.addWidget(self.openParaVisBtn)
        self.convertMedBtn = QtWidgets.QPushButton("Convert VTK to MED…")
        self.convertMedBtn.clicked.connect(self.convertSelectedResult)
        post_buttons.addWidget(self.convertMedBtn)
        post_layout.addLayout(post_buttons)
        post_note = QtWidgets.QLabel(
            "OOFEM writes VTK/PVD natively. MED conversion uses the optional "
            "meshio package bundled with or installed into SALOME's Python."
        )
        post_note.setWordWrap(True)
        post_layout.addWidget(post_note)
        self.tabs.addTab(post_tab, "Postprocess")

        # --- Bottom buttons ---
        bottom_layout = QtWidgets.QHBoxLayout()
        self.saveBtn = QtWidgets.QPushButton("💾 Commit OOFEM Settings")
        self.saveBtn.clicked.connect(self.saveState)
        bottom_layout.addWidget(self.saveBtn)

        layout.addLayout(bottom_layout)

        self.setLayout(layout)
        self.loadMaterialTemplates()
        self.loadBCTemplates()
        self.loadProjectTemplates()
        self.loadSolverPresets()

    def loadMaterialTemplates(self):
        """Load the material templates and named material library."""
        try:
            from OOFEMSalomePlugin.OOFEMConfig import load_material_catalog

            self.material_templates, self.material_library = load_material_catalog()
        except Exception as error:
            print("Error loading material templates: {}".format(error))
            self.material_templates = []
            self.material_library = []

    def loadBCTemplates(self):
        """Loads boundary condition definitions from the JSON file."""
        try:
            from OOFEMSalomePlugin.OOFEMConfig import (
                load_boundary_condition_templates,
            )

            self.bc_templates = load_boundary_condition_templates()
        except Exception as error:
            print("Error loading BC templates: {}".format(error))
            self.bc_templates = []

    def loadProjectTemplates(self):
        """Load engineering models, cross sections, and time functions."""
        try:
            from OOFEMSalomePlugin.OOFEMConfig import (
                load_analysis_templates,
                load_cross_section_templates,
                load_time_function_templates,
            )

            self.analysis_templates = load_analysis_templates()
            self.cross_section_templates = load_cross_section_templates()
            self.time_function_templates = load_time_function_templates()
            self._block_signals = True
            self.analysisCombo.clear()
            for template in self.analysis_templates:
                self.analysisCombo.addItem(
                    template.get("display_name", template.get("oofem_name", "")),
                    template.get("oofem_name"),
                )
            self._block_signals = False
        except Exception as error:
            print("Error loading OOFEM project templates: {}".format(error))
            self.analysis_templates = []
            self.cross_section_templates = []
            self.time_function_templates = []
            self._block_signals = False

    def loadSolverPresets(self):
        try:
            from OOFEMSalomePlugin.OOFEMConfig import load_solver_presets

            self.solver_presets = load_solver_presets()
            self.solverPresetCombo.clear()
            for preset in self.solver_presets:
                self.solverPresetCombo.addItem(
                    preset.get("display_name", preset["id"]), preset["id"]
                )
        except Exception as error:
            print("Error loading solver presets: {}".format(error))
            self.solver_presets = []

    def showLog(self):
        from OOFEMSalomePlugin.OOFEMModule import getModule

        getModule().showDebugConsole()

    def populateAll(self, checked=False, study=None, state=None):
        """Load the active study and rebuild all controls."""
        current_study = study
        if current_study is None:
            try:
                import salome

                current_study = getattr(salome, "myStudy", None)
                if current_study is None:
                    salome.salome_init()
                    current_study = salome.myStudy
            except Exception:
                current_study = None

        if current_study is None:
            self.statusLabel.setText(
                "No active SALOME study. Open or create one, then refresh."
            )
            return

        self.study = current_study
        if isinstance(state, dict):
            source_state = state
        elif isinstance(self.state, dict):
            source_state = self.state
        else:
            source_state = None

        if (
            isinstance(source_state, dict)
            and source_state.get("schema_version") == PROJECT_SCHEMA_VERSION
        ):
            # Keep the object identity used by SALOME's in-memory study session.
            self.state = source_state
        else:
            self.state = migrate_project_state(source_state)

        self.state.setdefault("element_mapping", {})
        for salome_type, oofem_type in DEFAULT_ELEMENT_MAP.items():
            self.state["element_mapping"].setdefault(salome_type, oofem_type)
        self.state.setdefault("materials", [])
        self.state.setdefault("cross_sections", [])
        self.state.setdefault("time_functions", [])
        self.state.setdefault("bcs", [])
        self.state.setdefault("analysis", {})
        self.state.setdefault(
            "solver_preset",
            self.solverPresetCombo.itemData(0)
            if self.solverPresetCombo.count()
            else None,
        )
        if not self.state.get("oofem_executable"):
            from OOFEMSalomePlugin.OOFEMRunner import resolve_executable

            self.state["oofem_executable"] = resolve_executable() or ""
        self.state.setdefault("last_input_file", "")

        self.populateMeshes()
        self.populateElementMapping()
        self.populateAnalysis()
        self.populateTimeFunctions()
        self.populateMaterials()
        self.populateCrossSections()
        self.populateBCs()
        preset_index = self.solverPresetCombo.findData(self.state["solver_preset"])
        if preset_index >= 0:
            self.solverPresetCombo.setCurrentIndex(preset_index)
        self.oofemExecutableEdit.setText(self.state["oofem_executable"])
        self.inputFileEdit.setText(self.state["last_input_file"])
        self.last_export_file = self.state["last_input_file"]
        self.refreshResults()

        mesh_count = self.meshCombo.count()
        if mesh_count:
            self.statusLabel.setText(
                "Loaded {} mesh{} from the active study.".format(
                    mesh_count, "" if mesh_count == 1 else "es"
                )
            )
        else:
            self.statusLabel.setText(
                "No meshes found. Create or import a mesh in SALOME's Mesh module, "
                "then refresh."
            )

    # ---------------------------
    # Mesh selector
    # ---------------------------
    def populateMeshes(self):
        selected_mesh_id = self.state.get("selected_mesh_id") or self.meshCombo.currentData()
        self.meshCombo.blockSignals(True)
        self.meshCombo.clear()
        try:
            smesh_comp = self.study.FindComponent("SMESH")
            if smesh_comp is not None:
                child_iterator = self.study.NewChildIterator(smesh_comp)
                while child_iterator.More():
                    s_object = child_iterator.Value()
                    mesh_object = s_object.GetObject()
                    if (
                        mesh_object is not None
                        and hasattr(mesh_object, "GetGroups")
                        and hasattr(mesh_object, "GetNodesId")
                    ):
                        self.meshCombo.addItem(s_object.GetName(), s_object.GetID())
                    child_iterator.Next()
        except Exception:
            # CORBA can report UNKNOWN while a study or SMESH component is being
            # created. Keep the module open and let Refresh retry the lookup.
            print("OOFEM: active-study mesh lookup failed")
            traceback.print_exc()
        finally:
            self.meshCombo.blockSignals(False)

        selected_index = self.meshCombo.findData(selected_mesh_id)
        if selected_index >= 0:
            self.meshCombo.setCurrentIndex(selected_index)
        self.onMeshChanged(self.meshCombo.currentIndex())

    def onMeshChanged(self, index):
        if index >= 0 and self.state is not None:
            self.state["selected_mesh_id"] = self.meshCombo.itemData(index)

    def _meshFromEntry(self, mesh_id):
        if self.study is not None:
            try:
                study_object = self.study.FindObjectID(mesh_id)
                if study_object is not None:
                    mesh = study_object.GetObject()
                    if mesh is not None:
                        return mesh
            except Exception:
                traceback.print_exc()
        try:
            import salome

            return salome.IDToObject(mesh_id)
        except Exception:
            return None

    def getMeshGroups(self):
        """Classify groups into material-domain, node, and boundary choices."""
        empty = {"elements": [], "nodes": [], "boundaries": []}
        mesh_id = self.meshCombo.currentData()
        if not mesh_id:
            return empty

        try:
            import SMESH

            mesh = self._meshFromEntry(mesh_id)
            if not mesh:
                print("Could not convert selected object to a mesh.")
                return empty

            node_type = getattr(SMESH.NODE, "_v", SMESH.NODE)
            dimensional_types = [
                getattr(getattr(SMESH, name, None), "_v", getattr(SMESH, name, None))
                for name in ("EDGE", "FACE", "VOLUME")
            ]
            groups_by_type = {}
            for group in mesh.GetGroups():
                group_type = getattr(group.GetType(), "_v", group.GetType())
                groups_by_type.setdefault(group_type, []).append(group.GetName())

            result = dict(empty)
            result["nodes"] = sorted(groups_by_type.get(node_type, []))
            available_dimensions = [
                entity_type
                for entity_type in dimensional_types
                if entity_type is not None and groups_by_type.get(entity_type)
            ]
            if not available_dimensions:
                return result
            material_type = available_dimensions[-1]
            result["elements"] = sorted(groups_by_type.get(material_type, []))
            material_index = dimensional_types.index(material_type)
            if material_index > 0:
                boundary_type = dimensional_types[material_index - 1]
                result["boundaries"] = sorted(
                    groups_by_type.get(boundary_type, [])
                )
            return result
        except Exception as error:
            print("Could not get mesh groups: {}".format(error))
            return empty

    # ---------------------------
    # Analysis and time functions
    @staticmethod
    def _templateForType(templates, oofem_type):
        expected = str(oofem_type or "").lower()
        return next(
            (
                template
                for template in templates
                if str(template.get("oofem_name") or "").lower() == expected
            ),
            None,
        )

    @staticmethod
    def _formatParameterValue(value, parameter_type):
        if value is None:
            return ""
        if parameter_type in ("int_list", "float_list") and isinstance(
            value, (list, tuple)
        ):
            return ", ".join(str(item) for item in value)
        return str(value)

    @staticmethod
    def _coerceParameterValue(text, parameter_type):
        if parameter_type == "float":
            return float(text)
        if parameter_type == "int":
            return int(text)
        if parameter_type == "string":
            return str(text)
        if parameter_type in ("int_list", "float_list"):
            parts = [part.strip() for part in text.split(",")]
            if not parts or any(not part for part in parts):
                raise ValueError("expected comma-separated numbers")
            converter = int if parameter_type == "int_list" else float
            return [converter(part) for part in parts]
        raise ValueError("unsupported type '{}'".format(parameter_type))

    def populateAnalysis(self):
        analysis = self.state.get("analysis") or {}
        analysis_type = analysis.get("oofem_type")
        self._block_signals = True
        index = next(
            (
                position
                for position, template in enumerate(self.analysis_templates)
                if str(template.get("oofem_name") or "").lower()
                == str(analysis_type or "").lower()
            ),
            -1,
        )
        if index < 0 and self.analysisCombo.count():
            index = 0
        self.analysisCombo.setCurrentIndex(index)
        self._block_signals = False
        self.populateAnalysisDetails()

    def populateAnalysisDetails(self):
        self._block_signals = True
        self.analysisPropsTable.setRowCount(0)
        template = self._templateForType(
            self.analysis_templates, self.analysisCombo.currentData()
        )
        if template is None:
            self._block_signals = False
            return
        current_params = (self.state.get("analysis") or {}).get("params") or {}
        for parameter in template.get("params", []):
            row = self.analysisPropsTable.rowCount()
            self.analysisPropsTable.insertRow(row)
            optional = bool(parameter.get("optional", False))
            label = parameter.get("name", parameter.get("key", ""))
            if optional:
                label += " (optional)"
            name_item = QtWidgets.QTableWidgetItem(label)
            name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
            name_item.setData(Qt.UserRole, parameter.get("key"))
            name_item.setData(Qt.UserRole + 1, parameter.get("type", "float"))
            name_item.setData(Qt.UserRole + 2, optional)
            value = current_params.get(
                parameter.get("key"), parameter.get("default", "")
            )
            value_item = QtWidgets.QTableWidgetItem(
                self._formatParameterValue(
                    value, parameter.get("type", "float")
                )
            )
            description = parameter.get("description")
            if description:
                name_item.setToolTip(description)
                value_item.setToolTip(description)
            self.analysisPropsTable.setItem(row, 0, name_item)
            self.analysisPropsTable.setItem(row, 1, value_item)
        self._block_signals = False

    def onAnalysisChanged(self, index):
        if self._block_signals or not (0 <= index < len(self.analysis_templates)):
            return
        template = self.analysis_templates[index]
        self.state["analysis"] = {
            "id": (self.state.get("analysis") or {}).get(
                "id", "analysis-{}".format(uuid.uuid4())
            ),
            "oofem_type": template.get("oofem_name"),
            "params": {
                parameter["key"]: parameter["default"]
                for parameter in template.get("params", [])
                if "default" in parameter
            },
        }
        self.populateAnalysisDetails()

    def onAnalysisPropertyChanged(self, row, column):
        if self._block_signals or column != 1:
            return
        key_item = self.analysisPropsTable.item(row, 0)
        value_item = self.analysisPropsTable.item(row, 1)
        if key_item is None or value_item is None:
            return
        key = key_item.data(Qt.UserRole)
        parameter_type = key_item.data(Qt.UserRole + 1) or "float"
        optional = bool(key_item.data(Qt.UserRole + 2))
        text = value_item.text().strip()
        parameters = self.state.setdefault("analysis", {}).setdefault(
            "params", {}
        )
        if optional and not text:
            parameters.pop(key, None)
            return
        try:
            parameters[key] = self._coerceParameterValue(text, parameter_type)
        except (TypeError, ValueError) as error:
            self.statusLabel.setText(
                "Invalid analysis parameter '{}': {}".format(key, error)
            )

    def populateTimeFunctions(self):
        self.timeFunctionTable.setRowCount(0)
        for index, function in enumerate(self.state.get("time_functions", []), start=1):
            row = self.timeFunctionTable.rowCount()
            self.timeFunctionTable.insertRow(row)
            name_item = QtWidgets.QTableWidgetItem(
                function.get("name") or "Time function {}".format(index)
            )
            name_item.setData(Qt.UserRole, function.get("id"))
            self.timeFunctionTable.setItem(row, 0, name_item)
            self.timeFunctionTable.setItem(
                row,
                1,
                QtWidgets.QTableWidgetItem(function.get("oofem_type", "")),
            )

    def _selectedTimeFunction(self):
        selected = self.timeFunctionTable.selectedItems()
        if not selected:
            return None
        function_id = selected[0].data(Qt.UserRole)
        return next(
            (
                function
                for function in self.state.get("time_functions", [])
                if function.get("id") == function_id
            ),
            None,
        )

    def addTimeFunction(self):
        data = OOFEMTimeFunctionDialog.run(
            self.time_function_templates, parent=self
        )
        if data:
            data["id"] = str(uuid.uuid4())
            self.state["time_functions"].append(data)
            self.populateTimeFunctions()

    def editTimeFunction(self, *unused):
        existing = self._selectedTimeFunction()
        if existing is None:
            QtWidgets.QMessageBox.warning(
                self, "Time Function", "Select a time function to edit."
            )
            return
        data = OOFEMTimeFunctionDialog.run(
            self.time_function_templates, existing_tf=existing, parent=self
        )
        if data:
            data["id"] = existing.get("id")
            existing.clear()
            existing.update(data)
            self.populateTimeFunctions()
            self.populateBCs()

    def removeTimeFunction(self):
        existing = self._selectedTimeFunction()
        if existing is None:
            QtWidgets.QMessageBox.warning(
                self, "Time Function", "Select a time function to remove."
            )
            return
        function_id = existing.get("id")
        references = [
            boundary_condition.get("name", "Unnamed")
            for boundary_condition in self.state.get("bcs", [])
            if boundary_condition.get("time_function_id") == function_id
        ]
        if references:
            QtWidgets.QMessageBox.warning(
                self,
                "Time Function in Use",
                "The function is referenced by: {}.".format(", ".join(references)),
            )
            return
        if len(self.state.get("time_functions", [])) <= 1:
            QtWidgets.QMessageBox.warning(
                self,
                "Time Function",
                "At least one load time function is required.",
            )
            return
        self.state["time_functions"] = [
            function
            for function in self.state["time_functions"]
            if function.get("id") != function_id
        ]
        self.populateTimeFunctions()

    # Element mapping table
    # ---------------------------
    def populateElementMapping(self):
        mapping = self.state.get("element_mapping", {})
        self.elemTable.setRowCount(0)

        for salome_type, oofem_type in mapping.items():
            row = self.elemTable.rowCount()
            self.elemTable.insertRow(row)
            self.elemTable.setItem(row, 0, QtWidgets.QTableWidgetItem(salome_type))
            self.elemTable.setItem(row, 1, QtWidgets.QTableWidgetItem(oofem_type))
            
    # ---------------------------
    # Cross sections and material/group assignments
    def populateCrossSections(self):
        self.crossSectionTable.setRowCount(0)
        material_names = {
            material.get("id"): material.get("name", "Unnamed")
            for material in self.state.get("materials", [])
        }
        for cross_section in self.state.get("cross_sections", []):
            row = self.crossSectionTable.rowCount()
            self.crossSectionTable.insertRow(row)
            name_item = QtWidgets.QTableWidgetItem(
                cross_section.get("name", "Unnamed")
            )
            name_item.setData(Qt.UserRole, cross_section.get("id"))
            self.crossSectionTable.setItem(row, 0, name_item)
            self.crossSectionTable.setItem(
                row,
                1,
                QtWidgets.QTableWidgetItem(
                    cross_section.get("oofem_type", "")
                ),
            )
            self.crossSectionTable.setItem(
                row,
                2,
                QtWidgets.QTableWidgetItem(
                    material_names.get(
                        cross_section.get("material_id"), "<missing>"
                    )
                ),
            )
            self.crossSectionTable.setItem(
                row,
                3,
                QtWidgets.QTableWidgetItem(
                    cross_section.get("assigned_group", "")
                ),
            )

    def _selectedCrossSection(self):
        selected = self.crossSectionTable.selectedItems()
        if not selected:
            return None
        cross_section_id = selected[0].data(Qt.UserRole)
        return next(
            (
                cross_section
                for cross_section in self.state.get("cross_sections", [])
                if cross_section.get("id") == cross_section_id
            ),
            None,
        )

    def _crossSectionDialogData(self, existing=None):
        return OOFEMCrossSectionDialog.run(
            self.cross_section_templates,
            self.state.get("materials", []),
            self.getMeshGroups().get("elements", []),
            self.state.get("element_mapping", {}).keys(),
            existing_cs=existing,
            parent=self,
        )

    def _crossSectionAssignmentAvailable(self, data, ignored_id=None):
        group_name = data.get("assigned_group")
        duplicate = next(
            (
                cross_section
                for cross_section in self.state.get("cross_sections", [])
                if cross_section.get("id") != ignored_id
                and cross_section.get("assigned_group") == group_name
            ),
            None,
        )
        if duplicate is None:
            return True
        QtWidgets.QMessageBox.warning(
            self,
            "Duplicate Cross Section",
            "Group '{}' is already assigned by '{}'.".format(
                group_name, duplicate.get("name", "Unnamed")
            ),
        )
        return False

    def addCrossSection(self):
        data = self._crossSectionDialogData()
        if data and self._crossSectionAssignmentAvailable(data):
            data["id"] = str(uuid.uuid4())
            self.state["cross_sections"].append(data)
            self.populateCrossSections()

    def editCrossSection(self, *unused):
        existing = self._selectedCrossSection()
        if existing is None:
            QtWidgets.QMessageBox.warning(
                self, "Cross Section", "Select a cross section to edit."
            )
            return
        data = self._crossSectionDialogData(existing)
        if data and self._crossSectionAssignmentAvailable(
            data, ignored_id=existing.get("id")
        ):
            data["id"] = existing.get("id")
            existing.clear()
            existing.update(data)
            self.populateCrossSections()

    def removeCrossSection(self):
        existing = self._selectedCrossSection()
        if existing is None:
            QtWidgets.QMessageBox.warning(
                self, "Cross Section", "Select a cross section to remove."
            )
            return
        cross_section_id = existing.get("id")
        self.state["cross_sections"] = [
            cross_section
            for cross_section in self.state["cross_sections"]
            if cross_section.get("id") != cross_section_id
        ]
        self.populateCrossSections()

    @staticmethod
    def _crossSectionFromLegacyMaterial(material):
        parameters = material.get("params") or {}
        section_parameters = {}
        if parameters.get("A") is not None:
            section_parameters["area"] = parameters["A"]
        if parameters.get("t") is not None:
            section_parameters["thick"] = parameters["t"]
        return {
            "id": str(uuid.uuid4()),
            "name": "{} cross section".format(
                material.get("name", "Unnamed")
            ),
            "oofem_type": "simplecs",
            "material_id": material.get("id"),
            "assigned_group": material.get("assigned_group"),
            "element_mapping_override": material.get(
                "element_mapping_override"
            ),
            "params": section_parameters,
        }

    # Materials
    # ---------------------------
    def populateMaterials(self):
        """Populates the main material table from the plugin state."""
        self._block_signals = True
        self.matTable.setRowCount(0)
        for i, mat_data in enumerate(self.state.get("materials", [])):
            row = self.matTable.rowCount()
            self.matTable.insertRow(row)
            
            name_item = QtWidgets.QTableWidgetItem(mat_data.get("name", "Unnamed"))
            # Store the material's unique ID in the item for later retrieval
            name_item.setData(Qt.UserRole, mat_data.get("id"))
            
            self.matTable.setItem(row, 0, name_item)
            self.matTable.setItem(row, 1, QtWidgets.QTableWidgetItem(mat_data.get("oofem_type", "")))
            self.matTable.setItem(row, 2, QtWidgets.QTableWidgetItem(mat_data.get("assigned_group", "")))
        self._block_signals = False
        self.populateMaterialDetails() # Clear details pane if no selection

    def populateMaterialDetails(self):
        """Populates the property editor based on the selected material."""
        self._block_signals = True
        self.matPropsTable.setRowCount(0)
        
        selected_items = self.matTable.selectedItems()
        if not selected_items:
            self._block_signals = False
            return

        mat_id = selected_items[0].data(Qt.UserRole)
        mat_data = next((m for m in self.state['materials'] if m['id'] == mat_id), None)
        if not mat_data:
            self._block_signals = False
            return

        # Find the template for this material type
        template = next((t for t in self.material_templates if t['oofem_name'] == mat_data['oofem_type']), None)
        if not template:
            self._block_signals = False
            return

        # Populate the properties table
        current_params = mat_data.get("params", {})
        for param_def in template.get("params", []):
            row = self.matPropsTable.rowCount()
            self.matPropsTable.insertRow(row)
            
            is_optional = param_def.get("optional", False)
            display_name = param_def['name']
            if is_optional:
                display_name += " (optional)"

            name_item = QtWidgets.QTableWidgetItem(display_name)
            name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
            name_item.setData(Qt.UserRole, param_def['key']) # Store key (e.g., "E")
            # Store the expected data type for later conversion. Default to 'float' for backward compatibility.
            name_item.setData(Qt.UserRole + 1, param_def.get('type', 'float'))
            
            value = current_params.get(
                param_def["key"], param_def.get("default", "")
            )
            value_item = QtWidgets.QTableWidgetItem(
                self._formatParameterValue(
                    value, param_def.get("type", "float")
                )
            )
            name_item.setData(Qt.UserRole + 2, is_optional)

            # Set tooltip if a description is available in the template
            description = param_def.get("description")
            if description:
                name_item.setToolTip(description)
                value_item.setToolTip(description)

            self.matPropsTable.setItem(row, 0, name_item)
            self.matPropsTable.setItem(row, 1, value_item)
        
        self._block_signals = False

    def addMaterial(self):
        """Opens a dialog to add a new material instance."""
        mesh_groups = self.getMeshGroups()
        new_mat_data = OOFEMMaterialDialog.run(
            self.material_templates,
            mesh_groups,
            material_library=self.material_library,
            parent=self,
        )

        if new_mat_data:
            new_mat_data['id'] = str(uuid.uuid4())
            self.state["materials"].append(new_mat_data)
            if new_mat_data.get("assigned_group"):
                section = self._crossSectionFromLegacyMaterial(new_mat_data)
                if self._crossSectionAssignmentAvailable(section):
                    self.state["cross_sections"].append(section)
                    self.populateCrossSections()
            self.populateMaterials()

    def removeMaterial(self):
        """Removes the selected material from the state."""
        selected_items = self.matTable.selectedItems()
        if not selected_items:
            QtWidgets.QMessageBox.warning(self, "Warning", "No material selected to remove.")
            return

        mat_id = selected_items[0].data(Qt.UserRole)
        references = [
            cross_section.get("name", "Unnamed")
            for cross_section in self.state.get("cross_sections", [])
            if cross_section.get("material_id") == mat_id
        ]
        if references:
            QtWidgets.QMessageBox.warning(
                self,
                "Material in Use",
                "Remove or reassign these cross sections first: {}.".format(
                    ", ".join(references)
                ),
            )
            return
        self.state['materials'] = [m for m in self.state['materials'] if m.get('id') != mat_id]
        self.populateMaterials()

    # ---------------------------
    # Boundary Conditions
    # ---------------------------
    def populateBCs(self):
        """Populates the main BC table from the plugin state."""
        self._block_signals = True
        self.bcTable.setRowCount(0)
        time_function_names = {
            function.get("id"): function.get("name")
            or "Time function {}".format(index)
            for index, function in enumerate(
                self.state.get("time_functions", []), start=1
            )
        }
        for i, bc_data in enumerate(self.state.get("bcs", [])):
            row = self.bcTable.rowCount()
            self.bcTable.insertRow(row)
            
            name_item = QtWidgets.QTableWidgetItem(bc_data.get("name", "Unnamed"))
            name_item.setData(Qt.UserRole, bc_data.get("id"))
            
            self.bcTable.setItem(row, 0, name_item)
            self.bcTable.setItem(row, 1, QtWidgets.QTableWidgetItem(bc_data.get("oofem_type", "")))
            self.bcTable.setItem(row, 2, QtWidgets.QTableWidgetItem(bc_data.get("assigned_group", "")))
            self.bcTable.setItem(
                row,
                3,
                QtWidgets.QTableWidgetItem(
                    time_function_names.get(
                        bc_data.get("time_function_id"), "<default>"
                    )
                ),
            )
        self._block_signals = False
        self.populateBCDetails()

    def populateBCDetails(self):
        """Populates the property editor based on the selected BC."""
        self._block_signals = True
        self.bcPropsTable.setRowCount(0)
        
        selected_items = self.bcTable.selectedItems()
        if not selected_items:
            self._block_signals = False
            return

        bc_id = selected_items[0].data(Qt.UserRole)
        bc_data = next((m for m in self.state['bcs'] if m['id'] == bc_id), None)
        if not bc_data:
            self._block_signals = False
            return

        template = self._templateForType(
            self.bc_templates, bc_data.get("oofem_type")
        )
        if not template:
            self._block_signals = False
            return

        current_params = bc_data.get("params", {})
        for param_def in template.get("params", []):
            row = self.bcPropsTable.rowCount()
            self.bcPropsTable.insertRow(row)
            
            is_optional = param_def.get("optional", False)
            display_name = param_def['name']
            if is_optional:
                display_name += " (optional)"

            name_item = QtWidgets.QTableWidgetItem(display_name)
            name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
            name_item.setData(Qt.UserRole, param_def['key'])
            name_item.setData(Qt.UserRole + 1, param_def.get('type', 'float'))
            name_item.setData(Qt.UserRole + 2, is_optional)
            
            parameter_key = param_def["key"]
            value = current_params.get(parameter_key)
            if value is None and parameter_key == "dofs" and "dof" in current_params:
                value = [current_params["dof"]]
            if (
                value is None
                and parameter_key in ("values", "components")
                and "val" in current_params
            ):
                value = [current_params["val"]]
            if value is None:
                value = param_def.get("default", "")
            value_item = QtWidgets.QTableWidgetItem(
                self._formatParameterValue(
                    value, param_def.get("type", "float")
                )
            )

            description = param_def.get("description")
            if description:
                name_item.setToolTip(description)
                value_item.setToolTip(description)

            self.bcPropsTable.setItem(row, 0, name_item)
            self.bcPropsTable.setItem(row, 1, value_item)
        
        self._block_signals = False

    def _selectedBC(self):
        selected = self.bcTable.selectedItems()
        if not selected:
            return None
        boundary_condition_id = selected[0].data(Qt.UserRole)
        return next(
            (
                boundary_condition
                for boundary_condition in self.state.get("bcs", [])
                if boundary_condition.get("id") == boundary_condition_id
            ),
            None,
        )

    def addBC(self):
        """Create a canonical multi-component BC."""
        new_bc_data = OOFEMBCDialog.run(
            self.bc_templates,
            self.getMeshGroups(),
            parent=self,
            time_functions=self.state.get("time_functions", []),
        )
        if new_bc_data:
            new_bc_data["id"] = str(uuid.uuid4())
            self.state["bcs"].append(new_bc_data)
            self.populateBCs()

    def editBC(self):
        existing = self._selectedBC()
        if existing is None:
            QtWidgets.QMessageBox.warning(
                self, "Boundary Condition", "Select a boundary condition to edit."
            )
            return
        data = OOFEMBCDialog.run(
            self.bc_templates,
            self.getMeshGroups(),
            existing_bc=existing,
            parent=self,
            time_functions=self.state.get("time_functions", []),
        )
        if data:
            data["id"] = existing.get("id")
            existing.clear()
            existing.update(data)
            self.populateBCs()

    def removeBC(self):
        """Removes the selected BC from the state."""
        selected_items = self.bcTable.selectedItems()
        if not selected_items:
            QtWidgets.QMessageBox.warning(self, "Warning", "No BC selected to remove.")
            return

        bc_id = selected_items[0].data(Qt.UserRole)
        self.state['bcs'] = [m for m in self.state['bcs'] if m.get('id') != bc_id]
        self.populateBCs()

    def onBCPropertyChanged(self, row, column):
        """Updates the state when a BC property value is changed."""
        if self._block_signals or column != 1:
            return

        selected_items = self.bcTable.selectedItems()
        if not selected_items: return

        bc_id = selected_items[0].data(Qt.UserRole)
        bc_data = next((m for m in self.state['bcs'] if m['id'] == bc_id), None)
        if not bc_data: return

        key_item = self.bcPropsTable.item(row, 0)
        value_item = self.bcPropsTable.item(row, 1)
        param_key = key_item.data(Qt.UserRole)
        param_type = key_item.data(Qt.UserRole + 1)
        is_optional = key_item.data(Qt.UserRole + 2)
        value_text = value_item.text().strip()

        if is_optional and not value_text:
            if param_key in bc_data['params']:
                del bc_data['params'][param_key]
            return

        try:
            new_value = self._coerceParameterValue(value_text, param_type)
        except (TypeError, ValueError):
            print(
                "Invalid value '{}' for parameter '{}' (expected type: {}). "
                "Change not saved.".format(value_text, param_key, param_type)
            )
            return
        bc_data["params"][param_key] = new_value
        # Keep scalar aliases editable for an unmigrated in-memory record.
        if param_key == "dofs" and "dof" in bc_data["params"]:
            bc_data["params"]["dof"] = new_value[0]
        if (
            param_key in ("values", "components")
            and "val" in bc_data["params"]
        ):
            bc_data["params"]["val"] = new_value[0]


    def onMaterialPropertyChanged(self, row, column):
        """Updates the state when a material property value is changed."""
        if self._block_signals or column != 1:
            return

        selected_items = self.matTable.selectedItems()
        if not selected_items:
            return

        mat_id = selected_items[0].data(Qt.UserRole)
        mat_data = next((m for m in self.state['materials'] if m['id'] == mat_id), None)
        if not mat_data:
            return

        key_item = self.matPropsTable.item(row, 0)
        value_item = self.matPropsTable.item(row, 1)
        param_key = key_item.data(Qt.UserRole)
        
        param_type = key_item.data(Qt.UserRole + 1) # Retrieve the stored type
        is_optional = key_item.data(Qt.UserRole + 2)
        
        value_text = value_item.text().strip()

        # If the parameter is optional and the user cleared the value, remove it from the state
        if is_optional and not value_text:
            if param_key in mat_data['params']:
                del mat_data['params'][param_key]
                print(f"INFO: Optional parameter '{param_key}' was removed.")
            return

        new_value = None
        try:
            if param_type == 'float':
                new_value = float(value_text)
            elif param_type == 'int':
                new_value = int(value_text)
            elif param_type == 'string':
                new_value = str(value_text)
            # Future types like 'bool' or choice lists can be added here.
            else:
                # Default to string if type is unknown or not specified
                new_value = str(value_text)
        except ValueError:
            print(f"Invalid value '{value_text}' for parameter '{param_key}' (expected type: {param_type}). Change not saved.")
            return
        mat_data['params'][param_key] = new_value

    # ---------------------------
    # State Management
    # ---------------------------
    def collectElementMapping(self):
        new_map = {}
        for row in range(self.elemTable.rowCount()):
            salome_item = self.elemTable.item(row, 0)
            oofem_item = self.elemTable.item(row, 1)
            if salome_item is None or oofem_item is None:
                continue
            salome_type = salome_item.text().strip()
            oofem_type = oofem_item.text().strip()
            if salome_type:
                new_map[salome_type] = oofem_type
        self.state["element_mapping"] = new_map

    def saveState(self):
        if self.study is None:
            QtWidgets.QMessageBox.warning(self, "Error", "Plugin not initialized. Click Refresh first.")
            return

        self.collectElementMapping()
        self._solverSettingsChanged()

        from OOFEMSalomePlugin.OOFEMModule import getModule

        if getModule().set_study_state(self.state, mark_modified=True):
            QtWidgets.QMessageBox.information(
                self,
                "OOFEM Settings Committed",
                "OOFEM settings are ready. Use File > Save to include them in the SALOME study.",
            )
        else:
            QtWidgets.QMessageBox.critical(
                self, "Save Failed", "OOFEM could not collect the project settings."
            )


    # ---------------------------
    # Export / solve
    # ---------------------------
    def _solverSettingsChanged(self, *unused):
        if not isinstance(self.state, dict):
            return
        self.state["solver_preset"] = self.solverPresetCombo.currentData()
        self.state["oofem_executable"] = self.oofemExecutableEdit.text().strip()
        self.state["last_input_file"] = self.inputFileEdit.text().strip()

    def _selectedSolverSettings(self):
        from OOFEMSalomePlugin.OOFEMConfig import solver_settings

        settings = solver_settings(self.solverPresetCombo.currentData())
        results_directory = os.environ.get("OOFEM_RESULTS_DIRECTORY", "").strip()
        if results_directory:
            settings["output_directory"] = os.path.abspath(
                os.path.expanduser(results_directory)
            )
        return settings

    @staticmethod
    def _solverTimeoutSeconds():
        raw_value = os.environ.get("OOFEM_SOLVER_TIMEOUT", "300")
        try:
            return max(1, min(86400, int(raw_value)))
        except (TypeError, ValueError):
            return 300

    @staticmethod
    def _autoOpenParaVis():
        return os.environ.get("OOFEM_AUTO_OPEN_PARAVIS", "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )

    def browseOOFEMExecutable(self):
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Select OOFEM Executable", self.oofemExecutableEdit.text()
        )
        if filename:
            self.oofemExecutableEdit.setText(filename)
            self._solverSettingsChanged()

    def browseInputFile(self):
        filename, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "OOFEM Input File",
            self.inputFileEdit.text() or "oofem-model.in",
            "OOFEM Input Files (*.in);;All Files (*)",
        )
        if filename:
            if not os.path.splitext(filename)[1]:
                filename += ".in"
            self.inputFileEdit.setText(filename)
            self._solverSettingsChanged()
            self.refreshResults()

    def _makeExporter(self):
        if self.study is None:
            raise RuntimeError("Plugin not initialized. Click Refresh first.")
        mesh_id = self.meshCombo.currentData()
        if not mesh_id:
            raise RuntimeError("No mesh selected for export.")
        mesh = self._meshFromEntry(mesh_id)
        if not mesh:
            raise RuntimeError("The selected study object is not a valid SMESH mesh.")

        from OOFEMSalomePlugin.OOFEMExporter import OOFEMExporter

        self.collectElementMapping()
        cross_sections = self.state.get("cross_sections", [])
        if not cross_sections and any(
            material.get("assigned_group")
            for material in self.state.get("materials", [])
        ):
            # Compatibility for objects appended by legacy scripts after load.
            cross_sections = None
        return OOFEMExporter(
            mesh,
            self.state.get("element_mapping", {}),
            self.state.get("materials", []),
            self.state.get("bcs", []),
            self.bc_templates,
            solver_settings=self._selectedSolverSettings(),
            cross_sections=cross_sections,
            time_functions=self.state.get("time_functions", []),
            analysis=self.state.get("analysis", {}),
        )

    def validateModel(self):
        try:
            summary = self._makeExporter().validate()
            message = (
                "Valid {domain} model: {nodes} nodes, {elements} elements, "
                "{materials} materials, {cross_sections} cross sections, "
                "{boundary_conditions} BCs, {time_functions} time functions."
            ).format(**summary)
            self.exportSummaryLabel.setText(message)
            self.statusLabel.setText(message)
            return summary
        except Exception as error:
            self.exportSummaryLabel.setText("Validation failed: {}".format(error))
            QtWidgets.QMessageBox.critical(
                self, "OOFEM Model Validation", str(error)
            )
            return None

    def _chooseInputFilename(self):
        filename = self.inputFileEdit.text().strip()
        if filename:
            return filename
        mesh_id = self.meshCombo.currentData()
        study_object = self.study.FindObjectID(mesh_id) if self.study else None
        default_name = "{}.in".format(
            study_object.GetName() if study_object else "oofem-model"
        )
        preferred_directory = (
            os.environ.get("OOFEM_WORKING_DIRECTORY", "").strip()
            or os.environ.get("OOFEM_RESULTS_DIRECTORY", "").strip()
        )
        if preferred_directory:
            preferred_directory = os.path.abspath(
                os.path.expanduser(preferred_directory)
            )
            default_name = os.path.join(
                preferred_directory, os.path.basename(default_name)
            )
        filename, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Export OOFEM Input File",
            default_name,
            "OOFEM Input Files (*.in);;All Files (*)",
        )
        if filename and not os.path.splitext(filename)[1]:
            filename += ".in"
        return filename

    def _exportModel(self):
        filename = self._chooseInputFilename()
        if not filename:
            return None
        exporter = self._makeExporter()
        summary = exporter.validate()
        result = exporter.export(filename)
        self.inputFileEdit.setText(result["input_file"])
        self.last_export_file = result["input_file"]
        self._solverSettingsChanged()
        self.exportSummaryLabel.setText(
            "Exported {domain}: {nodes} nodes, {elements} elements to {path}".format(
                path=result["input_file"], **summary
            )
        )
        self.refreshResults()
        return result

    def export(self):
        try:
            result = self._exportModel()
            if result:
                QtWidgets.QMessageBox.information(
                    self,
                    "Export Successful",
                    "OOFEM input generated at:\n{}".format(result["input_file"]),
                )
            return result
        except Exception as error:
            QtWidgets.QMessageBox.critical(self, "OOFEM Export", str(error))
            traceback.print_exc()
            return None

    def runSolver(self):
        try:
            result = self._exportModel()
            if not result:
                return

            from OOFEMSalomePlugin.OOFEMRunner import resolve_executable

            executable = resolve_executable(self.oofemExecutableEdit.text().strip())
            if executable is None:
                raise RuntimeError(
                    "OOFEM executable not found. Browse to it or set OOFEM_BIN."
                )
            if self.solverProcess is not None and (
                self.solverProcess.state() != QtCore.QProcess.NotRunning
            ):
                raise RuntimeError("An OOFEM solve is already running.")

            self.oofemExecutableEdit.setText(executable)
            self._solverSettingsChanged()
            self.solverLog.clear()
            self._solver_output_buffer = ""
            self._solver_cancelled = False
            self._solver_timed_out = False
            self.solverProcess = QtCore.QProcess(self)
            self.solverProcess.setProcessChannelMode(QtCore.QProcess.MergedChannels)
            working_directory = os.environ.get(
                "OOFEM_WORKING_DIRECTORY", ""
            ).strip()
            working_directory = os.path.abspath(
                os.path.expanduser(working_directory)
            ) if working_directory else os.path.dirname(result["input_file"])
            if not os.path.isdir(working_directory):
                working_directory = os.path.dirname(result["input_file"])
            self.solverProcess.setWorkingDirectory(working_directory)
            self.solverProcess.setProgram(executable)
            self.solverProcess.setArguments(["-f", result["input_file"]])
            self.solverProcess.readyReadStandardOutput.connect(
                self._readSolverOutput
            )
            self.solverProcess.finished.connect(self._solverFinished)
            self.solverProcess.errorOccurred.connect(self._solverError)
            self.runBtn.setEnabled(False)
            self.cancelRunBtn.setEnabled(True)
            self.solverLog.appendPlainText(
                "Running: {} -f {}".format(executable, result["input_file"])
            )
            self.solverProcess.start()
            timeout = self._solverTimeoutSeconds()
            self.solverTimeoutTimer.start(timeout * 1000)
            self.solverLog.appendPlainText(
                "Timeout: {} s; working directory: {}".format(
                    timeout, working_directory
                )
            )
        except Exception as error:
            QtWidgets.QMessageBox.critical(self, "OOFEM Solver", str(error))
            traceback.print_exc()

    def cancelSolver(self, force=False):
        process = self.solverProcess
        if process is None or process.state() == QtCore.QProcess.NotRunning:
            return False
        self.solverTimeoutTimer.stop()
        self._solver_cancelled = True
        self.cancelRunBtn.setEnabled(False)
        self.solverLog.appendPlainText("Cancellation requested...")
        if force:
            process.kill()
        else:
            process.terminate()
            QtCore.QTimer.singleShot(2000, self._killSolverIfRunning)
        return True

    def _solverTimedOut(self):
        if self.solverProcess is None or (
            self.solverProcess.state() == QtCore.QProcess.NotRunning
        ):
            return
        self._solver_timed_out = True
        self.solverLog.appendPlainText(
            "Solver exceeded the configured {} s timeout.".format(
                self._solverTimeoutSeconds()
            )
        )
        self.cancelSolver()

    def _killSolverIfRunning(self):
        if self.solverProcess is not None and (
            self.solverProcess.state() != QtCore.QProcess.NotRunning
        ):
            self.solverLog.appendPlainText(
                "Solver did not terminate; killing the process."
            )
            self.solverProcess.kill()

    def _solverError(self, process_error):
        self.solverTimeoutTimer.stop()
        if self._solver_cancelled:
            return
        self.runBtn.setEnabled(True)
        self.cancelRunBtn.setEnabled(False)
        self.statusLabel.setText(
            "Could not start or continue OOFEM (process error {}).".format(
                process_error
            )
        )

    def _readSolverOutput(self):
        if self.solverProcess is None:
            return
        data = bytes(self.solverProcess.readAllStandardOutput()).decode(
            "utf-8", errors="replace"
        )
        if data:
            self._solver_output_buffer += data
            self.solverLog.moveCursor(self.solverLog.textCursor().End)
            self.solverLog.insertPlainText(data)
            self.solverLog.moveCursor(self.solverLog.textCursor().End)

    def _solverFinished(self, exit_code, exit_status):
        del exit_status
        self._readSolverOutput()
        self.solverTimeoutTimer.stop()
        self.runBtn.setEnabled(True)
        self.cancelRunBtn.setEnabled(False)

        if self._solver_timed_out:
            self.statusLabel.setText("OOFEM solve timed out.")
            self.exportSummaryLabel.setText(
                "Solve stopped after {} seconds; partial results were preserved.".format(
                    self._solverTimeoutSeconds()
                )
            )
            self.refreshResults()
            return

        if self._solver_cancelled:
            self.statusLabel.setText("OOFEM solve was cancelled.")
            self.exportSummaryLabel.setText(
                "Solve cancelled; partial result files were left untouched."
            )
            self.refreshResults()
            return

        output = self._solver_output_buffer.lower()
        has_summary = "error(s)" in output
        succeeded = exit_code == 0 and (
            not has_summary or "0 error(s)" in output
        )
        if succeeded:
            self.statusLabel.setText("OOFEM solve completed successfully.")
            self.exportSummaryLabel.setText(
                "Solve complete. Open the Postprocess tab to inspect results."
            )
        else:
            self.statusLabel.setText(
                "OOFEM solve failed (exit code {}). See solver output.".format(
                    exit_code
                )
            )
            QtWidgets.QMessageBox.critical(
                self,
                "OOFEM Solver",
                "OOFEM did not complete successfully. See the solver output log.",
            )
        self.refreshResults()
        if succeeded and self._autoOpenParaVis():
            if self._selectedResultPath(visualization_only=True):
                self.openSelectedResult()

    # ---------------------------
    # Postprocess
    # ---------------------------
    def refreshResults(self):
        self.resultList.clear()
        input_file = self.inputFileEdit.text().strip()
        if not input_file:
            return []
        from OOFEMSalomePlugin.OOFEMPost import discover_result_files

        paths = discover_result_files(input_file)
        for path in paths:
            item = QtWidgets.QListWidgetItem(os.path.basename(path))
            item.setToolTip(path)
            item.setData(Qt.UserRole, path)
            self.resultList.addItem(item)
        if paths:
            self.resultList.setCurrentRow(0)
        return paths

    def _selectedResultPath(self, visualization_only=False):
        from OOFEMSalomePlugin.OOFEMPost import preferred_visualization_file

        paths = self.refreshResults()
        current = self.resultList.currentItem()
        selected = current.data(Qt.UserRole) if current else None
        if visualization_only and (
            not selected or selected.lower().endswith(".out")
        ):
            selected = preferred_visualization_file(paths)
        return selected

    def openSelectedResult(self):
        try:
            path = self._selectedResultPath(visualization_only=True)
            if not path:
                raise RuntimeError(
                    "No VTK/PVD/MED result exists yet. Generate and run the VTK preset."
                )
            from OOFEMSalomePlugin.OOFEMModule import getModule
            from OOFEMSalomePlugin.OOFEMPost import open_in_paravis

            open_in_paravis(path, getModule().context)
            self.statusLabel.setText(
                "Opened {} in ParaVis.".format(os.path.basename(path))
            )
        except Exception as error:
            QtWidgets.QMessageBox.critical(self, "OOFEM Postprocess", str(error))
            traceback.print_exc()

    def convertSelectedResult(self):
        try:
            paths = self.refreshResults()
            current = self.resultList.currentItem()
            source = current.data(Qt.UserRole) if current else None
            if not source or not source.lower().endswith((".vtu", ".vtk")):
                source = next(
                    (
                        path
                        for path in paths
                        if path.lower().endswith((".vtu", ".vtk"))
                    ),
                    None,
                )
            if not source:
                raise RuntimeError("No single .vtu or .vtk result is available.")
            default_name = os.path.splitext(source)[0] + ".med"
            destination, _ = QtWidgets.QFileDialog.getSaveFileName(
                self, "Convert OOFEM VTK Result to MED", default_name, "MED Files (*.med)"
            )
            if not destination:
                return
            if not destination.lower().endswith(".med"):
                destination += ".med"

            from OOFEMSalomePlugin.OOFEMPost import convert_vtk_to_med

            converted = convert_vtk_to_med(source, destination)
            self.refreshResults()
            QtWidgets.QMessageBox.information(
                self, "MED Conversion", "Created:\n{}".format(converted)
            )
        except Exception as error:
            QtWidgets.QMessageBox.critical(self, "MED Conversion", str(error))
            traceback.print_exc()
