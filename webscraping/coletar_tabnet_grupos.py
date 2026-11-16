#!/usr/bin/env python3
"""Séries mensais por UF do SIA/SUS (TabNet) filtradas por grupo de procedimento.

Complementa ``baixar_datasets.py``. O resolvedor do TabNet reproduz o formulário
com todos os filtros em "Todas as categorias". Para a Entrega II é preciso
isolar os dois grupos da Tabela SUS que representam insumos:

* **06 — Medicamentos** (em sua maior parte APAC do Componente Especializado);
* **07 — Órteses, próteses e materiais especiais (OPM)**.

Para cada grupo e cada medida (quantidade e valor aprovados) o script gera uma
consulta ``Linha = Ano/mês processamento × Coluna = UF`` com todas as
competências disponíveis. A consulta passa pelo mesmo ``Baixador`` do coletor,
o que garante robots.txt, gravação atômica e registro no ``MANIFEST.csv``.

Uso:  python3 webscraping/coletar_tabnet_grupos.py [--destino DATASETS]
"""

import argparse
import logging
import sys
from pathlib import Path
from urllib.parse import urlencode, urljoin

from coletor.baixador import Baixador
from coletor.cliente_http import ClienteHttp, LimitadorPorHost
from coletor.config import (CONEXOES_POR_HOST, CONEXOES_POR_HOST_PADRAO, DESTINO_PADRAO,
                            INTERVALO_PADRAO, INTERVALO_POR_HOST, USER_AGENT)
from coletor.manifesto import Manifesto
from coletor.modelos import Competencia, Status, Tarefa
from coletor.resolvedores.tabnet import _RE_ARQUIVO, _campos_fixos, _opcoes, extrair_tabela_prn

URL_FORMULARIO = "http://tabnet.datasus.gov.br/cgi/deftohtm.exe?sia/cnv/qauf.def"
CAMPO_GRUPO = "SGrupo_procedimento"
GRUPOS = {"6": "06_medicamentos", "7": "07_opm"}            # valor da <option> → sufixo do arquivo
MEDIDAS = {"Qtd.aprovada": "qtd_aprovada", "Valor aprovado": "valor_aprovado"}
LINHA, COLUNA = "Ano/mês_processamento", "Unidade_da_Federação"
PASTA = Path("DS-03_sia_sus_agregado_tabnet", "tabnet_grupos")

logger = logging.getLogger("coletar_tabnet_grupos")


def montar_tarefas(http: ClienteHttp) -> list[Tarefa]:
    """Lê o formulário uma vez e gera as 4 consultas (2 grupos × 2 medidas)."""
    sopa, resposta = http.sopa(URL_FORMULARIO)
    formulario = sopa.find("form")
    acao = urljoin(resposta.url, formulario["action"])
    arquivos = _opcoes(formulario, "Arquivos")
    competencias = sorted(Competencia.de_aamm(_RE_ARQUIVO.search(a)["aamm"]) for a in arquivos)
    # Os filtros fixos vêm com "Todas as categorias"; só o grupo é substituído.
    fixos = [(nome, valor) for nome, valor in _campos_fixos(formulario) if nome != CAMPO_GRUPO]
    linhas = set(_opcoes(formulario, "Linha"))
    medidas_disponiveis = {o.get_text(strip=True): o.get("value") for o in
                           formulario.find("select", attrs={"name": "Incremento"}).find_all("option")}
    if LINHA not in linhas:
        raise SystemExit(f"o formulário não oferece a linha {LINHA!r}")

    tarefas = []
    for grupo, sufixo in GRUPOS.items():
        for rotulo, nome_medida in MEDIDAS.items():
            campos = [("Linha", LINHA), ("Coluna", COLUNA), ("Incremento", medidas_disponiveis[rotulo]),
                      *(("Arquivos", a) for a in arquivos), *fixos, (CAMPO_GRUPO, grupo),
                      ("formato", "prn"), ("mostre", "Mostra")]
            tarefas.append(Tarefa(
                url=acao,
                destino=PASTA / f"tabnet_qauf__grupo_{sufixo}__{nome_medida}.csv",
                dataset="DS-03",
                corpo=urlencode(campos, encoding="latin-1").encode("ascii"),
                extrator=extrair_tabela_prn,
                parametros=f"def=qauf; Linha={LINHA}; Coluna={COLUNA}; Incremento={rotulo}; {CAMPO_GRUPO}={grupo}",
                competencia=f"{competencias[0]}..{competencias[-1]}",
                estatico=False,
            ))
    return tarefas


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--destino", type=Path, default=DESTINO_PADRAO)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")

    http = ClienteHttp(user_agent=USER_AGENT, limitador=LimitadorPorHost(INTERVALO_PADRAO, INTERVALO_POR_HOST))
    baixador = Baixador(destino=args.destino, transportes={"http": http, "https": http},
                        manifesto=Manifesto(args.destino / "MANIFEST.csv"), workers=1,
                        conexoes_por_host=CONEXOES_POR_HOST, conexoes_padrao=CONEXOES_POR_HOST_PADRAO)
    resultados = baixador.executar(montar_tarefas(http))
    falhas = [r for r in resultados if r.status is Status.FALHA]
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
