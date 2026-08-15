"""Validated SALOME mesh to OOFEM input writer."""

import os


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
    ):
        self.mesh = mesh
        self.elem_map = dict(elem_map or {})
        self.mat_map = list(mat_map or [])
        self.bc_map = list(bc_map or [])
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
        material_groups = {
            material.get("assigned_group")
            for material in self.mat_map
            if material.get("assigned_group")
        }
        for group in self.mesh.GetGroups():
            if group.GetName() not in material_groups:
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
            material = self.group_to_mat.get(group_name)
            override_map = material.get("element_mapping_override") if material else None
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

    def _prepare_records(self):
        errors = []
        groups = self._groups_by_name(errors)

        self.group_to_mat = {}
        for material in self.mat_map:
            group_name = material.get("assigned_group")
            if not group_name:
                errors.append("Material '{}' is not assigned to a mesh group.".format(material.get("name", "Unnamed")))
                continue
            if group_name in self.group_to_mat:
                errors.append("Mesh group '{}' has more than one material assignment.".format(group_name))
            self.group_to_mat[group_name] = material
            group = groups.get(group_name)
            if group is None:
                errors.append("Material group '{}' does not exist on the selected mesh.".format(group_name))
            elif self._enum_value(group.GetType()) == self._node_group_type():
                errors.append("Material '{}' is assigned to a node group; use an element group.".format(material.get("name", "Unnamed")))

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

        self.mat_internal_id_to_oofem_id = {
            material["id"]: index
            for index, material in enumerate(self._materials_to_export, start=1)
        }

        self._groups_to_export = []
        self._set_key_to_id = {}
        self.group_name_to_set_id = {}
        self._cross_sections_to_export = []
        for material in self._materials_to_export:
            group_name = material["assigned_group"]
            group = groups[group_name]
            translated = [
                self.element_id_map[element_id]
                for element_id in group.GetIDs()
                if element_id in self.element_id_map
            ]
            if not translated:
                errors.append("Material group '{}' has no supported exported elements.".format(group_name))
                continue
            set_id = self._add_set(group_name, "elements", sorted(set(translated)))
            self._cross_sections_to_export.append((material, set_id))

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
                set_id = self._add_set(group_name, "elementBoundaries", values)
            else:
                errors.append("Boundary condition '{}' has unsupported target '{}'.".format(bc_name, apply_to))
                continue
            self._boundary_conditions_to_export.append((boundary_condition, set_id, apply_to))

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
            "boundary_conditions": len(self._boundary_conditions_to_export),
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
        nlgeo_suffix = " nlgeo 1" if self.solver_settings.get("nlgeom") else ""
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
        for cross_section_id, (material, set_id) in enumerate(
            self._cross_sections_to_export, start=1
        ):
            group_name = material["assigned_group"]
            sample_element = next(
                element_id
                for element_id, group_names in self.elem_to_groups.items()
                if group_name in group_names
            )
            element_type = self._get_oofem_element_type(sample_element).lower()
            parameters = material.get("params", {})
            record = "SimpleCS {}".format(cross_section_id)
            if "truss" in element_type:
                record += " area {}".format(self._format_number(parameters.get("A", 1.0)))
            elif "planestress" in element_type or "planestrain" in element_type:
                record += " thick {}".format(self._format_number(parameters.get("t", 1.0)))
            material_id = self.mat_internal_id_to_oofem_id[material["id"]]
            output.write("{} material {} set {}\n".format(record, material_id, set_id))

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

    def _write_ogden_material(self, output, material, record_id):
        parameters = material.get("params", {})
        terms = [
            (parameters.get("alpha1"), parameters.get("mu1")),
            (parameters.get("alpha2"), parameters.get("mu2")),
            (parameters.get("alpha3"), parameters.get("mu3")),
        ]
        terms = [(a, m) for a, m in terms if a is not None and m is not None]
        if not terms:
            raise OOFEMValidationError(
                [
                    "Material '{}' needs at least one Ogden (alpha, mu) term.".format(
                        material.get("name", "Unnamed")
                    )
                ]
            )
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
        dof_count = self._domain_dof_count()
        for record_id, (boundary_condition, set_id, apply_to) in enumerate(
            self._boundary_conditions_to_export, start=1
        ):
            bc_type = boundary_condition["oofem_type"]
            parameters = boundary_condition.get("params", {})
            dof = int(parameters.get("dof", 1))
            value = float(parameters.get("val", 0.0))
            if dof < 1 or dof > dof_count:
                raise OOFEMValidationError(
                    [
                        "Boundary condition '{}' uses DOF {}, but domain '{}' has {} translational DOFs.".format(
                            boundary_condition.get("name", "Unnamed"),
                            dof,
                            self.domain_type,
                            dof_count,
                        )
                    ]
                )
            if bc_type == "Displacement":
                output.write(
                    "BoundaryCondition {} loadTimeFunction 1 dofs 1 {} values 1 {} set {}\n".format(
                        record_id, dof, self._format_number(value), set_id
                    )
                )
            elif bc_type == "NodalLoad":
                output.write(
                    "NodalLoad {} loadTimeFunction 1 dofs 1 {} components 1 {} set {}\n".format(
                        record_id, dof, self._format_number(value), set_id
                    )
                )
            elif bc_type == "SurfaceLoad" and apply_to == "element_boundary":
                dofs = " ".join(map(str, range(1, dof_count + 1)))
                components = [0.0] * dof_count
                components[dof - 1] = value
                values = " ".join(self._format_number(item) for item in components)
                if self.domain_type in ("2dplanestress", "planestrain"):
                    output.write(
                        "ConstantEdgeLoad {} loadTimeFunction 1 dofs {} {} "
                        "components {} {} loadType 3 set {}\n".format(
                            record_id, dof_count, dofs, dof_count, values, set_id
                        )
                    )
                else:
                    output.write(
                        "ConstantSurfaceLoad {} loadTimeFunction 1 dofs {} {} "
                        "components {} {} set {}\n".format(
                            record_id, dof_count, dofs, dof_count, values, set_id
                        )
                    )
            else:
                raise OOFEMValidationError(
                    ["Boundary condition '{}' has no OOFEM writer.".format(bc_type)]
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

    def export(self, filename):
        self._log("--- Starting OOFEM export to {} ---".format(filename))
        self._prepare_records()

        filename = os.path.abspath(filename)
        output_filename = os.path.splitext(filename)[0] + ".out"
        vtk_enabled = bool(self.solver_settings.get("vtk", False))
        model_name = self.solver_settings.get("engng_model", "StaticStructural")
        if model_name not in ("StaticStructural",):
            raise OOFEMValidationError(
                ["Unsupported solver engineering model '{}'. Use StaticStructural.".format(model_name)]
            )
        nsteps = max(1, int(self.solver_settings.get("nsteps", 1)))
        module_count = 1 if vtk_enabled else 0

        with open(filename, "w") as output:
            output.write(output_filename + "\n")
            output.write("Generated by the OOFEM SALOME plugin\n")
            output.write(
                "{} nsteps {}{}\n".format(
                    model_name,
                    nsteps,
                    " nmodules {}".format(module_count) if module_count else "",
                )
            )
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
                "nic 0 nltf 1 nset {}\n".format(
                    len(self.node_ids),
                    len(self.element_ids),
                    len(self._cross_sections_to_export),
                    len(self._materials_to_export),
                    len(self._boundary_conditions_to_export),
                    len(self._groups_to_export),
                )
            )
            self._export_nodes(output)
            self._export_elements(output)
            self._export_cross_sections(output)
            self._export_materials(output)
            self._export_boundary_conditions(output)
            output.write("\nConstantFunction 1 f(t) 1.0\n")
            self._export_sets(output)

        self.last_result = {
            "input_file": filename,
            "output_file": output_filename,
            "node_id_map": dict(self.node_id_map),
            "element_id_map": dict(self.element_id_map),
            "vtk_enabled": vtk_enabled,
        }
        self._log("--- Export finished successfully ---")
        return self.last_result
