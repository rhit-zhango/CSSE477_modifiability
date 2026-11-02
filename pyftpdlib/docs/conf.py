# Copyright (c) 2007, Giampaolo Rodola. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Sphinx config file.

See Sphinx doc at:
https://www.sphinx-doc.org/en/master/usage/configuration.html
"""

import ast
import datetime
import pathlib
import sys

PROJECT_NAME = "pyftpdlib"
AUTHOR = "Giampaolo Rodola"
THIS_YEAR = str(datetime.datetime.now().year)
HERE = pathlib.Path(__file__).resolve().parent
ROOT_DIR = HERE.parent


def get_version():
    path = ROOT_DIR / "pyftpdlib" / "__init__.py"
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)

    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__ver__":
                    if isinstance(node.value, ast.Constant) and isinstance(
                        node.value.value, str
                    ):
                        return node.value.value
    raise ValueError("version not found")


VERSION = get_version()


sys.path.insert(0, str(HERE / "_ext"))

extensions = [
    "sphinx.ext.extlinks",
    "sphinx.ext.intersphinx",
    "sphinx.ext.viewcode",
    "sphinx_copybutton",
    # custom extensions in _ext/ dir
    "availability",
    "check_python_syntax",
]

project = PROJECT_NAME
author = AUTHOR
version = VERSION
release = VERSION
intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
}
extlinks = {
    "gh": ("https://github.com/giampaolo/pyftpdlib/issues/%s", "#%s"),
}
htmlhelp_basename = f"{PROJECT_NAME}-doc"
copybutton_exclude = ".linenos, .gp"

# --- paths

templates_path = ["_templates"]
html_static_path = ["_static"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

# --- theming / visual

html_title = PROJECT_NAME
html_theme = "sphinx_rtd_theme"

copyright = f"2007-{THIS_YEAR}, {AUTHOR}"  # shown in the footer
html_last_updated_fmt = "%b %d, %Y"  # shown in the footer
