"""services.py — Regras de negócio reutilizadas por views e comandos."""

from calendar import monthrange
from datetime import date

from django.db import transaction
from django.utils import timezone

from .models import (
    Ciclo, CicloPrazo, Empresa, Fase, Item, ItemStatus, ModeloChecklist, Processo,
)


def mes_trabalho(ano, mes):
    """Mês de trabalho = mês seguinte à competência (o fechamento é feito no mês seguinte)."""
    return (ano + 1, 1) if mes == 12 else (ano, mes + 1)


def prazo_sugerido(referencia, fase):
    """Data-limite sugerida = dia sugerido da fase no mês de trabalho."""
    ano, mes = map(int, str(referencia).split("-"))
    t_ano, t_mes = mes_trabalho(ano, mes)
    dia = min(fase.prazo_offset_dias or 10, monthrange(t_ano, t_mes)[1])
    return date(t_ano, t_mes, dia)


# ── Cálculo de progresso e atraso (fiel às fórmulas da planilha) ──────────────
def _status_fase(total, feitos, pendentes, atrasada):
    if total == 0:
        return "N/A"
    if feitos == 0:
        return "Pendente de início"
    if pendentes == 0:
        return "Concluído"
    return "Atrasado" if atrasada else "Em andamento"


def resumo_processo(processo, fases, prazos, hoje=None):
    """
    Progresso de um processo, por fase e geral, com lógica de atraso por prazo.
    `fases` = lista de Fase (pré-carregada). `prazos` = {fase_id: data_limite}.
    Requer itens_status__item pré-carregado. Só itens que pontuam entram no cálculo.
    """
    hoje = hoje or timezone.localdate()
    por_fase = {f.id: [] for f in fases}
    for s in processo.itens_status.all():
        por_fase.setdefault(s.item.fase_id, []).append(s)

    linhas_fase = []
    g_total = g_feitos = g_atrasos = 0
    for fase in fases:
        statuses = por_fase.get(fase.id, [])
        scored = [s for s in statuses if s.item.pontua and s.status != ItemStatus.Status.NA]
        feitos = [s for s in scored if s.concluido]
        pendentes = [s for s in scored if not s.concluido]
        total = len(scored)
        deadline = prazos.get(fase.id)
        atrasada = bool(deadline) and hoje > deadline and len(pendentes) > 0
        linhas_fase.append({
            "fase": fase,
            "total": total,
            "feitos": len(feitos),
            "pendentes": len(pendentes),
            "pct": (len(feitos) / total) if total else 0.0,
            "deadline": deadline,
            "atrasada": atrasada,
            "status": _status_fase(total, len(feitos), len(pendentes), atrasada),
        })
        g_total += total
        g_feitos += len(feitos)
        if atrasada:
            g_atrasos += len(pendentes)

    g_pend = g_total - g_feitos
    if g_total == 0:
        status_geral = "N/A"
    elif g_feitos == 0:
        status_geral = "Pendente de início"
    elif g_pend == 0:
        status_geral = "Concluído"
    else:
        status_geral = "Atrasado" if g_atrasos > 0 else "Em andamento"

    return {
        "fases": linhas_fase,
        "total": g_total,
        "feitos": g_feitos,
        "pendencias": g_pend,
        "atrasos": g_atrasos,
        "percentual": (g_feitos / g_total) if g_total else 0.0,
        "status_geral": status_geral,
    }


class AberturaError(Exception):
    """Erro de negócio ao abrir um ciclo (mensagem pronta para o usuário)."""


def resolver_modelo(modelo=None):
    """Retorna o modelo informado ou o modelo ativo mais recente."""
    if isinstance(modelo, ModeloChecklist):
        return modelo
    if modelo:
        try:
            return ModeloChecklist.objects.get(nome=modelo)
        except ModeloChecklist.DoesNotExist:
            raise AberturaError(f"Modelo '{modelo}' não encontrado.")
    ativo = ModeloChecklist.objects.filter(ativo=True).order_by("-criado_em").first()
    if not ativo:
        raise AberturaError("Nenhum modelo ativo. Rode 'seed_template' primeiro.")
    return ativo


