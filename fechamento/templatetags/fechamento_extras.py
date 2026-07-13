"""Filtros de template para exibir status de itens do checklist."""

from django import template

register = template.Library()

_BG = {
    "feito": "table-success",
    "manual": "table-info",
    "na": "table-secondary text-muted",
    "pendente": "",
}
_ICON = {
    "feito": "bi-check-circle-fill text-success",
    "manual": "bi-hand-index-thumb-fill text-info",
    "na": "bi-slash-circle text-muted",
    "pendente": "bi-circle text-secondary",
}


@register.filter
def status_bg(status):
    return _BG.get(status, "")


@register.filter
def status_icon(status):
    return _ICON.get(status, "bi-circle")


@register.filter
def dictkey(d, key):
    """Acessa d[key] no template (retorna '' se não houver)."""
    if not d:
        return ""
    return d.get(key, "")


@register.filter
def sufixo(nome):
    """Parte depois do '—' (ex.: 'Financeiro — Solicitado' -> 'Solicitado'). Sem '—', retorna o nome inteiro."""
    if "—" in nome:
        return nome.split("—", 1)[1].strip()
    return nome
