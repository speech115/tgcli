import subprocess
import sys


def test_importing_the_archive_store_does_not_load_telethon():
    """Offline archive reads (`store stats`, `archive search`) need SQLite,
    not Telegram: `import tgcli.archive.<module>` must not drag in Telethon
    through the package's `__init__`."""
    probe = (
        "import sys, tgcli.archive.schema, tgcli.archive.store; "
        "print('telethon' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "False"
