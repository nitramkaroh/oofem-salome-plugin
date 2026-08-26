#!/usr/bin/env bash

set -euo pipefail

usage() {
    printf '%s\n' \
        "Usage: ./install.sh --uninstall [--target PLUGIN_DIRECTORY]" \
        "       ./install.sh --legacy-tools-plugin [--target PLUGIN_DIRECTORY]" \
        "" \
        "The supported way to install OOFEM is as a native SALOME module:" \
        "  ./install-salome-module.sh --salome /path/to/SALOME" \
        "which puts OOFEM in SALOME's module selector/toolbar." \
        "" \
        "This script only manages the LEGACY Tools > Plugins > OOFEM entry," \
        "which is redundant once the native module works, loads a separate" \
        "copy of the package that can shadow the native one, and is therefore" \
        "no longer installed by default." \
        "" \
        "  --uninstall             remove the Tools > Plugins entry and its" \
        "                          package copy (recommended)" \
        "  --legacy-tools-plugin   install the legacy entry anyway, e.g. on a" \
        "                          SALOME where the native module will not load" \
        "  --target DIRECTORY      operate on DIRECTORY instead of the default" \
        "                          \${XDG_CONFIG_HOME:-\$HOME/.config}/salome/Plugins"
}

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source_dir="$script_dir/src/OOFEMSalomePlugin"
target_dir="${XDG_CONFIG_HOME:-$HOME/.config}/salome/Plugins"
mode=""

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
        --uninstall)
            mode="uninstall"
            shift
            ;;
        --legacy-tools-plugin)
            mode="install"
            shift
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

package_dir="$target_dir/OOFEMSalomePlugin"
registration_file="$target_dir/salome_plugins.py"
registration_marker="# >>> OOFEM SALOME plugin >>>"
registration_end_marker="# <<< OOFEM SALOME plugin <<<"

if [ -z "$mode" ]; then
    printf 'ERROR: refusing to install the legacy Tools > Plugins entry implicitly.\n' >&2
    usage >&2
    exit 2
fi

if [ "$mode" = "uninstall" ]; then
    removed=0
    if [ -e "$package_dir" ]; then
        rm -rf -- "$package_dir"
        printf 'Removed package copy: %s\n' "$package_dir"
        removed=1
    fi
    if [ -f "$registration_file" ]; then
        if grep -Fq -- "$registration_marker" "$registration_file"; then
            # Strip only OOFEM's own marked block; another plugin's
            # registration in the same shared file must survive untouched.
            stripped=$(mktemp "$target_dir/.salome_plugins.py.XXXXXX")
            awk -v start="$registration_marker" -v end="$registration_end_marker" '
                index($0, start) { skipping = 1; next }
                index($0, end)   { skipping = 0; next }
                !skipping        { print }
            ' "$registration_file" > "$stripped"
            if [ -s "$stripped" ] && grep -q '[^[:space:]]' "$stripped"; then
                chmod --reference="$registration_file" "$stripped"
                mv -- "$stripped" "$registration_file"
                printf 'Removed OOFEM block from: %s\n' "$registration_file"
            else
                rm -f -- "$stripped" "$registration_file"
                printf 'Removed now-empty registration: %s\n' "$registration_file"
            fi
            removed=1
        fi
    fi
    if [ "$removed" -eq 0 ]; then
        printf 'Nothing to remove in: %s\n' "$target_dir"
    else
        printf '%s\n' "Restart SALOME; Tools > Plugins > OOFEM is gone."
    fi
    exit 0
fi

if [ ! -d "$source_dir" ]; then
    printf 'ERROR: plugin package not found: %s\n' "$source_dir" >&2
    exit 1
fi

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
        "$registration_end_marker" >> "$registration_file"
fi

printf '%s\n' \
    "Legacy OOFEM SALOME plugin installed in: $target_dir" \
    "Restart SALOME, then open Tools > Plugins > OOFEM." \
    "NOTE: this copy is separate from the native module and can shadow it;" \
    "      prefer ./install-salome-module.sh unless the native module fails."
