#!/usr/bin/env bash

set -euo pipefail

usage() {
    printf '%s\n' \
        "Usage: ./install.sh [--target PLUGIN_DIRECTORY]" \
        "" \
        "Installs into the per-user SALOME plugin directory by default:" \
        "  \${XDG_CONFIG_HOME:-\$HOME/.config}/salome/Plugins"
}

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source_dir="$script_dir/src/OOFEMSalomePlugin"
target_dir="${XDG_CONFIG_HOME:-$HOME/.config}/salome/Plugins"

while [ "$#" -gt 0 ]; do
    case "$1" in
        --target)
            if [ "$#" -lt 2 ]; then
                printf 'ERROR: --target requires a directory.\n' >&2
                usage >&2
                exit 2
            fi
            target_dir=$2
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

if [ ! -d "$source_dir" ]; then
    printf 'ERROR: plugin package not found: %s\n' "$source_dir" >&2
    exit 1
fi

package_dir="$target_dir/OOFEMSalomePlugin"
registration_file="$target_dir/salome_plugins.py"
registration_marker="# >>> OOFEM SALOME plugin >>>"

mkdir -p "$target_dir"
rm -rf -- "$package_dir"
cp -R -- "$source_dir" "$package_dir"

if [ ! -f "$registration_file" ]; then
    : > "$registration_file"
fi

if ! grep -Fq -- "$registration_marker" "$registration_file"; then
    printf '%s\n' \
        "" \
        "$registration_marker" \
        "from OOFEMSalomePlugin.plugin_entry import register_plugin as _register_oofem_plugin" \
        "_register_oofem_plugin()" \
        "# <<< OOFEM SALOME plugin <<<" >> "$registration_file"
fi

printf '%s\n' \
    "OOFEM SALOME plugin installed in: $target_dir" \
    "Restart SALOME, then open Tools > Plugins > OOFEM."
