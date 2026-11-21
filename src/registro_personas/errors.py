class DomainError(Exception):
    def __init__(self, status: int, code: str, title: str):
        self.status = status
        self.code = code
        self.title = title
        super().__init__(code)


def forbidden() -> DomainError:
    return DomainError(403, "forbidden", "Operación no autorizada")


def not_found() -> DomainError:
    return DomainError(404, "not-found", "Recurso no encontrado o no divulgable")


def conflict() -> DomainError:
    return DomainError(409, "identity-conflict", "Conflicto pendiente de revisión")
