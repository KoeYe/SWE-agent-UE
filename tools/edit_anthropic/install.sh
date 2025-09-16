#!/usr/bin/env bash

# Get the directory of this script
script_dir=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

# Ignore failures, see https://github.com/SWE-agent/SWE-agent/issues/1179
pip install 'tree-sitter==0.21.3' || true
pip install 'tree-sitter-languages' || true

# Don't install the wrong registry package, use our local one
# pip install 'registry' || true

# Set up PYTHONPATH to use the correct registry module
# Find the registry tool directory relative to this script
registry_lib_dir=$(realpath "$script_dir/../../registry/lib")
export PYTHONPATH="$registry_lib_dir:$PYTHONPATH"

echo "Set PYTHONPATH to include registry: $registry_lib_dir"