import os
import secrets
import pathlib

def setup_env():
    base_dir = pathlib.Path(__file__).parent.parent.resolve()
    env_path = base_dir / ".env"

    if env_path.exists():
        print(f"[*] .env already exists at {env_path}. Skipping generation.")
        return

    secret_key = secrets.token_hex(32)

    # 3. Construct the .env content
    env_content = [
        f"FLASK_SECRET_KEY={secret_key}",
        f"DATABASE_URL=sqlite:///user.db",
    ]

    secret_key = None

    # 4. Write the file
    with open(env_path, "w") as f:
        f.write("\n".join(env_content) + "\n")

    print(f"[+] Created secure .env file at {env_path}")

if __name__ == "__main__":
    setup_env()
