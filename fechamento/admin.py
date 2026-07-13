"""admin.py — Cadastros gerenciados pelo Django Admin."""

from django.contrib import admin

from .models import (
    Equipe, Empresa, ModeloChecklist, Fase, Item, Ciclo, Processo, Perfil,
    CatalogoEmpresa, CicloPrazo,
)


@admin.register(CatalogoEmpresa)
class CatalogoEmpresaAdmin(admin.ModelAdmin):
    list_display = ["razao_social", "cnpj"]
    search_fields = ["razao_social", "cnpj"]


@admin.register(Perfil)
class PerfilAdmin(admin.ModelAdmin):
    list_display = ["usuario", "papel"]
    list_filter = ["papel", "equipes"]
    search_fields = ["usuario__username", "usuario__first_name"]
    filter_horizontal = ["equipes"]


@admin.register(Equipe)
class EquipeAdmin(admin.ModelAdmin):
    list_display = ["nome"]
    search_fields = ["nome"]


@admin.register(Empresa)
class EmpresaAdmin(admin.ModelAdmin):
    list_display = ["razao_social", "cnpj", "equipe", "ativa"]
    list_filter = ["ativa", "equipe"]
    search_fields = ["razao_social", "cnpj"]
    list_editable = ["equipe", "ativa"]
    autocomplete_fields = ["equipe"]


class ItemInline(admin.TabularInline):
    model = Item
    extra = 1


class FaseInline(admin.TabularInline):
    model = Fase
    extra = 0


@admin.register(ModeloChecklist)
class ModeloChecklistAdmin(admin.ModelAdmin):
    list_display = ["nome", "ativo", "criado_em"]
    inlines = [FaseInline]


@admin.register(Fase)
class FaseAdmin(admin.ModelAdmin):
    list_display = ["nome", "modelo", "ordem", "principal", "prazo_offset_dias"]
    list_filter = ["modelo", "principal"]
    filter_horizontal = ["empresas"]
    inlines = [ItemInline]


class CicloPrazoInline(admin.TabularInline):
    model = CicloPrazo
    extra = 0


@admin.register(Ciclo)
class CicloAdmin(admin.ModelAdmin):
    list_display = ["referencia", "competencia_display", "modelo", "status", "criado_em"]
    list_filter = ["status", "modelo"]
    inlines = [CicloPrazoInline]


@admin.register(Processo)
class ProcessoAdmin(admin.ModelAdmin):
    list_display = ["empresa", "ciclo", "equipe", "responsavel"]
    list_filter = ["ciclo", "equipe"]
    search_fields = ["empresa__razao_social"]
    autocomplete_fields = ["empresa", "equipe"]
