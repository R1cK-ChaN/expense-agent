from fastapi import FastAPI

from config.settings import load_settings


def create_app() -> FastAPI:
    settings = load_settings()
    application = FastAPI(title="Expense Agent")

    @application.get("/health")
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "service": settings.service_name,
        }

    return application


app = create_app()
