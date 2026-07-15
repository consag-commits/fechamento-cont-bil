"""views.py — Telas do fechamento: lista de ciclos, consolidado e matriz interativa."""

from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.db.models import Count
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from .forms import (
    EmpresaForm, EquipeForm, OcorrenciaForm, UsuarioCriarForm, UsuarioEditarForm, nome_papel,
)
from .models import (
    CatalogoEmpresa, Ciclo, CicloPrazo, Empresa, Equipe, Fase, IndicadorCeipim,
    ItemStatus, ModeloChecklist, Ocorrencia, Perfil, Processo,
)
from .permissions import (
    gestor_required, is_gestor, filtrar_processos, pode_ver_processo,
)
from .services import (
    AberturaError, abrir_ciclo as abrir_ciclo_service, resumo_processo,
    adicionar_empresa_ciclo, remover_empresa_ciclo, empresas_para_ciclo,
)


def _equipe_ctx(request):
    """Contexto do filtro de equipe (dropdown só aparece para gestor).
    Sem parâmetro na URL, gestor de uma única equipe vê essa equipe por
    padrão — mas o dropdown (inclusive "Todas") continua disponível."""
    if not is_gestor(request.user):
        return {"pode_filtrar": False, "equipe_sel": None, "equipes_filtro": []}

    if "equipe" in request.GET:
        valor = request.GET.get("equipe") or None
        try:
            equipe_sel = int(valor) if valor else None
        except (ValueError, TypeError):
            equipe_sel = None
    else:
        perfil = getattr(request.user, "perfil", None)
        equipes_proprias = list(perfil.equipes.values_list("id", flat=True)) if perfil else []
        equipe_sel = equipes_proprias[0] if len(equipes_proprias) == 1 else None

    return {"pode_filtrar": True, "equipe_sel": equipe_sel, "equipes_filtro": list(Equipe.objects.all())}

_PEND = ItemStatus.Status.PENDENTE
_DONE = ItemStatus.Status.FEITO
_MANUAL = ItemStatus.Status.MANUAL
_NA = ItemStatus.Status.NA


@login_required
def index(request):
    """Lista de ciclos com progresso geral de cada um."""
    dados = []
    for ciclo in Ciclo.objects.select_related("modelo").order_by("data_referencia"):
        stats = ItemStatus.objects.filter(processo__ciclo=ciclo).exclude(status=_NA)
        total = stats.count()
        feitos = stats.filter(status=_DONE).count()
        dados.append({
            "ciclo": ciclo,
            "empresas": ciclo.processos.count(),
            "percentual": (feitos / total) if total else 0.0,
            "pendencias": total - feitos,
        })
    return render(request, "fechamento/index.html", {"dados": dados})


@login_required
def ciclo_consolidado(request, ciclo_id):
    """Painel de Integrações: resumo — uma linha por empresa, % por fase e status."""
    ciclo = get_object_or_404(Ciclo.objects.select_related("modelo"), pk=ciclo_id)
    fases = list(Fase.objects.filter(modelo=ciclo.modelo, principal=True).order_by("ordem"))
    prazos = ciclo.prazos_dict()
    hoje = timezone.localdate()
    ctx_eq = _equipe_ctx(request)
    processos = (
        filtrar_processos(ciclo.processos, request.user, ctx_eq["equipe_sel"])
        .select_related("empresa", "equipe", "ciclo")
        .prefetch_related("itens_status__item__fase")
    )

    linhas = []
    tot_feitos = tot_itens = tot_atrasos = 0
    concluidas = em_andamento = pendentes = atrasadas = 0
    for p in processos:
        r = resumo_processo(p, fases, prazos, hoje)
        linhas.append({"processo": p, "resumo": r})
        tot_feitos += r["feitos"]
        tot_itens += r["total"]
        tot_atrasos += r["atrasos"]
        sg = r["status_geral"]
        if sg == "Concluído":
            concluidas += 1
        elif sg == "Atrasado":
            atrasadas += 1
        elif sg == "Em andamento":
            em_andamento += 1
        else:
            pendentes += 1

    resumo = {
        "empresas": len(linhas),
        "concluidas": concluidas,
        "em_andamento": em_andamento,
        "pendentes": pendentes,
        "atrasadas": atrasadas,
        "pendencias": tot_itens - tot_feitos,
        "atrasos": tot_atrasos,
        "percentual": (tot_feitos / tot_itens) if tot_itens else 0.0,
    }
    return render(request, "fechamento/consolidado.html", {
        "ciclo": ciclo, "fases": fases, "linhas": linhas, "resumo": resumo, **ctx_eq,
    })


