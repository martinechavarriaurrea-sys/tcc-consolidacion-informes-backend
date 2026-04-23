from fastapi import Request, status
from fastapi.responses import JSONResponse


class AppError(Exception):
    def __init__(self, message: str, status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class NotFoundError(AppError):
    def __init__(self, resource: str, identifier: str | int):
        super().__init__(f"{resource} '{identifier}' no encontrado", status.HTTP_404_NOT_FOUND)


class DuplicateError(AppError):
    def __init__(self, resource: str, field: str, value: str):
        super().__init__(
            f"{resource} con {field}='{value}' ya existe",
            status.HTTP_409_CONFLICT,
        )


class IntegrationError(AppError):
    def __init__(self, provider: str, detail: str):
        super().__init__(
            f"Error en integración con {provider}: {detail}",
            status.HTTP_502_BAD_GATEWAY,
        )


class ValidationError(AppError):
    def __init__(self, detail: str):
        super().__init__(detail, status.HTTP_422_UNPROCESSABLE_ENTITY)


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.message, "path": str(request.url.path)},
    )


async def generic_error_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"error": "Error interno del servidor", "path": str(request.url.path)},
    )
