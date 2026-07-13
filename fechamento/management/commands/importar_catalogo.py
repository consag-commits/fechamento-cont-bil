"""
importar_catalogo — Importa a lista-mestre de empresas do chamado interno.

Lê a tabela `clientes` do chamados.db (empresas já sincronizadas do Omie) e
popula o CatalogoEmpresa, usado no autocomplete do cadastro.

    uv run python manage.py importar_catalogo
    uv run python manage.py importar_catalogo --db "caminho/para/chamados.db"
"""

import sqlite3
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from fechamento.models import CatalogoEmpresa


class Command(BaseCommand):
    help = "Importa o catálogo de empresas a partir do chamados.db do chamado interno."

    def add_arguments(self, parser):
        default = Path(settings.BASE_DIR).parent / "projeto_chamado" / "chamados.db"
        parser.add_argument("--db", default=str(default), help="Caminho para o chamados.db de origem.")

    def handle(self, *args, **options):
        db_path = Path(options["db"])
        if not db_path.exists():
            raise CommandError(f"Banco de origem não encontrado: {db_path}")

        conn = sqlite3.connect(str(db_path))
        try:
            rows = conn.execute("SELECT razao_social, cnpj FROM clientes").fetchall()
        except sqlite3.Error as e:
            raise CommandError(f"Erro ao ler tabela 'clientes': {e}")
        finally:
            conn.close()

        objs, vistos = [], set()
        for razao, cnpj in rows:
            razao = (razao or "").strip()
            cnpj = (cnpj or "").strip()
            if not razao:
                continue
            chave = cnpj or razao.lower()
            if chave in vistos:
                continue
            vistos.add(chave)
            objs.append(CatalogoEmpresa(razao_social=razao, cnpj=cnpj))

        CatalogoEmpresa.objects.all().delete()
        CatalogoEmpresa.objects.bulk_create(objs)

        self.stdout.write(self.style.SUCCESS(
            f"Catálogo importado: {len(objs)} empresas (de {len(rows)} registros)."
        ))
