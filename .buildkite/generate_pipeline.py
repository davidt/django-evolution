#!/usr/bin/env python3
"""Generate Buildkite pipeline steps from tox environments."""

from __future__ import annotations

import json
import re
import subprocess
import sys


DOCKER_PLUGIN = 'docker#v5.13.0'


def main() -> None:
    """Generate the new pipeline."""
    result = subprocess.run(
        ['tox', '-l'],
        capture_output=True,
        text=True,
        check=True,
    )

    valid_pairs: set[tuple[str, str]] = set()

    for env in result.stdout.strip().split('\n'):
        match = re.match(r'py(\d)(\d+)-django(\d+)_(\d+)', env)
        if not match:
            print(f'Skipping unrecognized env: {env}', file=sys.stderr)
            continue

        python_version = f'{match.group(1)}.{match.group(2)}'
        django_version = f'{match.group(3)}.{match.group(4)}'
        valid_pairs.add((python_version, django_version))

    pythons = sorted({p for p, _ in valid_pairs})
    djangos = sorted({d for _, d in valid_pairs})

    adjustments = [
        {'with': {'python': py, 'django': dj}, 'skip': True}
        for py in pythons
        for dj in djangos
        if (py, dj) not in valid_pairs
    ]

    steps = [
        {
            'group': ':pytest: Tests',
            'key': 'tests',
            'steps': [{
                'label': (
                    ':pytest: Py {{matrix.python}}'
                    ' / Django {{matrix.django}}'
                ),
                'command': '\n'.join([
                    'pip install tox',
                    "tox -e \"py$(printf '%s' '{{matrix.python}}'"
                    " | tr -d .)-django$(printf '%s'"
                    " '{{matrix.django}}' | tr . _)\"",
                ]),
                'matrix': {
                    'setup': {
                        'python': pythons,
                        'django': djangos,
                    },
                    'adjustments': adjustments,
                },
                'plugins': [{
                    DOCKER_PLUGIN: {
                        'image': 'python:{{matrix.python}}',
                    },
                }],
            }],
        },
        {
            'label': ':package: Build wheel',
            'key': 'build-wheel',
            'depends_on': 'tests',
            'command': '\n'.join([
                'pip install build',
                'python -m build --wheel',
             ]),
            'artifact_paths': 'dist/*.whl',
            'plugins': [
                {
                    DOCKER_PLUGIN: {'image': 'python:3.10'},
                },
            ],
        },
        {
            'label': ':wastebasket: Delete existing dev package',
            'key': 'delete-old-package',
            'depends_on': 'build-wheel',
            'plugins': [{
                'https://github.com/davidt/package-delete-buildkite-plugin.git': {
                    'artifacts': '*.whl',
                    'registry': 'david-trowbridge/python',
                },
            }],
        },
        {
            'label': ':buildkite: Publish package',
            'depends_on': 'delete-old-package',
            'plugins': [{
                'publish-to-packages#v2.2.0': {
                    'artifacts': '*.whl',
                    'registry': 'david-trowbridge/python',
                },
            }],
        },
    ]

    json.dump({'steps': steps}, sys.stdout, indent=2)


if __name__ == '__main__':
    main()