@transaction.atomic
def abrir_ciclo(referencia, modelo=None, prazos=None):
    """
    Abre um ciclo de competência AAAA-MM e gera processos + itens pendentes.
    `prazos` = {fase_id: date} — prazos por fase principal; os que faltarem usam
    a sugestão (mês de trabalho). Retorna (ciclo, qtd_processos, qtd_itens).
    """
    try:
        ano, mes = map(int, str(referencia).split("-"))
        data_ref = date(ano, mes, 1)
    except (ValueError, TypeError, AttributeError):
        raise AberturaError("Competência inválida. Use o formato AAAA-MM, ex.: 2026-06")

    modelo = resolver_modelo(modelo)

    if Ciclo.objects.filter(modelo=modelo, referencia=referencia).exists():
        raise AberturaError(f"Já existe um ciclo {referencia} para o modelo “{modelo.nome}”.")

    # Base de empresas: mesmas do ciclo anterior (ainda ativas), se existir.
    # Sem ciclo anterior, cai no padrão de todas as empresas ativas.
    anterior = (
        Ciclo.objects.filter(modelo=modelo, data_referencia__lt=data_ref)
        .order_by("-data_referencia").first()
    )
    if anterior:
        pares_empresa_equipe = [
            (p.empresa, p.equipe) for p in
            anterior.processos.select_related("empresa", "equipe").filter(empresa__ativa=True)
        ]
    else:
        pares_empresa_equipe = [(e, e.equipe) for e in Empresa.objects.filter(ativa=True)]
        if not pares_empresa_equipe:
            raise AberturaError("Nenhuma empresa ativa cadastrada. Cadastre empresas antes de abrir o ciclo.")

    fases_principais = list(Fase.objects.filter(modelo=modelo, principal=True))
    itens_principais = list(Item.objects.filter(fase__modelo=modelo, fase__principal=True))
    if not itens_principais:
        raise AberturaError("O modelo selecionado não tem itens de checklist.")

    # Itens de detalhamento, agrupados pelas empresas que os utilizam (escolha individual)
    from collections import defaultdict
    itens_por_empresa = defaultdict(list)
    detalhes = (
        Fase.objects.filter(modelo=modelo, principal=False)
        .prefetch_related("empresas", "itens")
    )
    for fase in detalhes:
        fitems = list(fase.itens.all())
        for empresa in fase.empresas.all():
            itens_por_empresa[empresa.id].extend(fitems)

    ciclo = Ciclo.objects.create(modelo=modelo, referencia=referencia, data_referencia=data_ref)

    # Prazos por fase principal (informados ou sugeridos pelo mês de trabalho)
    prazos = prazos or {}
    CicloPrazo.objects.bulk_create([
        CicloPrazo(
            ciclo=ciclo, fase=fase,
            data_limite=prazos.get(fase.id) or prazo_sugerido(referencia, fase),
        )
        for fase in fases_principais
    ])

    processos = [Processo(ciclo=ciclo, empresa=e, equipe=eq) for e, eq in pares_empresa_equipe]
    Processo.objects.bulk_create(processos)

    statuses = []
    for p in processos:
        itens = itens_principais + itens_por_empresa.get(p.empresa_id, [])
        statuses.extend(ItemStatus(processo=p, item=item) for item in itens)
    ItemStatus.objects.bulk_create(statuses)

    return ciclo, len(processos), len(statuses)


def adicionar_empresa_ciclo(ciclo, empresa):
    """
    Adiciona uma empresa a um ciclo já aberto, criando Processo + ItemStatus.
    Levanta AberturaError se o ciclo já estiver concluído ou a empresa já estiver no ciclo.
    Retorna o Processo criado.
    """
    if ciclo.status == Ciclo.Status.CONCLUIDO:
        raise AberturaError("Ciclo já concluído — não é possível adicionar empresas.")

    if Processo.objects.filter(ciclo=ciclo, empresa=empresa).exists():
        raise AberturaError(f"'{empresa.razao_social}' já está neste ciclo.")

    modelo = ciclo.modelo
    itens_principais = list(Item.objects.filter(fase__modelo=modelo, fase__principal=True))

    # Itens de detalhamento vinculados especificamente a essa empresa
    from collections import defaultdict
    itens_detalhe = []
    detalhes = (
        Fase.objects.filter(modelo=modelo, principal=False)
        .prefetch_related("empresas", "itens")
    )
    for fase in detalhes:
        if fase.empresas.filter(pk=empresa.pk).exists():
            itens_detalhe.extend(list(fase.itens.all()))

    with transaction.atomic():
        processo = Processo.objects.create(ciclo=ciclo, empresa=empresa, equipe=empresa.equipe)
        itens = itens_principais + itens_detalhe
        ItemStatus.objects.bulk_create([ItemStatus(processo=processo, item=i) for i in itens])

    return processo


def remover_empresa_ciclo(ciclo, processo):
    """
    Remove uma empresa de um ciclo (apaga o Processo em cascata).
    Só permitido se 100% pendente — nenhum item com status feito ou manual.
    Levanta AberturaError caso contrário.
    """
    if ciclo.status == Ciclo.Status.CONCLUIDO:
        raise AberturaError("Ciclo já concluído — não é possível remover empresas.")

    concluidos = processo.itens_status.filter(status__in=ItemStatus.CONCLUIDOS).count()
    if concluidos > 0:
        raise AberturaError(
            f"'{processo.empresa.razao_social}' já tem {concluidos} item(ns) concluído(s) "
            "— só é possível remover empresas 100% pendentes."
        )

    processo.delete()
