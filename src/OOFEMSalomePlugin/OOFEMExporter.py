"""Validated SALOME mesh to OOFEM input writer."""

import math
import os
import tempfile


try:
    import SMESH
except ImportError:
    SMESH = None

from OOFEMSalomePlugin.OOFEMModule import getModule
from OOFEMSalomePlugin.OOFEMContact import (
    canonical_contact,
    contact_element_spec,
    orient_contact_nodes,
)


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
    }
    ELEMENT_NLGEO_MODES = {"inherit", "on", "off"}

    # Engineering models this exporter can render, keyed by lower-case name.
    ANALYSIS_ALIASES = {
        "staticstructural": "StaticStructural",
        "nonlinearstatic": "NonLinearStatic",
        "linearstatic": "LinearStatic",
        "linearstatics": "LinearStatic",
        "eigenvaluedynamic": "EigenValueDynamic",
    }
    # Models driven by a step count rather than an eigenvalue count.
    STEPPED_ANALYSES = frozenset(
        {"staticstructural", "nonlinearstatic", "linearstatic", "linearstatics"}
    )
    # Stepped models that additionally accept a time-step length.
    TIME_STEPPED_ANALYSES = frozenset({"staticstructural", "nonlinearstatic"})
    # OOFEM defaults NonLinearStatic to indirect control, which then makes
    # steplength mandatory.  Direct (Newton-Raphson) load control is the
    # predictable default for a GUI-generated model.
    NONLINEAR_STATIC_DEFAULT_CONTROL_MODE = 1
    # (source_key, output_key, minimum, minimum_is_inclusive)
    NONLINEAR_STATIC_FLOAT_SETTINGS = (
        ("rtolv", "rtolv", 0.0, False),
        ("rtolf", "rtolf", 0.0, False),
        ("rtold", "rtold", 0.0, False),
        ("minsteplength", "minsteplength", 0.0, True),
    )
    # (source_key, output_key, minimum)
    NONLINEAR_STATIC_INTEGER_SETTINGS = (
        ("renumber", "renumber", 0),
        ("stiffmode", "stiffMode", 0),
        ("refloadmode", "refloadmode", 0),
        ("manrmsteps", "manrmsteps", 0),
        ("maxiter", "maxiter", 1),
        ("miniter", "miniter", 0),
        ("initialguess", "initialguess", 0),
        ("lstype", "lstype", 0),
        ("smtype", "smtype", 0),
    )
    # Read by the cylindrical arc-length method only (controlmode 0).
    ARC_LENGTH_FLOAT_SETTINGS = (
        ("steplength", "steplength", 0.0, False),
        ("initialsteplength", "initialsteplength", 0.0, False),
        ("psi", "psi", 0.0, True),
    )
    ARC_LENGTH_INTEGER_SETTINGS = (
        ("reqiterations", "reqIterations", 1),
        ("maxrestarts", "maxrestarts", 0),
    )

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
        initial_conditions=None,
        contacts=None,
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
        self.initial_conditions = list(initial_conditions or [])
        self.contacts = list(contacts or [])
        self.bc_templates = {
            self._boundary_type_key(template["oofem_name"]): template
            for template in (bc_templates or [])
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

    @staticmethod
    def _boundary_type_key(value):
        """Return the canonical, case-insensitive key for a load type."""
        key = str(value or "").strip().casefold()
        if key == "structtemperatureload":
            return "structuraltemperatureload"
        if key in ("displacement", "boundarycondition"):
            return "boundarycondition"
        return key

    @staticmethod
    def _coerce_dof_list(value):
        """Parse DOFs without silently truncating fractions or accepting bools."""
        if isinstance(value, str):
            value = value.replace(",", " ").split()
        elif not isinstance(value, (list, tuple)):
            value = [value]

        dofs = []
        for item in value:
            if isinstance(item, bool):
                raise ValueError("boolean DOF values are not allowed")
            if isinstance(item, int):
                dof = item
            elif isinstance(item, float):
                if not math.isfinite(item) or not item.is_integer():
                    raise ValueError("fractional or non-finite DOF value")
                dof = int(item)
            elif isinstance(item, str):
                try:
                    dof = int(item.strip(), 10)
                except (TypeError, ValueError):
                    raise ValueError("non-integer DOF value")
            else:
                raise ValueError("non-integer DOF value")
            dofs.append(dof)
        return dofs

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

    def _add_set(self, group_name, keyword, values, internal=False):
        namespace = "internal" if internal else "salome"
        key = (namespace, group_name, keyword)
        if key in self._set_key_to_id:
            return self._set_key_to_id[key]
        values = list(values)
        set_id = len(self._groups_to_export) + 1
        self._set_key_to_id[key] = set_id
        if not internal:
            self.group_name_to_set_id[group_name] = set_id
        self._groups_to_export.append((set_id, group_name, keyword, values))
        return set_id

    def _build_boundary_to_parent_map(self):
        boundary_map = {}
        self.boundary_oriented_nodes = {}
        for salome_element_id in self.element_ids:
            element_type = self._get_oofem_element_type(salome_element_id).lower()
            boundaries = self.BOUNDARY_NODE_INDICES.get(element_type, ())
            connectivity = self.element_connectivity[salome_element_id]
            for local_number, indices in enumerate(boundaries, start=1):
                oriented_nodes = tuple(connectivity[index] for index in indices)
                nodes = tuple(sorted(oriented_nodes))
                boundary_map.setdefault(nodes, []).append(
                    (self.element_id_map[salome_element_id], local_number)
                )
                self.boundary_oriented_nodes.setdefault(nodes, []).append(
                    oriented_nodes
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
            if len(parents) != 1:
                errors.append(
                    "Boundary element {} in group '{}' is shared by {} exported "
                    "material elements; a surface load requires an exterior "
                    "boundary and would otherwise be applied from every side.".format(
                        boundary_id, group.GetName(), len(parents)
                    )
                )
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

    def _contact_surface_for_group(self, group, reverse, errors):
        """Create or reuse contact elements and one StructuralFEContactSurface."""
        group_name = group.GetName()
        cache_key = (group_name, bool(reverse))
        existing = self._contact_surface_cache.get(cache_key)
        if existing is not None:
            return existing

        if self._enum_value(group.GetType()) == self._node_group_type():
            errors.append(
                "Contact boundary group '{}' must contain edge/face elements, "
                "not nodes.".format(group_name)
            )
            return None

        boundary_ids = sorted(set(group.GetIDs()))
        if not boundary_ids:
            errors.append("Contact boundary group '{}' is empty.".format(group_name))
            return None

        staged_elements = []
        surface_errors = []
        for boundary_id in boundary_ids:
            try:
                salome_nodes = self._get_element_nodes(boundary_id)
            except Exception as error:
                surface_errors.append(
                    "Could not read contact boundary element {} in group '{}': {}.".format(
                        boundary_id, group_name, error
                    )
                )
                continue

            missing_nodes = sorted(
                node_id
                for node_id in salome_nodes
                if node_id not in self.node_id_map
            )
            if missing_nodes:
                surface_errors.append(
                    "Contact boundary element {} in group '{}' contains nodes outside "
                    "the exported material domain: {}.".format(
                        boundary_id,
                        group_name,
                        ", ".join(map(str, missing_nodes[:8])),
                    )
                )
                continue

            boundary_key = tuple(sorted(salome_nodes))
            parents = self.boundary_to_parent_map.get(boundary_key, ())
            if not parents:
                surface_errors.append(
                    "Contact boundary element {} in group '{}' does not match a "
                    "boundary of an exported material element.".format(
                        boundary_id, group_name
                    )
                )
                continue
            if len(parents) != 1:
                surface_errors.append(
                    "Contact boundary element {} in group '{}' is shared by {} "
                    "exported material elements; contact requires an exterior boundary.".format(
                        boundary_id, group_name, len(parents)
                    )
                )
                continue

            # A standalone SMESH boundary element may have arbitrary local
            # connectivity. Use the owning continuum element's verified local
            # facet order so normals are consistent and outward-facing for a
            # valid parent element. Explicit reverse options are applied next.
            parent_facets = self.boundary_oriented_nodes.get(boundary_key, ())
            if len(parent_facets) != 1:
                surface_errors.append(
                    "Contact boundary element {} in group '{}' has ambiguous "
                    "parent-facet orientation.".format(boundary_id, group_name)
                )
                continue
            salome_nodes = parent_facets[0]

            try:
                keyword, nip = contact_element_spec(
                    self.domain_type, len(salome_nodes)
                )
                connectivity = orient_contact_nodes(
                    [self.node_id_map[node_id] for node_id in salome_nodes],
                    reverse,
                )
            except ValueError as error:
                surface_errors.append(
                    "Contact boundary element {} in group '{}': {}.".format(
                        boundary_id, group_name, error
                    )
                )
                continue

            record_id = (
                len(self.element_ids)
                + len(self._contact_elements_to_export)
                + len(staged_elements)
                + 1
            )
            staged_elements.append(
                {
                    "id": record_id,
                    "keyword": keyword,
                    "connectivity": connectivity,
                    "nip": nip,
                    "salome_boundary_id": boundary_id,
                }
            )

        if surface_errors:
            errors.extend(surface_errors)
            return None
        if not staged_elements:
            errors.append(
                "Contact boundary group '{}' has no supported contact elements.".format(
                    group_name
                )
            )
            return None

        self._contact_elements_to_export.extend(staged_elements)
        internal_group_name = "__contact_surface__:{}:{}".format(
            group_name, "reversed" if reverse else "forward"
        )
        set_id = self._add_set(
            internal_group_name,
            "elements",
            [record["id"] for record in staged_elements],
            internal=True,
        )
        surface_id = len(self._contact_surfaces_to_export) + 1
        if set_id != surface_id:
            errors.append(
                "Internal contact export error: surface {} must use Set {}, got Set {}.".format(
                    surface_id, surface_id, set_id
                )
            )
            return None
        self._contact_surfaces_to_export.append(
            {
                "id": surface_id,
                "set_id": set_id,
                "group_name": group_name,
                "reverse": bool(reverse),
            }
        )
        self._contact_surface_cache[cache_key] = surface_id
        return surface_id

    def _prepare_contacts(self, groups, errors):
        self._contacts_to_export = []
        self._contact_conditions_to_export = []
        self._contact_elements_to_export = []
        self._contact_surfaces_to_export = []
        self._contact_surface_cache = {}
        self._contact_cross_section_id = None
        self._contact_material_id = None
        if not self.contacts:
            return

        configured_analysis = (
            self.analysis.get("oofem_type")
            or self.analysis.get("type")
            or self.solver_settings.get("engng_model", "StaticStructural")
        )
        if str(configured_analysis).strip().casefold() != "staticstructural":
            errors.append(
                "Structural contact requires the StaticStructural engineering model."
            )
        seen_ids = set()
        seen_physical_pairs = []
        for source_contact in self.contacts:
            try:
                contact = canonical_contact(source_contact, self.domain_type)
            except ValueError as error:
                errors.append(str(error) + ".")
                continue

            contact_id = contact.get("id")
            if not isinstance(contact_id, str) or not contact_id.strip():
                errors.append(
                    "Contact '{}' has no internal ID.".format(contact["name"])
                )
                continue
            if contact_id in seen_ids:
                errors.append(
                    "Contact ID '{}' is used more than once.".format(contact_id)
                )
                continue
            seen_ids.add(contact_id)

            master_group = groups.get(contact["master_group"])
            slave_group = groups.get(contact["slave_group"])
            if master_group is None:
                errors.append(
                    "Contact '{}' references missing master boundary group '{}'.".format(
                        contact["name"], contact["master_group"]
                    )
                )
            if slave_group is None:
                errors.append(
                    "Contact '{}' references missing slave boundary group '{}'.".format(
                        contact["name"], contact["slave_group"]
                    )
                )
            if master_group is None or slave_group is None:
                continue

            try:
                master_facets = {
                    tuple(sorted(self._get_element_nodes(element_id)))
                    for element_id in master_group.GetIDs()
                }
                slave_facets = {
                    tuple(sorted(self._get_element_nodes(element_id)))
                    for element_id in slave_group.GetIDs()
                }
            except Exception:
                # The surface builder below reports the precise unreadable
                # element and group; do not duplicate that diagnostic here.
                master_facets = set()
                slave_facets = set()
            overlapping_facets = master_facets & slave_facets
            if overlapping_facets:
                errors.append(
                    "Contact '{}' master and slave groups contain the same "
                    "boundary facet(s); self-contact is not supported.".format(
                        contact["name"]
                    )
                )
                continue

            duplicate_name = None
            for previous_master, previous_slave, previous_name in (
                seen_physical_pairs
            ):
                same_direction = bool(master_facets & previous_master) and bool(
                    slave_facets & previous_slave
                )
                reverse_direction = bool(master_facets & previous_slave) and bool(
                    slave_facets & previous_master
                )
                if same_direction or reverse_direction:
                    duplicate_name = previous_name
                    break
            if duplicate_name is not None:
                errors.append(
                    "Contact '{}' overlaps physical pair '{}'; stacking contact "
                    "conditions would duplicate the penalty contribution. Use "
                    "one pair and its two-pass option instead.".format(
                        contact["name"], duplicate_name
                    )
                )
                continue

            master_surface = self._contact_surface_for_group(
                master_group,
                contact["params"]["reverse_master"],
                errors,
            )
            slave_surface = self._contact_surface_for_group(
                slave_group,
                contact["params"]["reverse_slave"],
                errors,
            )
            if master_surface is None or slave_surface is None:
                continue
            seen_physical_pairs.append(
                (master_facets, slave_facets, contact["name"])
            )

            default_time_function_id = (
                self._time_functions_to_export[0].get("id")
                if self._time_functions_to_export
                else None
            )
            function_id = contact.get("time_function_id") or default_time_function_id
            function_number = self.time_function_internal_id_to_oofem_id.get(
                function_id
            )
            if function_number is None:
                errors.append(
                    "Contact '{}' references missing time function '{}'.".format(
                        contact["name"], function_id
                    )
                )
                continue

            self._contacts_to_export.append(contact)
            self._contact_conditions_to_export.append(
                (contact, master_surface, slave_surface, function_number)
            )
            if contact["params"]["two_pass"]:
                self._contact_conditions_to_export.append(
                    (contact, slave_surface, master_surface, function_number)
                )

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

                element_options = cross_section.get("element_options")
                if element_options is None:
                    element_options = {}
                if not isinstance(element_options, dict):
                    errors.append(
                        "Cross section '{}' element_options must be an object.".format(
                            cross_section_name
                        )
                    )
                else:
                    nlgeo_mode = str(
                        element_options.get("nlgeo", "inherit")
                    ).strip().casefold()
                    if nlgeo_mode not in self.ELEMENT_NLGEO_MODES:
                        errors.append(
                            "Cross section '{}' has invalid element nlgeo mode "
                            "'{}'; use inherit, on, or off.".format(
                                cross_section_name,
                                element_options.get("nlgeo"),
                            )
                        )

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
        bc_type_key = self._boundary_type_key(bc_type)
        parameters = boundary_condition.get("params") or {}
        boundary_name = boundary_condition.get("name", "Unnamed")
        if not isinstance(parameters, dict):
            raise OOFEMValidationError(
                [
                    "Boundary condition '{}' parameters must be an object.".format(
                        boundary_name
                    )
                ]
            )

        if bc_type_key == "structuraltemperatureload":
            try:
                values = self._coerce_float_list(parameters.get("components"))
            except (TypeError, ValueError) as error:
                raise OOFEMValidationError(
                    [
                        "Boundary condition '{}' has invalid temperature "
                        "components: {}.".format(boundary_name, error)
                    ]
                )
            if any(not math.isfinite(value) for value in values):
                raise OOFEMValidationError(
                    [
                        "Boundary condition '{}' temperature components must "
                        "be finite numbers.".format(boundary_name)
                    ]
                )
            if len(values) not in (1, 2):
                raise OOFEMValidationError(
                    [
                        "Boundary condition '{}' needs one temperature "
                        "increment component and at most one optional "
                        "through-thickness gradient component.".format(
                            boundary_name
                        )
                    ]
                )
            if len(values) == 2 and values[1] != 0.0:
                raise OOFEMValidationError(
                    [
                        "Boundary condition '{}' uses a non-zero structural "
                        "temperature-gradient component, which is not supported "
                        "by the plugin's current truss/continuum element families.".format(
                            boundary_name
                        )
                    ]
                )
            if len(values) == 1:
                values.append(0.0)
            return [], values

        raw_values = parameters.get("components")
        if bc_type_key in ("boundarycondition", "displacement"):
            raw_values = parameters.get("values")
        if raw_values is None and "val" in parameters:
            raw_values = [parameters.get("val")]
        if raw_values is None and bc_type_key == "deadweight":
            raise OOFEMValidationError(
                [
                    "Boundary condition '{}' needs body-load components.".format(
                        boundary_name
                    )
                ]
            )
        if raw_values is None:
            raw_values = [0.0]
        if not isinstance(raw_values, (list, tuple)):
            raw_values = [raw_values]

        raw_dofs = parameters.get("dofs")
        if raw_dofs is None:
            if (
                bc_type_key == "deadweight"
                and len(raw_values) == self._domain_dof_count()
            ):
                raw_dofs = list(range(1, self._domain_dof_count() + 1))
            else:
                raw_dofs = [parameters.get("dof", 1)]
        try:
            dofs = self._coerce_dof_list(raw_dofs)
        except (TypeError, ValueError):
            raise OOFEMValidationError(
                [
                    "Boundary condition '{}' DOFs must be integers; boolean "
                    "and fractional values are not allowed.".format(
                        boundary_name
                    )
                ]
            )
        try:
            values = [float(item) for item in raw_values]
        except (TypeError, ValueError):
            raise OOFEMValidationError(
                [
                    "Boundary condition '{}' values must be numeric.".format(
                        boundary_name
                    )
                ]
            )
        if any(not math.isfinite(value) for value in values):
            raise OOFEMValidationError(
                [
                    "Boundary condition '{}' values must be finite numbers.".format(
                        boundary_name
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
        if bc_type_key in ("surfaceload", "deadweight"):
            values_by_dof = dict(zip(dofs, values))
            dofs = list(range(1, dof_count + 1))
            values = [values_by_dof.get(dof, 0.0) for dof in dofs]
        return dofs, values

    def _validate_element_load_materials(self, boundary_condition, group):
        load_key = self._boundary_type_key(boundary_condition.get("oofem_type"))
        if load_key == "deadweight":
            parameter_key = "d"
            requirement = "density parameter 'd' must be a positive finite number"
            validator = lambda value: value > 0.0
        elif load_key == "structuraltemperatureload":
            parameter_key = "alpha"
            requirement = (
                "thermal expansion parameter 'alpha' must be a non-zero finite number"
            )
            validator = lambda value: value != 0.0
        else:
            return []

        errors = []
        checked_materials = set()
        for element_id in group.GetIDs():
            for assignment_group in self.elem_to_groups.get(element_id, []):
                material = self.group_to_mat.get(assignment_group)
                if material is None:
                    continue
                material_marker = (
                    material.get("id")
                    or assignment_group
                )
                if material_marker in checked_materials:
                    continue
                checked_materials.add(material_marker)
                parameters = material.get("params") or {}
                raw_value = (
                    parameters.get(parameter_key)
                    if isinstance(parameters, dict)
                    else None
                )
                try:
                    numeric_value = float(raw_value)
                except (TypeError, ValueError, OverflowError):
                    numeric_value = None
                if (
                    isinstance(raw_value, bool)
                    or numeric_value is None
                    or not math.isfinite(numeric_value)
                    or not validator(numeric_value)
                ):
                    errors.append(
                        "Boundary condition '{}' targets element {} with material "
                        "'{}'; its {}.".format(
                            boundary_condition.get("name", "Unnamed"),
                            element_id,
                            material.get("name") or material_marker,
                            requirement,
                        )
                    )
        return errors

    def _prepare_initial_conditions(self, groups, errors):
        self._initial_conditions_to_export = []
        used_ids = set()
        assigned_node_dofs = {}
        prescribed_node_dofs = {}
        allowed_modes = ("u", "v", "a")
        domain_dof_count = self._domain_dof_count()
        configured_analysis = (
            self.analysis.get("oofem_type")
            or self.analysis.get("type")
            or self.solver_settings.get("engng_model", "StaticStructural")
        )
        analysis_key = str(configured_analysis or "").strip().casefold()
        non_transient_structural_analyses = {
            "staticstructural",
            "nonlinearstatic",
            "linearstatic",
            "linearstatics",
            "eigenvaluedynamic",
        }

        for boundary_condition, _set_id, _apply_to, dofs, _, _ in (
            self._boundary_conditions_to_export
        ):
            if (
                self._boundary_type_key(boundary_condition.get("oofem_type"))
                not in ("boundarycondition", "displacement")
            ):
                continue
            group = groups.get(boundary_condition.get("assigned_group"))
            if group is None:
                continue
            for salome_node_id in group.GetIDs():
                oofem_node_id = self.node_id_map.get(salome_node_id)
                if oofem_node_id is None:
                    continue
                for dof in dofs:
                    prescribed_node_dofs[(oofem_node_id, dof)] = (
                        boundary_condition.get("name", "Unnamed")
                    )

        for initial_condition in self.initial_conditions:
            if not isinstance(initial_condition, dict):
                errors.append("Initial condition records must be objects.")
                continue

            condition_name = initial_condition.get("name", "Unnamed")
            condition_id = initial_condition.get("id")
            if not condition_id:
                errors.append(
                    "Initial condition '{}' has no internal ID.".format(
                        condition_name
                    )
                )
                continue
            if condition_id in used_ids:
                errors.append(
                    "Initial condition ID '{}' is used more than once.".format(
                        condition_id
                    )
                )
                continue
            used_ids.add(condition_id)

            condition_type = str(
                initial_condition.get("oofem_type", "InitialCondition")
            ).lower()
            if condition_type != "initialcondition":
                errors.append(
                    "Initial condition '{}' uses unsupported type '{}'.".format(
                        condition_name, initial_condition.get("oofem_type")
                    )
                )
                continue

            group_name = initial_condition.get("assigned_group")
            group = groups.get(group_name)
            if group is None:
                errors.append(
                    "Initial condition '{}' references missing group '{}'.".format(
                        condition_name, group_name
                    )
                )
                continue
            if self._enum_value(group.GetType()) != self._node_group_type():
                errors.append(
                    "Initial condition '{}' requires a node group.".format(
                        condition_name
                    )
                )
                continue

            outside = sorted(set(group.GetIDs()) - set(self.node_ids))
            if outside:
                errors.append(
                    "Initial-condition node group '{}' contains nodes outside "
                    "the exported material domain: {}.".format(
                        group_name, ", ".join(map(str, outside[:8]))
                    )
                )
                continue

            parameters = initial_condition.get("params") or {}
            if not isinstance(parameters, dict):
                errors.append(
                    "Initial condition '{}' parameters must be an object.".format(
                        condition_name
                    )
                )
                continue
            raw_dofs = parameters.get("dofs")
            if raw_dofs is None and "dof" in parameters:
                raw_dofs = [parameters.get("dof")]
            try:
                dofs = self._coerce_dof_list(raw_dofs)
            except (TypeError, ValueError):
                errors.append(
                    "Initial condition '{}' DOFs must be integers; boolean "
                    "and fractional values are not allowed.".format(
                        condition_name
                    )
                )
                continue
            if not dofs:
                errors.append(
                    "Initial condition '{}' needs a non-empty integer DOF list.".format(
                        condition_name
                    )
                )
                continue
            if len(set(dofs)) != len(dofs):
                errors.append(
                    "Initial condition '{}' contains a duplicate DOF.".format(
                        condition_name
                    )
                )
                continue
            invalid_dofs = [
                dof for dof in dofs if dof < 1 or dof > domain_dof_count
            ]
            if invalid_dofs:
                errors.append(
                    "Initial condition '{}' uses DOF {}, but domain '{}' has {} "
                    "translational DOFs.".format(
                        condition_name,
                        invalid_dofs[0],
                        self.domain_type,
                        domain_dof_count,
                    )
                )
                continue

            raw_conditions = parameters.get("conditions")
            if raw_conditions is None and "value" in parameters:
                raw_conditions = {
                    str(parameters.get("mode", "u")).lower(): parameters["value"]
                }
            if not isinstance(raw_conditions, dict) or not raw_conditions:
                errors.append(
                    "Initial condition '{}' needs a non-empty conditions "
                    "object.".format(condition_name)
                )
                continue

            unknown_modes = sorted(set(raw_conditions) - set(allowed_modes))
            if unknown_modes:
                errors.append(
                    "Initial condition '{}' uses unsupported value mode '{}'; "
                    "supported modes are u, v, and a.".format(
                        condition_name, unknown_modes[0]
                    )
                )
                continue
            try:
                conditions = [
                    (mode, float(raw_conditions[mode]))
                    for mode in allowed_modes
                    if mode in raw_conditions
                ]
            except (TypeError, ValueError):
                errors.append(
                    "Initial condition '{}' has a non-numeric condition value.".format(
                        condition_name
                    )
                )
                continue
            if any(not math.isfinite(value) for _mode, value in conditions):
                errors.append(
                    "Initial condition '{}' values must be finite numbers.".format(
                        condition_name
                    )
                )
                continue
            if (
                analysis_key in non_transient_structural_analyses
                and any(value != 0.0 for _mode, value in conditions)
            ):
                errors.append(
                    "Initial condition '{}' contains non-zero values, but analysis "
                    "'{}' has no transient dynamics; OOFEM would ignore them.".format(
                        condition_name, configured_analysis
                    )
                )
                continue

            translated_nodes = sorted(
                {self.node_id_map[node_id] for node_id in group.GetIDs()}
            )
            if not translated_nodes:
                errors.append(
                    "Initial condition '{}' targets an empty node group.".format(
                        condition_name
                    )
                )
                continue
            collisions = [
                (node_id, dof, assigned_node_dofs[(node_id, dof)])
                for node_id in translated_nodes
                for dof in dofs
                if (node_id, dof) in assigned_node_dofs
            ]
            if collisions:
                node_id, dof, previous_name = collisions[0]
                errors.append(
                    "Initial condition '{}' overlaps '{}' on exported node {} "
                    "DOF {}; one OOFEM DOF can have only one initial "
                    "condition.".format(
                        condition_name, previous_name, node_id, dof
                    )
                )
                continue
            prescribed_collisions = [
                (node_id, dof, prescribed_node_dofs[(node_id, dof)])
                for node_id in translated_nodes
                for dof in dofs
                if (node_id, dof) in prescribed_node_dofs
            ]
            if prescribed_collisions:
                node_id, dof, boundary_name = prescribed_collisions[0]
                errors.append(
                    "Initial condition '{}' overlaps prescribed displacement "
                    "'{}' on exported node {} DOF {}; the prescribed "
                    "displacement would take precedence.".format(
                        condition_name, boundary_name, node_id, dof
                    )
                )
                continue
            for node_id in translated_nodes:
                for dof in dofs:
                    assigned_node_dofs[(node_id, dof)] = condition_name

            set_id = self._add_set(group_name, "nodes", translated_nodes)
            self._initial_conditions_to_export.append(
                (initial_condition, set_id, dofs, conditions)
            )

    def _analysis_record(self, module_count):
        parameters = dict(self.analysis.get("params") or {})
        configured_name = (
            self.analysis.get("oofem_type")
            or self.analysis.get("type")
            or self.solver_settings.get("engng_model", "StaticStructural")
        )
        key = str(configured_name).lower()
        model_name = self.ANALYSIS_ALIASES.get(key)
        if model_name is None:
            raise OOFEMValidationError(
                ["Unsupported OOFEM analysis type '{}'.".format(configured_name)]
            )

        fields = [model_name]
        if key in self.STEPPED_ANALYSES:
            nsteps = max(
                1,
                int(
                    parameters.get(
                        "nsteps", self.solver_settings.get("nsteps", 1)
                    )
                ),
            )
            fields.extend(("nsteps", str(nsteps)))
            if key in self.TIME_STEPPED_ANALYSES:
                deltat = self._analysis_setting(parameters, "deltat")
                if deltat is not None:
                    fields.extend(("deltat", self._format_number(deltat)))
        else:
            nroot = max(1, int(parameters.get("nroot", 5)))
            rtolv = float(parameters.get("rtolv", 1.0e-6))
            fields.extend(
                ("nroot", str(nroot), "rtolv", self._format_number(rtolv))
            )
        if module_count:
            fields.extend(("nmodules", str(module_count)))
        if key == "staticstructural":
            fields.extend(
                self._solver_control_fields(
                    parameters,
                    (("rtolv", "rtolv", 0.0, False),),
                    (
                        ("renumber", "renumber", 0),
                        ("stiffmode", "stiffMode", 0),
                        ("manrmsteps", "manrmsteps", 0),
                        ("maxiter", "maxiter", 1),
                        ("initialguess", "initialguess", 0),
                        ("smtype", "smtype", 0),
                    ),
                )
            )
        elif key == "nonlinearstatic":
            fields.extend(self._nonlinear_static_control_fields(parameters))
        return " ".join(fields)

    def _analysis_setting(self, parameters, key, output_key=None):
        """Resolve one analysis parameter, falling back to the solver preset.

        The OOFEM-cased ``output_key`` is accepted as an alternative name at
        both levels so hand-edited projects and presets keep working, while an
        explicit project parameter always wins over the selected preset.
        """
        keys = (key,) if output_key in (None, key) else (key, output_key)
        for source in (parameters, self.solver_settings):
            for candidate in keys:
                if source.get(candidate) is not None:
                    return source[candidate]
        return None

    def _solver_control_fields(self, parameters, float_settings, integer_settings):
        """Render validated numerical-control fields for an engineering model.

        ``float_settings`` entries are ``(source_key, output_key, minimum,
        inclusive)`` and ``integer_settings`` entries are ``(source_key,
        output_key, minimum)``.  Both accept the OOFEM-cased output key as an
        alternative source key so hand-edited projects keep working.
        """
        fields = []
        for source_key, output_key, minimum, inclusive in float_settings:
            value = self._analysis_setting(parameters, source_key, output_key)
            if value is None:
                continue
            if isinstance(value, bool):
                raise ValueError("{} must be a number".format(output_key))
            numeric_value = float(value)
            if not math.isfinite(numeric_value) or (
                numeric_value < minimum
                if inclusive
                else numeric_value <= minimum
            ):
                raise ValueError(
                    "{} must be a finite number {} {:g}".format(
                        output_key,
                        "greater than or equal to" if inclusive else "greater than",
                        minimum,
                    )
                )
            fields.extend((output_key, self._format_number(numeric_value)))

        for source_key, output_key, minimum in integer_settings:
            value = self._analysis_setting(parameters, source_key, output_key)
            if value is None:
                continue
            if isinstance(value, bool):
                raise ValueError("{} must be an integer".format(output_key))
            numeric_value = float(value)
            if (
                not math.isfinite(numeric_value)
                or not numeric_value.is_integer()
                or numeric_value < minimum
            ):
                raise ValueError(
                    "{} must be an integer greater than or equal to {}".format(
                        output_key, minimum
                    )
                )
            fields.extend((output_key, str(int(numeric_value))))
        return fields

    def _nonlinear_static_control_fields(self, parameters):
        """Render the NonLinearStatic solution controls for the active control mode.

        OOFEM builds the numerical method from ``controlmode``: direct control
        uses NRSolver while indirect control uses the cylindrical arc-length
        method.  Both read their fields from the same (default meta step)
        record, so the two field sets must not be mixed -- OOFEM would silently
        ignore the fields belonging to the other solver.
        """
        control_mode = self._analysis_setting(parameters, "controlmode")
        if control_mode is None:
            control_mode = self.NONLINEAR_STATIC_DEFAULT_CONTROL_MODE
        if isinstance(control_mode, bool):
            raise ValueError("controlmode must be an integer")
        numeric_mode = float(control_mode)
        if not numeric_mode.is_integer() or int(numeric_mode) not in (0, 1):
            raise ValueError(
                "controlmode must be 0 (indirect arc-length) or 1 (direct)"
            )
        control_mode = int(numeric_mode)

        arc_length_only = tuple(
            (source_key, output_key)
            for source_key, output_key, _minimum, _inclusive in (
                self.ARC_LENGTH_FLOAT_SETTINGS
            )
        ) + tuple(
            (source_key, output_key)
            for source_key, output_key, _minimum in (
                self.ARC_LENGTH_INTEGER_SETTINGS
            )
        )
        if control_mode == 1:
            ignored = [
                source_key
                for source_key, output_key in arc_length_only
                if self._analysis_setting(parameters, source_key, output_key)
                is not None
            ]
            if ignored:
                raise ValueError(
                    "{} applies to indirect arc-length control only; set "
                    "controlmode 0 or remove it".format(ignored[0])
                )
        elif self._analysis_setting(parameters, "steplength") is None:
            raise ValueError(
                "indirect arc-length control (controlmode 0) requires steplength"
            )

        fields = ["controlmode", str(control_mode)]
        fields.extend(
            self._solver_control_fields(
                parameters,
                self.NONLINEAR_STATIC_FLOAT_SETTINGS
                + (
                    self.ARC_LENGTH_FLOAT_SETTINGS
                    if control_mode == 0
                    else ()
                ),
                self.NONLINEAR_STATIC_INTEGER_SETTINGS
                + (
                    self.ARC_LENGTH_INTEGER_SETTINGS
                    if control_mode == 0
                    else ()
                ),
            )
        )
        if self._analysis_flag_enabled(parameters, "updateelasticstiffnessflag"):
            fields.append("updateelasticstiffnessflag")
        return fields

    def _analysis_flag_enabled(self, parameters, key):
        """Resolve a valueless OOFEM keyword flag from an integer 0/1 parameter."""
        value = self._analysis_setting(parameters, key)
        if value is None:
            return False
        if isinstance(value, bool):
            return value
        numeric_value = float(value)
        if not numeric_value.is_integer() or int(numeric_value) not in (0, 1):
            raise ValueError("{} must be 0 or 1".format(key))
        return int(numeric_value) == 1

    def _nonlinear_geometry_enabled(self):
        """Default large-strain kinematics for elements left on "inherit".

        There is no global nlgeo toggle: it is set per cross section (see
        `_element_nonlinear_geometry_enabled`), so "inherit" always means off.
        """
        return False

    def _element_nonlinear_geometry_enabled(self, salome_element_id):
        """Resolve the nlgeo flag for one continuum element record."""
        for group_name in self.elem_to_groups.get(salome_element_id, []):
            cross_section = self.group_to_cross_section.get(group_name)
            if cross_section is None:
                continue
            element_options = cross_section.get("element_options")
            if not isinstance(element_options, dict):
                continue
            mode = str(
                element_options.get("nlgeo", "inherit")
            ).strip().casefold()
            if mode == "on":
                return True
            if mode == "off":
                return False
        return self._nonlinear_geometry_enabled()

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
        hyperelastic_types = {
            "ogdencompressiblemat",
            "mooneyrivlincompressiblemat",
        }
        for group_name, material in self.group_to_mat.items():
            if (
                str(material.get("oofem_type", "")).strip().casefold()
                not in hyperelastic_types
            ):
                continue
            disabled_elements = [
                element_id
                for element_id in groups[group_name].GetIDs()
                if element_id in self.element_id_map
                and not self._element_nonlinear_geometry_enabled(element_id)
            ]
            if disabled_elements:
                errors.append(
                    "Hyperelastic material '{}' on group '{}' requires "
                    "Element nlgeo = Enabled; disabled exported element(s): {}.".format(
                        material.get("name", "Unnamed"),
                        group_name,
                        ", ".join(map(str, disabled_elements[:8])),
                    )
                )
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
        self.boundary_to_parent_map = self._build_boundary_to_parent_map()
        self._prepare_contacts(groups, errors)
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

        if self._contact_elements_to_export:
            self._contact_cross_section_id = len(self._cross_sections_to_export) + 1
            self._contact_material_id = len(self._materials_to_export) + 1

        self._boundary_conditions_to_export = []
        for boundary_condition in self.bc_map:
            group_name = boundary_condition.get("assigned_group")
            bc_name = boundary_condition.get("name", "Unnamed")
            template = self.bc_templates.get(
                self._boundary_type_key(boundary_condition.get("oofem_type"))
            )
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
            elif apply_to == "elements":
                if group_type == self._node_group_type():
                    errors.append(
                        "Boundary condition '{}' requires an element group.".format(
                            bc_name
                        )
                    )
                    continue
                outside = sorted(set(group.GetIDs()) - set(self.element_ids))
                if outside:
                    errors.append(
                        "Element group '{}' contains elements outside the exported "
                        "material domain: {}.".format(
                            group_name, ", ".join(map(str, outside[:8]))
                        )
                    )
                    continue
                values = sorted(
                    {self.element_id_map[element_id] for element_id in group.GetIDs()}
                )
                if not values:
                    errors.append(
                        "Boundary condition '{}' targets an empty element group.".format(
                            bc_name
                        )
                    )
                    continue
                set_id = self._add_set(group_name, "elements", values)
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
            if apply_to == "elements":
                material_errors = self._validate_element_load_materials(
                    boundary_condition, group
                )
                if material_errors:
                    errors.extend(material_errors)
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

        self._prepare_initial_conditions(groups, errors)

        if not self._cross_sections_to_export:
            errors.append("No cross section could be created from the material assignments.")
        if self.solver_settings.get("vtk", False):
            errors.extend(
                self._validate_vtk_record(
                    self.solver_settings.get(
                        "vtk_record", self.DEFAULT_SOLVER_SETTINGS["vtk_record"]
                    )
                )
            )
        if errors:
            raise OOFEMValidationError(errors)
        self._prepared = True

    @staticmethod
    def _validate_vtk_record(record):
        """Sanity-check a solver preset's raw 'vtk_record' export-module line.

        This is written verbatim into the .in file, so a malformed value
        (wrong type, embedded newline, or a primvars/cellvars count that
        doesn't match the number of IDs following it) would otherwise only
        surface as a confusing OOFEM parse error or a crash deep in
        ``str.write``.
        """
        errors = []
        if not isinstance(record, str) or not record.strip():
            errors.append(
                "Solver preset 'vtk_record' must be a non-empty string, got {!r}.".format(
                    record
                )
            )
            return errors
        if "\n" in record or "\r" in record:
            errors.append("Solver preset 'vtk_record' must not contain a newline.")
            return errors

        def _is_int_token(value):
            try:
                int(value)
            except (TypeError, ValueError):
                return False
            return True

        tokens = record.split()
        if not tokens or tokens[0].lower() != "vtkxml":
            errors.append(
                "Solver preset 'vtk_record' must start with the 'vtkxml' export "
                "module keyword, got {!r}.".format(record)
            )
            return errors

        for keyword in ("primvars", "vars", "cellvars", "ipvars"):
            for index, token in enumerate(tokens):
                if token.lower() != keyword:
                    continue
                count_text = tokens[index + 1] if index + 1 < len(tokens) else None
                try:
                    count = int(count_text)
                    if count < 0:
                        raise ValueError
                except (TypeError, ValueError):
                    errors.append(
                        "Solver preset 'vtk_record' has '{}' not followed by a "
                        "non-negative integer count.".format(keyword)
                    )
                    continue
                ids = tokens[index + 2 : index + 2 + count]
                if len(ids) != count or not all(
                    _is_int_token(value) for value in ids
                ):
                    errors.append(
                        "Solver preset 'vtk_record' declares {} '{}' but does not "
                        "provide that many integer IDs.".format(count, keyword)
                    )
        return errors

    def validate(self):
        self._prepare_records()
        has_contact = bool(self._contact_elements_to_export)
        return {
            "nodes": len(self.node_ids),
            "elements": len(self.element_ids) + len(self._contact_elements_to_export),
            "structural_elements": len(self.element_ids),
            "contact_elements": len(self._contact_elements_to_export),
            "materials": len(self._materials_to_export) + int(has_contact),
            "cross_sections": len(self._cross_sections_to_export) + int(has_contact),
            "boundary_conditions": len(self._boundary_conditions_to_export),
            "initial_conditions": len(self._initial_conditions_to_export),
            "contacts": len(self._contacts_to_export),
            "contact_conditions": len(self._contact_conditions_to_export),
            "contact_surfaces": len(self._contact_surfaces_to_export),
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
        for salome_element_id in self.element_ids:
            connectivity = [
                self.node_id_map[node_id]
                for node_id in self.element_connectivity[salome_element_id]
            ]
            nlgeo_suffix = (
                " nlgeo 1"
                if self._element_nonlinear_geometry_enabled(salome_element_id)
                else ""
            )
            output.write(
                "{} {} nodes {} {}{}\n".format(
                    self._get_oofem_element_type(salome_element_id),
                    self.element_id_map[salome_element_id],
                    len(connectivity),
                    " ".join(map(str, connectivity)),
                    nlgeo_suffix,
                )
            )
        for contact_element in self._contact_elements_to_export:
            connectivity = contact_element["connectivity"]
            output.write(
                "{} {} nodes {} {} crosssect {} mat {} NIP {}\n".format(
                    contact_element["keyword"],
                    contact_element["id"],
                    len(connectivity),
                    " ".join(map(str, connectivity)),
                    self._contact_cross_section_id,
                    self._contact_material_id,
                    contact_element["nip"],
                )
            )

    def _export_contact_surfaces(self, output):
        if not self._contact_surfaces_to_export:
            return
        output.write("\n# === CONTACT SURFACES ===\n")
        for surface in self._contact_surfaces_to_export:
            output.write(
                "StructuralFEContactSurface {} ce_set {}\n".format(
                    surface["id"], surface["set_id"]
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
        if self._contact_cross_section_id is not None:
            output.write(
                "DummyCS {} mat {}\n".format(
                    self._contact_cross_section_id,
                    self._contact_material_id,
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
        if self._contact_material_id is not None:
            output.write("DummyMat {}\n".format(self._contact_material_id))

    def _export_boundary_conditions(self, output):
        if (
            not self._boundary_conditions_to_export
            and not self._contact_conditions_to_export
        ):
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
            bc_type_key = self._boundary_type_key(bc_type)
            dof_text = " ".join(map(str, dofs))
            value_text = " ".join(
                self._format_number(value) for value in component_values
            )
            if bc_type_key in ("boundarycondition", "displacement"):
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
            elif bc_type_key == "nodalload":
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
            elif bc_type_key == "surfaceload" and apply_to == "element_boundary":
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
            elif bc_type_key == "deadweight" and apply_to == "elements":
                output.write(
                    "DeadWeight {} loadTimeFunction {} components {} {} set {}\n".format(
                        record_id,
                        function_number,
                        len(component_values),
                        value_text,
                        set_id,
                    )
                )
            elif bc_type_key == "structuraltemperatureload" and apply_to == "elements":
                output.write(
                    "StructTemperatureLoad {} loadTimeFunction {} "
                    "components {} {} set {}\n".format(
                        record_id,
                        function_number,
                        len(component_values),
                        value_text,
                        set_id,
                    )
                )
            else:
                raise OOFEMValidationError(
                    ["Boundary condition '{}' has no OOFEM writer.".format(bc_type)]
                )

        first_contact_id = len(self._boundary_conditions_to_export) + 1
        dofs = list(range(1, self._domain_dof_count() + 1))
        for offset, (
            contact,
            master_surface,
            slave_surface,
            function_number,
        ) in enumerate(self._contact_conditions_to_export):
            record_id = first_contact_id + offset
            parameters = contact["params"]
            record = (
                "structuralpenaltycontactbc {} loadTimeFunction {} "
                "dofs {} {} pn {} pt {} friction {} mastersurface {} "
                "slavesurface {} nsd {}"
            ).format(
                record_id,
                function_number,
                len(dofs),
                " ".join(map(str, dofs)),
                self._format_number(parameters["normal_penalty"]),
                self._format_number(parameters["tangential_penalty"]),
                self._format_number(parameters["friction"]),
                master_surface,
                slave_surface,
                self._domain_dof_count(),
            )
            if parameters["algorithm"]:
                record += " algo {}".format(parameters["algorithm"])
            output.write(record + "\n")

    def _export_initial_conditions(self, output):
        if not self._initial_conditions_to_export:
            return
        output.write("\n# === INITIAL CONDITIONS ===\n")
        for record_id, (
            _initial_condition,
            set_id,
            dofs,
            conditions,
        ) in enumerate(self._initial_conditions_to_export, start=1):
            condition_text = " ".join(
                "{} {}".format(mode, self._format_number(value))
                for mode, value in conditions
            )
            output.write(
                "InitialCondition {} conditions {} {} dofs {} {} set {}\n".format(
                    record_id,
                    len(conditions),
                    condition_text,
                    len(dofs),
                    " ".join(map(str, dofs)),
                    set_id,
                )
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
        has_contact = bool(self._contact_elements_to_export)
        header = (
            "ndofman {} nelem {} ncrosssect {} nmat {} nbc {} "
            "nic {} nltf {} nset {}"
        ).format(
                len(self.node_ids),
                len(self.element_ids) + len(self._contact_elements_to_export),
                len(self._cross_sections_to_export) + int(has_contact),
                len(self._materials_to_export) + int(has_contact),
                len(self._boundary_conditions_to_export)
                + len(self._contact_conditions_to_export),
                len(self._initial_conditions_to_export),
                len(self._time_functions_to_export),
                len(self._groups_to_export),
            )
        if self._contact_surfaces_to_export:
            header += " ncontactsurf {}".format(
                len(self._contact_surfaces_to_export)
            )
        output.write(header + "\n")
        self._export_nodes(output)
        self._export_elements(output)
        self._export_contact_surfaces(output)
        self._export_cross_sections(output)
        self._export_materials(output)
        self._export_boundary_conditions(output)
        self._export_initial_conditions(output)
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
