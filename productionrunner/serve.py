"""Explicit production entry point; run behind a trusted HTTPS ingress."""
import os
from . import create_app


def main():
    if os.environ.get('PR_ENV') != 'production':
        raise ValueError('Hosted startup requires PR_ENV=production.')
    port = int(os.environ.get('PORT', '8080'))
    if not 1 <= port <= 65535:
        raise ValueError('PORT must be between 1 and 65535.')
    from waitress import serve
    app = create_app()
    # Platform ingress must be the only network path to this listener.
    serve(app, host=os.environ.get('PR_BIND_HOST', '127.0.0.1'), port=port, threads=4)


if __name__ == '__main__':
    main()
