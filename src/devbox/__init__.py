"""DevBox: a multi-agent LangChain assistant for your Windows PC (web UI)."""


def main() -> None:
    from devbox.server import main as serve

    serve()
