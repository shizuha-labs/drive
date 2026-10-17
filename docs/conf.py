# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

import os
import sys

# Add the project to the path for autodoc
sys.path.insert(0, os.path.abspath('..'))

# Setup Django for autodoc (optional - skip if Django not available)
try:
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'drivemanager.settings')
    os.environ.setdefault('USE_S3_STORAGE', '0')  # Use local storage for docs build

    # Create log directory if it doesn't exist (for Django logging)
    log_dir = '/var/log/django'
    if not os.path.exists(log_dir):
        try:
            os.makedirs(log_dir, exist_ok=True)
        except PermissionError:
            # Skip Django setup if we can't create log directory
            pass

    import django
    django.setup()
except Exception as e:
    # Django setup is optional - autodoc won't work but static docs will build
    print(f"Note: Django setup skipped ({e}). Autodoc will be limited.")

# -- Project information -----------------------------------------------------
project = 'Shizuha Drive'
copyright = '2026, Shizuha Platform'
author = 'Shizuha Team'
release = '1.0.0'
version = '1.0'

# -- General configuration ---------------------------------------------------
extensions = [
    'sphinx.ext.autodoc',
    'sphinx.ext.viewcode',
    'sphinx.ext.napoleon',
    'sphinx.ext.intersphinx',
    'sphinx.ext.todo',
    'sphinx.ext.coverage',
    'sphinx_rtd_theme',
    'sphinxcontrib.httpdomain',
    'myst_parser',
    'sphinx_copybutton',
]

# Add support for Markdown files
source_suffix = {
    '.rst': 'restructuredtext',
    '.md': 'markdown',
}

templates_path = ['_templates']
exclude_patterns = ['_build', 'Thumbs.db', '.DS_Store']

# -- Options for HTML output -------------------------------------------------
html_theme = 'sphinx_rtd_theme'
html_static_path = ['_static']

html_theme_options = {
    'logo_only': False,
    'display_version': True,
    'prev_next_buttons_location': 'bottom',
    'style_external_links': True,
    'collapse_navigation': False,
    'sticky_navigation': True,
    'navigation_depth': 4,
    'includehidden': True,
    'titles_only': False,
}

# Custom CSS
html_css_files = [
    'custom.css',
]

# -- Extension configuration -------------------------------------------------

# Napoleon settings (for Google/NumPy docstrings)
napoleon_google_docstring = True
napoleon_numpy_docstring = True
napoleon_include_init_with_doc = True

# Autodoc settings
autodoc_member_order = 'bysource'
autodoc_default_options = {
    'members': True,
    'member-order': 'bysource',
    'special-members': '__init__',
    'undoc-members': True,
    'exclude-members': '__weakref__'
}

# Intersphinx mapping
intersphinx_mapping = {
    'python': ('https://docs.python.org/3', None),
    'django': ('https://docs.djangoproject.com/en/4.2/', 'https://docs.djangoproject.com/en/4.2/_objects/'),
}

# Todo extension
todo_include_todos = True

# MyST parser settings (for Markdown)
myst_enable_extensions = [
    'colon_fence',
    'deflist',
    'tasklist',
]
