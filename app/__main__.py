import uvicorn

INGRESS_PORT = 8099


def main() -> None:
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
