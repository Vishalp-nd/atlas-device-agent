"""Backward-compatible Atlas Streamlit entrypoint."""

from atlas.streamlit_ui import configure_app, render_atlas_page


def main() -> None:
    configure_app()
    render_atlas_page()


if __name__ == "__main__":
    main()