def _agrupar_por_subtopico(itens):
    """
    Agrupa itens consecutivos que compartilham o prefixo antes do '—'
    ('Fiscal — Recebido ICMS' → subtópico 'Fiscal'). Itens sem '—' (ex.:
    'Bancária', 'Análise DRE') ficam soltos, sem subtópico. O nome exibido
    (só o sufixo) é resolvido no template pelo filtro `sufixo`.
    """
    blocos = []
    for it in itens:
        nome = it["s"].item.nome
        if "—" in nome:
            label = nome.split("—", 1)[0].strip()
            if blocos and blocos[-1]["tipo"] == "sub" and blocos[-1]["label"] == label:
                blocos[-1]["itens"].append(it)
                continue
            blocos.append({"tipo": "sub", "label": label, "itens": [it]})
        else:
            blocos.append({"tipo": "solto", **it})
    return blocos


@login_required
def processo_matriz(request, processo_id):
    """Checklist de Integrações de uma empresa (Integrações/Conciliações/Análises)."""
    processo = get_object_or_404(
        Processo.objects.select_related("empresa", "ciclo__modelo"), pk=processo_id
    )
    if not pode_ver_processo(request.user, processo):
        raise PermissionDenied("Empresa de outra equipe.")
    principais = list(
        Fase.objects.filter(modelo=processo.ciclo.modelo, principal=True)
        .order_by("ordem").prefetch_related("itens")
    )
    prazos = processo.ciclo.prazos_dict()
    hoje = timezone.localdate()
    statuses = {s.item_id: s for s in processo.itens_status.select_related("item")}

    grupos = []
    for f in principais:
        deadline = prazos.get(f.id)
        vencida = bool(deadline) and hoje > deadline
        itens = []
        feitos = total = 0
        for i in f.itens.all():
            s = statuses.get(i.id)
            if not s:
                continue
            atrasado = vencida and i.pontua and not s.concluido and s.status != ItemStatus.Status.NA
            itens.append({"s": s, "atrasado": atrasado})
            if i.pontua and s.status != ItemStatus.Status.NA:
                total += 1
                if s.concluido:
                    feitos += 1
        grupos.append({
            "fase": f, "deadline": deadline, "blocos": _agrupar_por_subtopico(itens),
            "feitos": feitos, "total": total,
            "pct": (feitos / total) if total else 0.0,
            "atrasada": vencida and feitos < total,
        })

    return render(request, "fechamento/matriz.html", {
        "processo": processo, "grupos": grupos,
        "resumo": resumo_processo(processo, principais, prazos, hoje),
    })


@login_required
@require_POST
def item_set(request, status_id):
    """htmx: define o estado do item e, para 'Feito', a data real de conclusão."""
    s = get_object_or_404(
        ItemStatus.objects.select_related("item__fase", "processo__ciclo"), pk=status_id
    )
    if not pode_ver_processo(request.user, s.processo):
        raise PermissionDenied("Empresa de outra equipe.")
    novo = request.POST.get("status")
    if novo not in ItemStatus.Status.values:
        novo = _PEND
    if request.POST.get("toggle") == "1" and s.status == novo:
        novo = _PEND
    s.status = novo
    if novo == _DONE:
        s.data = parse_date(request.POST.get("data") or "") or timezone.localdate()
    else:
        s.data = None
    s.usuario = request.user
    s.save(update_fields=["status", "data", "usuario", "atualizado_em"])

    fase = s.item.fase
    deadline = s.processo.ciclo.prazo_fase(fase)
    atrasado = bool(
        fase.principal and s.item.pontua and not s.concluido
        and s.status != _NA and deadline and timezone.localdate() > deadline
    )
    return render(request, "fechamento/_item_row.html", {"s": s, "atrasado": atrasado})


