"""permissions.py — Controle de papéis (operador / gestor)."""

from functools import wraps

from django.core.exceptions import PermissionDenied

from .models import Perfil


def is_gestor(user) -> bool:
    """True se o usuário for gestor. Superusuário é sempre gestor."""
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    perfil = getattr(user, "perfil", None)
    return bool(perfil and perfil.papel == Perfil.Papel.GESTOR)


def gestor_required(view_func):
    """Bloqueia (403) quem não for gestor, mesmo acessando a URL na mão."""

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not is_gestor(request.user):
            raise PermissionDenied("Acesso restrito a gestores.")
        return view_func(request, *args, **kwargs)

    return _wrapped


def equipes_do_usuario(user):
    """IDs das equipes que o usuário atende. None = todas (gestor/superusuário)."""
    if is_gestor(user):
        return None
    perfil = getattr(user, "perfil", None)
    if not perfil:
        return []
    return list(perfil.equipes.values_list("id", flat=True))


def filtrar_processos(qs, user, equipe_id=None):
    """Restringe um queryset de Processo às equipes visíveis ao usuário.
    Gestor pode filtrar por uma equipe específica (equipe_id)."""
    visiveis = equipes_do_usuario(user)
    if visiveis is None:  # gestor: vê tudo, mas pode filtrar
        if equipe_id:
            return qs.filter(equipe_id=equipe_id)
        return qs
    return qs.filter(equipe_id__in=visiveis)


def pode_ver_processo(user, processo) -> bool:
    """Se o usuário pode abrir o checklist de um processo (pela equipe)."""
    visiveis = equipes_do_usuario(user)
    if visiveis is None:
        return True
    return processo.equipe_id in visiveis
