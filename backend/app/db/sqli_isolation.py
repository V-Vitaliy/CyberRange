"""Provision a read-only SQLi surface; never run injectable SQL as an administrator.

This module does not connect on import. Provisioning is explicit and transactional.
The reader owns nothing and is never a member of the non-login view-owner role.
"""
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text


class IsolationError(Exception):
    """Messages must be safe to print without connection details or passwords."""


@dataclass(frozen=True)
class LabObjects:
    lab_id: UUID

    def __post_init__(self):
        object.__setattr__(self, "lab_id", UUID(str(self.lab_id)))

    @property
    def schema(self):
        return "sqli_" + self.lab_id.hex

    @property
    def reader(self):
        return "sqli_read_" + self.lab_id.hex

    @property
    def owner(self):
        return "sqli_view_" + self.lab_id.hex


def _identifier(value):
    return '"' + value.replace('"', '""') + '"'


async def check_reader_boundary(conn, objects):
    """Fail closed on ambient grants, memberships, writable schemas or routines.

    Catalog metadata remains visible, as for ordinary PostgreSQL users. This is
    not a container/resource isolation boundary. Public temporary-table rights
    are not changed; no shared database grants are revoked by this code.
    """
    params = {"reader": objects.reader, "schema": objects.schema,
              "owner": objects.owner}
    roles = (await conn.execute(text("""
        SELECT rolname, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole,
               rolreplication, rolbypassrls
        FROM pg_catalog.pg_roles WHERE rolname IN (:reader, :owner)
    """), params)).mappings().all()
    if len(roles) != 2:
        raise IsolationError("SQLi roles are missing.")
    for role in roles:
        if (role["rolcanlogin"] != (role["rolname"] == objects.reader)
                or any(role[key] for key in ("rolsuper", "rolcreatedb", "rolcreaterole",
                                             "rolreplication", "rolbypassrls"))):
            raise IsolationError("SQLi roles have unsafe attributes.")
    if await conn.scalar(text("""
        SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m
          JOIN pg_catalog.pg_roles r ON r.oid=m.member
          WHERE r.rolname IN (:reader, :owner))
    """), params):
        raise IsolationError("SQLi roles must not have role memberships.")
    if await conn.scalar(text("""
        SELECT has_database_privilege(:reader, current_database(), 'CREATE')
          OR EXISTS (SELECT 1 FROM pg_catalog.pg_namespace n
                     WHERE has_schema_privilege(:reader, n.oid, 'CREATE')
                       AND n.nspname NOT LIKE 'pg_temp_%')
    """), params):
        raise IsolationError("SQLi reader can create persistent objects; review PUBLIC grants.")
    if await conn.scalar(text("""
        SELECT EXISTS (
          SELECT 1 FROM pg_catalog.pg_class c
          JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
          WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
            AND n.nspname NOT LIKE 'pg_toast%'
            AND n.nspname NOT LIKE 'pg_temp_%'
            AND c.relkind IN ('r','p','v','m','f')
            AND (
              (NOT (n.nspname=:schema AND c.relname IN ('chat_history','audit_logs'))
               AND (has_table_privilege(:reader,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                    OR has_any_column_privilege(:reader,c.oid,'SELECT,INSERT,UPDATE,REFERENCES')))
              OR has_table_privilege(:reader,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
              OR has_any_column_privilege(:reader,c.oid,'INSERT,UPDATE,REFERENCES')
            ))
    """), params):
        raise IsolationError("SQLi reader has unexpected data access; review PUBLIC grants.")
    if await conn.scalar(text("""
        SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_class c
          JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
          WHERE CASE WHEN c.relkind='S' AND n.nspname NOT LIKE 'pg_temp_%'
            THEN has_sequence_privilege(:reader,c.oid,'USAGE,SELECT,UPDATE')
            ELSE FALSE END)
          OR EXISTS (SELECT 1 FROM pg_catalog.pg_proc p
          JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
          WHERE n.nspname NOT IN ('pg_catalog','information_schema')
            AND n.nspname NOT LIKE 'pg_temp_%'
            AND has_function_privilege(:reader,p.oid,'EXECUTE'))
          OR EXISTS (SELECT 1 FROM pg_catalog.pg_foreign_server s
                     WHERE has_server_privilege(:reader,s.oid,'USAGE'))
    """), params):
        raise IsolationError("SQLi reader can access extra routines, sequences or foreign servers.")
    views = (await conn.execute(text("""
        SELECT c.relname, c.relkind::text AS relkind, r.rolname AS owner, c.reloptions,
               has_table_privilege(:reader,c.oid,'SELECT') AS readable
        FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_catalog.pg_roles r ON r.oid=c.relowner
        WHERE n.nspname=:schema
    """), params)).mappings().all()
    if (len(views) != 2
            or {v["relname"] for v in views} != {"chat_history", "audit_logs"}
            or any(v["relkind"] != 'v' or v["owner"] != objects.owner
                   or not v["readable"] or 'security_barrier=true' not in (v["reloptions"] or [])
                   or 'security_invoker=true' in (v["reloptions"] or []) for v in views)):
        raise IsolationError("SQLi views are missing or incorrectly protected.")


