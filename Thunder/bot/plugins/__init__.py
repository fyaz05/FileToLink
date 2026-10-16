"""Bot command handlers (admin / callbacks / common / stream).

Loaded by ``Thunder.__main__.import_plugins`` through
``importlib.util.spec_from_file_location``, which needs no package marker --
but without this file the directory is a namespace package, and coverage.py
only measures files in an importable package. The omit entry that used to
hide these modules from the report was therefore dead config: 900+ statements
of bot surface were neither tested nor measured. With this file in place they
are measured honestly.
"""
