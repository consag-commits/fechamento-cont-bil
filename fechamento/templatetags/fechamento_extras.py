"""Filtros de template para exibir status de itens do checklist."""

from django import template
from django.utils import timezone

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


_MESES_ABREV = {
    1: "jan", 2: "fev", 3: "mar", 4: "abr", 5: "mai", 6: "jun",
    7: "jul", 8: "ago", 9: "set", 10: "out", 11: "nov", 12: "dez",
}


@register.filter
def mes_abrev(mes):
    return _MESES_ABREV.get(int(mes), str(mes))


@register.filter
def data_relativa(data):
    """Data em linguagem do dia a dia: 'Hoje', 'Amanhã', 'Ontem' ou dd/mm."""
    if not data:
        return "—"
    dias = (data - timezone.localdate()).days
    if dias == 0:
        return "Hoje"
    if dias == 1:
        return "Amanhã"
    if dias == -1:
        return "Ontem"
    return data.strftime("%d/%m")


@register.filter
def data_atrasada(data):
    """True se a previsão já passou (para destacar em vermelho)."""
    return bool(data) and data < timezone.localdate()