# ── Área de Gestão (restrita a gestores) ──────────────────────────────────────
@gestor_required
def gestao_home(request):
    return render(request, "fechamento/gestao/home.html")


@gestor_required
def usuarios_list(request):
    q = request.GET.get("q", "").strip()
    usuarios = (
        User.objects.select_related("perfil")
        .prefetch_related("perfil__equipes")
        .annotate(qtd_ocorrencias=Count("ocorrencias"))
    )
    if q:
        usuarios = usuarios.filter(first_name__icontains=q) | usuarios.filter(username__icontains=q)
    usuarios = usuarios.distinct().order_by("first_name", "username")
    return render(request, "fechamento/gestao/usuarios_list.html", {"usuarios": usuarios, "q": q})


@gestor_required
def usuario_criar(request):
    form = UsuarioCriarForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        messages.success(request, f"Usuário “{user.first_name or user.username}” criado.")
        return redirect("usuarios_list")
    return render(request, "fechamento/gestao/usuario_form.html", {"form": form, "novo": True})


# ── Gestão › Equipes ──────────────────────────────────────────────────────────
@gestor_required
def equipes_list(request):
    equipes = Equipe.objects.annotate(qtd_empresas=Count("empresas"))
    return render(request, "fechamento/gestao/equipes_list.html", {"equipes": equipes})


@gestor_required
def equipe_criar(request):
    form = EquipeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        equipe = form.save()
        messages.success(request, f"Equipe “{equipe.nome}” criada.")
        return redirect("equipes_list")
    return render(request, "fechamento/gestao/equipe_form.html", {"form": form, "novo": True})


@gestor_required
def equipe_editar(request, equipe_id):
    equipe = get_object_or_404(Equipe, pk=equipe_id)
    form = EquipeForm(request.POST or None, instance=equipe)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Equipe atualizada.")
        return redirect("equipes_list")
    return render(request, "fechamento/gestao/equipe_form.html", {
        "form": form, "novo": False, "equipe": equipe, **_membros_ctx(equipe),
    })


def _membros_ctx(equipe):
    """Membros atuais da equipe + usuários disponíveis para adicionar (não-superusuário, fora da equipe)."""
    membros = [
        {"user": u, "rotulo": nome_papel(u)}
        for u in User.objects.filter(perfil__equipes=equipe).order_by("first_name", "username")
    ]
    membros_ids = [m["user"].id for m in membros]
    disponiveis = (
        User.objects.filter(is_superuser=False).exclude(id__in=membros_ids).order_by("first_name", "username")
    )
    disponiveis = [{"user": u, "rotulo": nome_papel(u)} for u in disponiveis]
    return {"membros": membros, "disponiveis": disponiveis}


@gestor_required
@require_POST
def equipe_membro_adicionar(request, equipe_id):
    """htmx: adiciona um usuário à equipe."""
    equipe = get_object_or_404(Equipe, pk=equipe_id)
    user_id = request.POST.get("user_id")
    if user_id:
        user = get_object_or_404(User, pk=user_id, is_superuser=False)
        perfil, _ = Perfil.objects.get_or_create(usuario=user)
        perfil.equipes.add(equipe)
    return render(request, "fechamento/gestao/_equipe_membros.html", {"equipe": equipe, **_membros_ctx(equipe)})


@gestor_required
@require_POST
def equipe_membro_remover(request, equipe_id, user_id):
    """htmx: remove um usuário da equipe."""
    equipe = get_object_or_404(Equipe, pk=equipe_id)
    user = get_object_or_404(User, pk=user_id)
    if hasattr(user, "perfil"):
        user.perfil.equipes.remove(equipe)
    return render(request, "fechamento/gestao/_equipe_membros.html", {"equipe": equipe, **_membros_ctx(equipe)})


# ── Gestão › Abrir ciclo ──────────────────────────────────────────────────────
def _parse_referencia(referencia):
    try:
        ano, mes = map(int, str(referencia).split("-"))
        return date(ano, mes, 1)
    except (ValueError, TypeError, AttributeError):
        return None


