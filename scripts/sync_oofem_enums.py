#!/usr/bin/env python3
"""Synchronize OOFEM UnknownType and InternalStateType enum definitions.

This script parses OOFEM C++ header files (`unknowntype.h` and
`internalstatetype.h`) -- either from a local OOFEM source tree or fetched
from the official OOFEM GitHub repository -- and updates
`src/OOFEMSalomePlugin/OOFEMExportVariables.json`.

Usage:
    python scripts/sync_oofem_enums.py
    python scripts/sync_oofem_enums.py --source /path/to/oofem
    python scripts/sync_oofem_enums.py --url-base https://raw.githubusercontent.com/oofem/oofem/master
"""

import argparse
import json
import os
import re
import sys
import urllib.request


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
OUTPUT_JSON_PATH = os.path.join(
    PROJECT_ROOT, "src", "OOFEMSalomePlugin", "OOFEMExportVariables.json"
)

DEFAULT_GITHUB_BASE = "https://raw.githubusercontent.com/oofem/oofem/master"

PRIMARY_DESCRIPTIONS = {
    "DisplacementVector": "Nodal displacement vector (u, v, w)",
    "GeneralizedDisplacementVector": "Generalized nodal displacement / rotational vector",
    "FluxVector": "Flux vector",
    "VelocityVector": "Velocity vector",
    "PressureVector": "Pressure vector",
    "Temperature": "Temperature field",
    "Humidity": "Relative humidity field",
    "EigenVector": "Modal eigen vector",
    "DirectorField": "Director vector field",
    "DeplanationFunction": "Deplanation warping function",
    "MacroSlipVector": "Macro slip vector",
    "ResidualForce": "Residual force vector",
    "Concentration": "Species concentration",
}

INTERNAL_DESCRIPTIONS = {
    "IST_StressTensor": "Stress tensor (Voigt notation)",
    "IST_PrincipalStressTensor": "Principal stress tensor",
    "IST_PrincipalStressTempTensor": "Temperature-dependent principal stress tensor",
    "IST_StrainTensor": "Total strain tensor (Voigt notation)",
    "IST_PrincipalStrainTensor": "Principal strain tensor",
    "IST_PrincipalStrainTempTensor": "Temperature-dependent principal strain tensor",
    "IST_BeamForceMomentTensor": "Beam generalized internal forces and moments (N, Vy, Vz, Mx, My, Mz)",
    "IST_BeamStrainCurvatureTensor": "Beam generalized strains and curvatures (eps, gam_y, gam_z, kap_x, kap_y, kap_z)",
    "IST_ShellMomentTensor": "Shell bending and twisting moments (Mx, My, Mxy)",
    "IST_ShellForceTensor": "Shell in-plane membrane forces (Nx, Ny, Nxy)",
    "IST_CurvatureTensor": "Plate/shell curvature tensor",
    "IST_DisplacementVector": "Internal displacement vector",
    "IST_DamageTensor": "Anisotropic damage tensor",
    "IST_DamageInvTensor": "Inverse damage tensor",
    "IST_PrincipalDamageTensor": "Principal damage tensor",
    "IST_CrackState": "Crack state identifier",
    "IST_PlasticStrainTensor": "Plastic strain tensor",
    "IST_PrincipalPlasticStrainTensor": "Principal plastic strain tensor",
    "IST_CylindricalStressTensor": "Stress tensor in cylindrical coordinate system",
    "IST_CylindricalStrainTensor": "Strain tensor in cylindrical coordinate system",
    "IST_MaxEquivalentStrainLevel": "Maximum historical equivalent strain level",
    "IST_ErrorIndicatorLevel": "Zienkiewicz-Zhu error indicator level",
    "IST_MicroplaneDamageValues": "Microplane damage variables",
    "IST_Temperature": "Temperature state",
    "IST_MassConcentration_1": "Primary mass concentration",
    "IST_HydrationDegree": "Degree of hydration (concrete)",
    "IST_Humidity": "Relative humidity state",
    "IST_Velocity": "Velocity state",
    "IST_Pressure": "Pore/fluid pressure state",
    "IST_Density": "Material density",
    "IST_MaterialNumber": "Active material ID index",
    "IST_ElementNumber": "Active element ID index",
    "IST_BoneVolumeFraction": "Bone volume fraction",
    "IST_PlasStrainEnerDens": "Plastic strain energy density",
    "IST_ElasStrainEnerDens": "Elastic strain energy density",
    "IST_TotalStrainEnerDens": "Total strain energy density",
    "IST_DamageScalar": "Isotropic scalar damage parameter (0 = intact, 1 = fully damaged)",
    "IST_MaterialOrientation_x": "Local material orientation vector X",
    "IST_MaterialOrientation_y": "Local material orientation vector Y",
    "IST_MaterialOrientation_z": "Local material orientation vector Z",
    "IST_TemperatureFlow": "Heat flow vector",
    "IST_CrackStatuses": "Individual crack status indicators",
    "IST_CrackedFlag": "Cracking initiation flag (0/1)",
    "IST_CrackDirs": "Crack plane normal directions",
    "IST_CumPlasticStrain": "Cumulative equivalent plastic strain (kappa)",
    "IST_StressWorkDensity": "Stress work density",
    "IST_DissWorkDensity": "Dissipated energy density",
    "IST_FreeEnergyDensity": "Helmholtz free energy density",
    "IST_ThermalConductivityIsotropic": "Isotropic thermal conductivity",
    "IST_HeatCapacity": "Specific heat capacity",
    "IST_AverageTemperature": "Average temperature",
    "IST_VolumetricPlasticStrain": "Volumetric plastic strain component",
    "IST_DeviatoricStrain": "Deviatoric strain tensor",
    "IST_DeviatoricStress": "Deviatoric stress tensor",
    "IST_Viscosity": "Dynamic viscosity",
    "IST_vonMisesStress": "von Mises equivalent stress",
    "IST_CrackVector": "Crack opening displacement vector",
    "IST_PressureGradient": "Pressure gradient vector",
    "IST_DissWork": "Total dissipated energy",
    "IST_CrackWidth": "Crack opening width",
    "IST_DeformationGradientTensor": "Deformation gradient tensor (F)",
    "IST_FirstPKStressTensor": "First Piola-Kirchhoff stress tensor (P)",
    "IST_Maturity": "Concrete equivalent age / maturity",
    "IST_CauchyStressTensor": "True Cauchy stress tensor (sigma)",
    "IST_InterfaceJump": "Interface displacement jump vector",
    "IST_InterfaceTraction": "Interface traction vector",
    "IST_CrossSectionNumber": "Active cross-section ID index",
    "IST_ShellStrainTensor": "Shell middle surface strain tensor",
    "IST_AutogenousShrinkageTensor": "Autogenous shrinkage strain tensor",
    "IST_DryingShrinkageTensor": "Drying shrinkage strain tensor",
    "IST_TotalShrinkageTensor": "Total shrinkage strain tensor",
    "IST_ThermalStrainTensor": "Thermal expansion strain tensor",
    "IST_CreepStrainTensor": "Creep strain tensor",
    "IST_TensileStrength": "Current tensile strength",
    "IST_ResidualTensileStrength": "Residual tensile strength",
    "IST_CrackIndex": "Crack index identifier",
    "IST_EigenStrainTensor": "Initial / Eigen strain tensor",
    "IST_CrackStrainTensor": "Crack opening strain tensor",
    "IST_YieldStrength": "Current yield strength",
    "IST_ElasticStrainTensor": "Elastic strain tensor",
    "IST_MoistureContent": "Moisture content fraction",
    "IST_NormalStress": "Normal stress component",
    "IST_ContactGap": "Contact normal separation / gap",
    "IST_ContactPressure": "Contact normal pressure",
    "IST_ContactStatus": "Contact interaction status (0 = open, 1 = stick, 2 = slip)",
}


