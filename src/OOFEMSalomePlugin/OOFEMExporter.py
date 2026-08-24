"""Validated SALOME mesh to OOFEM input writer."""

import os
import tempfile


try:
    import SMESH
except ImportError:
    SMESH = None

from OOFEMSalomePlugin.OOFEMModule import getModule


class OOFEMValidationError(ValueError):
    """Raised when the configured SALOME model cannot form a valid OOFEM input."""

    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__("\n".join(self.errors))


class OOFEMExporter:
    """Write a complete OOFEM input for the plugin's supported element families."""

    EXPECTED_NODE_COUNTS = {
        "truss1d": 2,
        "truss2d": 2,
        "truss3d": 2,
        "trplanestress2d": 3,
        "planestress2d": 4,
        "quad1planestrain": 4,
        "qplanestrain": 8,
        "ltrspace": 4,
        "lspace": 8,
        "qspace": 20,
    }

    # OOFEM local-boundary numbering, expressed as zero-based connectivity indices.
    # Verified against the FEI2dQuadLin/FEI2dQuadQuad/FEI3dHexaLin/FEI3dHexaQuad
    # computeLocalEdgeMapping/computeLocalSurfaceMapping implementations in oofem.
    BOUNDARY_NODE_INDICES = {
        "trplanestress2d": ((0, 1), (1, 2), (2, 0)),
        "planestress2d": ((0, 1), (1, 2), (2, 3), (3, 0)),
        "quad1planestrain": ((0, 1), (1, 2), (2, 3), (3, 0)),
        "qplanestrain": ((0, 1, 4), (1, 2, 5), (2, 3, 6), (3, 0, 7)),
        "ltrspace": ((0, 2, 1), (0, 1, 3), (1, 2, 3), (0, 3, 2)),
        "lspace": (
            (0, 3, 2, 1),
            (4, 5, 6, 7),
            (0, 1, 5, 4),
            (1, 2, 6, 5),
            (2, 3, 7, 6),
            (3, 0, 4, 7),
        ),
        "qspace": (
            (2, 1, 0, 3, 9, 8, 11, 10),
            (6, 7, 4, 5, 14, 15, 12, 13),
            (1, 5, 4, 0, 17, 12, 16, 8),
            (2, 6, 5, 1, 18, 13, 17, 9),
            (2, 3, 7, 6, 10, 19, 14, 18),
            (3, 0, 4, 7, 11, 16, 15, 19),
        ),
    }

    DEFAULT_SOLVER_SETTINGS = {
        "engng_model": "StaticStructural",
        "nsteps": 1,
        "vtk": True,
        "vtk_record": "vtkxml tstep_all domain_all primvars 1 1 cellvars 1 1",
        "nlgeom": False,
    }

    def __init__(
        self,
        mesh,
        elem_map,
        mat_map,
        bc_map,
        bc_templates,
        solver_settings=None,
        cross_sections=None,
        time_functions=None,
        analysis=None,
    ):
        self.mesh = mesh
        self.elem_map = dict(elem_map or {})
        self.mat_map = list(mat_map or [])
        self.bc_map = list(bc_map or [])
        self.cross_sections = list(cross_sections or [])
        self._uses_explicit_cross_sections = cross_sections is not None
        if time_functions is None:
            self.time_functions = [
                {
                    "id": "ltf-1",
                    "oofem_type": "constantfunction",
                    "params": {"f(t)": 1.0},
                }
            ]
        else:
            self.time_functions = list(time_functions)
        self.analysis = dict(analysis or {})
        self.bc_templates = {
            template["oofem_name"]: template for template in (bc_templates or [])
        }
        self.solver_settings = dict(self.DEFAULT_SOLVER_SETTINGS)
        if solver_settings:
            self.solver_settings.update(solver_settings)

        self.debug_console = None
        module = getModule()
        if module and hasattr(module, "debug_console"):
            self.debug_console = module.debug_console

        self.SALOME_TYPE_NAMES = self._build_salome_type_map()
        self._prepared = False
        self.last_result = None

    def _log(self, message):
        if self.debug_console:
            self.debug_console.log(message)
        else:
            print("OOFEMExporter log: {}".format(message))

    @staticmethod
    def _enum_value(value):
        return getattr(value, "_v", value)

    @staticmethod
    def _format_number(value):
        return "{:g}".format(float(value))

    def _build_salome_type_map(self):
        if not SMESH:
            return {}

        ui_name_map = {
            "Entity_Edge": "Segment",
            "Entity_Quad_Edge": "Segment",
            "Entity_Triangle": "Triangle",
            "Entity_Quad_Triangle": "Triangle",
            "Entity_BiQuad_Triangle": "Triangle",
            "Entity_Quadrangle": "Quadrangle",
            "Entity_Quad_Quadrangle": "Quadrangle8",
            "Entity_BiQuad_Quadrangle": "Quadrangle9",
            "Entity_Polygon": "Polygon",
            "Entity_Quad_Polygon": "Polygon",
            "Entity_Tetra": "Tetrahedron",
            "Entity_Quad_Tetra": "Tetrahedron",
            "Entity_Hexa": "Hexahedron",
            "Entity_Quad_Hexa": "Hexahedron20",
            "Entity_TriQuad_Hexa": "Hexahedron27",
            "Entity_Penta": "Pentahedron",
            "Entity_Quad_Penta": "Pentahedron",
            "Entity_BiQuad_Penta": "Pentahedron",
            "Entity_Pyramid": "Pyramid",
            "Entity_Quad_Pyramid": "Pyramid",
            "Entity_Polyhedra": "Polyhedron",
            "Entity_Quad_Polyhedra": "Polyhedron",
        }
        return {
            item._v: ui_name_map[item._n]
            for item in SMESH.EntityType._items
            if item._n in ui_name_map
        }

    def _node_group_type(self):
        return self._enum_value(getattr(SMESH, "NODE", 0))

    def _get_element_nodes(self, element_id):
        try:
            return list(self.mesh.GetElemNodes(element_id))
        except TypeError:
            return list(self.mesh.GetElemNodes(element_id, False))

    def _groups_by_name(self, errors):
        groups = {}
        for group in self.mesh.GetGroups():
            name = group.GetName()
            if name in groups:
                errors.append("Mesh group names must be unique; '{}' occurs more than once.".format(name))
            else:
                groups[name] = group
        return groups

    def _build_element_to_group_map(self):
        result = {}
        assignment_groups = set(self.group_to_mat)
        for group in self.mesh.GetGroups():
            if group.GetName() not in assignment_groups:
                continue
            if self._enum_value(group.GetType()) == self._node_group_type():
                continue
            for element_id in group.GetIDs():
                result.setdefault(element_id, []).append(group.GetName())
        return result

    def _get_oofem_element_type(self, element_id):
        geometry_type = self.mesh.GetElementGeomType(element_id)
        geometry_type_id = self._enum_value(geometry_type)
        salome_type_name = self.SALOME_TYPE_NAMES.get(geometry_type_id)
        if not salome_type_name:
            return "SalomeCell{}".format(geometry_type_id)

        overrides = {}
        for group_name in self.elem_to_groups.get(element_id, []):
            cross_section = self.group_to_cross_section.get(group_name)
            material = self.group_to_mat.get(group_name)
            override_map = (
                cross_section.get("element_mapping_override")
                if cross_section
                else None
            )
            if not override_map and material:
                override_map = material.get("element_mapping_override")
            if override_map and override_map.get(salome_type_name):
                overrides.setdefault(override_map[salome_type_name], []).append(group_name)
        if overrides:
            return next(iter(overrides))
        return self.elem_map.get(salome_type_name) or "Unmapped-{}".format(salome_type_name)

    def _domain_type(self):
        families = {
            self._get_oofem_element_type(element_id).lower()
            for element_id in self.element_ids
        }
        plane_stress = {name for name in families if "planestress" in name}
        plane_strain = {name for name in families if "planestrain" in name}
        spatial = {name for name in families if name in ("ltrspace", "lspace", "qspace", "truss3d")}
        if len([group for group in (plane_stress, plane_strain, spatial) if group]) > 1:
            raise OOFEMValidationError(
                ["Plane-stress, plane-strain, and 3D elements cannot be mixed in one OOFEM domain."]
            )
        if plane_stress:
            return "2dplanestress"
        if plane_strain:
            return "planestrain"
        if families and all("truss1d" in name for name in families):
            return "1dtruss"
        if families and all("truss2d" in name for name in families):
            return "2dtruss"
        return "3d"

    def _domain_dof_count(self):
        return {
            "1dtruss": 1,
            "2dtruss": 2,
            "2dplanestress": 2,
            "planestrain": 2,
            "3d": 3,
        }[self.domain_type]

    def _add_set(self, group_name, keyword, values):
        key = (group_name, keyword)
        if key in self._set_key_to_id:
            return self._set_key_to_id[key]
        values = list(values)
        set_id = len(self._groups_to_export) + 1
        self._set_key_to_id[key] = set_id
        self.group_name_to_set_id[group_name] = set_id
        self._groups_to_export.append((set_id, group_name, keyword, values))
        return set_id

    def _build_boundary_to_parent_map(self):
        boundary_map = {}
        for salome_element_id in self.element_ids:
            element_type = self._get_oofem_element_type(salome_element_id).lower()
            boundaries = self.BOUNDARY_NODE_INDICES.get(element_type, ())
            connectivity = self.element_connectivity[salome_element_id]
            for local_number, indices in enumerate(boundaries, start=1):
                nodes = tuple(sorted(connectivity[index] for index in indices))
                boundary_map.setdefault(nodes, []).append(
                    (self.element_id_map[salome_element_id], local_number)
                )
        return boundary_map

    def _surface_pairs(self, group, errors):
        pairs = []
        unmatched = []
        for boundary_id in group.GetIDs():
            try:
                key = tuple(sorted(self._get_element_nodes(boundary_id)))
            except Exception as error:
                errors.append(
                    "Could not read boundary element {} in group '{}': {}.".format(
                        boundary_id, group.GetName(), error
                    )
                )
                continue
            parents = self.boundary_to_parent_map.get(key)
            if not parents:
                unmatched.append(boundary_id)
                continue
            pairs.extend(parents)

        if unmatched:
            errors.append(
                "Boundary group '{}' contains {} element(s) that do not match a "
                "boundary of the material-assigned elements: {}.".format(
                    group.GetName(), len(unmatched), ", ".join(map(str, unmatched[:8]))
                )
            )
        flattened = []
        for element_id, boundary_number in sorted(set(pairs)):
            flattened.extend((element_id, boundary_number))
        return flattened

    @staticmethod
    def _coerce_float_list(value):
        if isinstance(value, str):
            value = value.replace(",", " ").split()
        if not isinstance(value, (list, tuple)):
            raise ValueError("expected a list of numbers")
        return [float(item) for item in value]

    def _prepare_assignments(self, groups, errors):
        self.group_to_mat = {}
        self.group_to_cross_section = {}
        self.materials_by_id = {}
        for material in self.mat_map:
            material_id = material.get("id")
            if not material_id:
                errors.append(
                    "Material '{}' has no internal ID.".format(
                        material.get("name", "Unnamed")
                    )
                )
                continue
            if material_id in self.materials_by_id:
                errors.append(
                    "Material ID '{}' is used more than once.".format(material_id)
                )
                continue
            self.materials_by_id[material_id] = material

        if self._uses_explicit_cross_sections:
            cross_section_ids = set()
            for cross_section in self.cross_sections:
                cross_section_name = cross_section.get("name", "Unnamed")
                cross_section_id = cross_section.get("id")
                if not cross_section_id:
                    errors.append(
                        "Cross section '{}' has no internal ID.".format(
                            cross_section_name
                        )
                    )
                    continue
                if cross_section_id in cross_section_ids:
                    errors.append(
                        "Cross section ID '{}' is used more than once.".format(
                            cross_section_id
                        )
                    )
                    continue
                cross_section_ids.add(cross_section_id)
                if str(cross_section.get("oofem_type", "")).lower() != "simplecs":
                    errors.append(
                        "Cross section '{}' uses unsupported type '{}'.".format(
                            cross_section_name, cross_section.get("oofem_type")
                        )
                    )
                    continue

                group_name = cross_section.get("assigned_group")
                material_id = cross_section.get("material_id")
                material = self.materials_by_id.get(material_id)
                if material is None:
                    errors.append(
                        "Cross section '{}' references missing material '{}'.".format(
                            cross_section_name, material_id
                        )
                    )
                    continue
                if not group_name:
                    errors.append(
                        "Cross section '{}' is not assigned to a mesh group.".format(
                            cross_section_name
                        )
                    )
                    continue
                if group_name in self.group_to_cross_section:
                    errors.append(
                        "Mesh group '{}' has more than one cross-section assignment.".format(
                            group_name
                        )
                    )
                    continue
                group = groups.get(group_name)
                if group is None:
                    errors.append(
                        "Cross-section group '{}' does not exist on the selected mesh.".format(
                            group_name
                        )
                    )
                    continue
                if self._enum_value(group.GetType()) == self._node_group_type():
                    errors.append(
                        "Cross section '{}' is assigned to a node group; use an element group.".format(
                            cross_section_name
                        )
                    )
                    continue
                self.group_to_cross_section[group_name] = cross_section
                self.group_to_mat[group_name] = material
            return

        for material in self.mat_map:
            group_name = material.get("assigned_group")
            if not group_name:
                errors.append(
                    "Material '{}' is not assigned to a mesh group.".format(
                        material.get("name", "Unnamed")
                    )
                )
                continue
            if group_name in self.group_to_mat:
                errors.append(
                    "Mesh group '{}' has more than one material assignment.".format(
                        group_name
                    )
                )
            self.group_to_mat[group_name] = material
            group = groups.get(group_name)
            if group is None:
                errors.append(
                    "Material group '{}' does not exist on the selected mesh.".format(
                        group_name
                    )
                )
            elif self._enum_value(group.GetType()) == self._node_group_type():
                errors.append(
                    "Material '{}' is assigned to a node group; use an element group.".format(
                        material.get("name", "Unnamed")
                    )
                )

    def _prepare_time_functions(self, errors):
        self._time_functions_to_export = []
        self.time_function_internal_id_to_oofem_id = {}
        for function in self.time_functions:
            function_id = function.get("id")
            function_name = function.get("name", "Unnamed")
            if not function_id:
                errors.append(
                    "Time function '{}' has no internal ID.".format(function_name)
                )
                continue
            if function_id in self.time_function_internal_id_to_oofem_id:
                errors.append(
                    "Time function ID '{}' is used more than once.".format(function_id)
                )
                continue

            function_type = str(function.get("oofem_type", "")).lower()
            parameters = function.get("params", {})
            try:
                if function_type == "constantfunction":
                    float(parameters.get("f(t)", parameters.get("value", 1.0)))
                elif function_type == "piecewiselinfunction":
                    times = parameters.get("t")
                    values = parameters.get("f(t)")
                    if times is None and values is None:
                        times = [parameters.get("t0", 0.0), parameters.get("t1", 1.0)]
                        values = [parameters.get("v0", 0.0), parameters.get("v1", 1.0)]
                    times = self._coerce_float_list(times)
                    values = self._coerce_float_list(values)
                    if len(times) < 2 or len(times) != len(values):
                        raise ValueError(
                            "t and f(t) must contain the same number of at least two values"
                        )
                    if any(right <= left for left, right in zip(times, times[1:])):
                        raise ValueError("time coordinates must be strictly increasing")
                else:
                    raise ValueError(
                        "unsupported OOFEM function type '{}'".format(
                            function.get("oofem_type")
                        )
                    )
            except (TypeError, ValueError) as error:
                errors.append(
                    "Time function '{}' is invalid: {}.".format(function_name, error)
                )
                continue

            record_id = len(self._time_functions_to_export) + 1
            self.time_function_internal_id_to_oofem_id[function_id] = record_id
            self._time_functions_to_export.append(function)

        if not self._time_functions_to_export:
            errors.append("At least one valid time function is required.")

    def _boundary_dofs_and_values(self, boundary_condition):
        bc_type = boundary_condition.get("oofem_type")
        parameters = boundary_condition.get("params", {})
        raw_dofs = parameters.get("dofs")
        if raw_dofs is None:
            raw_dofs = [parameters.get("dof", 1)]
        if not isinstance(raw_dofs, (list, tuple)):
            raw_dofs = [raw_dofs]

        value_key = "values" if bc_type == "Displacement" else "components"
        raw_values = parameters.get(value_key)
        if raw_values is None:
            raw_values = [parameters.get("val", 0.0)]
        if not isinstance(raw_values, (list, tuple)):
            raw_values = [raw_values]

        try:
            dofs = [int(item) for item in raw_dofs]
            values = [float(item) for item in raw_values]
        except (TypeError, ValueError):
            raise OOFEMValidationError(
                [
                    "Boundary condition '{}' has non-numeric DOFs or values.".format(
                        boundary_condition.get("name", "Unnamed")
                    )
                ]
            )
        if not dofs or len(dofs) != len(values):
            raise OOFEMValidationError(
                [
                    "Boundary condition '{}' needs equally sized non-empty DOF and value arrays.".format(
                        boundary_condition.get("name", "Unnamed")
                    )
                ]
            )
        if len(set(dofs)) != len(dofs):
            raise OOFEMValidationError(
                [
                    "Boundary condition '{}' contains a duplicate DOF.".format(
                        boundary_condition.get("name", "Unnamed")
                    )
                ]
            )
        dof_count = self._domain_dof_count()
        invalid = [dof for dof in dofs if dof < 1 or dof > dof_count]
        if invalid:
            raise OOFEMValidationError(
                [
                    "Boundary condition '{}' uses DOF {}, but domain '{}' has {} translational DOFs.".format(
                        boundary_condition.get("name", "Unnamed"),
                        invalid[0],
                        self.domain_type,
                        dof_count,
                    )
                ]
            )
        if bc_type == "SurfaceLoad":
            values_by_dof = dict(zip(dofs, values))
            dofs = list(range(1, dof_count + 1))
            values = [values_by_dof.get(dof, 0.0) for dof in dofs]
        return dofs, values

    def _analysis_record(self, module_count):
        parameters = dict(self.analysis.get("params") or {})
        configured_name = (
            self.analysis.get("oofem_type")
            or self.analysis.get("type")
            or self.solver_settings.get("engng_model", "StaticStructural")
        )
        key = str(configured_name).lower()
        aliases = {
            "staticstructural": "StaticStructural",
            "linearstatic": "LinearStatic",
            "linearstatics": "LinearStatic",
            "eigenvaluedynamic": "EigenValueDynamic",
        }
        model_name = aliases.get(key)
        if model_name is None:
            raise OOFEMValidationError(
                ["Unsupported OOFEM analysis type '{}'.".format(configured_name)]
            )

        fields = [model_name]
        if key in ("staticstructural", "linearstatic", "linearstatics"):
            nsteps = max(
                1,
                int(
                    parameters.get(
                        "nsteps", self.solver_settings.get("nsteps", 1)
                    )
                ),
            )
            fields.extend(("nsteps", str(nsteps)))
            if key == "staticstructural" and parameters.get("deltat") is not None:
                fields.extend(
                    ("deltat", self._format_number(parameters.get("deltat")))
                )
        else:
            nroot = max(1, int(parameters.get("nroot", 5)))
            rtolv = float(parameters.get("rtolv", 1.0e-6))
            fields.extend(
                ("nroot", str(nroot), "rtolv", self._format_number(rtolv))
            )
        if module_count:
            fields.extend(("nmodules", str(module_count)))
        return " ".join(fields)

    def _nonlinear_geometry_enabled(self):
        parameters = self.analysis.get("params") or {}
        return bool(parameters.get("nlgeom", self.solver_settings.get("nlgeom")))

    def _prepare_records(self):
        errors = []
        groups = self._groups_by_name(errors)

        self._prepare_assignments(groups, errors)
        self.elem_to_groups = self._build_element_to_group_map()
        self.element_ids = sorted(self.elem_to_groups)
        if not self.element_ids:
            errors.append("No elements belong to a material-assigned mesh group.")

        for element_id, group_names in self.elem_to_groups.items():
            if len(group_names) > 1:
                errors.append(
                    "Element {} belongs to overlapping material groups: {}.".format(
                        element_id, ", ".join(sorted(group_names))
                    )
                )

        self.element_connectivity = {}
        used_nodes = set()
        for element_id in self.element_ids:
            try:
                connectivity = self._get_element_nodes(element_id)
            except Exception as error:
                errors.append("Could not read element {} connectivity: {}.".format(element_id, error))
                continue
            element_type = self._get_oofem_element_type(element_id)
            expected = self.EXPECTED_NODE_COUNTS.get(element_type.lower())
            if expected is None:
                errors.append(
                    "Element {} maps to unsupported OOFEM type '{}'. Supported types: {}.".format(
                        element_id,
                        element_type,
                        ", ".join(sorted(self.EXPECTED_NODE_COUNTS)),
                    )
                )
            elif len(connectivity) != expected:
                errors.append(
                    "Element {} maps to {} ({} nodes) but its SALOME connectivity has {} nodes. "
                    "Use a matching linear element or an explicit supported mapping.".format(
                        element_id, element_type, expected, len(connectivity)
                    )
                )
            self.element_connectivity[element_id] = connectivity
            used_nodes.update(connectivity)

        if errors:
            raise OOFEMValidationError(errors)

        self.node_ids = sorted(used_nodes)
        self.node_id_map = {
            salome_id: oofem_id for oofem_id, salome_id in enumerate(self.node_ids, start=1)
        }
        self.element_id_map = {
            salome_id: oofem_id for oofem_id, salome_id in enumerate(self.element_ids, start=1)
        }
        self.domain_type = self._domain_type()
        self._prepare_time_functions(errors)
        try:
            self._analysis_record(0)
        except OOFEMValidationError as error:
            errors.extend(error.errors)
        except (TypeError, ValueError) as error:
            errors.append("Analysis parameters are invalid: {}.".format(error))

        self._materials_to_export = []
        seen_material_ids = set()
        for group_name in sorted(self.group_to_mat):
            material = self.group_to_mat[group_name]
            material_id = material.get("id")
            if not material_id:
                errors.append("Material '{}' has no internal ID.".format(material.get("name", "Unnamed")))
                continue
            if material_id not in seen_material_ids:
                seen_material_ids.add(material_id)
                self._materials_to_export.append(material)

        for material in self._materials_to_export:
            if str(material.get("oofem_type", "")).lower() != "ogdencompressiblemat":
                continue
            try:
                self._ogden_terms(material)
            except OOFEMValidationError as error:
                errors.extend(error.errors)

        self.mat_internal_id_to_oofem_id = {
            material["id"]: index
            for index, material in enumerate(self._materials_to_export, start=1)
        }

        self._groups_to_export = []
        self._set_key_to_id = {}
        self.group_name_to_set_id = {}
        self._cross_sections_to_export = []
        if self._uses_explicit_cross_sections:
            assignments = list(self.cross_sections)
        else:
            assignments = []
            for group_name in sorted(self.group_to_mat):
                material = self.group_to_mat[group_name]
                material_parameters = material.get("params", {})
                parameters = {}
                if material_parameters.get("A") is not None:
                    parameters["area"] = material_parameters.get("A")
                if material_parameters.get("t") is not None:
                    parameters["thick"] = material_parameters.get("t")
                assignments.append(
                    {
                        "id": "legacy-cs-{}".format(len(assignments) + 1),
                        "name": "{} cross section".format(
                            material.get("name", "Unnamed")
                        ),
                        "oofem_type": "SimpleCS",
                        "material_id": material.get("id"),
                        "assigned_group": group_name,
                        "params": parameters,
                    }
                )

        for cross_section in assignments:
            group_name = cross_section.get("assigned_group")
            material = self.group_to_mat.get(group_name)
            group = groups.get(group_name)
            if material is None or group is None:
                continue
            translated = [
                self.element_id_map[element_id]
                for element_id in group.GetIDs()
                if element_id in self.element_id_map
            ]
            if not translated:
                errors.append(
                    "Cross-section group '{}' has no supported exported elements.".format(
                        group_name
                    )
                )
                continue

            sample_element = next(
                (
                    element_id
                    for element_id in group.GetIDs()
                    if element_id in self.element_id_map
                ),
                None,
            )
            element_type = self._get_oofem_element_type(sample_element).lower()
            parameters = cross_section.get("params", {})
            required_parameter = None
            if "truss" in element_type:
                required_parameter = "area"
            elif "planestress" in element_type or "planestrain" in element_type:
                required_parameter = "thick"
            if required_parameter and parameters.get(required_parameter) is None:
                if (
                    not self._uses_explicit_cross_sections
                    or material.get("assigned_group") == group_name
                ):
                    cross_section = dict(cross_section)
                    parameters = dict(parameters)
                    parameters[required_parameter] = 1.0
                    cross_section["params"] = parameters
                else:
                    errors.append(
                        "Cross section '{}' requires a positive '{}' value for {} elements.".format(
                            cross_section.get("name", "Unnamed"),
                            required_parameter,
                            element_type,
                        )
                    )
                    continue

            valid_parameters = True
            for key, value in parameters.items():
                if value is None:
                    continue
                try:
                    numeric_value = float(value)
                except (TypeError, ValueError):
                    errors.append(
                        "Cross section '{}' parameter '{}' is not numeric.".format(
                            cross_section.get("name", "Unnamed"), key
                        )
                    )
                    valid_parameters = False
                    continue
                if key in ("area", "thick") and numeric_value <= 0.0:
                    errors.append(
                        "Cross section '{}' parameter '{}' must be positive.".format(
                            cross_section.get("name", "Unnamed"), key
                        )
                    )
                    valid_parameters = False
            if not valid_parameters:
                continue
            set_id = self._add_set(
                group_name, "elements", sorted(set(translated))
            )
            self._cross_sections_to_export.append(
                (cross_section, material, set_id)
            )

        self.boundary_to_parent_map = self._build_boundary_to_parent_map()
        self._boundary_conditions_to_export = []
        for boundary_condition in self.bc_map:
            group_name = boundary_condition.get("assigned_group")
            bc_name = boundary_condition.get("name", "Unnamed")
            template = self.bc_templates.get(boundary_condition.get("oofem_type"))
            group = groups.get(group_name)
            if template is None:
                errors.append("Boundary condition '{}' uses an unknown template.".format(bc_name))
                continue
            if group is None:
                errors.append("Boundary condition '{}' references missing group '{}'.".format(bc_name, group_name))
                continue

            apply_to = template.get("apply_to")
            group_type = self._enum_value(group.GetType())
            if apply_to == "nodes":
                if group_type != self._node_group_type():
                    errors.append("Boundary condition '{}' requires a node group.".format(bc_name))
                    continue
                outside = sorted(set(group.GetIDs()) - set(self.node_ids))
                if outside:
                    errors.append(
                        "Node group '{}' contains nodes outside the exported material domain: {}.".format(
                            group_name, ", ".join(map(str, outside[:8]))
                        )
                    )
                    continue
                values = sorted({self.node_id_map[node_id] for node_id in group.GetIDs()})
                set_id = self._add_set(group_name, "nodes", values)
            elif apply_to == "element_boundary":
                if group_type == self._node_group_type():
                    errors.append("Boundary condition '{}' requires a face/edge element group.".format(bc_name))
                    continue
                values = self._surface_pairs(group, errors)
                if not values:
                    continue
                # OOFEM keeps 2D element edges and 3D element surfaces in
                # different Set fields. ConstantEdgeLoad reads giveEdgeList(),
                # while ConstantSurfaceLoad reads giveBoundaryList(). Using
                # elementBoundaries for a 2D edge load is syntactically valid,
                # but silently applies no load in current OOFEM versions.
                set_keyword = (
                    "elementEdges"
                    if self.domain_type in ("2dplanestress", "planestrain")
                    else "elementBoundaries"
                )
                set_id = self._add_set(group_name, set_keyword, values)
            else:
                errors.append("Boundary condition '{}' has unsupported target '{}'.".format(bc_name, apply_to))
                continue
            try:
                dofs, component_values = self._boundary_dofs_and_values(
                    boundary_condition
                )
            except OOFEMValidationError as error:
                errors.extend(error.errors)
                continue
            default_time_function_id = (
                self._time_functions_to_export[0].get("id")
                if self._time_functions_to_export
                else None
            )
            function_id = (
                boundary_condition.get("time_function_id")
                or default_time_function_id
            )
            function_number = self.time_function_internal_id_to_oofem_id.get(
                function_id
            )
            if function_number is None:
                errors.append(
                    "Boundary condition '{}' references missing time function '{}'.".format(
                        bc_name, function_id
                    )
                )
                continue
            self._boundary_conditions_to_export.append(
                (
                    boundary_condition,
                    set_id,
                    apply_to,
                    dofs,
                    component_values,
                    function_number,
                )
            )

        if not self._cross_sections_to_export:
            errors.append("No cross section could be created from the material assignments.")
        if errors:
            raise OOFEMValidationError(errors)
        self._prepared = True

    def validate(self):
        self._prepare_records()
        return {
            "nodes": len(self.node_ids),
            "elements": len(self.element_ids),
            "materials": len(self._materials_to_export),
            "cross_sections": len(self._cross_sections_to_export),
            "boundary_conditions": len(self._boundary_conditions_to_export),
            "time_functions": len(self._time_functions_to_export),
            "analysis": (
                self.analysis.get("oofem_type")
                or self.analysis.get("type")
                or self.solver_settings.get("engng_model", "StaticStructural")
            ),
            "sets": len(self._groups_to_export),
            "domain": self.domain_type,
        }

    def _export_nodes(self, output):
        dimension = 2 if self.domain_type in ("2dplanestress", "planestrain", "2dtruss") else 3
        for salome_node_id in self.node_ids:
            coordinates = self.mesh.GetNodeXYZ(salome_node_id)
            values = " ".join(self._format_number(value) for value in coordinates[:dimension])
            output.write(
                "node {} coords {} {}\n".format(
                    self.node_id_map[salome_node_id], dimension, values
                )
            )

    def _export_elements(self, output):
        nlgeo_suffix = " nlgeo 1" if self._nonlinear_geometry_enabled() else ""
        for salome_element_id in self.element_ids:
            connectivity = [
                self.node_id_map[node_id]
                for node_id in self.element_connectivity[salome_element_id]
            ]
            output.write(
                "{} {} nodes {} {}{}\n".format(
                    self._get_oofem_element_type(salome_element_id),
                    self.element_id_map[salome_element_id],
                    len(connectivity),
                    " ".join(map(str, connectivity)),
                    nlgeo_suffix,
                )
            )

    def _export_cross_sections(self, output):
        output.write("\n# === CROSS SECTIONS ===\n")
        parameter_order = (
            "thick",
            "width",
            "area",
            "iy",
            "iz",
            "ik",
            "beamshearcoeff",
            "shearareay",
            "shearareaz",
            "drillstiffness",
            "reldrillstiffness",
        )
        for cross_section_id, (cross_section, material, set_id) in enumerate(
            self._cross_sections_to_export, start=1
        ):
            record = "SimpleCS {}".format(cross_section_id)
            parameters = cross_section.get("params", {})
            for key in parameter_order:
                value = parameters.get(key)
                if value is not None:
                    record += " {} {}".format(
                        key, self._format_number(value)
                    )
            material_id = self.mat_internal_id_to_oofem_id[material["id"]]
            output.write(
                "{} material {} set {}\n".format(
                    record, material_id, set_id
                )
            )

    def _write_isole_material(self, output, material, record_id):
        parameters = material.get("params", {})
        output.write(
            "IsoLE {} d {} E {} n {} tAlpha {}\n".format(
                record_id,
                self._format_number(parameters.get("d", 0.0)),
                self._format_number(parameters.get("E", 1.0)),
                self._format_number(parameters.get("nu", parameters.get("n", 0.0))),
                self._format_number(parameters.get("alpha", 0.0)),
            )
        )

    def _write_idm1_material(self, output, material, record_id):
        parameters = material.get("params", {})
        output.write(
            "idm1 {} d {} E {} n {} tAlpha {} equivstraintype {} damlaw {} e0 {} ef {}\n".format(
                record_id,
                self._format_number(parameters.get("d", 0.0)),
                self._format_number(parameters.get("E", 1.0)),
                self._format_number(parameters.get("nu", 0.2)),
                self._format_number(parameters.get("alpha", 0.0)),
                int(parameters.get("equivstraintype", 0)),
                int(parameters.get("damlaw", 0)),
                self._format_number(parameters.get("e0", 0.0001)),
                self._format_number(parameters.get("ef", 0.001)),
            )
        )

    def _write_misesmat_material(self, output, material, record_id):
        parameters = material.get("params", {})
        output.write(
            "misesmat {} d {} E {} n {} tAlpha {} sig0 {} H {} omega_crit {} a {}\n".format(
                record_id,
                self._format_number(parameters.get("d", 0.0)),
                self._format_number(parameters.get("E", 1.0)),
                self._format_number(parameters.get("nu", 0.3)),
                self._format_number(parameters.get("alpha", 0.0)),
                self._format_number(parameters.get("sig0", 1.0)),
                self._format_number(parameters.get("h", 0.0)),
                self._format_number(parameters.get("omega_crit", 0.0)),
                self._format_number(parameters.get("a", 0.0)),
            )
        )

    @staticmethod
    def _ogden_terms(material):
        parameters = material.get("params", {})
        material_name = material.get("name", "Unnamed")
        terms = []
        errors = []
        for index in (1, 2, 3):
            alpha_key = "alpha{}".format(index)
            mu_key = "mu{}".format(index)
            alpha_present = alpha_key in parameters and parameters[alpha_key] is not None
            mu_present = mu_key in parameters and parameters[mu_key] is not None

            if not alpha_present and not mu_present:
                if index == 1:
                    errors.append(
                        "Material '{}' requires Ogden pair 1 (alpha1 and mu1).".format(
                            material_name
                        )
                    )
                continue
            if alpha_present != mu_present:
                errors.append(
                    "Material '{}' has incomplete Ogden pair {}; alpha{} and mu{} "
                    "must both be provided.".format(
                        material_name, index, index, index
                    )
                )
                continue

            try:
                alpha = float(parameters[alpha_key])
                mu = float(parameters[mu_key])
            except (TypeError, ValueError):
                errors.append(
                    "Material '{}' Ogden pair {} must contain numeric alpha{} and mu{} values.".format(
                        material_name, index, index, index
                    )
                )
                continue

            if index > 1 and alpha == 0.0 and mu == 0.0:
                # Optional catalog defaults represent an unused term, not a
                # physical zero-exponent/zero-modulus contribution.
                continue
            if alpha == 0.0 or mu == 0.0:
                errors.append(
                    "Material '{}' has incomplete Ogden pair {}; alpha{} and mu{} "
                    "must both be non-zero, or both zero for an unused optional pair.".format(
                        material_name, index, index, index
                    )
                )
                continue
            terms.append((alpha, mu))

        if errors:
            raise OOFEMValidationError(errors)
        if not terms:
            raise OOFEMValidationError(
                [
                    "Material '{}' needs at least one non-zero Ogden (alpha, mu) term.".format(
                        material_name
                    )
                ]
            )
        return terms

    def _write_ogden_material(self, output, material, record_id):
        parameters = material.get("params", {})
        terms = self._ogden_terms(material)
        alphas = " ".join(self._format_number(a) for a, _ in terms)
        mus = " ".join(self._format_number(m) for _, m in terms)
        output.write(
            "ogdencompressiblemat {} d {} k {} alpha {} {} mu {} {}\n".format(
                record_id,
                self._format_number(parameters.get("d", 0.0)),
                self._format_number(parameters.get("k", 0.0)),
                len(terms),
                alphas,
                len(terms),
                mus,
            )
        )

    def _write_mooney_rivlin_material(self, output, material, record_id):
        parameters = material.get("params", {})
        output.write(
            "mooneyrivlincompressiblemat {} d {} k {} C1 {} C2 {}\n".format(
                record_id,
                self._format_number(parameters.get("d", 0.0)),
                self._format_number(parameters.get("k", 0.0)),
                self._format_number(parameters.get("c1", 0.0)),
                self._format_number(parameters.get("c2", 0.0)),
            )
        )

    MATERIAL_WRITERS = {
        "ElasticIsotropic3d": "_write_isole_material",
        "ElasticIsotropic2d": "_write_isole_material",
        "Truss": "_write_isole_material",
        "idm1": "_write_idm1_material",
        "misesmat": "_write_misesmat_material",
        "ogdencompressiblemat": "_write_ogden_material",
        "mooneyrivlincompressiblemat": "_write_mooney_rivlin_material",
    }

    def _export_materials(self, output):
        output.write("\n# === MATERIALS ===\n")
        for material in self._materials_to_export:
            template_name = material.get("oofem_type")
            writer_name = self.MATERIAL_WRITERS.get(template_name)
            if writer_name is None:
                raise OOFEMValidationError(
                    ["Material template '{}' has no OOFEM writer.".format(template_name)]
                )
            record_id = self.mat_internal_id_to_oofem_id[material["id"]]
            getattr(self, writer_name)(output, material, record_id)

    def _export_boundary_conditions(self, output):
        if not self._boundary_conditions_to_export:
            return
        output.write("\n# === BOUNDARY CONDITIONS ===\n")
        for record_id, (
            boundary_condition,
            set_id,
            apply_to,
            dofs,
            component_values,
            function_number,
        ) in enumerate(self._boundary_conditions_to_export, start=1):
            bc_type = boundary_condition["oofem_type"]
            dof_text = " ".join(map(str, dofs))
            value_text = " ".join(
                self._format_number(value) for value in component_values
            )
            if bc_type == "Displacement":
                output.write(
                    "BoundaryCondition {} loadTimeFunction {} dofs {} {} "
                    "values {} {} set {}\n".format(
                        record_id,
                        function_number,
                        len(dofs),
                        dof_text,
                        len(component_values),
                        value_text,
                        set_id,
                    )
                )
            elif bc_type == "NodalLoad":
                output.write(
                    "NodalLoad {} loadTimeFunction {} dofs {} {} "
                    "components {} {} set {}\n".format(
                        record_id,
                        function_number,
                        len(dofs),
                        dof_text,
                        len(component_values),
                        value_text,
                        set_id,
                    )
                )
            elif bc_type == "SurfaceLoad" and apply_to == "element_boundary":
                if self.domain_type in ("2dplanestress", "planestrain"):
                    record_name = "ConstantEdgeLoad"
                    load_type = " loadType 3"
                else:
                    record_name = "ConstantSurfaceLoad"
                    load_type = ""
                output.write(
                    "{} {} loadTimeFunction {} dofs {} {} "
                    "components {} {}{} set {}\n".format(
                        record_name,
                        record_id,
                        function_number,
                        len(dofs),
                        dof_text,
                        len(component_values),
                        value_text,
                        load_type,
                        set_id,
                    )
                )
            else:
                raise OOFEMValidationError(
                    ["Boundary condition '{}' has no OOFEM writer.".format(bc_type)]
                )

    def _export_time_functions(self, output):
        output.write("\n# === TIME FUNCTIONS ===\n")
        for record_id, function in enumerate(
            self._time_functions_to_export, start=1
        ):
            function_type = str(function.get("oofem_type", "")).lower()
            parameters = function.get("params", {})
            if function_type == "constantfunction":
                value = parameters.get("f(t)", parameters.get("value", 1.0))
                output.write(
                    "ConstantFunction {} f(t) {}\n".format(
                        record_id, self._format_number(value)
                    )
                )
                continue

            times = parameters.get("t")
            values = parameters.get("f(t)")
            if times is None and values is None:
                times = [parameters.get("t0", 0.0), parameters.get("t1", 1.0)]
                values = [parameters.get("v0", 0.0), parameters.get("v1", 1.0)]
            times = self._coerce_float_list(times)
            values = self._coerce_float_list(values)
            output.write(
                "PiecewiseLinFunction {} t {} {} f(t) {} {}\n".format(
                    record_id,
                    len(times),
                    " ".join(self._format_number(value) for value in times),
                    len(values),
                    " ".join(self._format_number(value) for value in values),
                )
            )

    def _export_sets(self, output):
        output.write("\n# === SETS ===\n")
        for set_id, group_name, keyword, values in self._groups_to_export:
            output.write("# SALOME group: {}\n".format(group_name))
            output.write(
                "Set {} {} {} {}\n".format(
                    set_id, keyword, len(values), " ".join(map(str, values))
                )
            )

    def _render_input(self, output, output_filename, analysis_record, vtk_enabled):
        output.write(output_filename + "\n")
        output.write("Generated by the OOFEM SALOME plugin\n")
        output.write(analysis_record + "\n")
        if vtk_enabled:
            output.write(
                self.solver_settings.get(
                    "vtk_record", self.DEFAULT_SOLVER_SETTINGS["vtk_record"]
                )
                + "\n"
            )
        output.write("domain {}\n".format(self.domain_type))
        output.write("OutputManager tstep_all dofman_all element_all\n")
        output.write(
            "ndofman {} nelem {} ncrosssect {} nmat {} nbc {} "
            "nic 0 nltf {} nset {}\n".format(
                len(self.node_ids),
                len(self.element_ids),
                len(self._cross_sections_to_export),
                len(self._materials_to_export),
                len(self._boundary_conditions_to_export),
                len(self._time_functions_to_export),
                len(self._groups_to_export),
            )
        )
        self._export_nodes(output)
        self._export_elements(output)
        self._export_cross_sections(output)
        self._export_materials(output)
        self._export_boundary_conditions(output)
        self._export_time_functions(output)
        self._export_sets(output)

    def export(self, filename):
        self._log("--- Starting OOFEM export to {} ---".format(filename))
        self._prepare_records()

        filename = os.path.abspath(filename)
        output_directory = self.solver_settings.get("output_directory")
        if output_directory:
            output_directory = os.path.abspath(
                os.path.expanduser(str(output_directory))
            )
            os.makedirs(output_directory, exist_ok=True)
            output_filename = os.path.join(
                output_directory,
                os.path.splitext(os.path.basename(filename))[0] + ".out",
            )
        else:
            output_filename = os.path.splitext(filename)[0] + ".out"
        vtk_enabled = bool(self.solver_settings.get("vtk", False))
        module_count = 1 if vtk_enabled else 0
        analysis_record = self._analysis_record(module_count)

        descriptor, temporary_filename = tempfile.mkstemp(
            prefix=".{}-".format(os.path.basename(filename)),
            suffix=".tmp",
            dir=os.path.dirname(filename),
            text=True,
        )
        try:
            output = os.fdopen(descriptor, "w", encoding="utf-8", newline="\n")
            descriptor = None
            with output:
                self._render_input(
                    output, output_filename, analysis_record, vtk_enabled
                )
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary_filename, filename)
            temporary_filename = None
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if temporary_filename is not None:
                try:
                    os.unlink(temporary_filename)
                except FileNotFoundError:
                    pass

        self.last_result = {
            "input_file": filename,
            "output_file": output_filename,
            "node_id_map": dict(self.node_id_map),
            "element_id_map": dict(self.element_id_map),
            "vtk_enabled": vtk_enabled,
        }
        self._log("--- Export finished successfully ---")
        return self.last_result