def _preview_ciclo_ctx(modelo, referencia, empresa_ids_override=None):
    """Prévia de empresas para abrir um ciclo dessa competência: todas as
    empresas ativas, agrupadas por equipe, com as do ciclo anterior
    pré-marcadas (editável antes de confirmar a abertura).
    `empresa_ids_override`, se informado, substitui a pré-marcação padrão
    (usado para reexibir a seleção do usuário em caso de erro no submit)."""
    data_ref = _parse_referencia(referencia)
    if modelo and data_ref:
        pares_default, anterior = empresas_para_ciclo(modelo, data_ref)
    else:
        pares_default = [(e, e.equipe) for e in Empresa.objects.filter(ativa=True)]
        anterior = None

    if empresa_ids_override is not None:
        ids_marcados = set(empresa_ids_override)
    else:
        ids_marcados = {e.id for e, _ in pares_default}
    todas_ativas = (
        Empresa.objects.filter(ativa=True).select_related("equipe")
        .order_by("equipe__nome", "razao_social")
    )
    empresas_marcadas = [
        {"empresa": e, "marcada": e.id in ids_marcados} for e in todas_ativas
    ]
    return {
        "empresas_marcadas": empresas_marcadas,
        "total_marcadas": len(ids_marcados),
        "empresas_ativas": len(empresas_marcadas),
        "anterior": anterior,
    }


@gestor_required
def ciclo_abrir_preview(request):
    """htmx: atualiza a prévia de empresas ao digitar a competência."""
    modelo = ModeloChecklist.objects.filter(ativo=True).order_by("-criado_em").first()
    contexto = _preview_ciclo_ctx(modelo, request.GET.get("referencia", ""))
    return render(request, "fechamento/gestao/_ciclo_abrir_preview.html", contexto)


@gestor_required
def ciclo_abrir(request):
    modelo = ModeloChecklist.objects.filter(ativo=True).order_by("-criado_em").first()
    fases = list(Fase.objects.filter(modelo=modelo, principal=True)) if modelo else []
    referencia = request.POST.get("referencia", "")
    contexto = {
        **_preview_ciclo_ctx(modelo, referencia),
        "referencia": referencia,
        "fases": fases,
    }
    if request.method == "POST":
        referencia = request.POST.get("referencia", "").strip()
        prazos = {}
        for f in fases:
            d = parse_date(request.POST.get(f"prazo_{f.id}") or "")
            if d:
                prazos[f.id] = d
        empresa_ids = [int(x) for x in request.POST.getlist("empresa_ids") if x.isdigit()]
        # devolve os prazos e a seleção digitados para reexibir em caso de erro
        contexto["prazos_form"] = {f.id: request.POST.get(f"prazo_{f.id}", "") for f in fases}
        contexto.update(_preview_ciclo_ctx(modelo, referencia, empresa_ids_override=empresa_ids))
        try:
            ciclo, n_proc, n_stat = abrir_ciclo_service(referencia, modelo, prazos, empresa_ids=empresa_ids)
        except AberturaError as e:
            messages.error(request, str(e))
            return render(request, "fechamento/gestao/ciclo_form.html", contexto)
        messages.success(request, f"Ciclo {ciclo.competencia_display} aberto: {n_proc} empresas, {n_stat} itens gerados.")
        return redirect("ciclo_consolidado", ciclo_id=ciclo.id)
    return render(request, "fechamento/gestao/ciclo_form.html", contexto)


# ── Gestão › Empresas ─────────────────────────────────────────────────────────
@gestor_required
def empresas_list(request):
    q = request.GET.get("q", "").strip()
    empresas = Empresa.objects.select_related("equipe")
    if q:
        empresas = empresas.filter(razao_social__icontains=q)
    return render(request, "fechamento/gestao/empresas_list.html", {
        "empresas": empresas,
        "equipes": Equipe.objects.all(),
        "q": q,
        "total": Empresa.objects.count(),
        "ativas": Empresa.objects.filter(ativa=True).count(),
    })


@gestor_required
def empresas_catalogo_json(request):
    """Fonte do autocomplete (Tom Select) no cadastro de empresa."""
    dados = list(CatalogoEmpresa.objects.values("id", "razao_social", "cnpj"))
    return JsonResponse(dados, safe=False)


@gestor_required
def empresa_criar(request):
    form = EmpresaForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        empresa = form.save()
        messages.success(request, f"Empresa “{empresa.razao_social}” criada.")
        return redirect("empresas_list")
    return render(request, "fechamento/gestao/empresa_form.html", {"form": form, "novo": True})


