"""
models.py — Modelos do Sistema de Fechamento Contábil.

Estrutura (normalizada a partir da planilha de checklist):

    ModeloChecklist ─< Fase ─< Item          (definição — cadastrada uma vez)
    Ciclo ─< Processo ─< ItemStatus          (execução — gerada a cada mês)
    Empresa, Equipe                          (cadastros base)

Progresso (% conclusão, pendências, atraso) é SEMPRE calculado a partir de
ItemStatus — nunca digitado.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone


# ── Catálogo de empresas (lista-mestre para autocomplete) ─────────────────────
class CatalogoEmpresa(models.Model):
    """Lista-mestre de empresas (importada do Omie via chamado interno).
    Usada só para autocomplete no cadastro — não participa dos ciclos."""

    razao_social = models.CharField("Razão social", max_length=255)
    cnpj = models.CharField("CNPJ", max_length=20, blank=True)

    class Meta:
        verbose_name = "Empresa (catálogo)"
        verbose_name_plural = "Catálogo de empresas"
        ordering = ["razao_social"]

    def __str__(self):
        return self.razao_social


# ── Perfil / papel de acesso ──────────────────────────────────────────────────
class Perfil(models.Model):
    """Papel de acesso do usuário. Ausência de perfil = operador."""

    class Papel(models.TextChoices):
        OPERADOR = "operador", "Operador"
        GESTOR = "gestor", "Gestor"

    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="perfil",
    )
    papel = models.CharField(max_length=20, choices=Papel.choices, default=Papel.OPERADOR)
    equipes = models.ManyToManyField(
        "Equipe", blank=True, related_name="membros",
        help_text="Equipes que este usuário atende. Operador só vê as empresas dessas equipes.",
    )

    class Meta:
        verbose_name = "Perfil"
        verbose_name_plural = "Perfis"

    def __str__(self):
        return f"{self.usuario} ({self.get_papel_display()})"


# ── Cadastros base ────────────────────────────────────────────────────────────
class Equipe(models.Model):
    nome = models.CharField(max_length=100, unique=True)

    class Meta:
        verbose_name = "Equipe"
        verbose_name_plural = "Equipes"
        ordering = ["nome"]

    def __str__(self):
        return self.nome


class Empresa(models.Model):
    razao_social = models.CharField("Razão social", max_length=255)
    cnpj = models.CharField("CNPJ", max_length=20, blank=True)
    equipe = models.ForeignKey(
        Equipe, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="empresas", verbose_name="Equipe padrão",
        help_text="Equipe que assume esta empresa por padrão a cada ciclo.",
    )
    ativa = models.BooleanField(default=True, help_text="Empresas inativas não entram em novos ciclos.")

    class Meta:
        verbose_name = "Empresa"
        verbose_name_plural = "Empresas"
        ordering = ["razao_social"]
        constraints = [
            models.UniqueConstraint(
                fields=["cnpj"], condition=~models.Q(cnpj=""), name="uniq_empresa_cnpj",
            ),
        ]

    def __str__(self):
        return self.razao_social


# ── Template (definido uma vez) ───────────────────────────────────────────────
class ModeloChecklist(models.Model):
    nome = models.CharField(max_length=120)
    ativo = models.BooleanField(default=True)
    criado_em = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "Modelo de checklist"
        verbose_name_plural = "Modelos de checklist"
        ordering = ["-criado_em"]

    def __str__(self):
        return self.nome


class Fase(models.Model):
    modelo = models.ForeignKey(ModeloChecklist, on_delete=models.CASCADE, related_name="fases")
    nome = models.CharField(max_length=120)
    ordem = models.PositiveIntegerField(default=0)
    prazo_offset_dias = models.PositiveIntegerField(
        "Dia sugerido do prazo", default=0,
        help_text="Dia do mês de trabalho usado como sugestão inicial do prazo ao abrir um ciclo. "
                  "O prazo real é definido por ciclo.",
    )
    principal = models.BooleanField(
        default=True,
        help_text="Fases principais contam no % geral e valem para todas as empresas. "
                  "Desmarque para checklists de detalhamento (opcionais por empresa).",
    )
    empresas = models.ManyToManyField(
        Empresa, blank=True, related_name="fases_detalhe",
        help_text="Empresas que usam este detalhamento (só para fases não-principais). "
                  "Escolhido empresa por empresa — independe da equipe.",
    )

    class Meta:
        verbose_name = "Fase"
        verbose_name_plural = "Fases"
        ordering = ["modelo", "ordem"]

    def __str__(self):
        return self.nome


class Item(models.Model):
    fase = models.ForeignKey(Fase, on_delete=models.CASCADE, related_name="itens")
    nome = models.CharField(max_length=200)
    ordem = models.PositiveIntegerField(default=0)
    pontua = models.BooleanField(
        "Conta no progresso", default=True,
        help_text="Desmarque para itens preliminares (ex.: 'Solicitado') que não entram no cálculo de %.",
    )

    class Meta:
        verbose_name = "Item do checklist"
        verbose_name_plural = "Itens do checklist"
        ordering = ["fase", "ordem"]

    def __str__(self):
        return f"{self.fase.nome} · {self.nome}"


# ── Execução (gerado a cada mês) ──────────────────────────────────────────────
_MESES_PT = [
    "", "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
]


class Ciclo(models.Model):
    class Status(models.TextChoices):
        ABERTO = "aberto", "Aberto"
        CONCLUIDO = "concluido", "Concluído"

    modelo = models.ForeignKey(ModeloChecklist, on_delete=models.PROTECT, related_name="ciclos")
    referencia = models.CharField(
        "Competência (AAAA-MM)", max_length=7,
        help_text="Mês contábil sendo fechado. Ex.: 2026-02 (o trabalho ocorre no mês seguinte).",
    )
    data_referencia = models.DateField(
        "Primeiro dia da competência", help_text="Ex.: 2026-02-01",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ABERTO)
    criado_em = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "Ciclo de fechamento"
        verbose_name_plural = "Ciclos de fechamento"
        ordering = ["-data_referencia"]
        constraints = [
            models.UniqueConstraint(fields=["modelo", "referencia"], name="uniq_ciclo_modelo_ref"),
        ]

    def __str__(self):
        return f"Fechamento {self.competencia_display}"

    @property
    def competencia_display(self) -> str:
        """Ex.: '2026-02' → 'Fevereiro/2026'."""
        try:
            ano, mes = self.referencia.split("-")
            return f"{_MESES_PT[int(mes)]}/{ano}"
        except (ValueError, IndexError):
            return self.referencia

    def prazos_dict(self):
        """{fase_id: data_limite} — prazos deste ciclo (1 query)."""
        return {cp.fase_id: cp.data_limite for cp in self.prazos.all()}

    def prazo_fase(self, fase):
        """Data-limite de uma fase neste ciclo (ou None se não houver)."""
        return self.prazos_dict().get(fase.id if hasattr(fase, "id") else fase)


class CicloPrazo(models.Model):
    """Data-limite de uma fase num ciclo específico (editável por mês)."""

    ciclo = models.ForeignKey(Ciclo, on_delete=models.CASCADE, related_name="prazos")
    fase = models.ForeignKey(Fase, on_delete=models.CASCADE, related_name="prazos_ciclo")
    data_limite = models.DateField()

    class Meta:
        verbose_name = "Prazo do ciclo"
        verbose_name_plural = "Prazos do ciclo"
        ordering = ["fase__ordem"]
        constraints = [
            models.UniqueConstraint(fields=["ciclo", "fase"], name="uniq_cicloprazo_ciclo_fase"),
        ]

    def __str__(self):
        return f"{self.ciclo.referencia} · {self.fase.nome}: {self.data_limite}"


class Processo(models.Model):
    """Uma empresa dentro de um ciclo (a 'linha' da planilha)."""

    ciclo = models.ForeignKey(Ciclo, on_delete=models.CASCADE, related_name="processos")
    empresa = models.ForeignKey(Empresa, on_delete=models.PROTECT, related_name="processos")
    equipe = models.ForeignKey(Equipe, on_delete=models.SET_NULL, null=True, blank=True)
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="processos_responsavel",
    )

    class Meta:
        verbose_name = "Processo"
        verbose_name_plural = "Processos"
        ordering = ["empresa__razao_social"]
        constraints = [
            models.UniqueConstraint(fields=["ciclo", "empresa"], name="uniq_processo_ciclo_empresa"),
        ]

    def __str__(self):
        return f"{self.empresa} · {self.ciclo.referencia}"


class ItemStatus(models.Model):
    """Estado de um item de checklist para um processo específico."""

    class Status(models.TextChoices):
        PENDENTE = "pendente", "Pendente"
        FEITO = "feito", "Feito"
        MANUAL = "manual", "Manual"
        NA = "na", "N/A"

    # Status que contam como concluído no cálculo de progresso
    CONCLUIDOS = ("feito", "manual")

    processo = models.ForeignKey(Processo, on_delete=models.CASCADE, related_name="itens_status")
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="statuses")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDENTE)
    data = models.DateField(null=True, blank=True)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="itens_atualizados",
    )
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Status de item"
        verbose_name_plural = "Status de itens"
        ordering = ["item__fase__ordem", "item__ordem"]
        constraints = [
            models.UniqueConstraint(fields=["processo", "item"], name="uniq_itemstatus_processo_item"),
        ]

    @property
    def concluido(self) -> bool:
        return self.status in self.CONCLUIDOS

    def __str__(self):
        return f"{self.processo} · {self.item.nome}: {self.get_status_display()}"
