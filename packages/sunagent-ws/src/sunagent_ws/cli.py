import typer
import uvicorn
from dotenv import load_dotenv
# 加载 .env 文件
load_dotenv()

app = typer.Typer()


@app.command()
def serve(
    # team: str = "",
    host: str = "127.0.0.1",
    port: int = 8084,
    workers: int = 1,
):
    """
    Serve an API Endpoint based on an AutoGen Studio workflow json file.

    Args:
        host (str, optional): Host to run the UI on. Defaults to 127.0.0.1 (localhost).
        port (int, optional): Port to run the UI on. Defaults to 8084
        workers (int, optional): Number of workers to run the UI with. Defaults to 1.

    """

    # os.environ["AUTOGENSTUDIO_TEAM_FILE"] = team

    uvicorn.run(
        "sunagent_ws.web.app:app",
        host=host,
        port=port,
        workers=workers,
        reload=False,
    )


def run():
    app()


if __name__ == "__main__":
    app()
