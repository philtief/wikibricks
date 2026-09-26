import hashlib
import re
from pathlib import Path

LOCAL_MIGRATIONS = "src/wikibricks/sql/migrations"
SQLITE_MIGRATIONS = "src/wikibricks/sql/sqlite"
REMOTE_MIGRATIONS = "src/wikibricks_remote/sql"
ROOT = Path(__file__).resolve().parents[1]
MIGRATION_DIRECTORIES = (
    LOCAL_MIGRATIONS,
    SQLITE_MIGRATIONS,
    REMOTE_MIGRATIONS,
)
RELEASED_MIGRATIONS = {
    f"{LOCAL_MIGRATIONS}/0001_local_postgres.sql": "2ebaa0d336de5c6cd2f0267540c6f0892563c33f63702adc0be6ab33bd29bd1b",
    f"{LOCAL_MIGRATIONS}/0002_archive_sync.sql": "a2f49ebb14db4e9bb91d806d780ca04db2ee662332c38bbe2d0c67ffcb8878c9",
    f"{LOCAL_MIGRATIONS}/0003_curation_patches.sql": "9fb88b6cf7caf295e6333aa188cad74acfa33326980f46aa02bc0c1de86a444d",
    f"{LOCAL_MIGRATIONS}/0004_archive_replica.sql": "65deeac9d5fdad30dccdf00c3bbe16857bde4707d4f9f768a45e84ef651365a9",
    f"{LOCAL_MIGRATIONS}/0005_remote_maintenance.sql": (
        "ba5ad411cff8fe7572900dcd1493efca4442e22818e4ef80a4192f46345cff5c"
    ),
    f"{LOCAL_MIGRATIONS}/0006_add_link.sql": "90de2f20cb319896227329a4de24f57cdac50f2c752ca1188781952bf4a5d4d8",
    f"{SQLITE_MIGRATIONS}/0001_core.sql": "622f8257f4a8dd9d5db3419ae30f3484f77566094dd416f33f13974d3ca18e8e",
    f"{SQLITE_MIGRATIONS}/0002_sync.sql": "70e60dd7f65b11df52feb20e1a411cd88485a1d2a45de6daae74cb6781eb17cc",
    f"{SQLITE_MIGRATIONS}/0003_backfill_late_tables.sql": (
        "cb5b62c81aa4c8d7c8aa22e11c558c13305ef16806a258567ea0c98cf4c55fbf"
    ),
    f"{REMOTE_MIGRATIONS}/0001_lakebase_search.sql": "b9b2da1ebcf902ed6a10e48dd7bdb4f3d3a9adcb28752bf8dc2e4a1120b17144",
}


def test_released_migrations_are_immutable():
    for relative_path, expected_hash in RELEASED_MIGRATIONS.items():
        path = ROOT / relative_path
        assert path.is_file(), (
            f"{relative_path} changed after release. Add a new forward migration "
            "instead of editing it."
        )
        actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        assert actual_hash == expected_hash, (
            f"{relative_path} changed after release. Add a new forward migration "
            "instead of editing it."
        )


def test_every_migration_is_in_the_manifest():
    current_migrations = {
        path.relative_to(ROOT).as_posix()
        for directory in MIGRATION_DIRECTORIES
        for path in (ROOT / directory).glob("*.sql")
    }
    unlisted_migrations = sorted(current_migrations - set(RELEASED_MIGRATIONS))

    assert not unlisted_migrations, ", ".join(unlisted_migrations)


def test_migration_numbers_are_contiguous_from_0001():
    for relative_directory in MIGRATION_DIRECTORIES:
        directory = ROOT / relative_directory
        numbers = []
        for path in sorted(directory.glob("*.sql")):
            match = re.match(r"^(\d{4})", path.name)
            assert match, f"{relative_directory}/{path.name} must start with a 4-digit number"
            numbers.append(int(match.group(1)))

        assert len(numbers) == len(set(numbers))
        assert sorted(numbers) == list(range(1, len(numbers) + 1))
