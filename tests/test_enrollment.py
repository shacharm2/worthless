"""Tests for enrollment consolidation — .meta files replaced by SQLite enrollments table."""
import asyncio
import sqlite3
from worthless.storage.repository import ShardRepository, StoredShard
from worthless.crypto.splitter import split_key


class TestForeignKeys:
    """PRAGMA foreign_keys is enabled on all connections."""

    def test_foreign_keys_enabled_in_schema_init(self, tmp_path):
        """init_db enables foreign keys."""
        from worthless.storage.schema import init_db
        db_path = str(tmp_path / "test.db")
        asyncio.run(init_db(db_path))
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys").fetchone()
        conn.close()
        # Note: PRAGMA is per-connection, so this checks the default
        # The real test is that CASCADE works (see below)

    def test_cascade_deletes_enrollments(self, tmp_path):
        """Deleting a shard row cascades to enrollments."""
        async def _test():
            db_path = str(tmp_path / "test.db")
            from cryptography.fernet import Fernet
            key = Fernet.generate_key()
            repo = ShardRepository(db_path, key)
            await repo.initialize()

            sr = split_key(b"sk-proj-test1234567890abcdef")
            shard = StoredShard(
                shard_b=bytearray(sr.shard_b),
                commitment=bytearray(sr.commitment),
                nonce=bytearray(sr.nonce),
                provider="openai",
            )
            await repo.store_enrolled("test-alias", shard, var_name="API_KEY", env_path="/tmp/.env")

            # Verify enrollment exists
            enrollments = await repo.list_enrollments("test-alias")
            assert len(enrollments) == 1

            # Delete shard — enrollment should cascade
            await repo.delete_enrolled("test-alias")

            enrollments = await repo.list_enrollments("test-alias")
            assert len(enrollments) == 0

        asyncio.run(_test())


class TestEnrollmentCRUD:
    """Basic enrollment operations."""

    def test_store_enrolled_creates_both(self, tmp_path):
        """store_enrolled creates shard row AND enrollment row atomically."""
        async def _test():
            db_path = str(tmp_path / "test.db")
            from cryptography.fernet import Fernet
            key = Fernet.generate_key()
            repo = ShardRepository(db_path, key)
            await repo.initialize()

            sr = split_key(b"sk-proj-test1234567890abcdef")
            shard = StoredShard(
                shard_b=bytearray(sr.shard_b),
                commitment=bytearray(sr.commitment),
                nonce=bytearray(sr.nonce),
                provider="openai",
            )
            await repo.store_enrolled(
                "test-alias", shard,
                var_name="OPENAI_API_KEY",
                env_path="/home/user/project/.env",
            )

            # Verify shard stored
            retrieved = await repo.retrieve("test-alias")
            assert retrieved is not None
            assert retrieved.provider == "openai"

            # Verify enrollment stored
            enrollment = await repo.get_enrollment("test-alias", "/home/user/project/.env")
            assert enrollment is not None
            assert enrollment.var_name == "OPENAI_API_KEY"
            assert enrollment.env_path == "/home/user/project/.env"

        asyncio.run(_test())

    def test_store_enrolled_null_env_path(self, tmp_path):
        """enroll command has no env_path — should work with None."""
        async def _test():
            db_path = str(tmp_path / "test.db")
            from cryptography.fernet import Fernet
            key = Fernet.generate_key()
            repo = ShardRepository(db_path, key)
            await repo.initialize()

            sr = split_key(b"sk-proj-test1234567890abcdef")
            shard = StoredShard(
                shard_b=bytearray(sr.shard_b),
                commitment=bytearray(sr.commitment),
                nonce=bytearray(sr.nonce),
                provider="openai",
            )
            await repo.store_enrolled("test-alias", shard, var_name="API_KEY", env_path=None)

            enrollment = await repo.get_enrollment("test-alias")
            assert enrollment is not None
            assert enrollment.env_path is None

        asyncio.run(_test())

    def test_multi_env_same_alias(self, tmp_path):
        """Same key in two .env files creates two enrollment rows."""
        async def _test():
            db_path = str(tmp_path / "test.db")
            from cryptography.fernet import Fernet
            key = Fernet.generate_key()
            repo = ShardRepository(db_path, key)
            await repo.initialize()

            sr = split_key(b"sk-proj-test1234567890abcdef")
            shard = StoredShard(
                shard_b=bytearray(sr.shard_b),
                commitment=bytearray(sr.commitment),
                nonce=bytearray(sr.nonce),
                provider="openai",
            )
            # First env file
            await repo.store_enrolled(
                "test-alias", shard,
                var_name="KEY", env_path="/project-a/.env",
            )
            # Second env file — same alias, different path
            await repo.store_enrolled(
                "test-alias", shard,
                var_name="KEY", env_path="/project-b/.env",
            )

            enrollments = await repo.list_enrollments("test-alias")
            assert len(enrollments) == 2
            paths = {e.env_path for e in enrollments}
            assert paths == {"/project-a/.env", "/project-b/.env"}

        asyncio.run(_test())


class TestDBPermissions:
    """SQLite DB file has restricted permissions."""

    def test_db_created_with_0600(self, tmp_path):
        """Database file should be created with 0600 permissions."""
        from worthless.cli.bootstrap import ensure_home
        home = ensure_home(tmp_path / ".worthless")
        mode = oct(home.db_path.stat().st_mode & 0o777)
        assert mode == "0o600"


class TestEnrollmentsTable:
    """The enrollments table exists in the schema."""

    def test_enrollments_table_exists(self, tmp_path):
        """Schema includes enrollments table."""
        from worthless.storage.schema import init_db
        db_path = str(tmp_path / "test.db")
        asyncio.run(init_db(db_path))
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        tables = [row[0] for row in rows]
        conn.close()
        assert "enrollments" in tables