def _format_description(name, fallback_map):
    if name in fallback_map:
        return fallback_map[name]
    readable = re.sub(r"^IST_", "", name)
    readable = re.sub(r"([a-z])([A-Z])", r"\1 \2", readable)
    readable = readable.replace("_", " ").strip()
    return readable


def parse_enum_items(content):
    items = []
    seen_ids = set()

    with_value_pattern = re.compile(
        r"ENUM_ITEM_WITH_VALUE\s*\(\s*([A-Za-z0-9_]+)\s*,\s*(\d+)\s*\)"
    )
    for match in with_value_pattern.finditer(content):
        name = match.group(1).strip()
        val = int(match.group(2))
        if val not in seen_ids and val > 0:
            items.append({"name": name, "id": val})
            seen_ids.add(val)

    if not items:
        enum_body_match = re.search(r"enum\s+[A-Za-z0-9_]+\s*\{([^}]+)\}", content)
        if enum_body_match:
            body = enum_body_match.group(1)
            current_val = 0
            for line in body.split(","):
                line = re.sub(r"//.*$", "", line).strip()
                if not line:
                    continue
                if "=" in line:
                    parts = line.split("=", 1)
                    name = parts[0].strip()
                    try:
                        current_val = int(parts[1].strip())
                    except ValueError:
                        pass
                else:
                    name = line.strip()
                if name and current_val > 0 and current_val not in seen_ids:
                    items.append({"name": name, "id": current_val})
                    seen_ids.add(current_val)
                current_val += 1

    return items


