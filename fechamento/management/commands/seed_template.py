"""
seed_template — Cria o modelo de checklist padrão do fechamento contábil.

As fases, itens e prazos foram extraídos da planilha
"CHECKLIST_Fechamento Contábil". Rode uma vez:

    uv run python manage.py seed_template
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from fechamento.models import ModeloChecklist, Fase, Item

MODELO_NOME = "Fechamento Contábil — Assessoria"

# (nome da fase, prazo = dia do mês, [(item, pontua?)...])
# Estrutura fiel à planilha (colunas C–Z e fórmulas do bloco RESUMO):
#   Integrações  = C–K (base 8; "Solicitado" não pontua)
#   Conciliações = L–U (10)
#   Análises/Fech.= V–Z (5)
ESTRUTURA = [
    ("Integrações", 10, [
        ("Financeiro — Solicitado", False),  # preliminar: não entra no %
        ("Financeiro — Recebido", True),
        ("Financeiro — Feito", True),
        ("Folha — Recebido", True),
        ("Folha — Feito", True),
        ("Fiscal — Recebido ICMS", True),
        ("Fiscal — Recebido PIS/COFINS", True),
        ("Fiscal — Feito", True),
        ("Fiscal — Receitas financeiras", True),
    ]),
    ("Conciliações", 20, [
        ("Bancária", True),
        ("Contas a receber", True),
        ("Despesas antecipadas", True),
        ("Depreciação do imobilizado", True),
        ("Empréstimos / Financiamentos / Consórcios / Parcelamentos", True),
        ("Fornecedores", True),
        ("Variação cambial", True),
        ("Eventos de folha", True),
        ("Apuração de impostos", True),
        ("Contas a pagar", True),
    ]),
    ("Análises / Fechamento", 24, [
        ("Análise DRE", True),
        ("Fechamento de custo", True),
        ("Indicadores / CEIPIM (enviado)", True),
        ("Fechamento", True),
        ("Balancete salvo na pasta", True),
    ]),
]


class Command(BaseCommand):
    help = "Cria o modelo de checklist padrão (fases + itens) do fechamento contábil."

    @transaction.atomic
    def handle(self, *args, **options):
        modelo, criado = ModeloChecklist.objects.get_or_create(nome=MODELO_NOME)
        if not criado:
            self.stdout.write(self.style.WARNING(f'Modelo "{MODELO_NOME}" já existe — nada a fazer.'))
            return

        total_itens = 0
        for ordem_fase, (nome_fase, prazo_dia, itens) in enumerate(ESTRUTURA, start=1):
            fase = Fase.objects.create(
                modelo=modelo, nome=nome_fase, ordem=ordem_fase, prazo_offset_dias=prazo_dia,
            )
            for ordem_item, (nome_item, pontua) in enumerate(itens, start=1):
                Item.objects.create(fase=fase, nome=nome_item, ordem=ordem_item, pontua=pontua)
                total_itens += 1

        self.stdout.write(self.style.SUCCESS(
            f'Modelo "{MODELO_NOME}" criado com {len(ESTRUTURA)} fases e {total_itens} itens.'
        ))
