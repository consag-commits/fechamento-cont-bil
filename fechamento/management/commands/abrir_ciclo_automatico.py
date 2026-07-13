"""
abrir_ciclo_automatico — Abre o ciclo do mês automaticamente no último dia do mês.

Feito para rodar TODO DIA via agendador externo (Railway Cron Job, Agendador
de Tarefas do Windows, etc.). É seguro rodar mais de uma vez: só age no
último dia do mês, e não duplica se o ciclo já existir.

    uv run python manage.py abrir_ciclo_automatico
    uv run python manage.py abrir_ciclo_automatico --forcar   (ignora a checagem de dia, útil para testar)
"""

from calendar import monthrange

from django.core.management.base import BaseCommand
from django.utils import timezone

from fechamento.services import AberturaError, abrir_ciclo


class Command(BaseCommand):
    help = "Abre o ciclo do mês corrente automaticamente, se hoje for o último dia do mês."

    def add_arguments(self, parser):
        parser.add_argument(
            "--forcar", action="store_true",
            help="Ignora a checagem de 'último dia do mês' (para testar manualmente).",
        )

    def handle(self, *args, **options):
        hoje = timezone.localdate()
        ultimo_dia_do_mes = monthrange(hoje.year, hoje.month)[1]

        if not options["forcar"] and hoje.day != ultimo_dia_do_mes:
            self.stdout.write(
                f"Hoje ({hoje}) não é o último dia do mês (seria dia {ultimo_dia_do_mes}). Nada a fazer."
            )
            return

        referencia = f"{hoje.year:04d}-{hoje.month:02d}"
        try:
            ciclo, n_proc, n_stat = abrir_ciclo(referencia)
        except AberturaError as e:
            # Ciclo já existe (rodou antes hoje) ou outro problema de negócio — não é erro fatal do cron.
            self.stdout.write(self.style.WARNING(f"Não abriu: {e}"))
            return

        self.stdout.write(self.style.SUCCESS(
            f"Ciclo {ciclo.competencia_display} aberto automaticamente: "
            f"{n_proc} empresas, {n_stat} itens gerados."
        ))