@gestor_required
def empresa_editar(request, empresa_id):
    empresa = get_object_or_404(Empresa, pk=empresa_id)
    form = EmpresaForm(request.POST or None, instance=empresa)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Empresa atualizada.")
        return redirect("empresas_list")
    return render(request, "fechamento/gestao/empresa_form.html", {
        "form": form, "novo": False, "empresa": empresa,
    })


@gestor_required
@require_POST
def empresa_toggle_ativa(request, empresa_id):
    """htmx: ativa/desativa a empresa e devolve a célula atualizada."""
    empresa = get_object_or_404(Empresa, pk=empresa_id)
    empresa.ativa = not empresa.ativa
    empresa.save(update_fields=["ativa"])
    return render(request, "fechamento/gestao/_empresa_ativa.html", {"e": empresa})


@gestor_required
@require_POST
def empresa_set_equipe(request, empresa_id):
    """htmx: troca a equipe padrão da empresa (dropdown inline)."""
    empresa = get_object_or_404(Empresa, pk=empresa_id)
    equipe_id = request.POST.get("equipe") or None
    empresa.equipe_id = int(equipe_id) if equipe_id else None
    empresa.save(update_fields=["equipe"])
    return HttpResponse(status=204)


@gestor_required
def usuario_editar(request, user_id):
    usuario = get_object_or_404(User, pk=user_id)
    perfil = getattr(usuario, "perfil", None)
    inicial = {
        "nome": usuario.first_name,
        "email": usuario.email,
        "papel": getattr(perfil, "papel", Perfil.Papel.OPERADOR),
        "cargo": getattr(perfil, "cargo", ""),
        "data_admissao": getattr(perfil, "data_admissao", None),
        "equipes": perfil.equipes.all() if perfil else [],
        "ativo": usuario.is_active,
    }
    form = UsuarioEditarForm(request.POST or None, initial=inicial, usuario=usuario)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Usuário atualizado.")
        return redirect("usuarios_list")
    return render(request, "fechamento/gestao/usuario_form.html", {
        "form": form, "novo": False, "usuario": usuario,
    })


# ── Gestão › Configuração de Ciclos ──────────────────────────────────────────

def _proc_data(processos):
    """Monta lista de dicts com processo + dados de progresso (para config de ciclo)."""
    result = []
    for p in processos:
        concluidos = sum(1 for s in p.itens_status.all() if s.status in ItemStatus.CONCLUIDOS)
        total = p.itens_status.count()
        result.append({
            "processo": p,
            "concluidos": concluidos,
            "total": total,
            "removivel": concluidos == 0,
        })
    return result


def _empresas_disponiveis(ciclo):
    """Empresas ativas que ainda não estão no ciclo."""
    ids_no_ciclo = ciclo.processos.values_list("empresa_id", flat=True)
    return (
        Empresa.objects.filter(ativa=True).exclude(id__in=ids_no_ciclo)
        .select_related("equipe").order_by("equipe__nome", "razao_social")
    )


@gestor_required
def ciclos_list(request):
    """Lista todos os ciclos com status, progresso e link para configurar."""
    dados = []
    for ciclo in Ciclo.objects.select_related("modelo").order_by("-data_referencia"):
        stats = (
            ItemStatus.objects
            .filter(processo__ciclo=ciclo, item__pontua=True)
            .exclude(status=_NA)
        )
        total = stats.count()
        feitos = stats.filter(status__in=ItemStatus.CONCLUIDOS).count()
        dados.append({
            "ciclo": ciclo,
            "empresas": ciclo.processos.count(),
            "percentual": (feitos / total) if total else 0.0,
            "pendencias": total - feitos,
        })
    return render(request, "fechamento/gestao/ciclos_list.html", {"dados": dados})


