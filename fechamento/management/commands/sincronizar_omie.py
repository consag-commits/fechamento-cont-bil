"""
sincronizar_omie — Sincroniza o catálogo de empresas direto da API da Omie.

Popula o CatalogoEmpresa (usado só no autocomplete do cadastro de Empresa).
Credenciais vêm de variáveis de ambiente, nunca hardcoded:

    OMIE_APP_KEY=...
    OMIE_APP_SECRET=...

    uv run python manage.py sincronizar_omie
"""

import os
import re
import time

import requests
from django.core.management.base import BaseCommand, CommandError

from fechamento.models import CatalogoEmpresa

OMIE_URL = "https://app.omie.com.br/api/v1/geral/clientes/"


def _limpar_razao_social(razao_social, cnpj):
    """Remove prefixo de CNPJ/CPF que às vezes vem embutido no nome."""
    match = re.match(r"^([\d.\-/]+)\s+(.*)$", razao_social)
    if not match:
        return razao_social
    prefixo, resto = match.groups()
    num_prefixo = "".join(filter(str.isdigit, prefixo))
    num_cnpj = "".join(filter(str.isdigit, cnpj))
    if num_prefixo and num_cnpj.startswith(num_prefixo):
        return resto.strip()
    return razao_social


class Command(BaseCommand):
    help = "Sincroniza o catálogo de empresas (autocomplete) direto da API da Omie."

    def handle(self, *args, **options):
        app_key = os.environ.get("OMIE_APP_KEY")
        app_secret = os.environ.get("OMIE_APP_SECRET")
        if not app_key or not app_secret:
            raise CommandError(
                "Defina OMIE_APP_KEY e OMIE_APP_SECRET nas variáveis de ambiente."
            )

        empresas, vistos = [], set()
        pagina_atual, total_paginas = 1, 1

        self.stdout.write("Sincronizando clientes com a Omie...")

        while pagina_atual <= total_paginas:
            payload = {
                "call": "ListarClientes",
                "app_key": app_key,
                "app_secret": app_secret,
                "param": [
                    {
                        "pagina": pagina_atual,
                        "registros_por_pagina": 50,
                        "apenas_importado_api": "N",
                    }
                ],
            }
            try:
                resp = requests.post(OMIE_URL, json=payload, timeout=30)
                resp.raise_for_status()
            except requests.RequestException as e:
                raise CommandError(f"Erro ao chamar a API da Omie: {e}")

            dados = resp.json()

            if pagina_atual == 1:
                total_paginas = dados.get("total_de_paginas", 1)
                self.stdout.write(f"Total de páginas: {total_paginas}")

            for c in dados.get("clientes_cadastro", []):
                razao_social = (c.get("razao_social") or "").strip()
                cnpj = (c.get("cnpj_cpf") or "").strip()
                if not razao_social or not cnpj:
                    continue

                razao_social = _limpar_razao_social(razao_social, cnpj)

                chave = cnpj or razao_social.lower()
                if chave in vistos:
                    continue
                vistos.add(chave)
                empresas.append(CatalogoEmpresa(razao_social=razao_social, cnpj=cnpj))

            self.stdout.write(f"[OK] Página {pagina_atual}/{total_paginas} processada.")
            pagina_atual += 1
            time.sleep(0.5)

        CatalogoEmpresa.objects.all().delete()
        CatalogoEmpresa.objects.bulk_create(empresas)

        self.stdout.write(self.style.SUCCESS(
            f"Catálogo sincronizado: {len(empresas)} empresas."
        ))
