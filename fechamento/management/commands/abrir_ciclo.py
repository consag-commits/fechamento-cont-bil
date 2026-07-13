"""
abrir_ciclo — Abre um ciclo de fechamento para um mês de referência.

Gera automaticamente um Processo para cada empresa ativa (já distribuído pela
equipe padrão da empresa) e um ItemStatus "pendente" para cada item do modelo.

    uv run python manage.py abrir_ciclo 2026-06
"""

from django.core.management.base import BaseCommand, CommandError

from fechamento.services import AberturaError, abrir_ciclo


class Command(BaseCommand):
    help = "Abre um ciclo de fechamento (AAAA-MM) e gera processos + itens pendentes."

    def add_arguments(self, parser):
        parser.add_argument("referencia", help="Mês de referência no formato AAAA-MM. Ex.: 2026-06")
        parser.add_argument("--modelo", default=None, help="Nome do modelo (padrão: modelo ativo mais recente).")

    def handle(self, *args, **options):
        try:
            ciclo, n_proc, n_stat = abrir_ciclo(options["referencia"], options["modelo"])
        except AberturaError as e:
            raise CommandError(str(e))

        self.stdout.write(self.style.SUCCESS(
            f"Ciclo {ciclo.referencia} aberto: {n_proc} empresas = {n_stat} pendências geradas."
        ))
