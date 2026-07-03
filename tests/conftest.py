import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ec import SECP256R1, generate_private_key

# Default so bare `uv run pytest` works in a fresh shell (CI/just override it).
os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres")

KID = "test-key"


@pytest.fixture(scope="session")
def ec_key():
    return generate_private_key(SECP256R1())


@pytest.fixture(scope="session")
def jwks_server(ec_key):
    jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(ec_key.public_key()))
    jwk.update({"kid": KID, "alg": "ES256", "use": "sig"})
    body = json.dumps({"keys": [jwk]}).encode()

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}/jwks.json"
    os.environ["SUPABASE_JWKS_URL"] = url
    os.environ["SUPABASE_JWT_ISSUER"] = "https://test.issuer/auth/v1"
    yield url
    srv.shutdown()


@pytest.fixture()
def clean_tables():
    """NOT autouse here — DB-dependent. Integration modules opt in so
    pure-unit tests (test_health, test_errors) never touch Postgres."""
    import psycopg

    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        conn.execute("truncate vehicles, app_events")
    yield


@pytest.fixture()
def make_token(ec_key, jwks_server):
    def _make(sub="11111111-1111-1111-1111-111111111111", email="reviewer@test.dev", **over):
        claims = {
            "sub": sub,
            "email": email,
            "aud": "authenticated",
            "role": "authenticated",
            "iss": os.environ["SUPABASE_JWT_ISSUER"],
            "exp": int(time.time()) + 3600,
        }
        claims.update(over)
        return jwt.encode(claims, ec_key, algorithm="ES256", headers={"kid": KID})

    return _make
