"""urls.py — Rotas do app de fechamento."""

from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("ciclo/<int:ciclo_id>/", views.ciclo_consolidado, name="ciclo_consolidado"),
    path("processo/<int:processo_id>/", views.processo_matriz, name="processo_matriz"),
    path("item/<int:status_id>/set/", views.item_set, name="item_set"),

    # Indicadores CEIPIM (função independente dos ciclos)
    path("indicadores-ceipim/", views.indicadores_ceipim, name="indicadores_ceipim"),
    path("indicadores-ceipim/lote/", views.ceipim_bulk_set, name="ceipim_bulk_set"),
    path(
        "indicadores-ceipim/celula/<int:empresa_id>/<int:ano>/<int:mes>/",
        views.ceipim_set_status, name="ceipim_set_status",
    ),
    path("indicadores-ceipim/empresas/adicionar/", views.ceipim_empresa_adicionar, name="ceipim_empresa_adicionar"),
    path(
        "indicadores-ceipim/empresas/<int:empresa_id>/remover/",
        views.ceipim_empresa_remover, name="ceipim_empresa_remover",
    ),

    # Clientes Lucro Real (função independente dos ciclos)
    path("lucro-real/", views.lucro_real, name="lucro_real"),
    path("lucro-real/empresas/adicionar/", views.lucro_real_empresa_adicionar, name="lucro_real_empresa_adicionar"),
    path(
        "lucro-real/empresas/<int:empresa_id>/remover/",
        views.lucro_real_empresa_remover, name="lucro_real_empresa_remover",
    ),
    path(
        "lucro-real/<int:empresa_id>/<int:ano>/campo/",
        views.lucro_real_set_campo, name="lucro_real_set_campo",
    ),
    path(
        "lucro-real/<int:empresa_id>/<int:ano>/mover/",
        views.lucro_real_mover, name="lucro_real_mover",
    ),

    # Gestão (restrito a gestores)
    path("gestao/", views.gestao_home, name="gestao_home"),
    path("gestao/ciclos/abrir/", views.ciclo_abrir, name="ciclo_abrir"),
    path("gestao/ciclos/abrir/preview/", views.ciclo_abrir_preview, name="ciclo_abrir_preview"),
    path("gestao/equipes/", views.equipes_list, name="equipes_list"),
    path("gestao/equipes/nova/", views.equipe_criar, name="equipe_criar"),
    path("gestao/equipes/<int:equipe_id>/editar/", views.equipe_editar, name="equipe_editar"),
    path("gestao/equipes/<int:equipe_id>/membros/adicionar/", views.equipe_membro_adicionar, name="equipe_membro_adicionar"),
    path("gestao/equipes/<int:equipe_id>/membros/<int:user_id>/remover/", views.equipe_membro_remover, name="equipe_membro_remover"),
    path("gestao/usuarios/", views.usuarios_list, name="usuarios_list"),
    path("gestao/usuarios/novo/", views.usuario_criar, name="usuario_criar"),
    path("gestao/usuarios/<int:user_id>/editar/", views.usuario_editar, name="usuario_editar"),
    path("gestao/usuarios/<int:user_id>/perfil/", views.usuario_perfil, name="usuario_perfil"),

    # Ocorrências (registro/edição — vivem dentro do perfil do funcionário)
    path("gestao/ocorrencias/<int:ocorrencia_id>/editar/", views.ocorrencia_editar, name="ocorrencia_editar"),
    path("gestao/ocorrencias/<int:ocorrencia_id>/remover/", views.ocorrencia_remover, name="ocorrencia_remover"),
    path("gestao/empresas/", views.empresas_list, name="empresas_list"),
    path("gestao/empresas/catalogo.json", views.empresas_catalogo_json, name="empresas_catalogo_json"),
    path("gestao/empresas/nova/", views.empresa_criar, name="empresa_criar"),
    path("gestao/empresas/<int:empresa_id>/editar/", views.empresa_editar, name="empresa_editar"),
    path("gestao/empresas/<int:empresa_id>/ativa/", views.empresa_toggle_ativa, name="empresa_toggle_ativa"),
    path("gestao/empresas/<int:empresa_id>/equipe/", views.empresa_set_equipe, name="empresa_set_equipe"),

    # Configuração de ciclos
    path("gestao/ciclos/", views.ciclos_list, name="ciclos_list"),
    path("gestao/ciclos/<int:ciclo_id>/", views.ciclo_config, name="ciclo_config"),
    path("gestao/ciclos/<int:ciclo_id>/prazo/<int:fase_id>/", views.ciclo_prazo_editar, name="ciclo_prazo_editar"),
    path("gestao/ciclos/<int:ciclo_id>/empresas/adicionar/", views.ciclo_empresa_adicionar, name="ciclo_empresa_adicionar"),
    path("gestao/ciclos/<int:ciclo_id>/empresas/<int:processo_id>/remover/", views.ciclo_empresa_remover, name="ciclo_empresa_remover"),
    path("gestao/ciclos/<int:ciclo_id>/empresas/<int:processo_id>/equipe/", views.ciclo_processo_set_equipe, name="ciclo_processo_set_equipe"),
    path("gestao/ciclos/<int:ciclo_id>/arquivar/", views.ciclo_arquivar, name="ciclo_arquivar"),
]
