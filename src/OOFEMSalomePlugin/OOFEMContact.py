"""Pure validation helpers for OOFEM structural contact export."""

import math


SUPPORTED_CONTACT_DOMAINS = frozenset(("2dplanestress", "planestrain", "3d"))


def _finite_number(value, label, positive=False, nonnegative=False):
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError("{} must be numeric".format(label)) from error
    if not math.isfinite(number):
        raise ValueError("{} must be finite".format(label))
    if positive and number <= 0.0:
        raise ValueError("{} must be positive".format(label))
    if nonnegative and number < 0.0:
        raise ValueError("{} must be non-negative".format(label))
    return number


def _boolean(value, label):
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in ("true", "yes", "1"):
            return True
        if normalized in ("false", "no", "0"):
            return False
    raise ValueError("{} must be a boolean".format(label))


def canonical_contact(contact, domain_type):
    """Return a validated canonical contact record without mutating *contact*."""
    if not isinstance(contact, dict):
        raise ValueError("Contact definition must be an object")
    if domain_type not in SUPPORTED_CONTACT_DOMAINS:
        raise ValueError(
            "Structural contact supports 2D continuum and 3D domains, not '{}'".format(
                domain_type
            )
        )
    name = str(contact.get("name") or "Unnamed")
    master_group = contact.get("master_group") or contact.get("master")
    slave_group = contact.get("slave_group") or contact.get("slave")
    if not isinstance(master_group, str) or not master_group:
        raise ValueError("Contact '{}' needs a master boundary group".format(name))
    if not isinstance(slave_group, str) or not slave_group:
        raise ValueError("Contact '{}' needs a slave boundary group".format(name))
    master_group = master_group.strip()
    slave_group = slave_group.strip()
    if not master_group or not slave_group:
        raise ValueError(
            "Contact '{}' needs non-empty master and slave boundary groups".format(
                name
            )
        )
    if master_group == slave_group:
        raise ValueError(
            "Contact '{}' master and slave groups must differ".format(name)
        )
    parameters = contact.get("params")
    if parameters is None:
        parameters = {}
    if not isinstance(parameters, dict):
        raise ValueError("Contact '{}' parameters must be an object".format(name))

    algorithm = parameters.get("algorithm", parameters.get("algo", 0))
    if isinstance(algorithm, bool):
        raise ValueError("Contact '{}' search algorithm must be 0 or 1".format(name))
    try:
        numeric_algorithm = float(algorithm)
    except (TypeError, ValueError) as error:
        raise ValueError(
            "Contact '{}' search algorithm must be 0 or 1".format(name)
        ) from error
    if not math.isfinite(numeric_algorithm) or not numeric_algorithm.is_integer():
        raise ValueError(
            "Contact '{}' search algorithm must be 0 or 1".format(name)
        )
    algorithm = int(numeric_algorithm)
    if algorithm not in (0, 1):
        raise ValueError("Contact '{}' search algorithm must be 0 or 1".format(name))
    if algorithm == 1 and domain_type != "3d":
        raise ValueError(
            "Contact '{}' sweep-and-prune search is available only in 3D".format(
                name
            )
        )

    return {
        "id": contact.get("id"),
        "name": name,
        "oofem_type": "StructuralPenaltyContactBC",
        "master_group": master_group,
        "slave_group": slave_group,
        "time_function_id": contact.get("time_function_id"),
        "params": {
            "normal_penalty": _finite_number(
                parameters.get("normal_penalty", parameters.get("pn", 1.0e6)),
                "Contact '{}' normal penalty".format(name),
                positive=True,
            ),
            "tangential_penalty": _finite_number(
                parameters.get(
                    "tangential_penalty", parameters.get("pt", 1.0e6)
                ),
                "Contact '{}' tangential penalty".format(name),
                positive=True,
            ),
            "friction": _finite_number(
                parameters.get("friction", 0.0),
                "Contact '{}' friction coefficient".format(name),
                nonnegative=True,
            ),
            "algorithm": algorithm,
            "two_pass": _boolean(
                parameters.get("two_pass", False),
                "Contact '{}' two-pass option".format(name),
            ),
            "reverse_master": _boolean(
                parameters.get("reverse_master", False),
                "Contact '{}' reverse-master option".format(name),
            ),
            "reverse_slave": _boolean(
                parameters.get("reverse_slave", False),
                "Contact '{}' reverse-slave option".format(name),
            ),
        },
    }


def contact_element_spec(domain_type, node_count):
    """Return ``(keyword, nip)`` for a supported linear contact facet."""
    if domain_type in ("2dplanestress", "planestrain"):
        if node_count == 2:
            return "StructuralContactElement_LineLin", 2
        raise ValueError(
            "2D contact boundaries must use linear two-node SALOME edges"
        )
    if domain_type == "3d":
        if node_count == 3:
            return "StructuralContactElement_TrLin", 3
        if node_count == 4:
            return "StructuralContactElement_QuadLin", 4
        raise ValueError(
            "3D contact boundaries must use linear triangular or quadrilateral faces"
        )
    raise ValueError("Contact elements are unsupported in domain '{}'".format(domain_type))


def orient_contact_nodes(nodes, reverse=False):
    """Reverse a line/triangle/quad while retaining its first vertex."""
    nodes = list(nodes)
    if not reverse:
        return nodes
    if len(nodes) == 2:
        return [nodes[1], nodes[0]]
    if len(nodes) in (3, 4):
        return [nodes[0]] + list(reversed(nodes[1:]))
    raise ValueError("Cannot reverse unsupported contact connectivity")
