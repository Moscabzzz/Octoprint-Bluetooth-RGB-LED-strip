# -*- coding: utf-8 -*-

from setuptools import setup

plugin_identifier = "physical_control"
plugin_package = "octoprint_physical_control"
plugin_name = "Physical Control"
plugin_version = "0.1.0"
plugin_description = """Controls physical hardware like LEDs, buzzers, and shutdown buttons."""
plugin_author = "Author"
plugin_author_email = "author@example.com"
plugin_url = "https://github.com/user/OctoPrint-PhysicalControl"
plugin_license = "AGPLv3"

plugin_requires = ["bleak"]

plugin_additional_data = []
plugin_additional_packages = []
plugin_ignored_packages = []

additional_setup_parameters = {}

setup(
    name=plugin_name,
    version=plugin_version,
    description=plugin_description,
    author=plugin_author,
    author_email=plugin_author_email,
    url=plugin_url,
    license=plugin_license,
    packages=[plugin_package],
    include_package_data=True,
    install_requires=plugin_requires,
    entry_points={
        "octoprint.plugin": [
            "{} = {}".format(plugin_identifier, plugin_package)
        ]
    },
    **additional_setup_parameters
)