@gestor_required
def ciclo_config(request, ciclo_id):
    """Configuração de um ciclo: prazos por fase + empresas."""
    ciclo = get_object_or_404(Ciclo.objects.select_related("modelo"), pk=ciclo_id)
    fases = list(Fase.objects.filter(modelo=ciclo.modelo, principal=True).order_by("ordem"))
    prazos = {cp.fase_id: cp for cp in ciclo.prazos.select_related("fase")}
    processos = (
        ciclo.processos
        .select_related("empresa", "equipe")
        .prefetch_related("itens_status")
        .order_by("equipe__nome", "empresa__razao_social")
    )
    return render(request, "fechamento/gestao/ciclo_config.html", {
        "ciclo": ciclo,
        "fases": fases,
        "prazos": prazos,
        "proc_data": _proc_data(processos),
        "empresas_disponiveis": _empresas_disponiveis(ciclo),
        "equipes": list(Equipe.objects.order_by("nome")),
    })


@gestor_required
@require_POST
def ciclo_prazo_editar(request, ciclo_id, fase_id):
    """htmx: salva o prazo de uma fase num ciclo aberto."""
    ciclo = get_object_or_404(Ciclo, pk=ciclo_id)
    if ciclo.status == Ciclo.Status.CONCLUIDO:
        return HttpResponse("Ciclo já concluído.", status=403)
    fase = get_object_or_404(Fase, pk=fase_id)
    nova_data = parse_date(request.POST.get("data_limite") or "")
    if not nova_data:
        return HttpResponse("Data inválida.", status=400)
    prazo, _ = CicloPrazo.objects.update_or_create(
        ciclo=ciclo, fase=fase,
        defaults={"data_limite": nova_data},
    )
    return render(request, "fechamento/gestao/_ciclo_prazo_row.html", {
        "prazo": prazo, "ciclo": ciclo,
    })


@gestor_required
@require_POST
def ciclo_empresa_adicionar(request, ciclo_id):
    """htmx: adiciona uma empresa ao ciclo e devolve a seção de empresas atualizada."""
    ciclo = get_object_or_404(Ciclo.objects.select_related("modelo"), pk=ciclo_id)
    empresa_id = request.POST.get("empresa_id")
    empresa = get_object_or_404(Empresa, pk=empresa_id, ativa=True)
    try:
        adicionar_empresa_ciclo(ciclo, empresa)
    except AberturaError as e:
        return HttpResponse(
            f'<div class="alert alert-warning py-2 small mb-0">{e}</div>', status=200
        )
    return _render_empresas_fragment(request, ciclo)


@gestor_required
@require_POST
def ciclo_empresa_remover(request, ciclo_id, processo_id):
    """htmx: remove uma empresa do ciclo (só se 100% pendente)."""
    ciclo = get_object_or_404(Ciclo, pk=ciclo_id)
    processo = get_object_or_404(Processo, pk=processo_id, ciclo=ciclo)
    try:
        remover_empresa_ciclo(ciclo, processo)
    except AberturaError as e:
        return HttpResponse(
            f'<div class="alert alert-warning py-2 small mb-0">{e}</div>', status=200
        )
    return _render_empresas_fragment(request, ciclo)


def _render_empresas_fragment(request, ciclo):
    """Fragmento htmx com a tabela de empresas do ciclo (usado após add/remove)."""
    processos = (
        ciclo.processos
        .select_related("empresa", "equipe")
        .prefetch_related("itens_status")
        .order_by("equipe__nome", "empresa__razao_social")
    )
    return render(request, "fechamento/gestao/_ciclo_empresas.html", {
        "ciclo": ciclo,
        "proc_data": _proc_data(processos),
        "empresas_disponiveis": _empresas_disponiveis(ciclo),
        "equipes": list(Equipe.objects.order_by("nome")),
    })


@gestor_required
@require_POST
def ciclo_processo_set_equipe(request, ciclo_id, processo_id):
    """htmx: troca a equipe de um processo dentro do ciclo."""
    ciclo = get_object_or_404(Ciclo, pk=ciclo_id)
    if ciclo.status == Ciclo.Status.CONCLUIDO:
        return HttpResponse("Ciclo já concluído.", status=403)
    processo = get_object_or_404(Processo, pk=processo_id, ciclo=ciclo)
    equipe_id = request.POST.get("equipe") or None
    processo.equipe_id = int(equipe_id) if equipe_id else None
    processo.save(update_fields=["equipe"])
    return _render_empresas_fragment(request, ciclo)


