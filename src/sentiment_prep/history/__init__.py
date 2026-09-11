"""Run history and spend ledger, persisted with SQLAlchemy.

Two small tables and an append-only ledger do not justify a second web framework; SQLAlchemy 2.0
gives typed models, a session, and ``create_all`` for schema setup in a few dozen lines.
"""
