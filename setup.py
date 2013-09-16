#!/usr/bin/python
# coding=utf8

from setuptools import find_packages, setup

version='0.1'

setup(
    name = 'RepositoryManager',
#      version = ,
    description = 'Repository manager plugin for Trac',
    author = 'Tobias Föhst',
    author_email = 'foehst@finroc.org',
#      url = 'URL',
#      keywords = 'trac plugin',
    license = "GPL",
    packages = find_packages(exclude = ['*.tests*']),
    include_package_data = True,
    package_data = {
        'repo_mgr': [
            'templates/*',
            'htdocs/css/*'
            ]
        },
    zip_safe = True,
    entry_points = {
        'trac.plugins': [
            'repo_mgr.web_ui = repo_mgr.web_ui',
            'repo_mgr.versioncontrol.svn = repo_mgr.versioncontrol.svn',
            'repo_mgr.versioncontrol.hg = repo_mgr.versioncontrol.hg',
        ]
    }
)