@gestor_required
@require_POST
def ciclo_arquivar(request, ciclo_id):
    """Marca um ciclo como Concluído (ação irreversível)."""
    ciclo = get_object_or_404(Ciclo, pk=ciclo_id)
    if ciclo.status != Ciclo.Status.CONCLUIDO:
        ciclo.status = Ciclo.Status.CONCLUIDO
        ciclo.save(update_fields=["status"])
        messages.success(request, f"Ciclo {ciclo.competencia_display} arquivado como Concluído.")
    return redirect("ciclo_config", ciclo_id=ciclo.id)


# ── Indicadores CEIPIM (função independente dos ciclos — só gestor) ───────────
def _linhas_ceipim(empresas, ano):
    """[{'empresa', 'anterior', 'meses': [{'mes','status'}, ...]}] para a
    tabela: coluna do ano anterior (mes=0) + 12 meses do ano selecionado."""
    empresa_ids = [e.id for e in empresas]
    indicadores = IndicadorCeipim.objects.filter(empresa_id__in=empresa_ids, ano__in=[ano - 1, ano])
    lookup = {(i.empresa_id, i.ano, i.mes): i.status for i in indicadores}
    linhas = []
    for e in empresas:
        linhas.append({
            "empresa": e,
            "anterior": lookup.get((e.id, ano - 1, 0), IndicadorCeipim.Status.NA),
            "meses": [
                {"mes": m, "status": lookup.get((e.id, ano, m), IndicadorCeipim.Status.NA)}
                for m in range(1, 13)
            ],
        })
    return linhas


def _ceipim_ctx(ano):
    """Contexto compartilhado pela página e pelos fragmentos htmx de
    adicionar/remover empresa (mantém a mesma lógica em um só lugar)."""
    empresas = Empresa.objects.filter(ativa=True, participa_ceipim=True).order_by("razao_social")
    return {
        "ano": ano,
        "linhas": _linhas_ceipim(empresas, ano),
        "meses": range(1, 13),
        "status_choices": IndicadorCeipim.Status.choices,
        "status_dict": dict(IndicadorCeipim.Status.choices),
        "empresas_disponiveis": (
            Empresa.objects.filter(ativa=True, participa_ceipim=False).order_by("razao_social")
        ),
    }


@gestor_required
def indicadores_ceipim(request):
    """Tabela de status CEIPIM por empresa x mês (só gestor; não depende de Ciclo/Processo/equipe)."""
    hoje = timezone.localdate()
    try:
        ano = int(request.GET.get("ano", hoje.year))
    except (ValueError, TypeError):
        ano = hoje.year

    return render(request, "fechamento/ceipim/indicadores.html", {
        "anos_disponiveis": [2026],
        **_ceipim_ctx(ano),
    })


def _ano_do_post(request):
    valor = request.POST.get("ano", "")
    return int(valor) if valor.isdigit() else timezone.localdate().year


@gestor_required
@require_POST
def ceipim_empresa_adicionar(request):
    """htmx: inclui uma empresa na lista de Indicadores CEIPIM."""
    empresa = get_object_or_404(Empresa, pk=request.POST.get("empresa_id"), ativa=True)
    empresa.participa_ceipim = True
    empresa.save(update_fields=["participa_ceipim"])
    return render(request, "fechamento/ceipim/_conteudo.html", _ceipim_ctx(_ano_do_post(request)))


@gestor_required
@require_POST
def ceipim_empresa_remover(request, empresa_id):
    """htmx: remove uma empresa da lista de Indicadores CEIPIM (não apaga os dados já gravados)."""
    empresa = get_object_or_404(Empresa, pk=empresa_id)
    empresa.participa_ceipim = False
    empresa.save(update_fields=["participa_ceipim"])
    return render(request, "fechamento/ceipim/_conteudo.html", _ceipim_ctx(_ano_do_post(request)))


@gestor_required
@require_POST
def ceipim_set_status(request, empresa_id, ano, mes):
    """htmx: define o status de uma célula (empresa x competência)."""
    empresa = get_object_or_404(Empresa, pk=empresa_id)
    novo = request.POST.get("status")
    if novo not in IndicadorCeipim.Status.values:
        return HttpResponse("Status inválido.", status=400)
    indicador, _created = IndicadorCeipim.objects.get_or_create(empresa=empresa, ano=ano, mes=mes)
    indicador.status = novo
    indicador.save(update_fields=["status", "atualizado_em"])
    return HttpResponse(status=204)


