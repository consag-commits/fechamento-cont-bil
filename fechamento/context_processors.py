"""context_processors.py — Expõe o papel do usuário para todos os templates."""

from .permissions import is_gestor


def papel(request):
    return {"is_gestor": is_gestor(request.user)}
