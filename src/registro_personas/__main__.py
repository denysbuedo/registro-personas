import argparse

import uvicorn


def local_port(value: str) -> int:
    port = int(value)
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Registro de Personas — local private API")
    parser.add_argument("--port", type=local_port, default=8000)
    args = parser.parse_args(argv)
    # Loopback only. Default access log includes path/query and can disclose references.
    uvicorn.run("registro_personas.api:app", host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
