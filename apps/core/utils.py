import secrets
import string


def generate_default_password(length=15):
    characters = string.ascii_letters + string.digits
    return "".join(secrets.choice(characters) for _ in range(length))