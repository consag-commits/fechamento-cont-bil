"""views.py — Telas do fechamento: lista de ciclos, consolidado e matriz interativa."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.db.models import Count
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from .forms import EmpresaForm, EquipeForm, UsuarioCriarForm, UsuarioEditarForm, nome_papel
from .models import (
    CatalogoEmpresa, Ciclo, CicloPrazo, Empresa, Equipe, Fase, ItemStatus,
    ModeloChecklist, Perfil, Processo,
)
from .permissions import (
    gestor_required, is_gestor, filtrar_processos, pode_ver_processo,
)
from .services import (
    AberturaError, abrir_ciclo as abrir_ciclo_service, resumo_processo,
    adicionar_empresa_ciclo, remover_empresa_ciclo,
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
    for ciclo in Ciclo.objects.select_related("modelo"):
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
    usuarios = User.objects.select_related("perfil").order_by("first_name", "username")
    return render(request, "fechamento/gestao/usuarios_list.html", {"usuarios": usuarios})


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
@gestor_required
def ciclo_abrir(request):
    modelo = ModeloChecklist.objects.filter(ativo=True).order_by("-criado_em").first()
    fases = list(Fase.objects.filter(modelo=modelo, principal=True)) if modelo else []
    contexto = {
        "empresas_ativas": Empresa.objects.filter(ativa=True).count(),
        "sem_equipe": Empresa.objects.filter(ativa=True, equipe__isnull=True).count(),
        "referencia": request.POST.get("referencia", ""),
        "fases": fases,
    }
    if request.method == "POST":
        referencia = request.POST.get("referencia", "").strip()
        prazos = {}
        for f in fases:
            d = parse_date(request.POST.get(f"prazo_{f.id}") or "")
            if d:
                prazos[f.id] = d
        # devolve os prazos digitados para reexibir em caso de erro
        contexto["prazos_form"] = {f.id: request.POST.get(f"prazo_{f.id}", "") for f in fases}
        try:
            ciclo, n_proc, n_stat = abrir_ciclo_service(referencia, modelo, prazos)
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
    return Empresa.objects.filter(ativa=True).exclude(id__in=ids_no_ciclo).order_by("razao_social")


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
        .order_by("empresa__razao_social")
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
        .order_by("empresa__razao_social")
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
