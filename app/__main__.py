import signal
import sys

import uvicorn

INGRESS_PORT = 8099


def _exit_cleanly(signum: int, frame: object) -> None:
    sys.exit(0)


def main() -> None:
    # uvicorn shuts down gracefully on SIGTERM and then re-raises the signal
    # with the previous handler restored. With Python's default handler the
    # process would die with exit code 143, which the Supervisor reports as
    # "did not handle SIGTERM"; this handler turns it into a clean exit.
    signal.signal(signal.SIGTERM, _exit_cleanly)
    # proxy_headers stays off: the client address must remain the Supervisor's,
    # otherwise the ingress allowlist in app.main could be bypassed.
    uvicorn.run(
        "app.main:create_app",
        factory=True,
        host="0.0.0.0",
        port=INGRESS_PORT,
        proxy_headers=False,
    )


if __name__ == "__main__":
    main()