async def provision_lab(conn, lab_id, password):
    """Create NEW lab objects atomically in the caller's transaction.

    Existing objects cause an error, never takeover, password rotation or repair.
    Only an explicit admin CLI should call this. Source tables must already exist
    in public. No tables, existing privileges, documents or flags are changed.
    """
    objects = LabObjects(lab_id)
    if not conn.in_transaction():
        raise IsolationError("Provisioning requires an explicit transaction.")
    if not isinstance(password, str) or len(password) < 24 or '\x00' in password:
        raise IsolationError("Use a SQLi password of at least 24 characters without NUL.")
    await conn.execute(text("SET LOCAL lock_timeout = '5s'"))
    await conn.execute(text("SET LOCAL statement_timeout = '15s'"))
    await conn.execute(text("SELECT pg_advisory_xact_lock(721034, 59)"))
    exists = await conn.scalar(text("""
        SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname IN (:reader,:owner))
            OR EXISTS (SELECT 1 FROM pg_catalog.pg_namespace WHERE nspname=:schema)
    """), {"reader": objects.reader, "owner": objects.owner, "schema": objects.schema})
    if exists:
        raise IsolationError("SQLi objects already exist; inspect them instead of overwriting.")
    if not await conn.scalar(text("""
        SELECT EXISTS (SELECT 1 FROM public.game_sessions WHERE lab_instance_id=:lab)
    """), {"lab": objects.lab_id}):
        raise IsolationError("Prepare the application's lab/session before SQLi provisioning.")
    owner, reader, schema = map(_identifier, (objects.owner, objects.reader, objects.schema))
    # quote_literal runs on the server with a bind parameter; no SQLAlchemy bind
    # parsing of a password interpolated into text(). exec_driver_sql sends DDL.
    literal = await conn.scalar(text("SELECT pg_catalog.quote_literal(:password)"),
                                {"password": password})
    attributes = "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS"
    await conn.exec_driver_sql(f"CREATE ROLE {owner} NOLOGIN {attributes}")
    await conn.exec_driver_sql(f"CREATE ROLE {reader} LOGIN {attributes} CONNECTION LIMIT 4 PASSWORD {literal}")
    database = _identifier(await conn.scalar(text("SELECT current_database()")))
    await conn.exec_driver_sql(f"GRANT CONNECT ON DATABASE {database} TO {reader}")
    await conn.exec_driver_sql(f"GRANT USAGE ON SCHEMA public TO {owner}")
    for table, columns in (
        ('chat_threads', 'id,session_id,title,messages,created_at,updated_at'),
        ('game_sessions', 'id,lab_instance_id'),
        ('security_audit_logs', 'id,lab_instance_id,timestamp,event_type,payload'),
    ):
        await conn.exec_driver_sql(f"GRANT SELECT ({columns}) ON public.{table} TO {owner}")
    await conn.exec_driver_sql(f"CREATE SCHEMA {schema} AUTHORIZATION {owner}")
    await conn.exec_driver_sql(f"REVOKE ALL ON SCHEMA {schema} FROM PUBLIC")
    # The UUID is canonical, immutable DDL, not current_setting() or caller input.
    await conn.exec_driver_sql(f"""
        CREATE VIEW {schema}.chat_history WITH (security_barrier=true) AS
        SELECT t.id,t.session_id,t.title,t.messages,t.created_at,t.updated_at
        FROM public.chat_threads t JOIN public.game_sessions s ON s.id=t.session_id
        WHERE s.lab_instance_id='{objects.lab_id}'::uuid
    """)
    await conn.exec_driver_sql(f"""
        CREATE VIEW {schema}.audit_logs WITH (security_barrier=true) AS
        SELECT id,timestamp,event_type,payload FROM public.security_audit_logs
        WHERE lab_instance_id='{objects.lab_id}'::uuid
    """)
    for view in ('chat_history', 'audit_logs'):
        await conn.exec_driver_sql(f"ALTER VIEW {schema}.{view} OWNER TO {owner}")
        await conn.exec_driver_sql(f"REVOKE ALL ON {schema}.{view} FROM PUBLIC")
        await conn.exec_driver_sql(f"GRANT SELECT ON {schema}.{view} TO {reader}")
    await conn.exec_driver_sql(f"GRANT USAGE ON SCHEMA {schema} TO {reader}")
    await conn.exec_driver_sql(f"ALTER ROLE {reader} IN DATABASE {database} SET search_path = pg_catalog, {schema}")
    await conn.exec_driver_sql(f"ALTER ROLE {reader} SET statement_timeout = '3s'")
    await conn.exec_driver_sql(f"ALTER ROLE {reader} SET lock_timeout = '1s'")
    await conn.exec_driver_sql(f"ALTER ROLE {reader} SET default_transaction_read_only = on")
    await check_reader_boundary(conn, objects)
    return objects
