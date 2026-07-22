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
    cargo = models.CharField("Cargo", max_length=120, blank=True)
    data_admissao = models.DateField("Data de admissão", null=True, blank=True)
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
    participa_ceipim = models.BooleanField(
        "Participa do CEIPIM", default=False,
        help_text="Entra na lista de Indicadores CEIPIM (função independente dos ciclos).",
    )
    participa_lucro_real = models.BooleanField(
        "Cliente Lucro Real", default=False,
        help_text="Entra no acompanhamento de Clientes Lucro Real (função independente dos ciclos).",
    )

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


# ── Indicadores CEIPIM (função independente dos ciclos) ───────────────────────
class IndicadorCeipim(models.Model):
    """Status mensal de entrega do CEIPIM por empresa. Não depende de Ciclo/
    Processo — só reaproveita o cadastro de Empresa."""

    class Status(models.TextChoices):
        NA = "na", "N/A"
        PREVIA = "previa", "Prévia"
        RETIFICADO = "retificado", "Retificado"
        DEFINITIVO = "definitivo", "Definitivo"

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="indicadores_ceipim")
    ano = models.PositiveIntegerField("Ano")
    mes = models.PositiveSmallIntegerField(
        "Mês", help_text="0 = indicador anual (coluna do ano anterior); 1-12 = mês específico.",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NA)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Indicador CEIPIM"
        verbose_name_plural = "Indicadores CEIPIM"
        ordering = ["empresa__razao_social", "ano", "mes"]
        constraints = [
            models.UniqueConstraint(fields=["empresa", "ano", "mes"], name="uniq_ceipim_empresa_ano_mes"),
        ]

    def __str__(self):
        competencia = f"{self.ano}" if self.mes == 0 else f"{self.ano}-{self.mes:02d}"
        return f"{self.empresa} · {competencia}: {self.get_status_display()}"


# ── Clientes Lucro Real (função independente dos ciclos) ──────────────────────
class AcompanhamentoLucroReal(models.Model):
    """Linha do acompanhamento de um cliente Lucro Real num ano.

    Reproduz a planilha "Clientes Lucro Real": regime de apuração, o que ainda
    falta e quando a entrega está prevista. Não depende de Ciclo/Processo — só
    reaproveita o cadastro de Empresa."""

    class Apuracao(models.TextChoices):
        NA = "na", "N/A"
        MENSAL = "mensal", "Mensal"
        TRIMESTRAL = "trimestral", "Trimestral"
        RECEITA_BRUTA = "receita_bruta", "Receita bruta"

    class StatusTrimestre(models.TextChoices):
        PENDENTE = "pendente", "Pendente"
        ANDAMENTO = "andamento", "Em andamento"
        CONCLUIDO = "concluido", "Concluído"
        NA = "na", "N/A"

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="lucro_real")
    ano = models.PositiveIntegerField("Ano")
    ordem = models.PositiveIntegerField(
        "Sequência", default=0,
        help_text="Ordem de exibição na tabela (a coluna SEQUÊNCIA da planilha).",
    )
    apuracao = models.CharField(
        "Apuração", max_length=20, choices=Apuracao.choices, default=Apuracao.NA,
    )
    status_t1 = models.CharField(
        "1º trimestre", max_length=20,
        choices=StatusTrimestre.choices, default=StatusTrimestre.PENDENTE,
    )
    status_t2 = models.CharField(
        "2º trimestre", max_length=20,
        choices=StatusTrimestre.choices, default=StatusTrimestre.PENDENTE,
    )
    status_t3 = models.CharField(
        "3º trimestre", max_length=20,
        choices=StatusTrimestre.choices, default=StatusTrimestre.PENDENTE,
    )
    status_t4 = models.CharField(
        "4º trimestre", max_length=20,
        choices=StatusTrimestre.choices, default=StatusTrimestre.PENDENTE,
    )
    atualizacoes = models.CharField(
        "Atualizações", max_length=255, blank=True,
        help_text="O que falta / quem está trabalhando. Ex.: 'FALTA O CUSTO'.",
    )
    previsao_entrega = models.DateField("Previsão de entrega", null=True, blank=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    # Nomes dos campos de trimestre — a view valida contra esta lista.
    CAMPOS_TRIMESTRE = ("status_t1", "status_t2", "status_t3", "status_t4")

    class Meta:
        verbose_name = "Cliente Lucro Real"
        verbose_name_plural = "Clientes Lucro Real"
        ordering = ["ordem", "empresa__razao_social"]
        constraints = [
            models.UniqueConstraint(fields=["empresa", "ano"], name="uniq_lucroreal_empresa_ano"),
        ]

    def __str__(self):
        return f"{self.empresa} · {self.ano}: {self.get_apuracao_display()}"

    @property
    def trimestres(self):
        """[{'numero', 'campo', 'status'}] — para montar as 4 colunas na tela."""
        return [
            {"numero": n, "campo": f"status_t{n}", "status": getattr(self, f"status_t{n}")}
            for n in range(1, 5)
        ]


# ── Ocorrências (observações do gestor sobre um funcionário) ───────────────────
class Ocorrencia(models.Model):
    """Observação de um gestor sobre um funcionário (bom/mau trabalho etc.)."""

    class Tipo(models.TextChoices):
        POSITIVA = "positiva", "Positiva"
        NEUTRA = "neutra", "Neutra"
        NEGATIVA = "negativa", "Negativa"

    funcionario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ocorrencias",
        verbose_name="Funcionário",
    )
    autor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="ocorrencias_escritas", verbose_name="Registrada por",
    )
    tipo = models.CharField(max_length=20, choices=Tipo.choices, default=Tipo.NEUTRA)
    data = models.DateField("Data do ocorrido", default=timezone.localdate)
    texto = models.TextField("Observação")
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Ocorrência"
        verbose_name_plural = "Ocorrências"
        ordering = ["-data", "-criado_em"]

    def __str__(self):
        return f"{self.funcionario} · {self.data}: {self.get_tipo_display()}"


# ── Entrada vinda do Portal de Sistemas ──────────────────────────────────────
class TicketPortal(models.Model):
    """
    Registro dos tickets de entrada já consumidos.

    O Portal assina um ticket de vida curta para trazer a pessoa até aqui já
    logada. Guardar o identificador de cada ticket usado garante que ele valha
    uma vez só — se alguém capturar um, não consegue reaproveitá-lo.
    """

    jti = models.CharField("Identificador do ticket", max_length=64, unique=True)
    usuario = models.CharField(max_length=150)
    usado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Ticket do portal"
        verbose_name_plural = "Tickets do portal"
        ordering = ["-usado_em"]

    def __str__(self):
        return f"{self.usuario} · {self.usado_em:%d/%m/%Y %H:%M}"