@gestor_required
@require_POST
def ceipim_bulk_set(request):
    """Aplica um status a várias empresas de uma vez, para uma competência."""
    ano = request.POST.get("ano", "")
    mes = request.POST.get("mes", "")
    status = request.POST.get("status", "")
    empresa_ids = [int(x) for x in request.POST.getlist("empresa_ids") if x.isdigit()]

    if not (ano.isdigit() and mes.isdigit()) or status not in IndicadorCeipim.Status.values or not empresa_ids:
        messages.error(request, "Selecione ao menos uma empresa e um status válido.")
        return redirect(f"{reverse('indicadores_ceipim')}?ano={ano}")

    empresas = Empresa.objects.filter(id__in=empresa_ids)
    atualizadas = 0
    for empresa in empresas:
        indicador, _created = IndicadorCeipim.objects.get_or_create(empresa=empresa, ano=int(ano), mes=int(mes))
        indicador.status = status
        indicador.save(update_fields=["status", "atualizado_em"])
        atualizadas += 1

    messages.success(request, f"{atualizadas} empresa(s) atualizada(s).")
    return redirect(f"{reverse('indicadores_ceipim')}?ano={ano}")


# ── Gestão › Perfil do funcionário (hub) e Ocorrências — só gestor ────────────
def _tempo_de_casa(data_admissao, hoje=None):
    """Texto 'X anos Y meses' a partir da data de admissão (ou None)."""
    if not data_admissao:
        return None
    hoje = hoje or timezone.localdate()
    meses = (hoje.year - data_admissao.year) * 12 + (hoje.month - data_admissao.month)
    if hoje.day < data_admissao.day:
        meses -= 1
    meses = max(meses, 0)
    anos, resto = divmod(meses, 12)
    partes = []
    if anos:
        partes.append(f"{anos} ano{'s' if anos != 1 else ''}")
    if resto:
        partes.append(f"{resto} {'meses' if resto != 1 else 'mês'}")
    return " ".join(partes) or "menos de 1 mês"


@gestor_required
def usuario_perfil(request, user_id):
    """Perfil do funcionário (hub): dados + indicadores + ocorrências.
    O POST registra uma nova ocorrência."""
    funcionario = get_object_or_404(User.objects.select_related("perfil"), pk=user_id)
    form = OcorrenciaForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        ocorrencia = form.save(commit=False)
        ocorrencia.funcionario = funcionario
        ocorrencia.autor = request.user
        ocorrencia.save()
        messages.success(request, "Ocorrência registrada.")
        return redirect("usuario_perfil", user_id=funcionario.id)
    perfil = getattr(funcionario, "perfil", None)
    return render(request, "fechamento/gestao/usuario_perfil.html", {
        "funcionario": funcionario,
        "perfil": perfil,
        "tempo_de_casa": _tempo_de_casa(getattr(perfil, "data_admissao", None)),
        "form": form,
        "ocorrencias": funcionario.ocorrencias.select_related("autor").all(),
    })


@gestor_required
def ocorrencia_editar(request, ocorrencia_id):
    """Edita uma ocorrência existente."""
    ocorrencia = get_object_or_404(Ocorrencia.objects.select_related("funcionario"), pk=ocorrencia_id)
    form = OcorrenciaForm(request.POST or None, instance=ocorrencia)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Ocorrência atualizada.")
        return redirect("usuario_perfil", user_id=ocorrencia.funcionario_id)
    return render(request, "fechamento/gestao/ocorrencia_form.html", {
        "form": form, "ocorrencia": ocorrencia, "funcionario": ocorrencia.funcionario,
    })


@gestor_required
@require_POST
def ocorrencia_remover(request, ocorrencia_id):
    """Apaga uma ocorrência."""
    ocorrencia = get_object_or_404(Ocorrencia, pk=ocorrencia_id)
    user_id = ocorrencia.funcionario_id
    ocorrencia.delete()
    messages.success(request, "Ocorrência removida.")
    return redirect("usuario_perfil", user_id=user_id)