def fetch_file_content(path_or_url):
    if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        req = urllib.request.Request(
            path_or_url,
            headers={"User-Agent": "OOFEM-Salome-Plugin-Enum-Sync/1.0"},
        )
        with urllib.request.urlopen(req) as resp:
            return resp.read().decode("utf-8")
    with open(path_or_url, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def build_catalog(unknown_content, internal_content):
    raw_unknowns = parse_enum_items(unknown_content)
    raw_internals = parse_enum_items(internal_content)

    primary_vars = []
    for item in sorted(raw_unknowns, key=lambda x: x["id"]):
        name = item["name"]
        primary_vars.append(
            {
                "id": item["id"],
                "name": name,
                "description": _format_description(name, PRIMARY_DESCRIPTIONS),
            }
        )

    internal_vars = []
    for item in sorted(raw_internals, key=lambda x: x["id"]):
        name = item["name"]
        internal_vars.append(
            {
                "id": item["id"],
                "name": name,
                "description": _format_description(name, INTERNAL_DESCRIPTIONS),
            }
        )

    existing_internal_ids = {item["id"] for item in internal_vars}
    contact_defaults = [
        (150, "IST_ContactGap", "Contact normal separation / gap"),
        (151, "IST_ContactPressure", "Contact normal pressure"),
        (152, "IST_ContactStatus", "Contact interaction status (0 = open, 1 = stick, 2 = slip)"),
    ]
    for cid, cname, cdesc in contact_defaults:
        if cid not in existing_internal_ids:
            internal_vars.append({"id": cid, "name": cname, "description": cdesc})

    internal_vars.sort(key=lambda x: x["id"])

    catalog = {
        "_comment": "Auto-generated by scripts/sync_oofem_enums.py from OOFEM header files.",
        "categories": {
            "primvars": {
                "keyword": "primvars",
                "display_name": "Primary Variable (primvars)",
                "description": "Primary unknowns (displacements, temperatures, etc.) evaluated at nodes.",
                "source_type": "primary",
            },
            "vars": {
                "keyword": "vars",
                "display_name": "Internal Variable - Nodal smoothed (vars)",
                "description": "Internal state variables (stresses, strains, etc.) smoothed and mapped to nodes.",
                "source_type": "internal",
            },
            "cellvars": {
                "keyword": "cellvars",
                "display_name": "Internal Variable - Element / Cell (cellvars)",
                "description": "Internal variables evaluated and stored per element (cell) without smoothing.",
                "source_type": "internal",
            },
            "ipvars": {
                "keyword": "ipvars",
                "display_name": "Internal Variable - Integration Point (ipvars)",
                "description": "Internal variables evaluated directly at integration points.",
                "source_type": "internal",
            },
        },
        "primary_variables": primary_vars,
        "internal_variables": internal_vars,
    }
    return catalog


def main():
    parser = argparse.ArgumentParser(
        description="Sync OOFEM UnknownType and InternalStateType enums into JSON catalog."
    )
    parser.add_argument(
        "--source",
        "-s",
        help="Path to local OOFEM source directory (e.g. /path/to/oofem).",
    )
    parser.add_argument(
        "--url-base",
        "-u",
        default=DEFAULT_GITHUB_BASE,
        help="Base URL for raw OOFEM GitHub repo.",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=OUTPUT_JSON_PATH,
        help="Output JSON file path.",
    )
    args = parser.parse_args()

    if args.source:
        candidate_unknown = [
            os.path.join(args.source, "src", "core", "unknowntype.h"),
            os.path.join(args.source, "src", "oofemlib", "unknowntype.h"),
        ]
        candidate_internal = [
            os.path.join(args.source, "src", "core", "internalstatetype.h"),
            os.path.join(args.source, "src", "oofemlib", "internalstatetype.h"),
        ]
        unknown_path = next((p for p in candidate_unknown if os.path.isfile(p)), None)
        internal_path = next((p for p in candidate_internal if os.path.isfile(p)), None)

        if not unknown_path or not internal_path:
            sys.exit(
                "Error: Could not find unknowntype.h or internalstatetype.h in {}".format(
                    args.source
                )
            )
        print("Reading local files:\n  {}\n  {}".format(unknown_path, internal_path))
        unknown_content = fetch_file_content(unknown_path)
        internal_content = fetch_file_content(internal_path)
    else:
        unknown_url = "{}/src/core/unknowntype.h".format(args.url_base)
        internal_url = "{}/src/core/internalstatetype.h".format(args.url_base)
        print("Fetching headers from GitHub:\n  {}\n  {}".format(unknown_url, internal_url))
        try:
            unknown_content = fetch_file_content(unknown_url)
            internal_content = fetch_file_content(internal_url)
        except Exception as err:
            sys.exit("Error fetching headers from {}: {}".format(args.url_base, err))

    catalog = build_catalog(unknown_content, internal_content)

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8", newline="\n") as out:
        json.dump(catalog, out, indent=2)

    print(
        "Successfully wrote {} primary and {} internal variables to {}".format(
            len(catalog["primary_variables"]),
            len(catalog["internal_variables"]),
            args.output,
        )
    )


if __name__ == "__main__":
    main()

