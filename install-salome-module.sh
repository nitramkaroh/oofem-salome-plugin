#!/usr/bin/env bash

set -euo pipefail

usage() {
    printf '%s\n' \
        "Usage: ./install-salome-module.sh --salome SALOME_DIRECTORY" \
        "" \
        "Installs OOFEM as a selectable SALOME light module, registers it" \
        "in SALOME's global/user GUI resources and extra.env.d hook, and creates:" \
        "  SALOME_DIRECTORY/salome-oofem"
}

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
salome_dir=""

while [ "$#" -gt 0 ]; do
    case "$1" in
        --salome)
            if [ "$#" -lt 2 ]; then
                printf 'ERROR: --salome requires a directory.\n' >&2
                usage >&2
                exit 2
            fi
            salome_dir=$2
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            printf 'ERROR: unknown option: %s\n' "$1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [ -z "$salome_dir" ] || [ ! -x "$salome_dir/salome" ]; then
    printf 'ERROR: SALOME launcher not found: %s/salome\n' "$salome_dir" >&2
    exit 1
fi

package_source="$script_dir/src/OOFEMSalomePlugin"
module_source="$script_dir/module"
module_root="$salome_dir/INSTALL/OOFEM"
python_root="$module_root/bin/salome"
resource_root="$module_root/share/salome/resources/oofem"
extra_env_root="$salome_dir/extra.env.d"
salome_app_xml="$salome_dir/INSTALL/SALOME/share/salome/resources/salome/SalomeApp.xml"
integration_xml="$module_source/SalomeApp.integration.xml"

if [ ! -f "$salome_app_xml" ]; then
    printf 'ERROR: SALOME GUI resource file not found: %s\n' "$salome_app_xml" >&2
    exit 1
fi

mkdir -p "$python_root" "$resource_root" "$extra_env_root"
rm -rf -- "$python_root/OOFEMSalomePlugin"
cp -R -- "$package_source" "$python_root/OOFEMSalomePlugin"
cp -- "$module_source/OOFEMGUI.py" "$python_root/OOFEMGUI.py"
cp -- "$module_source/register_oofem_user_config.py" "$python_root/register_oofem_user_config.py"
cp -- "$module_source/SalomeApp.xml" "$resource_root/SalomeApp.xml"
cp -- "$module_source/oofem.png" "$resource_root/oofem.png"
cp -- "$module_source/oofem-logo.png" "$resource_root/oofem-logo.png"
cp -- "$module_source/oofem_env.py" "$extra_env_root/oofem.py"

# A complete SALOME application (such as salome_meca with AsterStudy) includes
# each module section in a GUI resource file known when ResourceMgr starts.
# A late-added SalomeAppConfig directory is visible to the launch parser but,
# in some native SALOME distributions, not to the already-created GUI resource
# manager.  Register OOFEM in SALOME's primary resource file as well.
integration_start='<!-- BEGIN OOFEM SALOME MODULE (managed by oofem-salome-plugin) -->'
integration_end='<!-- END OOFEM SALOME MODULE (managed by oofem-salome-plugin) -->'
salome_app_backup="$salome_app_xml.before-oofem"
if [ ! -e "$salome_app_backup" ]; then
    cp -- "$salome_app_xml" "$salome_app_backup"
fi

updated_salome_app=$(mktemp "${TMPDIR:-/tmp}/oofem-SalomeApp.XXXXXX")
cleanup() {
    rm -f -- "$updated_salome_app"
}
trap cleanup EXIT
awk -v start="$integration_start" -v end="$integration_end" -v block="$integration_xml" '
    index($0, start) { skipping = 1; next }
    index($0, end) { skipping = 0; next }
    !skipping && /<\/document>/ {
        while ((getline line < block) > 0) print line
        close(block)
    }
    !skipping { print }
' "$salome_app_xml" > "$updated_salome_app"

if ! cmp -s -- "$salome_app_xml" "$updated_salome_app"; then
    cp -- "$updated_salome_app" "$salome_app_xml"
fi

user_config_registrar="$python_root/register_oofem_user_config.py"
python3 "$user_config_registrar" --salome "$salome_dir"

launcher="$salome_dir/salome-oofem"
{
    printf '%s\n' '#!/usr/bin/env bash' 'set -euo pipefail'
    printf 'salome_dir=%q\n' "$salome_dir"
    printf 'user_config_registrar=%q\n' "$user_config_registrar"
    printf '%s\n' 'python3 "$user_config_registrar" --salome "$salome_dir"'
    printf '%s\n' 'exec "$salome_dir/salome" "$@"'
} > "$launcher"
chmod +x "$launcher"

printf '%s\n' \
    "OOFEM SALOME module installed in: $module_root" \
    "Registered in SALOME GUI: $salome_app_xml" \
    "Registered in SALOME launcher: $extra_env_root/oofem.py" \
    "Start it with: $launcher"
