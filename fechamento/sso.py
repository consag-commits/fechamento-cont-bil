"""
sso.py — Entrada vinda do Portal de Sistemas.

O Portal autentica a pessoa e a traz para cá com um ticket assinado, de vida
curta e uso único. Aqui o ticket é conferido e a sessão do Django é aberta.
Nenhuma senha atravessa: o que se confia é a assinatura do Portal.

O login próprio do fechamento continua existindo — este é um caminho a mais,
não um substituto. Fechar a porta antiga é uma decisão separada, tomada só
depois que este caminho estiver provado.
"""

import logging

from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.models import User
from django.core import signing
from django.db import IntegrityError
from django.http import HttpResponseForbidden, HttpResponseServerError
from django.shortcuts import redirect
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import Perfil, TicketPortal

logger = logging.getLogger(__name__)

SALT = "portal-sso"
VALIDADE_SEGUNDOS = 60
SISTEMA = "fechamento"


@csrf_exempt  # o POST vem de outro domínio; quem autentica aqui é a assinatura
@require_POST
def entrar_pelo_portal(request):
    if not settings.PORTAL_SSO_SECRET:
        logger.error("PORTAL_SSO_SECRET não configurado — entrada pelo portal indisponível.")
        return HttpResponseServerError("Entrada pelo portal não configurada.")

    try:
        dados = signing.loads(
            request.POST.get("t", ""),
            key=settings.PORTAL_SSO_SECRET,
            salt=SALT,
            max_age=VALIDADE_SEGUNDOS,
        )
    except signing.SignatureExpired:
        logger.warning("Ticket do portal expirado.")
        return HttpResponseForbidden("Ticket expirado. Volte ao portal e entre de novo.")
    except signing.BadSignature:
        logger.warning("Ticket do portal com assinatura inválida.")
        return HttpResponseForbidden("Ticket inválido.")

    if dados.get("sistema") != SISTEMA:
        # Ticket emitido para outro sistema não vale aqui.
        logger.warning("Ticket destinado a %r recusado.", dados.get("sistema"))
        return HttpResponseForbidden("Ticket inválido.")

    username = (dados.get("usuario") or "").strip().lower()
    if not username:
        return HttpResponseForbidden("Ticket sem usuário.")

    try:
        TicketPortal.objects.create(jti=dados["jti"], usuario=username)
    except (IntegrityError, KeyError):
        # Já foi usado: é replay, não um acesso legítimo.
        logger.warning("Ticket repetido para %s.", username)
        return HttpResponseForbidden("Este ticket já foi usado.")

    usuario = User.objects.filter(username__iexact=username).first()

    if usuario is None:
        # Quem é criado no portal entra aqui como operador sem equipe: enxerga o
        # sistema, mas nenhuma empresa até alguém atribuir. Recusar a entrada
        # obrigaria a cadastrar a mesma pessoa em dois lugares.
        usuario = User(username=username, email=dados.get("email") or "")
        nome = (dados.get("nome") or "").strip()
        if nome:
            partes = nome.split(" ", 1)
            usuario.first_name = partes[0]
            usuario.last_name = partes[1] if len(partes) > 1 else ""
        usuario.set_unusable_password()
        usuario.save()
        logger.info("Usuário %s criado a partir do portal.", username)

    if not usuario.is_active:
        return HttpResponseForbidden("Este acesso está desativado.")

    Perfil.objects.get_or_create(usuario=usuario)

    login(request, usuario, backend="django.contrib.auth.backends.ModelBackend")
    return redirect("index")
