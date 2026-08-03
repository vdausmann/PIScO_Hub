"""Bootstrap an API token from the command line.

Usage: python scripts/create_api_token.py <username> <token-name>
Prints the plaintext token once; only its hash is stored.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.factory import create_app
from app.backend.models import User


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)

    username, token_name = sys.argv[1], sys.argv[2]
    app = create_app(create_worker=False)

    with app.app_context():
        from app.routes.api_v1.auth import create_token

        user = User.query.filter_by(username=username).first()
        if user is None:
            print(f"[!] User '{username}' not found.")
            sys.exit(1)

        row, plaintext = create_token(user.id, token_name)
        print(f"[*] Token '{row.name}' created for user '{username}'.")
        print("[*] Store it now; it cannot be retrieved again:")
        print(plaintext)


if __name__ == "__main__":
    main()
