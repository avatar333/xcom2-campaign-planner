"""Pure domain logic (ARCHITECTURE.md §2): no Flask, no SQLAlchemy, no I/O.

Every function here operates on plain Python values (dicts, dataclasses,
primitives) passed in by the caller, so it is testable without a database
and carries zero knowledge of what any particular game object *is* --
only of generic shapes (objects/types/relationships), per ADR-0003/0004.
"""
